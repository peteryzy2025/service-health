from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless

from django.db import connection, connections
from django.test import TransactionTestCase
from django.utils import timezone

from .domain import assign, build_report, rotate, rotation_slot
from .models import DailyHealthStat, Environment, ReportDayLock, Service, Team


@skipUnless(connection.vendor == "mysql", "Requires InnoDB row locks")
class ConcurrentTaskTests(TransactionTestCase):
    def setUp(self):
        env, _ = Environment.objects.get_or_create(code="dev", defaults={"name": "开发"})
        self.service = Service.objects.create(
            name="并发测试", environment=env, team=Team.objects.create(name="平台组"),
            base_url="http://web:8000", health_path="/health/")
        assign(self.service, self.service.created_at)

    def run_twice_concurrently(self, func, *args):
        start = Barrier(2, timeout=10)

        def run():
            try:
                connection.ensure_connection()
                start.wait()
                return func(*args)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run) for _ in range(2)]
            return [future.result(timeout=30) for future in futures]

    def test_rotation_contention(self):
        slot = rotation_slot(timezone.now())
        rotate(self.service.pk, slot)
        self.run_twice_concurrently(rotate, self.service.pk, slot + timedelta(hours=8))
        self.assertEqual(list(self.service.tokens.order_by("version").values_list("version", flat=True)), [1, 2])

    def test_concurrent_first_report_and_rebuild(self):
        day = timezone.localdate()
        for _ in range(2):
            self.assertEqual(self.run_twice_concurrently(build_report, day), [1, 1])
            self.assertEqual(ReportDayLock.objects.filter(stat_date=day).count(), 1)
            self.assertEqual(DailyHealthStat.objects.filter(stat_date=day).count(), 1)
