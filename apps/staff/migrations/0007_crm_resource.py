"""``crm`` resource: owner and manager full."""

from django.db import migrations

CRM = {"owner": ["create", "read", "update", "delete"], "manager": ["create", "read", "update", "delete"]}


def add_crm(apps, schema_editor):
    StaffRole = apps.get_model("staff", "StaffRole")
    for role in StaffRole.objects.filter(name__in=list(CRM)).iterator():
        perms = dict(role.permissions or {})
        if "crm" in perms:
            continue
        perms["crm"] = list(CRM[role.name])
        role.permissions = perms
        role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [("staff", "0006_timekeeping")]
    operations = [migrations.RunPython(add_crm, migrations.RunPython.noop)]
