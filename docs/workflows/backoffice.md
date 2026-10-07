# Backoffice Workflow

## Access Model

Every backoffice view declares its own role requirement with a decorator from `apps/users/decorators.py`. `LoginRequiredMiddleware` enforces site-wide login. There is no middleware route gate.

| Decorator | Who passes | Backoffice surfaces |
|---|---|---|
| `@backoffice_required` | superuser, Spicy Admin, or Spicy Manager | Dashboard, settings reads, staff list, inventory, menu, payments reads, orders register/detail, KOT register, shifts (opening/closing documents) |
| `@manager_required` | superuser, Spicy Admin, or Spicy Manager | Accounting, reports (Daily P&L and query reports), payments writes, order cancel/return/delete, restaurant settings, production unit writes |
| `@admin_required` | superuser or Spicy Admin | Staff role assignment/removal |

Anonymous users redirect to login. Authenticated users who fail the role test get `403`. See [Authentication and Authorization](auth.md) for the full table including POS surfaces.

## Main Surfaces

| Surface | URL include | Main operations |
|---|---|---|
| Dashboard | `apps.web.urls` | links to modules and setup |
| Settings | `apps.settings.urls` | Restaurant, staff roles, ProductionUnit |
| Inventory | `apps.inventory.urls` | masters, stock documents, stock reports |
| Menu | `apps.menu.urls` | menus, lines, add-ons, variants |
| Payments | `apps.payments.urls` | payment modes and GL mappings |
| Staff | `apps.staff.urls` | opening/closing documents |
| Orders | `apps.orders.urls` | order register, KOT register, cancel, return |
| Accounting | `apps.accounting.urls` | chart of accounts, journal entries, GL entries, fiscal years, supplier payables |
| Reports | `apps.reports.urls` | Daily P&L register, draft/submit/cancel/amend, P&L settings, sales reports, POS register, GL, trial balance, simple P&L |

## Accounting Operations

All accounting pages are manager/admin-only (`@manager_required` raises 403 directly). The chart of accounts is a tree page with a create/edit form per account. The account form enforces group/leaf and root-type rules via `LedgerAccount.clean()`. Journal entries use a prefixed inline formset of account rows. HTMX row add/remove endpoints rebuild the posted formset and re-render the accounts partial, per the inventory formset pattern. An empty formset is rejected. Drafts are edited and submitted from the detail page, where submit/cancel/amend are confirmation-aware POSTs. `JournalEntry.submit()` posts balanced rows to the GL. `cancel()` posts reversals dated the cancellation day. `amend()` copies a cancelled entry into a new draft dated today (one amendment per cancelled entry). GL entries are read-only with account/voucher-type/cancelled filters. Fiscal years are simple CRUD pages. All accounting models are registered in Django admin (`accounting/admin.py`). `GLEntry` and `JournalEntryAccount` are fully read-only there, and journal entries cannot be deleted from admin.

## Daily P&L Operations

All reports pages are manager/admin-only (`@manager_required`, view 403). P&L settings hold the business-day start hour, electricity rate, depreciation, cash-variance toggle, material catalog, and recurring expense templates. Creating a Daily P&L saves a draft for one business date (one draft / one submitted per date). The draft form accepts meter readings, material quantities, ad-hoc expenses, and an optional employee-cost override. HTMX "Refresh preview" saves the draft and returns the three-column statement partial. Submit freezes lines and totals inside one atomic block and does not post GL. Cancel leaves the snapshot on file. Amend copies inputs into a new draft that recomputes.

The full idea-to-code explainer is [Daily P&L](daily-pnl.md).

## Inventory Operations

Create views save a parent plus inline formset in an atomic block. HTMX endpoints add/remove formset rows by rebuilding posted data and returning the inline partial. Submit and cancel endpoints call inventory services and redirect to detail with validation messages.

## Menu and Product Operations

Menu and item forms save directly after validation. Model `clean()` enforces cross-app sellability and warehouse rules when it is invoked by forms. A direct `.save()` does not automatically call `full_clean()`, so model validation is most reliable through forms or explicit service validation.

## Order Operations

The order register filters by invoice/customer/order number, status, order type, and posting-date range (`from`/`to`). The same filters apply to its CSV export. Detail prefetches lines, payments, and tickets. Backoffice cancellation requires manager/admin/superuser and calls `cancel_sent_order()` (sent drafts only). Unsent drafts are deleted via `order_delete`, which tombstones them as `DISCARDED`. Return creation requires the same roles and calls `make_return()`. The negative draft can be edited (qty, drop line, wastage), then submitted through `order_return_submit` → `submit_return()`, which restores drink stock and writes proportional refund rows.

## Settings and Staff

Restaurant and ProductionUnit changes are manager/admin-only at view level. Restaurant validation blocks unsafe warehouse changes when reserved orders or draft stock documents exist. Admins create logins (username, name, password, one role) from the User-roles page and toggle active status per row. Deactivation blocks sign-in without deleting history, and self-deactivation is refused.

Staff role buttons use target-specific HTMX row replacement. Searches target `staff-table-body` and return the row collection. Role mutations target `staff-row-<pk>` and return one updated row. Boosted requests with another target continue through the full-page or redirect path, so a row fragment never replaces the document body. The role value is not validated before removing existing role groups, so an unknown role can strip roles. This is documented here as a risk. This file does not change it.

## Reporting Surfaces

`apps.reports` owns the Daily P&L document and the query-based sales, POS register, general ledger, trial balance, and simple P&L reports. See [Query reports](query-reports.md).

Four registers offer a `?export=csv` download on the same URL, with the same gates and filters. The download uses stdlib csv, UTF-8 with BOM, raw two-decimal money, and a 50,000-row cap. The registers: orders (`orders:order_list`), GL entries (`accounting:gl_entry_list`), stock ledger (`inventory:stock_ledger_list`), and Daily P&L list (`reports:daily_pnl_list`). Filenames carry the register, date, and active filters.

Current aggregates elsewhere:

- order dashboard: today count, paid count, cancelled count, revenue, recent orders, pending tickets.
- inventory dashboard: catalog, stock, and purchasing navigation with item and UOM counts.
- stock ledger and balance filtered lists.
- staff closing totals and per-mode variance.
- POS history query with date/status/payment/order-type filters.
