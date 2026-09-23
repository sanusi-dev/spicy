from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.inventory.models import UOM, Bin, Item, ItemGroup, Warehouse
from apps.menu.models import ItemAddOn, ItemVariant, Menu, MenuItem
from apps.payments.models import ModeOfPayment
from apps.settings.models import ProductionUnit, Restaurant
from apps.staff.models import OpeningPayment, POSOpeningEntry

from ..models import Order
from ..services import create_tickets, settle_order
from .accounting_setup import OrderAccountingMixin

CustomUser = get_user_model()


class VariantPickerTestBase(OrderAccountingMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.restaurant = Restaurant.objects.create(company="Test Co")
        cls.uom = UOM.objects.create(name="Nos")
        cls.group_food = ItemGroup.objects.create(name="Food")
        cls.group_drinks = ItemGroup.objects.create(name="Drinks")
        cls.warehouse = Warehouse.objects.create(name="Kitchen")
        cls.menu = Menu.objects.create(name="Main Menu")
        # Grouped food dish: Chicken template with Quarter / Half variants.
        cls.parent = Item.objects.create(
            item_name="Chicken",
            item_group=cls.group_food,
            stock_uom=cls.uom,
            department="FOOD",
            has_variants=True,
            is_sales_item=False,
            is_stock_item=False,
            is_purchase_item=False,
        )
        cls.quarter = Item.objects.create(
            item_name="Quarter Chicken",
            item_group=cls.group_food,
            stock_uom=cls.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        cls.half = Item.objects.create(
            item_name="Half Chicken",
            item_group=cls.group_food,
            stock_uom=cls.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        ItemVariant.objects.create(parent_item=cls.parent, variant_item=cls.quarter)
        ItemVariant.objects.create(parent_item=cls.parent, variant_item=cls.half)
        cls.quarter_menu_item = MenuItem.objects.create(menu=cls.menu, item=cls.quarter, rate=Decimal("1500"))
        cls.half_menu_item = MenuItem.objects.create(menu=cls.menu, item=cls.half, rate=Decimal("2500"))
        # Un-grouped control dish.
        cls.jollof = Item.objects.create(
            item_name="Jollof Rice",
            item_group=cls.group_food,
            stock_uom=cls.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        MenuItem.objects.create(menu=cls.menu, item=cls.jollof, rate=Decimal("1500"))
        # Grouped drinks: Star template with two bottle sizes.
        cls.beer_parent = Item.objects.create(
            item_name="Star",
            item_group=cls.group_drinks,
            stock_uom=cls.uom,
            department="DRINKS",
            has_variants=True,
            is_sales_item=False,
            is_stock_item=False,
            is_purchase_item=False,
        )
        cls.small_beer = Item.objects.create(
            item_name="Small Star",
            item_group=cls.group_drinks,
            stock_uom=cls.uom,
            department="DRINKS",
            is_sales_item=True,
            is_stock_item=True,
            is_purchase_item=True,
        )
        cls.big_beer = Item.objects.create(
            item_name="Big Star",
            item_group=cls.group_drinks,
            stock_uom=cls.uom,
            department="DRINKS",
            is_sales_item=True,
            is_stock_item=True,
            is_purchase_item=True,
        )
        ItemVariant.objects.create(parent_item=cls.beer_parent, variant_item=cls.small_beer)
        ItemVariant.objects.create(parent_item=cls.beer_parent, variant_item=cls.big_beer)
        MenuItem.objects.create(menu=cls.menu, item=cls.small_beer, rate=Decimal("500"))
        MenuItem.objects.create(menu=cls.menu, item=cls.big_beer, rate=Decimal("800"))
        Bin.objects.create(item=cls.small_beer, warehouse=cls.warehouse, actual_qty=Decimal("10"))
        Bin.objects.create(item=cls.big_beer, warehouse=cls.warehouse, actual_qty=Decimal("10"))
        cls.cash, _ = ModeOfPayment.objects.get_or_create(name="Cash", defaults={"type": "CASH"})
        cls.cash.is_default = True
        cls.cash.save(update_fields=["is_default"])
        cls.restaurant.active_menu = cls.menu
        cls.restaurant.default_warehouse = cls.warehouse
        cls.restaurant.save()
        cls._setup_accounting()
        ProductionUnit.objects.create(name="Kitchen", warehouse=cls.warehouse, department="FOOD")
        ProductionUnit.objects.create(name="Bar", warehouse=cls.warehouse, department="DRINKS")
        cls.user = CustomUser.objects.create_user(username="cashier", password="testpass123")
        cls.user.groups.add(Group.objects.get(name="Spicy Cashier"))

    def setUp(self):
        self.client.force_login(self.user)
        self.opening = POSOpeningEntry.objects.create(cashier=self.user)
        OpeningPayment.objects.create(
            opening_entry=self.opening, mode_of_payment=self.cash, opening_amount=Decimal("50000")
        )
        self.opening.submit()
        self.client.post(reverse("pos:pos_order_new"), {"order_type": "DINE_IN", "guest_count": "2"})
        self.order = Order.objects.first()
        self.order_url = reverse("pos:pos_order_screen", kwargs={"pk": self.order.pk})
        self.add_url = reverse("pos:pos_order_add_item", kwargs={"pk": self.order.pk})
        self.dialog_url = reverse(
            "pos:pos_order_variant_dialog", kwargs={"pk": self.order.pk, "parent_item_id": self.parent.pk}
        )


class VariantCatalogTest(VariantPickerTestBase):
    def test_parent_card_groups_variants_with_price_range(self):
        response = self.client.get(self.order_url)
        self.assertContains(response, "Chicken")
        self.assertContains(response, "₦1,500 – ₦2,500")
        self.assertContains(response, "2 sizes")
        self.assertNotContains(response, "Quarter Chicken")
        self.assertNotContains(response, "Half Chicken")

    def test_search_by_variant_name_surfaces_parent(self):
        response = self.client.get(self.order_url, {"q": "Quarter"})
        self.assertContains(response, "Chicken")
        self.assertNotContains(response, "Jollof Rice")


class VariantDialogTest(VariantPickerTestBase):
    def test_dialog_lists_variants_ordered_by_rate(self):
        response = self.client.get(self.dialog_url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertLess(content.index("Quarter Chicken"), content.index("Half Chicken"))
        self.assertContains(response, "₦1500")
        self.assertContains(response, "₦2500")

    def test_dialog_404_without_sellable_variants(self):
        self.quarter_menu_item.disabled = True
        self.quarter_menu_item.save(update_fields=["disabled"])
        self.half_menu_item.disabled = True
        self.half_menu_item.save(update_fields=["disabled"])
        response = self.client.get(self.dialog_url)
        self.assertEqual(response.status_code, 404)

    def test_out_of_stock_drink_variant_renders_disabled(self):
        Bin.objects.filter(item=self.small_beer, warehouse=self.warehouse).update(actual_qty=Decimal("0"))
        url = reverse(
            "pos:pos_order_variant_dialog", kwargs={"pk": self.order.pk, "parent_item_id": self.beer_parent.pk}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        small_input = next(
            line for line in content.splitlines() if f'value="{self.small_beer.pk}"' in line and "radio" in line
        )
        big_input = next(
            line for line in content.splitlines() if f'value="{self.big_beer.pk}"' in line and "radio" in line
        )
        self.assertIn("disabled", small_input)
        self.assertNotIn("disabled", big_input)

    def test_food_variants_always_selectable(self):
        response = self.client.get(self.dialog_url)
        content = response.content.decode()
        for variant in (self.quarter, self.half):
            line = next(line for line in content.splitlines() if f'value="{variant.pk}"' in line and "radio" in line)
            self.assertNotIn("disabled", line)

    def test_dialog_refused_after_send(self):
        from ..services import add_order_line

        add_order_line(self.order, self.jollof, qty=1, rate=Decimal("1500"))
        create_tickets(self.order, created_by=self.user)
        response = self.client.get(self.dialog_url)
        self.assertEqual(response.status_code, 404)


class VariantSubmitTest(VariantPickerTestBase):
    def test_submit_adds_chosen_variant_at_menu_rate(self):
        response = self.client.post(
            self.add_url,
            {"item_id": str(self.parent.pk), "variant_item_id": str(self.quarter.pk), "qty": "2"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        line = self.order.items.get()
        self.assertEqual(line.item, self.quarter)
        self.assertEqual(line.rate, Decimal("1500"))
        self.assertEqual(line.qty, Decimal("2"))
        self.assertEqual(line.customer_index, 1)

    def test_submit_uses_active_customer_card(self):
        self.client.post(
            reverse("pos:pos_customer_card_activate", kwargs={"pk": self.order.pk, "idx": 2}),
        )
        self.client.post(
            self.add_url,
            {"item_id": str(self.parent.pk), "variant_item_id": str(self.half.pk), "qty": "1"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(self.order.items.get().customer_index, 2)

    def test_template_as_line_rejected(self):
        response = self.client.post(self.add_url, {"item_id": str(self.parent.pk), "qty": "1"}, HTTP_HX_REQUEST="true")
        self.assertContains(response, "Choose a size.")
        self.assertEqual(self.order.items.count(), 0)

    def test_cross_parent_variant_rejected(self):
        other_parent = Item.objects.create(
            item_name="Fish",
            item_group=self.group_food,
            stock_uom=self.uom,
            department="FOOD",
            has_variants=True,
            is_sales_item=False,
            is_stock_item=False,
            is_purchase_item=False,
        )
        response = self.client.post(
            self.add_url,
            {"item_id": str(other_parent.pk), "variant_item_id": str(self.quarter.pk), "qty": "1"},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, "Choose a valid size.")
        self.assertEqual(self.order.items.count(), 0)

    def test_off_menu_variant_rejected(self):
        off_menu = Item.objects.create(
            item_name="Family Chicken",
            item_group=self.group_food,
            stock_uom=self.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        ItemVariant.objects.create(parent_item=self.parent, variant_item=off_menu)
        response = self.client.post(
            self.add_url,
            {"item_id": str(self.parent.pk), "variant_item_id": str(off_menu.pk), "qty": "1"},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, "not on the active menu")
        self.assertEqual(self.order.items.count(), 0)

    def test_disabled_variant_rejected(self):
        self.quarter_menu_item.disabled = True
        self.quarter_menu_item.save(update_fields=["disabled"])
        response = self.client.post(
            self.add_url,
            {"item_id": str(self.parent.pk), "variant_item_id": str(self.quarter.pk), "qty": "1"},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, "not on the active menu")
        self.assertEqual(self.order.items.count(), 0)

    def test_audit_carries_parent_and_variant(self):
        self.client.post(
            self.add_url,
            {"item_id": str(self.parent.pk), "variant_item_id": str(self.quarter.pk), "qty": "1"},
            HTTP_HX_REQUEST="true",
        )
        event = self.order.audit_events.filter(event_type="ITEM_ADDED").latest("pk")
        self.assertEqual(event.metadata["item_id"], self.quarter.pk)
        self.assertEqual(event.metadata["parent_item_id"], self.parent.pk)

    def test_submit_refused_after_send(self):
        from ..services import add_order_line

        add_order_line(self.order, self.jollof, qty=1, rate=Decimal("1500"))
        create_tickets(self.order, created_by=self.user)
        response = self.client.post(
            self.add_url,
            {"item_id": str(self.parent.pk), "variant_item_id": str(self.quarter.pk), "qty": "1"},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, "sent to the kitchen")
        self.assertEqual(self.order.items.count(), 1)


class VariantStockTest(VariantPickerTestBase):
    def test_drink_variant_reserves_correct_bin(self):
        self.client.post(
            self.add_url,
            {"item_id": str(self.beer_parent.pk), "variant_item_id": str(self.small_beer.pk), "qty": "2"},
        )
        self.assertEqual(Bin.objects.get(item=self.small_beer, warehouse=self.warehouse).reserved_qty, Decimal("2"))
        self.assertEqual(Bin.objects.get(item=self.big_beer, warehouse=self.warehouse).reserved_qty, Decimal("0"))

    def test_drink_variant_deducts_correct_bin_on_settle(self):
        self.client.post(
            self.add_url,
            {"item_id": str(self.beer_parent.pk), "variant_item_id": str(self.big_beer.pk), "qty": "3"},
        )
        settle_order(self.order, [{"mode_of_payment": self.cash.pk, "amount": "2400"}], cashier=self.user)
        self.assertEqual(Bin.objects.get(item=self.big_beer, warehouse=self.warehouse).actual_qty, Decimal("7"))
        self.assertEqual(Bin.objects.get(item=self.small_beer, warehouse=self.warehouse).actual_qty, Decimal("10"))

    def test_food_variant_reserves_nothing(self):
        self.client.post(
            self.add_url,
            {"item_id": str(self.parent.pk), "variant_item_id": str(self.quarter.pk), "qty": "2"},
        )
        self.assertFalse(Bin.objects.filter(item=self.quarter).exists())


class VariantAddOnChainTest(VariantPickerTestBase):
    def setUp(self):
        super().setUp()
        self.sauce = Item.objects.create(
            item_name="Extra Sauce",
            item_group=self.group_food,
            stock_uom=self.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        MenuItem.objects.create(menu=self.menu, item=self.sauce, rate=Decimal("300"))
        ItemAddOn.objects.create(parent_item=self.quarter, add_on_item=self.sauce)

    def test_variant_with_add_ons_chains_to_add_on_dialog(self):
        response = self.client.post(
            self.add_url,
            {"item_id": str(self.parent.pk), "variant_item_id": str(self.quarter.pk), "qty": "2"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Trigger"], "close-variant-dialog")
        self.assertContains(response, "Extra Sauce")
        self.assertEqual(self.order.items.count(), 0)

    def test_chained_add_on_submit_adds_variant_and_add_on(self):
        chained = self.client.post(
            self.add_url,
            {
                "item_id": str(self.parent.pk),
                "variant_item_id": str(self.quarter.pk),
                "qty": "2",
                "comments": "No pepper",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(chained["HX-Trigger"], "close-variant-dialog")
        response = self.client.post(
            self.add_url,
            {
                "item_id": str(self.quarter.pk),
                "qty": "2",
                "comments": "No pepper",
                "add_on_ids": [str(self.sauce.pk)],
                "variant_confirmed": "1",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response["HX-Trigger"], "close-add-on-dialog")
        lines = list(self.order.items.order_by("pk"))
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].item, self.quarter)
        self.assertEqual(lines[0].qty, Decimal("2"))
        self.assertEqual(lines[0].comments, "No pepper")
        self.assertEqual(lines[1].item, self.sauce)

    def test_add_ons_of_another_item_rejected(self):
        unrelated = Item.objects.create(
            item_name="Unrelated",
            item_group=self.group_food,
            stock_uom=self.uom,
            department="FOOD",
            is_sales_item=True,
        )
        response = self.client.post(
            self.add_url,
            {
                "item_id": str(self.parent.pk),
                "variant_item_id": str(self.quarter.pk),
                "qty": "1",
                "add_on_ids": [str(unrelated.pk)],
                "variant_confirmed": "1",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, "not available for this item")
        self.assertEqual(self.order.items.count(), 0)
