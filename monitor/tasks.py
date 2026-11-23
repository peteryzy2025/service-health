from datetime import date, datetime

from celery import shared_task
from django.db import OperationalError
from config.schedule import slot_argument

from .domain import build_report, rotate
from .models import Service


@shared_task
def dispatch_rotation(slot=None):
    slot = slot or slot_argument()
    for pk in Service.objects.filter(archived_at=None).values_list("pk", flat=True).iterator():
        rotate_service.delay(pk, slot)


@shared_task(autoretry_for=(OperationalError,), retry_backoff=True, max_retries=4)
def rotate_service(service_id, slot):
    rotate(service_id, datetime.fromisoformat(slot))


@shared_task(autoretry_for=(OperationalError,), retry_backoff=True, max_retries=4)
def report_day(stat_date):
    return build_report(date.fromisoformat(stat_date))
