# Verified Findings — Consolidated Check of `AUDIT_REPORT.md` and `CODE_REVIEW.md`

**Verification date:** 2026-09-26
**Verified against:** working tree at commit `d774b7e` (branch `main`), with only `AGENTS.md` modified.
**Sources checked:** both documents in full, plus every source file, template, asset and migration needed to prove or disprove each claim.
**No application code was changed.**

---

## How to read this document

Each finding from either report was placed in one of four buckets:

| Status | Meaning |
|---|---|
| **Confirmed** | The current code really does what the report says. |
| **Partially correct** | The concern is real, but the report describes it wrongly, or part of the problem was already fixed, or the remaining risk is smaller/larger than stated. |
| **Incorrect** | The current code does not support the claim. Usually an outdated line reference, a misread of a migration, or a claim about safety that the code contradicts. |
| **Already fixed** | The problem existed when the report was written but has since been fixed by a later commit. |

**Important context.** The audit is dated 2026-09-10 and the code review 2026-09-25. Between them, and after them, about 40 commits landed (`2026-09-14` to `2026-09-26`) that fixed many of the reported problems — for example `a0b76ab` (stock ledger and cancellation hardening), `725c406` (refund and reversal GL), `32266cf` (same-account sale legs), `570bb99` (per-mode cash variance), `ccd2c6d` (draft ownership), `456f139` (shift close ownership), `aec290e` (rounding), `3716977` (monthly expense slices) and `f410caa` (booked stock values). A fact-check today must therefore say "already fixed" much more often than either report could know.

Parts 1–3 are the substance: what is still wrong, what is already fixed, and what was reported incorrectly. Part 4 checks the refactor proposals one by one. Part 5 checks the nits. "What the code is doing now" always describes the *current* code, not the code at the time of the report.

A note on line numbers: both reports cite line numbers that have since shifted. This document uses current line numbers, verified by reading the files.

---

## Status at a glance

| # | Finding (short) | Reported in | Status |
|---|---|---|---|
| F1 | Cart +/- and guest stepper crash with `TransactionManagementError` | CODE_REVIEW BUG-1 | **Confirmed** |
| F2 | Stored XSS: user name flows into SweetAlert `html:` dialog | AUDIT §7 + CODE_REVIEW REF-15 (both understated) | **Confirmed** |
| F3 | Stock document status and ledger rows can be written through the ORM/admin with no SLE/GL/Bin update | CODE_REVIEW BUG-9 + BUG-10 | **Confirmed** |
| F4 | Reconciliation cancel mirrors old GL amounts after WAC has moved | CODE_REVIEW BUG-4 | **Confirmed** |
| F5 | Transfer cancel can skip lines and still mark the document CANCELLED | CODE_REVIEW BUG-3 | **Confirmed** (one case now fails closed) |
| F6 | Settlement prints tickets inside the money/stock transaction; add-item holds a row lock across a template render | CODE_REVIEW BUG-2 + AUDIT §6 | **Confirmed** |
| F7 | Daily P&L "Refresh preview" renders an empty table; invalid preview dumps the whole form into the preview slot | CODE_REVIEW BUG-5 | **Confirmed** |
| F8 | Daily P&L Gross profit: Food + Drinks ≠ Total when unallocated costs exist | CODE_REVIEW BUG-6 | **Confirmed** |
| F9 | Refund GL silently posts nothing if the source order's GL is missing | CODE_REVIEW BUG-11 | **Confirmed** |
| F10 | Backoffice can open a shift with no Restaurant configured | CODE_REVIEW BUG-8 | **Confirmed** |
| F11 | `/pos/` is registered twice; `web:pos_index` is still in use | CODE_REVIEW BUG-7 | **Confirmed** |
| F12 | A cashier can cancel their own sent order with no manager approval | AUDIT HIGH-2 | **Confirmed** (now ownership-scoped) |
| F13 | Electronic payment reference may be blank by default | AUDIT HIGH-1 | **Confirmed behaviour; documented design decision** |
| F14 | POS close has no variance-note input, so a manager cannot close a large variance from the POS; threshold defaults to "no gate" | AUDIT HIGH-4 | **Partially correct** |
| F15 | Reconciliation write-offs have no mandatory note or second approval | AUDIT HIGH-3 | **Partially correct** (actors now stamped) |
| F16 | P&L drink COGS follows the live `Item.department`, sales follow the snapshot | AUDIT §4 | **Confirmed** |
| F17 | Insecure boot defaults (`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`); production settings file never selected | AUDIT §5 | **Confirmed** |
| F18 | Open self-signup with no approval gate | AUDIT §5 + CODE_REVIEW REF-14 | **Confirmed** |
| F19 | Hardening flags, email verification, avatar validation, seed credentials, role management, printer IP, personal data | AUDIT §5 | **Confirmed** (grouped) |
| F20 | Submitted shift documents remain editable at the ORM layer | AUDIT §3 LOW | **Confirmed** (latent) |
| F21 | Calendar query reports each build their own filters | CODE_REVIEW REF-8 | **Confirmed** (one sub-claim wrong) |
| F22 | Small confirmed nits (NIT-1 … NIT-12 and audit §6 items) | both | **Confirmed** (see table) |
| — | All other audit findings | AUDIT | **Already fixed** (Part 2) |
| — | REF-1 … REF-18 | CODE_REVIEW | **Valid proposals** with corrections (Part 4) |
| — | `can_dispense_change` unused; migration 0030 made `department` required | CODE_REVIEW REF-16/REF-8 | **Incorrect** (Part 3) |

---

# Part 1 — Confirmed problems to fix

## Finding 1: Cart quantity buttons and the guest stepper can crash the POS

**Reported in:** `CODE_REVIEW.md` BUG-1. **Status: Confirmed.**

**What is wrong.** Two POS endpoints ask the database to lock an order row before they check whether the request is allowed. They do that outside the "all-or-nothing" database block that locking requires. On PostgreSQL (the database this project uses) that combination raises an error and the request returns HTTP 500.

**Where it occurs.**
- `apps/orders/views_pos.py:861-864` — `pos_order_update_meta` (the Guests +/− stepper and the order-type buttons).
- `apps/orders/views_pos.py:1010-1013` — `pos_order_update_item` (the cart + / − quantity buttons and remove).
- The services they call: `apps/orders/services.py:107-110` (`update_order_item` locks internally) and `apps/orders/services.py:77-104` (`update_order_meta` does **not** lock internally).
- Django's own check: `django/db/models/sql/compiler.py` raises `TransactionManagementError("select_for_update cannot be used outside of a transaction.")` when the connection is in autocommit mode. Verified in the installed Django 6.0.5.

**Why it is a problem.** A transaction is a batch of database work that either all succeeds or all fails. `select_for_update()` is the instruction "put a write lock on this row until my transaction ends". PostgreSQL only accepts that instruction inside a transaction. These two views call `get_object_or_404(Order.objects.select_for_update()...)` without wrapping the request in `transaction.atomic()`, so Django evaluates the query in autocommit mode and raises. In plain terms: the cashier taps **+** on a cart line and gets a server error instead of a quantity change. The same is true for the guest stepper.

A concrete example:

```text
Cashier taps + on a line
        ↓
views_pos.pos_order_update_item
        ↓
Order.objects.select_for_update().open_drafts_for(...)   ← evaluated here, no atomic()
        ↓
TransactionManagementError → HTTP 500
```

The reason tests stay green is that `django.test.TestCase` wraps every test in a transaction, so inside tests the lock *is* legal. The failure only appears in a real request. There is no `ATOMIC_REQUESTS = True` in `spicy/settings.py`, so in production the request is genuinely outside a transaction.

**What the code is doing now.** The two views fetch-and-lock the order, then call the service. Sibling views (`pos_order_add_item`, `pos_order_sync`, `pos_order_clear`, `pos_order_cancel`, `pos_order_delete`) already wrap the same fetch in `with transaction.atomic()`, which is why they do not fail.

**Recommended fix.** Remove the view-level `select_for_update()` from both views and let the service own the lock. For `update_order_item` that already works. For `update_order_meta` the report's claim that the service already locks is wrong — it does not. So the fix has two parts:

1. In `apps/orders/services.py`, make `update_order_meta` re-fetch the order with `select_for_update()`, exactly like `update_order_item` does.
2. In `apps/orders/views_pos.py`, change both views to a normal authorized fetch (`Order.objects.open_drafts_for(shift, request.user)`), no `select_for_update`.

**How the fix works.**

1. A service function is decorated with `@transaction.atomic`, so the lock happens inside a real transaction and PostgreSQL accepts it.
2. The view's job stays "authorize and dispatch"; the service's job is "lock, re-check, change".
3. Other cashiers who try to change the same order at the same moment wait for the lock instead of both writing at once.

**Important considerations.**
- Add a regression test that uses `TransactionTestCase` (or explicitly turns autocommit back on) so this class of bug cannot hide again. Normal `TestCase` cannot catch it.
- The `.open_drafts_for(...)` helper still matters for authorization: managers may work on all drafts, cashiers only on their own. Keep it in the view.
- Do not add `ATOMIC_REQUESTS = True` as a shortcut: it would wrap every request, including long ones, in one transaction and change many behaviours. The service-level fix is smaller and matches the existing pattern.

---

## Finding 2: A user's name can execute JavaScript in an admin's browser

**Reported in:** `AUDIT_REPORT.md` §7 ("safe today, fragile") and `CODE_REVIEW.md` REF-15 (same claim). Both reports say Django's auto-escaping makes this inert. **That safety assessment is wrong — the issue is a real stored XSS. Status: Confirmed.**

**What is wrong.** Several admin buttons put a user-controlled display name inside an HTML attribute, and the confirmation popup then renders that attribute value as raw HTML. Anyone who can edit their own first/last name (every logged-in user can) can store a script payload that runs when a superuser clicks a button for that name.

**Where it occurs.**
- `assets/javascript/confirm.js:15-25` — `confirmMessage()` reads `element.dataset.confirmMessage`, and `Swal.fire({ html: confirmMessage(element), ... })` passes it to SweetAlert2's `html` option, which inserts raw HTML.
- `templates/backoffice/settings/staff_list.html:74-91` — every role/activation button has `data-confirm-message="... <strong>{{ entry.user.get_display_name }}</strong> ..."`.
- `templates/backoffice/menu/menu_detail.html:120` — `data-confirm-message="Remove {{ mi.item_name }} from this menu?"`.
- `apps/users/forms.py:24-29` — `CustomUserChangeForm` exposes `first_name` and `last_name`; `apps/users/views.py:12-19` (`profile`) accepts them for any logged-in user.

**Why it is a problem.** The browser decodes HTML entities inside attribute values. Django auto-escaping writes `&lt;img ...&gt;` into the attribute; when the page is parsed, that becomes the literal string `<img ...>` in `element.dataset.confirmMessage`. SweetAlert2's `html:` option then inserts that string as HTML — so the "escaped" payload becomes live markup again. Example:

```text
Cashier sets First name to: <img src=x onerror=fetch('/users/profile/')>
        ↓
staff_list.html renders: data-confirm-message="Assign <strong>&lt;img src=x onerror=...&gt;</strong> as Cashier?"
        ↓
Browser stores dataset.confirmMessage = "Assign <strong><img src=x onerror=...></strong> as Cashier?"
        ↓
Superuser clicks "Assign as Cashier"
        ↓
Swal.fire({html: ...}) inserts the <img> tag → onerror runs in the superuser's session
```

The self-signup path (Finding 18) makes it worse: any person on the WiFi can create a login, set a name, and wait for the superuser to open the staff page. There is no Content Security Policy configured to blunt the impact.

**What the code is doing now.** `confirm.js` supports both `data-confirm-message` (HTML allowed) and `data-confirm-body`. Call sites interpolate names and item names into the HTML string. Django escapes them once; the attribute decode undoes that protection at the `html:` sink.

**Recommended fix.** Treat the confirmation message as text, not HTML, and keep the intentional styling elsewhere:

