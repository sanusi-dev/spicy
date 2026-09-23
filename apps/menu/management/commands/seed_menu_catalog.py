"""Seed Nigerian restaurant items, the Main Menu, and example recipes.

Usage:
    make manage ARGS='seed_menu_catalog'
    make manage ARGS='seed_menu_catalog --force'
"""

from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.inventory.models import UOM, Item, ItemGroup, ItemUOMConversion
from apps.menu.models import ItemVariant, Menu, MenuItem
from apps.settings.models import Restaurant

# (item_name, group, uom, department, last_purchase_rate_or_None)
# Raw materials — inventory only, not sold on POS.
RAW_ITEMS: list[tuple[str, str, str, str, str | None]] = [
    ("Raw Rice (bag)", "Grains & Swallows", "Kg", "FOOD", "900"),
    ("Palm Oil", "Supplies", "Litre", "FOOD", "1800"),
    ("Vegetable Oil", "Supplies", "Litre", "FOOD", "1600"),
    ("Raw Whole Chicken", "Proteins", "Kg", "FOOD", "3200"),
    ("Beef (raw)", "Proteins", "Kg", "FOOD", "4500"),
    ("Goat Meat (raw)", "Proteins", "Kg", "FOOD", "5000"),
    ("Fish (raw)", "Proteins", "Kg", "FOOD", "2800"),
    ("Crayfish", "Supplies", "Kg", "FOOD", "6000"),
    ("Stockfish", "Supplies", "Kg", "FOOD", "8000"),
    ("Tomatoes (fresh)", "Supplies", "Kg", "FOOD", "800"),
    ("Onions", "Supplies", "Kg", "FOOD", "600"),
    ("Pepper (ata)", "Supplies", "Kg", "FOOD", "1500"),
    ("Garri (Ijebu)", "Grains & Swallows", "Kg", "FOOD", "700"),
    ("Yam Tuber", "Grains & Swallows", "Each", "FOOD", "1200"),
    ("Plantain (unripe)", "Supplies", "Kg", "FOOD", "900"),
]

# Finished goods sold as a single size — (name, group, uom, dept, sell_rate, special?)
SIMPLE_MENU_ITEMS: list[tuple[str, str, str, str, str, bool]] = [
    # Rice / mains
    ("Jollof Rice", "Grains & Swallows", "Plate", "FOOD", "1500", True),
    ("Fried Rice", "Grains & Swallows", "Plate", "FOOD", "1500", False),
    ("Coconut Rice", "Grains & Swallows", "Plate", "FOOD", "1800", False),
    ("Ofada Rice & Sauce", "Grains & Swallows", "Plate", "FOOD", "2000", False),
    # Soups (sold as plate portion — swallow ordered separately)
    ("Egusi Soup", "Soups & Stews", "Plate", "FOOD", "2500", True),
    ("Efo Riro", "Soups & Stews", "Plate", "FOOD", "2500", False),
    ("Okra Soup", "Soups & Stews", "Plate", "FOOD", "2000", False),
    ("Ogbono Soup", "Soups & Stews", "Plate", "FOOD", "2500", False),
    ("Afang Soup", "Soups & Stews", "Plate", "FOOD", "2800", False),
    ("Banga Soup", "Soups & Stews", "Plate", "FOOD", "2800", False),
    ("Pepper Soup (Goat)", "Soups & Stews", "Plate", "FOOD", "3000", False),
    # Swallows / sides
    ("Pounded Yam", "Grains & Swallows", "Plate", "FOOD", "800", False),
    ("Eba", "Grains & Swallows", "Plate", "FOOD", "500", False),
    ("Amala", "Grains & Swallows", "Plate", "FOOD", "500", False),
    ("Semo", "Grains & Swallows", "Plate", "FOOD", "500", False),
    ("Fried Plantain", "Sides", "Plate", "FOOD", "800", False),
    ("Moi Moi", "Sides", "Each", "FOOD", "700", False),
    ("Coleslaw", "Sides", "Plate", "FOOD", "500", False),
    ("Salad", "Sides", "Plate", "FOOD", "1000", False),
    # Proteins (single-size)
    ("Assorted Meat", "Proteins", "Plate", "FOOD", "2000", False),
    ("Fried Fish", "Proteins", "Each", "FOOD", "1800", False),
    ("Suya (portion)", "Proteins", "Plate", "FOOD", "2000", True),
    # Small chops
    ("Puff Puff (6 pcs)", "Small Chops & Snacks", "Pack", "FOOD", "500", False),
    ("Spring Rolls (4 pcs)", "Small Chops & Snacks", "Pack", "FOOD", "800", False),
    ("Samosa (4 pcs)", "Small Chops & Snacks", "Pack", "FOOD", "800", False),
    # Breakfast
    ("Akara & Pap", "Breakfast", "Plate", "FOOD", "1200", False),
    ("Yam & Egg Sauce", "Breakfast", "Plate", "FOOD", "1500", False),
    # Soft drinks / water / malt
    ("Coke (35cl)", "Soft Drinks", "Bottle", "DRINKS", "500", False),
    ("Fanta (35cl)", "Soft Drinks", "Bottle", "DRINKS", "500", False),
    ("Sprite (35cl)", "Soft Drinks", "Bottle", "DRINKS", "500", False),
    ("Maltina", "Juice & Malt", "Bottle", "DRINKS", "700", False),
    ("Chapman", "Juice & Malt", "Bottle", "DRINKS", "1500", False),
    ("Zobo", "Juice & Malt", "Bottle", "DRINKS", "500", False),
    ("Bottled Water (75cl)", "Water", "Bottle", "DRINKS", "300", False),
    ("Sachet Water", "Water", "Sachet", "DRINKS", "50", False),
    # Beer
    ("Star Lager", "Beer", "Bottle", "DRINKS", "800", False),
    ("Gulder", "Beer", "Bottle", "DRINKS", "800", False),
    ("Heineken", "Beer", "Bottle", "DRINKS", "1000", False),
    ("Legend Stout", "Beer", "Bottle", "DRINKS", "900", False),
]

