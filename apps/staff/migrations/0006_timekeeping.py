"""hourly_rate on staff members + the ``timekeeping`` resource (owner / manager full, everyone else read + create)."""

from django.db import migrations, models

TIMEKEEPING = {
    "owner": ["create", "read", "update", "delete"],
    "manager": ["create", "read", "update", "delete"],
    "warehouse_manager": ["create", "read"],
    "kitchen": ["create", "read"],
    "bar": ["create", "read"],
    "waiter": ["create", "read"],
}


def add_resource(apps, schema_editor):
    StaffRole = apps.get_model("staff", "StaffRole")
    for role in StaffRole.objects.filter(name__in=list(TIMEKEEPING)).iterator():
        perms = dict(role.permissions or {})
        if "timekeeping" in perms:
            continue
        perms["timekeeping"] = list(TIMEKEEPING[role.name])
        role.permissions = perms
        role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [("staff", "0005_fiscal_resource")]

    operations = [
        migrations.AddField(
            model_name="staffmember",
            name="hourly_rate",
            field=models.DecimalField(
                blank=True, decimal_places=2, help_text="For the hours report (labour cost).", max_digits=8, null=True
            ),
        ),
        migrations.RunPython(add_resource, migrations.RunPython.noop),
    ]
