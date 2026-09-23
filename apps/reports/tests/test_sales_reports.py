"""Sales report queries: grouping, department split, return netting, exclusions."""

from datetime import date, time
from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.orders.models import (
    CANCEL_REASON_CASHIER_ERROR,
    CANCEL_REASON_WRONG_ORDER,
    CANCELLED,
    DINE_IN,
    DISCARDED,
    SUBMITTED,
    TAKE_AWAY,
    Order,
    OrderItem,
)
from apps.reports.sales_breakdown_reports import employeewise, itemwise, servicewise, timewise
from apps.reports.sales_reports import average_bill, cancelled_invoices, daywise, monthwise

from .helpers import DailyPnLTestMixin

_UNSET = object()


class SalesReportTestMixin(DailyPnLTestMixin):
    def _submit_sale(
        self,
        *,
        posting_date,
        items,
        posting_time=None,
        cashier=_UNSET,
        order_type=DINE_IN,
        is_return=False,
        return_against=None,
        status=SUBMITTED,
    ):
        order = Order.objects.create(
            posting_date=posting_date,
            posting_time=posting_time or time(12, 0),
            cashier=self.user if cashier is _UNSET else cashier,
            order_type=order_type,
            opening_entry=self.opening,
            is_return=is_return,
            return_against=return_against,
        )
        for item, qty, rate in items:
            source_line = None
            if is_return and return_against is not None:
                source_line = return_against.items.filter(item=item).first()
            OrderItem.objects.create(
                order=order,
                item=item,
                qty=qty,
                rate=rate,
                return_against_item=source_line,
            )
        order.recalculate_totals()
        if status != order.status:
            order.status = status
            if status == SUBMITTED:
                order._allow_submit = True
            elif status == CANCELLED:
                order.cancel_reason = CANCEL_REASON_WRONG_ORDER
                order._allow_cancellation = True
            elif status == DISCARDED:
                order._allow_discard = True
            order.save()
        return order


