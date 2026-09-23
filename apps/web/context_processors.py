from copy import copy

from django.conf import settings

from apps.inventory.models import ItemGroup

from .meta import absolute_url


def project_meta(request):
    project_data = copy(settings.PROJECT_METADATA)
    project_data["TITLE"] = "{} | {}".format(project_data["NAME"], project_data["DESCRIPTION"])
    return {
        "project_meta": project_data,
        "page_url": absolute_url(request.path),
        "page_title": "",
        "page_description": "",
        "page_image": "",
    }


def csrf_settings(request):
    """Expose the CSRF cookie name to templates."""
    return {
        "csrf_cookie_name": settings.CSRF_COOKIE_NAME,
    }


def inventory_navigation(request):
    """Expose the item-group count used by backoffice navigation."""
    if not request.path.startswith("/backoffice/"):
        return {}
    return {"item_group_count": ItemGroup.objects.count()}
