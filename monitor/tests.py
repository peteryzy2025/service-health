from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from .domain import build_report, inspect_service, rotate, rotation_slot
from .models import Assignment, CheckRun, DailyHealthStat, Environment, Service, Team
from .serializers import ServiceSerializer
from .tasks import dispatch_rotation, rotate_service


def service_data(team, origin="http://web:8000", name="orders"):
    return {"name": name, "team": team.pk, "environment": Environment.objects.get(code="dev").pk,
            "base_url": origin, "health_path": "/health/"}


@override_settings(PROBE_ORIGINS=["http://web:8000"])
class HealthTests(APITestCase):
    def setUp(self):
        self.client.force_authenticate(get_user_model().objects.create(username="review", is_staff=True))
        self.team = Team.objects.create(name="Orders")
        response = self.client.post("/api/services/", service_data(self.team))
        self.assertEqual(response.status_code, 201, response.data)
        self.service = Service.objects.get()
        self.url = f"/api/services/{self.service.pk}/"
        self.day = timezone.localdate()
        self.start = timezone.now().astimezone(timezone.get_default_timezone()).replace(
            hour=0, minute=0, second=0, microsecond=0)

    @patch("monitor.domain.requests.Session.get")
    def test_probes(self, get):
        for code, exception, error in [(200, None, ""), (503, None, "unexpected_status"),
                                       (302, None, "unexpected_status"), (None, requests.Timeout(), "timeout"),
                                       (None, requests.exceptions.SSLError(), "tls_error"),
                                       (None, requests.ConnectionError(), "connection_error")]:
            get.side_effect = exception
            get.return_value.__enter__.return_value.status_code = code
            response = self.client.post(self.url + "checks/")
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.data["error_code"], error)
            self.assertEqual(response.data["http_status"], code)
            self.assertEqual(response.data["status"], "failed" if error else "success")
            self.assertIn("X-Request-Duration-Ms", response)
            self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertEqual(get.call_count, CheckRun.objects.count())
        self.assertEqual(self.client.get("/api/checks/?status=failed").data["count"], 5)

    def test_ownership_and_archive(self):
        new_team = Team.objects.create(name="Platform")
        self.assertEqual(self.client.patch(self.url, {"team": new_team.pk}).status_code, 200)
        self.assertEqual(Assignment.objects.filter(end=None).get().team, new_team)
        self.assertEqual(self.client.delete(f"/api/teams/{self.team.pk}/").status_code, 409)
        self.assertEqual(self.client.delete(self.url).status_code, 204)
        response = self.client.post(self.url + "checks/")
        self.assertEqual(response.status_code, 404)
        self.assertIn("X-Request-Duration-Ms", response)
        self.assertFalse(Assignment.objects.filter(end=None).exists())

    def test_validation_and_access(self):
        for path in ["https://other/", "//other/", "/a\\b"]:
            self.assertEqual(self.client.patch(self.url, {"health_path": path}).status_code, 400)
        self.assertEqual(self.client.patch(self.url, {"base_url": "http://unregistered"}).status_code, 400)
        self.assertEqual(APIClient().get("/api/services/").status_code, 401)

    def test_rotation(self):
        original = self.service.tokens.get()
        slot = original.rotation_slot + timedelta(hours=8)
        rotate_service(self.service.pk, slot.isoformat())
        rotate(self.service.pk, slot)
        rotate(self.service.pk, original.rotation_slot)
        latest = self.service.tokens.latest("version")
        self.assertEqual(latest.version, 2)
        new = settings.TOKEN_CIPHER.decrypt(bytes(latest.ciphertext)).decode()
        old = settings.TOKEN_CIPHER.decrypt(bytes(original.ciphertext)).decode()
        self.assertNotEqual(new, old)
        self.assertNotIn(new.encode(), bytes(latest.ciphertext))
        body = self.client.get(self.url).content
        self.assertNotIn(new.encode(), body)
        self.assertNotIn(bytes(latest.ciphertext), body)
        from .views import demo_health
        from django.test import RequestFactory
        for token, code in [(old, 401), (new, 200)]:
            request = RequestFactory().get("/", HTTP_AUTHORIZATION=f"Bearer {token}")
            self.assertEqual(demo_health(request, self.service.pk).status_code, code)

    def test_daily_report(self):
        env = self.service.environment
        def finish(service, team, success, instant):
            CheckRun.objects.create(service=service, environment=env, team=team, token_version=1,
                                    started_at=instant - timedelta(seconds=2), finished_at=instant,
                                    status="success" if success else "failed")
        for success in [True, True, False]:
            finish(self.service, self.team, success, self.start)
        finish(self.service, self.team, False, self.start + timedelta(days=1))
        new_team = Team.objects.create(name="New")
        self.client.patch(self.url, {"team": new_team.pk})
        finish(self.service, new_team, False, timezone.now())
        r = self.client.post("/api/services/", service_data(self.team, name="idle"))
        idle = Service.objects.get(pk=r.data["id"])
        CheckRun.objects.create(service=idle, team=self.team, environment=env, token_version=1)
        build_report(self.day)
        build_report(self.day)
        rows = DailyHealthStat.objects.filter(stat_date=self.day)
        self.assertEqual(rows.count(), 3)
        self.assertEqual(rows.get(service=self.service, team=self.team).health_rate, Decimal("0.66666667"))
        self.assertEqual(rows.get(team=new_team).health_rate, 0)
        self.assertIsNone(rows.get(service=idle).health_rate)
        self.assertEqual(self.client.get(f"/api/daily-health/?team={self.team.pk}").data["count"], 2)
        finish(idle, self.team, True, timezone.now())
        build_report(self.day)
        self.assertEqual(rows.get(service=idle).success_count, 1)
        summary = self.client.get(f"/api/daily-health/summary/?team={self.team.pk}").data["results"]
        self.assertEqual(summary[0]["health_rate"], "0.75000000")

    def test_zero_sample_historical_ownership(self):
        yesterday = self.day - timedelta(days=1)
        Assignment.objects.filter(service=self.service).update(start=self.start - timedelta(days=2))
        new_team = Team.objects.create(name="Next")
        self.client.patch(self.url, {"team": new_team.pk})
        self.client.delete(self.url)
        build_report(yesterday)
        old = DailyHealthStat.objects.get(stat_date=yesterday)
        self.assertEqual(old.team, self.team)
        self.assertIsNone(old.health_rate)
        build_report(self.day)
        self.assertEqual(DailyHealthStat.objects.get(stat_date=self.day).team, new_team)

    def test_dispatch(self):
        with patch("monitor.tasks.rotate_service.delay") as send:
            dispatch_rotation()
            send.assert_called_once_with(self.service.pk, rotation_slot(timezone.now()).isoformat())
        report = settings.CELERY_BEAT_SCHEDULE["report"]
        self.assertEqual(report["task"], "monitor.tasks.report_day")
        self.assertEqual(report["args"][0](), (self.day - timedelta(days=1)).isoformat())


from django.urls import path
from .views import demo_health

urlpatterns = [path("demo/services/<int:pk>/health/", demo_health)]


@override_settings(ROOT_URLCONF=__name__)
class HttpIntegrationTests(LiveServerTestCase):
    def test_demo_roundtrip(self):
        with self.settings(PROBE_ORIGINS=[self.live_server_url]):
            team = Team.objects.create(name="Web")
            serializer = ServiceSerializer(data=service_data(team, self.live_server_url))
            serializer.is_valid(raise_exception=True)
            service = serializer.save()
            service.health_path = f"/demo/services/{service.pk}/health/"
            service.save()
            self.assertEqual(inspect_service(service.pk).http_status, 200)
            previous = service.tokens.get()
            rotate(service.pk, timezone.now())
            old = settings.TOKEN_CIPHER.decrypt(bytes(previous.ciphertext)).decode()
            with requests.Session() as client:
                client.trust_env = False
                response = client.get(self.live_server_url + service.health_path,
                                      headers={"Authorization": f"Bearer {old}"}, timeout=3)
                self.assertEqual(response.status_code, 401)
            self.assertEqual(inspect_service(service.pk).status, "success")
