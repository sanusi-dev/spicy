"""Shared chart-of-accounts setup for orders test bases.

Settlement now fails closed when the account chain is missing, so every test
base that settles orders needs the baseline chart and FK GL mappings.
"""

from apps.accounting.tests.helpers import setup_chart_of_accounts
from apps.inventory.models import Warehouse
from apps.payments.models import ModeOfPayment, PaymentGLMapping


class OrderAccountingMixin:
    """Sets up the Restaurant accounting FKs and payment GL mappings.

    Call ``cls._setup_accounting()`` at the end of ``setUpTestData`` after the
    Restaurant, payment modes, and warehouses exist.
    """

    @classmethod
    def _setup_accounting(cls):
        cls.accounts = setup_chart_of_accounts(cls.restaurant)
        # Map every enabled payment mode to a leaf account: CASH → cash,
        # everything else → the bank account.
        for mode in ModeOfPayment.objects.all():
            account = cls.accounts["cash"] if mode.type == ModeOfPayment.TYPE_CASH else cls.accounts["bank"]
            PaymentGLMapping.objects.get_or_create(mode_of_payment=mode, defaults={"default_account": account})
        # Point the stock warehouses at their stock asset account: drink COGS
        # debits the expense account and credits this warehouse account.
        for warehouse in Warehouse.objects.all():
            if not warehouse.account_id:
                warehouse.account = cls.accounts["stock_in_hand"]
                warehouse.save(update_fields=["account", "updated_at"])
        cls.restaurant.default_income_account = cls.accounts["food_sales"]
        cls.restaurant.default_expense_account = cls.accounts["cogs"]
        cls.restaurant.round_off_account = cls.accounts["round_off"]
        cls.restaurant.account_for_change_amount = cls.accounts["cash"]
        cls.restaurant.wastage_account = cls.accounts["wastage"]
        cls.restaurant.cash_shortage_account = cls.accounts["cogs"]
        cls.restaurant.cash_over_short_account = cls.accounts["round_off"]
        cls.restaurant.save()