class SalesReportQueryTest(SalesReportTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls._setup_pnl_world()
        cls.day = date(2026, 9, 10)
        cls.next_day = date(2026, 9, 11)

    def test_department_split_and_calendar_grouping(self):
        self._submit_sale(
            posting_date=self.day,
            items=[(self.food, 1, Decimal("1500")), (self.drink, 1, Decimal("500"))],
        )
        self._submit_sale(
            posting_date=self.next_day,
            items=[(self.food, 2, Decimal("1500"))],
        )
        rows, totals = daywise(self.day, self.next_day)
        self.assertEqual(len(rows), 2)
        first = next(row for row in rows if row["posting_date"] == self.day)
        self.assertEqual(first["bills"], 1)
        self.assertEqual(first["gross_food"], Decimal("1500.00"))
        self.assertEqual(first["gross_drinks"], Decimal("500.00"))
        self.assertEqual(first["net"], Decimal("2000.00"))
        self.assertEqual(totals["bills"], 2)
        self.assertEqual(totals["gross_food"], Decimal("4500.00"))
        self.assertEqual(totals["net"], Decimal("5000.00"))

    def test_returns_net_on_return_date(self):
        sale = self._submit_sale(
            posting_date=self.day,
            items=[(self.food, 1, Decimal("1500"))],
        )
        self._submit_sale(
            posting_date=self.next_day,
            items=[(self.food, -1, Decimal("1500"))],
            is_return=True,
            return_against=sale,
        )
        rows, _totals = daywise(self.day, self.next_day)
        sale_row = next(row for row in rows if row["posting_date"] == self.day)
        return_row = next(row for row in rows if row["posting_date"] == self.next_day)
        self.assertEqual(sale_row["gross_food"], Decimal("1500.00"))
        self.assertEqual(sale_row["refunded"], Decimal("0.00"))
        self.assertEqual(sale_row["net"], Decimal("1500.00"))
        self.assertEqual(return_row["gross_food"], Decimal("-1500.00"))
        self.assertEqual(return_row["refunded"], Decimal("1500.00"))
        self.assertEqual(return_row["net"], Decimal("-1500.00"))

    def test_draft_cancelled_discarded_excluded(self):
        self._submit_sale(posting_date=self.day, items=[(self.food, 1, Decimal("1500"))], status="DRAFT")
        self._submit_sale(
            posting_date=self.day,
            items=[(self.food, 1, Decimal("1500"))],
            status=CANCELLED,
        )
        self._submit_sale(
            posting_date=self.day,
            items=[(self.food, 1, Decimal("1500"))],
            status=DISCARDED,
        )
        rows, totals = daywise(self.day, self.day)
        self.assertEqual(rows, [])
        self.assertEqual(totals["bills"], 0)
        self.assertEqual(totals["net"], Decimal("0.00"))

    def test_monthwise_groups_calendar_month(self):
        self._submit_sale(posting_date=date(2026, 8, 31), items=[(self.food, 1, Decimal("1500"))])
        self._submit_sale(posting_date=date(2026, 9, 1), items=[(self.drink, 1, Decimal("500"))])
        rows, totals = monthwise(date(2026, 8, 1), date(2026, 9, 30))
        self.assertEqual(len(rows), 2)
        august = next(row for row in rows if row["month"] == 8)
        september = next(row for row in rows if row["month"] == 9)
        self.assertEqual(august["gross_food"], Decimal("1500.00"))
        self.assertEqual(september["gross_drinks"], Decimal("500.00"))
        self.assertEqual(totals["bills"], 2)

    def test_itemwise_qty_gross_refunded_net(self):
        sale = self._submit_sale(posting_date=self.day, items=[(self.food, 2, Decimal("1500"))])
        self._submit_sale(
            posting_date=self.next_day,
            items=[(self.food, -1, Decimal("1500"))],
            is_return=True,
            return_against=sale,
        )
        rows, totals = itemwise(self.day, self.next_day)
        rice = next(row for row in rows if row["item_id"] == self.food.pk)
        self.assertEqual(rice["qty"], Decimal("1.00"))
        self.assertEqual(rice["gross"], Decimal("3000.00"))
        self.assertEqual(rice["refunded"], Decimal("1500.00"))
        self.assertEqual(rice["net"], Decimal("1500.00"))
        self.assertEqual(totals["net"], Decimal("1500.00"))

    def test_itemwise_department_and_group_filters(self):
        self._submit_sale(
            posting_date=self.day,
            items=[(self.food, 1, Decimal("1500")), (self.drink, 1, Decimal("500"))],
        )
        rows, _totals = itemwise(self.day, self.day, department="FOOD")
        self.assertEqual([row["item_id"] for row in rows], [self.food.pk])
        grouped, _ = itemwise(self.day, self.day, item_group_id=self.group_drinks.pk)
        self.assertEqual([row["item_id"] for row in grouped], [self.drink.pk])

    def test_itemwise_one_row_per_item_despite_name_snapshots(self):
        sale = self._submit_sale(posting_date=self.day, items=[(self.food, 1, Decimal("1500"))])
        OrderItem.objects.filter(order=sale).update(item_name="Jollof Rice (old name)")
        self._submit_sale(posting_date=self.next_day, items=[(self.food, 1, Decimal("1500"))])
        rows, totals = itemwise(self.day, self.next_day)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["item_id"], self.food.pk)
        self.assertEqual(rows[0]["qty"], Decimal("2.00"))
        self.assertEqual(totals["net"], Decimal("3000.00"))

    def test_employee_and_service_splits(self):
        other = type(self.user).objects.create_user(username="other", password="testpass123")
        self._submit_sale(posting_date=self.day, items=[(self.food, 1, Decimal("1500"))], cashier=self.user)
        self._submit_sale(
            posting_date=self.day,
            items=[(self.drink, 1, Decimal("500"))],
            cashier=other,
            order_type=TAKE_AWAY,
        )
        self._submit_sale(posting_date=self.day, items=[(self.food, 1, Decimal("1500"))], cashier=None)
        employees, emp_totals = employeewise(self.day, self.day)
        self.assertEqual(emp_totals["bills"], 3)
        names = {row["cashier_name"]: row["bills"] for row in employees}
        self.assertEqual(names[self.user.get_display_name()], 1)
        self.assertEqual(names[other.get_display_name()], 1)
        self.assertEqual(names["—"], 1)
        services, svc_totals = servicewise(self.day, self.day)
        by_type = {row["order_type"]: row for row in services}
        self.assertEqual(by_type[DINE_IN]["bills"], 2)
        self.assertEqual(by_type[TAKE_AWAY]["bills"], 1)
        self.assertEqual(by_type[TAKE_AWAY]["gross_drinks"], Decimal("500.00"))
        self.assertEqual(svc_totals["bills"], 3)

    def test_hourly_buckets(self):
        self._submit_sale(
            posting_date=self.day,
            posting_time=time(14, 15),
            items=[(self.food, 1, Decimal("1500"))],
        )
        rows, totals = timewise(self.day, self.day)
        self.assertEqual(len(rows), 24)
        self.assertEqual(rows[14]["bills"], 1)
        self.assertEqual(rows[14]["net"], Decimal("1500.00"))
        self.assertEqual(rows[0]["bills"], 0)
        self.assertEqual(totals["bills"], 1)

    def test_average_bill_nets_returns_in_numerator_and_denominator(self):
        sale = self._submit_sale(posting_date=self.day, items=[(self.food, 1, Decimal("1500"))])
        self._submit_sale(
            posting_date=self.day,
            items=[(self.food, -1, Decimal("500"))],
            is_return=True,
            return_against=sale,
        )
        rows, totals = average_bill(self.day, self.day, grouping="day")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bills"], 2)
        self.assertEqual(rows[0]["net"], Decimal("1000.00"))
        self.assertEqual(rows[0]["average"], Decimal("500.00"))
        self.assertEqual(totals["average"], Decimal("500.00"))

    def test_cancelled_invoices_exclude_returns_and_total_lost_sales(self):
        self._submit_sale(
            posting_date=self.day,
            items=[(self.food, 1, Decimal("1500"))],
            status=CANCELLED,
        )
        sale = self._submit_sale(posting_date=self.day, items=[(self.food, 1, Decimal("1500"))])
        self._submit_sale(
            posting_date=self.day,
            items=[(self.food, -1, Decimal("1500"))],
            is_return=True,
            return_against=sale,
            status=CANCELLED,
        )
        other = self._submit_sale(
            posting_date=self.day,
            items=[(self.drink, 1, Decimal("500"))],
            status=CANCELLED,
        )
        other.cancel_reason = CANCEL_REASON_CASHIER_ERROR
        other._allow_cancellation = True
        other.save(update_fields=["cancel_reason", "updated_at"])
        rows, totals = cancelled_invoices(self.day, self.day)
        self.assertEqual(totals["bills"], 2)
        self.assertEqual(totals["lost_sales"], Decimal("2000.00"))
        self.assertTrue(all(not Order.objects.get(pk=row["pk"]).is_return for row in rows))
        filtered, _ = cancelled_invoices(self.day, self.day, reason=CANCEL_REASON_CASHIER_ERROR)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["total"], Decimal("500.00"))


class SalesReportViewTest(SalesReportTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls._setup_pnl_world()
        mgr, _ = Group.objects.get_or_create(name="Spicy Manager")
        cls.manager.groups.add(mgr)

    def test_cashier_forbidden(self):
        self.client.force_login(self.user)
        for name in (
            "reports:sales_today",
            "reports:sales_daywise",
            "reports:sales_itemwise",
            "reports:sales_cancelled",
            "reports:sales_average_bill",
        ):
            response = self.client.get(reverse(name))
            self.assertIn(response.status_code, (302, 403), name)
