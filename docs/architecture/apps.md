# App Responsibilities

## App Map

| App | Owns | Important implementation | Depends on |
|---|---|---|---|
| `apps.users` | `CustomUser`, roles, profile/avatar, auth customization | `models.py`, `signals.py`, `forms.py`, `adapter.py` | allauth, Django auth |
| `apps.settings` | singleton restaurant configuration and production stations | `Restaurant`, `ProductionUnit`, `views.py` | inventory, menu, users, accounting (GL FKs) |
| `apps.inventory` | item master, warehouses, bins, PWAC ledger, stock documents | `models.py`, `services.py`, `views.py` | settings, menu through validation, accounting (GL FKs) |
| `apps.menu` | menus, priced menu lines, add-ons, variant links | `models.py`, `views.py`, `management/commands/seed_menu_catalog.py` | inventory |
| `apps.payments` | payment method master and GL mapping | `models.py`, `views.py`, `forms.py` | accounting (LedgerAccount FK) |
| `apps.staff` | opening/closing shift documents and drawer reconciliation | `models.py`, `services.py`, `views.py` | users, payments, orders, settings, accounting (variance JE) |
| `apps.orders` | orders, order lines/payments, KOT/BOT snapshots, POS orchestration | `models.py`, `services.py`, `views_pos.py`, `views.py` | inventory, menu, payments, settings, staff, users, accounting (order GL) |
| `apps.accounting` | chart of accounts, GL entries, journal entries, fiscal years, supplier payables | `models.py`, `services.py`, `views.py` | orders, payments, settings, inventory (read-side) |
| `apps.reports` | Daily P&L snapshot, P&L settings, sales reports, POS register, GL/trial balance/simple P&L | `models.py`, `services.py`, `views.py`, `sales_reports.py`, `sales_breakdown_reports.py`, `accounting_reports.py` | orders, inventory, staff, accounting (fiscal year), settings |
| `apps.web` | role routing, public auth shell, shared middleware/context/template tags | `views.py`, `middleware.py`, `context_processors.py` | users, inventory navigation |
| `apps.utils` | timestamp base model and styled forms | `models.py`, `forms.py` | Django only |

## Implementation Index by App

### `apps.users`

- URLs: `users/urls.py` mounts profile display and avatar upload under `/users/`.
- Views/forms: `users/views.py`, `CustomUserChangeForm`, `UploadAvatarForm`, and allauth form overrides in `users/forms.py`.
- Models/helpers: `CustomUser` role/avatar properties live in `users/models.py`. Image and email helpers live in `users/helpers.py`.
- Signals: `users/signals.py` handles group-cache invalidation, primary-email update, and avatar file cleanup.
- Templates/frontend: `templates/account/*`, `templates/web/pending_approval.html`, and shared `logout.js` behavior.
- Side effects: `post_migrate` creates role groups. Avatar replacement/deletion removes files.

### `apps.settings`

- URLs: `settings/urls.py` exposes Restaurant settings, staff role list/assignment, and ProductionUnit CRUD under `/backoffice/settings/`.
- Models/forms: `Restaurant` and `ProductionUnit` live in `settings/models.py`. `RestaurantForm` and `ProductionUnitForm` live in `settings/forms.py`.

- Services/signals: no dedicated service module or model signals. Model `clean()` performs cross-app configuration checks.
- Templates/frontend: `templates/backoffice/settings/*`. Staff rows and production-unit deletion use HTMX. Filters use Alpine or normal navigation.
- Side effects: Restaurant validation queries open orders and draft stock documents. Role assignment modifies Django group memberships.

### `apps.inventory`

