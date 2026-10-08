from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.users.models import CustomUser


class PromoteUserToSuperuserTest(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(username="staffer", password="testpass123")

    def test_yes_flag_promotes_and_logs(self):
        out = StringIO()
        with self.assertLogs("apps.users.management.commands.promote_user_to_superuser", level="WARNING") as logs:
            call_command("promote_user_to_superuser", "staffer", yes=True, stdout=out)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_superuser)
        self.assertTrue(self.user.is_staff)
        self.assertIn("granted superuser to username=staffer", logs.output[0])

    def test_declined_confirm_aborts(self):
        with patch("builtins.input", return_value="n"), self.assertRaises(CommandError):
            call_command("promote_user_to_superuser", "staffer")
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_superuser)

    def test_approved_confirm_promotes(self):
        with patch("builtins.input", return_value="y"):
            call_command("promote_user_to_superuser", "staffer")
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_superuser)

    def test_unknown_username_fails(self):
        with self.assertRaises(CommandError):
            call_command("promote_user_to_superuser", "ghost")
