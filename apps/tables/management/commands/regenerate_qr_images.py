"""Re-render QR images so they encode the dynamic short link (dry run by default)."""

from django.core.management.base import BaseCommand

from apps.tables.models import TableQRCode
from apps.venues.models import VenueTable


class Command(BaseCommand):
    help = "Regenerate QR images that still encode a legacy direct URL (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write the new images (default: report only)")
        parser.add_argument("--restaurant", help="Only this restaurant's table codes (slug)")
        parser.add_argument("--all", action="store_true", help="Also regenerate images that are already short links")
        parser.add_argument("--skip-venues", action="store_true", help="Leave shared-venue table codes alone")

    def handle(self, *args, **options):
        apply, include_all = options["apply"], options["all"]
        qrs = TableQRCode.objects.filter(is_active=True).select_related("table__restaurant")
        if options["restaurant"]:
            qrs = qrs.filter(table__restaurant__slug=options["restaurant"])
        venue_tables = (
            [] if options["skip_venues"] else list(VenueTable.objects.filter(is_active=True).select_related("venue"))
        )

        todo = [q for q in qrs if include_all or not q.image_is_current]
        todo_v = [v for v in venue_tables if include_all or not v.image_is_current]
        self.stdout.write(f"table codes: {qrs.count()} total, {len(todo)} to regenerate")
        self.stdout.write(f"venue tables: {len(venue_tables)} total, {len(todo_v)} to regenerate")
        if not apply:
            self.stdout.write(self.style.WARNING("Dry run; re-run with --apply to write images."))
            return
        for obj in todo + todo_v:
            obj.regenerate_qr_image()
        self.stdout.write(self.style.SUCCESS(f"Regenerated {len(todo) + len(todo_v)} image(s)."))
