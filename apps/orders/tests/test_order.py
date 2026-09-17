import threading
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase

from apps.inventory.models import UOM, Bin, Item, ItemGroup, StockLedgerEntry, Warehouse
from apps.menu.models import Menu, MenuItem
from apps.payments.models import ModeOfPayment, PaymentGLMapping
from apps.settings.models import ProductionUnit, Restaurant
from apps.staff.models import OpeningPayment, POSOpeningEntry

from ..models import DISCARDED, Order, OrderItem, OrderPayment, OrderSequence
from ..services import (
    add_order_line,
    cancel_sent_order,
    clear_order_lines,
    create_tickets,
    delete_unsent_draft,
    discard_order,
    make_return,
    remove_order_line,
    settle_order,
    submit_return,
    update_order_line_quantity,
)
from .accounting_setup import OrderAccountingMixin

CustomUser = get_user_model()


class OrderTestBase(OrderAccountingMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.restaurant = Restaurant.objects.create(company="Test Co")
        cls.uom = UOM.objects.create(name="Nos")
        cls.group_food = ItemGroup.objects.create(name="Food")
        cls.group_drinks = ItemGroup.objects.create(name="Beverages")
        cls.warehouse = Warehouse.objects.create(name="Kitchen")
        cls.item = Item.objects.create(
            item_name="Jollof Rice",
            item_group=cls.group_food,
            stock_uom=cls.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        cls.item2 = Item.objects.create(
            item_name="Coke",
            item_group=cls.group_drinks,
            stock_uom=cls.uom,
            department="DRINKS",
            is_sales_item=True,
            is_stock_item=True,
            is_purchase_item=True,
        )
        cls.menu = Menu.objects.create(name="Main Menu")
        cls.menu_item = MenuItem.objects.create(menu=cls.menu, item=cls.item, rate=Decimal("1500"))
        cls.menu_item2 = MenuItem.objects.create(menu=cls.menu, item=cls.item2, rate=Decimal("500"))
        Bin.objects.create(item=cls.item, warehouse=cls.warehouse, actual_qty=Decimal("100"))
        Bin.objects.create(item=cls.item2, warehouse=cls.warehouse, actual_qty=Decimal("100"))
        cls.cash, _ = ModeOfPayment.objects.get_or_create(name="Cash", defaults={"type": "CASH"})
        cls.restaurant.active_menu = cls.menu
        cls.restaurant.default_warehouse = cls.warehouse
        cls.restaurant.save()
        cls._setup_accounting()
        cls.user = CustomUser.objects.create_user(username="cashier", password="testpass123")
        cls.sequence, _ = OrderSequence.objects.get_or_create(name="order", defaults={"current_value": 0})
        cls.opening = POSOpeningEntry.objects.create(cashier=cls.user)
        OpeningPayment.objects.create(
            opening_entry=cls.opening,
            mode_of_payment=cls.cash,
            opening_amount=Decimal("50000"),
        )
        cls.opening.submit()
        cls.kitchen = ProductionUnit.objects.create(name="Kitchen", warehouse=cls.warehouse, department="FOOD")
        cls.bar = ProductionUnit.objects.create(name="Bar", warehouse=cls.warehouse, department="DRINKS")

    def _create_order(self, **kwargs):
        defaults = {"opening_entry": self.opening}
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    def _create_and_print_order(self, **kwargs):
        order = self._create_order(**kwargs)
        order.invoice_printed = True
        order.save(update_fields=["invoice_printed"])
        return order


class OrderModelTest(OrderTestBase):
    def test_order_number_assignment(self):
        order = self._create_order()
        order.assign_order_number()
        self.assertEqual(order.order_number, 1)
        order2 = self._create_order()
        order2.assign_order_number()
        self.assertEqual(order2.order_number, 2)


class OrderItemTest(OrderTestBase):
    @skipUnless(connection.vendor == "postgresql", "PostgreSQL-specific row-lock regression")
    def test_reservation_locks_restaurant_without_nullable_outer_join(self):
        order = self._create_order()

        # This minimal reservation path exercises the PostgreSQL lock query
        # without introducing a nullable related warehouse join.
        add_order_line(order, self.item2, qty=1, rate=Decimal("500"))

        self.assertEqual(Bin.objects.get(item=self.item2, warehouse=self.warehouse).reserved_qty, Decimal("1"))

    def test_add_item(self):
        order = self._create_order()
        add_order_line(order, self.item, qty=2, rate=Decimal("1500"))
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.items.first().qty, Decimal("2"))

    def test_department_snapshots_from_item(self):
        order = self._create_order()
        add_order_line(order, self.item, qty=2, rate=Decimal("1500"))
        self.assertEqual(order.items.first().department, self.item.department)

    def test_department_constraint_rejects_null_and_unknown_values(self):
        order = self._create_order()
        add_order_line(order, self.item, qty=1, rate=Decimal("1500"))
        pk = order.items.first().pk
        for bad in (None, "OTHER"):
            with self.subTest(value=bad), self.assertRaises(IntegrityError), transaction.atomic():
                OrderItem.objects.filter(pk=pk).update(department=bad)

    def test_add_item_increments_existing(self):
        order = self._create_order()
        add_order_line(order, self.item, qty=1, rate=Decimal("1500"))
        add_order_line(order, self.item, qty=1, rate=Decimal("1500"))
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.items.first().qty, Decimal("2"))

    def test_different_comments_creates_separate_line(self):
        order = self._create_order()
        add_order_line(order, self.item, qty=1, rate=Decimal("1500"), comments="Extra spicy")
        add_order_line(order, self.item, qty=1, rate=Decimal("1500"), comments="No spice")
        self.assertEqual(order.items.count(), 2)

    def test_different_customer_index_creates_separate_line(self):
        order = self._create_order(guest_count=2)
        add_order_line(order, self.item, qty=1, rate=Decimal("1500"), customer_index=1)
        add_order_line(order, self.item, qty=1, rate=Decimal("1500"), customer_index=2)
        self.assertEqual(order.items.count(), 2)

    def test_remove_item(self):
        order = self._create_order()
        add_order_line(order, self.item, qty=1, rate=Decimal("1500"))
        oi_pk = order.items.first().pk
        remove_order_line(order, oi_pk)
        self.assertEqual(order.items.count(), 0)

    def test_food_stock_item_never_reserves(self):
        order = self._create_order()
        add_order_line(order, self.item, qty=1000, rate=Decimal("1500"))
        stock_bin = Bin.objects.get(item=self.item, warehouse=self.warehouse)
        self.assertEqual(stock_bin.reserved_qty, Decimal("0"))
        self.assertIsNone(order.stock_warehouse_id)

    def test_drink_add_increment_decrement_and_remove_reservation(self):
        order = self._create_order()
        add_order_line(order, self.item2, qty=2, rate=Decimal("500"))
        stock_bin = Bin.objects.get(item=self.item2, warehouse=self.warehouse)
        self.assertEqual(stock_bin.reserved_qty, Decimal("2"))
        self.assertEqual(order.stock_warehouse_id, self.warehouse.pk)

        line = order.items.get()
        update_order_line_quantity(order, line.pk, Decimal("3"))
        stock_bin.refresh_from_db()
        self.assertEqual(stock_bin.reserved_qty, Decimal("3"))

        update_order_line_quantity(order, line.pk, Decimal("1"))
        stock_bin.refresh_from_db()
        self.assertEqual(stock_bin.reserved_qty, Decimal("1"))

        remove_order_line(order, line.pk)
        stock_bin.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(stock_bin.reserved_qty, Decimal("0"))
        self.assertIsNone(order.stock_warehouse_id)

    def test_drink_reservation_rejects_non_stock_configuration(self):
        self.item2.is_stock_item = False
        self.item2.save(update_fields=["is_stock_item"])
        order = self._create_order()
        with self.assertRaisesMessage(ValidationError, "must be a stock-tracked"):
            add_order_line(order, self.item2, qty=1, rate=Decimal("500"))

    def test_drink_reservation_rejects_quantity_above_available(self):
        Bin.objects.filter(item=self.item2, warehouse=self.warehouse).update(
            actual_qty=Decimal("2"), reserved_qty=Decimal("1")
        )
        order = self._create_order()
        with self.assertRaisesMessage(ValidationError, "Insufficient stock"):
            add_order_line(order, self.item2, qty=2, rate=Decimal("500"))
        self.assertEqual(order.items.count(), 0)
        self.assertEqual(Bin.objects.get(item=self.item2, warehouse=self.warehouse).reserved_qty, Decimal("1"))

    def test_clear_and_delete_release_drink_reservation(self):
        order = self._create_order()
        add_order_line(order, self.item2, qty=2, rate=Decimal("500"))
        clear_order_lines(
            order,
        )
        self.assertEqual(Bin.objects.get(item=self.item2, warehouse=self.warehouse).reserved_qty, Decimal("0"))

        add_order_line(order, self.item2, qty=3, rate=Decimal("500"))
        delete_unsent_draft(order, deleted_by=self.user)
        self.assertEqual(Bin.objects.get(item=self.item2, warehouse=self.warehouse).reserved_qty, Decimal("0"))

    def test_release_reservation_from_bypassed_disabled_snapshot(self):
        order = self._create_order()
        add_order_line(order, self.item2, qty=2, rate=Decimal("500"))
        Warehouse.objects.filter(pk=self.warehouse.pk).update(disabled=True)
        order.refresh_from_db()

        clear_order_lines(
            order,
        )

        self.assertEqual(Bin.objects.get(item=self.item2, warehouse=self.warehouse).reserved_qty, Decimal("0"))
        self.assertIsNone(order.stock_warehouse_id)

    def test_disabled_snapshot_rejects_reservation_increase(self):
        order = self._create_order()
        add_order_line(order, self.item2, qty=1, rate=Decimal("500"))
        Warehouse.objects.filter(pk=self.warehouse.pk).update(disabled=True)
        order.refresh_from_db()

        with self.assertRaisesMessage(ValidationError, "snapshot is disabled"):
            update_order_line_quantity(order, order.items.get().pk, Decimal("2"))

    def test_hard_delete_refused(self):
        order = self._create_order()

        with self.assertRaisesMessage(ValidationError, "cannot be hard-deleted"):
            order.delete()

    def test_delete_unsent_draft_keeps_tombstone_and_audit_trail(self):
        order = self._create_order()
        add_order_line(order, self.item, qty=1, rate=Decimal("1500"))

        delete_unsent_draft(order, deleted_by=self.user)

        order.refresh_from_db()
        self.assertEqual(order.status, DISCARDED)
        self.assertEqual(order.discarded_by, self.user)
        self.assertIsNotNone(order.discarded_at)
        self.assertEqual(order.items.count(), 1)
        self.assertTrue(order.audit_events.filter(event_type="CREATED").exists())
        tombstone = order.audit_events.get(event_type="ORDER_DELETED")
        self.assertEqual(tombstone.actor, self.user)
        self.assertEqual(tombstone.metadata["items"][0]["item"], self.item.item_name)


