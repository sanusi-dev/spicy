from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def _role_required(test_func):
    """Return a decorator that requires login and passes test_func(user)."""

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if not test_func(request.user):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


backoffice_required = _role_required(lambda u: u.has_backoffice_access)
manager_required = _role_required(lambda u: u.is_superuser or u.is_admin or u.is_manager)
staff_required = _role_required(lambda u: u.has_staff_role)
admin_required = _role_required(lambda u: u.is_superuser or u.is_admin)
