"""Daily P&L view gate and submit flow."""

from datetime import date

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.reports.models import DailyPnL

from .helpers import DailyPnLTestMixin


class DailyPnLViewTest(DailyPnLTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls._setup_pnl_world()
        mgr, _ = Group.objects.get_or_create(name="Spicy Manager")
        cls.manager.groups.add(mgr)

    def test_cashier_forbidden(self):
        self.client.force_login(self.user)
        for name in ("reports:daily_pnl_list", "reports:pnl_settings", "reports:daily_pnl_create"):
            response = self.client.get(reverse(name))
            self.assertIn(response.status_code, (302, 403), name)

    def test_submit_via_post(self):
        self.client.force_login(self.manager)
        pnl = DailyPnL.objects.create(business_date=date.today())
        response = self.client.post(reverse("reports:daily_pnl_submit", args=[pnl.pk]))
        pnl.refresh_from_db()
        self.assertEqual(pnl.status, DailyPnL.SUBMITTED)
        self.assertRedirects(response, reverse("reports:daily_pnl_detail", args=[pnl.pk]))
        self.assertEqual(self.client.get(reverse("reports:daily_pnl_detail", args=[pnl.pk])).status_code, 200)
