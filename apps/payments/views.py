"""Backoffice views for payment modes and GL mappings."""

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.users.decorators import backoffice_required, manager_required

from .forms import ModeOfPaymentForm, PaymentGLMappingForm
from .models import ModeOfPayment, PaymentGLMapping


@backoffice_required
def payments_dashboard(request: HttpRequest) -> HttpResponse:
    """Payments backoffice overview with mode and mapping counts."""
    modes = ModeOfPayment.objects.all()
    mappings = PaymentGLMapping.objects.select_related("mode_of_payment")
    context = {
        "mode_count": modes.count(),
        "enabled_mode_count": modes.filter(enabled=True).count(),
        "mapping_count": mappings.count(),
        "modes": modes,
        "mappings": mappings,
    }
    return render(request, "backoffice/payments/dashboard.html", context)


@backoffice_required
def mode_list(request: HttpRequest) -> HttpResponse:
    modes = ModeOfPayment.objects.all()
    return render(request, "backoffice/payments/mode_list.html", {"modes": modes})


@manager_required
def mode_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = ModeOfPaymentForm(request.POST)
        if form.is_valid():
            mode = form.save()
            messages.success(request, f"Payment mode '{mode.name}' created.")
            return redirect("payments:mode_detail", pk=mode.pk)
    else:
        form = ModeOfPaymentForm()
    return render(
        request,
        "backoffice/payments/mode_form.html",
        {"form": form, "is_create": True},
    )


@backoffice_required
def mode_detail(request: HttpRequest, pk: int) -> HttpResponse:
    mode = get_object_or_404(ModeOfPayment, pk=pk)
    mapping = getattr(mode, "gl_mapping", None)
    return render(
        request,
        "backoffice/payments/mode_detail.html",
        {"mode": mode, "mapping": mapping},
    )


@manager_required
def mode_update(request: HttpRequest, pk: int) -> HttpResponse:
    mode = get_object_or_404(ModeOfPayment, pk=pk)
    if request.method == "POST":
        form = ModeOfPaymentForm(request.POST, instance=mode)
        if form.is_valid():
            form.save()
            messages.success(request, f"Payment mode '{mode.name}' updated.")
            return redirect("payments:mode_detail", pk=mode.pk)
    else:
        form = ModeOfPaymentForm(instance=mode)
    return render(
        request,
        "backoffice/payments/mode_form.html",
        {"form": form, "is_create": False, "mode": mode},
    )


@backoffice_required
def gl_mapping_list(request: HttpRequest) -> HttpResponse:
    mappings = PaymentGLMapping.objects.select_related("mode_of_payment").all()
    return render(request, "backoffice/payments/gl_mapping_list.html", {"mappings": mappings})


@manager_required
def gl_mapping_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = PaymentGLMappingForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "GL mapping created.")
            return redirect("payments:gl_mapping_list")
    else:
        form = PaymentGLMappingForm()
    return render(
        request,
        "backoffice/payments/gl_mapping_form.html",
        {"form": form, "is_create": True},
    )


@manager_required
def gl_mapping_update(request: HttpRequest, pk: int) -> HttpResponse:
    mapping = get_object_or_404(PaymentGLMapping, pk=pk)
    if request.method == "POST":
        form = PaymentGLMappingForm(request.POST, instance=mapping)
        if form.is_valid():
            form.save()
            messages.success(request, "GL mapping updated.")
            return redirect("payments:gl_mapping_list")
    else:
        form = PaymentGLMappingForm(instance=mapping)
    return render(
        request,
        "backoffice/payments/gl_mapping_form.html",
        {"form": form, "is_create": False, "mapping": mapping},
    )


@backoffice_required
@require_POST
def gl_mapping_delete(request: HttpRequest, pk: int) -> HttpResponse:
    mapping = get_object_or_404(PaymentGLMapping, pk=pk)
    label = str(mapping)
    mapping.delete()
    messages.success(request, f"GL mapping '{label}' removed.")
    return redirect("payments:gl_mapping_list")
