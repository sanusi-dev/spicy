# Open Findings — Spicy

Verification baseline: `main` at commit `440cee6` (2026-10-03). Only issues that still exist appear here. This document drops every fixed item and every incorrect claim from the previous document. The most important removal is F1, the POS `TransactionManagementError`. Commit `3039187` fixed it, and both views now wrap the lock in `transaction.atomic()`.

---

## Bugs and correctness issues

### F6 — Settlement prints inside the money/stock transaction and add-item holds its lock across a template render

**What is wrong.**

1. `settle_order` is one `@transaction.atomic` block. If nobody sent the order, the service builds the KOT rows and calls `dispatch_tickets`. `dispatch_tickets` calls `printing.print_ticket`. This print happens before the payment rows, the drink-reservation conversion, the status change and the GL. When the real print agent arrives, a later failure rolls the database back after the paper is already out. The kitchen cooks an order that the system never recorded. The Send flow already does this correctly. It creates tickets inside a small atomic block and prints after.
2. `pos_order_add_item` takes the order row lock and renders the add-on dialog inside the `atomic()` block. It returns before commit and holds the lock for the render. The error paths of `pos_order_update_meta` and `pos_order_update_item` also render inside the lock block.

**Where:**
- `apps/orders/services.py:152-236` (`settle_order`). `dispatch_tickets(created)` is at `:202`.
- `dispatch_tickets` prints at `:633` inside its own atomic block (`:629-639`). `apps/orders/printing.py:14-16` is currently a stub that always succeeds.
- `apps/orders/views_pos.py:893` opens the atomic block. The add-on dialog renders and returns at `:961-976`. Error renders sit inside the atomic block at `:875` and `:1025`.
- Shape to copy: `pos_order_sync` (`apps/orders/views_pos.py:1054-1066`).

**Fix.** Create the KOT rows inside `settle_order` and return them. Print them in `pos_order_settle` after the commit, as `pos_order_sync` does. Resolve the add-on dialog before the code takes the lock, or render the dialog after the atomic block exits. Keep `print_status` (`PENDING`/`PRINTED`) as the retry mechanism. Surface `print_failures` as the Send flow does.

### F7 — Daily P&L "Refresh preview" blanks the statement

**What is wrong.** `_statement.html` iterates over `rows`. The first page load passes `rows=preview.lines`. The preview endpoint passes `preview`, `pnl` and `food_usage_counted`, but it does not pass `rows`. The table body therefore disappears after Refresh. If the preview form is invalid, the endpoint returns the whole form page into the small `#pnl-preview` target. That response nests forms and breaks HTMX from then on.

**Where:**
- `templates/backoffice/reports/_statement.html:22` — `{% for line in rows %}` (the error box for `preview_error` exists at `:7-8`).
- `apps/reports/views.py:199-203` — the success branch does not pass `rows`.
- `apps/reports/views.py:204-208` — the invalid-form branch renders `daily_pnl_form.html` into the preview target.

**Fix.** Pass `rows=preview.lines` in the success branch. For the invalid case, render `_statement.html` with `preview_error`. Keep `food_usage_counted`, so the amber banner still displays. Add a template test. It posts the preview URL and asserts that a statement line is present, not only a 200 response.

### F8 — Food + Drinks gross profit does not add up to Total

**What is wrong.** Department columns receive only the amounts that a department can claim. `_split` returns `(0, 0, amount)` for an unassigned amount. The Total column also subtracts unallocated direct costs and the round-off adjustment. The page looks like "Food + Drinks = Total". The numbers disagree whenever an electricity amount, a material amount or an ad-hoc amount has no department. Prime cost has the same shape. The department columns omit employee costs.

**Where:**
- `apps/reports/services.py:45-50` (`_split`), `:158-204` (direct costs and `gp_food`/`gp_drinks`/`gp`), `:226-227` (prime cost).

**Fix.** Keep the department-only figures. Add a visible "unallocated direct costs" line to the Total column for electricity, unassigned materials and round-off. Define `gp = gp_food + gp_drinks − unallocated − round_off`, so a reader can check the column arithmetic. For prime cost, display the employee split in the department columns or leave the cells blank (`—`). Do not invent a 50/50 allocation. Add a test for a day with electricity.

