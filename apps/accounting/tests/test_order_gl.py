"""Order GL tests — settle legs, departmental income split, change, rounding, COGS,
cancel reversal, missing-account failures, fiscal year guard."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import GLEntry, LedgerAccount
from apps.inventory.models import UOM, Bin, Item, ItemGroup, StockLedgerEntry, Warehouse
from apps.menu.models import Menu, MenuItem
from apps.orders.models import Order, OrderItem
from apps.orders.services import (
    add_order_line,
    make_return,
    settle_order,
    submit_return,
    update_return_line,
)
from apps.payments.models import ModeOfPayment, PaymentGLMapping
from apps.settings.models import ProductionUnit, Restaurant
from apps.staff.models import OpeningPayment, POSOpeningEntry
from apps.users.models import CustomUser

from .helpers import setup_chart_of_accounts


class OrderGLTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.restaurant = Restaurant.objects.create(company="Test Co")
        cls.accounts = setup_chart_of_accounts(cls.restaurant)
        cls.uom = UOM.objects.create(name="Nos")
        cls.group_food = ItemGroup.objects.create(name="Food")
        cls.group_drinks = ItemGroup.objects.create(name="Beverages")
        cls.kitchen_wh = Warehouse.objects.create(name="Kitchen", account=cls.accounts["cogs"])
        cls.bar_wh = Warehouse.objects.create(name="Bar", account=cls.accounts["cash"])  # placeholder, fixed below
        cls.bar_wh.account = LedgerAccount.objects.create(
            name="Stock in Hand — Bar",
            parent=cls.accounts["assets"],
            account_type=LedgerAccount.ASSET,
            report_type=LedgerAccount.BALANCE_SHEET,
        )
        cls.bar_wh.save()
        cls.food = Item.objects.create(
            item_name="Jollof Rice",
            item_group=cls.group_food,
            stock_uom=cls.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        cls.drink = Item.objects.create(
            item_name="Coke",
            item_group=cls.group_drinks,
            stock_uom=cls.uom,
            department="DRINKS",
            is_sales_item=True,
            is_stock_item=True,
            is_purchase_item=True,
        )
        cls.menu = Menu.objects.create(name="Main")
        cls.food_mi = MenuItem.objects.create(menu=cls.menu, item=cls.food, rate=Decimal("1500"))
        cls.drink_mi = MenuItem.objects.create(menu=cls.menu, item=cls.drink, rate=Decimal("500"))
        Bin.objects.create(item=cls.food, warehouse=cls.kitchen_wh, actual_qty=Decimal("100"))
        Bin.objects.create(item=cls.drink, warehouse=cls.bar_wh, actual_qty=Decimal("100"))
        cls.cash, _ = ModeOfPayment.objects.get_or_create(name="Cash", defaults={"type": "CASH"})
        cls.bank, _ = ModeOfPayment.objects.get_or_create(name="Bank", defaults={"type": "BANK"})
        PaymentGLMapping.objects.get_or_create(
            mode_of_payment=cls.cash, defaults={"default_account": cls.accounts["cash"]}
        )
        PaymentGLMapping.objects.get_or_create(
            mode_of_payment=cls.bank, defaults={"default_account": cls.accounts["bank"]}
        )
        cls.restaurant.active_menu = cls.menu
        cls.restaurant.default_warehouse = cls.bar_wh
        cls.restaurant.save()
        cls.user = CustomUser.objects.create_user(username="cashier", password="testpass123")
        cls.opening = POSOpeningEntry.objects.create(cashier=cls.user)
        OpeningPayment.objects.create(
            opening_entry=cls.opening, mode_of_payment=cls.cash, opening_amount=Decimal("50000")
        )
        cls.opening.submit()
        cls.kitchen = ProductionUnit.objects.create(
            name="Kitchen",
            warehouse=cls.kitchen_wh,
            department="FOOD",
            income_account=cls.accounts["food_sales"],
            sales_returns_account=cls.accounts["food_sales_returns"],
        )
        cls.bar = ProductionUnit.objects.create(
            name="Bar",
            warehouse=cls.bar_wh,
            department="DRINKS",
            income_account=cls.accounts["drinks_sales"],
            sales_returns_account=cls.accounts["drinks_sales_returns"],
        )

    def _create_order(self, **kwargs):
        defaults = {"opening_entry": self.opening}
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    def _settle(self, order, amount=None):
        order.recalculate_totals()
        amount = amount or order.rounded_total
        settle_order(
            order,
            [{"mode_of_payment": self.cash.pk, "amount": amount}],
            cashier=self.user,
        )
        order.refresh_from_db()
        return order

    def _order_gl(self, order):
        return GLEntry.objects.filter(voucher_type="Order", voucher_no=order.invoice_number)


class OrderSettleGLTest(OrderGLTestBase):
    def test_settle_posts_income_and_payment_legs(self):
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        self._settle(order)
        entries = self._order_gl(order)
        self.assertEqual(entries.count(), 2)
        cash_entry = entries.get(account=self.accounts["cash"])
        self.assertEqual(cash_entry.debit, Decimal("1500"))
        income_entry = entries.get(account=self.accounts["food_sales"])
        self.assertEqual(income_entry.credit, Decimal("1500"))

    def test_departmental_income_split(self):
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        add_order_line(order, self.drink, qty=1, rate=Decimal("500"), menu_item=self.drink_mi)
        self._settle(order)
        entries = self._order_gl(order)
        self.assertEqual(entries.get(account=self.accounts["food_sales"]).credit, Decimal("1500"))
        self.assertEqual(entries.get(account=self.accounts["drinks_sales"]).credit, Decimal("500"))

    def test_change_reduces_cash_leg(self):
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        self._settle(order, amount=Decimal("2000"))
        cash_entry = self._order_gl(order).get(account=self.accounts["cash"])
        self.assertEqual(cash_entry.debit, Decimal("1500"))

    def test_rounding_posts_round_off(self):
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1499.50"), menu_item=self.food_mi)
        order.recalculate_totals()
        self._settle(order)
        entries = self._order_gl(order)
        round_entry = entries.get(account=self.accounts["round_off"])
        self.assertEqual(round_entry.credit, Decimal("0.50"))

    def test_cogs_posts_for_drink_order(self):
        # Bin starts with 100 @ 0 (from setUp); adding 100 @ 300 blends to WAC 150.
        StockLedgerEntry.create_entry(
            item=self.drink,
            warehouse=self.bar_wh,
            quantity=Decimal("100"),
            voucher_type="Purchase Receipt",
            voucher_no="PR-1",
            unit_rate=Decimal("300"),
        )
        order = self._create_order()
        add_order_line(order, self.drink, qty=2, rate=Decimal("500"), menu_item=self.drink_mi)
        self._settle(order)
        entries = self._order_gl(order)
        cogs_entry = entries.get(account=self.accounts["cogs"])
        self.assertEqual(cogs_entry.debit, Decimal("300"))
        stock_entry = entries.get(account=self.bar_wh.account)
        self.assertEqual(stock_entry.credit, Decimal("300"))

    def test_cogs_uses_bar_unit_expense_account(self):
        from apps.accounting.models import LedgerAccount

        drinks_cogs = LedgerAccount.objects.create(
            name="Drinks COGS",
            parent=self.accounts["expenses"],
            account_type=LedgerAccount.EXPENSE,
            report_type=LedgerAccount.PROFIT_AND_LOSS,
        )
        self.bar.expense_account = drinks_cogs
        self.bar.save(update_fields=["expense_account", "updated_at"])
        StockLedgerEntry.create_entry(
            item=self.drink,
            warehouse=self.bar_wh,
            quantity=Decimal("100"),
            voucher_type="Purchase Receipt",
            voucher_no="PR-2",
            unit_rate=Decimal("300"),
        )
        order = self._create_order()
        add_order_line(order, self.drink, qty=2, rate=Decimal("500"), menu_item=self.drink_mi)
        self._settle(order)
        entries = self._order_gl(order)
        self.assertEqual(entries.get(account=drinks_cogs).debit, Decimal("300"))
        self.assertFalse(entries.filter(account=self.accounts["cogs"]).exists())

    def test_missing_income_account_raises(self):
        self.kitchen.income_account = None
        self.kitchen.save()
        self.restaurant.default_income_account = None
        self.restaurant.save()
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        with self.assertRaisesMessage(ValidationError, "default income account"):
            self._settle(order)


class OrderCancelGLTest(OrderGLTestBase):
    def test_reverse_order_gl_posts_mirrored_entries(self):
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        self._settle(order)
        # Paid orders cannot be cancelled via cancel_order; the reversal is
        # invoked directly (as the refund flow does).
        from apps.accounting.services import reverse_order_gl

        reverse_order_gl(order)
        entries = self._order_gl(order)
        self.assertEqual(entries.filter(is_cancelled=True).count(), 2)
        self.assertEqual(entries.filter(is_cancelled=False).count(), 2)
        reversal = entries.filter(is_cancelled=False, account=self.accounts["cash"]).first()
        self.assertEqual(reversal.credit, Decimal("1500"))


class RefundGLTest(OrderGLTestBase):
    def _clean_drink(self, name):
        """A drink item with an empty bin, so WAC math in variance tests stays exact."""
        item = Item.objects.create(
            item_name=name,
            item_group=self.group_drinks,
            stock_uom=self.uom,
            department="DRINKS",
            is_sales_item=True,
            is_stock_item=True,
            is_purchase_item=True,
        )
        menu_item = MenuItem.objects.create(menu=self.menu, item=item, rate=Decimal("500"))
        Bin.objects.create(item=item, warehouse=self.bar_wh, actual_qty=Decimal("0"))
        return item, menu_item

    def _drink_return_draft(self, drink, menu_item, second_rate):
        """Sell 1, shift WAC with a second receipt, open a full return draft."""
        StockLedgerEntry.create_entry(
            item=drink,
            warehouse=self.bar_wh,
            quantity=Decimal("2"),
            voucher_type="Purchase Receipt",
            voucher_no="PR-V1",
            unit_rate=Decimal("300"),
        )
        order = self._create_order()
        add_order_line(order, drink, qty=1, rate=Decimal("500"), menu_item=menu_item)
        self._settle(order)
        StockLedgerEntry.create_entry(
            item=drink,
            warehouse=self.bar_wh,
            quantity=Decimal("1"),
            voucher_type="Purchase Receipt",
            voucher_no="PR-V2",
            unit_rate=second_rate,
        )
        ret = make_return(order)
        ret.recalculate_totals()
        return ret

    def test_drink_refund_restores_at_settle_time_cost_when_wac_rises(self):
        drink, menu_item = self._clean_drink("Rising WAC Drink")
        ret = self._drink_return_draft(drink, menu_item, Decimal("700"))
        submit_return(ret, actor=self.user)
        ret.refresh_from_db()
        entries = self._order_gl(ret)
        # The return reverses the sale exactly: stock and COGS both move at the
        # settle-time 300 even though the bin re-blended to 500.
        self.assertEqual(entries.get(account=self.accounts["cogs"]).credit, Decimal("300"))
        self.assertEqual(entries.get(account=self.bar_wh.account).debit, Decimal("300"))
        self.assertEqual(entries.get(account=self.accounts["drinks_sales_returns"]).debit, Decimal("500"))
        self.assertFalse(entries.filter(account=self.accounts["variance"]).exists())
        sle = StockLedgerEntry.objects.get(voucher_type="POS Return", item=drink, quantity__gt=0)
        self.assertEqual(sle.unit_rate, Decimal("300"))

    def test_drink_refund_restores_at_settle_time_cost_when_wac_falls(self):
        drink, menu_item = self._clean_drink("Falling WAC Drink")
        ret = self._drink_return_draft(drink, menu_item, Decimal("100"))
        submit_return(ret, actor=self.user)
        ret.refresh_from_db()
        entries = self._order_gl(ret)
        self.assertEqual(entries.get(account=self.accounts["cogs"]).credit, Decimal("300"))
        self.assertEqual(entries.get(account=self.bar_wh.account).debit, Decimal("300"))
        self.assertFalse(entries.filter(account=self.accounts["variance"]).exists())

    def test_wastage_refund_posts_at_settle_time_rate(self):
        wastage = LedgerAccount.objects.create(
            name="Wasted Returns",
            parent=self.accounts["expenses"],
            account_type=LedgerAccount.EXPENSE,
            report_type=LedgerAccount.PROFIT_AND_LOSS,
        )
        self.restaurant.wastage_account = wastage
        self.restaurant.save(update_fields=["wastage_account", "updated_at"])
        drink, menu_item = self._clean_drink("Wasted Drink")
        ret = self._drink_return_draft(drink, menu_item, Decimal("700"))
        update_return_line(ret, ret.items.first().pk, not_restockable=True)
        submit_return(ret, actor=self.user)
        ret.refresh_from_db()
        entries = self._order_gl(ret)
        self.assertEqual(entries.get(account=self.accounts["cogs"]).credit, Decimal("300"))
        self.assertEqual(entries.get(account=wastage).debit, Decimal("300"))
        self.assertEqual(entries.get(account=self.accounts["drinks_sales_returns"]).debit, Decimal("500"))
        # Destroyed stock never re-enters: the return does not touch the warehouse account.
        self.assertFalse(entries.filter(account=self.bar_wh.account).exists())

    def test_wastage_account_must_differ_from_expense_account(self):
        self.restaurant.wastage_account = self.accounts["cogs"]
        self.restaurant.save(update_fields=["wastage_account", "updated_at"])
        drink, menu_item = self._clean_drink("Misconfigured Wastage Drink")
        ret = self._drink_return_draft(drink, menu_item, Decimal("700"))
        update_return_line(ret, ret.items.first().pk, not_restockable=True)
        with self.assertRaisesMessage(ValidationError, "separate from the drink expense account"):
            submit_return(ret, actor=self.user)

    def test_return_posts_mirrored_refund(self):
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        self._settle(order)
        ret = make_return(order)
        ret.recalculate_totals()
        submit_return(ret, actor=self.user)
        ret.refresh_from_db()
        entries = self._order_gl(ret)
        self.assertEqual(entries.count(), 2)
        self.assertEqual(entries.get(account=self.accounts["food_sales_returns"]).debit, Decimal("1500"))
        self.assertEqual(entries.get(account=self.accounts["cash"]).credit, Decimal("1500"))

    def test_return_fails_closed_without_returns_account(self):
        self.restaurant.default_sales_returns_account = None
        self.restaurant.save(update_fields=["default_sales_returns_account", "updated_at"])
        self.kitchen.sales_returns_account = None
        self.kitchen.save(update_fields=["sales_returns_account", "updated_at"])
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        self._settle(order)
        ret = make_return(order)
        ret.recalculate_totals()
        with self.assertRaisesMessage(ValidationError, "sales returns account"):
            submit_return(ret, actor=self.user)
        self.assertFalse(self._order_gl(ret).exists())

    def test_drink_return_reverses_bar_unit_expense(self):
        from apps.accounting.models import LedgerAccount

        drinks_cogs = LedgerAccount.objects.create(
            name="Drinks COGS returns",
            parent=self.accounts["expenses"],
            account_type=LedgerAccount.EXPENSE,
            report_type=LedgerAccount.PROFIT_AND_LOSS,
        )
        self.bar.expense_account = drinks_cogs
        self.bar.save(update_fields=["expense_account", "updated_at"])
        StockLedgerEntry.create_entry(
            item=self.drink,
            warehouse=self.bar_wh,
            quantity=Decimal("100"),
            voucher_type="Purchase Receipt",
            voucher_no="PR-3",
            unit_rate=Decimal("300"),
        )
        order = self._create_order()
        add_order_line(order, self.drink, qty=2, rate=Decimal("500"), menu_item=self.drink_mi)
        self._settle(order)
        ret = make_return(order)
        ret.recalculate_totals()
        submit_return(ret, actor=self.user)
        ret.refresh_from_db()
        entries = self._order_gl(ret)
        self.assertEqual(entries.get(account=drinks_cogs).credit, Decimal("300"))

    def test_partial_return_posts_proportion(self):
        order = self._create_order()
        add_order_line(order, self.food, qty=2, rate=Decimal("1500"), menu_item=self.food_mi)
        self._settle(order)
        ret = make_return(order)
        # Reduce one line to half (qty -1 of -2)
        line = ret.items.first()
        line.qty = Decimal("-1")
        line.save()
        ret.recalculate_totals()
        submit_return(ret, actor=self.user)
        ret.refresh_from_db()
        entries = self._order_gl(ret)
        self.assertEqual(entries.get(account=self.accounts["food_sales_returns"]).debit, Decimal("1500"))

    def test_second_return_after_first_submitted(self):
        order = self._create_order()
        add_order_line(order, self.food, qty=4, rate=Decimal("1500"), menu_item=self.food_mi)
        self._settle(order)
        ret1 = make_return(order)
        line1 = ret1.items.first()
        line1.qty = Decimal("-2")
        line1.save()
        ret1.recalculate_totals()
        submit_return(ret1, actor=self.user)
        ret2 = make_return(order)
        self.assertEqual(abs(ret2.items.first().qty), Decimal("2"))
        ret2.recalculate_totals()
        submit_return(ret2, actor=self.user)
        self.assertEqual(GLEntry.objects.filter(voucher_no=ret2.invoice_number).count(), 2)

    def test_cumulative_qty_cap_enforced(self):
        order = self._create_order()
        add_order_line(order, self.food, qty=2, rate=Decimal("1500"), menu_item=self.food_mi)
        self._settle(order)
        ret1 = make_return(order)
        ret1.recalculate_totals()
        submit_return(ret1, actor=self.user)
        ret2 = make_return(order)
        self.assertEqual(ret2.items.count(), 0)
        with self.assertRaisesMessage(ValidationError, "no refundable value"):
            submit_return(ret2, actor=self.user)


class SameAccountCollisionTest(OrderGLTestBase):
    def test_settle_raises_when_payment_maps_to_income_account(self):
        PaymentGLMapping.objects.filter(mode_of_payment=self.cash).update(default_account=self.accounts["food_sales"])
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        with self.assertRaisesMessage(ValidationError, "sales account"):
            self._settle(order)
        self.assertFalse(self._order_gl(order).exists())

    def test_refund_raises_when_payment_maps_to_income_account(self):
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        self._settle(order)
        ret = make_return(order)
        ret.recalculate_totals()
        PaymentGLMapping.objects.filter(mode_of_payment=self.cash).update(default_account=self.accounts["food_sales"])
        with self.assertRaisesMessage(ValidationError, "sales account"):
            submit_return(ret, actor=self.user)
        self.assertFalse(self._order_gl(ret).exists())


class NotRestockableConstraintTest(OrderGLTestBase):
    def test_non_return_line_cannot_be_marked_not_restockable(self):
        from django.db import IntegrityError, transaction

        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        line = order.items.first()
        line.not_restockable = True
        with self.assertRaises(ValidationError):
            line.full_clean()
        # The DB constraint is the last line of defence (bypasses save()).
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrderItem.objects.filter(pk=line.pk).update(not_restockable=True)
