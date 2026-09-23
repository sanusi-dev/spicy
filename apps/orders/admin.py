"""Django admin registrations for order models."""

from django.contrib import admin

from apps.utils.admin import dev_admin_bypass

from .models import KOT, KOTItem, Order, OrderAuditEvent, OrderItem, OrderPayment


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ("item", "qty", "rate", "amount", "customer_index")
    readonly_fields = fields
    can_delete = False

    def get_readonly_fields(self, request, obj=None):
        if dev_admin_bypass(request):
            return []
        return super().get_readonly_fields(request, obj)

    def has_add_permission(self, request, obj=None):
        return bool(dev_admin_bypass(request))


class OrderPaymentInline(admin.TabularInline):
    model = OrderPayment
    extra = 0
    readonly_fields = ("mode_of_payment", "amount", "reference_no", "created_at", "updated_at")
    can_delete = False

    def get_readonly_fields(self, request, obj=None):
        if dev_admin_bypass(request):
            return []
        return super().get_readonly_fields(request, obj)

    def has_add_permission(self, request, obj=None):
        return bool(dev_admin_bypass(request))


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "order_type", "customer_name", "status", "grand_total", "posting_date")
    list_filter = ("status", "order_type", "posting_date")
    search_fields = ("invoice_number", "customer_name")
    inlines = (OrderItemInline, OrderPaymentInline)
    ordering = ("-posting_date",)
    readonly_fields = (
        "invoice_number",
        "order_number",
        "order_type",
        "customer_name",
        "guest_count",
        "cashier",
        "status",
        "is_paid",
        "invoice_printed",
        "posting_date",
        "posting_time",
        "net_total",
        "grand_total",
        "rounded_total",
        "rounding_adjustment",
        "paid_amount",
        "change_amount",
        "cancel_reason",
        "cancel_reason_note",
        "cancelled_by",
        "cancelled_at",
        "opening_entry",
        "stock_warehouse",
        "arrived_time",
        "submitted_at",
        "invoice_printed_at",
        "invoice_printed_by",
        "is_return",
        "return_against",
        "created_at",
        "updated_at",
    )

    def get_readonly_fields(self, request, obj=None):
        if dev_admin_bypass(request):
            return []
        return super().get_readonly_fields(request, obj)

    def has_add_permission(self, request):
        return bool(dev_admin_bypass(request))

    def has_delete_permission(self, request, obj=None):
        return bool(dev_admin_bypass(request))


@admin.register(KOT)
class KOTAdmin(admin.ModelAdmin):
    list_display = (
        "kot_number",
        "ticket_type",
        "type",
        "print_status",
        "status",
        "production_unit",
        "order",
        "posting_datetime",
    )
    list_filter = ("ticket_type", "type", "print_status", "status", "production_unit")
    search_fields = ("kot_number",)
    ordering = ("-posting_datetime",)
    readonly_fields = (
        "order",
        "production_unit",
        "type",
        "kot_number",
        "ticket_type",
        "status",
        "print_status",
        "created_by",
        "posting_datetime",
        "order_number",
        "original_kots",
        "cancelled_by",
        "cancelled_at",
        "created_at",
        "updated_at",
    )

    def get_readonly_fields(self, request, obj=None):
        if dev_admin_bypass(request):
            return []
        return super().get_readonly_fields(request, obj)

    def has_add_permission(self, request):
        return bool(dev_admin_bypass(request))

    def has_delete_permission(self, request, obj=None):
        return bool(dev_admin_bypass(request))


@admin.register(KOTItem)
class KOTItemAdmin(admin.ModelAdmin):
    list_display = ("kot", "item_name", "qty", "cancelled_qty")
    readonly_fields = (
        "kot",
        "item",
        "item_name",
        "qty",
        "cancelled_qty",
        "comments",
        "customer_index",
        "created_at",
        "updated_at",
    )

    def get_readonly_fields(self, request, obj=None):
        if dev_admin_bypass(request):
            return []
        return super().get_readonly_fields(request, obj)

    def has_add_permission(self, request):
        return bool(dev_admin_bypass(request))

    def has_delete_permission(self, request, obj=None):
        return bool(dev_admin_bypass(request))


@admin.register(OrderAuditEvent)
class OrderAuditEventAdmin(admin.ModelAdmin):
    list_display = ("order", "event_type", "actor", "created_at")
    list_filter = ("event_type", "created_at")
    search_fields = ("order__invoice_number", "event_type", "actor__username")
    readonly_fields = ("order", "event_type", "actor", "metadata", "created_at", "updated_at")

    def get_readonly_fields(self, request, obj=None):
        if dev_admin_bypass(request):
            return []
        return super().get_readonly_fields(request, obj)

    def has_add_permission(self, request):
        return bool(dev_admin_bypass(request))

    def has_change_permission(self, request, obj=None):
        return bool(dev_admin_bypass(request))

    def has_delete_permission(self, request, obj=None):
        return bool(dev_admin_bypass(request))