1. In `confirm.js`, switch `html:` to `text:` for the message (SweetAlert2 renders `text` as plain text).
2. Where bold styling is wanted (staff list), pass the name through a separate, escaped slot or keep the static words as HTML and inject the name via `text`/DOM APIs. The simplest correct version is `title` + plain `text`.
3. Optionally add a small server-side guard: strip `<`/`>` from first/last names in the profile and staff-create forms so the data is clean at the source too.

**How the fix works.** With `text:`, SweetAlert2 creates a text node instead of parsing HTML, so `<img ...>` is displayed as characters and never executed. Removing user data from the `html:` path removes the injection point; the static `<strong>` wrappers are lost, which is a cosmetic trade for a real fix.

**Important considerations.**
- This is a security fix, not just cleanup: any logged-in account can plant the payload and the target is a superuser.
- Audit all `data-confirm-*` uses that include `{{ }}` values, not only the staff list (menu items are also user-controlled).
- If an HTML-rich confirm is truly needed later, sanitise server-side and keep the raw text out of attributes; do not rely on template autoescaping across an attribute → `html:` boundary.
- Add a test asserting the dialog contains the literal payload string (e.g. `<img`) rather than executing it.

---

## Finding 3: The stock ledger and document status can be bypassed through the ORM or admin

**Reported in:** `CODE_REVIEW.md` BUG-9 (status can be flipped) and BUG-10 (raw ledger rows skip Bin). These are the same underlying problem — the database is writable without going through the services that keep it consistent — so they are combined here. **Status: Confirmed.**

**What is wrong.** Two related holes exist:

1. A `StockEntry`, `StockReconciliation` or `PurchaseReceipt` can be set to `SUBMITTED` by calling `.save()` (or from the Django admin) without running `submit_*`. The result is a "submitted" document with no stock movement, no Bin change, and no GL.
2. A `StockLedgerEntry` can be created with `StockLedgerEntry.objects.create(...)`, which writes the audit row but never touches the `Bin` (the running stock snapshot). Later operations trust the Bin, so the ledger and the Bin silently diverge.

**Where it occurs.**
- `apps/inventory/models.py:515-526` (`StockEntry.save`), `:740-749` (`StockReconciliation.save`), `:828-837` (`PurchaseReceipt.save`). All three only reject changes to documents that are *already* submitted or cancelled. They allow `DRAFT → SUBMITTED`, and on create they never validate the starting status at all.
- `apps/inventory/models.py:340-346` — `StockLedgerEntry.save()` rejects updates and deletes, but a `create` has no primary key yet, so the guard does not fire. `_create_entry_locked` itself inserts with `cls.objects.create(...)` at `:463`.
- `apps/inventory/admin.py:187-193` and the reconciliation/receipt admins — the `status` field is editable on draft documents.
- `apps/inventory/tests/test_admin_bypass.py:18-21` proves the status hole by doing exactly the forbidden thing.
- `apps/inventory/tests/test_ledger_guards.py::test_create_still_allowed` proves the create hole explicitly.

**Why it is a problem.** The project's rule is "stock only moves through the services; the ledger is the truth and the Bin is its current total". These two holes break the rule.

Concrete example (status flip):

```text
Superuser opens a DRAFT Stock Entry in admin, sets status = SUBMITTED, saves.
        ↓
No SLE rows, no Bin change, no GL.
        ↓
Reports show a submitted receipt; stock never arrived.
```

Concrete example (raw ledger create):

```text
Shell: StockLedgerEntry.objects.create(item=Rice, warehouse=Store, quantity=10)
        ↓
Ledger now shows +10 Rice; Bin.actual_qty is still 0.
        ↓
A later sale checks the Bin, sees 0, and refuses to sell.
```

**What the code is doing now.** Status is a plain editable field with a save guard that only protects already-submitted rows. `StockLedgerEntry.save()` protects updates and deletes (a real improvement from commit `a0b76ab`), but inserts bypass it. `Bin` has no model-level guard at all; it is only protected by the admin classes.

**Recommended fix.**

1. Treat document status like the order status: a model `save()` may only change status when a private flag set by the service is present (`_allow_submit`, `_allow_cancel` or similar). The pattern already exists on `Order` and `JournalEntry`, so use it.
2. Reject creation of a stock document with `status != "DRAFT"` in `save()`.
3. Make `status` read-only in the document admins (the `SubmittedDocumentAdminMixin` currently only freezes already-submitted rows).
4. For the ledger: make raw `StockLedgerEntry.objects.create(...)` unusable from business code. Either route `objects.create` through `create_entry` or require an explicit token that only `_create_entry_locked` sets. Because `_create_entry_locked` itself uses `objects.create`, the private classmethod needs to call `super()`/a private manager method instead of the guarded public one (otherwise it would block itself).

**How the fix works.**

1. Services keep doing the real work, then set the flag and save the status in the same transaction. Anyone else who tries to write a status gets a clear `ValidationError` instead of silence.
2. The ledger becomes append-only through one door. A create without the token raises, so a future developer who forgets the Bin update sees an error immediately rather than a slow data drift.

**Important considerations.**
- Existing tests and data migrations write statuses and create SLEs directly; they must be updated to use the services or the private flags. Budget for test churn.
- `Bin` is partly protected already (`BinAdmin` is fully read-only; `actual_qty`/`valuation_rate` are only written by `_create_entry_locked`). Decide whether to add a model guard there too; a shell user can always bypass Python guards, so the aim is to stop accidental and admin-driven writes, not to stop a determined person with database access.
- Keep `Bin.reserved_qty` as the one non-ledger field, updated only under the bin lock (the code already does this).
- This is a good candidate to fix together with REF-1 (one movement/reversal engine), because the same guard pattern serves all three documents.

---

## Finding 4: Cancelling a reconciliation puts the value back at today's price but reverses yesterday's GL

**Reported in:** `CODE_REVIEW.md` BUG-4. **Status: Confirmed.**

**What is wrong.** When a submitted reconciliation (consumption, adjustment, waste) is cancelled, the stock quantity is returned at the *current* weighted-average cost, but the GL reversal copies the *original* amounts. If the cost per unit changed in between, the stock value in the ledger and the stock value in the accounts drift apart, and nothing records the difference.

**Where it occurs.**
- `apps/inventory/services.py:877-943` — `cancel_stock_reconciliation`.
  - Reversal SLEs are created with `unit_rate=None` (current WAC) at `:903-914`.
  - The GL reversal swaps debit and credit on the original rows at `:917-938`.
- Contrast with the receipt/stock-entry cancels, which compute the drift and stamp it as `CANCELLATION_WAC` to the inventory price-variance account (`apps/inventory/services.py:623-643`, `:656-683`).

**Why it is a problem.** A worked example:

```text
Kitchen receives 10 kg @ ₦100 → Kitchen Bin value = ₦1,000, SIH (kitchen) = ₦1,000.
Consumption of 5 kg is filed → SLE −5 @ ₦100, GL Dr expense ₦500 / Cr SIH ₦500.
Later, a new delivery raises the Kitchen WAC to ₦200.
Cancel the consumption:
  - Stock reversal: +5 @ ₦200 = ₦1,000 back into the Bin.
  - GL reversal:   Dr SIH ₦500 / Cr expense ₦500 (mirror of the original).
Result: the Bin says the stock is worth ₦1,000 more, the accounts say ₦500 more — ₦500 of value is unattributable.
```

This also makes a later purchase-receipt cancel compute its variance from a Bin whose value no longer matches the GL, so the error compounds.

**What the code is doing now.** `cancel_stock_reconciliation` reverses quantity at current WAC, then marks the original GL rows cancelled and posts a straight debit/credit swap of those rows. No variance amount is written on the reversal SLE (unlike receipt cancel), and no variance account is used.

**Recommended fix.** Give reconciliation cancel the same reversal policy the other documents already use:

1. For each original SLE, read the Bin's current WAC *before* the reversal.
2. Post the stock-back reversal at current WAC (as now).
3. Re-post the counter-account (expense/adjustment/opening/wastage) at the original booked value.
4. Post the difference to `Restaurant.inventory_price_variance_account` and stamp the reversal SLE with `variance_amount`/`variance_type="CANCELLATION_WAC"`.

This is exactly the shape of the receipt-cancel branch at `apps/inventory/services.py:656-683`, so extracting it into a shared helper (REF-1) avoids a third copy.

**How the fix works.**

1. "Current value of the stock coming back" and "value originally booked" are compared.
2. The stock-in-hand account moves by today's value, the expense/counter account moves by the original value, and the variance account absorbs the difference.
3. The books stay balanced and the Balances page matches the Bins.

**Important considerations.**
- Which account is the "counter account" depends on the reconciliation reason (opening, adjustment, consumption, wastage). The original GL rows already tell you which account was used; keep using them for the counter side.
- Add a test with a WAC change between submit and cancel, asserting the variance row and the Balanced GL.
- The same policy must apply to `WASTE_DAMAGE` consumption rows.

---

## Finding 5: A transfer can be marked CANCELLED without actually taking the stock back

**Reported in:** `CODE_REVIEW.md` BUG-3. **Status: Confirmed** (one case now raises, the rest still silently skip).

**What is wrong.** When a submitted Store → Kitchen/Bar transfer is cancelled, the cancel code rebuilds the route from *current* settings instead of using the source/target warehouses stored on the document. If a production unit's warehouse has been changed, or a bin is missing, it skips that line with `continue` — and then still flips the document to `CANCELLED`. The stock stays in the destination while the paperwork says the transfer never happened.

**Where it occurs.**
- `apps/inventory/services.py:462-606` — `cancel_stock_entry`, transfer branch.
  - At submit, the real route is snapshotted onto each line: `detail.source_warehouse = restaurant.store_warehouse` / `detail.target_warehouse = target` (`:413-414`).
  - At cancel, those snapshots are ignored: `targets` is rebuilt from the current `ProductionUnit` rows and `Restaurant.default_warehouse` (`:477-488`).
  - `if not tgt: continue` (`:507-508`) and `if not dest_bin or not store_bin: continue` (`:512-513`).
  - After the loop the document is set to `CANCELLED` (`:689-691`).
  - `orig_value = curr_value` when the original store SLE is missing (`:544-546`), which also suppresses the variance.
- Snapshot fields exist on the model: `apps/inventory/models.py:545-558`.

**Why it is a problem.** Example:

```text
Manager transfers 10 bottles from Store to Bar. Bar is the destination snapshot on the lines.
Later, the Drinks production unit warehouse is changed (or the Bar bin row is removed).
Manager cancels the transfer:
  - targets["DRINKS"] now points at a different warehouse,
  - that warehouse has no bin for the bottle → `continue`,
  - the 10 bottles stay wherever they physically are,
  - the document says CANCELLED.
Store stock is never restored. The GL originals are marked cancelled only if reversal rows were built.
```

A partial failure is worse than a hard failure: the operator believes the transfer is gone.

The transfer branch's insufficient-stock guard added in `a0b76ab` (`:514-519`) is a genuine improvement — it stops a cancel when the destination has already consumed the stock. The remaining hole is the silent skipping of lines that cannot be routed at all.

**What the code is doing now.** Cancel re-derives warehouses, skips lines it cannot route, posts GL only for the lines it processed, and marks the whole document cancelled.

**Recommended fix.**

1. Reverse each line from its snapshotted `source_warehouse`/`target_warehouse`, falling back to the original SLE rows (`voucher_type="Stock Entry"`, `voucher_no`, positive quantity for the destination, negative for the Store).
2. If a target warehouse, a bin, or the original SLE pair is missing, raise `ValidationError` inside the atomic block. Do not `continue`.
3. If the original Store SLE is missing, raise instead of inventing `orig_value = curr_value`.

**How the fix works.**

1. The document records where stock actually went; cancel uses that record, not today's configuration.
2. A cancel either reverses everything or changes nothing (the whole method is `@transaction.atomic`), so the operator sees an error and can investigate rather than losing stock.
3. A missing original value is a broken-data signal, not something to paper over.

**Important considerations.**
- Test the "warehouse reassigned after submit" case and the "bin deleted" case; both should raise and leave the document `SUBMITTED`.
- This is also the moment to extract one `reverse_voucher` helper (REF-1), so receipt, transfer and reconciliation cancels share one policy.

---

## Finding 6: Settlement prints paper inside the money/stock transaction, and the add-item lock is held while a dialog renders

