from django.contrib.auth.models import Group
from django.urls import reverse

from apps.users.models import CustomUser

from .base import TestLoginRequiredViewBase, TestViewBase


class TestRoleBasedRedirects(TestViewBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager_group, _ = Group.objects.get_or_create(name="Spicy Manager")
        cls.cashier_group, _ = Group.objects.get_or_create(name="Spicy Cashier")

    def _make_user(
        self, username="testuser", email="test@example.com", is_staff=False, is_superuser=False, groups=None
    ):
        from apps.users.models import CustomUser

        user = CustomUser.objects.create_user(username=username, email=email, password="testpass")
        user.is_staff = is_staff
        user.is_superuser = is_superuser
        user.save()
        if groups:
            user.groups.set(groups)
        return user

    def _login_and_follow_redirect(self, user):
        self.client.login(username=user.username, password="testpass")
        response = self.client.get(reverse("web:home"))
        return response

    def test_manager_redirects_to_dashboard(self):
        user = self._make_user(username="test_manager", groups=[self.manager_group])
        response = self._login_and_follow_redirect(user)
        self.assertRedirects(response, reverse("web:dashboard"))

    def test_cashier_redirects_to_pos(self):
        user = self._make_user(username="test_cashier", groups=[self.cashier_group])
        response = self._login_and_follow_redirect(user)
        self.assertRedirects(response, reverse("web:pos_index"))

    def test_superuser_redirects_to_dashboard(self):
        user = self._make_user(username="test_superuser", is_superuser=True)
        response = self._login_and_follow_redirect(user)
        self.assertRedirects(response, reverse("web:dashboard"))

    def test_django_staff_without_spicy_role_redirects_to_pending(self):
        user = self._make_user(username="test_staff_user", is_staff=True)
        response = self._login_and_follow_redirect(user)
        self.assertRedirects(response, reverse("web:pending_approval"))

    def test_user_with_both_roles_is_manager(self):
        user = self._make_user(username="test_both", groups=[self.manager_group, self.cashier_group])
        self.assertTrue(user.has_backoffice_access)
        response = self._login_and_follow_redirect(user)
        self.assertRedirects(response, reverse("web:dashboard"))

    def test_no_role_user_redirects_to_pending_approval(self):
        user = self._make_user(username="test_newbie")
        response = self._login_and_follow_redirect(user)
        self.assertRedirects(response, reverse("web:pending_approval"))


class TestPendingApprovalView(TestViewBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = CustomUser.objects.create_user(username="pending@example.com", password="12345")

    def setUp(self):
        self.client.login(username="pending@example.com", password="12345")

    def test_staff_user_redirected_away_from_pending(self):
        manager_group, _ = Group.objects.get_or_create(name="Spicy Manager")
        self.user.groups.add(manager_group)
        response = self.client.get(reverse("web:pending_approval"))
        self.assertEqual(response.status_code, 302)


class TestDashboardView(TestLoginRequiredViewBase):
    def test_cashier_gets_403_on_dashboard(self):
        cashier_group, _ = Group.objects.get_or_create(name="Spicy Cashier")
        from apps.users.models import CustomUser

        CustomUser.objects.create_user(username="cash403@example.com", password="12345").groups.add(cashier_group)
        self.client.login(username="cash403@example.com", password="12345")
        response = self.client.get(reverse("web:dashboard"))
        self.assertEqual(response.status_code, 403)


class TestPOSView(TestLoginRequiredViewBase):
    def test_no_role_user_gets_403_on_pos(self):
        CustomUser.objects.create_user(username="norole403@example.com", password="12345")
        self.client.login(username="norole403@example.com", password="12345")
        response = self.client.get(reverse("web:pos_index"))
        self.assertEqual(response.status_code, 403)


class TestCustomUserProperties(TestViewBase):
    def test_has_backoffice_access(self):
        from apps.users.models import CustomUser

        su = CustomUser.objects.create_superuser(username="su@example.com", email="su@example.com")
        self.assertTrue(su.has_backoffice_access)

        staff = CustomUser.objects.create_user(username="staff2@example.com", email="staff2@example.com")
        staff.is_staff = True
        staff.save()
        self.assertFalse(staff.has_backoffice_access)

        mgr, _ = Group.objects.get_or_create(name="Spicy Manager")
        mgr_user = CustomUser.objects.create_user(username="mgr2@example.com", email="mgr2@example.com")
        mgr_user.groups.add(mgr)
        self.assertTrue(mgr_user.has_backoffice_access)

        cashier, _ = Group.objects.get_or_create(name="Spicy Cashier")
        cash_user = CustomUser.objects.create_user(username="cash2@example.com", email="cash2@example.com")
        cash_user.groups.add(cashier)
        self.assertFalse(cash_user.has_backoffice_access)

    def test_has_staff_role(self):
        from apps.users.models import CustomUser

        mgr, _ = Group.objects.get_or_create(name="Spicy Manager")
        cashier, _ = Group.objects.get_or_create(name="Spicy Cashier")
        mgr_user = CustomUser.objects.create_user(username="staffmgr@example.com", email="staffmgr@example.com")
        mgr_user.groups.add(mgr)
        self.assertTrue(mgr_user.has_staff_role)

        cash_user = CustomUser.objects.create_user(username="staffcash@example.com", email="staffcash@example.com")
        cash_user.groups.add(cashier)
        self.assertTrue(cash_user.has_staff_role)

        nobody = CustomUser.objects.create_user(username="nobody@example.com", email="nobody@example.com")
        self.assertFalse(nobody.has_staff_role)
        self.assertFalse(nobody.has_backoffice_access)
