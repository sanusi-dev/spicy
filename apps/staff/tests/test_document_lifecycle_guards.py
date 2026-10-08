"""Immutability guards for shift documents at the ORM level."""

from decimal import Decimal

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase

from apps.payments.models import ModeOfPayment
from apps.settings.models import Restaurant
from apps.staff.admin import ClosingPaymentAdmin, OpeningPaymentAdmin, POSClosingEntryAdmin, POSOpeningEntryAdmin
from apps.staff.models import ClosingPayment, OpeningPayment, POSClosingEntry, POSOpeningEntry
from apps.staff.services import submit_closing_entry
from apps.users.models import CustomUser


class ShiftDocumentGuardBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = CustomUser.objects.create_user(username="manager", password="testpass123")
        Restaurant.objects.create(company="Guard Co")
        cls.cash, _ = ModeOfPayment.objects.get_or_create(name="Cash", defaults={"type": "CASH"})
        cls.card, _ = ModeOfPayment.objects.get_or_create(name="Card", defaults={"type": "BANK"})
        cls.opening = POSOpeningEntry.objects.create(cashier=cls.user)
        cls.opening_row = OpeningPayment.objects.create(
            opening_entry=cls.opening, mode_of_payment=cls.cash, opening_amount=Decimal("0")
        )

    def _draft_closing(self):
        self.opening.submit()
        closing = POSClosingEntry.objects.create(opening_entry=self.opening, cashier=self.user)
        ClosingPayment.objects.create(closing_entry=closing, mode_of_payment=self.cash, closing_amount=Decimal("0"))
        return closing

    def _submitted_closing(self):
        closing = self._draft_closing()
        submit_closing_entry(closing)
        closing.refresh_from_db()
        return closing


class ClosingEntryGuardTest(ShiftDocumentGuardBase):
    def test_service_submit_saves_through_the_flag(self):
        closing = self._submitted_closing()
        self.assertEqual(closing.status, POSClosingEntry.SUBMITTED)

    def test_submitted_closing_rejects_orm_writes(self):
        closing = self._submitted_closing()
        closing.remarks = "tampered"
        with self.assertRaises(ValidationError):
            closing.save(update_fields=["remarks", "updated_at"])
        closing.refresh_from_db()
        self.assertNotEqual(closing.remarks, "tampered")

    def test_closing_rows_reject_saves_and_deletes_after_submit(self):
        closing = self._submitted_closing()
        row = closing.closing_payments.get()
        row.closing_amount = Decimal("5")
        with self.assertRaises(ValidationError):
            row.save(update_fields=["closing_amount", "updated_at"])
        with self.assertRaises(ValidationError):
            ClosingPayment.objects.create(closing_entry=closing, mode_of_payment=self.card)
        with self.assertRaises(ValidationError):
            row.delete()

    def test_closing_rows_stay_writable_while_draft(self):
        closing = self._draft_closing()
        row = closing.closing_payments.get()
        row.closing_amount = Decimal("7")
        row.save(update_fields=["closing_amount", "updated_at"])
        ClosingPayment.objects.create(closing_entry=closing, mode_of_payment=self.card)
        extra = closing.closing_payments.get(mode_of_payment=self.card)
        extra.delete()
        self.assertEqual(closing.closing_payments.count(), 1)

    def test_cancel_works_then_locks_again(self):
        closing = self._submitted_closing()
        closing.cancel(by_user=self.user)
        closing.refresh_from_db()
        self.assertEqual(closing.status, POSClosingEntry.CANCELLED)
        closing.remarks = "tampered"
        with self.assertRaises(ValidationError):
            closing.save(update_fields=["remarks", "updated_at"])


class OpeningEntryGuardTest(ShiftDocumentGuardBase):
    def test_opening_rows_reject_saves_and_deletes_after_submit(self):
        self.opening.submit()
        self.opening_row.opening_amount = Decimal("9")
        with self.assertRaises(ValidationError):
            self.opening_row.save(update_fields=["opening_amount", "updated_at"])
        with self.assertRaises(ValidationError):
            OpeningPayment.objects.create(opening_entry=self.opening, mode_of_payment=self.card)
        with self.assertRaises(ValidationError):
            self.opening_row.delete()

    def test_opening_rows_stay_writable_while_draft(self):
        row = OpeningPayment.objects.create(opening_entry=self.opening, mode_of_payment=self.card)
        row.opening_amount = Decimal("12")
        row.save(update_fields=["opening_amount", "updated_at"])
        row.delete()
        self.assertFalse(self.opening.opening_payments.filter(mode_of_payment=self.card).exists())


class ShiftAdminReadOnlyTest(ShiftDocumentGuardBase):
    def test_every_shift_admin_registration_is_view_only(self):
        request = RequestFactory().get("/")
        for admin_class in (POSOpeningEntryAdmin, OpeningPaymentAdmin, POSClosingEntryAdmin, ClosingPaymentAdmin):
            instance = admin_class(self.opening.__class__, admin.site)
            self.assertFalse(instance.has_add_permission(request), admin_class.__name__)
            self.assertFalse(instance.has_change_permission(request, self.opening), admin_class.__name__)
            self.assertFalse(instance.has_delete_permission(request, self.opening), admin_class.__name__)
