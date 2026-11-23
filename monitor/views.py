import secrets
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import OuterRef, Subquery, Sum
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from .domain import inspect_service
from .models import Assignment, CheckRun, DailyHealthStat, Environment, Service, ServiceToken, Team
from .serializers import EnvironmentSerializer, RunSerializer, ServiceSerializer, StatSerializer, TeamSerializer


class CatalogView(viewsets.ModelViewSet):
    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response({"detail": "存在关联记录，无法删除"}, status=409)


class EnvironmentView(CatalogView):
    queryset = Environment.objects.order_by("id")
    serializer_class = EnvironmentSerializer


class TeamView(CatalogView):
    queryset = Team.objects.order_by("id")
    serializer_class = TeamSerializer


class ServiceView(viewsets.ModelViewSet):
    lookup_value_regex = "[0-9]+"
    serializer_class = ServiceSerializer

    def get_queryset(self):
        current = ServiceToken.objects.filter(service_id=OuterRef("pk")).order_by("-version")
        services = Service.objects.filter(archived_at=None).annotate(
            token_version=Subquery(current.values("version")[:1]),
            rotated_at=Subquery(current.values("created_at")[:1])).order_by("pk")
        if self.action in ("update", "partial_update", "destroy"):
            services = services.select_for_update()
        return services

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        service = self.get_object()
        instant = timezone.now()
        service.archived_at = instant
        service.save(update_fields=["archived_at"])
        Assignment.objects.filter(service=service, end=None).update(end=instant)
        return Response(status=204)

    @action(methods=["post"], detail=True)
    def checks(self, request, pk=None):
        try:
            run = inspect_service(pk)
        except Service.DoesNotExist:
            raise NotFound()
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(RunSerializer(run).data, status=201)


class Filters(serializers.Serializer):
    service = serializers.IntegerField(min_value=1, required=False)
    environment = serializers.IntegerField(min_value=1, required=False)
    team = serializers.IntegerField(min_value=1, required=False)


class RunFilters(Filters):
    status = serializers.ChoiceField(choices=["success", "failed", "running"], required=False)
    started_from = serializers.DateTimeField(required=False)
    started_before = serializers.DateTimeField(required=False)


class StatFilters(Filters):
    stat_date = serializers.DateField(required=False)


class HistoryView(viewsets.ReadOnlyModelViewSet):
    def get_queryset(self):
        filters = self.filter_class(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        data = dict(filters.validated_data)
        for param, lookup in (("started_from", "started_at__gte"), ("started_before", "started_at__lt")):
            if param in data:
                data[lookup] = data.pop(param)
        return super().get_queryset().filter(**data)


class RunView(HistoryView):
    queryset = CheckRun.objects.order_by("-pk")
    serializer_class = RunSerializer
    filter_class = RunFilters


class StatView(HistoryView):
    queryset = DailyHealthStat.objects.order_by("-stat_date", "service_id", "pk")
    serializer_class = StatSerializer
    filter_class = StatFilters

    @action(detail=False, methods=["get"])
    def summary(self, request):
        groups = (self.get_queryset().order_by().values("stat_date", "environment_id", "team_id")
                  .annotate(total=Sum("total_count"), successful=Sum("success_count"))
                  .order_by("-stat_date", "environment_id", "team_id"))
        page = self.paginate_queryset(groups)
        for row in page:
            row["health_rate"] = (format(Decimal(row["successful"]) / row["total"], ".8f")
                                  if row["total"] else None)
        return self.get_paginated_response(page)


@require_GET
def demo_health(request, pk):
    token = ServiceToken.objects.filter(service_id=pk, service__archived_at=None).order_by("-version").first()
    if token is None:
        return HttpResponse(status=404)
    expected = b"Bearer " + settings.TOKEN_CIPHER.decrypt(bytes(token.ciphertext))
    supplied = request.headers.get("Authorization", "").encode()
    return HttpResponse(status=200 if secrets.compare_digest(supplied, expected) else 401)
