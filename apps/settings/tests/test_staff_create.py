from django.contrib.auth import authenticate
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.users.models import CustomUser

STRONG_PASSWORD = "S3cure-p@ssword-451"


class StaffCreateTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = CustomUser.objects.create_superuser(
            username="admin@test.com", password="testpass123", email="admin@test.com"
        )
        cls.manager = CustomUser.objects.create_user(username="manager", password="testpass123")
        cls.manager.groups.add(Group.objects.get_or_create(name="Spicy Manager")[0])
        cls.cashier = CustomUser.objects.create_user(username="cashier", password="testpass123")
        cls.cashier.groups.add(Group.objects.get_or_create(name="Spicy Cashier")[0])

    def _create_payload(self, **kwargs):
        payload = {
            "username": "newbie",
            "first_name": "New",
            "last_name": "Bie",
            "password1": STRONG_PASSWORD,
            "password2": STRONG_PASSWORD,
            "role": "cashier",
        }
        payload.update(kwargs)
        return payload


class StaffCreateViewTest(StaffCreateTestBase):
    def setUp(self):
        self.client.login(username="admin@test.com", password="testpass123")

    def test_admin_creates_cashier(self):
        response = self.client.post(reverse("settings:staff_create"), self._create_payload())
        self.assertEqual(response.status_code, 302)
        user = CustomUser.objects.get(username="newbie")
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual({group.name for group in user.groups.all()}, {"Spicy Cashier"})
        self.assertTrue(user.has_staff_role)
        self.assertEqual(user.first_name, "New")

    def test_admin_creates_manager(self):
        self.client.post(reverse("settings:staff_create"), self._create_payload(username="mgr", role="manager"))
        user = CustomUser.objects.get(username="mgr")
        self.assertEqual({group.name for group in user.groups.all()}, {"Spicy Manager"})
        self.assertTrue(user.has_staff_role)

    def test_admin_creates_admin(self):
        self.client.post(reverse("settings:staff_create"), self._create_payload(username="boss", role="admin"))
        user = CustomUser.objects.get(username="boss")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
        self.assertEqual({group.name for group in user.groups.all()}, {"Spicy Admin"})

    def test_duplicate_username_rejected_any_case(self):
        CustomUser.objects.create_user(username="Newbie", password="testpass123")
        response = self.client.post(reverse("settings:staff_create"), self._create_payload(username="newbie"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CustomUser.objects.filter(username__iexact="newbie").count(), 1)

    def test_mismatched_passwords_rejected(self):
        response = self.client.post(
            reverse("settings:staff_create"), self._create_payload(password2="different-p@ss-999")
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomUser.objects.filter(username="newbie").exists())

    def test_weak_password_rejected(self):
        response = self.client.post(
            reverse("settings:staff_create"), self._create_payload(password1="123", password2="123")
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomUser.objects.filter(username="newbie").exists())


class StaffCreatePermissionTest(StaffCreateTestBase):
    def test_manager_and_cashier_get_403(self):
        for username in ("manager", "cashier"):
            self.client.login(username=username, password="testpass123")
            self.assertEqual(self.client.get(reverse("settings:staff_create")).status_code, 403)
            self.assertEqual(
                self.client.post(reverse("settings:staff_create"), self._create_payload()).status_code, 403
            )
            target = CustomUser.objects.create_user(username=f"target-{username}", password="testpass123")
            self.assertEqual(
                self.client.post(reverse("settings:staff_toggle_active", args=[target.pk])).status_code, 403
            )
            self.client.logout()


class StaffToggleActiveTest(StaffCreateTestBase):
    def setUp(self):
        self.client.login(username="admin@test.com", password="testpass123")

    def test_self_deactivation_refused(self):
        response = self.client.post(reverse("settings:staff_toggle_active", args=[self.admin.pk]))
        self.assertEqual(response.status_code, 302)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_deactivated_user_cannot_authenticate(self):
        target = CustomUser.objects.create_user(username="leaver", password="testpass123")
        self.client.post(reverse("settings:staff_toggle_active", args=[target.pk]))
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertIsNone(authenticate(username="leaver", password="testpass123"))
        self.client.post(reverse("settings:staff_toggle_active", args=[target.pk]))
        target.refresh_from_db()
        self.assertTrue(target.is_active)

    def test_role_removal_flow_untouched(self):
        target = CustomUser.objects.create_user(username="leaver", password="testpass123")
        target.groups.add(Group.objects.get_or_create(name="Spicy Cashier")[0])
        self.client.post(reverse("settings:staff_remove_role", args=[target.pk]))
        target.refresh_from_db()
        self.assertTrue(target.is_active)
        self.assertEqual(target.groups.count(), 0)