- URLs: `inventory/urls.py` exposes masters, stock documents, formset row endpoints (including item UOM conversions and purchase-receipt UOM meta/preview), ledger, and balance views under `/backoffice/inventory/`.
- Models/forms: all inventory entities live in `inventory/models.py` (including `ItemUOMConversion`). ModelForms and inline formsets live in `inventory/forms.py`.
- Services: `inventory/services.py` owns Stock Entry, Stock Reconciliation, Purchase Receipt submit/cancel, and voucher reversal. Purchase-receipt submit converts as-bought qty into stock UOM and blends WAC on the as-bought amount.
- Templates/frontend: `templates/backoffice/inventory/*`. Alpine toggles purpose-specific fields and the UOM-conversion formset. HTMX adds/removes formset rows and refreshes receipt UOM/preview widgets.
- Signals/startup: `InventoryConfig.ready()` seeds UOMs and ItemGroups after migrations. No model signal receivers exist.
- Side effects: posting updates SLEs, Bins, and last purchase rates. Cancellation posts reversals.

### `apps.menu`

- URLs: `menu/urls.py` exposes Menu, MenuItem, ItemAddOn, and ItemVariant CRUD under `/backoffice/menu/`.
- Models/forms: `menu/models.py` and `menu/forms.py`. Model validation reaches into inventory Items and active menu membership.
- Services/signals: no service module or model signals. Views save forms directly.
- Management: `menu/management/commands/seed_menu_catalog.py` atomically seeds master/menu data and links the active menu.
- Templates/frontend: `templates/backoffice/menu/*`. Delete controls use HTMX `#app-content` swaps.
- Side effects: making an Item non-sales deletes its add-on relationships through `Item.save()` in inventory.

### `apps.payments`

- URLs: `payments/urls.py` exposes payment mode and GL mapping CRUD under `/backoffice/payments/`.
- Models/forms: `ModeOfPayment`, `PaymentGLMapping`, `ModeOfPaymentForm`, and `PaymentGLMappingForm`.
- Services/signals: no service module or signal receivers. `orders.services.settle_order()` consumes this app's master rows.
- Templates/frontend: `templates/backoffice/payments/*`. Ordinary forms are full-page. Mapping deletion uses a confirmation-aware POST.
- Side effects: mode enable/default changes affect shift opening and POS checkout availability. Mapping deletion can make a mode fail settlement.

### `apps.staff`

- URLs: `staff/urls.py` exposes opening/closing lists, drafts, details, submit, and cancel under `/backoffice/staff/`.
- Models/forms: opening/closing documents and child payment rows live in `staff/models.py`. Forms live in `staff/forms.py`.
- Services: `staff/services.py` owns opening, closing-draft creation, expected totals, payment aggregation, and close submission.
- Templates/frontend: `templates/backoffice/staff/*` and `templates/pos/close_shift.html`. POS close uses Alpine previews and HTMX submission.
- Signals: no staff model signals.
- Side effects: close submission aggregates orders/payments and links the opening to the closing. Canceling a close does not reopen the opening. Since Phase 6, a short/excess variance posts a linked JournalEntry atomically with the close. The close fails closed when the matching account is unconfigured. Canceling a close reverses that journal.

### `apps.orders`

- URLs: `orders/urls.py` exposes backoffice orders/KOTs. `orders/pos_urls.py` exposes the complete POS route set under `/pos/`.
- Models/forms: orders, lines, payments, KOT snapshots, audit events, and the sequence counter live in `orders/models.py`. The cancellation form is in `orders/forms.py`.
- Services: `orders/services.py` owns draft/cart/settlement/cancel/delete/return (including `submit_return`), reservations, KOT creation/dispatch, and history query construction.
- Printing: `orders/printing.py` is the current success-only print interface.
- Templates/frontend: `templates/pos/*`, `templates/pos/partials/*`, and `templates/backoffice/orders/*`. `views_pos.py` selects inline fragments. `order-details-drawer.js` owns history drawer presentation.
- Signals/startup: no order model signals or AppConfig startup behavior.
- Side effects: order services write payment, stock, KOT, audit, reservation, receipt-print, and session-related state. Since Phase 6, settlement also posts GL (income, payment, round-off, COGS) via `accounting.services.post_order_gl`. Returns post refund GL rebuilt from the returned lines via `post_refund_gl`.

### `apps.accounting`

