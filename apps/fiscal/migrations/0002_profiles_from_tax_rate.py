"""Existing restaurants keep today's totals: an exclusive tax_rate becomes an exclusive VAT-payer profile."""

from decimal import Decimal

from django.db import migrations


def forwards(apps, schema_editor):
    Restaurant = apps.get_model("tenants", "Restaurant")
    FiscalProfile = apps.get_model("fiscal", "FiscalProfile")
    for r in Restaurant.objects.all().iterator(chunk_size=200):
        rate = Decimal(r.tax_rate or 0)
        FiscalProfile.objects.get_or_create(
            restaurant=r,
            defaults=(
                {"vat_payer": True, "vat_rate": rate, "prices_include_vat": False}
                if rate > 0
                else {"vat_payer": False, "vat_rate": Decimal("18.00"), "prices_include_vat": True}
            ),
        )


class Migration(migrations.Migration):
    dependencies = [("fiscal", "0001_initial")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
