from decimal import Decimal

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounting.models import GLEntry
from apps.accounting.tests.helpers import setup_chart_of_accounts
from apps.orders.models import Order
from apps.orders.services import add_order_line, settle_order
from apps.payments.models import ModeOfPayment, PaymentGLMapping
from apps.settings.models import Restaurant
from apps.staff.models import ClosingPayment, OpeningPayment, POSClosingEntry, POSOpeningEntry, ShiftCashOut
from apps.staff.services import cancel_cash_out, expected_closing_amounts, record_cash_out, submit_closing_entry
from apps.users.models import CustomUser


class ShiftCashOutBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = CustomUser.objects.create_user(username="manager", password="testpass123")
        mgr, _ = Group.objects.get_or_create(name="Spicy Manager")
        cls.manager.groups.add(mgr)
        cls.cashier = CustomUser.objects.create_user(username="cashier", password="testpass123")
        cashier_group, _ = Group.objects.get_or_create(name="Spicy Cashier")
        cls.cashier.groups.add(cashier_group)
        cls.restaurant = Restaurant.objects.create(company="Cash-Out Co")
        cls.accounts = setup_chart_of_accounts(cls.restaurant)
        cls.cash, _ = ModeOfPayment.objects.get_or_create(name="Cash", defaults={"type": "CASH"})
        cls.bank, _ = ModeOfPayment.objects.get_or_create(name="Bank", defaults={"type": "BANK"})
        PaymentGLMapping.objects.get_or_create(
            mode_of_payment=cls.cash, defaults={"default_account": cls.accounts["cash"]}
        )
        PaymentGLMapping.objects.get_or_create(
            mode_of_payment=cls.bank, defaults={"default_account": cls.accounts["bank"]}
        )
        cls.opening = POSOpeningEntry.objects.create(cashier=cls.cashier)
        OpeningPayment.objects.create(
            opening_entry=cls.opening, mode_of_payment=cls.cash, opening_amount=Decimal("50000")
        )
        OpeningPayment.objects.create(
            opening_entry=cls.opening, mode_of_payment=cls.bank, opening_amount=Decimal("10000")
        )
        cls.opening.submit()

    def _expected(self):
        return {
            row["mode"].pk: row["expected_amount"]
            for row in expected_closing_amounts(self.opening, self.opening.period_start_date, timezone.now())
        }

    def _record(self, amount="2000", **kwargs):
        params = {
            "mode": self.cash,
            "amount": Decimal(amount),
            "reason": ShiftCashOut.TRANSPORT,
            "note": "",
            "actor": self.cashier,
        }
        params.update(kwargs)
        return record_cash_out(self.opening, **params)


