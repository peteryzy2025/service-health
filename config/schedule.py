from datetime import timedelta
from django.utils import timezone


def current_rotation_slot(instant=None):
    local = timezone.localtime(instant)
    return local.replace(hour=local.hour // 8 * 8, minute=0, second=0, microsecond=0)


def slot_argument():
    return current_rotation_slot().isoformat()


def report_argument():
    return (timezone.localdate() - timedelta(days=1)).isoformat()
