"""Kitchen food-usage report computations: theoretical recipe usage vs actual consumption."""

from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Q

from apps.inventory.models import Bin, Item, Recipe, StockLedgerEntry, StockReconciliation
from apps.utils.rounding import TWO_PLACES, money

from .models import PnLConfiguration
from .sources import ZERO, business_day_window, orders_in_window


@dataclass
class DishShare:
    dish_name: str
    qty: Decimal


@dataclass
class IngredientUsage:
    ingredient_id: int
    ingredient_name: str
    uom: str
    theoretical_qty: Decimal = Decimal("0")
    consumption_qty: Decimal = Decimal("0")
    waste_qty: Decimal = Decimal("0")
    actual_qty: Decimal = Decimal("0")
    variance_qty: Decimal = Decimal("0")
    rate: Decimal = Decimal("0")
    theoretical_amount: Decimal = Decimal("0")
    consumption_amount: Decimal = Decimal("0")
    waste_amount: Decimal = Decimal("0")
    actual_amount: Decimal = Decimal("0")
    variance_amount: Decimal = Decimal("0")
    rate_estimated: bool = False
    dishes: list = field(default_factory=list)


@dataclass
class UnmappedDish:
    item_name: str
    qty: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")


@dataclass
class FoodUsage:
    usages: list = field(default_factory=list)
    unmapped: list = field(default_factory=list)
    theoretical_cost: Decimal = Decimal("0")
    actual_cost: Decimal = Decimal("0")
    variance_cost: Decimal = Decimal("0")
    counted: bool = True


def _kitchen_warehouse():
    from apps.settings.models import ProductionUnit

    kitchen = ProductionUnit.objects.select_related("warehouse").filter(department=ProductionUnit.FOOD).first()
    return kitchen.warehouse if kitchen else None


def _ingredient_rate(ingredient, kitchen, actual_qty, actual_amount):
    """Weighted actual-SLE rate, else Kitchen bin WAC, else last purchase rate, else 0."""
    if actual_qty:
        return money(actual_amount / actual_qty)
    if kitchen is not None:
        bin_obj = Bin.objects.filter(item=ingredient, warehouse=kitchen).first()
        if bin_obj is not None and bin_obj.valuation_rate:
            return bin_obj.valuation_rate
    if ingredient.last_purchase_rate:
        return ingredient.last_purchase_rate
    return Decimal("0")


def recipe_plate_cost(recipe):
    """Display-only cost of one portion: sum(qty × Kitchen WAC or last rate) / output."""
    items = recipe.items.select_related("ingredient") if recipe.pk else []
    return _plate_cost(recipe.output_qty, items)


def _plate_cost(output_qty, rows):
    """Cost per output portion for (ingredient, qty) rows."""
    kitchen = _kitchen_warehouse()
    total = Decimal("0")
    for line in rows:
        ingredient = line.ingredient
        rate = None
        if kitchen is not None:
            bin_obj = Bin.objects.filter(item=ingredient, warehouse=kitchen).first()
            if bin_obj is not None and bin_obj.valuation_rate:
                rate = bin_obj.valuation_rate
        if rate is None:
            rate = ingredient.last_purchase_rate or Decimal("0")
        total += line.qty * rate
    if not output_qty:
        return Decimal("0")
    return money(total / output_qty)


