"""Seed the app with realistic data for visual testing.

Creates the configuration chain: Restaurant settings → Warehouses → payment
modes → ProductionUnits, and sets up cross-references so the POS screen loads
immediately.

Usage:
    make manage ARGS='seed_pos_setup'
"""

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Seed the restaurant with realistic POS configuration for visual testing."

    @transaction.atomic
    def handle(self, *args, **options):
        from apps.accounting.management.commands.seed_chart_of_accounts import (
            Command as ChartSeed,
        )
        from apps.accounting.management.commands.seed_chart_of_accounts import (
            wire_production_unit_accounts,
        )
        from apps.inventory.models import Warehouse
        from apps.menu.models import Menu, MenuItem
        from apps.payments.models import ModeOfPayment
        from apps.settings.models import ProductionUnit, Restaurant

        # The chart of accounts must exist before payment GL mappings are FK'd.
        ChartSeed().handle()

        restaurant = Restaurant.load()
        if restaurant is None:
            restaurant = Restaurant.objects.create(company="Saki Restaurant")
            self.stdout.write(self.style.WARNING("Created restaurant settings: Saki Restaurant"))

        warehouses = {}
        for name in ["Bar", "Kitchen", "Store"]:
            warehouses[name], _ = Warehouse.objects.get_or_create(name=name)

        cash = ModeOfPayment.objects.filter(name="Cash").first()
        if cash is None:
            cash = ModeOfPayment.objects.create(name="Cash", type="CASH")
        electronic = ModeOfPayment.objects.filter(name="Electronic").first()
        if electronic is None:
            electronic = ModeOfPayment.objects.create(name="Electronic", type="BANK")
        if not ModeOfPayment.objects.filter(is_default=True).exists():
            cash.is_default = True
            cash.save(update_fields=["is_default", "updated_at"])

        changed = []
        if restaurant.default_warehouse_id != warehouses["Bar"].pk:
            restaurant.default_warehouse = warehouses["Bar"]
            changed.append("default_warehouse")
        if restaurant.store_warehouse_id != warehouses["Store"].pk:
            restaurant.store_warehouse = warehouses["Store"]
            changed.append("store_warehouse")
        if changed:
            restaurant.save(update_fields=changed + ["updated_at"])

        pu_kitchen, _ = ProductionUnit.objects.get_or_create(
            name="Kitchen",
            defaults={
                "department": "FOOD",
                "warehouse": warehouses["Kitchen"],
                "printer_ip": "192.168.1.50",
                "printer_paper_width": "WIDTH_80MM",
                "printer_cut_mode": "FULL_CUT",
            },
        )
        pu_bar, _ = ProductionUnit.objects.get_or_create(
            name="Bar",
            defaults={
                "department": "DRINKS",
                "warehouse": warehouses["Bar"],
                "printer_ip": "192.168.1.51",
                "printer_paper_width": "WIDTH_80MM",
                "printer_cut_mode": "FULL_CUT",
            },
        )
        wire_production_unit_accounts()
        self.stdout.write(self.style.SUCCESS("Production Units: Kitchen (FOOD), Bar (DRINKS)"))

        menu = Menu.objects.filter(enabled=True).first()
        if menu and restaurant.active_menu_id != menu.pk:
            restaurant.active_menu = menu
            restaurant.save(update_fields=["active_menu", "updated_at"])
            self.stdout.write(f"Active menu set: {menu.name}")

        menu_count = MenuItem.objects.filter(menu=restaurant.active_menu).count() if restaurant.active_menu_id else 0
        if menu_count == 0:
            from apps.menu.management.commands.seed_menu_catalog import Command as MenuSeed

            MenuSeed().handle(force=True)
            menu = Menu.objects.filter(enabled=True).first()
            if menu:
                restaurant.active_menu = menu
                restaurant.save(update_fields=["active_menu", "updated_at"])
            menu_count = (
                MenuItem.objects.filter(menu=restaurant.active_menu).count() if restaurant.active_menu_id else 0
            )
        else:
            self.stdout.write(f"Menu already has {menu_count} items — skipping seed.")

        self.stdout.write(self.style.SUCCESS("── POS setup complete ──"))
        self.stdout.write(f"  Restaurant: {restaurant.company}")
        self.stdout.write(f"  Store warehouse: {restaurant.store_warehouse}")
        self.stdout.write(f"  Bar / POS warehouse: {restaurant.default_warehouse}")
        self.stdout.write(f"  Payment Methods: {', '.join(str(m) for m in ModeOfPayment.objects.filter(enabled=True))}")
        self.stdout.write(f"  Production Units: Kitchen={pu_kitchen.department}, Bar={pu_bar.department}")
        self.stdout.write(f"  Menu items: {menu_count}")
        self.stdout.write("  Note: every enabled payment method appears on the POS — disable any you don't accept.")
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Open http://localhost:8000/pos/ to test the POS screen"))