class OrderSettleTest(OrderTestBase):
    def setUp(self):
        self.order = self._create_order()
        add_order_line(self.order, self.item, qty=2, rate=Decimal("1500"))

    def _add_bank_mode(self):
        bank, _ = ModeOfPayment.objects.get_or_create(name="Bank", defaults={"type": "BANK"})
        PaymentGLMapping.objects.get_or_create(
            mode_of_payment=bank, defaults={"default_account": self.accounts["bank"]}
        )
        OpeningPayment.objects.get_or_create(
            opening_entry=self.opening,
            mode_of_payment=bank,
            defaults={"opening_amount": Decimal("0")},
        )
        return bank

    def test_settle_changes_status(self):
        settle_order(self.order, [{"mode_of_payment": self.cash.pk, "amount": "3000"}])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "SUBMITTED")
        self.assertTrue(self.order.is_paid)

    def test_settle_accepts_electronic_payment_without_reference(self):
        bank = self._add_bank_mode()

        settle_order(self.order, [{"mode_of_payment": bank.pk, "amount": "3000"}])

        payment = self.order.payments.get()
        self.assertEqual(payment.mode_of_payment, bank)
        self.assertEqual(payment.reference_no, "")

    def test_settle_ignores_zero_payment_rows(self):
        bank = self._add_bank_mode()

        settle_order(
            self.order,
            [
                {"mode_of_payment": bank.pk, "amount": "0"},
                {"mode_of_payment": self.cash.pk, "amount": "3000"},
            ],
        )

        self.assertEqual(self.order.payments.count(), 1)
        self.assertEqual(self.order.payments.get().mode_of_payment, self.cash)

    def test_settle_rejects_all_zero_payment_rows(self):
        with self.assertRaisesMessage(ValidationError, "At least one payment is required"):
            settle_order(self.order, [{"mode_of_payment": self.cash.pk, "amount": "0"}])

    def test_settle_rejects_negative_payment_rows(self):
        with self.assertRaisesMessage(ValidationError, "amount must not be negative"):
            settle_order(self.order, [{"mode_of_payment": self.cash.pk, "amount": "-1"}])

    def test_settle_computes_change(self):
        settle_order(self.order, [{"mode_of_payment": self.cash.pk, "amount": "5000"}])
        self.order.refresh_from_db()
        self.assertTrue(self.order.change_amount > 0)

    def test_settle_rejects_underpayment(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            settle_order(self.order, [{"mode_of_payment": self.cash.pk, "amount": "1000"}])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "DRAFT")

    def test_settle_food_stock_item_bypasses_stock(self):
        Bin.objects.filter(item=self.item, warehouse=self.warehouse).update(actual_qty=Decimal("10"))
        settle_order(self.order, [{"mode_of_payment": self.cash.pk, "amount": "3000"}])
        sles = StockLedgerEntry.objects.filter(voucher_type="POS Order", voucher_no=str(self.order.pk))
        self.assertFalse(sles.exists())
        self.order.refresh_from_db()
        self.assertIsNone(self.order.stock_warehouse_id)

    def test_settle_converts_drink_reservation_to_deduction(self):
        add_order_line(self.order, self.item2, qty=1, rate=Decimal("500"))
        self.assertEqual(Bin.objects.get(item=self.item2, warehouse=self.warehouse).reserved_qty, Decimal("1"))
        settle_order(self.order, [{"mode_of_payment": self.cash.pk, "amount": "3500"}])
        sles = StockLedgerEntry.objects.filter(voucher_type="POS Order", voucher_no=str(self.order.pk))
        self.assertEqual(sles.count(), 1)
        stock_bin = Bin.objects.get(item=self.item2, warehouse=self.warehouse)
        self.assertEqual(stock_bin.reserved_qty, Decimal("0"))
        self.assertEqual(stock_bin.actual_qty, Decimal("99"))

    def test_settle_one_qty_is_one_stock_unit_after_crate_receipt(self):
        from apps.inventory.models import ItemUOMConversion, PurchaseReceipt, PurchaseReceiptItem
        from apps.inventory.services import submit_purchase_receipt

        self.restaurant.store_warehouse = self.warehouse
        self.restaurant.save(update_fields=["store_warehouse", "updated_at"])
        crate, _ = UOM.objects.get_or_create(name="Crate")
        ItemUOMConversion.objects.create(item=self.item2, uom=crate, conversion_factor=Decimal("24"))
        receipt = PurchaseReceipt.objects.create(supplier_name="Brewery", warehouse=self.warehouse)
        PurchaseReceiptItem.objects.create(
            purchase_receipt=receipt,
            item=self.item2,
            received_qty=Decimal("1"),
            rate=Decimal("7200"),
            uom=crate,
        )
        submit_purchase_receipt(receipt)
        stock_bin = Bin.objects.get(item=self.item2, warehouse=self.warehouse)
        self.assertEqual(stock_bin.actual_qty, Decimal("124"))
        add_order_line(self.order, self.item2, qty=1, rate=Decimal("500"))
        settle_order(self.order, [{"mode_of_payment": self.cash.pk, "amount": "3500"}])
        stock_bin.refresh_from_db()
        self.assertEqual(stock_bin.actual_qty, Decimal("123"))
        sle = StockLedgerEntry.objects.get(voucher_type="POS Order", voucher_no=str(self.order.pk), item=self.item2)
        self.assertEqual(sle.quantity, Decimal("-1"))

    def test_settle_failure_rolls_back_payment_status_stock_and_reservation(self):
        add_order_line(self.order, self.item2, qty=1, rate=Decimal("500"))
        with (
            patch.object(StockLedgerEntry, "_create_entry_locked", side_effect=ValidationError("Ledger failed")),
            self.assertRaisesMessage(ValidationError, "Ledger failed"),
        ):
            settle_order(self.order, [{"mode_of_payment": self.cash.pk, "amount": "3500"}])
        self.order.refresh_from_db()
        stock_bin = Bin.objects.get(item=self.item2, warehouse=self.warehouse)
        self.assertEqual(self.order.status, "DRAFT")
        self.assertEqual(self.order.payments.count(), 0)
        self.assertEqual(stock_bin.actual_qty, Decimal("100"))
        self.assertEqual(stock_bin.reserved_qty, Decimal("1"))

    def test_settle_preserves_snapshot_before_rejecting_changed_configuration(self):
        add_order_line(self.order, self.item2, qty=1, rate=Decimal("500"))
        changed = Warehouse.objects.create(name="Changed Bar")
        Restaurant.objects.filter(pk=self.restaurant.pk).update(default_warehouse=changed)

        with self.assertRaisesMessage(ValidationError, "warehouse changed"):
            settle_order(self.order, [{"mode_of_payment": self.cash.pk, "amount": "3500"}])

        self.order.refresh_from_db()
        self.assertEqual(self.order.stock_warehouse_id, self.warehouse.pk)
        self.assertEqual(self.order.status, "DRAFT")
        self.assertEqual(self.order.payments.count(), 0)

    def test_settle_takeaway_no_print_required(self):
        order = self._create_order(order_type="TAKE_AWAY")
        add_order_line(order, self.item, qty=1, rate=Decimal("1500"))
        settle_order(order, [{"mode_of_payment": self.cash.pk, "amount": "1500"}])
        self.assertEqual(order.status, "SUBMITTED")

    def test_settle_requires_active_shift(self):
        from django.core.exceptions import ValidationError

        order = self._create_order(opening_entry=None)
        add_order_line(order, self.item, qty=1, rate=Decimal("1500"))
        with self.assertRaises(ValidationError):
            settle_order(order, [{"mode_of_payment": self.cash.pk, "amount": "1500"}])

    def test_non_cash_overpayment_is_rejected(self):
        bank = self._add_bank_mode()
        with self.assertRaises(ValidationError):
            settle_order(self.order, [{"mode_of_payment": bank.pk, "amount": "3500", "reference_no": "BANK-1"}])


