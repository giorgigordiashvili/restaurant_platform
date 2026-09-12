"""Re-run the registry -> member mirroring for every venue and report drift."""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.venues import services
from apps.venues.models import Venue


class Command(BaseCommand):
    help = "Re-sync every member restaurant's tables with its venue registry (dry-run by default)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Persist changes (default: dry run)")

    def handle(self, *args, **options):
        apply = options["apply"]
        for venue in Venue.objects.filter(is_active=True).order_by("name"):
            with transaction.atomic():
                summary = services.sync_all(venue)
                self.stdout.write(
                    f"{venue.slug}: created={summary.created} linked={summary.linked} updated={summary.updated} "
                    f"conflicts={len(summary.conflicts)}"
                )
                if not apply:
                    transaction.set_rollback(True)
        if not apply:
            self.stdout.write(self.style.WARNING("Dry run; re-run with --apply to persist."))