# Variant families: parent display name + list of (variant_name, rate).
VARIANT_FAMILIES: list[dict] = [
    {
        "parent": "Grilled Chicken",
        "group": "Proteins",
        "department": "FOOD",
        "uom": "Plate",
        "special": True,
        "variants": [
            ("Quarter Chicken", "2000"),
            ("Half Chicken", "3500"),
            ("Full Chicken", "6500"),
        ],
    },
    {
        "parent": "Pepper Soup",
        "group": "Soups & Stews",
        "department": "FOOD",
        "uom": "Plate",
        "special": False,
        "variants": [
            ("Pepper Soup (Small)", "1500"),
            ("Pepper Soup (Large)", "2500"),
        ],
    },
    {
        "parent": "Party Jollof",
        "group": "Grains & Swallows",
        "department": "FOOD",
        "uom": "Pack",
        "special": False,
        "variants": [
            ("Party Jollof (Small pack)", "5000"),
            ("Party Jollof (Medium pack)", "10000"),
            ("Party Jollof (Large pack)", "18000"),
        ],
    },
]

# Dedicated upsell items (sellable + on menu) used only as add-ons where needed.
# (name, group, uom, dept, sell_rate)
EXTRA_ADDON_ITEMS: list[tuple[str, str, str, str, str]] = [
    ("Extra Meat", "Proteins", "Plate", "FOOD", "1500"),
    ("Extra Sauce", "Sides", "Each", "FOOD", "300"),
    ("Extra Yaji", "Sides", "Each", "FOOD", "200"),
]

# Per-bottle last purchase rate for sellable drinks (stock UOM = Bottle).
DRINK_BOTTLE_COST: dict[str, str] = {
    "Coke (35cl)": "200",
    "Fanta (35cl)": "200",
    "Sprite (35cl)": "200",
    "Maltina": "350",
    "Chapman": "400",
    "Zobo": "150",
    "Bottled Water (75cl)": "100",
    "Star Lager": "300",
    "Gulder": "300",
    "Heineken": "400",
    "Legend Stout": "350",
}

# (item_name, bulk_uom, factor) — ingredient conversions in stock UOM.
INGREDIENT_CONVERSIONS: list[tuple[str, str, str]] = [
    ("Raw Rice (bag)", "Bag", "50"),
    ("Palm Oil", "Paint Tin", "5"),
    ("Vegetable Oil", "Paint Tin", "5"),
]

# (dish_name, output_qty, [(ingredient_name, qty_in_stock_uom)]) — yield baked into qty.
EXAMPLE_RECIPES: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "Jollof Rice",
        "1",
        [
            ("Raw Rice (bag)", "0.125"),
            ("Palm Oil", "0.03"),
            ("Tomatoes (fresh)", "0.10"),
            ("Onions", "0.03"),
            ("Pepper (ata)", "0.02"),
            ("Crayfish", "0.01"),
        ],
    ),
    (
        "Egusi Soup",
        "1",
        [
            ("Palm Oil", "0.05"),
            ("Stockfish", "0.04"),
            ("Crayfish", "0.02"),
            ("Pepper (ata)", "0.02"),
            ("Onions", "0.03"),
        ],
    ),
    (
        "Quarter Chicken",
        "1",
        [
            ("Raw Whole Chicken", "0.40"),
            ("Pepper (ata)", "0.01"),
            ("Onions", "0.02"),
        ],
    ),
]