class OrderCancelTest(OrderTestBase):
    def setUp(self):
        self.order = self._create_order()
        add_order_line(self.order, self.item, qty=2, rate=Decimal("1500"))
        create_tickets(
            self.order,
        )

    def test_paid_order_requires_refund_workflow(self):
        from django.core.exceptions import ValidationError

        Bin.objects.filter(item=self.item, warehouse=self.warehouse).update(actual_qty=Decimal("10"))
        settle_order(self.order, [{"mode_of_payment": self.cash.pk, "amount": "3000"}])
        with self.assertRaises(ValidationError):
            cancel_sent_order(self.order, "Test reason")

    def test_cancel_sets_status(self):
        cancel_sent_order(self.order, "Test reason")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "CANCELLED")
        self.assertEqual(self.order.cancel_reason, "other")
        self.assertEqual(self.order.cancel_reason_note, "Test reason")

    def test_cancel_requires_reason(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            cancel_sent_order(self.order, "")

    def test_cancel_creates_cancel_kot(self):
        cancel_sent_order(self.order, "Test reason")
        cancel_kots = self.order.kots.filter(type="Cancelled")
        self.assertTrue(cancel_kots.exists())

    def test_cancel_unsent_draft_requires_delete(self):
        """An unsent draft is deleted, never cancelled — stage rule 1."""
        draft = self._create_order()
        add_order_line(draft, self.item, qty=1, rate=Decimal("1500"))
        with self.assertRaisesMessage(ValidationError, "never sent — delete it instead"):
            cancel_sent_order(draft, "Changed mind")
        draft.refresh_from_db()
        self.assertEqual(draft.status, "DRAFT")

    def test_cancel_sent_order_releases_drink_reservation(self):
        order = self._create_order()
        add_order_line(order, self.item2, qty=2, rate=Decimal("500"))
        create_tickets(
            order,
        )
        cancel_sent_order(order, "wrong_order")
        self.assertEqual(Bin.objects.get(item=self.item2, warehouse=self.warehouse).reserved_qty, Decimal("0"))

    def test_discard_empty_draft(self):
        draft = self._create_order()
        discard_order(draft, discarded_by=self.user)
        draft.refresh_from_db()
        self.assertEqual(draft.status, "DISCARDED")
        self.assertEqual(draft.discarded_by, self.user)
        self.assertIsNotNone(draft.discarded_at)
        self.assertEqual(draft.audit_events.filter(event_type="DISCARDED").count(), 1)

    def test_discard_blocks_cancelled_order(self):
        from django.core.exceptions import ValidationError

        draft = self._create_order()
        add_order_line(draft, self.item, qty=1, rate=Decimal("1500"))
        create_tickets(
            draft,
        )
        cancel_sent_order(draft, "Changed mind")
        with self.assertRaisesMessage(ValidationError, "Only draft orders can be discarded."):
            discard_order(
                draft,
            )


class OrderReturnTest(OrderTestBase):
    """Model-level tests for Order.make_return() and return validation."""

    def setUp(self):
        self.order = self._create_order()
        add_order_line(self.order, self.item, qty=2, rate=Decimal("1500"))
        add_order_line(self.order, self.item2, qty=1, rate=Decimal("500"))
        settle_order(self.order, [{"mode_of_payment": self.cash.pk, "amount": "3500"}])

    def test_make_return_creates_is_return_true(self):
        return_order = make_return(
            self.order,
        )
        self.assertTrue(return_order.is_return)
        self.assertEqual(return_order.return_against, self.order)

    def test_make_return_item_qty_negative(self):
        return_order = make_return(
            self.order,
        )
        for item in return_order.items.all():
            self.assertLess(item.qty, 0)
            self.assertLess(item.amount, 0)

    def test_duplicate_return_is_rejected(self):
        from django.core.exceptions import ValidationError

        make_return(
            self.order,
        )
        with self.assertRaises(ValidationError):
            make_return(
                self.order,
            )

    def test_cannot_return_draft_order(self):
        from django.core.exceptions import ValidationError

        draft = self._create_order()
        add_order_line(draft, self.item, qty=1, rate=Decimal("1500"))
        with self.assertRaises(ValidationError):
            make_return(
                draft,
            )

    def test_clean_requires_return_against_when_is_return(self):
        from django.core.exceptions import ValidationError

        order = Order(is_return=True)
        with self.assertRaises(ValidationError):
            order.clean()

    def test_clean_blocks_chain_return(self):
        from django.core.exceptions import ValidationError

        return_order = make_return(
            self.order,
        )
        chain = Order(is_return=True, return_against=return_order)
        with self.assertRaises(ValidationError):
            chain.clean()

    def test_settle_return_is_deferred(self):
        from django.core.exceptions import ValidationError

        return_order = make_return(
            self.order,
        )
        with self.assertRaises(ValidationError):
            settle_order(return_order, [{"mode_of_payment": self.cash.pk, "amount": "3500"}])


class SubmitReturnTest(OrderTestBase):
    def _settled_order(self):
        order = self._create_order()
        add_order_line(order, self.item, qty=2, rate=Decimal("1500"))
        add_order_line(order, self.item2, qty=1, rate=Decimal("500"))
        settle_order(order, [{"mode_of_payment": self.cash.pk, "amount": "3500"}])
        return order

    def test_submit_return_restores_drink_stock(self):
        order = self._settled_order()
        drink_balance = Bin.objects.get(item=self.item2, warehouse=self.warehouse).actual_qty
        wac_before = Bin.objects.get(item=self.item2, warehouse=self.warehouse).valuation_rate
        return_order = make_return(order)
        submit_return(return_order, actor=self.user)
        sle = StockLedgerEntry.objects.get(voucher_type="POS Return", voucher_no=str(return_order.pk), item=self.item2)
        self.assertEqual(sle.quantity, Decimal("1"))
        self.assertEqual(sle.unit_rate, wac_before)
        self.assertEqual(
            Bin.objects.get(item=self.item2, warehouse=self.warehouse).actual_qty,
            drink_balance + 1,
        )

    def test_submit_return_creates_negative_refund_rows(self):
        order = self._settled_order()
        return_order = make_return(order)
        submit_return(return_order, actor=self.user)
        return_order.refresh_from_db()
        self.assertEqual(return_order.payments.count(), 1)
        refund = return_order.payments.get()
        self.assertEqual(refund.mode_of_payment, self.cash)
        self.assertEqual(refund.amount, Decimal("-3500"))
        self.assertEqual(refund.reference_no, "")
        self.assertEqual(return_order.paid_amount, Decimal("-3500"))
        self.assertFalse(return_order.is_paid)
        self.assertEqual(return_order.status, "SUBMITTED")
        self.assertTrue(return_order.audit_events.filter(event_type="RETURN_SUBMITTED").exists())

    def test_submit_return_requires_draft(self):
        order = self._settled_order()
        return_order = make_return(order)
        submit_return(return_order, actor=self.user)
        with self.assertRaises(ValidationError):
            submit_return(return_order, actor=self.user)

    def test_submit_return_rejects_non_return_draft(self):
        draft = self._create_order()
        add_order_line(draft, self.item, qty=1, rate=Decimal("1500"))
        with self.assertRaises(ValidationError):
            submit_return(draft, actor=self.user)

    def test_submit_return_requires_submitted_source(self):
        source = self._create_order()
        add_order_line(source, self.item, qty=1, rate=Decimal("1500"))
        draft_return = Order.objects.create(
            opening_entry=self.opening,
            is_return=True,
            return_against=source,
        )
        with self.assertRaisesMessage(ValidationError, "must reference a submitted non-return order"):
            submit_return(draft_return, actor=self.user)


class OrderPaymentValidationTest(OrderTestBase):
    def test_negative_amount_rejected_on_normal_order(self):
        order = self._create_order()
        with self.assertRaisesMessage(ValidationError, "Payment amount must be greater than zero."):
            OrderPayment.objects.create(order=order, mode_of_payment=self.cash, amount=Decimal("-3500"))

    def test_negative_amount_allowed_on_return_draft(self):
        source = self._create_order()
        add_order_line(source, self.item, qty=2, rate=Decimal("1500"))
        settle_order(source, [{"mode_of_payment": self.cash.pk, "amount": "3000"}])
        return_order = make_return(source)
        OrderPayment.objects.create(
            order=return_order,
            mode_of_payment=self.cash,
            amount=Decimal("-3000"),
            reference_no="",
        )
        self.assertEqual(return_order.payments.get().amount, Decimal("-3000"))

    def test_missing_reference_rejected_on_normal_order_when_required(self):
        order = self._create_order()
        bank = ModeOfPayment.objects.create(name="Bank", type="BANK")
        Restaurant.objects.update(require_payment_reference=True)

        with self.assertRaisesMessage(ValidationError, "A reference is required for Bank payments."):
            OrderPayment.objects.create(order=order, mode_of_payment=bank, amount=Decimal("3000"), reference_no="")

    def test_missing_reference_allowed_on_return_draft_when_required(self):
        source = self._create_order()
        return_order = Order.objects.create(opening_entry=self.opening, is_return=True, return_against=source)
        bank = ModeOfPayment.objects.create(name="Bank", type="BANK")
        Restaurant.objects.update(require_payment_reference=True)

        payment = OrderPayment.objects.create(
            order=return_order,
            mode_of_payment=bank,
            amount=Decimal("-3000"),
            reference_no="",
        )

        self.assertEqual(payment.reference_no, "")


class OrderRecalculateTest(OrderTestBase):
    def test_recalculate_updates_grand_total(self):
        order = self._create_order()
        add_order_line(order, self.item, qty=2, rate=Decimal("1500"))
        order.recalculate_totals()
        self.assertEqual(order.grand_total, Decimal("3000.00"))


class OrderSequenceConcurrencyTest(TransactionTestCase):
    def setUp(self):
        OrderSequence.objects.get_or_create(name="order", defaults={"current_value": 0})

    def test_concurrent_assignment_yields_unique_consecutive_numbers(self):
        n_threads = 8
        results = []
        lock = threading.Lock()
        barrier = threading.Barrier(n_threads)

        def assign():
            try:
                # Start all workers together so the sequence row lock is tested
                # under real contention rather than by chance.
                barrier.wait()
                with transaction.atomic():
                    order = Order.objects.create()
                    number = order.assign_order_number()
                with lock:
                    results.append(number)
            finally:
                # Django connections are thread-local; close each worker's
                # connection before the test tears down the database.
                connection.close()

        threads = [threading.Thread(target=assign) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        connections.close_all()

        self.assertEqual(len(results), n_threads)
        self.assertEqual(sorted(results), list(range(1, n_threads + 1)))


class DrinkReservationConcurrencyTest(TransactionTestCase):
    def setUp(self):
        suffix = uuid4().hex
        restaurant = Restaurant.objects.create(company=f"Concurrent Test {suffix}")
        uom = UOM.objects.create(name=f"Bottle {suffix}")
        group = ItemGroup.objects.create(name=f"Drinks {suffix}")
        warehouse = Warehouse.objects.create(name=f"Bar {suffix}")
        self.item = Item.objects.create(
            item_name="Malt",
            item_group=group,
            stock_uom=uom,
            department="DRINKS",
            is_sales_item=True,
            is_stock_item=True,
            is_purchase_item=True,
        )
        Bin.objects.create(item=self.item, warehouse=warehouse, actual_qty=Decimal("1"))
        restaurant.default_warehouse = warehouse
        restaurant.save(update_fields=["default_warehouse"])
        self.orders = [Order.objects.create(), Order.objects.create()]

    def test_concurrent_orders_cannot_reserve_the_same_last_unit(self):
        outcomes = []
        result_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def reserve(order_pk):
            try:
                # Force both orders to compete for the same final unit.
                barrier.wait()
                order = Order.objects.get(pk=order_pk)
                add_order_line(order, self.item, qty=1, rate=Decimal("500"))
                outcome = "reserved"
            except ValidationError:
                outcome = "rejected"
            finally:
                connection.close()
            with result_lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=reserve, args=(order.pk,)) for order in self.orders]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        connections.close_all()

        self.assertCountEqual(outcomes, ["reserved", "rejected"])
        stock_bin = Bin.objects.get(item=self.item)
        self.assertEqual(stock_bin.reserved_qty, Decimal("1"))
        self.assertEqual(sum(order.items.count() for order in self.orders), 1)
