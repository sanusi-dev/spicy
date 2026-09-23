"""Django admin registrations for shift models."""

from django.contrib import admin

from .models import ClosingPayment, OpeningPayment, POSClosingEntry, POSOpeningEntry


@admin.register(POSOpeningEntry)
class POSOpeningEntryAdmin(admin.ModelAdmin):
    list_display = (
        "pk",
        "cashier",
        "posting_date",
        "period_start_date",
        "period_end_date",
        "status",
        "closing_entry",
    )
    list_filter = ("status", "posting_date")
    list_select_related = ("cashier", "closing_entry")
    search_fields = ("=pk", "cashier__username", "remarks")
    readonly_fields = (
        "period_start_date",
        "period_end_date",
        "cancelled_at",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "posting_date"
    ordering = ("-period_start_date",)


@admin.register(OpeningPayment)
class OpeningPaymentAdmin(admin.ModelAdmin):
    list_display = ("opening_entry", "mode_of_payment", "opening_amount")
    list_filter = ("mode_of_payment",)
    list_select_related = ("opening_entry", "mode_of_payment")
    search_fields = ("opening_entry__pk", "mode_of_payment__name")
    ordering = ("opening_entry__pk", "mode_of_payment__name")


@admin.register(POSClosingEntry)
class POSClosingEntryAdmin(admin.ModelAdmin):
    list_display = (
        "pk",
        "cashier",
        "opening_entry",
        "period_end_date",
        "status",
        "total_short_excess",
        "grand_total",
    )
    list_filter = ("status", "posting_date")
    list_select_related = ("cashier", "opening_entry")
    search_fields = ("pk", "opening_entry__pk", "remarks")
    readonly_fields = (
        "period_start_date",
        "total_quantity",
        "net_total",
        "grand_total",
        "total_short_excess",
        "cancelled_at",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "posting_date"
    ordering = ("-period_end_date",)


@admin.register(ClosingPayment)
class ClosingPaymentAdmin(admin.ModelAdmin):
    list_display = (
        "closing_entry",
        "mode_of_payment",
        "opening_amount",
        "expected_amount",
        "closing_amount",
        "difference",
    )
    list_filter = ("mode_of_payment",)
    list_select_related = ("closing_entry", "mode_of_payment")
    search_fields = ("closing_entry__pk", "mode_of_payment__name")
    ordering = ("closing_entry__pk", "mode_of_payment__name")
