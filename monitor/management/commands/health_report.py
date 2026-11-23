from datetime import date
from django.core.management.base import BaseCommand
from monitor.domain import build_report


class Command(BaseCommand):
    help = "Rebuild the report for a local business date"

    def add_arguments(self, parser):
        parser.add_argument("day", type=date.fromisoformat)

    def handle(self, day, **options):
        self.stdout.write(f"{day}: {build_report(day)} rows")
