"""Seed the database with end-to-end test data.

Creates configuration, staff logins, supplier stock, and a week of closed
shifts with settled orders, then leaves today's shift open with live draft
orders on the POS.

Development only: the command refuses to run unless DEBUG is enabled.

Usage:
    make manage ARGS='seed_test_data'
    make manage ARGS='seed_test_data --days 14 --orders-per-day 8'
"""

import random
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

TWO_PLACES = Decimal("0.01")

# Drink name: (supplier cost, quantity transferred to the Bar).
DRINKS = {
    "Coke (35cl)": (Decimal("200"), 150),
    "Fanta (35cl)": (Decimal("200"), 150),
    "Sprite (35cl)": (Decimal("200"), 150),
    "Maltina": (Decimal("350"), 100),
    "Chapman": (Decimal("900"), 80),
    "Zobo": (Decimal("200"), 120),
    "Bottled Water (75cl)": (Decimal("120"), 200),
    "Sachet Water": (Decimal("20"), 300),
    "Star Lager": (Decimal("450"), 200),
    "Gulder": (Decimal("450"), 150),
    "Heineken": (Decimal("550"), 120),
    "Legend Stout": (Decimal("500"), 100),
}

CUSTOMER_NAMES = [
    "Walk-in Customer",
    "Chinedu Okafor",
    "Aisha Bello",
    "Funke Adeyemi",
    "Musa Ibrahim",
    "Ngozi Eze",
    "Tunde Bakare",
]

LINE_COMMENTS = ["", "", "", "", "Less pepper", "No onions", "Extra spicy", "Well done", "Without ice"]


