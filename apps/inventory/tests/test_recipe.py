from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.tests.helpers import setup_chart_of_accounts
from apps.inventory.models import (
    UOM,
    Bin,
    Item,
    ItemGroup,
    Recipe,
    RecipeItem,
    StockLedgerEntry,
    StockReconciliation,
    StockReconciliationItem,
    Warehouse,
)
from apps.inventory.services import compute_food_usage, recipe_plate_cost, submit_stock_reconciliation
from apps.menu.models import Menu, MenuItem
from apps.orders.models import Order, OrderItem
from apps.orders.services import add_order_line, settle_order
from apps.payments.models import ModeOfPayment, PaymentGLMapping
from apps.settings.models import ProductionUnit, Restaurant
from apps.staff.models import OpeningPayment, POSOpeningEntry
from apps.users.models import CustomUser


class RecipeWorld(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.kg, _ = UOM.objects.get_or_create(name="Kg")
        cls.plate, _ = UOM.objects.get_or_create(name="Plate")
        cls.group, _ = ItemGroup.objects.get_or_create(name="Food")
        cls.store = Warehouse.objects.create(name="Store")
        cls.kitchen = Warehouse.objects.create(name="Kitchen")
        cls.bar = Warehouse.objects.create(name="Bar")
        cls.restaurant = Restaurant.objects.create(
            company="Recipe Co", store_warehouse=cls.store, default_warehouse=cls.bar
        )
        cls.accounts = setup_chart_of_accounts(cls.restaurant)
        for wh in (cls.store, cls.kitchen, cls.bar):
            wh.account = cls.accounts["stock_in_hand"]
            wh.save(update_fields=["account", "updated_at"])
        ProductionUnit.objects.create(name="Kitchen", department="FOOD", warehouse=cls.kitchen)
        ProductionUnit.objects.create(name="Bar", department="DRINKS", warehouse=cls.bar)
        cls.rice = Item.objects.create(
            item_name="Raw Rice",
            item_group=cls.group,
            stock_uom=cls.kg,
            department="FOOD",
            is_stock_item=True,
            is_purchase_item=True,
            last_purchase_rate=Decimal("900"),
        )
        cls.oil = Item.objects.create(
            item_name="Palm Oil",
            item_group=cls.group,
            stock_uom=cls.kg,
            department="FOOD",
            is_stock_item=True,
            is_purchase_item=True,
            last_purchase_rate=Decimal("1800"),
        )
        cls.jollof = Item.objects.create(
            item_name="Jollof Rice",
            item_group=cls.group,
            stock_uom=cls.plate,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        cls.coke = Item.objects.create(
            item_name="Coke",
            item_group=cls.group,
            stock_uom=cls.kg,
            department="DRINKS",
            is_stock_item=True,
            is_sales_item=True,
            is_purchase_item=True,
        )
        cls.cash, _ = ModeOfPayment.objects.get_or_create(name="Cash", defaults={"type": "CASH"})
        PaymentGLMapping.objects.get_or_create(
            mode_of_payment=cls.cash, defaults={"default_account": cls.accounts["cash"]}
        )
        cls.user = CustomUser.objects.create_user(username="cashier", password="testpass123")
        cls.opening = POSOpeningEntry.objects.create(cashier=cls.user)
        OpeningPayment.objects.create(
            opening_entry=cls.opening, mode_of_payment=cls.cash, opening_amount=Decimal("50000")
        )
        cls.opening.submit()
        cls.menu = Menu.objects.create(name="Main")
        cls.jollof_mi = MenuItem.objects.create(menu=cls.menu, item=cls.jollof, rate=Decimal("1500"))
        cls.restaurant.active_menu = cls.menu
        cls.restaurant.save(update_fields=["active_menu", "updated_at"])

    def _order_with(self, item, qty, rate=Decimal("1500")):
        order = Order.objects.create(opening_entry=self.opening)
        add_order_line(order, item, qty=qty, rate=rate, menu_item=self.jollof_mi)
        order.recalculate_totals()
        settle_order(order, [{"mode_of_payment": self.cash.pk, "amount": order.rounded_total}], cashier=self.user)
        return Order.objects.get(pk=order.pk)

    def _stock_kitchen(self, item, qty, rate):
        Bin.objects.filter(item=item, warehouse=self.kitchen).update(actual_qty=0, valuation_rate=0)
        StockLedgerEntry.create_entry(
            item=item, warehouse=self.kitchen, quantity=qty, voucher_type="Receipt", voucher_no="R", unit_rate=rate
        )

    def _consume(self, item, counted):
        rec = StockReconciliation.objects.create(
            reason="CONSUMPTION", warehouse=self.kitchen, posting_date=date.today()
        )
        StockReconciliationItem.objects.create(reconciliation=rec, item=item, qty=counted)
        submit_stock_reconciliation(rec)
        return rec

    def _waste(self, item, qty):
        rec = StockReconciliation.objects.create(
            reason="WASTE_DAMAGE", warehouse=self.kitchen, posting_date=date.today()
        )
        StockReconciliationItem.objects.create(reconciliation=rec, item=item, qty=qty)
        submit_stock_reconciliation(rec)
        return rec

    def _recipe(self, dish=None, output="1", lines=None):
        recipe = Recipe.objects.create(item=dish or self.jollof, output_qty=Decimal(output))
        for ingredient, qty in lines or [(self.rice, "0.20")]:
            RecipeItem.objects.create(recipe=recipe, ingredient=ingredient, qty=Decimal(qty))
        return recipe


class RecipeValidationTest(RecipeWorld):
    def test_drink_parent_rejected(self):
        recipe = Recipe(item=self.coke, output_qty=Decimal("1"))
        with self.assertRaises(ValidationError):
            recipe.full_clean()

    def test_template_parent_rejected(self):
        parent = Item.objects.create(
            item_name="Chicken T",
            item_group=self.group,
            stock_uom=self.plate,
            department="FOOD",
            has_variants=True,
        )
        with self.assertRaises(ValidationError):
            Recipe(item=parent, output_qty=Decimal("1")).full_clean()

    def test_non_sellable_parent_rejected(self):
        with self.assertRaises(ValidationError):
            Recipe(item=self.rice, output_qty=Decimal("1")).full_clean()

    def test_second_active_recipe_rejected_until_deactivated(self):
        self._recipe()
        with self.assertRaises(ValidationError):
            Recipe(item=self.jollof, output_qty=Decimal("1")).full_clean()

    def test_inactive_recipe_allowed_alongside_active(self):
        self._recipe()
        other = Recipe(item=self.jollof, output_qty=Decimal("2"), is_active=False)
        other.full_clean()
        other.save()
        self.assertFalse(other.is_active)

    def test_output_qty_must_be_positive(self):
        with self.assertRaises(ValidationError):
            Recipe(item=self.jollof, output_qty=Decimal("0")).full_clean()

    def test_sellable_ingredient_rejected(self):
        recipe = self._recipe()
        with self.assertRaises(ValidationError):
            RecipeItem(recipe=recipe, ingredient=self.jollof, qty=Decimal("1")).full_clean()

    def test_drink_ingredient_rejected(self):
        recipe = self._recipe()
        with self.assertRaises(ValidationError):
            RecipeItem(recipe=recipe, ingredient=self.coke, qty=Decimal("1")).full_clean()

    def test_qty_must_be_positive(self):
        recipe = self._recipe()
        with self.assertRaises(ValidationError):
            RecipeItem(recipe=recipe, ingredient=self.oil, qty=Decimal("0")).full_clean()

    def test_duplicate_ingredient_rejected(self):
        self._recipe(lines=[(self.rice, "0.20")])
        recipe = Recipe.objects.get(item=self.jollof)
        with self.assertRaises(ValidationError):
            RecipeItem(recipe=recipe, ingredient=self.rice, qty=Decimal("0.10")).full_clean()


class RecipeItemGuardTest(RecipeWorld):
    def test_stock_uom_change_blocked_for_parent_and_ingredient(self):
        self._recipe(lines=[(self.rice, "0.20"), (self.oil, "0.03")])
        litre, _ = UOM.objects.get_or_create(name="Litre")
        self.rice.stock_uom = litre
        with self.assertRaisesMessage(ValidationError, "recipes use this item"):
            self.rice.full_clean()
        self.jollof.stock_uom = litre
        with self.assertRaisesMessage(ValidationError, "recipes use this item"):
            self.jollof.full_clean()

    def test_ingredient_cannot_go_sellable_or_non_stock(self):
        self._recipe()
        self.rice.is_sales_item = True
        with self.assertRaises(ValidationError):
            self.rice.full_clean()
        self.rice.is_sales_item = False
        self.rice.is_stock_item = False
        with self.assertRaises(ValidationError):
            self.rice.full_clean()


class ExplosionTest(RecipeWorld):
    def test_fifty_jollof_times_point_two_kg_is_ten_kg(self):
        self._recipe(lines=[(self.rice, "0.20")])
        self._order_with(self.jollof, 50)
        usage = compute_food_usage(date.today())
        rice = next(u for u in usage.usages if u.ingredient_id == self.rice.pk)
        self.assertEqual(rice.theoretical_qty, Decimal("10.00"))
        self.assertEqual(usage.theoretical_cost, rice.theoretical_amount)

    def test_variant_and_addon_recipes_are_independent(self):
        quarter = Item.objects.create(
            item_name="Quarter Chicken",
            item_group=self.group,
            stock_uom=self.plate,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        chicken = Item.objects.create(
            item_name="Raw Chicken",
            item_group=self.group,
            stock_uom=self.kg,
            department="FOOD",
            is_stock_item=True,
            is_purchase_item=True,
            last_purchase_rate=Decimal("3200"),
        )
        self._recipe(lines=[(self.rice, "0.20")])
        Recipe.objects.create(item=quarter, output_qty=Decimal("1"))
        qr = Recipe.objects.get(item=quarter)
        RecipeItem.objects.create(recipe=qr, ingredient=chicken, qty=Decimal("0.40"))
        self._order_with(self.jollof, 2)
        order = Order.objects.create(opening_entry=self.opening)
        add_order_line(order, quarter, qty=1, rate=Decimal("2000"))
        order.recalculate_totals()
        settle_order(order, [{"mode_of_payment": self.cash.pk, "amount": order.rounded_total}], cashier=self.user)
        usage = compute_food_usage(date.today())
        by_id = {u.ingredient_id: u for u in usage.usages}
        self.assertEqual(by_id[self.rice.pk].theoretical_qty, Decimal("0.40"))
        self.assertEqual(by_id[chicken.pk].theoretical_qty, Decimal("0.40"))

    def test_returns_net_qty(self):
        self._recipe(lines=[(self.rice, "0.20")])
        order = self._order_with(self.jollof, 10)
        source = order.items.get()
        ret = Order.objects.create(is_return=True, return_against=order)
        OrderItem.objects.create(
            order=ret,
            item=self.jollof,
            item_name=self.jollof.item_name,
            qty=Decimal("-4"),
            rate=Decimal("1500"),
            amount=Decimal("-6000"),
            department="FOOD",
            return_against_item=source,
        )
        Order.objects.filter(pk=ret.pk).update(status="SUBMITTED")
        usage = compute_food_usage(date.today())
        rice = next(u for u in usage.usages if u.ingredient_id == self.rice.pk)
        self.assertEqual(rice.theoretical_qty, Decimal("1.20"))

    def test_inactive_recipe_ignored_and_unmapped_listed(self):
        recipe = self._recipe()
        recipe.is_active = False
        recipe.save(update_fields=["is_active", "updated_at"])
        self._order_with(self.jollof, 3)
        usage = compute_food_usage(date.today())
        self.assertEqual(usage.usages, [])
        self.assertEqual(len(usage.unmapped), 1)
        self.assertEqual(usage.unmapped[0].item_name, "Jollof Rice")
        self.assertEqual(usage.unmapped[0].qty, Decimal("3.00"))


class ActualVsTheoreticalTest(RecipeWorld):
    def test_consumption_and_waste_split_with_actual_sle_rate(self):
        self._recipe(lines=[(self.rice, "0.20")])
        self._stock_kitchen(self.rice, Decimal("10"), Decimal("100"))
        self._order_with(self.jollof, 10)
        self._consume(self.rice, Decimal("6"))
        self._waste(self.rice, Decimal("1"))
        usage = compute_food_usage(date.today())
        rice = next(u for u in usage.usages if u.ingredient_id == self.rice.pk)
        self.assertEqual(rice.theoretical_qty, Decimal("2.00"))
        self.assertEqual(rice.consumption_qty, Decimal("4.00"))
        self.assertEqual(rice.waste_qty, Decimal("1.00"))
        self.assertEqual(rice.actual_qty, Decimal("5.00"))
        self.assertEqual(rice.rate, Decimal("100.00"))
        self.assertEqual(rice.actual_amount, Decimal("500.00"))
        self.assertEqual(rice.variance_qty, Decimal("-3.00"))

    def test_rate_falls_back_to_bin_wac_then_last_rate(self):
        self._recipe(lines=[(self.rice, "0.20")])
        self._order_with(self.jollof, 1)
        usage = compute_food_usage(date.today())
        rice = next(u for u in usage.usages if u.ingredient_id == self.rice.pk)
        self.assertEqual(rice.rate, Decimal("900"))

    def test_missing_rate_flagged_zero(self):
        self.rice.last_purchase_rate = None
        self.rice.save(update_fields=["last_purchase_rate", "updated_at"])
        self._recipe(lines=[(self.rice, "0.20")])
        self._order_with(self.jollof, 1)
        usage = compute_food_usage(date.today())
        rice = next(u for u in usage.usages if u.ingredient_id == self.rice.pk)
        self.assertEqual(rice.rate, Decimal("0"))
        self.assertTrue(rice.rate_estimated)

    def test_plate_cost_uses_kitchen_wac(self):
        recipe = self._recipe(lines=[(self.rice, "0.20"), (self.oil, "0.03")])
        self._stock_kitchen(self.rice, Decimal("10"), Decimal("100"))
        cost = recipe_plate_cost(recipe)
        self.assertEqual(cost, Decimal("74.00"))

    def test_pos_food_does_not_deduct(self):
        self._recipe(lines=[(self.rice, "0.20")])
        self._stock_kitchen(self.rice, Decimal("10"), Decimal("100"))
        self._order_with(self.jollof, 5)
        self.assertEqual(Bin.objects.get(item=self.rice, warehouse=self.kitchen).actual_qty, Decimal("10"))
