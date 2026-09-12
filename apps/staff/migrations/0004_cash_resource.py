"""Add the ``cash`` resource to the built-in roles (add-only; custom roles are untouched)."""

from django.db import migrations

CASH = {
    "owner": ["create", "read", "update", "delete"],
    "manager": ["create", "read", "update", "delete"],
    "waiter": ["read", "create"],
}


def add_cash(apps, schema_editor):
    StaffRole = apps.get_model("staff", "StaffRole")
    for role in StaffRole.objects.filter(name__in=list(CASH)).iterator():
        perms = dict(role.permissions or {})
        if "cash" in perms:
            continue
        perms["cash"] = list(CASH[role.name])
        role.permissions = perms
        role.save(update_fields=["permissions"])


class Migration(migrations.Migration):

    dependencies = [
        ("staff", "0003_custom_roles"),
    ]

    operations = [
        migrations.RunPython(add_cash, migrations.RunPython.noop),
    ]