### F9 — A refund can finish "submitted" while its GL posts nothing

**What is wrong.** `post_refund_gl` returns without a message when the source order has no live GL:

```python
if not GLEntry.objects.filter(voucher_type="Order", voucher_no=source.invoice_number, is_cancelled=False).exists():
    return
```

The first idempotency check works as intended. It detects that the return GL already posted. The second check turns a data problem into silence. The return order still ends as `SUBMITTED`. The service may restore stock with no refund in the books.

**Where:** `apps/accounting/services.py:310-394`, silent return at `:322-323`.

**Fix.** Raise `ValidationError` when the source order never posted. `submit_return` is atomic. A raise rolls back the stock restore and the status change, and the operator sees the error.

### F10 — Two different rules decide whether a shift can be opened

**What is wrong.** `open_shift()` refuses to run without a `Restaurant` row. The backoffice path creates and submits an opening entry without that check. `POSOpeningEntry.submit()` locks the Restaurant row but does not require it. The result can be an "open" shift that the POS refuses for orders and the close service refuses to close.

**Where:**
- POS service: `apps/staff/services.py:117-123`.
- Backoffice draft writer: `apps/staff/views.py:130-157`. Model submit: `apps/staff/models.py:91-125` (lock at `:99`, no `None` check).
- The close service also requires Restaurant settings: `apps/staff/services.py:151-153`.

**Fix.** Add a row check after the Restaurant lock in `POSOpeningEntry.submit()`. When no row exists, raise the same "Restaurant settings are not configured." error. Optionally fail earlier with a friendly message in `_save_opening_entry`. Keep the lock. It serialises the one-open-shift race.

### F11 — The URL config registers `/pos/` twice, and callers still use the shadowed name

**What is wrong.** `spicy/urls.py` mounts the real POS first. It then includes `apps.web.urls`, which declares `pos/` again and points at a context-free view. `{% url 'web:pos_index' %}` reverses to `/pos/`. That URL reaches the real POS only because of include order. A reorder of the includes sends cashiers to an empty shell.

**Where:**
- `spicy/urls.py:22-23`, `apps/web/urls.py:10`, `apps/web/views.py:132-134`.
- Callers: `apps/web/views.py:14`, `templates/web/app/app_base.html:168`, `templates/backoffice/dashboard.html:10`, `apps/web/tests/test_auth_flow.py:42,101`. (`templates/web/landing.html` no longer exists.)

**Fix.** Delete `web.views.pos_index` and the `pos/` route. Point every caller at `pos:pos_home`. Update the auth-flow tests. Check the expected behaviour of each caller. `pos:pos_home` has the `@staff_required` decorator.

### F12 — A cashier can cancel their own sent order with no second person

**What is wrong.** The creator of an order can cancel that order after it goes to the kitchen. The form requires only a dropdown reason. The cashier can collect cash off-record, cancel the order and serve the food. The shift still draws down cleanly, because no payment ever existed. Ownership scoping blocks the cancellation of *someone else's* draft. It does not block the cancellation of your own.

**Where:** `apps/orders/views_pos.py:1159-1202`, `apps/orders/services.py:240-284` (`can_be_accessed_by` + reason, no manager requirement).

**Fix — decide and record in `FEATURES.md`/`PLAN.md` first:**
1. Require manager approval to cancel a sent order. The service raises unless the actor is a manager or an admin. The POS displays a locked state.
2. Alternatively, keep cashier cancellation, but surface the shift's cancellations at close. The close screen displays a blocking review item, and the Z-report counts the cancellations.

Option 2 alone is weaker, because the kitchen has already cooked. A threshold (for example, above ₦X) is a reasonable middle ground. A "request cancellation" workflow adds a new `Order` state. It needs the PLAN.md protocol.

### F14 — The POS cannot close a large variance

**What is wrong.** `submit_closing_entry` requires a non-empty `variance_note` **and** a manager actor when the variance exceeds `variance_approval_threshold`. The backoffice close form has a `variance_note` input. The POS close surface has no input and no threshold warning. The POS view writes only `closing.remarks`. A threshold therefore makes above-threshold closes impossible from the POS. The manager sees "A manager must provide a variance note" with nowhere to type it.

