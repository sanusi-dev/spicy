"""Shared test helpers — chart-of-accounts setup used across app test suites."""

from datetime import date

from apps.accounting.models import FiscalYear, LedgerAccount
from apps.payments.models import PaymentGLMapping


def setup_chart_of_accounts(restaurant):
    """Create the baseline chart of accounts and wire the Restaurant singleton.

    Returns a dict of the created ledger accounts for assertions.
    """
    assets = LedgerAccount.objects.create(
        name=f"Assets {restaurant.pk}",
        is_group=True,
        account_type=LedgerAccount.ASSET,
        report_type=LedgerAccount.BALANCE_SHEET,
    )
    liabilities = LedgerAccount.objects.create(
        name=f"Liabilities {restaurant.pk}",
        is_group=True,
        account_type=LedgerAccount.LIABILITY,
        report_type=LedgerAccount.BALANCE_SHEET,
    )
    payable = LedgerAccount.objects.create(
        name=f"Accounts Payable {restaurant.pk}",
        parent=liabilities,
        account_type=LedgerAccount.LIABILITY,
        report_type=LedgerAccount.BALANCE_SHEET,
    )
    bank_group = LedgerAccount.objects.create(
        name=f"Bank Accounts {restaurant.pk}",
        parent=assets,
        is_group=True,
        account_type=LedgerAccount.ASSET,
        report_type=LedgerAccount.BALANCE_SHEET,
    )
    cash = LedgerAccount.objects.create(
        name=f"Cash Account {restaurant.pk}",
        parent=assets,
        account_type=LedgerAccount.ASSET,
        report_type=LedgerAccount.BALANCE_SHEET,
    )
    bank = LedgerAccount.objects.create(
        name=f"Electronic Account {restaurant.pk}",
        parent=bank_group,
        account_type=LedgerAccount.ASSET,
        report_type=LedgerAccount.BALANCE_SHEET,
    )
    income = LedgerAccount.objects.create(
        name=f"Income {restaurant.pk}",
        is_group=True,
        account_type=LedgerAccount.INCOME,
        report_type=LedgerAccount.PROFIT_AND_LOSS,
    )
    food_sales = LedgerAccount.objects.create(
        name=f"Food Sales {restaurant.pk}",
        parent=income,
        account_type=LedgerAccount.INCOME,
        report_type=LedgerAccount.PROFIT_AND_LOSS,
    )
    drinks_sales = LedgerAccount.objects.create(
        name=f"Drinks Sales {restaurant.pk}",
        parent=income,
        account_type=LedgerAccount.INCOME,
        report_type=LedgerAccount.PROFIT_AND_LOSS,
    )
    food_sales_returns = LedgerAccount.objects.create(
        name=f"Food Sales Returns {restaurant.pk}",
        parent=income,
        account_type=LedgerAccount.INCOME,
        report_type=LedgerAccount.PROFIT_AND_LOSS,
    )
    drinks_sales_returns = LedgerAccount.objects.create(
        name=f"Drinks Sales Returns {restaurant.pk}",
        parent=income,
        account_type=LedgerAccount.INCOME,
        report_type=LedgerAccount.PROFIT_AND_LOSS,
    )
    expenses = LedgerAccount.objects.create(
        name=f"Expenses {restaurant.pk}",
        is_group=True,
        account_type=LedgerAccount.EXPENSE,
        report_type=LedgerAccount.PROFIT_AND_LOSS,
    )
    cogs = LedgerAccount.objects.create(
        name=f"Cost of Goods Sold {restaurant.pk}",
        parent=expenses,
        account_type=LedgerAccount.EXPENSE,
        report_type=LedgerAccount.PROFIT_AND_LOSS,
    )
    round_off = LedgerAccount.objects.create(
        name=f"Round Off {restaurant.pk}",
        parent=expenses,
        account_type=LedgerAccount.EXPENSE,
        report_type=LedgerAccount.PROFIT_AND_LOSS,
    )
    equity = LedgerAccount.objects.create(
        name=f"Equity {restaurant.pk}",
        is_group=True,
        account_type=LedgerAccount.EQUITY,
        report_type=LedgerAccount.BALANCE_SHEET,
    )
    owner_equity = LedgerAccount.objects.create(
        name=f"Owner's Equity {restaurant.pk}",
        parent=equity,
        account_type=LedgerAccount.EQUITY,
        report_type=LedgerAccount.BALANCE_SHEET,
    )
    stock_in_hand = LedgerAccount.objects.create(
        name=f"Stock in Hand {restaurant.pk}",
        parent=assets,
        account_type=LedgerAccount.ASSET,
        report_type=LedgerAccount.BALANCE_SHEET,
    )
    grni = LedgerAccount.objects.create(
        name=f"Stock Received But Not Billed {restaurant.pk}",
        parent=liabilities,
        account_type=LedgerAccount.LIABILITY,
        report_type=LedgerAccount.BALANCE_SHEET,
    )
    variance = LedgerAccount.objects.create(
        name=f"Inventory Price Variance {restaurant.pk}",
        parent=expenses,
        account_type=LedgerAccount.EXPENSE,
        report_type=LedgerAccount.PROFIT_AND_LOSS,
    )
    supplier_expense = LedgerAccount.objects.create(
        name=f"Supplier Expenses {restaurant.pk}",
        parent=expenses,
        account_type=LedgerAccount.EXPENSE,
        report_type=LedgerAccount.PROFIT_AND_LOSS,
    )
    stock_adjustment = LedgerAccount.objects.create(
        name=f"Stock Adjustments {restaurant.pk}",
        parent=expenses,
        account_type=LedgerAccount.EXPENSE,
        report_type=LedgerAccount.PROFIT_AND_LOSS,
    )
    wastage = LedgerAccount.objects.create(
        name=f"Wastage {restaurant.pk}",
        parent=expenses,
        account_type=LedgerAccount.EXPENSE,
        report_type=LedgerAccount.PROFIT_AND_LOSS,
    )
    temporary_opening = LedgerAccount.objects.create(
        name=f"Temporary Opening {restaurant.pk}",
        parent=equity,
        account_type=LedgerAccount.EQUITY,
        report_type=LedgerAccount.BALANCE_SHEET,
    )
    today = date.today()
    fiscal_year = FiscalYear.objects.create(
        name=f"FY{today.year}-{restaurant.pk}",
        year_start_date=date(today.year, 1, 1),
        year_end_date=date(today.year, 12, 31),
    )

    restaurant.default_income_account = food_sales
    restaurant.default_sales_returns_account = drinks_sales_returns
    restaurant.default_expense_account = cogs
    restaurant.round_off_account = round_off
    restaurant.account_for_change_amount = cash
    restaurant.wastage_account = wastage
    restaurant.cash_shortage_account = cogs
    restaurant.cash_over_short_account = round_off
    restaurant.default_payable_account = payable
    restaurant.default_supplier_expense_account = supplier_expense
    restaurant.stock_received_but_not_billed_account = grni
    restaurant.inventory_price_variance_account = variance
    restaurant.stock_adjustment_account = stock_adjustment
    restaurant.temporary_opening_account = temporary_opening
    restaurant.save()

    return {
        "assets": assets,
        "liabilities": liabilities,
        "payable": payable,
        "bank_group": bank_group,
        "cash": cash,
        "bank": bank,
        "income": income,
        "food_sales": food_sales,
        "drinks_sales": drinks_sales,
        "food_sales_returns": food_sales_returns,
        "drinks_sales_returns": drinks_sales_returns,
        "expenses": expenses,
        "cogs": cogs,
        "stock_in_hand": stock_in_hand,
        "grni": grni,
        "variance": variance,
        "wastage": wastage,
        "supplier_expense": supplier_expense,
        "round_off": round_off,
        "equity": equity,
        "owner_equity": owner_equity,
        "stock_adjustment": stock_adjustment,
        "temporary_opening": temporary_opening,
        "fiscal_year": fiscal_year,
    }


def map_payment_modes(accounts, cash_modes, bank_modes):
    """Point the given payment modes at cash/bank ledger accounts."""
    for mode in cash_modes:
        PaymentGLMapping.objects.get_or_create(
            mode_of_payment=mode,
            defaults={"default_account": accounts["cash"]},
        )
    for mode in bank_modes:
        PaymentGLMapping.objects.get_or_create(
            mode_of_payment=mode,
            defaults={"default_account": accounts["bank"]},
        )


def setup_closing_variance_accounts(restaurant, accounts):
    """Wire the variance accounts used by shift-close posting tests."""
    restaurant.cash_shortage_account = accounts["cogs"]
    restaurant.cash_over_short_account = accounts["round_off"]
    restaurant.save()


def _fiscal_year_for(date):
    return FiscalYear.get_for(date)
