# Authentication and Authorization

## Authentication

`AUTH_USER_MODEL` is `users.CustomUser`. django-allauth provides `/accounts/login/`, signup, logout, and related routes. `CustomLoginForm` and `CustomSignupForm` only adjust help text. Email verification defaults to `none`; if enabled, `email_confirmed` sets the confirmed address primary.

`django.contrib.auth.middleware.LoginRequiredMiddleware` is installed, so every view requires login unless it opts out with `@login_not_required`. The only opt-out in project code is `web.views.home`; allauth's own login/signup views opt themselves out.

## Roles

Roles are Django groups seeded after migrations:

- `Spicy Admin`
- `Spicy Manager`
- `Spicy Cashier`

Plain properties in `CustomUser` derive `is_admin`, `is_manager`, `is_cashier`, `has_backoffice_access`, and `has_staff_role` via `groups.filter(...).exists()` — no caching and no invalidation signal. Repeated access on the same user issues repeated indexed `exists()` queries (O(1) per access, not per row).

## Per-View Decorators

Every `/backoffice/*` and `/pos/*` view declares its role requirement through `apps/users/decorators.py`. No view relies on URL prefix for security.

| Decorator | Test | Surface |
|---|---|---|
| `@backoffice_required` | `has_backoffice_access` (admin/manager/superuser) | Inventory, menu, payments reads, settings reads, orders backoffice, backoffice shifts (opening/closing entries), web dashboard |
| `@manager_required` | superuser/admin/manager | Accounting, reports (Daily P&L and query reports), payments writes (modes, GL mappings), order cancel/return/delete, restaurant settings, production unit writes |
| `@staff_required` | `has_staff_role` (any Spicy role) | POS (`views_pos`), `web:pos_index` |
| `@admin_required` | superuser/admin | Staff role assignment/removal only |

Each decorator wraps `login_required`: anonymous users redirect to `settings.LOGIN_URL?next=...`; authenticated users who fail the role test get `403 PermissionDenied`, not a redirect.

Remaining inline checks (kept deliberately, as row-level capability checks inside authorized views):

- Ticket reprint from POS: manager check inside `pos_order_action`/history print.
- Full order history on POS: manager check inside `pos_order_history`.
- `web:pending_approval` stays `@login_required` — it serves logged-in users with no role.
- `users.views.profile` and `upload_profile_image` stay `@login_required` — self-service.

## Profile Side Effects

`users.views.upload_profile_image()` validates extension and 5 MB size, saves the avatar, and returns plain success or JSON errors. Avatar replacement/deletion removes old files through signals. Without an uploaded avatar, `CustomUser.avatar_url` returns an external Gravatar URL.

## Access Debugging

Start at the decorator on the specific view (`apps/users/decorators.py`), then the role properties in `apps/users/models.py`. A `403` means the view's decorator rejected the role; a `302` to login means the session is missing. There is no middleware gate and no role cache to go stale — group changes apply on the next request.