- URLs: `accounting/urls.py` exposes the dashboard, chart of accounts, journal entries, read-only GL entries, and fiscal years under `/backoffice/accounting/`, all behind the manager gate.
- Models/forms: `LedgerAccount`, `FiscalYear`, `GLEntry`, `JournalEntry`, and `JournalEntryAccount` live in `accounting/models.py`. Forms live in `accounting/forms.py`. Journal rows use an inline formset.
- Services: `accounting/services.py` owns order settle GL (`post_order_gl`), refund GL (`post_refund_gl`), and shift-close cash variance posting (`post_cash_variance_gl`).
- Templates/frontend: `templates/backoffice/accounting/*`. The chart of accounts is a recursive tree with expand/collapse. Opening journals go through a read-only review screen before submit. Journal entries use the standard formset add/remove pattern. GL entries are a filtered read-only table.
- Side effects: `GLEntry` is immutable. Reversal postings mark originals cancelled and write mirror rows dated the cancellation day. `JournalEntry.submit()` posts to the GL. `cancel()` posts reversals dated today. `amend()` copies a cancelled entry into a new draft dated today. It allows one amendment per cancelled entry.
- Management: `accounting/management/commands/seed_chart_of_accounts.py` idempotently seeds the chart and current fiscal year, and fills Restaurant/warehouse/production-unit/payment GL FKs only when they are currently null.

### `apps.reports`

- URLs: `reports/urls.py` exposes the Daily P&L register, draft/detail/submit/cancel/amend, HTMX preview and formset row endpoints, and P&L settings under `/backoffice/reports/`. It also exposes query reports (sales, POS register, GL, trial balance, simple P&L). All report routes sit behind the manager gate.
- Models/forms: `PnLConfiguration` singleton, `PnLMaterial`, and `PnLRecurringExpense` live in `reports/models.py`. `DailyPnL` and the snapshot/input children live in `reports/pnl_models.py`. Query reports have no models.
- Services: `reports/services.py` builds the three-column Daily P&L statement (`compute_daily_pnl`) and freezes it on submit (`submit_daily_pnl`). Submit does not post GL. Daily P&L source queries live in `reports/sources.py`. Sales aggregations live in `reports/sales_reports.py` (period, average bill, cancelled) and `reports/sales_breakdown_reports.py` (item, employee, service, time). GL/trial balance/simple P&L lives in `reports/accounting_reports.py`. The POS register lives in `reports/register_reports.py`. Views are `views.py` (Daily P&L) and `report_views.py` (query reports).
- Templates/frontend: `templates/backoffice/reports/*`. The Daily P&L statement partial is shared by draft preview and submitted detail. Query reports are GET filter forms plus tables.
- Side effects: none on other apps. Recurring rates are snapshotted onto the submitted Daily P&L, so later settings edits do not rewrite history.

### `apps.web`

- URLs: `web/urls.py` exposes the root role-routing view (signed-out visitors redirect to sign-in), backoffice dashboard, and pending approval. The POS lives at `pos:pos_home`.
- Views/utilities: `web/views.py`, `middleware.py`, `context_processors.py`, `meta.py`, and template tags under `web/templatetags/`.
- Services/forms/signals: no business service or model form. Middleware and context processors are the cross-cutting layer.
- Templates/frontend: `templates/web/*`, `templates/web/app/app_base.html`, and global `site.js` imports.
- Side effects: middleware applies route gates and serializes Django messages into HTMX triggers. Context processors query metadata and the item-group count.

### `apps.utils`

- URLs/views/templates: none. It is a shared code package. Django does not install it as an app.
- Models/forms: `BaseModel`, `StyledModelForm`, `active_choices`, and `add_formset_row`/`remove_formset_row` in `utils/models.py` and `utils/forms.py`. The row endpoints rebuild bound formsets from posted data. Tailwind widget constants also live there.
- Rounding: `utils/rounding.py` owns every money mode: `money()` (2 dp, half-even), `cash_round()` (whole naira, half-up), `percent()` (3 dp, half-even), and the shared `TWO_PLACES` quantity constant.
- Side effects: form initialization styles widgets and preserves current disabled choices in select querysets.

## Management Commands and Background Work

