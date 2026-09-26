# Spicy Code Quality Review

**Date:** 2026-09-25
**Scope:** Full live codebase — `apps/`, `templates/`, `assets/`, `spicy/`
**Working tree:** `main`, including uncommitted inventory/reports work present at review time
**Out of scope:** `references/`, `node_modules/`, compiled `static/`, migrations as history
**Method:** Static review of models, services, views, templates, and JS, verified against the current source with `file:line` citations. No code was changed as part of this review.

This is a **maintainability and structure** review. A separate security/fraud audit already exists in `AUDIT_REPORT.md` (2026-09-10). This document does not repeat those employee-fraud paths unless they are also a structural problem.

---

## Verdict

The domain logic is real and mostly fail-closed. Settlement, stock posting, and GL writes go through services. Money is `Decimal`. Costing is perpetual weighted-average (PWAC) only. POS and backoffice view modules collect many short functions in one file; that is ordinary Django and is not treated as a problem here.

What is a problem: submit/cancel paths for the same kind of document have already drifted (transfer cancel vs receipt cancel, recon cancel vs the rest). A few cashier and backoffice endpoints will 500, silently skip work, or render a blank statement. Copied rules (item flags, department filters, GL merge, variant graphs) can disagree.

A finding is included only when splitting, extracting, or deleting something **changes an outcome**: a bug class goes away, two paths stop drifting, a request stops doing unnecessary work, or a reader can follow one function that is currently doing several jobs. File length by itself is not a finding.

---

## How to read this

| Category | Meaning |
|---|---|
| **Bug** | Incorrect behavior, a production 500, silent data drift, or a statement that lies. Fix these first. |
| **Refactor** | Same behavior, clearer ownership, or one copy of a rule that currently exists in several places and can diverge. |
| **Nit** | Small, local cleanup. Do not start here. |
| **Keep** | Intentional design. Leave it alone. |

Each finding has:

1. **What happens** — the behavior a cashier, manager, or future developer will see.
2. **Why it happens** — the flow through the code.
3. **Where** — file and line.
4. **How to fix** — the smallest change that actually removes the class of problem.

---

## Contents

