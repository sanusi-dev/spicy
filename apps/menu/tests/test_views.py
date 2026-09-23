from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.inventory.models import UOM, Item, ItemGroup
from apps.menu.models import ItemAddOn, Menu, MenuItem
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
