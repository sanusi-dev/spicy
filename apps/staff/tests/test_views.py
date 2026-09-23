from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.accounting.tests.helpers import setup_chart_of_accounts
from apps.payments.models import ModeOfPayment, PaymentGLMapping
from apps.settings.models import Restaurant
from apps.staff.models import OpeningPayment, POSOpeningEntry
from apps.staff.services import submit_closing_entry
from apps.users.models import CustomUser


class StaffViewTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = CustomUser.objects.create_user(
            username="manager@test.com", password="testpass123", email="manager@test.com"
        )
        mgr, _ = Group.objects.get_or_create(name="Spicy Manager")
        cls.user.groups.add(mgr)
        cls.restaurant = Restaurant.objects.create(company="Staff Views Co")
        cls.accounts = setup_chart_of_accounts(cls.restaurant)
        cls.cash_mode = ModeOfPayment.objects.create(name="Test Cash", type="CASH")
        cls.bank_mode = ModeOfPayment.objects.create(name="Test Bank", type="BANK")
        PaymentGLMapping.objects.create(mode_of_payment=cls.cash_mode, default_account=cls.accounts["cash"])
        PaymentGLMapping.objects.create(mode_of_payment=cls.bank_mode, default_account=cls.accounts["bank"])
        cls.entry = POSOpeningEntry.objects.create(
            cashier=cls.user,
            posting_date="2026-07-24",
        )
        OpeningPayment.objects.create(
            opening_entry=cls.entry,
            mode_of_payment=cls.cash_mode,
            opening_amount=Decimal("50000"),
        )
        OpeningPayment.objects.create(
            opening_entry=cls.entry,
            mode_of_payment=cls.bank_mode,
            opening_amount=Decimal("0"),
        )

    def setUp(self):
        self.client.login(username="manager@test.com", password="testpass123")


