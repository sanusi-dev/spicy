from django.db.models import Count, OuterRef, Subquery
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.inventory.models import Item
from apps.settings.models import Restaurant
from apps.users.decorators import backoffice_required

from .forms import (
    ItemAddOnForm,
    ItemVariantForm,
    MenuForm,
    MenuItemForm,
)
from .models import ItemAddOn, ItemVariant, Menu, MenuItem


@backoffice_required
def menu_dashboard(request: HttpRequest) -> HttpResponse:
    context = {
        "menu_count": Menu.objects.count(),
        "menu_item_count": MenuItem.objects.count(),
        "add_on_count": ItemAddOn.objects.count(),
        "variant_count": ItemVariant.objects.count(),
    }
    return render(request, "backoffice/menu/dashboard.html", context)


@backoffice_required
def menu_list(request: HttpRequest) -> HttpResponse:
    menus = Menu.objects.annotate(item_count=Count("items"))
    return render(request, "backoffice/menu/menu_list.html", {"menus": menus})


@backoffice_required
def menu_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = MenuForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("menu:menu_list")
    else:
        form = MenuForm()
    return render(request, "backoffice/menu/menu_form.html", {"form": form, "is_create": True})


@backoffice_required
def menu_detail(request: HttpRequest, pk: int) -> HttpResponse:
    menu = get_object_or_404(Menu, pk=pk)
    restaurant = Restaurant.load()
    menu_items = menu.items.select_related("item", "item__item_group").all()
    return render(
        request,
        "backoffice/menu/menu_detail.html",
        {
            "menu": menu,
            "menu_items": menu_items,
            "active_menu": restaurant.active_menu if restaurant else None,
            "is_active_menu": bool(restaurant and restaurant.active_menu_id == menu.pk and menu.enabled),
        },
    )


@backoffice_required
def menu_update(request: HttpRequest, pk: int) -> HttpResponse:
    menu = get_object_or_404(Menu, pk=pk)
    if request.method == "POST":
        form = MenuForm(request.POST, instance=menu)
        if form.is_valid():
            form.save()
            return redirect("menu:menu_detail", pk=menu.pk)
    else:
        form = MenuForm(instance=menu)
    return render(
        request,
        "backoffice/menu/menu_form.html",
        {"form": form, "is_create": False, "menu": menu},
    )


@backoffice_required
def menu_item_list(request: HttpRequest) -> HttpResponse:
    menu_id = request.GET.get("menu", "")
    department = request.GET.get("department", "")
    status = request.GET.get("status", "all")
    menu_items = MenuItem.objects.select_related("menu", "item").all()
    if menu_id:
        menu_items = menu_items.filter(menu_id=menu_id)
    if department:
        menu_items = menu_items.filter(item__department=department)
    if status == "active":
        menu_items = menu_items.filter(disabled=False, special_dish=False)
    elif status == "special":
        menu_items = menu_items.filter(disabled=False, special_dish=True)
    elif status == "disabled":
        menu_items = menu_items.filter(disabled=True)
    menus = Menu.objects.all().order_by("name")
    return render(
        request,
        "backoffice/menu/menu_item_list.html",
        {
            "menu_items": menu_items,
            "menus": menus,
            "selected_menu": menu_id,
            "selected_department": department,
            "selected_status": status,
        },
    )


@backoffice_required
def menu_item_create(request: HttpRequest) -> HttpResponse:
    menu_id = request.GET.get("menu")
    if request.method == "POST":
        form = MenuItemForm(request.POST)
        if form.is_valid():
            menu_item = form.save()
            return redirect("menu:menu_detail", pk=menu_item.menu_id)
    else:
        form = MenuItemForm(initial={"menu": menu_id} if menu_id else None)
    return render(
        request,
        "backoffice/menu/menu_item_form.html",
        {"form": form, "is_create": True, "menu_id": menu_id},
    )


@backoffice_required
def menu_item_update(request: HttpRequest, pk: int) -> HttpResponse:
    menu_item = get_object_or_404(MenuItem, pk=pk)
    if request.method == "POST":
        form = MenuItemForm(request.POST, instance=menu_item)
        if form.is_valid():
            menu_item = form.save()
            return redirect("menu:menu_detail", pk=menu_item.menu_id)
    else:
        form = MenuItemForm(instance=menu_item)
    return render(
        request,
        "backoffice/menu/menu_item_form.html",
        {"form": form, "is_create": False, "menu_item": menu_item},
    )


