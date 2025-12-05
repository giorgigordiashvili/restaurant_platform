"""
Management command to create StaffMember records for restaurant owners who are missing them.
"""

from django.core.management.base import BaseCommand

from apps.staff.models import StaffMember, StaffRole
from apps.tenants.models import Restaurant


class Command(BaseCommand):
    help = "Create StaffMember records for restaurant owners who are missing them"

    def handle(self, *args, **options):
        restaurants = Restaurant.objects.filter(is_active=True)
        fixed = 0

        for restaurant in restaurants:
            owner = restaurant.owner
            if not owner:
                continue

            # Check if owner already has StaffMember for this restaurant
            exists = StaffMember.objects.filter(user=owner, restaurant=restaurant).exists()

            if not exists:
                # Get or create owner role
                owner_role, _ = StaffRole.objects.get_or_create(
                    restaurant=restaurant,
                    name="owner",
                    defaults={"display_name": "Owner", "is_system_role": True},
                )

                StaffMember.objects.create(
                    user=owner,
                    restaurant=restaurant,
                    role=owner_role,
                    is_active=True,
                )

                # Make user a Django staff member
                if not owner.is_staff:
                    owner.is_staff = True
                    owner.save(update_fields=["is_staff"])

                fixed += 1
                self.stdout.write(f"Fixed: {owner.email} -> {restaurant.name}")

        self.stdout.write(self.style.SUCCESS(f"Fixed {fixed} restaurant owners"))