**Where:**
- Requirement: `apps/staff/services.py:223-230`.
- POS view that never posts `variance_note`: `apps/orders/views_pos.py:529-538`.
- POS template with no note input and no threshold warning: `templates/pos/close_shift.html:32-106`.
- Backoffice input exists at `apps/staff/views.py:284`.

**Fix.** Add the `variance_note` input and a server-derived threshold warning to the POS close form. Post the note into `closing.variance_note` before `submit_closing_entry`, as the backoffice does. Alternative: block POS closes above the threshold with a clear message that points to the backoffice. That option is less friendly. Update the close-workflow docs in either case.

### F16 — P&L drink COGS follows today's item department but sales follow the sale-date snapshot

**What is wrong.** An order line snapshots `department` at sale time. The sales columns use that snapshot. The drink COGS query filters on `StockLedgerEntry.item__department=DRINKS`, which is the *current* classification. A reclassification moves historical cost between columns while the revenue stays put. Nothing flags the change.

**Where:**
- Sales snapshot: `apps/orders/models.py:470-481`. Sales query with fallback: `apps/reports/sources.py:49-58`.
- Live-department COGS filters: `apps/reports/sources.py:72-77` and `:92-97`. The wastage branch at `:114` filters the order-line snapshot instead.
- The ledger has no department column: `apps/inventory/models.py:292-334`. However, `voucher_detail_no` stores the `OrderItem` pk for POS sale and return rows (`apps/orders/services.py:1041,1149`).

**Fix (choose one):**
- Cheap fix: derive drink COGS through the order line (`voucher_detail_no` → `OrderItem.department`). Guard against SLE rows that do not come from an order.
- Full fix: add a `department` snapshot field to `StockLedgerEntry`. Populate the field at creation and backfill it with a data migration after the schema migration.

Both options make reports depend only on data recorded at event time.

### F17 — Insecure boot defaults and a production settings file that is never selected

**What is wrong.**
- `SECRET_KEY` falls back to a committed value. The same value ships in `.env.example:9`.
- `DEBUG` defaults to `True`. `ALLOWED_HOSTS` defaults to `["*"]`. `SPICY_DEV_ADMIN_BYPASS = DEBUG` turns off the admin document locks.
- `spicy/settings_production.py` exists, but nothing selects it. `Makefile:24` runs `manage.py runserver` with the default module. The base config has no HSTS settings, no `NOSNIFF` setting, no referrer policy, no secure-cookie flags and no CSP.

**Where:**
- `spicy/settings.py:16,18,22,24`
- `spicy/settings_production.py:1-12`
- `Makefile:23-24`

**Fix.** Drop the committed `SECRET_KEY` fallback. Fail fast when the key is missing, and document the key in the setup steps. Make the safe values the defaults. Let `.env` opt into debug. Add the standard hardening flags to the base settings, or wire the production module into the deployment command and document it. Do not overwrite the developer's `.env`. Change only the defaults.

### F18 — Anyone on the network can create a login

**What is wrong.** The project enables allauth signup and leaves it open. `SpicyAccountAdapter` is an empty subclass of `DefaultAccountAdapter`. In the installed allauth 65.18.0, `is_open_for_signup()` returns `True`. New accounts become active immediately. The only reaction is an admin email.

**Where:**
- `apps/users/adapter.py:1-5`, `spicy/settings.py:169-189`, `templates/account/login.html:24` (signup link), `apps/users/signals.py:11-27`.

**Fix.** Override `is_open_for_signup()` to return `False`. You can gate it behind an env flag for first-time setup. Delete the signup link. Keep backoffice `settings.staff_create` as the only provisioning path. `promote_user_to_superuser` covers the bootstrap case.

### F19 — Remaining security-hardening items

