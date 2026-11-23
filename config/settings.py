import os
from pathlib import Path

from celery.schedules import crontab
from celery.beat import BeatLazyFunc
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from .schedule import report_argument, slot_argument

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
try:
    TOKEN_CIPHER = Fernet(os.environ["TOKEN_ENCRYPTION_KEY"])
except (KeyError, ValueError) as exc:
    raise ImproperlyConfigured("Set TOKEN_ENCRYPTION_KEY to a valid Fernet key") from exc

DEBUG = False
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,web").split(",")
PROBE_ORIGINS = os.getenv("PROBE_ORIGINS", "http://web:8000").split(",")
DEMO_ENABLED = os.getenv("DEMO_ENABLED") == "1"
ROOT_URLCONF = "config.urls"
STATIC_URL = "/static/"
INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "rest_framework", "monitor"]
MIDDLEWARE = ["monitor.middleware.RequestTimer", "django.middleware.common.CommonMiddleware"]
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.BasicAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAdminUser"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "Asia/Shanghai"
if os.getenv("POSTGRES_HOST"):
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql", "HOST": os.environ["POSTGRES_HOST"],
        "NAME": "health", "USER": "health", "PASSWORD": os.environ["POSTGRES_PASSWORD"],
    }}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "dev.sqlite3"}}

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "rotate": {"task": "monitor.tasks.dispatch_rotation", "schedule": crontab(hour="0,8,16", minute=0),
               "args": (BeatLazyFunc(slot_argument),)},
    "report": {"task": "monitor.tasks.report_day", "schedule": crontab(hour=0, minute=0),
               "args": (BeatLazyFunc(report_argument),)},
}
LOGGING = {"version": 1, "disable_existing_loggers": False,
           "handlers": {"console": {"class": "logging.StreamHandler"}},
           "loggers": {"monitor.requests": {"handlers": ["console"], "level": "INFO", "propagate": False}}}
