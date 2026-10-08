from django.urls import path

from . import views

app_name = "web"
urlpatterns = [
    path("", views.home, name="home"),
    path("backoffice/dashboard/", views.dashboard, name="dashboard"),
    path("pending-approval/", views.pending_approval, name="pending_approval"),
]