| Item | Where | Current state | Fix |
|---|---|---|---|
| Email verification off, non-unique email allowed | `spicy/settings.py:176,189` | `ACCOUNT_EMAIL_VERIFICATION="none"`. `ACCOUNT_UNIQUE_EMAIL=False`. | Login uses the username. Set `ACCOUNT_UNIQUE_EMAIL=True` to avoid ambiguous password resets, or document why the project permits duplicates. |
| Weak seed credentials | `apps/orders/management/commands/seed_test_data.py:105-127` | The seed creates `cashier/pos1234` and `manager/manager1234`. `set_password` bypasses the validators. | Restrict seeds to DEBUG. Alternatively, generate random passwords and print them once. |
| Role management can silently strip a role | `apps/settings/views.py:110-135`, `apps/settings/urls.py:10` | `_apply_role` clears all three groups first. An unrecognised `<str:role>` leaves the user with no role. No last-admin guard and no self-demotion guard exist. | Check `role in {admin, manager, cashier}`. Block the demotion of the last admin or of yourself. `staff_toggle_active` already guards self-deactivation. |
| Avatar validation is extension-only | `apps/users/helpers.py:9-33`, `apps/users/models.py:27-35` | The check uses the name extension and a 5 MB cap. A script renamed to `.jpg` passes. `avatar_url` still calls gravatar.com, which does not work on an offline LAN. | Switch to `ImageField`, which lets Pillow check the content. Drop gravatar or make it opt-in with a local default. |
| `printer_ip` unvalidated | `apps/settings/models.py:314`, `apps/settings/forms.py:144` | A plain `CharField` with a placeholder. No client exists yet. | Check that the value is an IP address or a hostname when the Phase-12 print agent arrives. |
| Personal data / Pegasus leftovers | `spicy/settings.py:239,281,286,311-314` | `DEFAULT_FROM_EMAIL` and `ADMINS` contain a hardcoded personal address. The keywords say "SaaS, django". The logger name is `pegasus`. The metadata image is now the local brand asset, so that part works. | Move the email and admin values to env vars. Update the metadata and the logger names with REF-14. |
| `promote_user_to_superuser` has no confirmation | `apps/users/management/commands/promote_user_to_superuser.py:12-22` | A bare username grants superuser with no confirmation and no log entry. | Add a `--yes` confirmation and a log line. Low priority, because the command runs only from a shell. |

### F20 — Submitted shift documents remain editable at the ORM layer

**What is wrong.** `POSClosingEntry.save()` has no status guard. Neither child row type has an immutability guard. A direct ORM write can overwrite the `closing_amount` of a submitted closing. That write leaves no audit event and no GL correction. The Z-report no longer matches its variance journal. The journal-row race from the same report no longer exists. Only the document guards are missing.

**Where:**
- `apps/staff/models.py:232-237` (`POSClosingEntry.save`), `:277-311` (`ClosingPayment`), `:150-170` (`OpeningPayment`).

**Fix.** Reject a `POSClosingEntry` save when the stored status is `SUBMITTED` or `CANCELLED`. Permit the save only behind the service-flag pattern from `Order` and `JournalEntry`. Child rows reject saves when the parent is no longer `DRAFT`, and they reject deletion. Make any admin registration fully read-only. Add immutability tests like the tests in `test_order.py`.

### F22 — Small fixes

