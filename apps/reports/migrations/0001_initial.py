from django.db import migrations


def _proxy(name, base, verbose):
    return migrations.CreateModel(
        name=name,
        fields=[],
        options={
            "verbose_name": verbose,
            "verbose_name_plural": verbose,
            "proxy": True,
            "indexes": [],
            "constraints": [],
        },
        bases=(base,),
    )


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("orders", "0008_discounts_voids"),
        ("inventory", "0003_seed_beat"),
        ("payments", "0008_payment_restaurant_not_null"),
        ("reservations", "0002_alter_reservationhistory_options_and_more"),
        ("reviews", "0001_initial"),
    ]

    operations = [
        _proxy("SalesReport", "orders.order", "Sales"),
        _proxy("MenuReport", "orders.orderitem", "Menu & dishes"),
        _proxy("FoodCostReport", "inventory.stockmovement", "Food cost"),
        _proxy("StaffReport", "orders.order", "Staff"),
        _proxy("ShiftsReport", "payments.cashshift", "Cash shifts"),
        _proxy("ReservationsReport", "reservations.reservation", "Reservations"),
        _proxy("ReviewsReport", "reviews.review", "Reviews"),
    ]
