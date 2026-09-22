from django.conf import settings


def dev_admin_bypass(request) -> bool:
    """Whether this request skips Django-admin locks.

    True only for superusers while ``SPICY_DEV_ADMIN_BYPASS`` is on, which is
    tied to ``DEBUG`` — so the bypass lives in dev and dies automatically when
    the product goes live (``DEBUG=False``). The restriction code stays in
    place; this only short-circuits it.
    """
    if not getattr(settings, "SPICY_DEV_ADMIN_BYPASS", False):
        return False
    return bool(getattr(getattr(request, "user", None), "is_superuser", False))
