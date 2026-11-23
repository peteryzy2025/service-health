from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from .domain import assign, check_target, rotate, rotation_slot
from .models import Assignment, CheckRun, DailyHealthStat, Environment, Service, Team


class EnvironmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Environment
        fields = ["id", "code", "name"]


class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ["id", "name"]


class ServiceSerializer(serializers.ModelSerializer):
    token_version = serializers.IntegerField(read_only=True)
    rotated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Service
        fields = ["id", "name", "environment", "team", "base_url", "health_path", "expected_status",
                  "created_at", "archived_at", "token_version", "rotated_at"]
        read_only_fields = ["created_at", "archived_at"]

    def validate(self, attrs):
        try:
            check_target(attrs.get("base_url", getattr(self.instance, "base_url", "")),
                         attrs.get("health_path", getattr(self.instance, "health_path", "")))
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        service = super().create(validated_data)
        assign(service, service.created_at)
        rotate(service.pk, rotation_slot(service.created_at))
        current = service.tokens.latest("version")
        service.token_version = current.version
        service.rotated_at = current.created_at
        return service

    def update(self, instance, validated_data):
        before = (instance.environment_id, instance.team_id)
        service = super().update(instance, validated_data)
        if before != (service.environment_id, service.team_id):
            instant = timezone.now()
            Assignment.objects.filter(service=service, end=None).update(end=instant)
            assign(service, instant)
        return service


class RunSerializer(serializers.ModelSerializer):
    class Meta:
        model = CheckRun
        fields = "__all__"


class StatSerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyHealthStat
        fields = "__all__"
