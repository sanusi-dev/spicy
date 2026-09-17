from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.inventory.models import UOM, Bin, Item, ItemGroup, Warehouse
from apps.menu.models import Menu, MenuItem
from apps.orders.models import Order
from apps.orders.services import add_order_line, create_tickets, settle_order
from apps.payments.models import ModeOfPayment
from apps.settings.models import ProductionUnit, Restaurant
from apps.staff.models import OpeningPayment, POSOpeningEntry

from .accounting_setup import OrderAccountingMixin

CustomUser = get_user_model()


class BackofficeViewTestBase(OrderAccountingMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.restaurant = Restaurant.objects.create(company="Test Co")
        cls.uom = UOM.objects.create(name="Nos")
        cls.group_food = ItemGroup.objects.create(name="Food")
        cls.warehouse = Warehouse.objects.create(name="Kitchen")
        cls.food_item = Item.objects.create(
            item_name="Jollof Rice",
            item_group=cls.group_food,
            stock_uom=cls.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        cls.menu = Menu.objects.create(name="Main Menu")
        MenuItem.objects.create(menu=cls.menu, item=cls.food_item, rate=Decimal("1500"))
        cls.cash, _ = ModeOfPayment.objects.get_or_create(name="Cash", defaults={"type": "CASH"})
        cls.restaurant.active_menu = cls.menu
        cls.restaurant.default_warehouse = cls.warehouse
        cls.restaurant.save()
        cls._setup_accounting()
        ProductionUnit.objects.create(name="Kitchen", warehouse=cls.warehouse, department="FOOD")
        cls.manager = CustomUser.objects.create_user(
            username="manager", password="testpass123", is_staff=True, is_superuser=True
        )
        cls.cashier_user = CustomUser.objects.create_user(username="cashier2", password="testpass123")
        cls.opening = POSOpeningEntry.objects.create(cashier=cls.manager)
        OpeningPayment.objects.create(
            opening_entry=cls.opening,
            mode_of_payment=cls.cash,
            opening_amount=Decimal("50000"),
        )
        cls.opening.submit()
        Bin.objects.create(item=cls.food_item, warehouse=cls.warehouse, actual_qty=Decimal("100"))

    def setUp(self):
        self.client.force_login(self.manager)

    def _create_order(self, **kwargs):
        defaults = {"opening_entry": self.opening}
        defaults.update(kwargs)
        return Order.objects.create(**defaults)


class OrderCancelTest(BackofficeViewTestBase):
    def _sent_order(self):
        order = self._create_order()
        add_order_line(order, self.food_item, qty=1, rate=Decimal("1500"))
        create_tickets(order, created_by=self.manager)
        return order

    def test_cancel_order_manager(self):
        order = self._sent_order()
        response = self.client.post(
            reverse("orders:order_cancel", kwargs={"pk": order.pk}),
            {"cancel_reason": "wrong_order", "cancel_reason_note": "Test cancel"},
        )
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, "CANCELLED")

    def test_cancel_order_cashier_blocked(self):
        self.client.logout()
        self.client.force_login(self.cashier_user)
        order = self._sent_order()
        response = self.client.post(
            reverse("orders:order_cancel", kwargs={"pk": order.pk}),
            {"cancel_reason": "wrong_order", "cancel_reason_note": "Test"},
        )
        self.assertEqual(response.status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.status, "DRAFT")

    def test_order_delete_backoffice_manager(self):
        order = self._create_order()
        add_order_line(order, self.food_item, qty=1, rate=Decimal("1500"))
        response = self.client.post(reverse("orders:order_delete", kwargs={"pk": order.pk}))
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, "DISCARDED")
        self.assertEqual(order.discarded_by, self.manager)
        self.assertTrue(order.items.exists())
        self.assertTrue(order.audit_events.filter(event_type="ORDER_DELETED").exists())


class OrderReturnTest(BackofficeViewTestBase):
    def _settle_order(self, order):
        add_order_line(order, self.food_item, qty=2, rate=Decimal("1500"))
        settle_order(order, [{"mode_of_payment": self.cash.pk, "amount": "3000"}])
        order.refresh_from_db()

    def test_return_creates_draft_for_manager(self):
        order = self._create_order()
        self._settle_order(order)
        response = self.client.post(reverse("orders:order_return", kwargs={"pk": order.pk}))
        self.assertEqual(response.status_code, 302)
        return_order = Order.objects.filter(is_return=True).first()
        self.assertIsNotNone(return_order)
        self.assertEqual(return_order.status, "DRAFT")
        self.assertEqual(return_order.return_against, order)

    def test_return_draft_can_reduce_qty(self):
        order = self._create_order()
        self._settle_order(order)
        self.client.post(reverse("orders:order_return", kwargs={"pk": order.pk}))
        return_order = Order.objects.get(is_return=True)
        line = return_order.items.first()
        response = self.client.post(
            reverse("orders:order_return_line_update", kwargs={"pk": return_order.pk, "line_pk": line.pk}),
            {"qty": "1"},
        )
        self.assertEqual(response.status_code, 302)
        line.refresh_from_db()
        self.assertEqual(line.qty, Decimal("-1"))
        return_order.refresh_from_db()
        self.assertEqual(abs(return_order.grand_total), Decimal("1500"))

    def test_return_submit_view(self):
        order = self._create_order()
        self._settle_order(order)
        self.client.post(reverse("orders:order_return", kwargs={"pk": order.pk}))
        return_order = Order.objects.get(is_return=True)
        response = self.client.post(reverse("orders:order_return_submit", kwargs={"pk": return_order.pk}))
        self.assertEqual(response.status_code, 302)
        return_order.refresh_from_db()
        self.assertEqual(return_order.status, "SUBMITTED")
        self.assertEqual(return_order.payments.count(), 1)
        self.assertEqual(return_order.payments.get().amount, Decimal("-3000"))


class KOTDetailTest(BackofficeViewTestBase):
    def test_kot_detail_404(self):
        response = self.client.get(reverse("orders:kot_detail", kwargs={"pk": 99999}))
        self.assertEqual(response.status_code, 404)
