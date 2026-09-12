"""
Recompute StockItem.on_hand_qty / reserved_qty from the ledger and active
reservation lines. Reports (and fixes) any drift.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.inventory import services
from apps.tenants.models import Restaurant


class Command(BaseCommand):
    help = "Rebuild the cached stock counters from the movement ledger."

    def add_arguments(self, parser):
        parser.add_argument("--restaurant", help="Restaurant slug (default: all restaurants).")

    def handle(self, *args, **options):
        restaurant = None
        if options["restaurant"]:
            restaurant = Restaurant.objects.filter(slug=options["restaurant"]).first()
            if restaurant is None:
                raise CommandError(f"Unknown restaurant slug: {options['restaurant']}")
        drift = services.rebuild_stock_cache(restaurant)
        if not drift:
            self.stdout.write(self.style.SUCCESS("Stock cache is consistent with the ledger."))
            return
        for row in drift:
            item = row["stock_item"]
            self.stdout.write(
                f"{item.restaurant.slug} / {item.name}: on_hand {row['on_hand'][0]} -> {row['on_hand'][1]}, "
                f"reserved {row['reserved'][0]} -> {row['reserved'][1]}"
            )
        self.stdout.write(self.style.WARNING(f"Fixed {len(drift)} item(s)."))
