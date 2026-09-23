"""Backoffice order URLs."""

from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("dashboard/", view=views.orders_dashboard, name="dashboard"),
    path("", view=views.order_list, name="order_list"),
    path("<int:pk>/", view=views.order_detail, name="order_detail"),
    path("<int:pk>/cancel/", view=views.order_cancel, name="order_cancel"),
    path("<int:pk>/delete/", view=views.order_delete, name="order_delete"),
    path("<int:pk>/return/", view=views.order_return, name="order_return"),
    path("<int:pk>/return/submit/", view=views.order_return_submit, name="order_return_submit"),
    path(
        "<int:pk>/return/lines/<int:line_pk>/",
        view=views.order_return_line_update,
        name="order_return_line_update",
    ),
    path("kots/", view=views.kot_list, name="kot_list"),
    path("kots/<int:pk>/", view=views.kot_detail, name="kot_detail"),
]
