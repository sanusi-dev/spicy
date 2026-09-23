from typing import cast

from django.contrib import messages
from django.contrib.auth.models import Group
from django.db import models
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django_htmx.middleware import HtmxDetails

from apps.users.decorators import admin_required, backoffice_required, manager_required
from apps.users.models import CustomUser

from .forms import (
    ProductionUnitForm,
    RestaurantForm,
    StaffCreateForm,
)
from .models import (
    ProductionUnit,
    Restaurant,
)

SPICY_GROUP_NAMES = ["Spicy Admin", "Spicy Manager", "Spicy Cashier"]


class _HtmxRequest(HttpRequest):
    htmx: HtmxDetails


def _is_htmx(request: HttpRequest) -> bool:
    return bool(cast(_HtmxRequest, request).htmx)


def _ensure_spicy_groups():
    """Fetch Spicy role groups in one query, creating any missing ones. Returns dict keyed by name."""
    groups = {group.name: group for group in Group.objects.filter(name__in=SPICY_GROUP_NAMES)}
    for name in SPICY_GROUP_NAMES:
        if name not in groups:
            groups[name] = Group.objects.create(name=name)
    return groups


@backoffice_required
def settings_dashboard(request: HttpRequest) -> HttpResponse:
    context = {
        "settings_configured": Restaurant.objects.exists(),
        "staff_count": CustomUser.objects.filter(
            groups__name__in=["Spicy Admin", "Spicy Manager", "Spicy Cashier"]
        )
        .distinct()
        .count(),
        "production_unit_count": ProductionUnit.objects.count(),
    }
    return render(request, "backoffice/settings/dashboard.html", context)


@manager_required
def restaurant_settings(request: HttpRequest) -> HttpResponse:
    restaurant = Restaurant.load()
    if request.method == "POST":
        form = RestaurantForm(request.POST, instance=restaurant)
        if form.is_valid():
            form.save()
            messages.success(request, "Restaurant settings saved.")
            return redirect("settings:restaurant_settings")
    else:
        form = RestaurantForm(instance=restaurant) if restaurant else RestaurantForm()
    return render(request, "backoffice/settings/restaurant_settings.html", {"form": form, "restaurant": restaurant})


@backoffice_required
def staff_list(request: HttpRequest) -> HttpResponse:
    search = request.GET.get("search", "")

    users = CustomUser.objects.all().order_by("-date_joined")
    if search:
        users = users.filter(
            models.Q(username__icontains=search)
            | models.Q(first_name__icontains=search)
            | models.Q(last_name__icontains=search)
        )
    # Prefetch only Spicy role groups so the per-row role derivation hits the prefetch cache.
    users = users.prefetch_related(
        models.Prefetch("groups", queryset=Group.objects.filter(name__in=SPICY_GROUP_NAMES))
    )

    staff_data = [_build_staff_entry(user) for user in users]

    context = {"staff_data": staff_data, "search": search}
    if _is_htmx(request) and request.htmx.target == "staff-table-body":
        return render(request, "backoffice/settings/staff_list.html#staff-rows", context)
    return render(request, "backoffice/settings/staff_list.html", context)


@admin_required
@require_POST
def staff_assign_role(request: HttpRequest, pk: int, role: str) -> HttpResponse:
    user = get_object_or_404(CustomUser, pk=pk)
    _apply_role(user, role)

    if role == "admin":
        messages.success(request, f"{user.get_display_name()} is now an Admin.")
    elif role == "manager":
        messages.success(request, f"{user.get_display_name()} is now a Manager.")
    elif role == "cashier":
        messages.success(request, f"{user.get_display_name()} is now a Cashier.")

    if _is_htmx(request) and request.htmx.target == f"staff-row-{pk}":
        return render(request, "backoffice/settings/staff_list.html#staff-row", {"entry": _build_staff_entry(user)})
    return redirect("settings:staff_list")


def _apply_role(user: CustomUser, role: str) -> None:
    """Apply one Spicy role: exactly one group plus the matching flags."""
    groups = _ensure_spicy_groups()
    admin_group = groups["Spicy Admin"]
    manager_group = groups["Spicy Manager"]
    cashier_group = groups["Spicy Cashier"]

    user.groups.remove(admin_group, manager_group, cashier_group)

    if role == "admin":
        user.is_superuser = True
        user.is_staff = True
        user.groups.add(admin_group)
        user.save()
    elif role == "manager":
        if user.is_superuser:
            user.is_superuser = False
            user.is_staff = False
        user.save()
        user.groups.add(manager_group)
    elif role == "cashier":
        if user.is_superuser:
            user.is_superuser = False
            user.is_staff = False
        user.save()
        user.groups.add(cashier_group)