**Reported in:** `CODE_REVIEW.md` BUG-2 (plus the add-on dialog part), `AUDIT_REPORT.md` §6 (printing inside settlement). **Status: Confirmed.**

**What is wrong.** Two related "doing slow work while holding a database lock" problems:

1. `settle_order` is one big transaction. If the order was never sent, it creates the kitchen/bar tickets **and prints them** before creating payment rows, converting drink reservations, and posting the GL. Printing is external I/O (a network call to the print agent in the future). If anything after the print fails, the database rolls back but the paper is already out.
2. `pos_order_add_item` holds the order row lock while it returns the add-on dialog template (a render inside the `transaction.atomic()` block), so the lock lives as long as the template render.

**Where it occurs.**
- `apps/orders/services.py:152-236` — `settle_order`, decorated `@transaction.atomic`.
  - `dispatch_tickets(created)` inside the transaction at `:194-202`.
  - `dispatch_tickets` itself calls `printing.print_ticket(ticket)` and then writes `print_status` (`:625-640`).
- `apps/orders/views_pos.py:892-975` — `with transaction.atomic():` opens at `:892`; the add-on dialog is rendered and returned at `:960-975` before the block exits.
- The correctly-shaped reference is `pos_order_sync`, which creates tickets inside a small `atomic()` and prints **after** it (`apps/orders/views_pos.py:1051-1066`).

**Why it is a problem.** Today `apps/orders/printing.py` is a stub that always returns success (`:15-21`), so the cashier sees nothing wrong. The moment a real LAN print agent exists:

```text
Cashier settles a ₦3,000 cash order.
  → tickets created and printed (paper out)
  → payment row insert fails / GL account misconfigured → ValidationError
  → transaction rolls back: no order, no payment, no KOT rows
Kitchen already has the ticket and starts cooking an order the system never recorded.
```

The reverse case is also real: the printer call holds the order and shift row locks for the duration of the HTTP request, so other cashiers touching that shift wait.

**What the code is doing now.** Tickets are created and dispatched inside the settle transaction; the add-on dialog is rendered inside the add-item transaction. `docs/architecture/side-effects.md` states the intended rule: ticket creation commits before each physical print attempt (the Send button follows it; settlement does not).

