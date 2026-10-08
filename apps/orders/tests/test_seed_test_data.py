from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.users.models import CustomUser


class SeedTestDataGuardTest(TestCase):
    """The demo seed must not run with DEBUG off — the production default."""

    def test_refuses_without_debug(self):
        with self.assertRaises(CommandError):
            call_command("seed_test_data")
        self.assertFalse(CustomUser.objects.filter(username="cashier").exists())
