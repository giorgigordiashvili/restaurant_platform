"""Add the ``fiscal`` resource to owner (full) and manager (read + update); custom roles untouched."""

from django.db import migrations

FISCAL = {"owner": ["create", "read", "update", "delete"], "manager": ["read", "update"]}


def add_fiscal(apps, schema_editor):
    StaffRole = apps.get_model("staff", "StaffRole")
    for role in StaffRole.objects.filter(name__in=list(FISCAL)).iterator():
        perms = dict(role.permissions or {})
        if "fiscal" in perms:
            continue
        perms["fiscal"] = list(FISCAL[role.name])
        role.permissions = perms
        role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [("staff", "0004_cash_resource")]
    operations = [migrations.RunPython(add_fiscal, migrations.RunPython.noop)]