| # | Where | Issue |
|---|---|---|
| Audit metadata | `apps/orders/services.py:127-131` | `ITEM_QUANTITY_CHANGED` stores the **line** pk under `item_id`. The `remove` branch at `:112-120` stores the real item id. Item-based reconciliation attributes the change to the wrong item. |
| NIT-1 | `apps/accounting/services.py:18-35, 268-272` | Three near-identical account lookups per department, one query per call site. One cached helper avoids the extra queries. |
| NIT-2 | `apps/reports/views.py:62-67`, `apps/accounting/views.py:254` | Raw GET values go into `business_date__gte/lte` and `int(account_id)`. Malformed input causes a 500. Reuse `report_filters.parse_date` and the int parser. |
| NIT-3 | `apps/web/context_processors.py:29-33` | `ItemGroup.objects.count()` runs on every backoffice request only to decorate navigation. Pass the count in or cache it. |
| NIT-4 | `assets/javascript/order-details-drawer.js:80` | `posModalDialog` lives in a file named after the history drawer. Four dialogs use it: payment, add-on, variant and cash-out. |
| NIT-5 | `templates/pos/close_shift.html:50-59` vs `templates/pos/partials/gates/no_shift.html:36-45`. Spinner blocks are in `totals.html:86-126` and `order_history_detail.html:17-49,307` | The payment-mode icon switch and the spinner markup repeat. |
| NIT-6 | `close_shift.html:11`, `no_shift.html:6`, `cash_out_dialog.html:12`, `payment/dialog.html:55,87,91,99` | Four copies of a `money()` helper with `toLocaleString('en-NG')` exist. |
| NIT-7 | `templates/pos/index.html:26-28` | The `payment_dialog` partialdef only includes `dialog.html`. The extra level is needless. |
| NIT-8 | `templates/pos/index.html:30`, `catalog/grid.html:5,31` | `#add-on-dialog-container` also hosts variant dialogs. The name is misleading. |
| NIT-9 | `assets/styles/site-tailwind.css:33` | A stale comment about the DaisyUI removal remains. `app-components.css` no longer exists. |
| NIT-10 | `docs/database/transactions.md:15,18`, `docs/database/queries.md:19`, `docs/architecture/overview.md:53`, `docs/architecture/data-model.md:124` | The docs describe FIFO queues and a nonexistent `_reverse_voucher`. The runtime uses weighted-average WAC. |
| NIT-11 | `apps/orders/services.py:1217-1218` | Anything that is not `FOOD` maps to the bar printer. A NULL or unexpected department becomes a bar ticket with no warning. Make the mapping exhaustive and raise on a bad value. |
| NIT-12 | `apps/reports/report_views.py:12-13` | `_range` is a one-line alias of `report_filters.date_range`. Delete it. |
| Duplication | `apps/orders/models.py:324-347`, `apps/menu/models.py:58-67`, `apps/inventory/models.py:158-186`. The label join is at `apps/orders/services.py:197,558` | The FOOD/DRINKS validation blocks and the department-label join repeat. One helper can serve each. |
| Food usage placement | `apps/inventory/services.py:114` (cycle-breaker import at `:117-118`), `apps/inventory/views.py:1083-1093` | `compute_food_usage` is report logic that lives in the posting module. The food-usage view swallows `ValidationError` and displays ₦0 food sales. Move the function to `apps.reports` (REF-2) and surface the error. |
| Long lines | `payables_models.py:37` (122), `inventory/forms.py:108` (126), `inventory/models.py:803` (122), `menu/models.py:106` (123), `reports/models.py:24` (122), `settings/models.py:128` (145) | These files have lines over the 120-char limit. |
| UOM conversions | `apps/inventory/models.py:219-255` | A user can edit or delete `ItemUOMConversion` rows after use. Historical SLEs keep the old blended WAC with no link back. Guard the rows after first use. |
| Printing stub | `apps/orders/printing.py:14-16` | The stub always succeeds, and no direct test covers the interface because the tests mock it. This is acceptable until the print agent lands. Add a real test at that point. |
| Loading indicators | `templates/pos/order_history.html` | The filter and navigation GETs lack `hx-indicator`. Only `order_history_detail.html` has it in the POS. This is minor, because all mutations use POST. |
| Inline dialogs | `templates/pos/partials/cart/totals.html:55`, `templates/backoffice/orders/order_detail.html:18,27,34` | Inline `onclick="Swal.fire(...)"` calls bypass the guard in `confirm.js`. The draft-delete copy still says the operation deletes the audit trail. That text is stale after the tombstone change. |
| Type hints | Service modules (`orders`, `inventory`, `accounting`, `staff`, `reports`) | No return type hints on service functions. |

---

## Product decisions (not code bugs)

- **F13 — electronic payment references.** `require_payment_reference` ships as `False` (`apps/settings/models.py:47-51`). The code enforces the requirement and uniqueness correctly when the flag is on (`apps/orders/services.py:899,945-949` and `apps/orders/models.py:541-559`). `FEATURES.md` #29 documents the flag as configurable. Decision: flip the default for new installs, or surface the flag in the setup checklist. This is not a code fix.
- **F14 threshold remainder.** A nullable `variance_approval_threshold` means "no gate" by documented design (`PLAN.md` §4.5). The template displays expected amounts before the count, and the spec does not require a blind count. Revisit this only as a deliberate product change.
- **F15 — reconciliation write-offs.** The service now stamps the actor (`apps/inventory/services.py:870-874`). `remarks` is still optional (`apps/inventory/models.py:714`). There is no value threshold and no second approver for `ADJUSTMENT` or `WASTE_DAMAGE`. Proposal: add a nullable `reconciliation_approval_threshold` like the cash-variance threshold. Require remarks above the threshold and a manager check at submit. Record the decision in `PLAN.md` before you implement it. Limit mandatory remarks to adjustments and waste, because daily kitchen consumption is routine.

