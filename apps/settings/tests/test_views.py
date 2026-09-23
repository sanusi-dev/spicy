from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.inventory.models import Warehouse
from apps.settings.models import ProductionUnit, Restaurant
from apps.users.models import CustomUser


class SettingsViewTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = CustomUser.objects.create_user(
            username="admin@test.com", password="testpass123", email="admin@test.com"
        )
        mgr, _ = Group.objects.get_or_create(name="Spicy Manager")
        cls.user.groups.add(mgr)

    def setUp(self):
        self.client.login(username="admin@test.com", password="testpass123")


class TestDashboardView(SettingsViewTestBase):
    def _post_data(self, **overrides):
        data = {
            "company": "Test Co",
            "invoice_series_prefix": "REST-",
            "address": "123 Street",
            "active_menu": "",
            "store_warehouse": "",
            "default_warehouse": "",
            "max_open_drafts": "50",
        }
        data.update(overrides)
        return data

    def test_post_updates_singleton(self):
        restaurant = Restaurant.objects.create(company="Test Co")
        response = self.client.post(reverse("settings:restaurant_settings"), self._post_data(company="Updated Co"))
        self.assertRedirects(response, reverse("settings:restaurant_settings"))
        restaurant.refresh_from_db()
        self.assertEqual(restaurant.company, "Updated Co")
        self.assertEqual(Restaurant.objects.count(), 1)

    def test_cashier_cannot_post(self):
        cashier = CustomUser.objects.create_user(username="cashier@test.com", password="testpass123")
        cashier_group, _ = Group.objects.get_or_create(name="Spicy Cashier")
        cashier.groups.add(cashier_group)
        self.client.login(username="cashier@test.com", password="testpass123")
        response = self.client.post(reverse("settings:restaurant_settings"), self._post_data())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Restaurant.objects.exists())


class TestProductionUnitViews(SettingsViewTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.warehouse = Warehouse.objects.create(name="Kitchen")
        cls.unit = ProductionUnit.objects.create(name="Kitchen", warehouse=cls.warehouse, department="FOOD")

    def test_list_does_not_filter_by_department(self):
        unit_url = reverse("settings:production_unit_detail", kwargs={"pk": self.unit.pk})
        response = self.client.get(reverse("settings:production_unit_list"), {"department": "DRINKS"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, unit_url)


class TestStaffManagementViews(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = CustomUser.objects.create_superuser(
            username="admin@test.com", password="testpass123", email="admin@test.com"
        )
        cls.manager_user = CustomUser.objects.create_user(
            username="manager@test.com", password="testpass123", email="manager@test.com"
        )
        cls.cashier = CustomUser.objects.create_user(
            username="cashier@test.com", password="testpass123", email="cashier@test.com"
        )
        cls.newbie = CustomUser.objects.create_user(
            username="newbie@test.com", password="testpass123", email="newbie@test.com"
        )
        cls.admin_group, _ = Group.objects.get_or_create(name="Spicy Admin")
        cls.manager_group, _ = Group.objects.get_or_create(name="Spicy Manager")
        cls.cashier_group, _ = Group.objects.get_or_create(name="Spicy Cashier")
        cls.manager_user.groups.add(cls.manager_group)

    def setUp(self):
        self.client.login(username="admin@test.com", password="testpass123")

    def test_staff_list_requires_backoffice_access(self):
        self.client.logout()
        self.client.login(username="newbie@test.com", password="testpass123")
        response = self.client.get(reverse("settings:staff_list"))
        self.assertEqual(response.status_code, 403)

    def test_assign_cashier_role(self):
        response = self.client.post(
            reverse("settings:staff_assign_role", kwargs={"pk": self.newbie.pk, "role": "cashier"})
        )
        self.assertRedirects(response, reverse("settings:staff_list"))
        self.assertTrue(self.newbie.groups.filter(name="Spicy Cashier").exists())

    def test_assign_admin_role(self):
        response = self.client.post(
            reverse("settings:staff_assign_role", kwargs={"pk": self.newbie.pk, "role": "admin"})
        )
        self.assertRedirects(response, reverse("settings:staff_list"))
        self.newbie.refresh_from_db()
        self.assertTrue(self.newbie.groups.filter(name="Spicy Admin").exists())
        self.assertTrue(self.newbie.is_superuser)
        self.assertTrue(self.newbie.is_staff)

    def test_remove_role(self):
        self.cashier.groups.add(self.cashier_group)
        response = self.client.post(reverse("settings:staff_remove_role", kwargs={"pk": self.cashier.pk}))
        self.assertRedirects(response, reverse("settings:staff_list"))
        self.assertFalse(self.cashier.groups.filter(name="Spicy Cashier").exists())

    def test_remove_role_htmx_returns_targeted_row(self):
        self.cashier.groups.add(self.cashier_group)
        target = f"staff-row-{self.cashier.pk}"
        response = self.client.post(
            reverse("settings:staff_remove_role", kwargs={"pk": self.cashier.pk}),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET=target,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'<tr id="{target}"')
        self.assertContains(response, "No role")
        self.assertNotContains(response, "User roles")

    def test_role_mutation_with_boosted_body_target_redirects(self):
        response = self.client.post(
            reverse("settings:staff_assign_role", kwargs={"pk": self.newbie.pk, "role": "cashier"}),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="body",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], reverse("settings:staff_list"))

        self.cashier.groups.add(self.cashier_group)
        response = self.client.post(
            reverse("settings:staff_remove_role", kwargs={"pk": self.cashier.pk}),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="body",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], reverse("settings:staff_list"))

    def test_cannot_remove_admin(self):
        self.admin_user.groups.add(self.admin_group)
        response = self.client.post(reverse("settings:staff_remove_role", kwargs={"pk": self.admin_user.pk}))
        self.assertRedirects(response, reverse("settings:staff_list"))
        self.assertTrue(self.admin_user.groups.filter(name="Spicy Admin").exists())

    def test_non_superuser_cannot_assign(self):
        self.client.logout()
        self.client.login(username="manager@test.com", password="testpass123")
        response = self.client.post(
            reverse("settings:staff_assign_role", kwargs={"pk": self.newbie.pk, "role": "cashier"})
        )
        self.assertEqual(response.status_code, 403)

    def test_non_superuser_cannot_remove(self):
        self.cashier.groups.add(self.cashier_group)
        self.client.logout()
        self.client.login(username="manager@test.com", password="testpass123")
        response = self.client.post(reverse("settings:staff_remove_role", kwargs={"pk": self.cashier.pk}))
        self.assertEqual(response.status_code, 403)

    def test_staff_list_search(self):
        response = self.client.get(reverse("settings:staff_list"), {"search": "cashier"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cashier@test.com")
        self.assertNotContains(response, "newbie@test.com")
