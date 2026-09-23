"""Django admin registrations for payment models."""

from django.contrib import admin

from .models import ModeOfPayment, PaymentGLMapping


@admin.register(ModeOfPayment)
class ModeOfPaymentAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "enabled", "is_default", "created_at")
    list_filter = ("type", "enabled")
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(PaymentGLMapping)
class PaymentGLMappingAdmin(admin.ModelAdmin):
    list_display = ("mode_of_payment", "default_account", "created_at")
    list_select_related = ("mode_of_payment", "default_account")
    search_fields = ("mode_of_payment__name", "default_account__name")
    ordering = ("mode_of_payment__name",)