# (parent_item_name, add_on_item_name) — both must be sellable menu items.
ADD_ON_LINKS: list[tuple[str, str]] = [
    # Rice plates
    ("Jollof Rice", "Fried Plantain"),
    ("Jollof Rice", "Coleslaw"),
    ("Jollof Rice", "Assorted Meat"),
    ("Jollof Rice", "Coke (35cl)"),
    ("Fried Rice", "Fried Plantain"),
    ("Fried Rice", "Coleslaw"),
    ("Fried Rice", "Assorted Meat"),
    ("Fried Rice", "Coke (35cl)"),
    ("Coconut Rice", "Fried Plantain"),
    ("Coconut Rice", "Coleslaw"),
    ("Ofada Rice & Sauce", "Assorted Meat"),
    ("Ofada Rice & Sauce", "Fried Plantain"),
    # Soups + swallow / protein upsell
    ("Egusi Soup", "Pounded Yam"),
    ("Egusi Soup", "Eba"),
    ("Egusi Soup", "Amala"),
    ("Egusi Soup", "Extra Meat"),
    ("Efo Riro", "Pounded Yam"),
    ("Efo Riro", "Eba"),
    ("Efo Riro", "Extra Meat"),
    ("Okra Soup", "Eba"),
    ("Okra Soup", "Semo"),
    ("Ogbono Soup", "Eba"),
    ("Ogbono Soup", "Pounded Yam"),
    ("Afang Soup", "Pounded Yam"),
    ("Pepper Soup (Goat)", "Eba"),
    ("Pepper Soup (Goat)", "Pounded Yam"),
    ("Pepper Soup (Small)", "Eba"),
    ("Pepper Soup (Large)", "Eba"),
    ("Pepper Soup (Large)", "Extra Meat"),
    # Proteins
    ("Suya (portion)", "Extra Yaji"),
    ("Suya (portion)", "Coleslaw"),
    ("Suya (portion)", "Coke (35cl)"),
    ("Suya (portion)", "Bottled Water (75cl)"),
    ("Assorted Meat", "Extra Sauce"),
    ("Fried Fish", "Coleslaw"),
    ("Fried Fish", "Extra Sauce"),
    ("Quarter Chicken", "Coleslaw"),
    ("Quarter Chicken", "Fried Plantain"),
    ("Quarter Chicken", "Coke (35cl)"),
    ("Half Chicken", "Coleslaw"),
    ("Half Chicken", "Fried Plantain"),
    ("Half Chicken", "Coke (35cl)"),
    ("Full Chicken", "Coleslaw"),
    ("Full Chicken", "Fried Plantain"),
    ("Full Chicken", "Salad"),
    # Small chops
    ("Puff Puff (6 pcs)", "Coke (35cl)"),
    ("Puff Puff (6 pcs)", "Maltina"),
    ("Spring Rolls (4 pcs)", "Coke (35cl)"),
    ("Samosa (4 pcs)", "Coke (35cl)"),
    # Breakfast
    ("Akara & Pap", "Coke (35cl)"),
    ("Yam & Egg Sauce", "Bottled Water (75cl)"),
]


