"""Regression tests for review.md P1/P2 findings."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.inventory.models import UOM, Item, ItemGroup, StockEntry, Warehouse
from apps.menu.models import Menu, MenuItem
from apps.orders.management.commands.seed_pos_setup import Command as SeedPosSetup
from apps.orders.models import DRAFT, KOT_CANCELLED, KOT_PRINT_PENDING, KOT_PRINTED, Order
from apps.orders.printing import PrintResult
from apps.orders.services import (
    add_order_line,
    cancel_sent_order,
    create_tickets,
    settle_order,
    update_order_line_quantity,
)
from apps.payments.models import ModeOfPayment
from apps.settings.models import ProductionUnit, Restaurant
from apps.staff.models import ClosingPayment, OpeningPayment, POSClosingEntry, POSOpeningEntry
from apps.staff.services import submit_closing_entry

from .accounting_setup import OrderAccountingMixin

CustomUser = get_user_model()


class ReviewFixBase(OrderAccountingMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.restaurant = Restaurant.objects.create(company="Review Co")
        cls.uom = UOM.objects.create(name="Nos")
        cls.group = ItemGroup.objects.create(name="Food")
        cls.store = Warehouse.objects.create(name="Store")
        cls.bar = Warehouse.objects.create(name="Bar")
        cls.kitchen_wh = Warehouse.objects.create(name="Kitchen")
        cls.item = Item.objects.create(
            item_name="Jollof",
            item_group=cls.group,
            stock_uom=cls.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        cls.menu = Menu.objects.create(name="Main")
        cls.menu_item = MenuItem.objects.create(menu=cls.menu, item=cls.item, rate=Decimal("1000"))
        cls.restaurant.active_menu = cls.menu
        cls.restaurant.default_warehouse = cls.bar
        cls.restaurant.store_warehouse = cls.store
        cls.restaurant.save()
        cls._setup_accounting()
        cls.cash, _ = ModeOfPayment.objects.get_or_create(name="Cash", defaults={"type": "CASH"})
        if not cls.cash.is_default:
            ModeOfPayment.objects.exclude(pk=cls.cash.pk).filter(is_default=True).update(is_default=False)
            cls.cash.is_default = True
            cls.cash.save(update_fields=["is_default"])
        cls.bank, _ = ModeOfPayment.objects.get_or_create(name="Bank Transfer", defaults={"type": "BANK"})
        ProductionUnit.objects.get_or_create(
            department="FOOD",
            defaults={"name": "Kitchen", "warehouse": cls.kitchen_wh},
        )
        ProductionUnit.objects.get_or_create(
            department="DRINKS",
            defaults={"name": "Bar", "warehouse": cls.bar},
        )
        cls.user = CustomUser.objects.create_user(username="rev-cashier", password="pass")
        cls.user.groups.add(Group.objects.get(name="RestPOS Cashier"))

    def setUp(self):
        self.client.force_login(self.user)
        self.opening = POSOpeningEntry.objects.create(cashier=self.user)
        OpeningPayment.objects.create(
            opening_entry=self.opening, mode_of_payment=self.cash, opening_amount=Decimal("0")
        )
        OpeningPayment.objects.create(
            opening_entry=self.opening, mode_of_payment=self.bank, opening_amount=Decimal("0")
        )
        self.opening.submit()

    def _draft_order_with_item(self):
        order = Order.objects.create(opening_entry=self.opening)
        add_order_line(order, self.item, qty=1, rate=Decimal("1000"), menu_item=self.menu_item)
        return order


class DisabledLineValidationTest(ReviewFixBase):
    def test_quantity_update_rejects_disabled_item(self):
        order = self._draft_order_with_item()
        line = order.items.get()
        self.item.disabled = True
        self.item.save(update_fields=["disabled"])
        with self.assertRaises(ValidationError):
            update_order_line_quantity(order, line.pk, Decimal("2"))
        line.refresh_from_db()
        self.assertEqual(line.qty, Decimal("1"))

    def test_settle_rejects_disabled_menu_item(self):
        order = self._draft_order_with_item()
        self.menu_item.disabled = True
        self.menu_item.save(update_fields=["disabled"])
        with self.assertRaises(ValidationError):
            settle_order(
                order,
                [{"mode_of_payment": self.cash, "amount": Decimal("1000")}],
                cashier=self.user,
            )
        order.refresh_from_db()
        self.assertEqual(order.status, DRAFT)


class SeedStoreWarehouseTest(TestCase):
    def test_seed_assigns_store_and_bar_warehouses(self):
        Restaurant.objects.create(company="Seed Co")
        SeedPosSetup().handle()
        restaurant = Restaurant.load()
        self.assertIsNotNone(restaurant.store_warehouse_id)
        self.assertIsNotNone(restaurant.default_warehouse_id)
        self.assertEqual(restaurant.store_warehouse.name, "Store")
        self.assertEqual(restaurant.default_warehouse.name, "Bar")


class OpeningCancelBlocksOrdersTest(ReviewFixBase):
    def test_cancel_blocked_when_orders_exist(self):
        Order.objects.create(opening_entry=self.opening)
        with self.assertRaises(ValidationError):
            self.opening.cancel(by_user=self.user)
        self.opening.refresh_from_db()
        self.assertEqual(self.opening.status, POSOpeningEntry.SUBMITTED)


class ClosingPeriodEndIncludesLateOrdersTest(ReviewFixBase):
    def test_submit_includes_orders_after_draft_created(self):
        # Backdate the draft close; submit must refresh its cutoff before
        # totaling an order settled after the draft was opened.
        closing = POSClosingEntry.objects.create(
            opening_entry=self.opening,
            cashier=self.user,
            period_end_date=timezone.now() - timedelta(hours=1),
        )
        for op in self.opening.opening_payments.all():
            # Counted cash covers the settled order; bank processed nothing, and
            # an over-counted non-cash total is rejected at submit.
            counted = Decimal("1000") if op.mode_of_payment.type == ModeOfPayment.TYPE_CASH else Decimal("0")
            ClosingPayment.objects.create(
                closing_entry=closing,
                mode_of_payment=op.mode_of_payment,
                opening_amount=op.opening_amount,
                expected_amount=op.opening_amount,
                closing_amount=counted,
            )
        order = self._draft_order_with_item()
        settle_order(
            order,
            [{"mode_of_payment": self.cash, "amount": Decimal("1000")}],
            cashier=self.user,
        )
        order.refresh_from_db()
        submit_closing_entry(closing)
        closing.refresh_from_db()
        self.assertEqual(closing.status, POSClosingEntry.SUBMITTED)
        self.assertEqual(closing.grand_total, Decimal("1000"))
        self.assertGreaterEqual(closing.period_end_date, order.submitted_at)


class InventorySubmittedImmutableTest(ReviewFixBase):
    def test_submitted_stock_entry_cannot_be_edited(self):
        entry = StockEntry.objects.create(purpose="MATERIAL_RECEIPT", status="DRAFT")
        entry.status = "SUBMITTED"
        entry.save(update_fields=["status"])
        entry.remarks = "tamper"
        with self.assertRaises(ValidationError):
            entry.save()


class CancellationTicketRetryTest(ReviewFixBase):
    def test_retry_pending_cancellation_ticket_on_cancelled_order(self):
        order = self._draft_order_with_item()
        create_tickets(order, created_by=self.user)
        cancel_sent_order(order, reason="wrong_order", cancelled_by=self.user)
        cancel_ticket = order.kots.filter(type=KOT_CANCELLED).first()
        self.assertIsNotNone(cancel_ticket)
        # Simulate the print agent failing after cancellation, leaving a ticket
        # that order history must allow the cashier to retry.
        cancel_ticket.print_status = KOT_PRINT_PENDING
        cancel_ticket.save(update_fields=["print_status"])
        with patch(
            "apps.orders.views_pos.printing.print_ticket",
            return_value=PrintResult(success=True, ticket_type="kitchen"),
        ):
            response = self.client.post(
                reverse(
                    "pos:pos_order_ticket_print",
                    kwargs={"pk": order.pk, "ticket_type": "kitchen", "action": "retry"},
                )
            )
        self.assertEqual(response.status_code, 302)
        cancel_ticket.refresh_from_db()
        self.assertEqual(cancel_ticket.print_status, KOT_PRINTED)


class DefaultPaymentModeTest(TestCase):
    def test_cannot_unset_only_default(self):
        cash, _ = ModeOfPayment.objects.get_or_create(name="Cash", defaults={"type": "CASH"})
        ModeOfPayment.objects.exclude(pk=cash.pk).filter(is_default=True).update(is_default=False)
        cash.is_default = True
        cash.save(update_fields=["is_default"])
        cash.is_default = False
        with self.assertRaises(ValidationError):
            cash.save()