---

## Possible refactors

Each refactor below is valid, and each was re-checked at HEAD. The previous document's two bad claims are already gone. `can_dispense_change` is in use at `templates/backoffice/payments/mode_detail.html:44`. Migration 0030 does **not** make `department` required. It adds only a `CHECK` constraint, and NULLs remain legal.

| Ref | State | Verified notes |
|---|---|---|
| **REF-1** — one movement/reversal engine | Valid | Transfer cancel reverses snapshotted warehouses. Reconciliation, receipt, and stock-entry receipt cancel share `_reverse_sles_at_current_wac` / `_cancellation_wac_gl_rows`. Transfer cancel still has its own two-warehouse GL. `_post_gl_rows` and `GLEntry.post` also differ. `_resolve_account` (`apps/inventory/services.py`) duplicates `_resolve_required_account` (`apps/accounting/services.py:38`) across apps. Keep the existing lazy-import direction. |
| **REF-2** — move food usage to reports | Valid | `compute_food_usage` and `recipe_plate_cost` (`apps/inventory/services.py:89-254`) post nothing. The `apps.reports.sources` import at `:117-118` breaks a cycle. The view's `except ValidationError: food_sales = 0` (`apps/inventory/views.py:1092-1093`) is the smell. |
| **REF-3** — one inbound line helper | Valid (helper, not merger) | `StockEntryDetail.stock_qty` and `stock_unit_rate` (`:575,:584`) duplicate `PurchaseReceiptItem` (`:876,:883`). The forms, the widgets and `_entry_line_params`/`_receipt_line_params` (`apps/inventory/views.py:448,739`) also duplicate. Keep the two documents. Their GL stories differ. |
| **REF-4** — split cart vs catalog context | Valid | `_render_cart` (`apps/orders/views_pos.py:187-192`) always calls `_build_order_context`. That call loads the menu, variants, add-ons, availability and payment modes even for guest-card and quantity actions. This is a high-frequency win. |
| **REF-5** — lifecycle in services, drop `_allow_*` flags | Valid, gradual | The flags are at `apps/orders/models.py:218-220`, and `_settling` is at `:560`. The KOT-freeze rule repeats in `Order.save`, `_ensure_editable`, `OrderPayment.save/delete` and several views. Replace one transition at a time. Keep the model-level guards as a last resort. |
| **REF-6** — one public GL voucher API | Valid, medium effort | `_merge_rows` nets amounts (`apps/accounting/services.py:185`), while inventory `_post_gl_rows` sums them. `_reverse_gl` is private, but `apps/staff/services.py:299` imports it. `post_refund_gl` merges twice (`:380,:388`). Fix that call while you unify the API. |
| **REF-7** — one Daily P&L statement | Valid, phased | Three representations exist: `Computation`, about 26 `DailyPnL` columns, and `DailyPnLLine` rows. Nothing ever appends `KITCHEN_CONSUMPTION` (`apps/reports/pnl_models.py:267,283`), and a test asserts its absence. Start with the `rows` fix from F7 and delete the dead choice. Column removal is a bigger and riskier change. |
| **REF-8** — canonical calendar querysets | Valid (= old F21) | Window filters repeat in `sales_breakdown_reports.py:23-31`, `accounting_reports.py:157-165,250-258` and `register_reports.py:18-27`. The canonical builder is `submitted_orders` (`sales_reports.py:23-30`). Keep the NULL-department fallback (`apps/reports/sources.py:56`). It is live, not dead. |
| **REF-9** — payables workflow off the models | Valid, low priority | `SupplierInvoice.submit/cancel` and `SupplierPayment.submit/cancel` live on the models (`apps/accounting/payables_models.py:161,196,453,501`). The dashboard does an N+1 query (`apps/accounting/views.py:43`). Keep payables in `apps.accounting`, as `PLAN.md` requires. |
| **REF-10** — one shift writer / `objects.open()` | Valid, phased. After F10. | Staff code and POS code infer the open state from `status=SUBMITTED` and `closing_entry__isnull=True`. Two close implementations exist, with different form prefixes. |
| **REF-11** — one Item variant graph | Valid. Product decision first. | `Item.has_variants`/`variant_of` (`apps/inventory/models.py:112-113`) and `menu.ItemVariant` (`apps/menu/models.py:115-125`) are two graphs. The POS uses both. `Item.save` silently deletes add-ons (`:146`), and `MenuItem.clean` changes `rate` from `last_purchase_rate` (`:68-69`). Pick one graph and migrate. |
| **REF-12** — Restaurant as data, not an oracle | Valid, carefully | `Restaurant.clean()` imports payments, orders and inventory code and blocks warehouse changes under drafts (`apps/settings/models.py:219,251,262`). Those checks are load-bearing. Move them to the owning services without losing them. `load()` (`:203-205`) ignores the unique `singleton_key` (`:11`). |
| **REF-13** — cached roles and collapsed decorators | Valid | `is_admin`, `is_manager` and `is_cashier` each run `groups.filter().exists()` (`apps/users/models.py:39-48`). `_role_required` wraps `@login_required` (`apps/users/decorators.py:7-12`) under the global `LoginRequiredMiddleware`. A per-request cached role set eliminates the N+1. |
| **REF-14** — delete the Pegasus shell | Valid, updated | Still present: open signup (F18), gravatar (`apps/users/models.py:31`), the keywords, the `pegasus` logger and the personal email (`spicy/settings.py:281,286,311-314`). The empty adapter remains, along with orphan pycs for the deleted Branch/Room/Table/Tax tests and migrations. Decide the fate of the `pending_approval` page. The landing page is **already gone** (`d7b5ffc`), and `app-components.css` no longer exists. Also delete `web.pos_index` (F11). |
| **REF-15** — one cart line / order body / nav contract | Valid, go incrementally | `items.html` forks between flat and grouped (`:4`). `order_history_detail.html` is a surface plus a drawer, and the drawer displays a hardcoded ₦0 discount (`:268-270`). The OOB shell navigation repeats on five surfaces. The `#app-content` select/swap remains on four backoffice templates. Inline `onclick` confirmations remain. Start with the shared partials for the drawer and the surface. |
| **REF-16** — delete leftover modes | Valid, minus the two wrong claims | Leftovers: the empty `apps/orders/models/` and `apps/inventory/models/` directories contain only `__pycache__`, and a stray `__init__.py` would shadow `models.py`. The `*ModelForm: pass` subclasses are empty in `payments`, `staff`, `reports`, `accounting`, `inventory`, `menu` and `payables`. `SettingsModelForm` forks `StyledModelForm` (`apps/settings/forms.py:24` vs `apps/utils/forms.py:31`). Orphan pycs remain. The "Coming soon" discount UI is at `templates/pos/partials/payment/dialog.html:22`. Only seed code and tests call `discard_order`. |
| **REF-17** — catalog add-item rules in the service | Valid | `pos_order_add_item` does variant, add-on and menu resolution in the view. Add-on resolution also repeats in `_variant_add_ons` (`apps/orders/views_pos.py:785`). The active-card session-dict boilerplate (`isinstance(cards, dict)`) appears in six places. `apply_add_on_line` is the natural home. |
| **REF-18** — payment policy out of `OrderPayment.save` | Valid, with a correction | The reference rules, the KOT lock and `requires_payment_reference` live in `OrderPayment.save` (`apps/orders/models.py:529-568`). The claim "default-mode uniqueness enforced three times" is inaccurate. The three places are the `UniqueConstraint`, `ModeOfPayment.clean`, and a save guard that covers only *unsetting* the default. Trust the constraint and keep one Python guard. |

**Suggested order.** Do REF-6 and REF-1 next. The remaining fixes and refactors can go with related work. Cosmetic refactors (REF-15, REF-16 and the NITs) should not block the correctness work.