class Command(BaseCommand):
    help = "Seed typical Nigerian restaurant items (raw + finished) and the Main Menu."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Update menu rates and re-link variants even if they already exist.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        force = options["force"]

        self._ensure_groups_and_uoms()

        raw_count = self._seed_raw_items()
        simple_items = self._seed_simple_items(force=force)
        variant_data = self._seed_variant_families()
        self._seed_uom_conversions(simple_items, force=force)

        menu, _ = Menu.objects.get_or_create(name="Main Menu", defaults={"enabled": True})

        menu_count = self._seed_menu(menu, simple_items, variant_data, force=force)
        self._link_variants(variant_data, force=force)
        self._set_active_menu(menu)
        recipe_count = self._seed_recipes(force=force)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {raw_count} raw items, {len(simple_items)} simple finished items, "
                f"{len(variant_data)} variant families, {recipe_count} recipes; Main Menu has {menu_count} lines."
            )
        )

    def _ensure_groups_and_uoms(self):
        for name in {
            "Proteins",
            "Grains & Swallows",
            "Soups & Stews",
            "Sides",
            "Breakfast",
            "Small Chops & Snacks",
            "Soft Drinks",
            "Beer",
            "Spirits",
            "Wine",
            "Water",
            "Juice & Malt",
            "Supplies",
        }:
            ItemGroup.objects.get_or_create(name=name)
        for name in {
            "Each",
            "Kg",
            "Litre",
            "Plate",
            "Pack",
            "Bottle",
            "Can",
            "Carton",
            "Crate",
            "Sachet",
            "Bag",
            "Paint Tin",
        }:
            UOM.objects.get_or_create(name=name)

    def _get_group(self, name: str) -> ItemGroup:
        return ItemGroup.objects.get(name=name)

    def _get_uom(self, name: str) -> UOM:
        return UOM.objects.get(name=name)

    def _seed_raw_items(self) -> int:
        created = 0
        for name, group, uom, dept, rate in RAW_ITEMS:
            defaults = {
                "item_group": self._get_group(group),
                "stock_uom": self._get_uom(uom),
                "department": dept,
                "is_stock_item": True,
                "is_sales_item": False,
                "is_purchase_item": True,
                "description": "Raw / bulk ingredient — not sold on POS.",
                "last_purchase_rate": Decimal(rate) if rate else None,
            }
            item, was_created = Item.objects.get_or_create(item_name=name, defaults=defaults)
            if was_created:
                created += 1
            else:
                item.is_sales_item = False
                item.is_purchase_item = True
                item.is_stock_item = True
                if rate:
                    item.last_purchase_rate = Decimal(rate)
                item.save()
        return created

    def _seed_simple_items(self, force: bool = False) -> list[tuple[Item, Decimal, bool]]:
        # Bought-in drinks: sellable + purchasable. Kitchen dishes: sellable only.
        drink_groups = {"Soft Drinks", "Beer", "Spirits", "Wine", "Water", "Juice & Malt"}
        result = []
        for name, group, uom, dept, rate, special in SIMPLE_MENU_ITEMS:
            is_bought_in = group in drink_groups
            is_stock = is_bought_in
            if dept == "FOOD":
                # Food dishes are virtual (made in Kitchen, no stock ledger).
                is_stock = False
            defaults = {
                "item_group": self._get_group(group),
                "stock_uom": self._get_uom(uom),
                "department": dept,
                "is_stock_item": is_stock,
                "is_sales_item": True,
                "is_purchase_item": is_bought_in,
                "description": "Finished dish / drink sold on POS.",
            }
            item, _ = Item.objects.get_or_create(item_name=name, defaults=defaults)
            item.is_sales_item = True
            item.is_purchase_item = is_bought_in
            item.is_stock_item = is_stock
            bottle_cost = DRINK_BOTTLE_COST.get(name)
            if bottle_cost and (item.last_purchase_rate is None or force):
                item.last_purchase_rate = Decimal(bottle_cost)
            item.save()
            result.append((item, Decimal(rate), special))
        return result

    def _upsert_conversion(self, item: Item, uom_name: str, factor: Decimal, force: bool):
        uom = self._get_uom(uom_name)
        if uom.pk == item.stock_uom_id:
            return
        conv, created = ItemUOMConversion.objects.get_or_create(
            item=item,
            uom=uom,
            defaults={"conversion_factor": factor},
        )
        if not created and force and conv.conversion_factor != factor:
            conv.conversion_factor = factor
            conv.save(update_fields=["conversion_factor", "updated_at"])

    def _seed_uom_conversions(self, simple_items: list[tuple[Item, Decimal, bool]], force: bool):
        crate = "Crate"
        for item, _rate, _special in simple_items:
            if item.department != "DRINKS" or item.stock_uom.name != "Bottle":
                continue
            self._upsert_conversion(item, crate, Decimal("24"), force)
            bottle_cost = DRINK_BOTTLE_COST.get(item.item_name)
            if bottle_cost and (item.last_purchase_rate is None or force):
                item.last_purchase_rate = Decimal(bottle_cost)
                item.save(update_fields=["last_purchase_rate", "updated_at"])
        for name, uom_name, factor in INGREDIENT_CONVERSIONS:
            item = Item.objects.filter(item_name=name).first()
            if item is None:
                continue
            self._upsert_conversion(item, uom_name, Decimal(factor), force)

    def _seed_variant_families(self) -> list[dict]:
        """Create parent template items with POS-level size variants."""
        families = []
        for fam in VARIANT_FAMILIES:
            group = self._get_group(fam["group"])
            uom = self._get_uom(fam["uom"])
            parent, _ = Item.objects.get_or_create(
                item_name=fam["parent"],
                defaults={
                    "item_group": group,
                    "stock_uom": uom,
                    "department": fam["department"],
                    "is_stock_item": False,
                    "is_sales_item": False,
                    "is_purchase_item": False,
                    "has_variants": True,
                    "description": f"Template — sizes sold as separate variants ({fam['parent']}).",
                },
            )
            parent.has_variants = True
            parent.is_stock_item = False
            parent.is_sales_item = False
            parent.is_purchase_item = False
            parent.save()

            variants = []
            for vname, rate in fam["variants"]:
                is_stock_variant = fam["department"] != "FOOD"
                vitem, _ = Item.objects.get_or_create(
                    item_name=vname,
                    defaults={
                        "item_group": group,
                        "stock_uom": uom,
                        "department": fam["department"],
                        "is_stock_item": is_stock_variant,
                        "is_sales_item": True,
                        "is_purchase_item": False,
                        "has_variants": False,
                        "variant_of": parent,
                        "description": f"Size variant of {fam['parent']}.",
                    },
                )
                vitem.variant_of = parent
                vitem.is_sales_item = True
                vitem.is_purchase_item = False
                vitem.is_stock_item = is_stock_variant
                vitem.has_variants = False
                vitem.save()
                variants.append((vitem, Decimal(rate)))

            families.append({"parent": parent, "variants": variants, "special": fam["special"]})
        return families

    def _seed_menu(self, menu: Menu, simple_items, variant_data, force: bool) -> int:
        # Remove template lines left by earlier seeds.
        MenuItem.objects.filter(menu=menu, item__has_variants=True).delete()

        for item, rate, special in simple_items:
            self._upsert_menu_item(menu, item, rate, special, force)

        for fam in variant_data:
            for i, (vitem, rate) in enumerate(fam["variants"]):
                special = fam["special"] and i == 0
                self._upsert_menu_item(menu, vitem, rate, special, force)

        return MenuItem.objects.filter(menu=menu).count()

    def _upsert_menu_item(self, menu: Menu, item: Item, rate: Decimal, special: bool, force: bool):
        mi, created = MenuItem.objects.get_or_create(
            menu=menu,
            item=item,
            defaults={
                "item_name": item.item_name,
                "rate": rate,
                "special_dish": special,
                "disabled": False,
            },
        )
        if not created and force:
            mi.rate = rate
            mi.special_dish = special
            mi.item_name = item.item_name
            mi.disabled = False
            mi.save()

    def _link_variants(self, variant_data, force: bool):
        for fam in variant_data:
            parent = fam["parent"]
            for vitem, _rate in fam["variants"]:
                link, created = ItemVariant.objects.get_or_create(
                    parent_item=parent,
                    variant_item=vitem,
                )
                if created:
                    self.stdout.write(f"  Linked variant: {parent.item_name} → {vitem.item_name}")
                elif force:
                    self.stdout.write(f"  Variant already linked: {parent.item_name} → {vitem.item_name}")

    def _set_active_menu(self, menu: Menu):
        restaurant = Restaurant.objects.order_by("pk").first()
        if restaurant is None:
            return
        if restaurant.active_menu_id != menu.pk:
            restaurant.active_menu = menu
            restaurant.save(update_fields=["active_menu", "updated_at"])
            self.stdout.write(self.style.SUCCESS(f"Set Restaurant.active_menu → {menu.name}"))

    def _seed_recipes(self, force: bool = False) -> int:
        from apps.inventory.models import Recipe, RecipeItem

        count = 0
        for dish_name, output_qty, lines in EXAMPLE_RECIPES:
            dish = Item.objects.filter(item_name=dish_name).first()
            if dish is None:
                continue
            recipe, created = Recipe.objects.get_or_create(
                item=dish,
                is_active=True,
                defaults={"output_qty": Decimal(output_qty)},
            )
            if not created and force and recipe.output_qty != Decimal(output_qty):
                recipe.output_qty = Decimal(output_qty)
                recipe.save(update_fields=["output_qty", "updated_at"])
            count += 1 if created else 0
            for ingredient_name, qty in lines:
                ingredient = Item.objects.filter(item_name=ingredient_name).first()
                if ingredient is None:
                    continue
                row, row_created = RecipeItem.objects.get_or_create(
                    recipe=recipe,
                    ingredient=ingredient,
                    defaults={"qty": Decimal(qty)},
                )
                if not row_created and force and row.qty != Decimal(qty):
                    row.qty = Decimal(qty)
                    row.save(update_fields=["qty", "updated_at"])
        return count
