from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class Environment(models.Model):
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=80)


class Team(models.Model):
    name = models.CharField(max_length=100, unique=True)


class Ownership(models.Model):
    environment = models.ForeignKey(Environment, on_delete=models.PROTECT)
    team = models.ForeignKey(Team, on_delete=models.PROTECT)

    class Meta:
        abstract = True


class Service(Ownership):
    name = models.CharField(max_length=120)
    base_url = models.CharField(max_length=255)
    health_path = models.CharField(max_length=500)
    expected_status = models.PositiveSmallIntegerField(default=200, validators=[
        MinValueValidator(100), MaxValueValidator(599)])
    created_at = models.DateTimeField(default=timezone.now)
    archived_at = models.DateTimeField(null=True)

    class Meta:
        unique_together = [("environment", "name")]


class Assignment(Ownership):
    service = models.ForeignKey(Service, on_delete=models.CASCADE)
    start = models.DateTimeField()
    end = models.DateTimeField(null=True)


class ServiceToken(models.Model):
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="tokens")
    version = models.PositiveIntegerField()
    ciphertext = models.BinaryField()
    created_at = models.DateTimeField(default=timezone.now)
    rotation_slot = models.DateTimeField()

    class Meta:
        unique_together = [("service", "version"), ("service", "rotation_slot")]


class CheckRun(Ownership):
    service = models.ForeignKey(Service, on_delete=models.PROTECT)
    token_version = models.PositiveIntegerField()
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, db_index=True)
    duration_ms = models.PositiveIntegerField(null=True)
    status = models.CharField(max_length=8, default="running", choices=[
        ("running", "Running"), ("success", "Success"), ("failed", "Failed")])
    http_status = models.PositiveSmallIntegerField(null=True)
    error_code = models.CharField(max_length=32, blank=True)

    class Meta:
        indexes = [models.Index(fields=["service", "finished_at"])]


class DailyHealthStat(Ownership):
    stat_date = models.DateField(db_index=True)
    service = models.ForeignKey(Service, on_delete=models.PROTECT)
    total_count = models.PositiveIntegerField()
    success_count = models.PositiveIntegerField()
    health_rate = models.DecimalField(max_digits=9, decimal_places=8, null=True)

    class Meta:
        unique_together = [("stat_date", "service", "environment", "team")]
