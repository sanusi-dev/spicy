# Backoffice User Creation — Implementation Plan

**Status:** proposed (awaiting developer review; not yet in `PLAN.md` / `FEATURES.md`).
**Scope:** create logins + active toggle on the existing User-roles page. No change to
role semantics, login flow, or allauth.

## 1. Objective

Let an Admin create a cashier/manager login (username + name + password + one role) and
lock out leavers, without touching Django admin. Role meanings stay exactly as today
(`FEATURES.md` A1 #3; `apps/users/models.py:46-64`).

## 2. Decisions

- New `StaffCreateForm` (`apps/settings/forms.py`): `username`, `first_name`,
  `last_name`, `password1`/`password2`, `role` (choices cashier/manager/admin, default
  cashier). Validation: username unique case-insensitively; passwords must match and pass
  Django's `AUTH_PASSWORD_VALIDATORS`; names optional but stored when given.
- New views in `apps/settings/views.py`, both `@admin_required` (same gate as
  `staff_assign_role`/`staff_remove_role`, `views.py:95-150`):
  - `staff_create`: GET renders `staff_form.html`; POST validates, creates an active user,
    applies the role, flashes success, redirects to `staff_list` (PRG, same pattern as
    `production_unit_create`, `views.py:172-181`).
  - `staff_toggle_active`: POST-only; flips `is_active`; refuses self-deactivation
    ("You cannot deactivate your own login.").
- Creation reuses role application: refactor the group/flag block out of
  `staff_assign_role` (`views.py:99-129`) into `_apply_role(user, role)` and call it from
  both views. Semantics preserved: admin → `is_superuser=True, is_staff=True` + Admin
  group; manager/cashier → superuser/staff cleared + respective group. Exactly one
  Spicy group per user, as today.
- Deactivation replaces nothing: `staff_remove_role` keeps working (strips groups, keeps
  login for profile/history). Toggle covers the "ex-staff must not log in at all" case;
  inactive users vanish from POS login and fail `has_staff_role` implicitly (`is_active`
  is checked by Django auth before any group test).
- URLs (`apps/settings/urls.py:10-12`): `staff/create/` named `staff_create` placed
  **before** `staff/<int:pk>/…` routes; `staff/<int:pk>/toggle-active/` named
  `staff_toggle_active`.
- Template: `staff_form.html` mirrors `production_unit_form.html` (backoffice base, inline
  field errors, double-submit guard). `staff_list.html` gains an "Add user" button in the
  header row and an Active/Inactive badge plus Activate/Deactivate button per row (same
  `data-confirm-*` pattern as the existing role buttons, `:56-72`). No new HTMX fragments
  for create (full-page PRG); row toggle reuses the `#staff-row` partial swap.
- Passwords are never displayed, logged, or emailed. No password-change screen v1 —
  corrections happen via Django admin / `changepassword` until a dedicated reset is
  requested (recorded as deferred below).

## 3. Tests

`apps/settings/tests/test_staff_create.py` (new):

- Admin creates cashier: active, in Cashier group only, `is_staff=False`, can pass
  `staff_required`; manager variant likewise; admin variant gains superuser/staff/group.
- Duplicate username (any case) rejected; mismatched passwords rejected; weak password
  rejected by validators.
- Manager and cashier get 403 on both new endpoints; anonymous redirects to login.
- Self-deactivation refused; deactivated user cannot authenticate; role removal flow
  untouched.

## 4. Docs (same task)

- `docs/workflows/backoffice.md` settings/staff section: create + toggle documented.
- `FEATURES.md` A1 #3: append "Admins create logins and toggle active status from the
  User-roles page."
- Deferred explicitly: self-service password change/reset, bulk import, avatar upload
  here (exists on profile), audit of who created whom (covered by no log — flag if
  wanted, not built).
