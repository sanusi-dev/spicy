from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.inventory.models import UOM, Item, ItemGroup
from apps.menu.models import ItemAddOn, ItemVariant, Menu, MenuItem
from apps.settings.models import Restaurant
from apps.users.models import CustomUser


class MenuViewTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = CustomUser.objects.create_user(
            username="admin@test.com", password="testpass123", email="admin@test.com"
        )
        mgr, _ = Group.objects.get_or_create(name="Spicy Manager")
        cls.user.groups.add(mgr)
        cls.uom = UOM.objects.create(name="Nos")
        cls.group = ItemGroup.objects.create(name="Food")
        cls.item_food = Item.objects.create(
            item_code="RICE001",
            item_name="Jollof Rice",
            item_group=cls.group,
            stock_uom=cls.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        cls.item_drink = Item.objects.create(
            item_code="DRINK001",
            item_name="Coke",
            item_group=cls.group,
            stock_uom=cls.uom,
            department="DRINKS",
            is_sales_item=True,
        )
        cls.menu = Menu.objects.create(name="Lunch Menu")
        cls.menu_item = MenuItem.objects.create(menu=cls.menu, item=cls.item_food, rate=Decimal("1500"))
        MenuItem.objects.create(menu=cls.menu, item=cls.item_drink, rate=Decimal("500"))

    def setUp(self):
        self.client.login(username="admin@test.com", password="testpass123")


class TestMenuViews(MenuViewTestBase):
    def test_menu_dashboard_shows_catalog_counts(self):
        response = self.client.get(reverse("menu:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["menu_count"], 1)
        self.assertEqual(response.context["menu_item_count"], 2)
        self.assertContains(response, "Menu building blocks")
        self.assertNotContains(response, "Catalog control")
        self.assertNotContains(response, "Live on POS")

    def test_menu_list_renders_enabled_status(self):
        response = self.client.get(reverse("menu:menu_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lunch Menu")
        self.assertContains(response, "Enabled")

    def test_menu_detail_marks_active_menu(self):
        Restaurant.objects.create(company="Spicy", active_menu=self.menu)
        response = self.client.get(reverse("menu:menu_detail", kwargs={"pk": self.menu.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Live on POS")

    def test_menu_detail_includes_inventory_context(self):
        response = self.client.get(reverse("menu:menu_detail", kwargs={"pk": self.menu.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.item_food.item_code)
        self.assertContains(response, self.group.name)

    def test_menu_item_list_filters_by_menu(self):
        response = self.client.get(reverse("menu:menu_item_list"), {"menu": self.menu.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["menu_items"].count(), 2)
        self.assertEqual(response.context["selected_menu"], str(self.menu.pk))

    def test_menu_create_post(self):
        response = self.client.post(
            reverse("menu:menu_create"),
            {"name": "Dinner Menu", "enabled": "on"},
        )
        self.assertRedirects(response, reverse("menu:menu_list"))
        menu = Menu.objects.get(name="Dinner Menu")
        self.assertEqual(menu.name, "Dinner Menu")

    def test_menu_update_post(self):
        response = self.client.post(
            reverse("menu:menu_update", kwargs={"pk": self.menu.pk}),
            {"name": "Updated Menu", "enabled": "on"},
        )
        self.assertRedirects(response, reverse("menu:menu_detail", kwargs={"pk": self.menu.pk}))
        self.menu.refresh_from_db()
        self.assertEqual(self.menu.name, "Updated Menu")


class TestMenuItemViews(MenuViewTestBase):
    def test_menu_item_create_post(self):
        item3 = Item.objects.create(
            item_code="CHICK001",
            item_name="Fried Chicken",
            item_group=self.group,
            stock_uom=self.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        response = self.client.post(
            reverse("menu:menu_item_create"),
            {
                "menu": self.menu.pk,
                "item": item3.pk,
                "item_name": "Fried Chicken",
                "rate": "2500",
                "special_dish": "",
                "disabled": "",
            },
        )
        self.assertRedirects(response, reverse("menu:menu_detail", kwargs={"pk": self.menu.pk}))
        self.assertTrue(MenuItem.objects.filter(item=item3).exists())

    def test_menu_item_update_post(self):
        response = self.client.post(
            reverse("menu:menu_item_update", kwargs={"pk": self.menu_item.pk}),
            {
                "menu": self.menu.pk,
                "item": self.item_food.pk,
                "item_name": "Jollof Rice",
                "rate": "1800",
                "special_dish": "on",
                "disabled": "",
            },
        )
        self.assertRedirects(response, reverse("menu:menu_detail", kwargs={"pk": self.menu.pk}))
        self.menu_item.refresh_from_db()
        self.assertEqual(self.menu_item.rate, Decimal("1800"))
        self.assertTrue(self.menu_item.special_dish)

    def test_menu_item_delete_post(self):
        pk = self.menu_item.pk
        response = self.client.post(reverse("menu:menu_item_delete", kwargs={"pk": pk}))
        self.assertRedirects(response, reverse("menu:menu_detail", kwargs={"pk": self.menu.pk}))
        self.assertFalse(MenuItem.objects.filter(pk=pk).exists())


class TestItemAddOnViews(MenuViewTestBase):
    def test_add_on_list_shows_active_menu_price(self):
        add_on_item = Item.objects.create(
            item_code="EXTRA-GET",
            item_name="Extra Sauce",
            item_group=self.group,
            stock_uom=self.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        MenuItem.objects.create(menu=self.menu, item=add_on_item, rate=Decimal("125"))
        ItemAddOn.objects.create(parent_item=self.item_food, add_on_item=add_on_item)
        Restaurant.objects.create(company="Spicy", active_menu=self.menu)
        response = self.client.get(reverse("menu:add_on_list"))
        self.assertEqual(response.status_code, 200)
        priced_add_on = response.context["add_ons"].get(add_on_item=add_on_item)
        self.assertEqual(priced_add_on.active_menu_rate, Decimal("125.00"))
        self.assertContains(response, "125.00")

    def test_add_on_list_marks_unpriced_relationship(self):
        add_on_item = Item.objects.create(
            item_code="EXTRA-UNPRICED",
            item_name="Unpriced Sauce",
            item_group=self.group,
            stock_uom=self.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        other_menu = Menu.objects.create(name="Dinner Menu")
        MenuItem.objects.create(menu=other_menu, item=add_on_item, rate=Decimal("125"))
        ItemAddOn.objects.create(parent_item=self.item_food, add_on_item=add_on_item)
        Restaurant.objects.create(company="Spicy", active_menu=self.menu)
        response = self.client.get(reverse("menu:add_on_list"))
        self.assertEqual(response.status_code, 200)
        unpriced_add_on = response.context["add_ons"].get(add_on_item=add_on_item)
        self.assertIsNone(unpriced_add_on.active_menu_rate)
        self.assertContains(response, "Not priced on active menu")

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.add_on_item = Item.objects.create(
            item_code="EXTRA001",
            item_name="Extra Sauce",
            item_group=cls.group,
            stock_uom=cls.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        MenuItem.objects.create(menu=cls.menu, item=cls.add_on_item, rate=Decimal("100"))
        cls.add_on = ItemAddOn.objects.create(parent_item=cls.item_food, add_on_item=cls.add_on_item)

    def test_add_on_create_post(self):
        item_new = Item.objects.create(
            item_code="NEW001",
            item_name="New Add-on Item",
            item_group=self.group,
            stock_uom=self.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        MenuItem.objects.create(menu=self.menu, item=item_new, rate=Decimal("50"))
        response = self.client.post(
            reverse("menu:add_on_create"),
            {"parent_item": self.item_food.pk, "add_on_item": item_new.pk},
        )
        self.assertRedirects(response, reverse("menu:add_on_list"))
        self.assertTrue(ItemAddOn.objects.filter(parent_item=self.item_food, add_on_item=item_new).exists())

    def test_add_on_update_post(self):
        item_new = Item.objects.create(
            item_code="NEW002",
            item_name="Another Add-on",
            item_group=self.group,
            stock_uom=self.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        MenuItem.objects.create(menu=self.menu, item=item_new, rate=Decimal("75"))
        response = self.client.post(
            reverse("menu:add_on_update", kwargs={"pk": self.add_on.pk}),
            {"parent_item": self.item_food.pk, "add_on_item": item_new.pk},
        )
        self.assertRedirects(response, reverse("menu:add_on_list"))
        self.add_on.refresh_from_db()
        self.assertEqual(self.add_on.add_on_item, item_new)

    def test_add_on_delete_post(self):
        pk = self.add_on.pk
        response = self.client.post(reverse("menu:add_on_delete", kwargs={"pk": pk}))
        self.assertRedirects(response, reverse("menu:add_on_list"))
        self.assertFalse(ItemAddOn.objects.filter(pk=pk).exists())


class TestItemVariantViews(MenuViewTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.variant_item = Item.objects.create(
            item_code="COKE-L",
            item_name="Large Coke",
            item_group=cls.group,
            stock_uom=cls.uom,
            department="DRINKS",
            is_sales_item=True,
        )
        MenuItem.objects.create(menu=cls.menu, item=cls.variant_item, rate=Decimal("700"))
        cls.variant = ItemVariant.objects.create(parent_item=cls.item_drink, variant_item=cls.variant_item)

    def test_variant_create_post(self):
        item_new = Item.objects.create(
            item_code="COKE-Z",
            item_name="Zero Coke",
            item_group=self.group,
            stock_uom=self.uom,
            department="DRINKS",
            is_sales_item=True,
        )
        MenuItem.objects.create(menu=self.menu, item=item_new, rate=Decimal("600"))
        response = self.client.post(
            reverse("menu:variant_create"),
            {"parent_item": self.item_drink.pk, "variant_item": item_new.pk},
        )
        self.assertRedirects(response, reverse("menu:variant_list"))
        self.assertTrue(ItemVariant.objects.filter(parent_item=self.item_drink, variant_item=item_new).exists())

    def test_variant_update_post(self):
        item_new = Item.objects.create(
            item_code="COKE-D",
            item_name="Diet Coke",
            item_group=self.group,
            stock_uom=self.uom,
            department="DRINKS",
            is_sales_item=True,
        )
        MenuItem.objects.create(menu=self.menu, item=item_new, rate=Decimal("650"))
        response = self.client.post(
            reverse("menu:variant_update", kwargs={"pk": self.variant.pk}),
            {"parent_item": self.item_drink.pk, "variant_item": item_new.pk},
        )
        self.assertRedirects(response, reverse("menu:variant_list"))
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.variant_item, item_new)

    def test_variant_delete_post(self):
        pk = self.variant.pk
        response = self.client.post(reverse("menu:variant_delete", kwargs={"pk": pk}))
        self.assertRedirects(response, reverse("menu:variant_list"))
        self.assertFalse(ItemVariant.objects.filter(pk=pk).exists())