**Recommended fix (matching the Send button's shape).**

1. In `settle_order`, create KOT rows inside the transaction but do **not** print. Return the created tickets (or their ids) to the caller.
2. In `pos_order_settle`, after `settle_order` returns (commit done), call `services.dispatch_tickets(kots)`.
3. For the add-on dialog, resolve whether a dialog is needed *before* taking the lock, or render it after the `atomic()` block exits. The variant path already retargets with `HX-Retarget`; it can do so outside the lock.

**How the fix works.**

1. Money, stock and GL either commit or fail as one unit; printing happens strictly after, so a failed print never rolls back a sale and a failed sale never prints.
2. A print retry is already modelled by `print_status` (`PENDING`/`PRINTED`), so printing after commit fits the existing retry UI.
3. Releasing the add-item lock before rendering means a second cashier can change the same order while the first one's dialog is open — the service still re-checks `_ensure_editable()` on save, so the data stays safe.

**Important considerations.**
- Keep the order of effects clear: create KOT rows → commit → print. Do not use `transaction.on_commit` for work that must return a result to the view; return the tickets and let the view print, exactly like `pos_order_sync`.
- When the real agent lands, a print failure after a successful settle must be visible: `pos_order_sync` already returns `print_failures`; reuse that feedback.
- Update `docs/architecture/side-effects.md` if the settlement flow changes.

---

## Finding 7: The Daily P&L "Refresh preview" button blanks the statement

**Reported in:** `CODE_REVIEW.md` BUG-5. **Status: Confirmed.**

**What is wrong.** The statement template prints its rows from a variable called `rows`. The first page load passes `rows=preview.lines`. The preview endpoint does not pass `rows` at all, so after clicking **Refresh preview** the table has a header and no body. On top of that, if the preview form is invalid, the view returns the **whole** form page into the small preview area.

**Where it occurs.**
- `templates/backoffice/reports/_statement.html:22` — `{% for line in rows %}`.
- `templates/backoffice/reports/daily_pnl_form.html:47-51` — the initial include is `{% include "backoffice/reports/_statement.html" with rows=preview.lines %}`.
- `apps/reports/views.py:199-203` — the success branch renders `_statement.html` with `{"preview": preview, "pnl": pnl, "food_usage_counted": ...}` and no `rows`.
- `apps/reports/views.py:204-208` — the invalid-form branch renders `daily_pnl_form.html` into the `#pnl-preview` target.

**Why it is a problem.** The feature's normal use is "change meter readings, hit Refresh preview, check the numbers before saving". The most important screen silently shows nothing. The amber "food usage not counted" banner still appears because that flag *is* passed, which makes the blank table look deliberate rather than broken. The invalid-form path nests a full page (with its own forms and buttons) inside a fragment target, producing nested forms and broken HTMX from then on.

**What the code is doing now.** `daily_pnl_preview` saves the form, recomputes the statement, and renders the statement partial without the `rows` variable that the partial requires.

**Recommended fix.**

1. In the success branch, pass `rows=preview.lines` (the `LineSpec` objects have the same fields the template reads).
2. In the invalid-form branch, render `_statement.html` with `preview_error` (the template already has a red error box for that variable at lines 7-8), or return the form with an `HX-Retarget` header if the design really wants the full form.
3. Never put the full form page into `#pnl-preview`.

**How the fix works.**

1. The partial now receives the data it iterates over, so the table is repopulated after refresh.
2. A validation error is shown as a message inside the preview area, keeping the page structure intact.

**Important considerations.**
- Add a template-level test: POST the preview URL and assert an expected statement line (e.g. "Gross sales") is present in the response, not just a 200.
- If `preview_error` is used, keep `food_usage_counted` in the context so the warning banner still shows.

---

## Finding 8: The three-column Gross profit row does not add up

**Reported in:** `CODE_REVIEW.md` BUG-6. **Status: Confirmed.**

**What is wrong.** In the Daily P&L statement the Food and Drinks columns are computed without the unallocated costs (electricity, unassigned materials, ad-hoc amounts with no department), while the Total column subtracts them. The result is a row that looks like "Food + Drinks = Total" but is not.

**Where it occurs.**
- `apps/reports/services.py:158-204` — `direct_food`/`direct_drinks` only receive amounts whose template or ad-hoc row has a department (`_split()` at `:45-50` returns `(0, 0, amount)` for no department). `gp_food = food - food_actual - direct_food` (`:201`), `gp_drinks = drinks - cogs_drinks - direct_drinks` (`:202`), `gp = net - cogs - direct_total` (`:203`).
- `net` includes the round-off adjustment (`:90`), which is also only in the total column.
- Prime cost has the same shape: `food_actual` / `cogs_drinks` in the department columns, `cogs + employee_total` in total (`:226-227`).

**Why it is a problem.** Example, as the report showed:

| | Food | Drinks | Total |
|---|---:|---:|---:|
| Gross profit | 1,000 | 800 | 1,600 |

Food + Drinks = 1,800, but the total is 1,600 because ₦200 of electricity was subtracted only from the total. A manager reading the sheet adds the first two columns in their head and gets a different number every time. The sheet loses trust.

**What the code is doing now.** Department columns contain only what can be attributed to a department. The total column contains the true profit including everything. Nothing on the page explains the gap.

**Recommended fix.** Make the total column visibly the sum of the parts:

1. Keep `gp_food`/`gp_drinks` as department-only figures.
2. Add a visible "unallocated direct costs" line in the total column (electricity, unassigned materials, round-off).
3. Define the total as `gp_food + gp_drinks − unallocated − round_off`, so the column arithmetic is checkable.
4. For the prime-cost memo, either show the employee split in the department columns or leave the department cells blank (`—`) for that row, since employees are not allocated per department.

**How the fix works.**

1. The reader can now add Food + Drinks and reach the total by subtracting the explicit unallocated line.
2. "Unallocated costs" becomes a visible number management can choose to allocate later, instead of an invisible reconciliation difference.

**Important considerations.**
- Do not invent a 50/50 allocation of electricity; the report is right about that. Show it, do not guess it.
- `DailyPnLLine` rows are snapshotted at submit; changing the line layout affects existing submitted documents' detail pages (they render stored rows, so old documents will keep the old layout — acceptable, but mention it in the release note).
- Add a test asserting `gp_food + gp_drinks - unallocated - round_off == gp` for a day with electricity.

---

## Finding 9: A refund can finish "already submitted" while its GL silently posts nothing

**Reported in:** `CODE_REVIEW.md` BUG-11. **Status: Confirmed.**

**What is wrong.** `post_refund_gl` refuses to post if the *source* order's GL is missing, but it does so with a bare `return` — no error, no warning. The return order still ends up `SUBMITTED`.

**Where it occurs.**
- `apps/accounting/services.py:317-323`:

```python
if GLEntry.objects.filter(
    voucher_type="Order", voucher_no=return_order.invoice_number, is_cancelled=False
).exists():
    return  # idempotent — correct
if not GLEntry.objects.filter(
    voucher_type="Order", voucher_no=source.invoice_number, is_cancelled=False
).exists():
    return  # silent skip — the problem
```

**Why it is a problem.** The first check (already posted → do nothing) is correct idempotency. The second check turns a data problem into silence: the return is accepted, stock may be restored, and no refund reaches the books. Settle is currently atomic with `post_order_gl`, so the normal flow cannot hit this; the exposure is historical data, a manual GL cancellation followed by a return, or a future refactor of settlement. When it does happen, P&L and GL disagree and nobody is told.

**What the code is doing now.** Missing source GL is treated as "nothing to do", the same as "already done".

**Recommended fix.** Fail closed: raise `ValidationError` when the source order has no live GL. Because `submit_return` is `@transaction.atomic`, the raise rolls back the stock restore and the status change, so the operator sees the error and nothing half-finishes.

**How the fix works.**

1. "Already posted for this return" → return quietly (idempotent retry).
2. "Source order never posted" → error, because a refund cannot be booked against income that does not exist.

**Important considerations.**
- Check the deployed data for existing return orders whose source has no live GL before enabling the hard error, otherwise a historical record could become impossible to cancel or amend.
- Add a test that cancels a source order's GL and asserts the return submission raises and leaves no partial rows.

---

## Finding 10: Two different rules decide whether a shift can be opened

**Reported in:** `CODE_REVIEW.md` BUG-8. **Status: Confirmed.**

**What is wrong.** The POS shift-open path refuses to work without a `Restaurant` settings row. The backoffice path creates and submits an opening entry without ever checking. The result can be an "open" shift that the POS immediately refuses to use.

**Where it occurs.**
- POS: `apps/staff/services.py:117-141` (`open_shift`) — `settings = Restaurant.objects.select_for_update().first()`; if `None`, raises "Restaurant settings are not configured."
- Backoffice: `apps/staff/views.py:130-157` (`_save_opening_entry`) writes a DRAFT with no Restaurant check; `apps/staff/views.py:160-179` submits it; `POSOpeningEntry.submit` (`apps/staff/models.py:91-125`) locks the Restaurant row as a mutex but does not require it.
- The close service also requires Restaurant (`apps/staff/services.py:151-153`), so such a shift cannot even be closed normally.

**Why it is a problem.** Two writers, two rules, for the same document. An operator can end up with a shift the POS will not accept orders for and the close service will not close — a state only fixable by editing configuration or data. In a single-restaurant install this is a setup-time trap rather than an everyday bug, but it is exactly the kind of inconsistency that wastes a support call.

**What the code is doing now.** `open_shift()` is fail-closed; `POSOpeningEntry.submit()` is fail-open.

**Recommended fix (smaller than "one writer", preserves the draft workflow).** Keep the backoffice draft flow, but make the state rules identical:

1. In `POSOpeningEntry.submit()`, require a `Restaurant` row — raise the same "Restaurant settings are not configured." error.
2. Optionally have `_save_opening_entry` fail early with a friendly message, but the model-level guard is the real fix.
3. If the project wants truly one writer, convert the backoffice submit view to call `open_shift()` with the form's amounts; that removes DRAFT openings, which is a product decision, not a bug fix.

**How the fix works.** Both doors now apply the same precondition, so a shift cannot exist in a state the rest of the system refuses to operate on.

**Important considerations.**
- The `Restaurant` lock in `submit()` is intentional (it serialises the one-open-shift race). Keep it and add the `None` check after it.
- Check `FEATURES.md`/`PLAN.md` before removing the draft-opening workflow; managers may deliberately prepare opening entries before configuring everything.

---

## Finding 11: `/pos/` is registered twice, and the "wrong" name is still the one templates use

**Reported in:** `CODE_REVIEW.md` BUG-7. **Status: Confirmed.**

**What is wrong.** `spicy/urls.py` mounts the real POS at `pos/` first, then includes `apps.web.urls`, which declares `pos/` again pointing at a no-context view. The name `web:pos_index` (used by templates and by `web.views.home`) reverses to `/pos/`, which today happens to hit the real POS only because of include order. If anyone reorders the includes, the shell renders instead of the POS home.

**Where it occurs.**
- `spicy/urls.py:22-23`:

```python
path("pos/", include("apps.orders.pos_urls")),   # wins today
path("",     include("apps.web.urls")),          # also declares pos/
```

- `apps/web/urls.py:10` — `path("pos/", views.pos_index, name="pos_index")`.
- `apps/web/views.py:132-133` — `pos_index` renders `pos/index.html` with no shift, no order, no catalog context.
- Still in active use: `apps/web/views.py:13` (`home()` redirects staff without backoffice access to `web:pos_index`), `templates/web/app/app_base.html:168`, `templates/backoffice/dashboard.html:10`, `templates/web/landing.html`, and `apps/web/tests/test_auth_flow.py`.

**Why it is a problem.** Reverse lookups are stable — `{% url 'web:pos_index' %}` always produces `/pos/`. What is unstable is that `/pos/` resolves to a different view than the name suggests. Reorder the includes and every "Launch POS" link opens an empty shell for cashiers. The report's phrase "the named view is unreachable" is only half true: the *name* is in use and the *view* is shadowed, which is why the landmine persists.

**What the code is doing now.** Include order silently arbitrates the duplicate. No code enforces it.

**Recommended fix.**

1. Delete `web.views.pos_index` and the `pos/` entry in `apps/web/urls.py`.
2. Point `home()`, the sidebar link, the dashboard link, the landing page, and the tests at `pos:pos_home`.
3. Keep `pos:pos_home` as the only POS entry point.

**How the fix works.** One path, one view, one name. No include can shadow anything.

**Important considerations.**
- `pos:pos_home` is decorated `@staff_required`; `web:pos_index` was not role-checked the same way. Confirm every caller's expected behaviour after the swap (the landing page should not link a signed-out visitor into a staff-only page; check the template conditions).
- Update `apps/web/tests/test_auth_flow.py` in the same commit.

---

## Finding 12: A cashier can still cancel their own sent order with no second person

**Reported in:** `AUDIT_REPORT.md` HIGH-2. **Status: Confirmed, now ownership-scoped.**

**What is wrong.** An order that has been sent to the kitchen can be cancelled by the cashier who created it, with only a dropdown reason — no manager approval. The "take cash off-record, cancel the order, serve the food" path remains open for the order's own creator. Since commit `ccd2c6d` a cashier cannot cancel *someone else's* draft, which closes the plain cross-cashier version of the hole.

**Where it occurs.**
- `apps/orders/views_pos.py:1157-1200` (`pos_order_cancel`, `@staff_required`).
- `apps/orders/services.py:240-284` (`cancel_sent_order`) — checks `locked.can_be_accessed_by(cancelled_by)` (creator or manager), a non-empty reason, and that a KOT exists. No manager requirement.
- Ownership: `apps/orders/models.py:74-79` (`open_drafts_for`) and `:318-322` (`can_be_accessed_by`).
- Shift close only blocks *open* drafts (`apps/staff/services.py:166-170`), so cancelled orders never force a review.

**Why it is a problem.** The sequence is unchanged from the audit: send order → collect cash off-record → cancel with reason "wrong_order" → serve the food. No payment rows exist, so the drawer expected amount is untouched and the shift balances. Detection requires a manager to proactively read cancelled-order history.

**What the code is doing now.** Ownership scoping (partial fix) plus the reason dropdown and audit event. No approval, no threshold, no shift-close review.

**Recommended fix — choose one and record the decision:**

1. **Manager approval required** for cancelling any order with live KOTs. There is no PIN concept in this codebase; the practical form is: cashiers may *request* a cancellation, and a manager/admin confirms it (or the endpoint requires `user.is_manager or user.is_admin`).
2. **Shift-close review**: keep cashier cancellation, but surface the shift's cancelled/sent orders as a blocking review item on the close screen, and count them in the close report. This fixes the "detection requires proactive review" problem without slowing down legitimate cancels.

The audit recommended (1) plus (2)'s visibility. Given the kitchen has already started cooking, option 1 is the stronger fraud control; option 2 is friendlier if wrong orders are common.

**How the fix works.** For option 1, the service raises unless the actor is a manager, and the POS shows a "manager required" state for sent orders. For option 2, `submit_closing_entry` refuses to close until the reviewer ticks off the shift's cancellations, and the closing document stores the count.

**Important considerations.**
- Legitimate reason "wrong_order" is common at busy times. Requiring a manager for every sent order will create a bottleneck; consider a threshold (e.g. manager approval above ₦X or for prepaid items) and record the rule in `FEATURES.md`.
- If a "request" workflow is added, it is a new state on `Order`; that is a bigger plan and needs the PLAN.md protocol.

---

## Finding 13: An electronic payment can be accepted with no reference (by design, still a fraud path)

**Reported in:** `AUDIT_REPORT.md` HIGH-1. **Status: Confirmed behaviour; the design is documented, the risk is real.**

**What is wrong.** A bank/phone payment may be settled with an empty reference. The reference is only required when `Restaurant.require_payment_reference` is enabled, and it ships disabled. The project's own tests assert the blank-reference behaviour.

**Where it occurs.**
- `apps/settings/models.py:47-51` — `require_payment_reference = models.BooleanField(default=False)`.
- `apps/orders/services.py:899` (`require_reference = Restaurant.requires_payment_reference()`) and `:945-949` (only rejects a blank reference when the flag is on).
- `apps/orders/models.py:541-559` — the same rule at the model layer, plus the unique-reference constraint (`:518-524`).
- `apps/orders/tests/test_order.py:292` and `apps/orders/tests/test_pos_views.py:859` — `test_settle_accepts_electronic_payment_without_reference`.
- `FEATURES.md` #29 documents the intended design: "Electronic payment references must be unique, and the restaurant **can** require them at settlement."

**Why it is a problem.** The audit's theft path is real in a default install: customer pays cash (or an accomplice takes goods), cashier records the sale as Bank/Transfer with a blank reference, the order submits as paid, and the drawer balances because no cash is expected. The missing bank credit is only visible at a bank reconciliation performed outside the system.

**What the code is doing now.** The mechanism to enforce references exists, is enforced at both service and model level when on, and uniqueness is enforced for non-empty references. The default is off.

**Recommended fix.** This is a product decision, not a coding error — the report should not be treated as "the code is broken". Two defensible changes:

1. Flip the default to `True` for new installs (the setting remains available for restaurants that genuinely do not track transfer references), or
2. Keep the default but make the setup checklist/warning surface it, so the owner consciously chooses.

If flipped, existing restaurants keep their stored value (it is a stored boolean), so only new installs change behaviour.

**How the fix works.** With the flag on, `_validate_payment_data` rejects a blank reference at settle with a clear message, and `OrderPayment.save` is the second line of defence for any other code path.

**Important considerations.**
- Flipping the default changes behaviour for a fresh install only; no migration data change is needed.
- If references are always required, the POS payment dialog must always show the reference input (it already receives `require_payment_reference` to decide), and staff must be trained not to invent placeholder references — enforce something stronger (e.g. a per-shift sequence) if placeholders appear.
- Do not remove the setting; `FEATURES.md` documents it.

---

## Finding 14: The POS cannot close a large variance, and there is no blind count

**Reported in:** `AUDIT_REPORT.md` HIGH-4. **Status: Partially correct** — several sub-findings were fixed, one real gap remains, and one is a documented policy.

**What is wrong.**
- **Real gap:** when the variance exceeds the configured threshold, `submit_closing_entry` requires a non-empty `variance_note` and a manager actor. The backoffice close form has a `variance_note` input. The POS close template has **no** note input and no threshold warning, so a manager closing from the POS cannot satisfy the requirement and the shift cannot be closed from the POS.
- **Fixed since the audit:** any cashier can no longer close any shift (opener or manager only, `456f139`); `closing_amount` can no longer be negative (form `min_value` and model `MinValueValidator`, `3cbf169`); only one cash mode can be declared per shift (`dc216f6`).
- **Documented policy, not a bug:** `variance_approval_threshold` is nullable, and `PLAN.md` §4.5 explicitly says "null = no approval gate". The audit's recommendation to disallow a blank threshold contradicts the recorded decision; it should be raised as a product change, not a bug.
- **Also policy:** the template shows expected amounts before counting. `FEATURES.md`/`PLAN.md` do not require a blind count. Hiding the expected amount is a reasonable fraud control but changes the UX deliberately.

**Where it occurs.**
- Threshold check and note requirement: `apps/staff/services.py:223-230`.
- Nullable field and help text: `apps/settings/models.py:122-129`.
- Backoffice note input: `apps/staff/views.py:284`.
- POS template that lacks any note input or threshold warning: `templates/pos/close_shift.html:32-106`.
- Documented decision: `PLAN.md` §4.5.

**Why it is a problem.** The moment a restaurant sets a threshold, above-threshold closes can only be completed from the backoffice. A manager at the POS gets "A manager must provide a variance note" with nowhere to type it. If the owner sets the threshold expecting the POS to ask for the note (as the plan describes: "the closing form shows the threshold warning before submit"), the POS silently cannot close.

**What the code is doing now.** The service requires the note and manager role; the POS form never provides a note field; the close error is surfaced as a message with no way to resolve it on that screen.

**Recommended fix.**

1. Add a `variance_note` input (and the threshold warning) to the POS close surface, mirroring the backoffice form, and post it into `closing.variance_note` before submit.
2. Or, if the POS is not meant for manager closes, block the POS close for above-threshold variance with a clear message pointing to the backoffice, and document that. (Less friendly.)
3. Leave the nullable-threshold and blind-count decisions to `FEATURES.md`/`PLAN.md`; if changed, update the documents.

**How the fix works.** The POS view already loads the closing draft and expected rows; it can compute the same `abs(total_short_excess)` preview and pass a threshold flag to the template. The form field is saved the same way `closing.remarks` already is.

**Important considerations.**
- The POS totals shown are a client-side preview; the authoritative variance is recomputed at submit. The warning should come from the server value, not just the Alpine arithmetic.
- Update `docs/` for the close workflow after the change.

---

## Finding 15: Reconciliation write-offs are attributable but not reviewable

**Reported in:** `AUDIT_REPORT.md` HIGH-3. **Status: Partially correct** — actor stamps were added; the rest of the recommendation is still open.

**What is wrong.** The audit's core concern was that a single person could file an adjustment or wastage document to hide stolen stock, with no actor, no mandatory note, and no second approval. Since commit `c1378cc` the documents now record `submitted_by`/`submitted_at` and `cancelled_by`/`cancelled_at`, and the detail page shows them. What remains:

- `remarks` is optional (`apps/inventory/models.py:714`).
- No amount threshold or second-approver step (unlike cash variance, which has `variance_approval_threshold`).
- There is no dedicated "Adjustments & Wastage" view that surfaces these costs prominently; they appear in stock reports and the P&L via the expense accounts.

**Where it occurs.**
- Model: `apps/inventory/models.py:690-786` (`StockReconciliation`, `StockReconciliationItem`).
- Services: `apps/inventory/services.py:694-874` (submit), `:877-943` (cancel) — actor is now stored.
- Views: `apps/inventory/views.py:644, 658` pass `actor=request.user`.
- Detail page shows the actor: `templates/backoffice/inventory/reconciliation_detail.html:66-71`.

**Why it is a problem.** The write-off is now traceable to a named user, which is a real deterrent and a starting point for investigation. It is still a single-person action: the same person can file "12 bottles broken in transit" with no note and no approval, and the Bin and GL will agree with the physical theft. Attribution without review means the audit trail exists but nobody is forced to look at it.

**What the code is doing now.** The actor is recorded; nothing blocks a write-off on value, nothing requires an explanation, and nothing routes it for approval.

**Recommended fix (scoped, not all-or-nothing):**

1. Require `remarks` for `ADJUSTMENT` and `WASTE_DAMAGE` above a configurable value (a new setting, e.g. `reconciliation_approval_threshold`, mirroring the cash variance pattern).
2. Optionally require a manager actor/approver above the threshold, using the same flag pattern as shift close.
3. Surface a monthly adjustment/wastage total on the P&L and the inventory dashboard so the owner sees the trend without hunting.

**How the fix works.** Mirrors the cash-variance design that already exists: a nullable threshold, a note field, and a manager check at submit. The service raises `ValidationError` with a clear message, and the form shows the warning.

**Important considerations.**
- Food waste is routine; forcing a manager for every small write-off will be ignored or worked around. Use a value threshold and make the threshold visible in settings.
- `remarks` mandatory at all values is probably too strict for daily kitchen consumption; limit to adjustments/waste.
- Record the final decision in `PLAN.md` before implementing (per the project protocol).

---

## Finding 16: P&L drink cost follows today's item classification, sales follow the sale-date snapshot

**Reported in:** `AUDIT_REPORT.md` §4. **Status: Confirmed.**

**What is wrong.** Order lines snapshot their department when the sale happens (`OrderItem.save` copies `item.department`). The Daily P&L sales columns use that snapshot. But the drink COGS calculation reads stock-ledger rows joined to `Item.department`, which is the *current* classification. Reclassifying an item rewrites the historical cost column while leaving the sales column alone.

**Where it occurs.**
- Sales snapshot: `apps/orders/models.py:470-481` (`OrderItem.save` sets `department` from the item).
- Sales query: `apps/reports/sources.py:45-58` (`row["department"] or row["item__department"]`).
- COGS query: `apps/reports/sources.py:72-77` and `:92-97` — `filter(item__department=DRINKS)` on `StockLedgerEntry`.
- Stock ledger has no department column: `apps/inventory/models.py:292-334`.

**Why it is a problem.** Example: a drink is reclassified to FOOD after a bad-margin day. The sales row for the past day still shows the drink revenue, because it used the snapshot, but the drink COGS column re-reads the ledger through the item's new department — so the cost disappears from Drinks and the bar margin looks better than it was. Nothing marks the change.

**What the code is doing now.** Two different sources of truth for the same concept, joined at report time.

**Recommended fix (two options).**

- **Cheap:** derive drink COGS through the order line, which already carries the snapshot. `StockLedgerEntry.voucher_detail_no` stores the `OrderItem` pk for POS order/return rows, so the query can join to `OrderItem.department` (or fetch the department in Python alongside the rows). No schema change for the report.
- **Robust:** add a `department` snapshot field to `StockLedgerEntry`, populate it at creation, and backfill existing rows. The ledger is immutable, so a snapshot fits its design, and reports no longer depend on `Item` at all.

Prefer the cheap option now; take the schema option if/when the ledger becomes the reporting source for more dimensions.

**How the fix works.** Both options make historical reports depend only on data recorded at the time of the event. Reclassifying an item then affects only future sales.

**Important considerations.**
- The cheap join relies on `voucher_detail_no` matching an `OrderItem` pk; guard against non-order SLE rows (returns have their own voucher type).
- If a backfill is chosen, run it as a data migration after the schema migration (project rules) and test on a copy of production data.

---

## Finding 17: Insecure boot defaults and a production settings file that is never used

**Reported in:** `AUDIT_REPORT.md` §5. **Status: Confirmed.**

**What is wrong.**

- `SECRET_KEY` falls back to a known, committed value (`spicy/settings.py:16`, same value in `.env.example:9`). The secret key signs sessions and password-reset tokens; anyone who knows it can forge a session cookie.
- `DEBUG` defaults to `True` (`:18`) and `ALLOWED_HOSTS` defaults to `["*"]` (`:24`). Booting without `.env` yields debug pages, the dev admin bypass (`SPICY_DEV_ADMIN_BYPASS = DEBUG`, `:22`), and host-header weakness.
- `spicy/settings_production.py` sets `DEBUG=False`, secure cookies and `SECURE_SSL_REDIRECT`, but nothing selects it: `Makefile:24` runs `manage.py runserver` with the default module and no `--settings`. There are no `SECURE_HSTS_*`, `SECURE_CONTENT_TYPE_NOSNIFF`, `SECURE_REFERRER_POLICY`, secure cookie flags, `SESSION_COOKIE_AGE`, or CSP in the base settings.
- `docker-compose.yml` uses `POSTGRES_PASSWORD=postgres` and an unauthenticated Redis, but both ports are bound to `127.0.0.1` (`:12`, `:24`), so they are not reachable from the network. That part of the audit is accurate and low risk.

**Why it is a problem.** This is a LAN application, but "local network" is not "trusted". A known signing key plus debug mode plus wildcard hosts is a real escalation path if the box is ever exposed or a `.env` is lost during a restore. The project already has the correct production file; the danger is that nothing forces its use.

**Where it occurs.** `spicy/settings.py:16-24`, `spicy/settings_production.py:1-12`, `Makefile:24`, `.env.example:9`.

**Recommended fix.**

1. Remove the committed fallback for `SECRET_KEY` and fail fast with a clear message when it is missing.
2. Make the safe values the defaults (`DEBUG=False`, explicit `ALLOWED_HOSTS`), and let `.env` turn debug on for development.
3. Add the standard hardening flags to the base settings (they are harmless in development) or document that `settings_production` is the production module and wire it into the documented start command.
4. Keep the compose credentials but note they are development-only.

**How the fix works.** A fresh checkout without `.env` refuses to boot instead of running insecurely. Production gets the hardening by default rather than by remembering a flag.

**Important considerations.**
- Do not overwrite the developer's `.env` (project rule); only change the defaults.
- If `SECRET_KEY` becomes mandatory, document it in `README`/`Makefile init`.
- `USE_HTTPS_IN_ABSOLUTE_URLS = True` in production will build `https://` links; on a plain-HTTP LAN that can break redirects. Verify the deployment mode.

---

## Finding 18: Anyone on the network can create a login

**Reported in:** `AUDIT_REPORT.md` §5 and `CODE_REVIEW.md` REF-14. **Status: Confirmed.**

**What is wrong.** allauth signup is wired: `/accounts/signup/` exists, the login page links to it ("Don't have an account? Get Started", `templates/account/login.html:18`), and `SpicyAccountAdapter` is an empty subclass of `DefaultAccountAdapter`, whose `is_open_for_signup()` returns `True` in allauth 65.18.0 (verified in the installed package). A new user becomes active immediately; the only reaction is an admin email.

**Where it occurs.**
- `spicy/settings.py:169-189` — `ACCOUNT_ADAPTER`, `ACCOUNT_FORMS["signup"]`, `ACCOUNT_SIGNUP_FIELDS`.
- `apps/users/adapter.py:1-5` — empty adapter.
- `apps/users/signals.py:11-13` — the `user_signed_up` receiver only emails admins.
- `templates/account/login.html:18` — the signup link.

**Why it is a problem.** The signup form creates a role-less account; the role decorators deny business actions, so this is not direct privilege escalation. But it is a free account on a staff-only system, it clutters the staff list, and (combined with Finding 2) it gives an attacker a place to store a name payload. It is also the wrong default for an on-premises restaurant: staff accounts are provisioned from the backoffice (`settings.staff_create`).

**What the code is doing now.** Public signup is open, immediate, and only announced by email.

**Recommended fix.**

1. Override `SpicyAccountAdapter.is_open_for_signup()` to return `False` (optionally gated by an env flag for first-time setup).
2. Remove the signup link from the login page.
3. Keep the backoffice staff-create flow as the only provisioning path.

**How the fix works.** allauth calls the adapter before showing/processing signup; returning `False` closes the door server-side (not just hiding a link). The existing admin notification can stay for the staff-create path if desired.

**Important considerations.**
- If the very first admin must be created via signup, provide a management command or document the one-time override flag; there is already a `promote_user_to_superuser` command, so the flow can be: create login via shell/seed → promote.
- `pending_approval` remains useful for "created but not yet assigned a role"; keep the page if that workflow stays.

---

## Finding 19: Remaining security-hardening items (grouped)

**Reported in:** `AUDIT_REPORT.md` §5. **Status: Confirmed**, with one correction (media serving).

| Item | Where | Verified state | Suggested direction |
|---|---|---|---|
| Email verification off; non-unique email | `spicy/settings.py:176, 189` | `ACCOUNT_EMAIL_VERIFICATION="none"`, `ACCOUNT_UNIQUE_EMAIL=False`. Login is by username, so the practical risk is duplicate emails confusing password resets. | If email is never used for login, set `ACCOUNT_UNIQUE_EMAIL=True` to avoid ambiguity, or document why duplicates are allowed. |
| Weak seed credentials | `apps/orders/management/commands/seed_test_data.py:105-127` | Still creates `cashier/pos1234` and `manager/manager1234` with `set_password` (bypasses validators). | Restrict seeds to DEBUG, or generate random passwords and print them once. Never run seeds in production. |
| Role management can silently strip a role | `apps/settings/views.py:110-135`, `apps/settings/urls.py:10` | `_apply_role` removes all three groups first; an unrecognised `role` value removes roles and adds none. The URL accepts any `<str:role>`. There is no last-admin/self-demotion guard. `staff_toggle_active` does guard self-deactivation. | Validate `role in {admin, manager, cashier}` at the top of `_apply_role`; block demoting the last admin (or yourself); consider an audit log of role changes. |
| Avatar validation is extension-only | `apps/users/models.py:16`, `apps/users/helpers.py:20-46` | `FileField` with a name-extension check and a 5 MB cap. A script renamed `.jpg` passes. `avatar_url` still calls gravatar.com (`models.py:31`), which does not work offline. | Switch to `ImageField` (Pillow verifies real image content), keep the size cap. Drop gravatar or make it opt-in; provide a local default. |
| Media serving | `spicy/urls.py:24` | `static(settings.MEDIA_URL, ...)` — the audit says "unconditionally", but Django's `static()` returns `[]` when `DEBUG=False` (verified). In DEBUG it serves uploaded files without auth. | Correct the report. In production a front server should serve media; decide whether avatars need authentication. |
| `printer_ip` unvalidated | `apps/settings/models.py:314`, `apps/settings/forms.py:128-144` | Plain `CharField` with a placeholder; no client exists yet. | Validate as an IP/hostname when the Phase-12 print agent is built (latent SSRF only if a client later fetches arbitrary URLs). |
| Personal data committed | `spicy/settings.py:239, 280-281, 286, 311` | `DEFAULT_FROM_EMAIL` and `ADMINS` hardcode `sanusio293@gmail.com`; `PROJECT_METADATA` keywords say "SaaS, django"; logger is named `pegasus`; image points at Wikimedia. | Move email/admin to env vars; update metadata and logger names as part of the Pegasus cleanup (REF-14). |
| `promote_user_to_superuser` has no confirmation | `apps/users/management/commands/promote_user_to_superuser.py:12-24` | Bare username → superuser, no confirmation/logging (the command output message was improved, behaviour unchanged). | Add `--yes`/confirmation and a log line; shell-only, so low priority. |

---

## Finding 20: Submitted shift documents are still editable at the ORM layer

**Reported in:** `AUDIT_REPORT.md` §3 LOW. **Status: Confirmed (latent).**

**What is wrong.** `POSClosingEntry.save()` has no status guard, and neither `ClosingPayment` nor `OpeningPayment` has one. A submitted closing entry's `closing_amount` can be overwritten through the ORM with no validation, no audit event, and no GL correction. The UI never does this today; it is a defence-in-depth gap that the project applies elsewhere (orders, GL entries, stock documents).

**Where it occurs.**
- `apps/staff/models.py:232-237` (`POSClosingEntry.save`) — only defaults `period_start_date`/`cashier`; no status check.
- `apps/staff/models.py:277-311` (`ClosingPayment`) — field validator on `closing_amount` exists, but no immutability guard.
- `apps/staff/models.py:150-170` (`OpeningPayment`) — no guard.
- The journal-row race the audit mentioned in the same finding **is fixed**: `JournalEntryAccount.save` re-checks the parent journal's status (`apps/accounting/models.py:484-490`).

**Why it is a problem.** Consistent guard-rails matter more than any single hole: if every other financial document refuses ORM edits, a missing guard on one document is the path a future script or admin action will take by mistake. The consequence here is a Z-report that no longer matches its GL variance journal.

**What the code is doing now.** Service paths are correct; the model layer is not.

**Recommended fix.** Follow the patterns already in the codebase:

1. `POSClosingEntry.save()` rejects changes when the stored status is `SUBMITTED`/`CANCELLED`, except for the service-flag pattern used by `Order`/`JournalEntry`.
2. `ClosingPayment`/`OpeningPayment` reject saves when the parent is no longer `DRAFT`, and reject deletion.
3. Admin: make closed documents fully read-only (there may be no staff admin registrations today; verify).

**How the fix works.** Any accidental script/admin write raises immediately; legitimate transitions still work because the services set the private flag in the same transaction.

**Important considerations.**
- Check the close service's `update_fields` calls and wrap them with the flag.
- Add a regression test mirroring `test_order.py`'s immutability tests.

---

## Finding 21: Calendar reports each rebuild their own date filters

**Reported in:** `CODE_REVIEW.md` REF-8. **Status: Confirmed** — with one sub-claim that is wrong (see Part 3).

**What is wrong.** `submitted_orders(date_from, date_to)` exists in `apps/reports/sales_reports.py:23`, but three other report modules re-filter the data themselves: item-wise (`apps/reports/sales_breakdown_reports.py:21-27`), trial balance and simple P&L (`apps/reports/accounting_reports.py:157-165`, `:250-256`), and the POS register (`apps/reports/register_reports.py:19-29`). Each copy can drift.

**Where it occurs.** The files above, plus `ZERO`/`_q2` helpers shared/copied across modules.

**Why it is a problem.** This is a maintainability finding, not a current wrong number — the predicates happen to match today. When the business-day rules or statuses change, some reports will change and others will not, and the disagreement will be hard to trace. The report is right that the fix is a small set of canonical querysets, not a new reports app.

**Recommended fix.** Adopt the report's proposal: `submitted_orders(...)`, `order_items(...)`, `gl_entries(...)` as the single calendar-window builders; leave `orders_in_window`/`business_day_window` as the P&L-only business-day clock (that split is documented and correct). Point item-wise, month-wise, cancelled, register, trial balance and simple P&L at the shared builders.

**How the fix works.** One place decides what "in range" means for calendar reports; the callers only choose grouping.

**Important considerations.** The report's claim that the `department or item__department` fallback is dead is wrong (Part 3); keep the fallback. Do the refactor with report-equivalence tests comparing the old and new query results.

---

## Finding 22: Small confirmed items (nits and code-quality)

These are real but small. Full per-item reasoning is in Part 5; this is the confirmed list.

| Item | Where | What is wrong |
|---|---|---|
| NIT-1 | `apps/accounting/services.py:18, 28, 268` | Three near-identical per-department ProductionUnit account lookups, each a query per call site. |
| NIT-2 | `apps/reports/views.py:62-67`, `apps/accounting/views.py:254` | Raw GET strings go into `business_date__gte/lte` and `int(account_id)`; a malformed value is a 500 instead of a friendly error. |
| NIT-3 | `apps/web/context_processors.py:29-33` | `ItemGroup.objects.count()` runs on every backoffice request just to decorate navigation. |
| NIT-4 | `assets/javascript/order-details-drawer.js:80-115` | `posModalDialog` (used by payment/add-on/variant/cash-out dialogs) lives in a file named after the history drawer. |
| NIT-5 | `templates/pos/close_shift.html:50-59` vs `templates/pos/partials/gates/no_shift.html:36-44`; spinner blocks in `totals.html`, `order_history_detail.html` | Payment-mode icon switch and spinner markup are copy-pasted. |
| NIT-6 | `close_shift.html:11`, `no_shift.html:6`, `cash_out_dialog.html:12`, payment `dialog.html` | Four copies of a `money()`/`toLocaleString('en-NG')` helper. |
| NIT-7 | `templates/pos/index.html:26-28` | `payment_dialog` partialdef only includes `dialog.html`; an extra indirection. |
| NIT-8 | `templates/pos/index.html:30`, `catalog/grid.html:5, 31` | `#add-on-dialog-container` hosts variant dialogs too; the name lies. |
| NIT-9 | `assets/styles/site-tailwind.css:33` | Residual "DaisyUI removed" comment; `app-components.css` still has Pegasus rules. |
| NIT-10 | `docs/database/transactions.md:15, 18`, `docs/database/queries.md:19`, `docs/architecture/overview.md:53`, `docs/architecture/data-model.md:124` | Docs still describe FIFO queues and a nonexistent `_reverse_voucher`; runtime is weighted-average WAC. |
| NIT-11 | `apps/orders/services.py:1217-1218` | `_ticket_type_for_department` maps anything not `FOOD` to the bar printer; an unexpected/NULL department silently becomes a bar ticket. |
| NIT-12 | `apps/reports/report_views.py:12-13` | `_range` is a one-line alias of `report_filters.date_range`. |
| Audit §6 | `apps/orders/services.py:127-131` | `ITEM_QUANTITY_CHANGED` audit metadata stores the line pk under `item_id`; the remove branch (`:113-119`) stores the real item id. Reconciliation by item is misattributed. |
| Audit §6 | `apps/orders/services.py:1217` etc. | Duplicated FOOD/DRINKS validation blocks (`inventory/models.py:158-186`, `menu/models.py:58-67`, `orders/models.py:324-347`); duplicated label-join (`orders/services.py:197, 558`). |
| Audit §6 | All service modules | Zero return type hints (0/40, 0/16, 0/27, 0/5, 0/7 annotated functions). |
| Audit §6 | `apps/inventory/services.py:114-254`, `views.py:943`, `:1075-1093` | `compute_food_usage` lives in the posting module (REF-2); the food-usage page swallows `ValidationError` and shows ₦0 food sales. |
| Audit §6 | 6 places | Lines over 120 chars (`accounting/payables_models.py:37`, `inventory/forms.py:108`, `inventory/models.py:803`, `menu/models.py:106`, `reports/models.py:24`, `settings/models.py:128`). |
| Audit §6 | `apps/inventory/models.py:219-255` | `ItemUOMConversion` can be edited/deleted after use with no guard; historical SLEs keep old blended WAC with no link back. |
| Audit §6 | `apps/orders/printing.py:1-21` | Stub always succeeds; no direct test of the interface (tests mock it). Fine until the agent lands. |
| Audit §6 | exception syntax | 14+ unparenthesized `except A, B:` sites. Valid in Python 3.14, but `ast.parse` fails on ≤3.13 (verified with system Python). `AGENTS.md` explicitly says not to mass-fix these. |
| Audit §7 | `templates/pos/order_history.html:10-114` etc. | Filter/nav GETs still lack loading indicators (only `order_history_detail.html` has `hx-indicator` in POS). Minor, mutations all POST. |
| Audit §7 | `assets/styles` / templates | Boilerplate inline SVGs and inline `onclick="Swal.fire(...)"` confirmations remain (`pos/partials/cart/totals.html:55`, `backoffice/orders/order_detail.html:18-34`), bypassing `confirm.js`'s guard. The backoffice copy also still says deleting a draft removes its audit trail — stale after the tombstone change (Finding "already fixed"). |

---

# Part 2 — Already fixed (do not re-fix)

These findings were valid when written; the current code no longer has the problem. Evidence is the current code and the commit that fixed it.

## Calculation and accounting

| Original finding | Current state | Evidence / fix commit |
|---|---|---|
| Income and payment legs can net to zero on one account (AUDIT §2 HIGH) | Fixed three ways: payment mappings cannot point at income accounts; `post_order_gl` rejects an account appearing on both sides. | `apps/accounting/services.py:60-61` (`_resolve_payment_account`), `:210-224` (`_ensure_disjoint_sides`), `apps/payments/models.py:110-117`; commit `32266cf`. |
| Refund COGS valued at current WAC (AUDIT §2) | Fixed: the return restores stock at the settle-time rate and GL mirrors the booked value. | `apps/orders/services.py:1127-1151` (`_restore_stock` uses `settle_time_rate`), `apps/accounting/services.py:278-288, 362-377`; commits `4842c4f`, `f410caa`. |
| P&L drink-COGS rate disagreed with GL refund rate (AUDIT §2) | Fixed: both call `settle_time_rate`. | `apps/reports/sources.py:115`; `apps/accounting/services.py:364`; commit `769f42e`. |
| P&L dropped NULL-department sales (AUDIT §2) | Fixed: sales fall back to the item's live department; NULL rows still exist and are still handled (the field is still nullable — see Part 3). | `apps/reports/sources.py:45-58`; commit `0e2ce26`. |
| Cash variance posted to the first cash mode (AUDIT §2) | Fixed: one GL leg per payment mode's drawer. | `apps/accounting/services.py:405-448`; commit `570bb99`. |
| P&L and shift close used two different clocks (AUDIT §2) | Fixed: `posting_date`/`posting_time` are stamped from `submitted_at` at settlement, so both agree. | `apps/orders/services.py:224-226, 338-341`; commit `67d17b8`. |
| Cash-change double count with two cash modes (AUDIT §2) | Fixed in practice: a shift may declare at most one cash mode, so the double-count scenario cannot be created. The `collect_submitted_payment_totals` query still attributes change per payment row but `.distinct()` plus the constraint keeps it correct. | `apps/staff/models.py:113-123` (submit enforces one cash row); commit `dc216f6`. |
| Mixed rounding modes (AUDIT §2) | Fixed: `money()`, `cash_round()`, `percent()` centralise explicit modes. Remaining bare `quantize()` calls are quantities, not money. | `apps/utils/rounding.py`; commit `aec290e`. |
| Monthly recurring expense never summed to the month (AUDIT §2) | Fixed: the last day absorbs the remainder. | `apps/reports/sources.py:175-181`; commit `3716977`. |
| `unit_rate` vs `stock_value_change` penny divergence (AUDIT §2) | Fixed: GL and P&L use the booked `stock_value_change`. | `apps/inventory/services.py:640-683`, `apps/reports/sources.py:80, 100`; commit `f410caa`. |
| Zero-rate receipts diluted WAC (AUDIT §2) | Fixed: receipt lines require a rate > 0. | `apps/inventory/models.py:658-659`; `PurchaseReceiptItem.validate_for_submission:920-921`; commit `230b07f`. |

## Reversals, transfers, and GL

| Original finding | Current state | Evidence / fix commit |
|---|---|---|
| Reversals backdated to the original period (AUDIT §3 HIGH) | Fixed: all reversals post today — `_reverse_gl`, `JournalEntry.cancel`, stock/receipt/reconciliation cancels. | `apps/accounting/services.py:486-508`; `apps/accounting/models.py:390-417`; `apps/inventory/services.py:606, 683, 911-938, 1085`; commit `725c406`. |
| Material transfers posted no GL (AUDIT §3) | Fixed: transfer journals are posted (Dr destination SIH / Cr Store SIH) when the accounts differ. | `apps/inventory/services.py:437-444`; commit `a0b76ab` (and earlier). |
| Wastage returns collapsed to zero under the seed (AUDIT §3) | Fixed: the seed creates a dedicated "Wastage" account; the service refuses to use the drink expense account and fails closed if they match. | `apps/accounting/management/commands/seed_chart_of_accounts.py:162-170, 306`; `apps/accounting/services.py:372-375`; commits `eef2ceb`, plus seed update. |
| Cash-variance posting silently skipped (AUDIT §3) | Fixed: both shortage and over accounts go through `_resolve_required_account`, which raises when unconfigured. | `apps/accounting/services.py:421-424`; commit `abb10b0`. |
| `default_stock_in_hand_account` configured but unused (AUDIT §3) | Fixed: the field was removed. | `apps/settings/models.py` (no such field); commit `507ed05`. |
| `JournalEntry.save` raised `NameError` on direct non-draft create (AUDIT §3) | Fixed: flags are initialised before the `if self.pk` branch, and a clear `ValidationError` is raised. | `apps/accounting/models.py:295-310`; commit `8d44be2`. |
| `reverse_order_gl` dead production code (AUDIT §3) | Fixed: removed; refund posting replaced it. | grep finds no `reverse_order_gl`; commit `725c406`. |
| Journal row could be inserted between submit totals and GL posting (AUDIT §3) | Fixed: `JournalEntryAccount.save` re-checks the parent's status. | `apps/accounting/models.py:484-490`. |
| Transfer cancel revalued at destination WAC with no variance (AUDIT §4) | Fixed: the source is restored at the original transfer value and the drift posts as `CANCELLATION_WAC`. | `apps/inventory/services.py:539-597`; commit `a0b76ab`. |
| Ledger ignored `reserved_qty` (AUDIT §4) | Fixed: outbound movements cannot take actual stock below reserved. | `apps/inventory/models.py:454-459`; commit `a0b76ab`. |
| Transfer lines ignored `basic_rate` (AUDIT §4) | Fixed: transfer lines force the stock UOM and zero their rate/amount. | `apps/inventory/models.py:597-611, 622-625`; commit `a0b76ab`. |
| Post-consumption transfers failed with a bare error (AUDIT §4) | Fixed: the error now names the item and quantity consumed and suggests the adjustment path. | `apps/inventory/services.py:514-519`; commit `a0b76ab`. |
| `prevent_negative` parameter was dead (AUDIT §4) | Fixed: the parameter was removed; the non-negative check is unconditional. | `apps/inventory/models.py:348-390, 430-461`; commit `a0b76ab`. |
| `StockLedgerEntry` allowed edits/deletes; `Bin` was editable in admin (AUDIT §4) | Mostly fixed: SLE `save`/`delete` raise; `BinAdmin` and `StockLedgerEntryAdmin` are fully read-only. Remaining ORM-create hole is Finding 3. | `apps/inventory/models.py:340-346`; `apps/inventory/admin.py:125-179`; commit `a0b76ab`. |
| Backdated postings revalued current WAC (AUDIT §4) | Still true, still deliberate: the code comments state posting date is a business/audit date only and valuation always blends at current WAC. No action recommended. | `apps/inventory/models.py:418-424`. |

## Orders, shifts, and audit trail

| Original finding | Current state | Evidence / fix commit |
|---|---|---|
| No draft ownership (AUDIT §1 MEDIUM) | Fixed: `Order.created_by`, `open_drafts_for`, `can_be_accessed_by`; every draft-mutating POS view uses the scoped fetch. | `apps/orders/models.py:74-79, 115-122, 318-322`; commit `ccd2c6d`. |
| History detail/reprint unscoped (AUDIT §1 MEDIUM) | Partially fixed: cashiers are limited to paid sales; returns/cancelled/discarded require a manager or `pos_allow_full_history`. Still not restricted by shift/date — a cashier can open or reprint any paid sale by guessing the pk, which leaves the duplicate-receipt social-engineering path open. | `apps/orders/views_pos.py:129-134, 1341-1386`; commit `e825f1d`. |
| `closing_amount` accepted negatives (AUDIT §1 MEDIUM) | Fixed: model `MinValueValidator` and form `min_value`, plus migration `0011`. | `apps/staff/models.py:292-294`; `apps/staff/forms.py:31-36`; migration `staff/0011`; commit `3cbf169`. |
| Deleting an unsent draft purged the audit trail (AUDIT §1 MEDIUM) | Fixed: the order becomes `DISCARDED` with an `ORDER_DELETED` audit event and an item snapshot; hard delete raises. | `apps/orders/models.py:267-268`; `apps/orders/services.py:307-325`; commits `f4eb3c7`, `035535a`. |
| Submitted shift documents editable at the ORM; journal race (AUDIT §3 LOW) | Partially fixed: journal rows are guarded; shift documents remain unguarded (Finding 20). | see Finding 20. |
| `pos_order_cancel`, `settle`, ticket print lacked ownership checks | Fixed/covered by draft scoping and `can_be_accessed_by`. | `apps/orders/services.py:160-161, 250-251`; `views_pos.py:1251`. |

## Frontend (AUDIT §7)

| Original finding | Current state | Evidence / fix commit |
|---|---|---|
| POS had no global HTMX error state | Fixed: `templates/pos/base.html:3, 42-44` has the error banner and `hx-on::response-error`/`send-error`. | commit `be1c9e6`. |
| Hardcoded relative URLs in `order_history.html` | Fixed: all links use `{% url %}` and include the search term. | `templates/pos/order_history.html:10-114`; commit `75c8dc9`. |
| Alpine numeric interpolation without `escapejs` | Fixed in the cited files: close shift, payment dialog, cash-out dialog all use `|escapejs`. | commits `87c8189`, `d4cfdd9`. |
| `print()` in management commands | Fixed: the two commands use `self.stdout.write`. | commit `6d5b849`. |
| `update_order_item` audit metadata mislabel | **Still present** (kept in Part 1's table as a small confirmed item): `apps/orders/services.py:127-131` stores the line pk under `item_id`. | — |

## Code quality claims that no longer hold

| Original claim | Current state |
|---|---|
| "Views almost entirely undocumented" | Partially: `views_pos.py` now documents every handler, but `menu/views.py` 17/17, `settings/views.py` 12/12, `reports/views.py` 14/14, `accounting/views.py` 19/21, `payments/views.py` 8/9, `staff/views.py` 7/11 public functions still lack docstrings (verified by AST scan). |
| Bare `except Exception` (5 sites) | Reduced to 4, all in `staff/views.py` with `logger.exception`; the `apps/utils/forms.py` catch is now a specific `FieldDoesNotExist`. |
| `_validate_pos_item` undocumented; stale `change_guest_count` docstring; deployment rationale in `utils/admin.py` docstring | Fixed: those docstrings are now one line and accurate; `utils/admin.py`'s docstring is one line. |
| "No mock data in production" / "no TODO/FIXME" / "zero unused imports" | Confirmed by inspection and `ruff check --select F,B` (all checks passed) with today's tree. |

---

# Part 3 — Claims that are incorrect or misleading

These claims should not be acted on as written. Each explanation says what the code actually shows.

### 3.1 "Migration 0030 made `OrderItem.department` required, so the fallback is dead"

**Reported in:** `CODE_REVIEW.md` REF-8 and REF-16. **Incorrect.**

Migration `apps/orders/migrations/0030_orderitem_orders_item_department_valid.py` only adds a `CheckConstraint` for `department__in=("FOOD", "DRINKS")`. A `CHECK` constraint in SQL passes when the value is NULL (NULL makes the condition NULL, and NULL is not false), and the model field is still `null=True, blank=True` (`apps/orders/models.py:387-392`). So NULL rows are still legal. The codebase still handles them in `apps/reports/sources.py:56`, `apps/orders/services.py:574, 648, 972, 993, 1131`, `apps/accounting/services.py:70, 265` and `apps/inventory/services.py:125`. The fallback is not dead, and deleting it would drop legacy sales from reports.

### 3.2 "`ModeOfPayment.can_dispense_change` is unused outside its test"

**Reported in:** `CODE_REVIEW.md` REF-16. **Incorrect.** The property is used by `templates/backoffice/payments/mode_detail.html:44` (and exercised by `apps/payments/tests/test_mode_of_payment.py`). It is not dead code.

### 3.3 "Django autoescape makes the `data-confirm-message` pattern inert"

**Reported in:** `AUDIT_REPORT.md` §7 ("safe today, fragile") and `CODE_REVIEW.md` REF-15. **Incorrect safety assessment.** As explained in Finding 2, escaping happens once when the template renders, but the browser decodes entities inside the attribute value, and SweetAlert2's `html:` option re-parses the decoded string as HTML. The pattern is not inert; it is a working stored-XSS path.

### 3.4 "The named view `web:pos_index` is unreachable"

**Reported in:** `CODE_REVIEW.md` BUG-7. **Misleading.** The *view* is shadowed because `/pos/` resolves to the orders include first, but the *name* is very much reachable and in active use: `apps/web/views.py:13`, `templates/web/app/app_base.html:168`, `templates/backoffice/dashboard.html:10`, `templates/web/landing.html`, and `apps/web/tests/test_auth_flow.py`. The landmine is real (see Finding 11); the report's wording understates how much is wired to the name.

### 3.5 "`Django serves MEDIA_URL unconditionally`"

**Reported in:** `AUDIT_REPORT.md` §5. **Incorrect.** `spicy/urls.py:24` uses `static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)`. Django's `static()` helper returns an empty list when `DEBUG=False` (verified by calling it with the current settings, where DEBUG is True only because of `.env`). So media is served without auth only in debug; in production it is not served by Django at all. The underlying avatar-validation risk remains.

### 3.6 "Refund COGS uses return-time WAC" (the exact mechanism)

**Reported in:** `AUDIT_REPORT.md` §2. **Outdated/inaccurate as to the current code.** The original problem is fixed (Part 2), but note the actual mechanism: restockable returns now use `_restore_value_for` — the booked value of the return SLE — and that SLE was created at `settle_time_rate`. Not restockable (wastage) returns use `settle_time_rate` directly. Both are consistent with the GL.

### 3.7 "The cash-change join can mis-attribute change if an order has two cash rows"

**Reported in:** `CODE_REVIEW.md` REF-18. **Overstated as a current risk.** Since a shift may declare only one cash mode (`dc216f6`), and orders can only pay with modes declared at open (`_validate_payment_data:936-937`), the two-different-cash-mode scenario cannot occur. Two payment rows in the *same* cash mode collapse under `.distinct()` and count change once. The code is still worth simplifying, but there is no live mis-attribution.

### 3.8 "`settle_order` calls `dispatch_tickets` … and the report's fix claim about `update_order_meta` already locking"

**Reported in:** `CODE_REVIEW.md` BUG-1 and BUG-2. **Partially wrong.** BUG-2's description is accurate. BUG-1's statement that *both* `update_order_item` and `update_order_meta` "already lock inside `@transaction.atomic`" is wrong: `update_order_meta` (`apps/orders/services.py:77-104`) has no `select_for_update` at all. The recommended fix must add the lock to that service (Finding 1), otherwise removing the view lock silently removes serialisation.

### 3.9 "POS has no global HTMX error state" / "hardcoded `hx-get` URLs" / "no `escapejs`"

**Reported in:** `AUDIT_REPORT.md` §7. **Already fixed** by commits `be1c9e6`, `75c8dc9`, `87c8189` (Part 2). Listed here so nobody re-does the work.

### 3.10 Claims I could not fully verify

- "**No `hx-get` mutates state**" and "**no `fetch()`**" (AUDIT §7 "Verified OK"): I spot-checked the POS and backoffice templates and found no counterexample, but I did not exhaustively read every one of the 131 templates, so I am not certifying this as a complete audit.
- "**Zero unused imports / zero `TODO` / zero commented-out code**": confirmed for `ruff check --select F,B` and for the `TODO/FIXME/HACK/XXX` grep; "commented-out code" was spot-checked, not enumerated.
- "**No obsolete views/URLs**": the duplicate `web.pos_index` is one counterexample (Finding 11); the general "none" claim was not exhaustively verified.

---

# Part 4 — The refactor suggestions, checked

The code review's verdict ("a finding is included only when splitting/extracting/deleting changes an outcome") is sound, and the proposals are mostly valid. This section says whether each is worth doing and where the report is wrong.

| Ref | Worth doing? | Verified notes |
|---|---|---|
| **REF-1** — one movement/reversal engine | **Yes, after F4/F5.** | The six functions and the drift are real: transfer cancel re-derives warehouses, recon cancel mirrors GL, receipt cancel computes `CANCELLATION_WAC`, and `_post_gl_rows` vs `GLEntry.post` differ. The proposed `post_movements`/`reverse_voucher` shape fits. Caveat: `_resolve_account` (inventory) and `_resolve_required_account` (accounting) are duplicates, but they live in different apps; merging them must respect the existing import direction (inventory already imports accounting lazily). Do not start this before F4/F5 are fixed, or the refactor will bake in the bugs. |
| **REF-2** — move food usage to reports | **Yes.** | `compute_food_usage`/`recipe_plate_cost` (`apps/inventory/services.py:89-254`) read recipes, SLEs and orders and post nothing; the `reports.sources` import inside the function is an awkward cycle breaker. The `food_usage` page's `except ValidationError: food_sales = 0` (`apps/inventory/views.py:1085-1093`) is a real swallow-the-error smell. Moving the report-shaped logic to `apps.reports` removes the cycle and matches ownership. The `food_usage` page would import from reports; that direction already exists in that view's local imports, so the cycle risk is manageable. |
| **REF-3** — one inbound line, two headers | **Yes, as a helper, not a merger.** | `StockEntryDetail` and `PurchaseReceiptItem` genuinely duplicate `stock_qty()`, `stock_unit_rate()`, conversion derivation, and the forms/widgets (`apps/inventory/forms.py` around `:237-431`), and the views duplicate `_entry_line_params`/`_receipt_line_params` (`apps/inventory/views.py:448, 739`). The report is right to keep two documents: their GL stories differ. |
| **REF-4** — split cart vs catalog context | **Yes.** | `_render_cart` always calls `_build_order_context`, which loads the active menu, prefetches variants/add-ons, computes drink availability, catalog cards, and payment modes. Guest-card activate and guest-count changes need none of that. `catalog_oob` only controls rendering, not the queries. Low-risk, high-frequency improvement. |
| **REF-5** — lifecycle in services; drop `_allow_*` flags | **Directionally yes, do it gradually.** | The flags exist (`_allow_submit`, `_allow_cancellation`, `_allow_discard`, `_settling`) and the KOT-freeze rule is repeated in `Order.save`, `Order._ensure_editable`, `OrderPayment.save/delete`, and three views. But `Order.save` guards are load-bearing (they caught real bypasses), and `recalculate_totals` saves through the same door. Replace the flag-punching one transition at a time, keeping the model-level last-resort guards. |
| **REF-6** — one public GL voucher API | **Yes, medium effort.** | `_merge_rows` (nets) vs inventory `_post_gl_rows` (sums without netting) genuinely differ; `_reverse_gl` is private but imported by `apps/staff/services.py:299`; journals, orders, refunds, payables, cash-outs and inventory each write GL differently. The proposed `post_voucher`/`reverse_voucher` is the right shape. Caveat: `post_refund_gl` merges twice (`apps/accounting/services.py:380, 388`) — fix that while unifying. Keep the "fail if a used production unit has no account" idea. |
| **REF-7** — one Daily P&L statement | **Yes, phased.** | The triple representation is real: `Computation` (`apps/reports/services.py:66-74`), ~26 denormalised columns on `DailyPnL` (`apps/reports/pnl_models.py:62-100`), and `DailyPnLLine` rows. `KITCHEN_CONSUMPTION` is a choice that is never appended; a test even asserts its absence. The minimal first step (pass `rows`, delete the dead section) is cheap; removing the columns is a bigger, riskier change because lists/CSV and constraints use them. Do not post the Daily P&L to GL — the report is right that these are different products. |
| **REF-8** — canonical calendar querysets | **Yes.** | Verified copies (Part 1, Finding 21). Ignore the report's wrong claim about migration 0030 (Part 3). |
| **REF-9** — payables workflow off the models | **Yes, low priority.** | `SupplierInvoice.submit/cancel` and `SupplierPayment.submit/cancel` live on the models, `SupplierInvoiceItem.validate_for_submission` repeats `clean()`, and the dashboard does `sum(s.outstanding_balance for s in Supplier.objects.all())` (an N+1). The models are otherwise clean; moving submit/cancel next to `post_supplier_*_gl` matches the inventory pattern. Keep payables in `apps.accounting` as `PLAN.md` requires. |
| **REF-10** — one shift writer, `objects.open()` | **Yes, phased.** | Verified: open state is inferred as `status=SUBMITTED, closing_entry__isnull=True` in staff models/services/views and `views_pos._get_open_shift`; the two OneToOnes are real and deliberate; close is implemented twice with different form prefixes. The `open()` manager and a shared close API are good. Implement after Finding 10 so both paths share one rule. |
| **REF-11** — one Item family, one shape check | **Yes, but it is a product decision.** | Verified: `Item.has_variants`/`variant_of` and `menu.ItemVariant` are two graphs (POS uses both: `has_variants` to demand a size, `pos_variant_of` for catalog grouping); the FOOD/DRINKS flag matrix is copied into `Item.clean`, `ItemForm.clean`, `MenuItem.clean`, `ItemAddOn.clean`. Also confirmed: `Item.save` silently deletes add-ons (`:142-146`), `MenuItem.clean` mutates `rate` from `last_purchase_rate` (`:68-69`), `MenuItem.item_name` drifts after first save. Pick a single graph deliberately and migrate; the seed writing both is the immediate source of confusion. |
| **REF-12** — Restaurant as data, not an oracle | **Yes, carefully.** | Verified: `Restaurant.clean()` imports `PaymentGLMapping`, `Order`, `PurchaseReceipt`, `StockEntry` and blocks warehouse changes under drafts; `load()` is `order_by("pk").first()` and the unique `singleton_key` is never read. The cross-app guards are load-bearing (they prevent warehouse repointing under open drafts), so move them to the owning services without losing the checks. |
| **REF-13** — cached roles; collapse duplicate decorators | **Yes.** | Verified: `is_admin`/`is_manager`/`is_cashier` each run a `groups.filter().exists()`; `has_backoffice_access` and `manager_required` are the same predicate; `_role_required` wraps `@login_required` under a global `LoginRequiredMiddleware`. The orphan `test_role_perf` pyc shows this was measured before. A per-request cached role set removes the N+1. |
| **REF-14** — delete the Pegasus shell | **Yes, as live-path cleanup.** | Verified: signup wired, landing page for anonymous users, gravatar, `"SaaS, django"` keywords, `pegasus` logger, empty adapter, orphan pycs for Branch/Room/Table/Tax/icons. Pair with Finding 18 (close signup). Keep allauth itself; the login/logout/reset flows are used. `pending_approval` may still serve "account created, no role yet" — decide before deleting. Also delete `web.pos_index` (Finding 11). |
| **REF-15** — one cart line, one order body, one nav contract | **Yes, but large; go incrementally.** | Verified in full: `items.html` forks flat vs grouped (lines 4 vs 63); `order_history_detail.html` is a surface (4-166) plus a drawer (167-321) that drops customer grouping; the drawer shows a hardcoded ₦0 discount; the payment dialog stacks several Alpine blocks and closes via `window.location.href`; filter chips rebuild the query string eight times (the missing-`q` bug is fixed); OOB shell nav is copy-pasted on five surfaces; `#app-content` select/swap remains in four backoffice templates; inline `onclick` confirmations remain. Start with the highest-value piece (drawer/surface shared partials) and keep behaviour tests. |
| **REF-16** — delete leftover modes | **Mostly yes, two claims wrong.** | Correct: `discard_order` is only called by seed/tests; empty `apps/orders/models/` and `apps/inventory/models/` directories survive with `__pycache__` (a stray `__init__.py` would shadow `models.py`); empty `*ModelForm` subclasses exist (`Menu/Payments/Staff/Accounting/Payables/Reports`); `SettingsModelForm` forks `StyledModelForm` with different classes; orphan pycs; coming-soon discount UI. Wrong: `can_dispense_change` is used by the payments detail template (3.2); migration 0030 did not make `department` required (3.1). |
| **REF-17** — catalog add-item rules in the service | **Yes.** | Verified: `pos_order_add_item` does variant/add-on/menu resolution in the view; add-on resolution is repeated in `_variant_add_ons`; the active-card session dict is mutated with identical `isinstance(cards, dict)` boilerplate in six places. `apply_add_on_line` is the natural home for the acceptance rules. Keep the session helpers small; do not add a guest service. |
| **REF-18** — payment policy out of `OrderPayment.save` | **Yes, with a correction.** | Verified: reference rules, KOT lock, and `Restaurant.requires_payment_reference()` live in `OrderPayment.save`; the model split itself is clean. The report's "default-mode uniqueness enforced three times" is inaccurate: it is the `UniqueConstraint`, `ModeOfPayment.clean`, and a `save` guard that only covers *unsetting* the default — trust the constraint and keep one Python guard. The staff-services change query is now moot (3.7). |

**One note on REF-14 and REF-15 sequencing:** the reports propose a lot of file surgery. The order that changes outcomes fastest is: F1 (500), F2 (XSS), F3 (ledger bypass), F4+F5 (cancel correctness), then REF-6/REF-1, then the rest. Cosmetic refactors (REF-15, REF-16, NITs) can ride along with related work but should not block the correctness fixes.

---

# Part 5 — Nit verdicts (compact)

| Nit | Verdict | Note |
|---|---|---|
| NIT-1 | Confirmed | Three identical account lookups; one cached helper removes per-line queries. |
| NIT-2 | Confirmed | Raw `from`/`to` and `int(account_id)` can 500; reuse `report_filters.parse_date`/`int_param`. |
| NIT-3 | Confirmed | `ItemGroup.objects.count()` on every backoffice request; pass/cache it. |
| NIT-4 | Confirmed | `posModalDialog` lives in `order-details-drawer.js`; rename/split. |
| NIT-5 | Confirmed | Spinner and payment-icon blocks duplicated; a shared partial is reasonable. |
| NIT-6 | Confirmed | Four `money()` copies; one helper on the shared dialog component. |
| NIT-7 | Confirmed | Extra partialdef hop; harmless but needless. |
| NIT-8 | Confirmed | Container name lies once variant dialogs target it. |
| NIT-9 | Confirmed | Residual comment; delete with the Pegasus CSS leftovers. |
| NIT-10 | Confirmed | Docs describe FIFO and a nonexistent `_reverse_voucher`; runtime is WAC. Update when REF-1 lands. |
| NIT-11 | Confirmed | Non-FOOD departments become bar tickets silently; make it exhaustive and raise. |
| NIT-12 | Confirmed | One-line alias; delete. |

---

# Appendix — What was verified and how

**Method.** Both documents were read completely. Every finding that named a file, model, service, view, template, migration, setting or command was checked by reading the current source, and where claims depended on behaviour (Django internals, allauth defaults, `static()` behaviour, migration SQL semantics, deduplication of SQL rows) the relevant library code or Django's own behaviour was checked directly.

**Notable direct checks.**
- `django/db/models/sql/compiler.py` (Django 6.0.5): confirms `select_for_update` raises outside a transaction — Finding 1.
- `allauth.account.adapter.DefaultAccountAdapter.is_open_for_signup` (allauth 65.18.0): returns `True`; the project's adapter is empty — Finding 18.
- `django.conf.urls.static.static()`: returns `[]` when `DEBUG=False` — Part 3.5.
- `ruff check --select F,B`: all checks pass (confirms the audit's unused-import/TODO cleanliness for that scope).
- `ruff check --select E501`: six lines over 120 characters, at different locations from the audit's list.
- Python 3.12 `ast.parse`: fails on `apps/inventory/views.py:759` and `apps/orders/views.py:325` because of unparenthesized `except A, B:` — confirms the portability point (but see `AGENTS.md`'s explicit instruction not to mass-fix it).
- AST scan of public view functions: counts of missing docstrings by module.
- Git history `2026-09-10 … 2026-09-26`: identified the commits that fixed reported issues (cited per item in Part 2).

**What this document is not.** It is not a new security audit. It verifies the two reports; it does not claim to have found every issue. Claims both reports marked "Verified OK" were spot-checked, not exhaustively re-audited (listed in Part 3.10). No application code, templates, migrations or settings were modified.
