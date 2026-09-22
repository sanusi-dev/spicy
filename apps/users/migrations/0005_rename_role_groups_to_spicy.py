"""Rename the seeded role groups from the old brand to Spicy."""

from django.db import migrations

ROLE_RENAMES = [
    ("RestPOS Admin", "Spicy Admin"),
    ("RestPOS Manager", "Spicy Manager"),
    ("RestPOS Cashier", "Spicy Cashier"),
]


def rename_role_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for old_name, new_name in ROLE_RENAMES:
        Group.objects.filter(name=old_name).update(name=new_name)


def restore_role_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for old_name, new_name in ROLE_RENAMES:
        Group.objects.filter(name=new_name).update(name=old_name)


class Migration(migrations.Migration):
    dependencies = [("users", "0004_add_admin_role")]

    operations = [migrations.RunPython(rename_role_groups, restore_role_groups)]