@backoffice_required
@require_POST
def menu_item_delete(request: HttpRequest, pk: int) -> HttpResponse:
    menu_item = get_object_or_404(MenuItem, pk=pk)
    menu_id = menu_item.menu_id
    menu_item.delete()
    return redirect("menu:menu_detail", pk=menu_id)


@backoffice_required
def add_on_list(request: HttpRequest) -> HttpResponse:
    parent_id = request.GET.get("parent_item", "")
    department = request.GET.get("department", "")
    restaurant = Restaurant.load()
    active_menu = restaurant.active_menu if restaurant else None
    active_menu_id = active_menu.pk if active_menu and active_menu.enabled else None
    active_price = MenuItem.objects.filter(
        menu_id=active_menu_id,
        item_id=OuterRef("add_on_item_id"),
        disabled=False,
    ).values("rate")[:1]
    add_ons = ItemAddOn.objects.select_related(
        "parent_item", "parent_item__item_group", "add_on_item", "add_on_item__item_group"
    ).annotate(active_menu_rate=Subquery(active_price))
    if parent_id:
        try:
            parent_pk = int(parent_id)
        except ValueError:
            parent_pk = None
        if parent_pk is not None:
            add_ons = add_ons.filter(parent_item_id=parent_pk)
    if department:
        add_ons = add_ons.filter(add_on_item__department=department)
    parent_items = Item.objects.filter(add_ons__isnull=False).distinct().order_by("item_name").only("item_name")
    return render(
        request,
        "backoffice/menu/add_on_list.html",
        {
            "add_ons": add_ons,
            "parent_items": parent_items,
            "selected_parent": parent_id,
            "selected_department": department,
            "active_menu": active_menu,
            "active_menu_is_live": bool(active_menu_id),
        },
    )


@backoffice_required
def add_on_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = ItemAddOnForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("menu:add_on_list")
    else:
        form = ItemAddOnForm()
    return render(request, "backoffice/menu/add_on_form.html", {"form": form, "is_create": True})


@backoffice_required
def add_on_update(request: HttpRequest, pk: int) -> HttpResponse:
    add_on = get_object_or_404(ItemAddOn, pk=pk)
    if request.method == "POST":
        form = ItemAddOnForm(request.POST, instance=add_on)
        if form.is_valid():
            form.save()
            return redirect("menu:add_on_list")
    else:
        form = ItemAddOnForm(instance=add_on)
    return render(
        request,
        "backoffice/menu/add_on_form.html",
        {"form": form, "is_create": False, "add_on": add_on},
    )


@backoffice_required
@require_POST
def add_on_delete(request: HttpRequest, pk: int) -> HttpResponse:
    add_on = get_object_or_404(ItemAddOn, pk=pk)
    add_on.delete()
    return redirect("menu:add_on_list")


@backoffice_required
def variant_list(request: HttpRequest) -> HttpResponse:
    parent_id = request.GET.get("parent_item")
    variants = ItemVariant.objects.select_related("parent_item", "variant_item").all()
    if parent_id:
        try:
            parent_pk = int(parent_id)
        except ValueError:
            parent_pk = None
        if parent_pk is not None:
            variants = variants.filter(parent_item_id=parent_pk)
    parent_items = Item.objects.filter(pos_variants__isnull=False).distinct().order_by("item_name").only("item_name")
    return render(
        request,
        "backoffice/menu/variant_list.html",
        {"variants": variants, "parent_items": parent_items, "selected_parent": parent_id},
    )


@backoffice_required
def variant_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = ItemVariantForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("menu:variant_list")
    else:
        form = ItemVariantForm()
    return render(request, "backoffice/menu/variant_form.html", {"form": form, "is_create": True})


@backoffice_required
def variant_update(request: HttpRequest, pk: int) -> HttpResponse:
    variant = get_object_or_404(ItemVariant, pk=pk)
    if request.method == "POST":
        form = ItemVariantForm(request.POST, instance=variant)
        if form.is_valid():
            form.save()
            return redirect("menu:variant_list")
    else:
        form = ItemVariantForm(instance=variant)
    return render(
        request,
        "backoffice/menu/variant_form.html",
        {"form": form, "is_create": False, "variant": variant},
    )


@backoffice_required
@require_POST
def variant_delete(request: HttpRequest, pk: int) -> HttpResponse:
    variant = get_object_or_404(ItemVariant, pk=pk)
    variant.delete()
    return redirect("menu:variant_list")
