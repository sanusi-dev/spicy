from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.accounting.tests.helpers import setup_chart_of_accounts
from apps.inventory.models import UOM, Item, ItemGroup, Warehouse
from apps.menu.models import Menu, MenuItem
from apps.orders.models import Order
from apps.orders.services import add_order_line, make_return, settle_order, submit_return
from apps.payments.models import ModeOfPayment, PaymentGLMapping
from apps.settings.models import ProductionUnit, Restaurant
from apps.staff.models import ClosingPayment, OpeningPayment, POSClosingEntry, POSOpeningEntry
from apps.staff.services import submit_closing_entry
from apps.users.models import CustomUser


class ClosingZReportBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = CustomUser.objects.create_user(username="manager", password="testpass123")
        mgr, _ = Group.objects.get_or_create(name="Spicy Manager")
        cls.user.groups.add(mgr)
        cls.restaurant = Restaurant.objects.create(company="Z Co")
        cls.accounts = setup_chart_of_accounts(cls.restaurant)
        cls.uom = UOM.objects.create(name="Nos")
        cls.group = ItemGroup.objects.create(name="Food")
        cls.warehouse = Warehouse.objects.create(name="Kitchen", account=cls.accounts["cogs"])
        cls.item = Item.objects.create(
            item_name="Jollof Rice",
            item_group=cls.group,
            stock_uom=cls.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        cls.menu = Menu.objects.create(name="Main Menu")
        cls.menu_item = MenuItem.objects.create(menu=cls.menu, item=cls.item, rate=Decimal("1500"))
        cls.restaurant.active_menu = cls.menu
        cls.restaurant.default_warehouse = cls.warehouse
        cls.restaurant.save()
        ProductionUnit.objects.create(name="Kitchen", warehouse=cls.warehouse, department="FOOD")
        cls.cash, _ = ModeOfPayment.objects.get_or_create(name="Cash", defaults={"type": "CASH"})
        PaymentGLMapping.objects.get_or_create(
            mode_of_payment=cls.cash, defaults={"default_account": cls.accounts["cash"]}
        )
        cls.opening = POSOpeningEntry.objects.create(cashier=cls.user)
        OpeningPayment.objects.create(
            opening_entry=cls.opening, mode_of_payment=cls.cash, opening_amount=Decimal("50000")
        )
        cls.opening.submit()

    def _settle(self, qty, rate):
        order = Order.objects.create(opening_entry=self.opening)
        add_order_line(order, self.item, qty=qty, rate=rate)
        settle_order(order, [{"mode_of_payment": self.cash.pk, "amount": str(qty * rate)}], cashier=self.user)
        return order

    def _draft_closing(self):
        closing = POSClosingEntry.objects.create(opening_entry=self.opening, cashier=self.user)
        ClosingPayment.objects.create(closing_entry=closing, mode_of_payment=self.cash, closing_amount=Decimal("0"))
        return closing


class ClosingZReportSubmitTest(ClosingZReportBase):
    def test_submit_stores_bills_qty_net_grand_refunded(self):
        order1 = self._settle(Decimal("2"), Decimal("1500"))
        self._settle(Decimal("1"), Decimal("1500"))
        draft_return = make_return(order1)
        line = draft_return.items.get()
        line.qty = Decimal("-1")
        line.save()
        draft_return.recalculate_totals()
        submit_return(draft_return, actor=self.user)

        closing = self._draft_closing()
        submit_closing_entry(closing)
        closing.refresh_from_db()

        self.assertEqual(closing.bill_count, 2)
        self.assertEqual(closing.total_quantity, Decimal("3"))
        self.assertEqual(closing.net_total, Decimal("4500"))
        self.assertEqual(closing.grand_total, Decimal("4500"))
        self.assertEqual(closing.refunded_total, Decimal("1500"))

    def test_zero_orders_stores_zeros_and_closes(self):
        closing = self._draft_closing()
        submit_closing_entry(closing)
        closing.refresh_from_db()
        self.assertEqual(closing.status, POSClosingEntry.SUBMITTED)
        self.assertEqual(closing.bill_count, 0)
        self.assertEqual(closing.total_quantity, Decimal("0"))
        self.assertEqual(closing.refunded_total, Decimal("0"))


class ClosingZReportBackfillTest(ClosingZReportBase):
    def test_backfill_restores_bill_count_and_refunded(self):
        import importlib

        from django.apps import apps as django_apps

        migration = importlib.import_module("apps.staff.migrations.0009_backfill_closing_z_report")

        order1 = self._settle(Decimal("2"), Decimal("1500"))
        draft_return = make_return(order1)
        line = draft_return.items.get()
        line.qty = Decimal("-1")
        line.save()
        draft_return.recalculate_totals()
        submit_return(draft_return, actor=self.user)
        closing = self._draft_closing()
        submit_closing_entry(closing)
        POSClosingEntry.objects.filter(pk=closing.pk).update(bill_count=0, refunded_total=Decimal("0"))
        migration.backfill_closing_sales(django_apps, None)
        closing.refresh_from_db()
        self.assertEqual(closing.bill_count, 1)
        self.assertEqual(closing.refunded_total, Decimal("1500"))


class ClosingZReportPagesTest(ClosingZReportBase):
    def setUp(self):
        self.client.login(username="manager", password="testpass123")

    def test_detail_renders_stored_sales_values(self):
        self._settle(Decimal("2"), Decimal("1500"))
        closing = self._draft_closing()
        submit_closing_entry(closing)
        closing.refresh_from_db()
        response = self.client.get(reverse("staff:closing_entry_detail", args=[closing.pk]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        for value in ("Bills", "Item Qty", "Net Total", "Grand Total", "Refunded Total"):
            self.assertIn(value, content)
        self.assertIn(str(closing.bill_count), content)
        self.assertIn(str(closing.grand_total), content)
