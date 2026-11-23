from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from monitor.domain import rotate
from monitor.models import Service


class Command(BaseCommand):
    help = "Immediately rotate the token for one active service"

    def add_arguments(self, parser):
        parser.add_argument("service_id", type=int)

    def handle(self, service_id, **options):
        try:
            service = Service.objects.get(pk=service_id, archived_at=None)
        except Service.DoesNotExist:
            raise CommandError("找不到有效服务")
        rotate(service.pk, timezone.now())
        self.stdout.write(f"Token version: {service.tokens.latest('version').version}")