class ShiftCashOutRecordTest(ShiftCashOutBase):
    def test_record_reduces_that_modes_expected_only(self):
        self._record("2000")
        rows = self._expected()
        self.assertEqual(rows[self.cash.pk], Decimal("48000"))
        self.assertEqual(rows[self.bank.pk], Decimal("10000"))

    def test_submit_stores_reduced_expected(self):
        self._record("2000")
        closing = POSClosingEntry.objects.create(opening_entry=self.opening, cashier=self.cashier)
        ClosingPayment.objects.create(closing_entry=closing, mode_of_payment=self.cash, closing_amount=Decimal("48000"))
        ClosingPayment.objects.create(closing_entry=closing, mode_of_payment=self.bank, closing_amount=Decimal("10000"))
        submit_closing_entry(closing, actor=self.manager)
        self.assertEqual(closing.closing_payments.get(mode_of_payment=self.cash).expected_amount, Decimal("48000"))

    def test_gl_legs_dr_expense_cr_cash(self):
        row = self._record("2000")
        entries = list(GLEntry.objects.filter(voucher_type="Shift Cash-Out", voucher_no=str(row.pk)))
        self.assertEqual(len(entries), 2)
        debit = next(entry for entry in entries if entry.debit)
        credit = next(entry for entry in entries if entry.credit)
        self.assertEqual(debit.account_id, self.restaurant.default_expense_account_id)
        self.assertEqual(debit.debit, Decimal("2000"))
        self.assertEqual(credit.account_id, self.accounts["cash"].id)
        self.assertEqual(credit.credit, Decimal("2000"))

    def test_record_is_idempotent_per_row(self):
        from apps.accounting.services import post_shift_cash_out_gl

        row = self._record("2000")
        post_shift_cash_out_gl(row)
        self.assertEqual(GLEntry.objects.filter(voucher_type="Shift Cash-Out", voucher_no=str(row.pk)).count(), 2)

    def test_missing_expense_chain_fails_closed(self):
        self.restaurant.petty_cash_expense_account = None
        self.restaurant.default_expense_account = None
        self.restaurant.save()
        with self.assertRaisesMessage(ValidationError, "petty cash expense account"):
            self._record("2000")
        self.assertEqual(ShiftCashOut.objects.count(), 0)

    def test_petty_cash_account_preferred_over_default(self):
        self.restaurant.petty_cash_expense_account = self.accounts["round_off"]
        self.restaurant.save()
        row = self._record("2000")
        debit = GLEntry.objects.get(voucher_type="Shift Cash-Out", voucher_no=str(row.pk), debit__gt=0)
        self.assertEqual(debit.account_id, self.accounts["round_off"].id)

    def test_non_cash_mode_rejected(self):
        with self.assertRaises(ValidationError):
            self._record("2000", mode=self.bank)

    def test_mode_outside_opening_set_rejected(self):
        other = ModeOfPayment.objects.create(name="Spare Cash", type="CASH")
        PaymentGLMapping.objects.create(mode_of_payment=other, default_account=self.accounts["cash"])
        with self.assertRaises(ValidationError):
            self._record("2000", mode=other)

    def test_zero_and_negative_amount_rejected(self):
        with self.assertRaises(ValidationError):
            self._record("0")
        with self.assertRaises(ValidationError):
            self._record("-500")

    def test_other_without_note_rejected(self):
        with self.assertRaises(ValidationError):
            self._record("2000", reason=ShiftCashOut.OTHER, note="")
        row = self._record("2000", reason=ShiftCashOut.OTHER, note="Ice blocks")
        self.assertEqual(row.note, "Ice blocks")

    def test_record_on_closed_shift_refused(self):
        closing = POSClosingEntry.objects.create(opening_entry=self.opening, cashier=self.cashier)
        for op in self.opening.opening_payments.all():
            ClosingPayment.objects.create(closing_entry=closing, mode_of_payment=op.mode_of_payment)
        submit_closing_entry(closing, actor=self.manager)
        with self.assertRaises(ValidationError):
            self._record("2000")


class ShiftCashOutCancelTest(ShiftCashOutBase):
    def test_cancel_posts_reversal_and_restores_expected(self):
        row = self._record("2000")
        cancel_cash_out(row, actor=self.manager)
        row.refresh_from_db()
        self.assertEqual(row.status, ShiftCashOut.CANCELLED)
        self.assertEqual(row.cancelled_by, self.manager)
        self.assertIsNotNone(row.cancelled_at)
        rows = self._expected()
        self.assertEqual(rows[self.cash.pk], Decimal("50000"))
        originals = GLEntry.objects.filter(voucher_type="Shift Cash-Out", voucher_no=str(row.pk))
        self.assertEqual(originals.count(), 4)
        self.assertEqual(originals.filter(is_cancelled=True).count(), 2)
        live = originals.filter(is_cancelled=False)
        self.assertEqual(sum((entry.debit for entry in live), Decimal("0")), Decimal("2000"))
        self.assertEqual(sum((entry.credit for entry in live), Decimal("0")), Decimal("2000"))

    def test_cancel_by_cashier_refused(self):
        row = self._record("2000")
        with self.assertRaises(ValidationError):
            cancel_cash_out(row, actor=self.cashier)
        row.refresh_from_db()
        self.assertEqual(row.status, ShiftCashOut.SUBMITTED)

    def test_double_cancel_refused(self):
        row = self._record("2000")
        cancel_cash_out(row, actor=self.manager)
        with self.assertRaises(ValidationError):
            cancel_cash_out(row, actor=self.manager)

    def test_cancel_on_closed_shift_refused(self):
        row = self._record("2000")
        closing = POSClosingEntry.objects.create(opening_entry=self.opening, cashier=self.cashier)
        for op in self.opening.opening_payments.all():
            ClosingPayment.objects.create(closing_entry=closing, mode_of_payment=op.mode_of_payment)
        submit_closing_entry(closing, actor=self.manager)
        with self.assertRaises(ValidationError):
            cancel_cash_out(row, actor=self.manager)