def compute_food_usage(business_date):
    """Theoretical (recipe × sales) vs actual (kitchen SLEs) ingredient usage for a business date."""
    from apps.orders.models import OrderItem

    config = PnLConfiguration.load()
    start, end = business_day_window(business_date, config.business_day_start_hour)
    orders = orders_in_window(start, end)
    lines = (
        OrderItem.objects.filter(
            Q(department="FOOD") | Q(department__isnull=True, item__department="FOOD"),
            order_id__in=[o.pk for o in orders],
        )
        .select_related("item")
        .order_by("pk")
    )
    item_ids = {line.item_id for line in lines}
    recipes = {
        recipe.item_id: recipe
        for recipe in Recipe.objects.filter(is_active=True, item_id__in=item_ids).prefetch_related(
            "items__ingredient__stock_uom"
        )
    }
    theoretical = {}
    unmapped = {}

    def _usage(ingredient):
        usage = theoretical.get(ingredient.pk)
        if usage is None:
            usage = theoretical[ingredient.pk] = IngredientUsage(
                ingredient_id=ingredient.pk,
                ingredient_name=ingredient.item_name,
                uom=ingredient.stock_uom.name if ingredient.stock_uom_id else "",
            )
        return usage

    for line in lines:
        recipe = recipes.get(line.item_id)
        if recipe is None or not recipe.output_qty:
            dish = unmapped.setdefault(line.item_id, UnmappedDish(item_name=line.item_name or line.item.item_name))
            dish.qty += line.qty
            dish.amount += line.amount
            continue
        factor = line.qty / recipe.output_qty
        dish_name = line.item_name or line.item.item_name
        for recipe_item in recipe.items.all():
            qty_add = factor * recipe_item.qty
            usage = _usage(recipe_item.ingredient)
            usage.theoretical_qty += qty_add
            usage.dishes.append(DishShare(dish_name=dish_name, qty=qty_add))

    kitchen = _kitchen_warehouse()
    counted = False
    actual = {}
    if kitchen is not None:
        recs = list(
            StockReconciliation.objects.filter(
                status="SUBMITTED",
                reason__in=["CONSUMPTION", "WASTE_DAMAGE"],
                posting_date=business_date,
                warehouse=kitchen,
            )
        )
        counted = bool(recs)
        reason_by_no = {str(rec.pk): rec.reason for rec in recs}
        if reason_by_no:
            sles = (
                StockLedgerEntry.objects.filter(
                    voucher_type="Stock Reconciliation",
                    voucher_no__in=list(reason_by_no),
                    quantity__lt=0,
                )
                .select_related("item", "item__stock_uom")
                .order_by("pk")
            )
            for sle in sles:
                entry = actual.setdefault(
                    sle.item_id,
                    {
                        "ingredient": sle.item,
                        "consumption_qty": ZERO,
                        "waste_qty": ZERO,
                        "consumption_amount": ZERO,
                        "waste_amount": ZERO,
                        "amount": ZERO,
                    },
                )
                qty = abs(sle.quantity)
                amount = money(abs(sle.stock_value_change))
                if reason_by_no[sle.voucher_no] == "WASTE_DAMAGE":
                    entry["waste_qty"] += qty
                    entry["waste_amount"] += amount
                else:
                    entry["consumption_qty"] += qty
                    entry["consumption_amount"] += amount
                entry["amount"] += amount

    usages = []
    ingredients = {}
    for usage in theoretical.values():
        ingredients[usage.ingredient_id] = usage
    for item_id, entry in actual.items():
        if item_id not in ingredients:
            ingredient = entry["ingredient"]
            ingredients[item_id] = IngredientUsage(
                ingredient_id=item_id,
                ingredient_name=ingredient.item_name,
                uom=ingredient.stock_uom.name if ingredient.stock_uom_id else "",
            )
    for usage in ingredients.values():
        entry = actual.get(usage.ingredient_id, {})
        usage.consumption_qty = entry.get("consumption_qty", ZERO)
        usage.waste_qty = entry.get("waste_qty", ZERO)
        usage.actual_qty = (usage.consumption_qty + usage.waste_qty).quantize(TWO_PLACES)
        usage.theoretical_qty = usage.theoretical_qty.quantize(TWO_PLACES)
        usage.consumption_amount = money(entry.get("consumption_amount", ZERO))
        usage.waste_amount = money(entry.get("waste_amount", ZERO))
        usage.actual_amount = money(entry.get("amount", ZERO))
        ingredient = entry.get("ingredient")
        if ingredient is None:
            ingredient = Item.objects.select_related("stock_uom").get(pk=usage.ingredient_id)
        usage.rate = _ingredient_rate(ingredient, kitchen, usage.actual_qty, usage.actual_amount)
        usage.rate_estimated = usage.rate == 0
        usage.theoretical_amount = money(usage.theoretical_qty * usage.rate)
        usage.variance_qty = (usage.theoretical_qty - usage.actual_qty).quantize(TWO_PLACES)
        usage.variance_amount = money(usage.theoretical_amount - usage.actual_amount)
        usages.append(usage)
    usages.sort(key=lambda u: u.ingredient_name)
    unmapped_list = sorted(unmapped.values(), key=lambda d: d.item_name)
    for dish in unmapped_list:
        dish.qty = dish.qty.quantize(TWO_PLACES)
        dish.amount = money(dish.amount)
    return FoodUsage(
        usages=usages,
        unmapped=unmapped_list,
        theoretical_cost=money(sum((u.theoretical_amount for u in usages), ZERO)),
        actual_cost=money(sum((u.actual_amount for u in usages), ZERO)),
        variance_cost=money(sum((u.variance_amount for u in usages), ZERO)),
        counted=counted,
    )
