from django.conf import settings
from django.contrib.sites.models import Site


def get_protocol(is_secure: bool = settings.USE_HTTPS_IN_ABSOLUTE_URLS) -> str:
    """Return 'http' or 'https' for the configured scheme."""
    return f"http{'s' if is_secure else ''}"


def get_server_root(is_secure: bool = settings.USE_HTTPS_IN_ABSOLUTE_URLS) -> str:
    """Return the server root with protocol (e.g. https://www.example.com)."""
    return f"{get_protocol(is_secure)}://{Site.objects.get_current().domain}"


def absolute_url(relative_url: str, is_secure: bool = settings.USE_HTTPS_IN_ABSOLUTE_URLS):
    """Return the full absolute URL for a relative path."""
    return f"{get_server_root(is_secure)}{relative_url}"
