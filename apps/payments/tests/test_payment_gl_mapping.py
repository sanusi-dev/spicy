from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import LedgerAccount
from apps.payments.models import ModeOfPayment, PaymentGLMapping
from apps.settings.models import Restaurant

from .helpers import create_payment_accounts


class PaymentGLMappingModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.accounts = create_payment_accounts()
        cls.cash = ModeOfPayment.objects.create(name="Test Cash", type="CASH")
        cls.bank = ModeOfPayment.objects.create(name="Test Bank", type="BANK")
        cls.mapping = PaymentGLMapping.objects.create(mode_of_payment=cls.cash, default_account=cls.accounts["cash"])

    def test_clean_no_account_raises(self):
        m = PaymentGLMapping(mode_of_payment=self.bank)
        with self.assertRaises(ValidationError) as ctx:
            m.full_clean()
        self.assertIn("default_account", ctx.exception.message_dict)

    def test_leaf_only_validation(self):
        with self.assertRaises(ValidationError):
            PaymentGLMapping(mode_of_payment=self.bank, default_account=self.accounts["assets"]).full_clean()


class PaymentGLMappingIncomeAccountTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.accounts = create_payment_accounts()
        cls.food_sales = LedgerAccount.objects.create(
            name="Food Sales (payments test)",
            account_type=LedgerAccount.INCOME,
            report_type=LedgerAccount.PROFIT_AND_LOSS,
        )
        Restaurant.objects.create(company="Test Co", default_income_account=cls.food_sales)
        cls.cash = ModeOfPayment.objects.create(name="Test Cash", type="CASH")

    def test_clean_rejects_income_account(self):
        with self.assertRaisesMessage(ValidationError, "sales account"):
            PaymentGLMapping(mode_of_payment=self.cash, default_account=self.food_sales).full_clean()

    def test_cash_mapping_still_allowed_alongside_income_accounts(self):
        PaymentGLMapping(mode_of_payment=self.cash, default_account=self.accounts["cash"]).full_clean()