@admin_required
def staff_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = StaffCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            _apply_role(user, form.cleaned_data["role"])
            messages.success(request, f"Login created for {user.get_display_name()}.")
            return redirect("settings:staff_list")
    else:
        form = StaffCreateForm()
    return render(request, "backoffice/settings/staff_form.html", {"form": form, "is_create": True})


@admin_required
@require_POST
def staff_toggle_active(request: HttpRequest, pk: int) -> HttpResponse:
    user = get_object_or_404(CustomUser, pk=pk)
    if user.pk == request.user.pk and user.is_active:
        messages.error(request, "You cannot deactivate your own login.")
        return redirect("settings:staff_list")
    user.is_active = not user.is_active
    user.save(update_fields=["is_active"])
    messages.success(
        request,
        f"{user.get_display_name()} is now {'active' if user.is_active else 'inactive'}.",
    )
    if _is_htmx(request) and request.htmx.target == f"staff-row-{pk}":
        return render(request, "backoffice/settings/staff_list.html#staff-row", {"entry": _build_staff_entry(user)})
    return redirect("settings:staff_list")


@admin_required
@require_POST
def staff_remove_role(request: HttpRequest, pk: int) -> HttpResponse:
    user = get_object_or_404(CustomUser, pk=pk)
    groups = _ensure_spicy_groups()
    admin_group = groups["Spicy Admin"]
    manager_group = groups["Spicy Manager"]
    cashier_group = groups["Spicy Cashier"]

    if user.groups.filter(pk=admin_group.pk).exists():
        messages.error(request, "Cannot remove role from an Admin. Demote them to Manager first.")
        return redirect("settings:staff_list")

    user.groups.remove(admin_group, manager_group, cashier_group)
    messages.success(request, f"Role removed from {user.get_display_name()}.")

    if _is_htmx(request) and request.htmx.target == f"staff-row-{pk}":
        return render(request, "backoffice/settings/staff_list.html#staff-row", {"entry": _build_staff_entry(user)})
    return redirect("settings:staff_list")


def _build_staff_entry(user: CustomUser) -> dict[str, CustomUser | str]:
    """Derive a user's role for the staff table row entry."""
    user_group_names = {group.name for group in user.groups.all()}
    if user.is_superuser or "Spicy Admin" in user_group_names:
        role = "admin"
    elif "Spicy Manager" in user_group_names:
        role = "manager"
    elif "Spicy Cashier" in user_group_names:
        role = "cashier"
    else:
        role = ""
    return {"user": user, "role": role}


@backoffice_required
def production_unit_list(request: HttpRequest) -> HttpResponse:
    production_units = ProductionUnit.objects.select_related("warehouse").all()
    return render(request, "backoffice/settings/production_unit_list.html", {"production_units": production_units})


@manager_required
def production_unit_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = ProductionUnitForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("settings:production_unit_list")
    else:
        form = ProductionUnitForm()
    return render(request, "backoffice/settings/production_unit_form.html", {"form": form, "is_create": True})


@backoffice_required
def production_unit_detail(request: HttpRequest, pk: int) -> HttpResponse:
    production_unit = get_object_or_404(ProductionUnit.objects.select_related("warehouse"), pk=pk)
    return render(request, "backoffice/settings/production_unit_detail.html", {"production_unit": production_unit})


@manager_required
def production_unit_update(request: HttpRequest, pk: int) -> HttpResponse:
    production_unit = get_object_or_404(ProductionUnit, pk=pk)
    if request.method == "POST":
        form = ProductionUnitForm(request.POST, instance=production_unit)
        if form.is_valid():
            form.save()
            return redirect("settings:production_unit_detail", pk=production_unit.pk)
    else:
        form = ProductionUnitForm(instance=production_unit)
    return render(
        request,
        "backoffice/settings/production_unit_form.html",
        {"form": form, "is_create": False, "production_unit": production_unit},
    )


@manager_required
@require_POST
def production_unit_delete(request: HttpRequest, pk: int) -> HttpResponse:
    production_unit = get_object_or_404(ProductionUnit, pk=pk)
    production_unit.delete()
    return redirect("settings:production_unit_list")
