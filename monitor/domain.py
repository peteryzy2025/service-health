import secrets
from datetime import datetime, time, timedelta
from decimal import Decimal
from time import perf_counter
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from config.schedule import current_rotation_slot as rotation_slot

from .models import Assignment, CheckRun, DailyHealthStat, ReportDayLock, Service, ServiceToken


def check_target(origin, path):
    # Allow exact administrator-approved origins, including the port.
    if origin not in settings.PROBE_ORIGINS:
        raise ValidationError("目标地址不在 PROBE_ORIGINS 中")
    url = urlsplit(origin)
    if (url.scheme not in ("http", "https") or not url.hostname or url.username is not None
            or url.path or url.query or url.fragment):
        raise ValidationError("base_url 应只包含协议、主机及可选端口")
    if (not path.startswith("/") or path.startswith("//") or "\\" in path
            or "#" in path or any(ord(c) < 32 for c in path)):
        raise ValidationError("health_path 必须是站内绝对路径")


@transaction.atomic
def rotate(service_id, slot):
    service = Service.objects.select_for_update().get(pk=service_id)
    current = service.tokens.order_by("-version").first()
    if service.archived_at or (current and current.rotation_slot >= slot):
        return
    secret = secrets.token_urlsafe(32).encode()
    service.tokens.create(version=current.version + 1 if current else 1,
                          rotation_slot=slot, ciphertext=settings.TOKEN_CIPHER.encrypt(secret))


def assign(service, instant):
    Assignment.objects.create(service=service, environment_id=service.environment_id,
                              team_id=service.team_id, start=instant)


def inspect_service(service_id):
    with transaction.atomic():
        service = Service.objects.select_for_update().get(pk=service_id, archived_at=None)
        check_target(service.base_url, service.health_path)
        token = service.tokens.latest("version")
        bearer = settings.TOKEN_CIPHER.decrypt(bytes(token.ciphertext)).decode()
        run = CheckRun.objects.create(service=service, team_id=service.team_id,
                                      environment_id=service.environment_id, token_version=token.version)
    start = perf_counter()
    error = ""
    try:
        with requests.Session() as client:
            client.trust_env = False
            with client.get(service.base_url + service.health_path, headers={"Authorization": f"Bearer {bearer}"},
                            timeout=(2, 3), allow_redirects=False, stream=True) as response:
                run.http_status = response.status_code
                if run.http_status != service.expected_status:
                    error = "unexpected_status"
    except requests.exceptions.Timeout:
        error = "timeout"
    except requests.exceptions.SSLError:
        error = "tls_error"
    except requests.RequestException:
        error = "connection_error"
    run.duration_ms = round((perf_counter() - start) * 1000)
    run.finished_at = timezone.now()
    run.error_code = error
    run.status = "failed" if error else "success"
    run.save(update_fields=["duration_ms", "finished_at", "error_code", "status", "http_status"])
    return run


@transaction.atomic
def build_report(day):
    # Keep one persistent lock row even if the report for this day is empty.
    ReportDayLock.objects.select_for_update().get_or_create(stat_date=day)
    tz = timezone.get_default_timezone()
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    groups = (CheckRun.objects.filter(finished_at__gte=start, finished_at__lt=end,
                                       status__in=["success", "failed"])
              .values("service_id", "environment_id", "team_id")
              .annotate(total_count=Count("id"), success_count=Count("id", filter=Q(status="success"))))
    rows = []
    seen = set()
    for group in groups:
        seen.add(group["service_id"])
        rate = Decimal(group["success_count"]) / group["total_count"]
        rows.append(DailyHealthStat(stat_date=day, health_rate=rate, **group))
    assignments = (Assignment.objects.filter(start__lt=end).filter(Q(end=None) | Q(end__gt=start))
                   .order_by("service_id", "-start", "-pk"))
    for entry in assignments.iterator():
        if entry.service_id not in seen:
            rows.append(DailyHealthStat(stat_date=day, service_id=entry.service_id,
                                        environment_id=entry.environment_id, team_id=entry.team_id,
                                        total_count=0, success_count=0))
            seen.add(entry.service_id)
    DailyHealthStat.objects.filter(stat_date=day).delete()
    DailyHealthStat.objects.bulk_create(rows)
    return len(rows)