1. [What is already healthy](#what-is-already-healthy)
2. [Bugs](#bugs)
3. [Refactor suggestions](#refactor-suggestions)
4. [Nits](#nits)
5. [Recommended sequence](#recommended-sequence)
6. [Keep as-is](#keep-as-is)

---

## What is already healthy

Protect these. New work should extend them, not work around them.

- **PWAC is the only costing path.** FIFO queues, batches, and `valuation_method` are gone from runtime code (migrations `0028` / `0029`).
- **Money is `DecimalField`.** No `FloatField` for money in app models.
- **Submitted stock and GL rows are treated as immutable.** Updates and deletes on `StockLedgerEntry` raise. Reversal is a new row dated today.
- **Write paths lock.** Settlement, returns, and stock documents use `select_for_update` inside `@transaction.atomic` in the service layer.
- **POS is HTMX + Django 6 `{% partialdef %}`.** Alpine is limited to dialogs and live previews. The server remains authoritative for prices, stock, and totals.
- **Fail-closed account resolution** exists on many GL paths: missing leaf / disabled / non-leaf accounts raise `ValidationError`.
- **Architecture docs match the app map** in `docs/architecture/apps.md`.
- **View files are traceable.** `views_pos.py` and `inventory/views.py` are collections of separate HTTP handlers. Each function maps to one action (add item, submit receipt, close shift). Business rules already live in services for the write paths. That layout is kept.

---

## Bugs

### BUG-1 — Cart quantity and guest-stepper endpoints will 500 on PostgreSQL

**What happens**

The cashier taps **+ / −** on a cart line, or changes guest count with the stepper. Django raises:

```text
TransactionManagementError: select_for_update cannot be used outside of a transaction
```

`make test` stays green. The failure only appears in a real request against PostgreSQL.

**Why it happens**

Django’s default is autocommit: each query is its own transaction. `SELECT … FOR UPDATE` is only legal inside an open transaction. On PostgreSQL, Django 6 raises if you evaluate `select_for_update()` outside `transaction.atomic()`.

Two POS views fetch the draft with a lock and then call a service:

```text
HTTP POST (cart +/- or guest stepper)
        ↓
views_pos.pos_order_update_item / pos_order_update_meta
        ↓
Order.objects.select_for_update()     ← evaluated here, no atomic()
        ↓
500
```

Sibling views (`pos_order_add_item`, `pos_order_sync`, `pos_order_clear`) wrap the same fetch in `with transaction.atomic()`. These two do not.

The service functions `update_order_item` and `update_order_meta` already lock inside `@transaction.atomic`. The view lock is redundant and is the thing that crashes.

Tests subclass `django.test.TestCase`, which wraps every test in a transaction. That hides the error. There is no `ATOMIC_REQUESTS = True` in `spicy/settings.py`.

**Where**

- `apps/orders/views_pos.py:861` (`pos_order_update_meta`)
- `apps/orders/views_pos.py:1010` (`pos_order_update_item`)

**How to fix**

Drop the view-level `select_for_update`. Authorize with a normal `open_drafts_for()` fetch. Let the service own the lock. If a view lock is required, wrap fetch + service call in one `atomic()` the way `pos_order_sync` already does.

Add a `TransactionTestCase` (or an explicit autocommit assertion) so this class of bug cannot hide again.

---

### BUG-2 — Settlement prints kitchen/bar tickets inside the payment/GL transaction

**What happens**

On settle, if the order was never sent, tickets are created **and printed** before payment rows, drink-stock conversion, and GL posting, all inside one `@transaction.atomic`.

Today `apps/orders/printing.py` is a stub that always returns success, so cashiers do not see a failure. The moment the LAN print agent exists:

1. Printer I/O holds the order and shift row locks for the duration of the HTTP call.
2. If payment validation or GL posting fails after paper is out, the transaction rolls back. The kitchen has a ticket for an order that does not exist.

`pos_order_sync` (the Send button) already does this correctly: create tickets inside the transaction, print after commit.

**Why it happens**

```text
settle_order  [@transaction.atomic]
  lock order + shift
  if no KOTs:
      _plan_tickets
      _build_ticket_snapshots
      dispatch_tickets()          ← print I/O while rows are locked
  create OrderPayment rows
  convert drink reservations → SLE
  post_order_gl
  commit
```

`dispatch_tickets` (`apps/orders/services.py:625`) calls `printing.print_ticket` and then updates `print_status`. That is the print interface. Docs (`docs/architecture/side-effects.md`) say ticket creation commits before each physical attempt. Settlement violates that.

The same lock-across-render pattern exists on add-item: `pos_order_add_item` can `return render(add_on_dialog)` at line 960 **inside** `transaction.atomic()`, holding the order lock while the template renders.

**Where**

- `apps/orders/services.py:194–202` (`settle_order` → `dispatch_tickets`)
- `apps/orders/views_pos.py:960` (add-on dialog return inside the lock)

**How to fix**

Make `settle_order` commit money + stock + GL only. Call `create_tickets` inside the transaction and **return** the created KOTs. The view calls `dispatch_tickets` after commit — same shape as `cancel_sent_order` + `pos_order_sync`.

Resolve the add-on/variant dialog without holding `select_for_update`.

---

### BUG-3 — Transfer cancel can mark CANCELLED without reversing stock

**What happens**

A manager submits a Store → Kitchen/Bar transfer, then later cancels it. If production-unit warehouses have been reassigned, or a destination bin is missing, cancel **skips those lines**, still marks the document `CANCELLED`, and may reverse GL for a movement that never came back.

Kitchen/Bar can keep the stock. The voucher looks cancelled.

**Why it happens**

Submit snapshots `source_warehouse` and `target_warehouse` on each `StockEntryDetail`. Cancel ignores those snapshots and rebuilds routing from **current** settings:

```text
cancel_stock_entry (MATERIAL_TRANSFER)
        ↓
Restaurant.load() + ProductionUnit.objects.all()
        ↓
targets["FOOD"]  = current Kitchen warehouse
targets["DRINKS"] = current Bar warehouse
        ↓
for each detail:
    if target missing: continue
    if dest_bin or store_bin missing: continue
        ↓
status = CANCELLED
```

`continue` inside an atomic block that still flips status is a silent paper-over. A second paper-over at `services.py:544` invents `orig_value = curr_value` when the original Store SLE is missing, so variance accounting disappears too.

Purchase-receipt cancel already walks **original SLE rows**. Transfer cancel should do the same.

**Where**

- `apps/inventory/services.py:476–513` (re-derive + `continue`)
- `apps/inventory/services.py:544` (invent original value)
- Snapshots written at `apps/inventory/services.py:387` and `:413`

**How to fix**

Reverse from the snapshotted detail warehouses, or from the original SLE pair (Store outbound + destination inbound). Missing target, missing bin, or missing original SLE must raise inside the atomic block. Never `continue` into `status = CANCELLED`.

This is also why inventory needs one `reverse_voucher` helper (see REF-1). The transfer special case is how this bug was born.

---

### BUG-4 — Reconciliation cancel books the wrong stock value after WAC has moved

**What happens**

A kitchen consumption (or adjustment) is submitted. Later inbounds change the warehouse WAC. Cancelling the reconciliation:

- Puts quantity back at **current** WAC (Bin is now right for qty).
- Posts GL as a **mirror of the original** debit/credit (GL is at the old value).

Bin stock value and Stock-in-hand GL diverge. No `CANCELLATION_WAC` row records the drift.

**Why it happens**

Purchase-receipt and stock-entry cancel already have the correct policy:

1. Reverse quantity at current WAC.
2. Stamp `CANCELLATION_WAC` for the difference.
3. Post SIH at current value, original counter-account at original value, drift to the inventory price-variance account.

Reconciliation cancel (`cancel_stock_reconciliation`) issues `quantity=-sle.quantity` with `unit_rate=None` (current WAC), then:

```text
GLEntry.post(rows=[{debit: original.credit, credit: original.debit}, ...])
```

That is a naive swap of the original legs. After WAC has moved, those amounts no longer match Bin.

**Where**

- `apps/inventory/services.py:901–930`

**How to fix**

One reversal helper with one policy. Reconciliation calls it. Delete the naive GL swap.

---

### BUG-5 — Daily P&L “Refresh preview” blanks the statement table

**What happens**

On a Daily P&L draft, the first paint of the three-column statement is correct. Clicking **Refresh preview** leaves an empty table (header only). The uncommitted `food_usage_counted` banner can still appear, because that flag is passed.

If the preview POST is invalid (bad material qty, etc.), the **entire form** is rendered into `#pnl-preview`. Nested forms, duplicate submit buttons, broken HTMX after that.

**Why it happens**

The statement partial iterates `rows`:

```django
{% for line in rows %}
```

The GET form include passes `rows=preview.lines`, so first paint works.

The HTMX preview view returns:

```python
{"preview": preview, "pnl": pnl, "food_usage_counted": preview.food_usage_counted}
```

It never passes `rows`. The template’s `{% for line in rows %}` loops over nothing.

On validation failure the same view renders `daily_pnl_form.html` into a fragment target that expected `_statement.html`.

**Where**

- `apps/reports/views.py:199–208`
- `templates/backoffice/reports/_statement.html:22`
- `templates/backoffice/reports/daily_pnl_form.html` (`hx-target="#pnl-preview"`)

**How to fix**

Pass `rows=preview.lines` (and keep `food_usage_counted`). On validation failure, return `_statement.html` with `preview_error`, or retarget the full form. Never dump the page into the preview slot.

---

### BUG-6 — Gross profit food + drinks does not equal total

**What happens**

The Daily P&L three-column Gross profit row can show:

| | Food | Drinks | Total |
|---|---:|---:|---:|
| Gross profit | 1,000 | 800 | 1,600 |

when food + drinks is 1,800. The total column is ₦200 lower because electricity (or another unallocated direct) was subtracted only from the total.

**Why it happens**

Unallocated costs (electricity, some materials, round-off) are added to `direct_total` and to `net`, and are **not** split into `direct_food` / `direct_drinks`.

```text
gp_food   = food   − food_actual − direct_food
gp_drinks = drinks − cogs_drinks − direct_drinks
gp        = net    − cogs        − direct_total
```

`net` includes round-off. `direct_total` includes costs that never entered the department columns. The three-column row then looks like a breakdown of the total, and it is not.

Prime cost has the same shape: food/drinks are COGS only; the total is COGS + employees.

**Where**

- `apps/reports/services.py:201–204` (gross profit)
- `apps/reports/services.py:226` (prime cost)

**How to fix**

Unallocated amounts belong in the total column only. Either:

- show departmental GP as department sales − department cost − department directs, and treat the total as that sum **plus** a visible unallocated line, or
- leave departmental GP blank/`—` when unallocated directs exist, so the columns cannot be added in the head.

Do not silently allocate electricity 50/50 to invent a split.

---

### BUG-7 — `/pos/` is registered twice; the named URL is a landmine

**What happens**

Every “Launch POS” link reverses `{% url 'web:pos_index' %}`. That name points at `apps/web/views.py:pos_index`, which renders `pos/index.html` **with no shift, no order, no catalog**.

Today Django include order saves you: `spicy/urls.py` mounts `apps.orders.pos_urls` on `pos/` **first**, so `/pos/` is served by `pos:pos_home`. The named view `web:pos_index` is unreachable.

If anyone ever reorders includes, or includes `web.urls` first, cashiers get an empty POS shell instead of the shift gate.

**Why it happens**

```text
spicy/urls.py
  path("pos/", include("apps.orders.pos_urls"))   # real POS — wins
  path("",     include("apps.web.urls"))          # also declares path("pos/", pos_index)
```

Templates and `home()` still reverse `web:pos_index` because that was the Pegasus-era name.

**Where**

- `spicy/urls.py:22–23`
- `apps/web/urls.py:10`
- `apps/web/views.py:132` (`pos_index`)
- `templates/web/app/app_base.html:168`, `templates/backoffice/dashboard.html:10`

**How to fix**

Delete `web.views.pos_index` and the web URL. Point every template and redirect at `pos:pos_home`.

---

### BUG-8 — Backoffice can open a shift with no Restaurant; POS cannot

**What happens**

POS open (`staff.services.open_shift`) refuses to proceed without a Restaurant row:

```text
"Restaurant settings are not configured."
```

Backoffice submit (`POSOpeningEntry.submit`) takes the same Restaurant row lock as a mutex, then **ignores** a missing row and opens the shift anyway.

Two writers, two rules. A shift can exist in a state POS will immediately refuse to use.

**Why it happens**

```text
POS path:        open_shift() → Restaurant.objects.select_for_update().first()
                 if None: raise
Backoffice path: _save_opening_entry() writes DRAFT (no restaurant check)
                 POSOpeningEntry.submit() locks Restaurant, does not require it
```

`_save_opening_entry` (`apps/staff/views.py:130`) never calls `open_shift()`. Cash-mode cardinality and “one open shift” run only later in `submit()`.

**Where**

- `apps/staff/services.py:121–123` (POS, fail-closed)
- `apps/staff/models.py:96–99` (backoffice, lock only)
- `apps/staff/views.py:130` (`_save_opening_entry`)

**How to fix**

`open_shift()` is the only writer. Backoffice create is a thin draft wrapper that still goes through the same guards, or it calls the service on submit.

---

### BUG-9 — Inventory documents can be flipped DRAFT → SUBMITTED with no SLE and no GL

**What happens**

A superuser (or any code path that sets `status` and calls `save()`) can mark a Stock Entry, Purchase Receipt, or Reconciliation `SUBMITTED` without going through `submit_*`. Result: a submitted voucher with empty stock ledger and empty GL.

Cancel then hits the empty-SLE branches and still marks `CANCELLED`. The audit trail says the document existed and was voided. Nothing moved.

**Why it happens**

`StockEntry.save` (copied on Reconciliation and Purchase Receipt) blocks edits to **already-submitted** rows. It allows `DRAFT → SUBMITTED` as a field write.

Admin only freezes documents that are already submitted. Uncommitted work correctly made Bin/SLE admin view-only; document **status** is still writable.

`apps/inventory/tests/test_admin_bypass.py:18` itself writes `status="SUBMITTED"` through the ORM with no posting, which proves the hole.

**Where**

- `apps/inventory/models.py:515` (`StockEntry.save`)
- Same machine at `models.py:740` (Reconciliation) and `models.py:828` (Purchase Receipt)

**How to fix**

Status may change only inside `submit_*` / `cancel_*`. Model `save()` rejects status writes unless a private service flag is set (the same pattern orders already uses, and should eventually drop — see REF-5). Admin: `status` always readonly; hide submit/cancel from admin.

---

### BUG-10 — `StockLedgerEntry.objects.create()` writes a ledger row and skips Bin

**What happens**

`_create_entry_locked` is the only path that updates `Bin.actual_qty` / `valuation_rate`. A raw `StockLedgerEntry.objects.create(...)` inserts an audit row and leaves Bin untouched. Subsequent posting **trusts Bin**, not `SUM(SLE.quantity)`. If they drift, Bin wins.

Uncommitted `save()` now rejects **updates**. Creates are still open. `tests/test_ledger_guards.py` creates an SLE this way on purpose.

Bin is therefore a live mutable snapshot, not a projection of the ledger:

1. `actual_qty` / `valuation_rate` are denormalized cache written by the poster.
2. `reserved_qty` is a second live field, written only by `orders.services.reserve_drink_stock`. It has no SLE representation (that part is intentional: reservations are promises, not movements).
3. `Bin.get_or_create_bin` and `StockReconciliationItem.save` create empty bins with no ledger row.

**Where**

- `apps/inventory/models.py:340` (update/delete rejected; create still open)
- `apps/inventory/models.py:393–481` (`_create_entry_locked`)
- `apps/orders/services.py:699` (`reserved_qty`)

**How to fix**

Make `objects.create` on SLE go through `create_entry`, or raise unless a service token is set. Treat Bin as a locked snapshot owned by the poster: no direct `actual_qty` writes outside `_create_entry_locked`. Keep `reserved_qty` as the one non-SLE field, updated only under the same `select_for_update` bin lock.

---

### BUG-11 — Refund GL silently no-ops when the source order has no live GL

**What happens**

A submitted return can finish without refund GL if the source order’s voucher is missing or already cancelled. Settle is atomic with `post_order_gl` today, so this is a landmine for historical data, a cancel-then-return sequence, or a future settle refactor.

**Why it happens**

```python
if not GLEntry.objects.filter(
    voucher_type="Order", voucher_no=source.invoice_number, is_cancelled=False
).exists():
    return
```

Idempotency on the **return** voucher is correct. Skipping because the **source** voucher is gone is a silent miss. The return order is still `SUBMITTED` and `is_paid`.

**Where**

- `apps/accounting/services.py:322–323`

**How to fix**

Fail closed: raise `ValidationError` if the source has no live GL. A return against an order that never posted is a data error, not a no-op.

---

## Refactor suggestions

These keep behavior and delete whole categories of complexity. Ordered by leverage, not by file.

---

### REF-1 — One inventory movement engine, three policies

**What is wrong**

Six near-duplicate functions implement one workflow:

| Function | Document |
|---|---|
| `submit_stock_entry` / `cancel_stock_entry` | Market receipt and Store → production transfer |
| `submit_purchase_receipt` / `cancel_purchase_receipt` | Supplier GRNI receipt |
| `submit_stock_reconciliation` / `cancel_stock_reconciliation` | Opening / adjustment / consumption / waste |

Each one: lock document → validate lines → `get_or_create` bins → `select_for_update` → `_create_entry_locked` → post GL → flip status.

The **product** differences are real:

- Market receipt credits the payment-mode GL account (no GRNI).
- Purchase receipt credits GRNI.
- Transfer is two-leg (Store out, Kitchen/Bar in) at WAC.
- Reconciliation quantity means four different things depending on `reason`.

Those are **policies**. They are implemented as six engines, which is why the cancel paths already disagree:

- transfer cancel re-derives warehouses (BUG-3)
- recon cancel skipped variance (BUG-4)
- `_post_gl_rows` is used for some reversals while others call `GLEntry.post` raw
- last-purchase-rate revert is forked (`_revert_last_purchase_rates` vs `_revert_last_purchase_rates_for_stock_entry`)

The gain is one reversal policy so the next document cannot skip variance or `continue` into CANCELLED. Splitting `services.py` into more files without that shared engine does not fix the drift.

**The judo**

```text
post_movements(voucher, lines, gl_policy)
reverse_voucher(voucher, gl_policy)
```

Each document supplies lines `(item, warehouse, signed_qty, inbound_value)` plus a counterpart account (funding, GRNI, destination SIH, opening/adjustment/expense/wastage). The six submit/cancel functions become thin callers of that engine.

**Where**

- `apps/inventory/services.py:295` (`submit_stock_entry` and the five siblings)

---

### REF-2 — Move food usage out of the posting module

**What is wrong**

`compute_food_usage` / `recipe_plate_cost` live in `inventory.services`. They do not post stock. They read Kitchen reconciliation SLEs, recipes, and order lines, then assemble a Daily P&L input.

That mix creates an import cycle:

```text
reports.services.compute_daily_pnl
        ↓
inventory.services.compute_food_usage
        ↓
reports.sources.orders_in_window
```

Rate fallback (`actual SLE → Kitchen WAC → last_purchase_rate → 0`) is a **report** rule. It does not belong next to `_create_entry_locked`.

**The judo**

Move food-usage into reports next to `compute_daily_pnl`. Keep `recipe_plate_cost` next to Recipe if the inventory recipe page needs it; it is display-only and does not post. The inventory `food_usage` view currently swallows `ValidationError` and reports ₦0 food sales — that stays a hard error after the move.

**Where**

- `apps/inventory/services.py:69–253`
- `apps/reports/services.py:11` and `:92`
- `apps/inventory/views.py:1075` (`food_usage` page)

---

### REF-3 — One inbound line, two headers

**What is wrong**

`StockEntryDetail` and `PurchaseReceiptItem` duplicate the as-bought UOM line: `uom`, snapshotted `conversion_factor`, `stock_qty()`, `stock_unit_rate()`, department flag checks, last-purchase-rate update.

Product intent is two **GL** stories (FEATURES.md: market purchase vs supplier GRNI), not two stock models. Transfer-only fields (`source_warehouse`, `target_warehouse`, `purpose`) hang on the same receipt line.

Forms duplicate the HTMX UOM widget (`forms.py:234` vs `:391`). Views duplicate `_entry_line_params` / `_receipt_line_params`.

**The judo**

Extract one inbound-line helper (qty, uom, factor, rate, amount, `stock_qty()`, `stock_unit_rate()`) used by both headers. Keep two documents: they post different counterpart accounts. Stop hanging transfer-only warehouse fields on the receipt line; transfer validation belongs on the transfer purpose only.

**Where**

- `apps/inventory/models.py:540` (`StockEntryDetail`)
- `apps/inventory/models.py:858` (`PurchaseReceiptItem`)
- `apps/inventory/forms.py:234` and `:391`

---

### REF-4 — Cart HTMX rebuilds the full catalog on every guest tap

**What is wrong**

`_render_cart` always calls `_build_order_context`, which loads the full active menu, variant parent cards, drink availability, payment modes, and tickets.

Guest-card activate and guest-count changes only need the cart. They still rebuild the catalog. Qty changes set `catalog_oob=True` so stock badges refresh — that is the one case that needs catalog data, and it is buried inside one context builder.

This is wasted work on a hot path, and it couples cart endpoints to catalog assembly. It is not a reason to split `views_pos.py` into more modules.

Related, but already covered elsewhere: `settle_order` inlines ticket print (BUG-2); POS close and backoffice close bind the same forms twice (REF-10). Those are function/duplication issues, not a file-split.

**The judo**

Split `_cart_context(request, order)` from `_catalog_context(...)`. Cart HTMX endpoints load cart context. Pass catalog only when `catalog_oob` is true.

**Where**

- `apps/orders/views_pos.py:187` (`_render_cart`)
- `apps/orders/views_pos.py:269` (`_build_order_context`)

---

### REF-5 — Order lifecycle lives in services; stop punching flags through `Order.save`

**What is wrong**

`Order.save()` is a second workflow engine. Every save, including `recalculate_totals`, re-fetches the previous row and may query `KOT.objects.filter(order=self)`. Services punch through it with instance flags:

- `_allow_submit`
- `_allow_cancellation`
- `_allow_discard`
- `_settling` (on payment insert)

The real transitions already live in `settle_order`, `cancel_sent_order`, `discard_order`, `delete_unsent_draft`. A missed flag raises a confusing `ValidationError`. Views still re-check `kots.exists()` themselves, so the model is not the only gate.

“Once a KOT exists, freeze the draft” is implemented in at least seven places: `Order._ensure_editable`, `Order.save` draft-field guard, `OrderPayment.save` / `delete`, and views `pos_order_add_item`, `pos_order_clear`, `pos_order_variant_dialog`. Each copy has its own message.

**The judo**

Keep immutability constraints that are data-shaped (return linkage, warehouse snapshot, invoice_printed one-way). Move lifecycle permission into the service functions that already own it. One predicate on `Order` (or a `sent_tickets` annotation already fetched for `order_sent` in cart context). Views render `order_sent` and let the service raise.

**Where**

- `apps/orders/models.py:218` (`Order.save`)
- `apps/orders/models.py:315` (`_ensure_editable` and copies)
- `apps/orders/views_pos.py:897`, `:1094`, `:812`

---

### REF-6 — One public GL voucher API

**What is wrong**

`GLEntry.post` is the write. Everything above it is forked:

| Path | How it posts | How it reverses |
|---|---|---|
| Manual journal | `JournalEntry.submit` → `GLEntry.post` | `JournalEntry.cancel` inlines reverse |
| Cash variance | Builds a `JournalEntry`, then `submit()` | Via the journal UI, independently of the close |
| Order / refund / cash-out / payables | Direct `GLEntry.post` | `_reverse_gl` (payables, cash-out) or none (orders) |
| Inventory | Private `_post_gl_rows` that **re-merges without netting** | Inlined cancel loops |

Two mergers, two semantics. Inventory sums debit and credit on the same account. Accounting nets. Same-account two-sided rows fail `GLEntry.clean`.

`_resolve_required_account` (`accounting/services.py:38`) and inventory `_resolve_account` (`inventory/services.py:257`) are the same function.

`_reverse_gl` is “private” and already imported by `apps/staff/services.py:299`. Staff should call a public function.

Refund **cannot** be “settle with negative lines.” Income vs sales-returns are different accounts; wastage is a third. What *is* duplicated is the pipeline: load settings, resolve accounts, merge, disjoint-sides, against-string, `GLEntry.post`, idempotent guard. `post_refund_gl` even merges twice.

Optional production-unit mappings hide broken config: missing unit income/expense falls through to restaurant default, so food can post into a generic or drinks sales account until someone notices.

**The judo**

```text
post_voucher(posting_date, rows, voucher_type, voucher_no, remarks)
    → disjoint check, merge (net), against, GLEntry.post, idempotent exists()

reverse_voucher(voucher_type, voucher_no, remarks)
    → today's date, mark originals cancelled, post mirrors
```

Point journals, order GL, refund GL, payables, cash-out, and inventory at it. Delete inventory `_resolve_account` / `_post_gl_rows`.

Order GL: shared pipeline, two **leg builders**.

```text
legs = income_or_returns(order) + tender(order) + rounding(order) + drink_stock(order)
post_voucher(...)
```

`drink_stock` is the single reader of POS Order / POS Return SLEs. `reports.sources.drink_cogs` becomes a projection of that reader. Fail if a production unit used on the order has no income/returns/expense account.

Keep cash variance as “build a journal, then submit” only if the variance journal must remain a user-visible document. Otherwise post GL like cash-out.

**Where**

- `apps/accounting/services.py:227` (`post_order_gl`)
- `apps/accounting/services.py:309` (`post_refund_gl`)
- `apps/accounting/services.py:185` (`_merge_rows`)
- `apps/inventory/services.py:267` (`_post_gl_rows`)
- `apps/accounting/models.py:215` (`GLEntry.post`)

---

### REF-7 — Daily P&L is one statement, stored once

**What is wrong**

One economic statement exists as three representations:

1. Live `Computation` (`LineSpec` list + `totals` dict + child row lists).
2. Denormalized money columns on `DailyPnL` (header/CSV).
3. `DailyPnLLine` rows (the table).

Submit deletes children, writes lines from `Computation`, then `setattr` every key in `totals` onto `DailyPnL`. Preview reads `Computation.lines`. Submitted detail reads `pnl.lines` plus the columns. A future line change can freeze a header that no longer matches the table.

`DailyPnLLine.KITCHEN_CONSUMPTION` is still a section choice and is never appended. Food actual is the FOOD column of the COGS line plus `kitchen_consumption` on the header. Dead section + header field + child `DailyPnLConsumptionRow` for the same idea.

Uncommitted `food_usage_counted` is the right idea (freeze the warning). It does not reduce the triple statement.

Drink COGS is implemented twice: GL `_cogs_legs` reads POS Order SLEs; P&L `drink_cogs` re-reads them and walks return lines for wastage relabel. Two readers, two shapes, one event.

**The judo**

`compute_daily_pnl` returns the statement. Preview renders it. Submit serializes it (lines + child tables + `food_usage_counted`). Header/CSV either read from lines or keep a small cached subset (`gross_sales`, `net_profit`) filled from the statement.

Delete `KITCHEN_CONSUMPTION` from `SECTION_CHOICES`. Fix GP while you are there (BUG-6). Pass `rows=preview.lines` (BUG-5).

Do not post Daily P&L to the GL to “unify” it with Simple P&L. They are different products. Simple P&L is GL income − expense. Rename the sidebar label if the two names collide.

**Where**

- `apps/reports/services.py:67` (`Computation`)
- `apps/reports/services.py:82` (`compute_daily_pnl`)
- `apps/reports/services.py:318` (`submit_daily_pnl`)
- `apps/reports/pnl_models.py:62` (denormalized columns)
- `apps/reports/sources.py:65` (`drink_cogs`)

---

### REF-8 — Reports split files, then recopied filters

**What is wrong**

`submitted_orders(date_from, date_to)` exists in `sales_reports.py:23` and is not the source of truth.

- Item-wise rebuilds `OrderItem.objects.filter(order__status=SUBMITTED)` plus the same date predicates.
- Month-wise calls `submitted_orders` twice and inlines a second department bucket.
- Cancelled invoices copies the date predicates onto `status=CANCELLED`.
- POS register copies them onto closings.
- Trial balance and simple P&L re-filter `GLEntry` themselves even though `_gl_queryset` exists.
- `ZERO` / `_q2` are copied across three modules.

P&L `orders_in_window` is a **different** clock (business-day datetime, Python filter over three calendar dates). That split is documented and correct. The problem is the *calendar* reports.

`sales_by_department` still falls back `department or item__department`. Query reports rely on the `OrderItem.department` snapshot. Two department rules for the same column. After migration `0030`, `department` cannot be null, so the fallback is dead (same leftover as REF-16).

**The judo**

```text
submitted_orders(date_from, date_to)    # calendar posting_date — already exists
order_items(date_from, date_to)         # OrderItem for those orders
gl_entries(fy, date_from, date_to, account)
```

Item-wise, month-wise, cancelled, register, trial balance, simple P&L call these. Leave `orders_in_window` / `business_day_window` as the P&L-only clock.

Do not add another `foo_reports.py` without deleting a copied date filter.

**Where**

- `apps/reports/sales_reports.py:23`
- `apps/reports/sales_breakdown_reports.py:21`
- `apps/reports/accounting_reports.py:157` and `:250`

---

### REF-9 — Payables workflow off the models; keep the app boundary

**What is wrong**

`PLAN.md` says payables live in `apps.accounting`. That product decision is correct. The problem is the **layer**: submit/cancel/allocate live on the models.

`validate_for_submission` repeats `clean()` almost verbatim. `Supplier.outstanding_balance` is a Python loop; the accounting dashboard sums that property — N+1 over every supplier × invoices.

Stock lines are materialized at submit from the receipt (`build_supplier_invoice_stock_lines`) and then the GL loop still checks `purchase_receipt_id` per line.

`SupplierInvoice.save` / `SupplierPayment.save` skip `full_clean()` (forms + submit carry it). `DailyPnL.save` and `LedgerAccount.save` do not. Inconsistent document discipline.

**The judo**

Models stay thin. Submit/cancel/allocate move next to the existing payables GL functions (`post_supplier_invoice_gl`, `post_supplier_payment_gl`). Do not extract `apps.payables`. Do not split the models file unless the workflow has actually moved.

**Where**

- `apps/accounting/payables_models.py:160` (`SupplierInvoice.submit`)
- `apps/accounting/payables_models.py:68` (`outstanding_balance`)
- `apps/accounting/views.py:43` (dashboard N+1)
- `apps/accounting/services.py:512`

---

### REF-10 — One Shift write path and an explicit open query

**What is wrong**

Open vs closed is inferred everywhere as:

```text
status=SUBMITTED AND closing_entry_id IS NULL
```

Copied in staff models, staff services, staff views, and `views_pos._get_open_shift`.

There are **two** OneToOnes between the same pair:

- `POSOpeningEntry.closing_entry` — set only when the close is **submitted**.
- `POSClosingEntry.opening_entry` — set when the closing **draft** is created.

That encoding is how “close in progress” is distinguished from “closed.” It is also why `POSClosingEntry.clean()` forbids `full_clean` once the opening is no longer open, and why cancel-close does **not** reopen: operators open a new shift. The word cancel means void the Z-report.

`POSOpeningEntry.clean()` treats every DRAFT as “about to open,” which is why backoffice save skips `full_clean()` and why you can park a draft while a shift is already open — until submit explodes.

Close-shift is implemented twice (POS and backoffice) with different form prefixes (`cp_mop_*` vs `cp_{pk}`). `submit_closing_entry` then, in one transaction: locks Restaurant + closing + opening, re-checks actor, cuts `period_end_date`, aggregates orders/payments, nets cash change, subtracts refunds and cash-outs, writes Z-report totals, validates non-cash over-count, enforces variance-note + manager, marks the opening closed, and posts cash-variance GL.

**The judo**

1. `open_shift()` is the only writer (also fixes BUG-8).
2. `POSOpeningEntry.objects.open()` replaces every `filter(status=SUBMITTED, closing_entry__isnull=True)`.
3. One close use-case: `preview_close`, `count_drawer`, `submit_close`. POS and backoffice become two templates over that API.
4. Split submit into compute → validate → persist → post_gl.

A full `Shift` model with `draft → open → closing → closed` is a larger cut. The manager + one writer is the 80% fix.

**Where**

- `apps/staff/models.py:35–41` and `:185–189` (two OneToOnes)
- `apps/staff/models.py:63–79` (open inferred)
- `apps/staff/services.py:144–258` (`submit_closing_entry`)
- `apps/orders/views_pos.py:85–92` and `:471–619`

---

### REF-11 — One Item family; one FOOD/DRINKS shape check

**What is wrong**

Three identities describe one SKU:

| Identity | Role |
|---|---|
| `inventory.Item` | Stock flags, department, `has_variants`, `variant_of` |
| `menu.MenuItem` | POS price, `special_dish`, denormalized `item_name` |
| `menu.ItemVariant` / `ItemAddOn` | Item↔Item links that live in menu because they need a menu price |

Two family graphs exist for sizes:

| Graph | Written by | Read by POS |
|---|---|---|
| `Item.has_variants` / `Item.variant_of` | Item form | `item.has_variants` requires a size |
| `menu.ItemVariant` | Menu variants CRUD + seed | catalog grouping via `pos_variant_of` |

Seed writes **both**. Menu UI writes only `ItemVariant`. Inventory UI writes only `variant_of`. Operators can create a family that groups on the catalog but does not demand a size, or the reverse.

The FOOD/DRINKS flag matrix (sellable food is virtual; drinks are stocked/sold/bought; recipe ingredients are stocked non-sellable food) is copied into `Item.clean`, `ItemForm.clean`, `MenuItem.clean`, and `ItemAddOn.clean`. `ItemVariant.clean` does not repeat it.

`Item.save()` silently deletes add-ons when the parent is no longer a sales item. `MenuItem.clean` mutates `rate` from `last_purchase_rate` when rate is falsy — validation with a side effect. `MenuItem.item_name` is copied once on save if empty and then drifts.

**The judo**

Pick one family. Stronger default: `Item.variant_of` as source of truth; derive POS parent cards from it; delete `menu.ItemVariant` (or the reverse — pick one). Move `ItemAddOn` next to Item. Keep `MenuItem` as “this SKU at this price on this menu.”

One `assert_item_shape(item)` used by Item, ItemForm, MenuItem, and ItemAddOn. Stop mutating in `clean()`.

**Where**

- `apps/inventory/models.py:112–165` (`has_variants` / `variant_of`)
- `apps/menu/models.py:115` (`ItemVariant`)
- `apps/orders/views_pos.py:916` (size required) and `:222` (catalog grouping)
- Flag matrix: `inventory/models.py:166`, `inventory/forms.py:122`, `menu/models.py:58`, `menu/models.py:97`

---

### REF-12 — Restaurant is a settings row, not a cross-app oracle

**What is wrong**

`Restaurant` is the settings singleton **and** the global mutex **and** a 15-account GL chart **and** a validator that reaches into payments, orders, and inventory (`clean()` imports `PaymentGLMapping`, `Order`, `PurchaseReceipt`, `StockEntry`).

The reverse exists too: `PaymentGLMapping.clean` builds a sales-account set from Restaurant + ProductionUnit. `Warehouse.clean` asks Restaurant whether it is the store/bar. `Order.save` reads `invoice_series_prefix`. `OrderPayment.save` calls `Restaurant.requires_payment_reference()`.

`Restaurant.load()` is `order_by("pk").first()` with `select_related`. Callers that need the mutex use `Restaurant.objects.select_for_update().first()` instead. The unique `singleton_key` is never read.

Staff role assignment living under Settings is **documented and correct**. The misplaced thing is the GL field dump and the cross-app `clean()`.

**The judo**

Keep Restaurant as a dumb settings row. `load()` for reads, `select_for_update` on that same row for mutex. Move “cannot change bar while drafts exist” to the warehouse/order services that already own those documents. Extract account-role checks to one helper used by both PaymentGLMapping and Restaurant forms.

**Where**

- `apps/settings/models.py:202–205` (`load`)
- `apps/settings/models.py:214–270` (`clean`)

---

### REF-13 — Role checks are N+1; two backoffice decorators are the same predicate

**What is wrong**

Each of `is_admin`, `is_manager`, `is_cashier` runs `groups.filter(name=...).exists()`. `has_backoffice_access` can hit Admin **and** Manager. `has_staff_role` can hit three. Sidebar `user.has_backoffice_access` adds another pair of queries on every backoffice page.

`backoffice_required` and `manager_required` are identical predicates. Payments “create = manager, list = backoffice” is a distinction without a difference. POS views then re-test `user.is_manager or user.is_admin or user.is_superuser` inline.

`_role_required` also wraps `@login_required` while `LoginRequiredMiddleware` is already global.

A deleted `test_role_perf.py` still sits in `apps/users/tests/__pycache__/`, so this was already measured.

**The judo**

One `user.spicy_roles` set, cached on the instance, prefetchable. Decorators read the set. Drop `manager_required` or give it a real extra check. Prefetch groups in a small auth middleware if templates keep calling properties.

**Where**

- `apps/users/models.py:38–56`
- `apps/users/decorators.py:23–26`
- `apps/orders/views_pos.py:131`, `:345`, `:642`, `:711`, `:1235`

---

### REF-14 — Delete the SaaS / Pegasus shell from the live path

**What is wrong**

This is a LAN POS. User provisioning is `settings.staff_create`. Signup is still wired:

- `ACCOUNT_FORMS["signup"]`
- “Get Started” → `account_signup`
- `user_signed_up` mails admins
- Empty `SpicyAccountAdapter`
- `web.home` still has a marketing landing for anonymous users
- `CustomUser.avatar_url` hits **gravatar.com** on a no-internet product
- `PROJECT_METADATA` keywords are `"SaaS, django"`
- Logger named `"pegasus"`
- Dead pycaches for Branch / POSProfile / Room / Table / Tax, sitemaps, icons

Anyone on the WiFi can create a login and sit on `pending_approval`. Harmless for POS access, wrong for a staff-only system.

**The judo**

Disable allauth signup. `home()` redirects anonymous users to login. Drop gravatar, landing, Pegasus metadata, empty adapter, signup form. Keep `pending_approval` only if “created but unassigned” is still a workflow.

Also delete `web.pos_index` (BUG-7).

**Where**

- `spicy/settings.py:186`, `:272–282`, `:311`
- `apps/users/models.py:27–36`
- `apps/web/views.py:7–15`
- `templates/web/landing.html`, `templates/account/login.html:18`

---

### REF-15 — POS frontend: one cart line, one order body, one navigation contract

**What is wrong**

**Grouped vs flat cart** is a duplicated template, not two presentations. `templates/pos/partials/cart/items.html` forks at `guest_count > 1`. The line body (name, comments, − / qty / +, rate, remove) is copy-pasted almost byte-for-byte, including three HTMX forms per line. Flat view already draws a “Customer #1” header, so the two layouts already look the same.

**History detail is two complete UIs** (`surface` vs `drawer`) in one template. Status pill, item row, payments, totals, and print forms are duplicated independently. The drawer drops customer grouping, so the two surfaces can disagree about the same order. `order-details-drawer.js` is fine (focus trap only). Backoffice `order_detail.html` is a **third** renderer of the same document.

**Payment dialog** stacks three Alpine `x-data` blocks. Escape / backdrop full-reloads via `window.location.href`; the X button HTMX-swaps `#pos-main`. “Apply Discount / Coming soon” plus a hardcoded ₦0.00 discount line is still in the pay dialog and the history drawer.

**History filter chips** rebuild the full query string eight times (status, payment, type, pagination). A missing `&amp;q=` is a latent filter bug.

**OOB shell nav** is copy-pasted on every POS surface (`index`, `draft_orders`, `order_history`, `order_history_detail`, `close_shift`).

**Backoffice** already has `breadcrumbs.html` and `form_field.html`, then ignores them as a layout system. List / form / document-detail pages are clones. Reports already proved the fix (`_report_header.html` + `_report_filters.html`). Menu deletes still do `hx-target="#app-content" hx-select="#app-content"` — a full-page download dressed as HTMX.

Two confirm systems: `confirm.js` (`data-confirm-*`) is the supported path; POS clear and backoffice order actions use inline `onclick="Swal.fire(...)"`.

**The judo**

1. Always iterate `guest_groups` (length 1 when `guest_count == 1`). One `{% partialdef cart_line %}`. Disable the activate POST for a single guest.
2. Shared `{% partialdef order_lines %}` / `order_payments` / `order_totals` / `order_status_pill`. Surface, drawer, and backoffice include it. Actions stay per-surface.
3. One `hx_nav` inclusion tag: `href` + `hx-get` + `hx-target` + `hx-swap` + `hx-push-url`. History chips take `history_qs` from the view.
4. One OOB nav include inside every `{% partialdef surface %}`.
5. One `paymentDialog` Alpine component. Close always `removeOnClose`. Delete the discount block.
6. Confirm is `data-confirm-*` or it does not exist.
7. Backoffice: `page_header.html`, `table_card.html`, `status_badge.html`, `document_actions.html` — includes with slots, the reports pattern. Do not invent a generic CRUD engine.
8. Deletes: ordinary POST+redirect, or a `#rows` partial like `item_list.html`. Remove the `#app-content` select/swap.

POS is `slate-*` + `teal-*`. Backoffice is `gray-*` + `orange-*`. Shared primitives need semantic classes (or `@apply`) before they can cross the two shells. `app-components.css` is leftover Pegasus (`.section`, `.help`, `.app-card`), not that design system.

**Where**

- `templates/pos/partials/cart/items.html:4` vs `:63`
- `templates/pos/order_history_detail.html` (surface 4–166, drawer 167–321)
- `templates/backoffice/orders/order_detail.html`
- `templates/pos/partials/payment/dialog.html`
- `templates/pos/order_history.html:8–47`
- `assets/javascript/confirm.js` vs inline Swal in `order_detail.html` and `totals.html:55`

---

### REF-16 — Delete leftover modes rather than wrapping them

These are whole branches/files gone:

| Leftover | Why it can go |
|---|---|
| `discard_order` | Same tombstone as `delete_unsent_draft`. Unused by POS/backoffice; only seed data and tests call it. |
| `Q(department__isnull=True, item__department=…)` | Migration `0030` made `OrderItem.department` required. Dead fallback, copied at services.py:648, :972, :993, :1131 and in P&L `sales_by_department`. |
| Empty `apps/orders/models/` and `apps/inventory/models/` directories | Only `__pycache__` remains (`order`, `kot`, `audit`, `common`, `documents`, `item`, `stock`). `models.py` currently wins the import. A stray `__init__.py` would shadow it. |
| Orphan test pyc | `test_tmp_empty_cart_check`, `test_tmp_refactor_check`, `test_performance`, `test_batch`, `test_debug_cancel`, `test_product_bundle`, `test_role_perf`, `test_profile` — no matching `.py`. |
| Empty `*ModelForm` subclasses | `MenuModelForm`, `PaymentsModelForm`, `StaffModelForm`, `AccountingModelForm`, `PayablesModelForm`, `ReportsModelForm` are `StyledModelForm` with `pass`. |
| `ModeOfPayment.can_dispense_change` | One-line wrapper, unused outside its test. |
| `SettingsModelForm` | Reimplements `StyledModelForm` with different classes. Settings is the only app that forked it. |
| Coming-soon discount UI | Pay dialog and history drawer. |

**Where**

- `apps/orders/services.py:288` (`discard_order`)
- `apps/orders/services.py:648` (null department fallback)
- `apps/orders/models/` and `apps/inventory/models/` (directories)

---

### REF-17 — Catalog add-item rules belong in the service

**What is wrong**

`pos_order_add_item` is doing catalog work in the HTTP handler: variant resolution, size availability, comment length, add-on ID parsing, and a mid-lock return that renders the add-on dialog (BUG-2). Add-on menu resolution is copy-pasted between the add-on dialog and `_variant_add_ons`.

`apply_add_on_line` already prices and inserts lines.

Active guest index is a session dict (`pos_active_cards`) mutated in five places with the same `isinstance(cards, dict)` boilerplate: new order, guest-count shrink, card activate, settle, cancel, delete. Guest **rules** are already centralized (`Order.change_guest_count`, `add_order_line` merge key, `_group_items_by_guest`). The sprinkle is session plumbing.

**The judo**

Collapse add-on resolution into one helper used by both dialogs. Move variant/add-on acceptance into `apply_add_on_line` (or `add_catalog_item` next to it). Two session helpers: `_set_active_card(request, order, idx)` and `_clear_active_card(request, order)`. Do not introduce a GuestService.

**Where**

- `apps/orders/views_pos.py:886` (`pos_order_add_item`)
- `apps/orders/views_pos.py:763–802` (add-on resolvers)
- `apps/orders/views_pos.py:425`, `:877`, `:1036`, `:1136`, `:1187`, `:1220` (session cards)

---

### REF-18 — Payment policy out of `OrderPayment.save`

**What is wrong**

The model split is clean: `ModeOfPayment` + `PaymentGLMapping` are the catalog; `OrderPayment` is the settlement line. Opening/closing/cash-out rows correctly FK the catalog.

The leak is behavior. Unique electronic `reference_no`, cash-vs-return amount rules, KOT lock, and `Restaurant.requires_payment_reference()` all live on `OrderPayment.save()`, not in `settle_order` (which already creates the rows).

Default-mode uniqueness is enforced three times: `UniqueConstraint`, `clean()`, and `save()`. Trust the constraint; keep one Python guard.

Shift expected-cash math subtracts `Order.change_amount` with a join that can mis-attribute change if an order ever has two cash rows (`staff/services.py:39–49`).

**The judo**

Keep the models split. Move reference/change policy to `orders.services.settle` (or a small payments policy helper the settle path calls). `OrderPayment.save` is persistence.

**Where**

- `apps/orders/models.py:529–568`
- `apps/payments/models.py:50–80`
- `apps/staff/services.py:39–49`

---

## Nits

Small. Do these while touching the surrounding file. Do not open a task for them alone.

### NIT-1 — Three identical production-unit account lookups

`_income_account_for`, `_expense_account_for`, `_sales_returns_account_for` are one shape: `ProductionUnit.objects.filter(department=...).first()` then a FK. Called per line. One `_unit_account(department, "income_account")` with a per-request cache.

- `apps/accounting/services.py:18`, `:28`, `:268`

### NIT-2 — Daily P&L list dates are raw GET strings

`daily_pnl_list` feeds `request.GET.get("from")` straight into `business_date__gte`. Invalid `from` is a 500. Query reports go through `report_filters.parse_date`. Same class: `gl_entry_list` does `int(account_id)` vs `int_param` in report filters.

- `apps/reports/views.py:62`
- `apps/accounting/views.py:254`

### NIT-3 — `inventory_navigation` counts item groups on every backoffice request

`apps/web/context_processors.py:29–33` queries `ItemGroup.objects.count()` to decorate chrome. Pass the count from the inventory dashboard, or cache it.

### NIT-4 — `posModalDialog` lives in `order-details-drawer.js`

Payment, add-on, variant, and cash-out all depend on it. The file name lies. Rename to `pos-dialogs.js` or split.

- `assets/javascript/order-details-drawer.js:80–115`

### NIT-5 — Spinner SVG and payment-mode icon switches are copy-pasta

The same 3-line spinner appears in cart totals, history detail, and close/open shift. One `{% partialdef spinner %}` or a CSS `[.htmx-request_&]:block` icon.

Payment-mode icon switch (CASH / BANK / PHONE / else) is duplicated in `no_shift.html` and `close_shift.html`.

### NIT-6 — Three copies of `money()` in Alpine `x-data`

`close_shift.html:11`, `no_shift.html:6`, `cash_out_dialog.html:12`, plus `toLocaleString('en-NG')` in the payment dialog. One `posMoney` helper on the shared dialog component.

### NIT-7 — `#payment_dialog` partialdef only includes `dialog.html`

`templates/pos/index.html:26–28` is an extra hop. Point the HTMX target at the dialog file, or inline the partialdef where it is used.

### NIT-8 — `#add-on-dialog-container` hosts variant dialogs too

Rename to `#pos-dialog-container`.

### NIT-9 — DaisyUI comment leftover

No DaisyUI in POS/backoffice templates. Residual comment only: `assets/styles/site-tailwind.css:33`. Delete it with the Pegasus `.section` / `.help` / `.app-card` rules in `app-components.css`.

### NIT-10 — Docs still describe FIFO in two places

Runtime is PWAC. `docs/database/transactions.md` and `docs/architecture/overview.md` still mention a FIFO queue / a nonexistent `_reverse_voucher`. Update them when REF-1 lands.

### NIT-11 — `_ticket_type_for_department` maps anything that is not FOOD to bar

Unexpected department silently becomes a bar ticket. Keep the mapping exhaustive over `{FOOD, DRINKS}` and raise on anything else.

- `apps/orders/services.py:1217`

### NIT-12 — `report_views._range` is a one-line wrapper around `report_filters.date_range`

Delete the alias; call the canonical helper.

- `apps/reports/report_views.py:12`

---

## Recommended sequence

Do these in order. Each step is independently shippable and makes the next step smaller.

| Step | What | Why this order |
|---|---|---|
| 1 | **BUG-1** — drop view-level `select_for_update` on cart +/- and guest stepper | Production 500 on the hottest POS path. Small. |
| 2 | **BUG-5** — pass `rows=preview.lines` on Daily P&L preview | Visible backoffice break. One-line fix. |
| 3 | **BUG-7** — delete `web.pos_index`; reverse `pos:pos_home` | Removes a landmine before URL work. |
| 4 | **BUG-3 + BUG-4 + REF-1** — one reverse policy for stock documents | Transfer and recon cancel stop drifting from receipt cancel. |
| 5 | **BUG-2** — print after commit on settle; release the add-item lock before dialog render | Matches the Send-button path that already works. |
| 6 | **REF-6** — shared `post_voucher` / `reverse_voucher` in accounting | Inventory and order GL stop merging with two different semantics. |
| 7 | **REF-2 + REF-7** — food usage into reports; one Daily P&L statement object; fix GP (BUG-6) | Header, table, and preview stop being three independent writes. |
| 8 | **REF-10 + BUG-8** — `open_shift()` only writer; `objects.open()` manager | POS and backoffice cannot open a shift under different rules. |
| 9 | **REF-11** — one variant graph; `assert_item_shape` | Catalog grouping and “choose a size” cannot disagree. |
| 10 | **REF-15** — one cart line, one order body | Grouped/flat and surface/drawer stop rendering different documents. |
| 11 | **REF-4 + REF-17** — cart context vs catalog; add-item rules in the service | Hot POS paths stop rebuilding the menu; variant/add-on rules live in one place. |
| 12 | **REF-14 + REF-16** — delete SaaS shell and leftover modes | Live path matches a LAN restaurant POS. |

If only three things happen: **1, 2, and 4**. Those are a production 500, a blank statement, and stock-cancel drift.

---

## Keep as-is

These look like duplication or missing apps. They are intentional. File length is also not a reason to split them.

| Decision | Why it stays |
|---|---|
| `views_pos.py` and `inventory/views.py` as one module each | Many short functions, each one HTTP action, writes already in services. Splitting into `catalog.py` / `cart.py` / `stock_entry.py` does not make a handler easier to find. Split a **function** when it is doing several jobs (REF-4, REF-17), not the file. |
| Payables live in `apps.accounting` | `PLAN.md` forbids a new app. Move submit/cancel off the models (REF-9). Do not extract `apps.payables`. |
| Purchase Receipt and Stock Entry MATERIAL_RECEIPT are two documents | GRNI/supplier vs cash-now market purchase are different GL stories (FEATURES.md). Share the inbound-line helper and the posting engine (REF-1, REF-3). Keep two headers. |
| Simple P&L vs Daily P&L | Simple P&L is GL income − expense. Daily P&L is the owner’s operational sheet (recipe vs counts, drinks WAC, memos). Rename the sidebar label if the names collide. Do not post Daily P&L to the GL. |
| Refund is a separate leg builder | Sales-returns and wastage are different accounts. Share the **pipeline** (REF-6). |
| `MessagesMiddleware` | Focused HTMX adapter for Django messages. Not a god middleware. |
| `add_formset_row` / `remove_formset_row` | Canonical. Inventory, accounting, and reports already use them. Do not wrap them. |
| Staff role assignment under Settings | Documented. Correct home. |
| Business-day window vs calendar `posting_date` | P&L uses restaurant opening hour. Query reports use calendar dates. Two clocks, on purpose. |
| `Bin.reserved_qty` as a non-SLE field | Reservations are promises to open drafts, not movements. Keep it, under the same bin lock (BUG-10). |
| No DaisyUI, no DRF, no React | Project constraints. Frontend debt is copy-paste inside HTMX partials, not a missing SPA. |

---

## Appendix: issue index

| ID | Category | One-line |
|---|---|---|
| BUG-1 | Bug | Cart +/- and guest stepper: `select_for_update` in autocommit |
| BUG-2 | Bug | Settle prints tickets inside the money transaction |
| BUG-3 | Bug | Transfer cancel can CANCELLED without reversing stock |
| BUG-4 | Bug | Recon cancel mirrors original GL after WAC has moved |
| BUG-5 | Bug | Daily P&L refresh preview blanks the table |
| BUG-6 | Bug | Gross profit food + drinks ≠ total |
| BUG-7 | Bug | `/pos/` registered twice; `web.pos_index` is a trap |
| BUG-8 | Bug | Backoffice can open a shift with no Restaurant |
| BUG-9 | Bug | ORM/admin can SUBMITTED a stock document with no SLE/GL |
| BUG-10 | Bug | Raw SLE create skips Bin |
| BUG-11 | Bug | Refund GL no-ops when source voucher is gone |
| REF-1 | Refactor | One inventory `post_movements` / `reverse_voucher` |
| REF-2 | Refactor | Food usage out of inventory posting |
| REF-3 | Refactor | One inbound line, two headers |
| REF-4 | Refactor | Cart HTMX rebuilds the full catalog on every guest tap |
| REF-5 | Refactor | Lifecycle in services; drop `_allow_*` flags |
| REF-6 | Refactor | One public `post_voucher` / `reverse_voucher` |
| REF-7 | Refactor | One Daily P&L statement object |
| REF-8 | Refactor | One calendar queryset for query reports |
| REF-9 | Refactor | Payables workflow off the models |
| REF-10 | Refactor | One shift writer; `objects.open()` |
| REF-11 | Refactor | One Item family; one shape check |
| REF-12 | Refactor | Restaurant as data, not an oracle |
| REF-13 | Refactor | Cached `user.roles`; collapse duplicate decorators |
| REF-14 | Refactor | Delete SaaS/Pegasus live path |
| REF-15 | Refactor | One cart line, one order body, one `hx_nav` |
| REF-16 | Refactor | Delete leftover modes and empty `models/` packages |
| REF-17 | Refactor | Catalog add-item rules in the service |
| REF-18 | Refactor | Payment policy out of `OrderPayment.save` |
| NIT-1 … NIT-12 | Nit | See [Nits](#nits) |
