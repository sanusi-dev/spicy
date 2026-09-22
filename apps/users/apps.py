from django.apps import AppConfig


class UserConfig(AppConfig):
    name = "apps.users"
    label = "users"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from django.contrib.auth.models import Group
        from django.db.models.signals import post_migrate

        # These names are the stable role identifiers used by CustomUser's
        # permission properties, so keep seed data and authorization aligned.
        def create_roles(**kwargs):
            Group.objects.get_or_create(name="Spicy Admin")
            Group.objects.get_or_create(name="Spicy Manager")
            Group.objects.get_or_create(name="Spicy Cashier")

        post_migrate.connect(create_roles)

        from . import signals  # noqa F401
