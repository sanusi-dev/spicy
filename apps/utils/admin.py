from django.conf import settings


def dev_admin_bypass(request) -> bool:
    """Whether this request skips Django-admin locks (dev superuser bypass)."""
    if not getattr(settings, "SPICY_DEV_ADMIN_BYPASS", False):
        return False
    return bool(getattr(getattr(request, "user", None), "is_superuser", False))
