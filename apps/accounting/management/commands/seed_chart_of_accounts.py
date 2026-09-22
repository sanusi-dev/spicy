"""Seed the chart of accounts for visual testing and go-live.

Idempotent. Creates the account tree, the current-year fiscal year, and wires
the Restaurant accounting FKs, warehouse accounts, production-unit income
accounts, and payment GL mappings.

Usage:
    make manage ARGS='seed_chart_of_accounts'
"""

from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction


def wire_production_unit_accounts():
    """Wire each unit to its department's income, sales-returns, and expense accounts."""
    from apps.accounting.models import LedgerAccount
    from apps.settings.models import ProductionUnit

    by_name = {account.name: account for account in LedgerAccount.objects.filter(is_group=False)}
    for unit in ProductionUnit.objects.all():
        if unit.department == ProductionUnit.FOOD:
            income, returns, expense = "Food Sales", "Food Sales Returns", "Food COGS"
        elif unit.department == ProductionUnit.DRINKS:
            income, returns, expense = "Drinks Sales", "Drinks Sales Returns", None
        else:
            continue
        changed = []
        if not unit.income_account_id and by_name.get(income):
            unit.income_account = by_name[income]
            changed.append("income_account")
        if not unit.sales_returns_account_id and by_name.get(returns):
            unit.sales_returns_account = by_name[returns]
            changed.append("sales_returns_account")
        if expense and not unit.expense_account_id and by_name.get(expense):
            unit.expense_account = by_name[expense]
            changed.append("expense_account")
        if changed:
            unit.save(update_fields=[*changed, "updated_at"])