class TestShiftPagesRequireBackofficeAccess(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cashier = CustomUser.objects.create_user(
            username="cashier@test.com", password="testpass123", email="cashier@test.com"
        )
        cashier_group, _ = Group.objects.get_or_create(name="Spicy Cashier")
        cls.cashier.groups.add(cashier_group)

    def setUp(self):
        self.client.login(username="cashier@test.com", password="testpass123")

    def test_cashier_gets_403_on_shift_pages(self):
        urls = [
            reverse("staff:dashboard"),
            reverse("staff:opening_entry_list"),
            reverse("staff:opening_entry_create"),
            reverse("staff:closing_entry_list"),
            reverse("staff:closing_entry_create"),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_cashier_gets_403_on_shift_mutations(self):
        self.assertEqual(self.client.post(reverse("staff:opening_entry_submit", kwargs={"pk": 1})).status_code, 403)
        self.assertEqual(self.client.post(reverse("staff:opening_entry_cancel", kwargs={"pk": 1})).status_code, 403)
        self.assertEqual(self.client.post(reverse("staff:closing_entry_submit", kwargs={"pk": 1})).status_code, 403)
        self.assertEqual(self.client.post(reverse("staff:closing_entry_cancel", kwargs={"pk": 1})).status_code, 403)


class TestPOSOpeningEntryViews(StaffViewTestBase):
    def test_create_post_captures_all_methods(self):
        """Both cash and electronic mode opening balances are persisted."""
        response = self.client.post(
            reverse("staff:opening_entry_create"),
            data={
                f"mop_{self.cash_mode.pk}": "25000",
                f"mop_{self.bank_mode.pk}": "120000",
            },
        )
        self.assertEqual(response.status_code, 302)
        entry = POSOpeningEntry.objects.exclude(pk=self.entry.pk).get()
        active_count = ModeOfPayment.objects.filter(enabled=True).count()
        self.assertEqual(entry.opening_payments.count(), active_count)
        self.assertEqual(
            entry.opening_payments.get(mode_of_payment=self.cash_mode).opening_amount,
            Decimal("25000"),
        )
        self.assertEqual(
            entry.opening_payments.get(mode_of_payment=self.bank_mode).opening_amount,
            Decimal("120000"),
        )

    def test_create_post_blocks_when_no_modes_configured(self):
        """Re-render with error if no active ModeOfPayment exists."""
        ModeOfPayment.objects.update(enabled=False)
        response = self.client.post(reverse("staff:opening_entry_create"), data={})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No active payment methods")
        self.assertEqual(POSOpeningEntry.objects.count(), 1)

    def test_detail_post_saves_amounts(self):
        """POST to the detail URL saves the edited opening amounts (PRG)."""
        response = self.client.post(
            reverse("staff:opening_entry_detail", kwargs={"pk": self.entry.pk}),
            data={
                f"mop_{self.cash_mode.pk}": "99999",
                f"mop_{self.bank_mode.pk}": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.entry.refresh_from_db()
        self.assertEqual(
            self.entry.opening_payments.get(mode_of_payment=self.cash_mode).opening_amount,
            Decimal("99999"),
        )

    def test_detail_post_blocked_when_submitted(self):
        """POST to a SUBMITTED opening's detail URL is rejected — the entry is immutable once open."""
        self.entry.submit()
        response = self.client.post(
            reverse("staff:opening_entry_detail", kwargs={"pk": self.entry.pk}),
            data={f"mop_{self.cash_mode.pk}": "1"},
        )
        self.assertEqual(response.status_code, 302)
        self.entry.refresh_from_db()
        self.assertEqual(
            self.entry.opening_payments.get(mode_of_payment=self.cash_mode).opening_amount,
            Decimal("50000"),
        )

    def test_submit_post(self):
        response = self.client.post(reverse("staff:opening_entry_submit", kwargs={"pk": self.entry.pk}))
        self.assertRedirects(
            response,
            reverse("staff:opening_entry_detail", kwargs={"pk": self.entry.pk}),
        )
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, POSOpeningEntry.SUBMITTED)

    def test_cancel_post(self):
        response = self.client.post(reverse("staff:opening_entry_cancel", kwargs={"pk": self.entry.pk}))
        self.assertEqual(response.status_code, 302)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, POSOpeningEntry.CANCELLED)
        self.assertEqual(self.entry.cancelled_by, self.user)


class TestPOSClosingEntryViews(StaffViewTestBase):
    def setUp(self):
        super().setUp()
        # Open the shift so the closing entry is allowed
        self.entry.submit()

    def _seed_closing_draft(self):
        """Create a DRAFT closing via the real closing_entry_create flow; returns the new POSClosingEntry."""
        from apps.staff.models import POSClosingEntry

        response = self.client.post(reverse("staff:closing_entry_create"))
        self.assertEqual(response.status_code, 302)
        return POSClosingEntry.objects.get(opening_entry=self.entry)

    def test_create_get_auto_creates_draft_and_seeds_rows(self):
        """GET to closing_entry_create starts a DRAFT close for the open shift and seeds ClosingPayment rows."""
        from apps.staff.models import POSClosingEntry

        response = self.client.get(reverse("staff:closing_entry_create"))
        self.assertEqual(response.status_code, 302)
        closing = POSClosingEntry.objects.get(opening_entry=self.entry)
        self.assertEqual(closing.status, POSClosingEntry.DRAFT)
        self.assertEqual(closing.cashier, self.user)
        self.assertEqual(closing.closing_payments.count(), self.entry.opening_payments.count())
        for cp in closing.closing_payments.all():
            self.assertEqual(cp.closing_amount, Decimal("0"))
            self.assertEqual(cp.opening_amount, cp.expected_amount)

    def test_create_redirects_to_existing_draft(self):
        """A second 'Close Shift' click redirects to the existing draft instead of creating a duplicate."""
        from apps.staff.models import POSClosingEntry

        first = self._seed_closing_draft()
        response = self.client.get(reverse("staff:closing_entry_create"))
        self.assertRedirects(response, reverse("staff:closing_entry_detail", kwargs={"pk": first.pk}))
        self.assertEqual(POSClosingEntry.objects.filter(opening_entry=self.entry).count(), 1)

    def test_create_no_open_shift_redirects_to_dashboard(self):
        """With no open shift, the create endpoint redirects back to the dashboard with a warning."""
        from apps.staff.models import POSClosingEntry

        self.entry.cancel(by_user=self.user)
        response = self.client.get(reverse("staff:closing_entry_create"))
        self.assertRedirects(response, reverse("staff:dashboard"))
        self.assertEqual(POSClosingEntry.objects.count(), 0)

    def test_detail_get_renders_inline_form_for_draft(self):
        """For a DRAFT closing, the reconciliation table renders as an inline-editable form."""
        closing = self._seed_closing_draft()
        response = self.client.get(reverse("staff:closing_entry_detail", kwargs={"pk": closing.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reconciliation")
        self.assertContains(
            response,
            f'action="{reverse("staff:closing_entry_detail", kwargs={"pk": closing.pk})}"',
        )
        self.assertContains(response, "Submit & Close Shift")

    def test_detail_post_saves_amounts(self):
        """POST to the detail URL saves the entered closing amounts (PRG)."""
        closing = self._seed_closing_draft()
        post_data = {}
        for cp in closing.closing_payments.all():
            post_data[f"cp_{cp.pk}-closing_amount"] = "49500.00"
        response = self.client.post(
            reverse("staff:closing_entry_detail", kwargs={"pk": closing.pk}),
            data=post_data,
        )
        self.assertRedirects(response, reverse("staff:closing_entry_detail", kwargs={"pk": closing.pk}))
        for cp in closing.closing_payments.all():
            cp.refresh_from_db()
            self.assertEqual(cp.closing_amount, Decimal("49500.00"))

    def test_detail_post_blocked_when_submitted(self):
        """POST to a SUBMITTED closing's detail URL is rejected — the entry is immutable once submitted."""
        closing = self._seed_closing_draft()
        for cp in closing.closing_payments.all():
            cp.closing_amount = cp.expected_amount
            cp.save(update_fields=["closing_amount"])
        submit_closing_entry(closing)
        post_data = {f"cp_{cp.pk}-closing_amount": "99999" for cp in closing.closing_payments.all()}
        response = self.client.post(
            reverse("staff:closing_entry_detail", kwargs={"pk": closing.pk}),
            data=post_data,
        )
        self.assertEqual(response.status_code, 302)
        for cp in closing.closing_payments.all():
            cp.refresh_from_db()
            self.assertEqual(cp.closing_amount, cp.expected_amount)

    def test_detail_get_read_only_when_submitted(self):
        """For a SUBMITTED closing, the reconciliation table renders read-only."""
        closing = self._seed_closing_draft()
        submit_closing_entry(closing)
        response = self.client.get(reverse("staff:closing_entry_detail", kwargs={"pk": closing.pk}))
        self.assertEqual(response.status_code, 200)
        # The 'Difference' column only appears in the read-only view.
        self.assertContains(response, "Difference")
        self.assertNotContains(
            response,
            f'action="{reverse("staff:closing_entry_detail", kwargs={"pk": closing.pk})}"',
        )

    def test_submit_post(self):
        from apps.staff.models import POSClosingEntry

        closing = self._seed_closing_draft()
        for cp in closing.closing_payments.all():
            cp.closing_amount = cp.expected_amount
            cp.save(update_fields=["closing_amount"])
        response = self.client.post(reverse("staff:closing_entry_submit", kwargs={"pk": closing.pk}))
        self.assertRedirects(
            response,
            reverse("staff:closing_entry_detail", kwargs={"pk": closing.pk}),
        )
        closing.refresh_from_db()
        self.assertEqual(closing.status, POSClosingEntry.SUBMITTED)
        self.entry.refresh_from_db()
        self.assertTrue(self.entry.is_closed)

    def test_cancel_post(self):
        from apps.staff.models import POSClosingEntry

        closing = self._seed_closing_draft()
        for cp in closing.closing_payments.all():
            cp.closing_amount = cp.expected_amount
            cp.save(update_fields=["closing_amount"])
        submit_closing_entry(closing)
        response = self.client.post(reverse("staff:closing_entry_cancel", kwargs={"pk": closing.pk}))
        self.assertEqual(response.status_code, 302)
        closing.refresh_from_db()
        self.assertEqual(closing.status, POSClosingEntry.CANCELLED)

    def test_cancel_blocked_by_new_open_shift(self):
        from apps.staff.models import POSClosingEntry

        closing = self._seed_closing_draft()
        for cp in closing.closing_payments.all():
            cp.closing_amount = cp.expected_amount
            cp.save(update_fields=["closing_amount"])
        submit_closing_entry(closing)
        # Open a new shift — cancelling the older close must not reopen it while a newer shift is live.
        new_entry = POSOpeningEntry.objects.create(
            cashier=self.user,
            posting_date="2026-07-25",
        )
        OpeningPayment.objects.create(
            opening_entry=new_entry,
            mode_of_payment=self.cash_mode,
            opening_amount=Decimal("0"),
        )
        new_entry.submit()
        response = self.client.post(reverse("staff:closing_entry_cancel", kwargs={"pk": closing.pk}))
        self.assertEqual(response.status_code, 302)
        closing.refresh_from_db()
        self.assertEqual(closing.status, POSClosingEntry.SUBMITTED)
