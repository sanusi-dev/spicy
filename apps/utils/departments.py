"""Shared department rules and labels for FOOD/DRINKS products."""

FOOD = "FOOD"
DRINKS = "DRINKS"
DEPARTMENT_LABELS = {FOOD: "Food", DRINKS: "Drinks"}


def department_label(department):
    """Display label for a department; unknown sale-time snapshots read as Unassigned."""
    return DEPARTMENT_LABELS.get(department, "Unassigned")


def department_rule_breach(item):
    """First department rule a POS product breaks, or None.

    A menu drink is always a stock-tracked, sellable, purchasable purchase.
    A sellable food item is never stock-tracked or purchasable.
    """
    if item.department == DRINKS:
        if not (item.is_stock_item and item.is_sales_item and item.is_purchase_item):
            return f"{item.item_name} must be a stock-tracked, sellable, purchasable drink."
    elif item.department == FOOD and item.is_sales_item and (item.is_stock_item or item.is_purchase_item):
        return f"{item.item_name} is a sellable food item and must not be stock-tracked or purchasable."
    return None