class Command(BaseCommand):
    help = "Seed the chart of accounts, fiscal year, and GL wiring."

    @transaction.atomic
    def handle(self, *args, **options):
        from apps.accounting.models import FiscalYear, LedgerAccount
        from apps.inventory.models import Warehouse
        from apps.payments.models import ModeOfPayment, PaymentGLMapping
        from apps.settings.models import Restaurant

        assets = LedgerAccount.objects.get_or_create(
            name="Assets",
            defaults={
                "is_group": True,
                "account_type": LedgerAccount.ASSET,
                "report_type": LedgerAccount.BALANCE_SHEET,
            },
        )[0]
        bank_group = LedgerAccount.objects.get_or_create(
            name="Bank Accounts",
            defaults={
                "parent": assets,
                "is_group": True,
                "account_type": LedgerAccount.ASSET,
                "report_type": LedgerAccount.BALANCE_SHEET,
            },
        )[0]
        cash_account = LedgerAccount.objects.get_or_create(
            name="Cash Account",
            defaults={
                "parent": assets,
                "is_group": False,
                "account_type": LedgerAccount.ASSET,
                "report_type": LedgerAccount.BALANCE_SHEET,
            },
        )[0]
        electronic_account = LedgerAccount.objects.get_or_create(
            name="Electronic Account",
            defaults={
                "parent": bank_group,
                "is_group": False,
                "account_type": LedgerAccount.ASSET,
                "report_type": LedgerAccount.BALANCE_SHEET,
            },
        )[0]

        income = LedgerAccount.objects.get_or_create(
            name="Income",
            defaults={
                "is_group": True,
                "account_type": LedgerAccount.INCOME,
                "report_type": LedgerAccount.PROFIT_AND_LOSS,
            },
        )[0]
        food_sales = LedgerAccount.objects.get_or_create(
            name="Food Sales",
            defaults={
                "parent": income,
                "is_group": False,
                "account_type": LedgerAccount.INCOME,
                "report_type": LedgerAccount.PROFIT_AND_LOSS,
            },
        )[0]
        drinks_sales = LedgerAccount.objects.get_or_create(
            name="Drinks Sales",
            defaults={
                "parent": income,
                "is_group": False,
                "account_type": LedgerAccount.INCOME,
                "report_type": LedgerAccount.PROFIT_AND_LOSS,
            },
        )[0]
        food_sales_returns = LedgerAccount.objects.get_or_create(
            name="Food Sales Returns",
            defaults={
                "parent": income,
                "is_group": False,
                "account_type": LedgerAccount.INCOME,
                "report_type": LedgerAccount.PROFIT_AND_LOSS,
            },
        )[0]
        drinks_sales_returns = LedgerAccount.objects.get_or_create(
            name="Drinks Sales Returns",
            defaults={
                "parent": income,
                "is_group": False,
                "account_type": LedgerAccount.INCOME,
                "report_type": LedgerAccount.PROFIT_AND_LOSS,
            },
        )[0]

        expenses = LedgerAccount.objects.get_or_create(
            name="Expenses",
            defaults={
                "is_group": True,
                "account_type": LedgerAccount.EXPENSE,
                "report_type": LedgerAccount.PROFIT_AND_LOSS,
            },
        )[0]
        cogs = LedgerAccount.objects.get_or_create(
            name="Cost of Goods Sold",
            defaults={
                "parent": expenses,
                "is_group": False,
                "account_type": LedgerAccount.EXPENSE,
                "report_type": LedgerAccount.PROFIT_AND_LOSS,
            },
        )[0]
        round_off = LedgerAccount.objects.get_or_create(
            name="Round Off",
            defaults={
                "parent": expenses,
                "is_group": False,
                "account_type": LedgerAccount.EXPENSE,
                "report_type": LedgerAccount.PROFIT_AND_LOSS,
            },
        )[0]
        food_cogs = LedgerAccount.objects.get_or_create(
            name="Food COGS",
            defaults={
                "parent": expenses,
                "is_group": False,
                "account_type": LedgerAccount.EXPENSE,
                "report_type": LedgerAccount.PROFIT_AND_LOSS,
            },
        )[0]
        wastage = LedgerAccount.objects.get_or_create(
            name="Wastage",
            defaults={
                "parent": expenses,
                "is_group": False,
                "account_type": LedgerAccount.EXPENSE,
                "report_type": LedgerAccount.PROFIT_AND_LOSS,
            },
        )[0]

        equity = LedgerAccount.objects.get_or_create(
            name="Equity",
            defaults={
                "is_group": True,
                "account_type": LedgerAccount.EQUITY,
                "report_type": LedgerAccount.BALANCE_SHEET,
            },
        )[0]
        LedgerAccount.objects.get_or_create(
            name="Owner's Equity",
            defaults={
                "parent": equity,
                "is_group": False,
                "account_type": LedgerAccount.EQUITY,
                "report_type": LedgerAccount.BALANCE_SHEET,
            },
        )

        today = date.today()
        fiscal_year = FiscalYear.objects.filter(disabled=False).first()
        if fiscal_year is None:
            fiscal_year = FiscalYear.objects.create(
                name=str(today.year),
                year_start_date=date(today.year, 1, 1),
                year_end_date=date(today.year, 12, 31),
            )

        # Wire production units, warehouses, and the Restaurant singleton.
        # Bar COGS keeps falling back to the default expense account (Cost of
        # Goods Sold); only the Kitchen gets a dedicated Food COGS leaf.
        wire_production_unit_accounts()

        # Inventory stock leaves per warehouse (credited at settle-time COGS).
        stock_group = LedgerAccount.objects.get_or_create(
            name="Inventory Stock",
            defaults={
                "parent": assets,
                "is_group": True,
                "account_type": LedgerAccount.ASSET,
                "report_type": LedgerAccount.BALANCE_SHEET,
            },
        )[0]
        for warehouse in Warehouse.objects.all():
            if warehouse.account_id:
                continue
            account, _ = LedgerAccount.objects.get_or_create(
                name=f"Stock in Hand — {warehouse.name}",
                defaults={
                    "parent": stock_group,
                    "is_group": False,
                    "account_type": LedgerAccount.ASSET,
                    "report_type": LedgerAccount.BALANCE_SHEET,
                },
            )
            warehouse.account = account
            warehouse.save(update_fields=["account", "updated_at"])

        # Supplier payables: a dedicated payable leaf under Liabilities plus a
        # stock-in-hand default under the Inventory Stock group.
        liabilities = LedgerAccount.objects.get_or_create(
            name="Liabilities",
            defaults={
                "is_group": True,
                "account_type": LedgerAccount.LIABILITY,
                "report_type": LedgerAccount.BALANCE_SHEET,
            },
        )[0]
        payable_account = LedgerAccount.objects.get_or_create(
            name="Accounts Payable",
            defaults={
                "parent": liabilities,
                "is_group": False,
                "account_type": LedgerAccount.LIABILITY,
                "report_type": LedgerAccount.BALANCE_SHEET,
            },
        )[0]
        grni_account = LedgerAccount.objects.get_or_create(
            name="Stock Received But Not Billed",
            defaults={
                "parent": liabilities,
                "is_group": False,
                "account_type": LedgerAccount.LIABILITY,
                "report_type": LedgerAccount.BALANCE_SHEET,
            },
        )[0]
        variance_account = LedgerAccount.objects.get_or_create(
            name="Inventory Price Variance",
            defaults={
                "parent": expenses,
                "is_group": False,
                "account_type": LedgerAccount.EXPENSE,
                "report_type": LedgerAccount.PROFIT_AND_LOSS,
            },
        )[0]
        stock_in_hand = LedgerAccount.objects.filter(
            parent=stock_group,
            is_group=False,
            account_type=LedgerAccount.ASSET,
        ).first()
        if stock_in_hand is None:
            stock_in_hand = LedgerAccount.objects.get_or_create(
                name="Stock in Hand",
                defaults={
                    "parent": stock_group,
                    "is_group": False,
                    "account_type": LedgerAccount.ASSET,
                    "report_type": LedgerAccount.BALANCE_SHEET,
                },
            )[0]
        supplier_expense_account = LedgerAccount.objects.get_or_create(
            name="Supplier Expenses",
            defaults={
                "parent": expenses,
                "is_group": False,
                "account_type": LedgerAccount.EXPENSE,
                "report_type": LedgerAccount.PROFIT_AND_LOSS,
            },
        )[0]
        stock_adjustment_account = LedgerAccount.objects.get_or_create(
            name="Stock Adjustments",
            defaults={
                "parent": expenses,
                "is_group": False,
                "account_type": LedgerAccount.EXPENSE,
                "report_type": LedgerAccount.PROFIT_AND_LOSS,
            },
        )[0]
        temporary_opening_account = LedgerAccount.objects.get_or_create(
            name="Temporary Opening",
            defaults={
                "parent": equity,
                "is_group": False,
                "account_type": LedgerAccount.EQUITY,
                "report_type": LedgerAccount.BALANCE_SHEET,
            },
        )[0]
        petty_cash_expense_account = LedgerAccount.objects.get_or_create(
            name="Petty Cash Expenses",
            defaults={
                "parent": expenses,
                "is_group": False,
                "account_type": LedgerAccount.EXPENSE,
                "report_type": LedgerAccount.PROFIT_AND_LOSS,
            },
        )[0]

        restaurant = Restaurant.load()
        if restaurant is not None:
            changed = []
            for field, value in [
                ("default_income_account", food_sales),
                ("default_expense_account", cogs),
                ("round_off_account", round_off),
                ("account_for_change_amount", cash_account),
                ("wastage_account", wastage),
                ("cash_shortage_account", cogs),
                ("cash_over_short_account", round_off),
                ("default_payable_account", payable_account),
                ("default_supplier_expense_account", supplier_expense_account),
                ("default_stock_in_hand_account", stock_in_hand),
                ("stock_received_but_not_billed_account", grni_account),
                ("inventory_price_variance_account", variance_account),
                ("stock_adjustment_account", stock_adjustment_account),
                ("temporary_opening_account", temporary_opening_account),
                ("petty_cash_expense_account", petty_cash_expense_account),
            ]:
                if getattr(restaurant, f"{field}_id") is None:
                    setattr(restaurant, field, value)
                    changed.append(field)
            if changed:
                restaurant.save(update_fields=changed + ["updated_at"])

        # Payment GL mappings now reference ledger accounts.
        for mode in ModeOfPayment.objects.all():
            account = cash_account if mode.type == ModeOfPayment.TYPE_CASH else electronic_account
            mapping, created = PaymentGLMapping.objects.get_or_create(
                mode_of_payment=mode,
                defaults={"default_account": account},
            )
            if not created and mapping.default_account_id is None:
                mapping.default_account = account
                mapping.save(update_fields=["default_account", "updated_at"])

        self.stdout.write(self.style.SUCCESS("── Chart of accounts seeded ──"))
        self.stdout.write(f"  Fiscal year: {fiscal_year.name}")
        self.stdout.write(f"  Cash account: {cash_account.name}")
        self.stdout.write(f"  Electronic account: {electronic_account.name}")
        self.stdout.write(f"  Income: {food_sales.name} / {drinks_sales.name}")
        self.stdout.write(f"  Sales returns: {food_sales_returns.name} / {drinks_sales_returns.name}")
        self.stdout.write(f"  GRNI: {grni_account.name}")
        self.stdout.write(f"  Supplier expenses: {supplier_expense_account.name}")
        self.stdout.write(f"  Variance: {variance_account.name}")
        self.stdout.write(f"  Food COGS: {food_cogs.name}")
        self.stdout.write(f"  Wastage: {wastage.name}")
        self.stdout.write(f"  Stock adjustments: {stock_adjustment_account.name}")
        self.stdout.write(f"  Temporary opening: {temporary_opening_account.name}")