class Command(BaseCommand):
    help = "Seed a realistic week of shifts, stock, and orders for testing."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7, help="Past days of closed-shift history to generate.")
        parser.add_argument("--orders-per-day", type=int, default=6, help="Settled orders per historical day.")
        parser.add_argument("--force", action="store_true", help="Run even when order data already exists.")

    def handle(self, *args, **options):
        from apps.orders.models import Order
        from apps.staff.models import POSOpeningEntry

        if not settings.DEBUG:
            raise CommandError("seed_test_data is a development tool. It only runs with DEBUG enabled.")

        if Order.objects.exists() and not options["force"]:
            raise CommandError("Order data already exists. Use --force to append test data on top of it.")

        self._ensure_config()
        cashier, manager = self._ensure_users()
        self._ensure_stock()
        if POSOpeningEntry.objects.exists():
            self.stdout.write(self.style.WARNING("Shifts already exist — skipping historical shift seeding."))
        else:
            self._seed_history(options["days"], options["orders_per_day"], cashier)
        self._seed_today(cashier)
        self._print_summary()

    def _ensure_config(self):
        from apps.menu.management.commands.seed_menu_catalog import Command as MenuSeed
        from apps.menu.models import Menu, MenuItem
        from apps.orders.management.commands.seed_pos_setup import Command as PosSetupSeed
        from apps.settings.models import Restaurant

        MenuSeed().handle(force=True)
        PosSetupSeed().handle()

        # seed_pos_setup links the first enabled menu, which can be an empty
        # leftover from earlier experiments — point the POS at a populated one.
        counts = {menu.pk: MenuItem.objects.filter(menu=menu).count() for menu in Menu.objects.filter(enabled=True)}
        for menu_pk, count in counts.items():
            if count == 0 and len(counts) > 1:
                Menu.objects.filter(pk=menu_pk).delete()
                self.stdout.write(f"Removed empty menu #{menu_pk}.")
        counts = {pk: n for pk, n in counts.items() if n > 0}
        if counts:
            best_pk = max(counts, key=counts.get)
            restaurant = Restaurant.load()
            if restaurant is not None and restaurant.active_menu_id != best_pk:
                restaurant.active_menu_id = best_pk
                restaurant.save(update_fields=["active_menu", "updated_at"])
                self.stdout.write(f"Active menu set to menu #{best_pk} ({counts[best_pk]} items).")

    def _ensure_users(self):
        from apps.users.models import CustomUser

        cashier, created = CustomUser.objects.get_or_create(
            username="cashier",
            defaults={"email": "cashier@spicy.test", "first_name": "Amina", "last_name": "Bello"},
        )
        if created:
            cashier.set_password("pos1234")
            cashier.save(update_fields=["password"])
        cashier.groups.add(Group.objects.get(name="Spicy Cashier"))

        manager, created = CustomUser.objects.get_or_create(
            username="manager",
            defaults={
                "email": "manager@spicy.test",
                "first_name": "Emeka",
                "last_name": "Nwosu",
                "is_staff": True,
            },
        )
        if created:
            manager.set_password("manager1234")
            manager.save(update_fields=["password"])
        manager.groups.add(Group.objects.get(name="Spicy Manager"))
        return cashier, manager

    def _ensure_stock(self):
        from apps.inventory import services as inventory_services
        from apps.inventory.models import Bin, Item, PurchaseReceipt, PurchaseReceiptItem, StockEntry, StockEntryDetail
        from apps.settings.models import Restaurant

        restaurant = Restaurant.load()
        if Bin.objects.filter(warehouse_id=restaurant.default_warehouse_id, actual_qty__gt=0).exists():
            self.stdout.write("Bar stock already exists — skipping inventory seed.")
            return

        supply_day = timezone.localdate() - timedelta(days=9)
        raw_items = list(
            Item.objects.filter(
                is_purchase_item=True,
                is_sales_item=False,
                disabled=False,
                has_variants=False,
                department="FOOD",
            )
        )
        drink_items = {
            item.item_name: item
            for item in Item.objects.filter(
                is_purchase_item=True,
                is_sales_item=True,
                department="DRINKS",
                disabled=False,
                has_variants=False,
            )
        }

        raw_receipt = PurchaseReceipt.objects.create(
            supplier_name="Mama Bisi Foods Ltd",
            posting_date=supply_day,
            warehouse=restaurant.store_warehouse,
            remarks="Seed supplier delivery — raw food and supplies.",
        )
        for item in raw_items:
            PurchaseReceiptItem.objects.create(
                purchase_receipt=raw_receipt,
                item=item,
                received_qty=Decimal("60"),
                rate=item.last_purchase_rate or Decimal("1000"),
            )
        inventory_services.submit_purchase_receipt(raw_receipt)

        drink_receipt = PurchaseReceipt.objects.create(
            supplier_name="Nigerian Breweries Depot",
            posting_date=supply_day,
            warehouse=restaurant.store_warehouse,
            remarks="Seed supplier delivery — drinks.",
        )
        for name, (cost, transfer_qty) in DRINKS.items():
            item = drink_items.get(name)
            if item is None:
                continue
            PurchaseReceiptItem.objects.create(
                purchase_receipt=drink_receipt,
                item=item,
                received_qty=Decimal(transfer_qty * 2),
                rate=cost,
            )
        inventory_services.submit_purchase_receipt(drink_receipt)

        transfer = StockEntry.objects.create(
            purpose="MATERIAL_TRANSFER",
            posting_date=supply_day,
            remarks="Seed transfer from Store to units.",
        )
        for name, (cost, transfer_qty) in DRINKS.items():
            item = drink_items.get(name)
            if item is None:
                continue
            StockEntryDetail.objects.create(stock_entry=transfer, item=item, qty=Decimal(transfer_qty), basic_rate=cost)
        for item in raw_items:
            StockEntryDetail.objects.create(
                stock_entry=transfer,
                item=item,
                qty=Decimal("25"),
                basic_rate=item.last_purchase_rate or Decimal("1000"),
            )
        inventory_services.submit_stock_entry(transfer)
        self.stdout.write(self.style.SUCCESS("Stocked the Store, Kitchen, and Bar via receipt + transfer."))

    def _menu_pools(self):
        from apps.menu.models import MenuItem
        from apps.settings.models import Restaurant

        restaurant = Restaurant.load()
        if restaurant is None or not restaurant.active_menu_id:
            raise CommandError("No active menu is configured — run seed_pos_setup first.")
        menu_items = list(
            MenuItem.objects.filter(menu_id=restaurant.active_menu_id, disabled=False).select_related("item")
        )
        food_items = [mi for mi in menu_items if mi.item.department == "FOOD"]
        drink_items = [mi for mi in menu_items if mi.item.department == "DRINKS"]
        if not food_items or not drink_items:
            raise CommandError("The active menu needs both food and drink items to seed orders.")
        return food_items, drink_items

    def _make_order(self, shift, cashier, food_items, drink_items, rng, guest_count=1, order_type=None):
        from apps.orders import services

        order_type = order_type or rng.choice(["DINE_IN", "DINE_IN", "DINE_IN", "TAKE_AWAY"])
        order = services.create_draft_order(shift, cashier, order_type=order_type, guest_count=guest_count)
        order.customer_name = rng.choice(CUSTOMER_NAMES)
        order.save(update_fields=["customer_name", "updated_at"])
        for i in range(rng.randint(1, 4)):
            if i == 0:
                pool = rng.choice([food_items, food_items, drink_items])
            else:
                pool = rng.choices([food_items, drink_items], weights=[60, 40], k=1)[0]
            mi = rng.choice(pool)
            services.add_order_line(
                order,
                item=mi.item,
                qty=rng.randint(1, 3),
                customer_index=1 if guest_count == 1 else rng.randint(1, guest_count),
                comments=rng.choice(LINE_COMMENTS),
                rate=mi.rate,
                menu_item=mi,
                item_name=mi.item_name,
            )
        return order

    def _pay_and_settle(self, order, shift, cashier, cash_mode, electronic_mode, rng, allow_electronic=True):
        from apps.orders import services

        net = sum((line.amount for line in order.items.all()), Decimal("0"))
        grand = net.quantize(Decimal("1"), rounding="ROUND_HALF_UP")
        kinds = ["cash", "cash", "cash_change"]
        if allow_electronic:
            kinds += ["electronic", "split"]
        kind = rng.choice(kinds)
        if kind in {"cash", "cash_change"}:
            extra = Decimal("500") if kind == "cash_change" else Decimal("0")
            rows = [{"mode_of_payment": cash_mode.pk, "amount": grand + extra}]
        elif kind == "electronic":
            rows = [
                {
                    "mode_of_payment": electronic_mode.pk,
                    "amount": grand,
                    "reference_no": f"SEED-{uuid4().hex[:8].upper()}",
                }
            ]
        else:
            electronic = (grand / 2).quantize(TWO_PLACES, rounding="ROUND_DOWN")
            rows = [
                {
                    "mode_of_payment": electronic_mode.pk,
                    "amount": electronic,
                    "reference_no": f"SEED-{uuid4().hex[:8].upper()}",
                },
                {"mode_of_payment": cash_mode.pk, "amount": grand - electronic},
            ]
        tickets = services.settle_order(order, rows, cashier=cashier, opening_entry=shift)
        services.dispatch_tickets(tickets)

    def _backdate_order(self, order, day, rng, *, settled=True):
        from apps.orders.models import Order

        midnight = timezone.make_aware(datetime(day.year, day.month, day.day))
        arrived = midnight + timedelta(hours=10, minutes=rng.randint(0, 720))
        fields = {
            "posting_date": day,
            "posting_time": arrived.time(),
            "arrived_time": arrived,
        }
        if settled:
            # Seed-only: bypass save() immutability so reports read naturally.
            fields["submitted_at"] = arrived + timedelta(minutes=rng.randint(10, 25))
        Order.objects.filter(pk=order.pk).update(**fields)

    def _close_shift(self, shift, cashier, day_start, day_end, day):
        from apps.staff import services as staff_services
        from apps.staff.models import POSClosingEntry, POSOpeningEntry

        closing = staff_services.ensure_closing_draft(shift, cashier)
        expected = {row["mode"].pk: row for row in staff_services.expected_closing_amounts(shift, day_start, day_end)}
        for cp in closing.closing_payments.all():
            cp.closing_amount = expected[cp.mode_of_payment_id]["expected_amount"]
            cp.save(update_fields=["closing_amount", "updated_at"])
        staff_services.submit_closing_entry(closing)
        # Seed-only backdating: the close belongs to the seeded day, not seed-run time.
        POSClosingEntry.objects.filter(pk=closing.pk).update(period_end_date=day_end, posting_date=day)
        POSOpeningEntry.objects.filter(pk=shift.pk).update(period_end_date=day_end)

    def _seed_history(self, days, orders_per_day, cashier):
        from apps.orders import services as orders_services
        from apps.payments.models import ModeOfPayment
        from apps.staff import services as staff_services
        from apps.staff.models import POSOpeningEntry

        cash_mode = ModeOfPayment.objects.get(name="Cash")
        electronic_mode = ModeOfPayment.objects.get(name="Electronic")
        food_items, drink_items = self._menu_pools()
        today = timezone.localdate()
        for offset in range(days, 0, -1):
            day = today - timedelta(days=offset)
            day_start = timezone.make_aware(datetime(day.year, day.month, day.day, 8))
            day_end = day_start + timedelta(hours=15)
            rng = random.Random(offset)
            shift = staff_services.open_shift(
                cashier,
                {cash_mode: Decimal("20000"), electronic_mode: Decimal("0")},
                remarks=f"Seed shift {day}",
            )
            # Seed-only backdating: place the shift on its day before orders are settled.
            POSOpeningEntry.objects.filter(pk=shift.pk).update(period_start_date=day_start, posting_date=day)
            shift.refresh_from_db()
            for _ in range(orders_per_day):
                order = self._make_order(shift, cashier, food_items, drink_items, rng)
                self._pay_and_settle(order, shift, cashier, cash_mode, electronic_mode, rng)
                self._backdate_order(order, day, rng)
            if offset % 3 == 0:
                order = self._make_order(shift, cashier, food_items, drink_items, rng)
                orders_services.create_tickets(order, created_by=cashier)
                orders_services.cancel_sent_order(
                    order,
                    "wrong_order",
                    cancelled_by=cashier,
                    reason_note="Seed cancellation",
                )
                self._backdate_order(order, day, rng, settled=False)
            self._close_shift(shift, cashier, day_start, day_end, day)
            self.stdout.write(f"Seeded {day}: {orders_per_day} settled orders, shift closed.")

    def _seed_today(self, cashier):
        from apps.orders import services as orders_services
        from apps.payments.models import ModeOfPayment
        from apps.staff import services as staff_services
        from apps.staff.models import POSOpeningEntry

        cash_mode = ModeOfPayment.objects.get(name="Cash")
        electronic_mode = ModeOfPayment.objects.get(name="Electronic")
        open_shift = POSOpeningEntry.objects.filter(
            status=POSOpeningEntry.SUBMITTED,
            closing_entry__isnull=True,
        ).first()
        if open_shift is None:
            open_shift = staff_services.open_shift(
                cashier,
                {cash_mode: Decimal("20000"), electronic_mode: Decimal("0")},
                remarks="Seed open shift",
            )
            self.stdout.write(self.style.SUCCESS(f"Opened seed shift #{open_shift.pk}."))
        else:
            self.stdout.write(self.style.WARNING(f"Reusing existing open shift #{open_shift.pk}."))

        food_items, drink_items = self._menu_pools()
        rng = random.Random(20260815)
        mode_ids = set(open_shift.opening_payments.values_list("mode_of_payment_id", flat=True))
        allow_electronic = electronic_mode.pk in mode_ids

        for _ in range(5):
            order = self._make_order(open_shift, cashier, food_items, drink_items, rng)
            self._pay_and_settle(order, open_shift, cashier, cash_mode, electronic_mode, rng, allow_electronic)

        cancelled = self._make_order(open_shift, cashier, food_items, drink_items, rng)
        orders_services.create_tickets(cancelled, created_by=cashier)
        orders_services.cancel_sent_order(
            cancelled,
            "customer_changed_mind",
            cancelled_by=cashier,
            reason_note="Seed cancellation",
        )

        empty = orders_services.create_draft_order(open_shift, cashier)
        orders_services.discard_order(empty, discarded_by=cashier)

        sent = self._make_order(open_shift, cashier, food_items, drink_items, rng)
        orders_services.create_tickets(sent, created_by=cashier)

        grouped = orders_services.create_draft_order(open_shift, cashier, order_type="TAKE_AWAY", guest_count=2)
        for customer_index in (1, 2):
            for mi in rng.sample(food_items, 2):
                orders_services.add_order_line(
                    grouped,
                    item=mi.item,
                    qty=rng.randint(1, 2),
                    customer_index=customer_index,
                    comments="",
                    rate=mi.rate,
                    menu_item=mi,
                    item_name=mi.item_name,
                )
            mi = rng.choice(drink_items)
            orders_services.add_order_line(
                grouped,
                item=mi.item,
                qty=1,
                customer_index=customer_index,
                comments="",
                rate=mi.rate,
                menu_item=mi,
                item_name=mi.item_name,
            )

    def _print_summary(self):
        from apps.inventory.models import Bin
        from apps.orders.models import Order
        from apps.settings.models import Restaurant
        from apps.staff.models import POSOpeningEntry

        restaurant = Restaurant.load()
        orders = Order.objects.count()
        submitted = Order.objects.filter(status="SUBMITTED", is_return=False).count()
        drafts = Order.objects.filter(status="DRAFT", is_return=False).count()
        stock_rows = Bin.objects.filter(warehouse_id=restaurant.default_warehouse_id, actual_qty__gt=0).count()
        open_shift = POSOpeningEntry.objects.filter(
            status=POSOpeningEntry.SUBMITTED,
            closing_entry__isnull=True,
        ).exists()

        self.stdout.write(self.style.SUCCESS("── Seed complete ──"))
        self.stdout.write("  Logins: cashier / pos1234 | manager / manager1234")
        self.stdout.write(f"  Orders: {orders} total ({submitted} submitted, {drafts} open drafts)")
        self.stdout.write(f"  Bar stock: {stock_rows} stocked items in {restaurant.default_warehouse}")
        self.stdout.write(f"  Open shift: {'yes' if open_shift else 'no'}")
        self.stdout.write(self.style.WARNING("Open http://localhost:8000/pos/ and log in as cashier"))