- `users`: `promote_user_to_superuser` changes a named user's Django superuser/staff flags.
- `menu`: `seed_menu_catalog [--force]` seeds items, variants, menu lines, add-ons, and the active menu in one transaction.
- `inventory`: `backfill_item_images` assigns the default media image to items with no image.
- `orders`: `seed_pos_setup` creates the Restaurant/warehouse/payment/production-unit configuration chain, invokes the chart-of-accounts seed first, and seeds the menu when needed.
- `accounting`: `seed_chart_of_accounts` seeds the chart, current fiscal year, and GL wiring (idempotent).
- `web`: `send_test_email` exercises the configured email backend.
- There is no background worker or task scheduler. All application work runs in the request cycle and in management commands.

## Users

`apps/users/models.py` extends `AbstractUser` with avatar storage (`ImageField`) and plain role properties derived from Django groups via `groups.filter(...).exists()`. `apps/users/decorators.py` turns them into `backoffice_required`, `manager_required`, `staff_required`, and `admin_required` view decorators. `UserConfig.ready()` seeds three role groups after migrations and imports `apps/users/signals.py`. Profile editing is in `apps/users/views.py`. Allauth owns login and logout routes; signup is closed.

## Settings

`Restaurant` is the single installation record. It points to the active menu, the central Store warehouse, and the Bar/POS warehouse. It also controls the open-draft cap and full-history permission. `ProductionUnit` represents FOOD/Kitchen or DRINKS/Bar routing and stores printer metadata. Staff role assignment is also hosted here in `apps/settings/views.py`.

## Inventory

Inventory is both a master-data app and a posting engine. `Item` is shared by menu and order lines. `Bin` is the current item/warehouse snapshot. Ledger services are its only writers, and the admin surface is view-only. `StockLedgerEntry` is the movement history: unflagged inserts and all updates are rejected. Stock Entry, Stock Reconciliation, and Purchase Receipt are draft documents. Their service functions post ledger movements, then save status with a private submit/cancel flag.

## Menu

`MenuItem.rate` is the POS selling price. Menu lines snapshot the displayed name and link to `inventory.Item`. `ItemAddOn` and `ItemVariant` are relationship models. Add-ons are resolved in the active menu at POS time. The current POS has an add-on dialog but no parent-item variant selection flow.

## Payments

The payments app does not own sale payment rows. It owns `ModeOfPayment` and one-to-one `PaymentGLMapping`. `OrderPayment` lives in `apps/orders/models.py`, and `settle_order()` creates it.

## Staff

`POSOpeningEntry` and `POSClosingEntry` define the global shift window. Child opening/closing rows snapshot each payment mode. `staff.services` computes expected drawer values from submitted orders and order payment rows.

## Orders

Orders are the integration hub. `Order` references the shift and optional stock warehouse. `OrderItem` references inventory and menu snapshots. `OrderPayment` references payment modes. `KOT` references production units. Order services call inventory and staff models directly. The POS is implemented in `views_pos.py` and uses the same order services as backoffice actions.

## Web and Utilities

`apps/web` is the shell rather than a business domain. Its middleware applies route-level access gates and forwards Django messages into HTMX headers. `apps/utils` is a shared base/form package and is not a separate installed Django app. Most domain models inherit `BaseModel`. `users.CustomUser` inherits Django's `AbstractUser` instead.

## Views, Forms, Templates, and Services

The normal pattern is function-based views plus ModelForms. Inventory document creation uses inline formsets and HTMX row add/remove endpoints. Complex cross-record mutations are service functions, generally decorated with `transaction.atomic`. POS responses use inline partials from `templates/pos/*.html`. Backoffice pages mostly use full-page templates with selected HTMX fragments.

## Missing or Distributed Domains

- Sales reporting for operations lives in `apps.reports` query reports. The order dashboard, POS history, stock reports, and shift totals remain as their own surfaces.
- Receipts are represented by order fields and a print interface. There is no receipt model or renderer.
- Customers are represented by `customer_name` and customer indices. There is no customer master.
- Printing configuration is in `ProductionUnit`, while print calls are in `apps/orders/printing.py`.