class ShiftCashOutViewTest(ShiftCashOutBase):
    def test_cashier_can_record_but_cannot_cancel(self):
        self.client.login(username="cashier", password="testpass123")
        response = self.client.post(
            reverse("pos:pos_cash_out_record"),
            {"mode_of_payment": str(self.cash.pk), "amount": "2000", "reason": "TRANSPORT", "note": ""},
        )
        self.assertIn(response.status_code, (200, 302))
        row = ShiftCashOut.objects.get()
        self.assertEqual(row.recorded_by, self.cashier)
        response = self.client.post(reverse("pos:pos_cash_out_cancel", args=[row.pk]))
        self.assertEqual(response.status_code, 403)

    def test_manager_can_record_and_cancel(self):
        self.client.login(username="manager", password="testpass123")
        response = self.client.get(reverse("pos:pos_cash_out_dialog"))
        self.assertEqual(response.status_code, 200)
        self.client.post(
            reverse("pos:pos_cash_out_record"),
            {"mode_of_payment": str(self.cash.pk), "amount": "2000", "reason": "ICE", "note": ""},
        )
        row = ShiftCashOut.objects.get()
        response = self.client.post(reverse("pos:pos_cash_out_cancel", args=[row.pk]))
        self.assertIn(response.status_code, (200, 302))
        row.refresh_from_db()
        self.assertEqual(row.status, ShiftCashOut.CANCELLED)

    def test_record_with_sales_still_nets_expected(self):
        from apps.inventory.models import UOM, Item, ItemGroup, Warehouse
        from apps.menu.models import Menu, MenuItem
        from apps.settings.models import ProductionUnit

        uom = UOM.objects.create(name="Nos")
        group = ItemGroup.objects.create(name="Food")
        warehouse = Warehouse.objects.create(name="Kitchen")
        item = Item.objects.create(
            item_name="Jollof",
            item_group=group,
            stock_uom=uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        menu = Menu.objects.create(name="Menu")
        MenuItem.objects.create(menu=menu, item=item, rate=Decimal("1500"))
        self.restaurant.active_menu = menu
        self.restaurant.default_warehouse = warehouse
        self.restaurant.save()
        ProductionUnit.objects.create(name="Kitchen", warehouse=warehouse, department="FOOD")
        order = Order.objects.create(opening_entry=self.opening)
        add_order_line(order, item, qty=1, rate=Decimal("1500"))
        settle_order(order, [{"mode_of_payment": self.cash.pk, "amount": "1500"}], cashier=self.cashier)
        self._record("2000")
        rows = self._expected()
        self.assertEqual(rows[self.cash.pk], Decimal("49500"))

    def test_backoffice_detail_shows_cash_outs(self):
        self._record("2000")
        closing = POSClosingEntry.objects.create(opening_entry=self.opening, cashier=self.cashier)
        for op in self.opening.opening_payments.all():
            ClosingPayment.objects.create(closing_entry=closing, mode_of_payment=op.mode_of_payment)
        self.client.login(username="manager", password="testpass123")
        response = self.client.get(reverse("staff:closing_entry_detail", args=[closing.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Shift cash-outs")
