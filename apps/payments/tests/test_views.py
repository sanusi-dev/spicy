from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.payments.models import ModeOfPayment, PaymentGLMapping
from apps.users.models import CustomUser

from .helpers import create_payment_accounts


class PaymentsViewTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = CustomUser.objects.create_user(
            username="admin@test.com", password="testpass123", email="admin@test.com"
        )
        mgr, _ = Group.objects.get_or_create(name="Spicy Manager")
        cls.user.groups.add(mgr)
        cls.accounts = create_payment_accounts()
        # Use test-only names so we don't conflict with the seed migration's defaults.
        cls.cash = ModeOfPayment.objects.create(name="Test Cash", type="CASH")
        cls.bank = ModeOfPayment.objects.create(name="Test Bank", type="BANK")
        cls.mapping = PaymentGLMapping.objects.create(
            mode_of_payment=cls.cash,
            default_account=cls.accounts["cash"],
        )

    def setUp(self):
        self.client.login(username="admin@test.com", password="testpass123")


class TestLoginRequired(TestCase):
    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("payments:dashboard"))
        self.assertEqual(response.status_code, 302)

    def test_mode_list_requires_login(self):
        response = self.client.get(reverse("payments:mode_list"))
        self.assertEqual(response.status_code, 302)

    def test_mode_create_requires_login(self):
        response = self.client.get(reverse("payments:mode_create"))
        self.assertEqual(response.status_code, 302)

    def test_gl_mapping_list_requires_login(self):
        response = self.client.get(reverse("payments:gl_mapping_list"))
        self.assertEqual(response.status_code, 302)


class TestModeOfPaymentViews(PaymentsViewTestBase):
    def test_mode_create_post(self):
        response = self.client.post(
            reverse("payments:mode_create"),
            data={"name": "Opay Transfer", "type": "BANK", "enabled": "on"},
        )
        # Redirect target pk is unknown without counting; assert the redirect
        # happened and the new mode exists.
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ModeOfPayment.objects.filter(name="Opay Transfer").exists())

    def test_mode_update_post(self):
        response = self.client.post(
            reverse("payments:mode_update", kwargs={"pk": self.cash.pk}),
            data={"name": "Test Cash", "type": "CASH", "enabled": ""},
        )
        self.assertRedirects(response, reverse("payments:mode_detail", kwargs={"pk": self.cash.pk}))
        self.cash.refresh_from_db()
        self.assertFalse(self.cash.enabled)


class TestPaymentGLMappingViews(PaymentsViewTestBase):
    def test_gl_mapping_create_post(self):
        response = self.client.post(
            reverse("payments:gl_mapping_create"),
            data={
                "mode_of_payment": self.bank.pk,
                "default_account": self.accounts["bank"].pk,
            },
        )
        self.assertRedirects(response, reverse("payments:gl_mapping_list"))
        self.assertTrue(PaymentGLMapping.objects.filter(default_account=self.accounts["bank"]).exists())

    def test_gl_mapping_update_post(self):
        response = self.client.post(
            reverse("payments:gl_mapping_update", kwargs={"pk": self.mapping.pk}),
            data={
                "mode_of_payment": self.cash.pk,
                "default_account": self.accounts["bank"].pk,
            },
        )
        self.assertRedirects(response, reverse("payments:gl_mapping_list"))
        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.default_account, self.accounts["bank"])

    def test_gl_mapping_delete_post(self):
        pk = self.mapping.pk
        response = self.client.post(reverse("payments:gl_mapping_delete", kwargs={"pk": pk}))
        self.assertRedirects(response, reverse("payments:gl_mapping_list"))
        self.assertFalse(PaymentGLMapping.objects.filter(pk=pk).exists())

    def test_gl_mapping_delete_requires_post(self):
        response = self.client.get(reverse("payments:gl_mapping_delete", kwargs={"pk": self.mapping.pk}))
        self.assertEqual(response.status_code, 405)
