from django import forms
from django.contrib.auth import password_validation

from apps.accounting.models import LedgerAccount
from apps.inventory.models import Warehouse
from apps.menu.models import Menu
from apps.users.models import CustomUser
from apps.utils.forms import active_choices

from .models import (
    ProductionUnit,
    Restaurant,
)

TAILWIND_INPUT_CLASS = (
    "w-full rounded-xl border border-gray-300 px-3 py-2.5 text-sm "
    "focus:border-gray-400 focus:outline-none focus:ring-1 focus:ring-gray-400/20 "
    "bg-white/50 transition-shadow"
)

TAILWIND_CHECKBOX_CLASS = "rounded border-gray-300 text-orange-500 focus:ring-orange-500/25"


class SettingsModelForm(forms.ModelForm):
    """Base ModelForm that applies Tailwind CSS classes to all fields."""

    TEXT_WIDGETS = (
        forms.TextInput,
        forms.Textarea,
        forms.EmailInput,
        forms.URLInput,
        forms.NumberInput,
        forms.PasswordInput,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "address" in self.fields:
            self.fields["address"].widget.attrs["rows"] = 4
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput) and not field.widget.attrs.get("class"):
                field.widget.attrs["class"] = TAILWIND_CHECKBOX_CLASS
            elif (
                isinstance(field.widget, self.TEXT_WIDGETS)
                and not field.widget.attrs.get("class")
                or isinstance(field.widget, forms.Select)
                and not field.widget.attrs.get("class")
            ):
                field.widget.attrs["class"] = TAILWIND_INPUT_CLASS
        for _name, field in self.fields.items():
            if isinstance(field, forms.ModelChoiceField):
                field.empty_label = f"Select {field.label.lower()}..."


class RestaurantForm(SettingsModelForm):
    """The single settings record: identity, menu, stock, POS behaviour, and accounting."""

    class Meta:
        model = Restaurant
        fields = [
            "company",
            "invoice_series_prefix",
            "address",
            "active_menu",
            "store_warehouse",
            "default_warehouse",
            "max_open_drafts",
            "pos_allow_full_history",
            "require_payment_reference",
            "default_income_account",
            "default_sales_returns_account",
            "default_expense_account",
            "round_off_account",
            "account_for_change_amount",
            "wastage_account",
            "cash_shortage_account",
            "cash_over_short_account",
            "variance_approval_threshold",
            "default_payable_account",
            "default_supplier_expense_account",
            "stock_received_but_not_billed_account",
            "inventory_price_variance_account",
            "stock_adjustment_account",
            "temporary_opening_account",
            "petty_cash_expense_account",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["max_open_drafts"].required = False
        self.fields["active_menu"].queryset = active_choices(Menu, self.instance.active_menu_id, enabled=True)
        self.fields["default_warehouse"].queryset = active_choices(
            Warehouse, self.instance.default_warehouse_id, disabled=False
        )
        self.fields["store_warehouse"].queryset = active_choices(
            Warehouse, self.instance.store_warehouse_id, disabled=False
        )
        for field_name in (
            "default_income_account",
            "default_sales_returns_account",
            "default_expense_account",
            "round_off_account",
            "account_for_change_amount",
            "wastage_account",
            "cash_shortage_account",
            "cash_over_short_account",
            "default_payable_account",
            "default_supplier_expense_account",
            "stock_received_but_not_billed_account",
            "inventory_price_variance_account",
            "stock_adjustment_account",
            "temporary_opening_account",
            "petty_cash_expense_account",
        ):
            self.fields[field_name].queryset = active_choices(
                LedgerAccount, getattr(self.instance, f"{field_name}_id"), disabled=False, is_group=False
            )


class ProductionUnitForm(SettingsModelForm):
    class Meta:
        model = ProductionUnit
        fields = [
            "name",
            "department",
            "warehouse",
            "block_takeaway_kot",
            "printer_ip",
            "printer_paper_width",
            "printer_cut_mode",
            "income_account",
            "sales_returns_account",
            "expense_account",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs["placeholder"] = "e.g. Main Kitchen"
        self.fields["department"].choices = [
            ("", "Select department..."),
            *self.fields["department"].choices,
        ]
        self.fields["block_takeaway_kot"].help_text = "When enabled, this station will not receive takeaway orders."
        self.fields["printer_ip"].widget.attrs["placeholder"] = "e.g. 192.168.1.51"
        self.fields["warehouse"].queryset = active_choices(Warehouse, self.instance.warehouse_id, disabled=False)
        self.fields[
            "warehouse"
        ].help_text = "The warehouse that supplies this station — Kitchen for food, Bar for drinks."
        self.fields["income_account"].queryset = active_choices(
            LedgerAccount, self.instance.income_account_id, disabled=False, is_group=False
        )
        self.fields["sales_returns_account"].queryset = active_choices(
            LedgerAccount, self.instance.sales_returns_account_id, disabled=False, is_group=False
        )
        self.fields["expense_account"].queryset = active_choices(
            LedgerAccount, self.instance.expense_account_id, disabled=False, is_group=False
        )


class StaffCreateForm(forms.Form):
    """Create a login with one role. Passwords are never displayed or stored in plain text."""

    ROLE_CHOICES = [
        ("cashier", "Cashier"),
        ("manager", "Manager"),
        ("admin", "Admin"),
    ]

    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    password1 = forms.CharField(widget=forms.PasswordInput)
    password2 = forms.CharField(widget=forms.PasswordInput)
    role = forms.ChoiceField(choices=ROLE_CHOICES, initial="cashier")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not field.widget.attrs.get("class"):
                field.widget.attrs["class"] = TAILWIND_INPUT_CLASS

    def clean_username(self):
        username = (self.cleaned_data["username"] or "").strip()
        if not username:
            raise forms.ValidationError("A username is required.")
        if CustomUser.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("A user with this username already exists.")
        return username

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The two passwords do not match.")
        if password1:
            try:
                password_validation.validate_password(
                    password1,
                    user=CustomUser(
                        username=cleaned.get("username", ""),
                        first_name=cleaned.get("first_name", ""),
                        last_name=cleaned.get("last_name", ""),
                    ),
                )
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned

    def save(self):
        user = CustomUser(
            username=self.cleaned_data["username"],
            first_name=self.cleaned_data.get("first_name", ""),
            last_name=self.cleaned_data.get("last_name", ""),
            is_active=True,
        )
        user.set_password(self.cleaned_data["password1"])
        user.save()
        return user
