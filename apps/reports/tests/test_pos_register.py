"""POS register: submitted closes, displayed netting, filters."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.reports.register_reports import pos_register
from apps.staff.models import ClosingPayment, POSClosingEntry, POSOpeningEntry

from .helpers import DailyPnLTestMixin


class POSRegisterTest(DailyPnLTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls._setup_pnl_world()
        mgr, _ = Group.objects.get_or_create(name="Spicy Manager")
        cls.manager.groups.add(mgr)
        cls.day = date(2026, 9, 10)

    def _submitted_close(self, *, posting_date, cashier, expected="50000", counted="49800", difference="-200"):
        opening = POSOpeningEntry.objects.create(
            cashier=cashier, posting_date=posting_date, status=POSOpeningEntry.SUBMITTED
        )
        closing = POSClosingEntry.objects.create(
            opening_entry=opening,
            cashier=cashier,
            posting_date=posting_date,
            status=POSClosingEntry.DRAFT,
            total_short_excess=Decimal(difference),
        )
        ClosingPayment.objects.create(
            closing_entry=closing,
            mode_of_payment=self.cash,
            expected_amount=Decimal(expected),
            closing_amount=Decimal(counted),
            difference=Decimal(difference),
        )
        # ORM immutability: submitted closes reject child-row saves, so stamp the status last via update.
        POSClosingEntry.objects.filter(pk=closing.pk).update(status=POSClosingEntry.SUBMITTED)
        closing.refresh_from_db()
        return closing

    def test_submitted_rows_display_stored_netting(self):
        closing = self._submitted_close(posting_date=self.day, cashier=self.user)
        POSClosingEntry.objects.create(
            opening_entry=self.opening,
            cashier=self.user,
            posting_date=self.day,
            status=POSClosingEntry.DRAFT,
            total_short_excess=Decimal("999"),
        )
        rows, totals = pos_register(self.day, self.day)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pk"], closing.pk)
        self.assertEqual(rows[0]["total_short_excess"], Decimal("-200.00"))
        self.assertEqual(rows[0]["payments"][0]["expected"], Decimal("50000.00"))
        self.assertEqual(rows[0]["payments"][0]["counted"], Decimal("49800.00"))
        self.assertEqual(rows[0]["payments"][0]["difference"], Decimal("-200.00"))
        self.assertEqual(totals["closes"], 1)
        self.assertEqual(totals["total_short_excess"], Decimal("-200.00"))

    def test_filters_by_cashier_and_date(self):
        other = type(self.user).objects.create_user(username="closer-two", password="testpass123")
        mine = self._submitted_close(posting_date=self.day, cashier=self.user)
        self._submitted_close(posting_date=self.day, cashier=other, difference="50", counted="50050", expected="50000")
        self._submitted_close(posting_date=self.day + timedelta(days=1), cashier=self.user)
        rows, _totals = pos_register(self.day, self.day, cashier_id=self.user.pk)
        self.assertEqual([row["pk"] for row in rows], [mine.pk])

    def test_cancelled_close_excluded(self):
        opening = POSOpeningEntry.objects.create(
            cashier=self.user, posting_date=self.day, status=POSOpeningEntry.SUBMITTED
        )
        POSClosingEntry.objects.create(
            opening_entry=opening,
            cashier=self.user,
            posting_date=self.day,
            status=POSClosingEntry.CANCELLED,
            total_short_excess=Decimal("-10"),
        )
        rows, totals = pos_register(self.day, self.day)
        self.assertEqual(rows, [])
        self.assertEqual(totals["closes"], 0)

    def test_manager_page_renders_and_cashier_forbidden(self):
        self._submitted_close(posting_date=date.today(), cashier=self.user)
        self.client.force_login(self.user)
        self.assertIn(self.client.get(reverse("reports:pos_register")).status_code, (302, 403))
        self.client.force_login(self.manager)
        response = self.client.get(reverse("reports:pos_register"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Close #")
        self.assertContains(response, "50,000.00")
