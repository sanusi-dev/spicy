"""Shared setup for Daily P&L tests."""

from datetime import date
from decimal import Decimal

from apps.accounting.tests.helpers import setup_chart_of_accounts
from apps.inventory.models import UOM, Bin, Item, ItemGroup, Warehouse
from apps.menu.models import Menu, MenuItem
from apps.orders.models import Order
from apps.orders.services import settle_order
from apps.payments.models import ModeOfPayment, PaymentGLMapping
from apps.settings.models import ProductionUnit, Restaurant
from apps.staff.models import OpeningPayment, POSOpeningEntry
from apps.users.models import CustomUser

from ..models import DailyPnL, PnLConfiguration


class DailyPnLTestMixin:
    @classmethod
    def _setup_pnl_world(cls):
        cls.restaurant = Restaurant.objects.create(company="PnL Co")
        cls.accounts = setup_chart_of_accounts(cls.restaurant)
        cls.uom = UOM.objects.create(name="Nos")
        cls.group_food = ItemGroup.objects.create(name="Food")
        cls.group_drinks = ItemGroup.objects.create(name="Beverages")
        cls.kitchen_wh = Warehouse.objects.create(name="Kitchen", account=cls.accounts["cogs"])
        from apps.accounting.models import LedgerAccount

        stock_in_hand = LedgerAccount.objects.create(
            name=f"Stock in Hand — Bar {cls.restaurant.pk}",
            parent=cls.accounts["assets"],
            account_type=LedgerAccount.ASSET,
            report_type=LedgerAccount.BALANCE_SHEET,
        )
        cls.bar_wh = Warehouse.objects.create(name="Bar", account=stock_in_hand)
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
        PaymentGLMapping.objects.get_or_create(
            mode_of_payment=cls.cash, defaults={"default_account": cls.accounts["cash"]}
        )
        cls.restaurant.active_menu = cls.menu
        cls.restaurant.default_warehouse = cls.bar_wh
        cls.restaurant.save()
        cls.user = CustomUser.objects.create_user(username="cashier", password="testpass123")
        cls.manager = CustomUser.objects.create_user(
            username="manager", password="testpass123", is_staff=True, is_superuser=True
        )
        cls.opening = POSOpeningEntry.objects.create(cashier=cls.user)
        OpeningPayment.objects.create(
            opening_entry=cls.opening, mode_of_payment=cls.cash, opening_amount=Decimal("50000")
        )
        cls.opening.submit()
        ProductionUnit.objects.create(
            name="Kitchen",
            warehouse=cls.kitchen_wh,
            department="FOOD",
            income_account=cls.accounts["food_sales"],
            sales_returns_account=cls.accounts["food_sales_returns"],
        )
        ProductionUnit.objects.create(
            name="Bar",
            warehouse=cls.bar_wh,
            department="DRINKS",
            income_account=cls.accounts["drinks_sales"],
            sales_returns_account=cls.accounts["drinks_sales_returns"],
        )
        cls.config = PnLConfiguration.load()

    def _create_order(self, **kwargs):
        defaults = {"opening_entry": self.opening}
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    def _settle(self, order, amount=None):
        order.recalculate_totals()
        amount = amount or order.rounded_total
        settle_order(order, [{"mode_of_payment": self.cash.pk, "amount": amount}], cashier=self.user)
        order.refresh_from_db()
        return order

    def _draft(self, business_date=None, **kwargs):
        return DailyPnL.objects.create(business_date=business_date or date.today(), **kwargs)
