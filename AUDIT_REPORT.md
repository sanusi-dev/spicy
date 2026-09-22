# Spicy Codebase Audit Report

**Date:** 2026-09-10
**Scope:** Full codebase — `apps/` (accounting, inventory, orders, staff, users, settings, payments, menu, reports, web, utils), `spicy/`, `templates/`, `assets/`
**Method:** Static code review of 325 Python files + 131 templates, verified against code with `file:line` references. No changes made.
**Reference baseline:** Costing is Perpetual Weighted-Average Cost (PWAC), not FIFO. All money fields are `DecimalField` — no `FloatField` for money in app models (verified clean).

---

## Executive Summary

| Severity | Count | Headline |
|---|---|---|
| Critical | 0 confirmed remote-exploitable; 4 High employee-fraud paths | Electronic payment without reference, solo void of sent orders, single-actor stock write-offs, shift-close takeover |
| High | 9 | Backdated reversals, GL net-to-zero, transfer GL silence, race conditions, insecure defaults, self-signup |
| Medium | 18 | Cash variance misposting, P&L/GL divergence (4 variants), expected-drawer double-count, IDOR history, draft ownership, negative close amounts |
| Low / Info | ~25 | Rounding modes, monthly-slice penny loss, dead params, N+1s, docstring gaps, HTMX error states, XSS-fragile patterns |
| Code health | Good overall | Zero unused imports (ruff F clean), zero TODO/FIXME, zero commented-out code; 14 files over 300 lines; Python 3.14-only `except` syntax breaks older tooling |

**Strongest controls (do not regress):** negative stock blocked unconditionally (`apps/inventory/models.py:434-436`); drink reservation→settlement fully locked with `select_for_update`; prices/discounts/totals recomputed server-side (client sends only `item_id`/`qty`); submitted docs immutable at ORM level (except gaps noted); no `CASCADE` on orders/payments/ledger entries; CSRF on all HTMX; no `|safe` in `templates/`.

---

## 1. Exploitable Design Decisions by Employees (Fraud Paths)

These are the highest-priority findings — each is a complete, low-skill theft/concealment path with current code.

### HIGH-1 — Electronic payment accepted with empty reference: fake-transfer theft
**Files:** `apps/orders/services.py:924-936`, `apps/orders/views_pos.py:811-832`, `apps/orders/models.py:516-521`

**What happens:** `_validate_payment_data` strips `reference_no` and only checks length. Empty string passes. The duplicate-reference check only fires when non-empty. The project's own test proves it: `test_settle_accepts_electronic_payment_without_reference` (`apps/orders/tests/test_pos_views.py:785-796`) settles a ₦3,000 Bank payment with no reference.

**Attack steps:**
1. Customer pays cash (or accomplice takes goods).
2. At Settle, cashier enters full total under Bank/Transfer with reference left blank.
3. Order submits (`SUBMITTED`, `is_paid=True`). Cash drawer expected is unchanged, so drawer balances.
4. Missing bank credit is visible only at off-system bank reconciliation, not in the drawer.

**Fix:** require non-empty `reference_no` for all non-cash modes at settle; add unique-per-mode reference enforcement already present for non-empty values.

### HIGH-2 — Cashier can void a sent order solo: take-cash-then-cancel
**Files:** `apps/orders/views_pos.py:855-902`, `apps/orders/services.py:238-284`

**What happens:** `pos_order_cancel` requires only `@staff_required` plus a dropdown reason (`wrong_order`/`customer_changed_mind`/`cashier_error`/`other`). No manager approval. Return-after-payment is manager-only (`apps/orders/views.py:212-244`) — the hole is specifically pre-payment voids.

**Attack steps:**
1. Take order, press Send (kitchen/bar ticket prints, food prepared).
2. Collect cash off-record.
3. Cancel sent draft with reason "wrong_order". No payment rows exist, drawer expected unchanged.
4. Serve food. Detection requires proactive manager review of cancelled-order history — shift close only blocks *open* drafts (`apps/staff/services.py:156-160`).

**Fix:** require manager PIN/approval for cancelling any order with KOTs; surface cancel counts on shift-close screen as a blocking review item.

### HIGH-3 — Single-actor, no-approval, no-attribution stock write-offs hide theft
**Files:** `apps/inventory/models.py:680-761`, `apps/inventory/services.py:638,821`, `apps/inventory/views.py:638-661`

**What happens:** `StockReconciliation`/`StockReconciliationItem` have no `submitted_by`/`cancelled_by` actor fields (contrast `Order.cancelled_by`, `KOT.cancelled_by`). Services take no actor, views pass none, `remarks` is optional, no amount threshold, no second-approver step (unlike cash `variance_approval_threshold`).

**Attack steps:**
1. Steal 12 bottles from Bar.
2. File `ADJUSTMENT` with counted qty = physical remainder (or `WASTE_DAMAGE` qty 12 "broken in transit"). Bin matches physical, GL balances to adjustment/wastage account.
3. No actor on document, no mandatory note, no approval. Write-off is indistinguishable from legitimate use.

**Fix:** add actor stamps, mandatory remarks, amount-threshold second approval, and surface adjustment/wastage lines prominently on P&L + shift close.

### HIGH-4 — Any cashier can close any shift with any variance; POS close has no variance-note path
**Files:** `apps/settings/models.py:108-115`, `apps/staff/services.py:199-206`, `apps/orders/views_pos.py:407-453`, `templates/pos/close_shift.html:32-106`

**What happens:** `variance_approval_threshold` is nullable with help text "Leave blank to allow any variance without a note" — enforcement only runs `if threshold is not None`. The POS template renders only `closing_amount` inputs — no `variance_note` field — while `submit_closing_entry` checks `locked.variance_note`. No opener/closer ownership check: `_get_open_shift()` returns the global open shift, closer is whoever submits.

**Attack steps:** open Close Shift, enter counts hiding shortage (or zeros). With blank threshold (default-able), shift closes solo with no note and no manager. Cashier also sees exact expected before counting (`close_shift.html:63-66`) so target-matching is trivial.

**Fix:** disallow blank threshold (require explicit opt-out with manager sign-off); add `variance_note` input to POS close template; enforce opener≠closer or manager co-sign on variance; implement blind-count (hide expected until after submit).

### MEDIUM — No draft ownership: any cashier edits/settles/cancels anyone's draft
**Files:** `apps/orders/views_pos.py:530-546,628-641,692-703,736-749,778-791,804-809,857-875,907-920`, `apps/orders/services.py:174-175`

Every draft-mutating POS view scopes only by `opening_entry=shift` + `status=DRAFT`. Drafts carry no owner — `Order.cashier` is assigned only at settle. One open shift max (`apps/staff/models.py:60-72`), so all drafts are a shared pool. Cashier B can resume, alter, settle, cancel, or delete Cashier A's draft. Post-hoc attribution depends on `audit.actor` rows nobody is forced to review.

**Fix (if named logins must mean accountability):** stamp `created_by` on draft, restrict edit/settle/cancel/delete to owner or manager.

### MEDIUM — Opening float self-declared, zero allowed; draft opening takeovers
**Files:** `apps/orders/views_pos.py:342-366`, `apps/staff/services.py:108-133`, `apps/staff/forms.py:34-76`, `apps/staff/views.py:128-155,158-200`

Any `@staff_required` user opens a shift with any non-negative floats including all-zeros. All `staff/views.py` shift mutations look up by `pk` with no `request.user == entry.cashier` check. `_save_opening_entry` unconditionally reassigns `entry.cashier` (`staff/views.py:141`), silently changing owner.

**Fix:** require manager sign-off on zero/low floats; add ownership checks on all shift mutations.

### MEDIUM — History detail + receipt reprint unscoped by shift (IDOR/enumeration)
**Files:** `apps/orders/views_pos.py:1052-1092`, `apps/orders/services.py:730-777`

`pos_order_history_detail` filters only by `pk` + status — no shift/date/cashier. `pos_order_history_print` filters only `pk` + `SUBMITTED`. Clearing date param returns all dates. Incrementing `history/<pk>/` reads any past order; POST `history/<pk>/print/` reprints any receipt (duplicate pickup/refund social engineering). Contrast `pos_order_ticket_print` (`views_pos.py:943-956`), correctly scoped to open shift.

**Fix:** scope history detail/print to current shift (or manager override); add rate-limit/audit on reprints.

### MEDIUM — `closing_amount` accepts negatives: variance manipulation
**Files:** `apps/staff/forms.py:17-31`, `apps/staff/models.py:269-272`, `apps/orders/views_pos.py:437-441`, `apps/staff/views.py:284-288`

`ClosingPaymentForm` has no `min_value` (`min="0"` is widget-only). Model field has no validator. Crafted POST `closing_amount=-5000` passes; `difference = closing - expected` records artificial shortage (or nets with inflated mode to wash a real shortage under threshold).

**Fix:** add `min_value=0` + model `MinValueValidator(0)`.

### MEDIUM — Deleting unsent draft purges audit trail by design
**Files:** `apps/orders/models.py:252-271`, `apps/orders/views_pos.py:905-931`

`Order.delete()` releases reservations then wipes audit events via queryset delete, bypassing the immutability guard (comment at `models.py:269-270` admits deliberate). Any cashier can invoke on any unsent draft. No money moves, but probing (add drinks, check availability, delete) is untraceable.

**Fix:** retain `ORDER_DELETED` audit row with actor + item snapshot instead of purging.

---

## 2. Calculation Errors and Inaccuracies

### HIGH — Income and payment legs can net to zero and vanish when misconfigured to same account
**File:** `apps/accounting/services.py:169-191` (`_merge_rows`, lines 186-190)

Rows sharing `(account, against)` are netted; zero-net rows dropped. Nothing prevents a payment mode's GL account equalling the income account. Admin maps Cash mode to Food Sales account → ₦5,500 cash sale posts Dr 5,500 / Cr 5,500 to same account → both legs dropped. Batch "balances", P&L (order-driven) shows ₦5,500, GL shows nothing. Same for supplier payment if payable == cash account.

**Fix:** validate debit-side and credit-side accounts are disjoint before merging; reject config mapping payment account to income account.

### MEDIUM — Refund COGS uses current WAC, sale used settle-time WAC; difference never posted
**Files:** `apps/accounting/services.py:285-305,382-389`, `apps/orders/services.py:1113-1151`

Sale debits COGS at settle-time WAC; refund credits expense and debits warehouse at *return-time* WAC. `_restore_stock` computes variance for audit (`SALE_RETURN`) but no GL leg posts it. Sell (COGS ₦100) → WAC rises to ₦150 → return restores +₦150. Cycle nets −₦50 COGS for an unwound transaction; warehouse never returns to pre-sale value.

**Fix:** refund at original settle-time WAC; post variance to inventory price variance account (pattern exists at `apps/inventory/services.py:597-624`).

### MEDIUM — P&L drink-COGS rate disagrees with GL refund rate
**Files:** `apps/reports/sources.py:57-87` (single last-SLE rate) vs `apps/accounting/services.py:264-282` (weighted average)

Same return valued differently: two sales at ₦100/₦140 then wastage return → GL ₦120 (average), P&L ₦140 (last). Daily P&L never reconciles to GL on days with drink returns at mixed rates.

**Fix:** share one rate function.

### MEDIUM — P&L silently drops sales lines with NULL department
**Files:** `apps/reports/sources.py:43-50`, `apps/inventory/services.py:117-121`, `apps/orders/models.py:388-393`

`OrderItem.department` is nullable. `sales_by_department` reads only `.get(FOOD)`/`.get(DRINKS)`; NULL lines excluded from `gross = food + drinks`. GL (`_income_legs` falls back to default income account) still includes them. Legacy/backfilled NULL rows → P&L and GL permanently disagree.

**Fix:** coalesce NULL to live item department (as `_order_lines_with_accounts` does) or explicit bucket.

### MEDIUM — Cash variance posts to arbitrary cash account with split cash modes
**File:** `apps/accounting/services.py:448-459` (`.first()` enabled cash mode)

Till exact, Safe over ₦500 → journal debits Till ₦500 instead of Safe. Total right, drawer-level GL wrong.

**Fix:** post per-mode variance legs.

### MEDIUM — P&L and shift reports attribute one order to different days (two clocks)
**Files:** `apps/reports/sources.py:28-40` (buckets by `posting_date`+`posting_time` set at draft creation, `apps/orders/models.py:109-110,229-231`) vs `apps/orders/models.py:72-80` + `apps/staff/services.py:162` (shift close uses `submitted_at`)

Draft 23:50, settled 00:10 → P&L today, closing shift tomorrow. Day-level P&L-to-shift reconciliation breaks.

**Fix:** stamp `posting_date/time` at submit, or bucket P&L by `submitted_at`.

### MEDIUM — Cash-change double-count with split cash modes
**File:** `apps/staff/services.py:39-49`

Change summed per `(mode_id, order_pk)` row; order split across two *different* cash modes contributes full `change_amount` once per mode. ₦5,000 order, ₦3,000 Till + ₦3,000 Safe, ₦1,000 change → ₦2,000 subtracted, expected understated ₦1,000, phantom shortage.

**Fix:** attribute each order's change once (e.g., first cash mode row).

### LOW — Inconsistent rounding modes (HALF_UP vs HALF_EVEN)
**Files:** `apps/orders/models.py:302` (HALF_UP) vs `apps/orders/models.py:495`, `apps/accounting/payables_models.py:311`, `apps/reports/services.py` (bare quantize → HALF_EVEN)

Max 1 kobo per line, but GL, P&L splits, supplier totals can round opposite directions on identical inputs.

**Fix:** one shared `quantize` helper with explicit mode.

### LOW — Monthly recurring-expense daily slice never sums to monthly amount
**File:** `apps/reports/sources.py:186-188` (`(amount / days).quantize(TWO)`)

₦10,000 / 30 days → ₦333.33 × 30 = ₦9,999.90. 10 kobo never appears in any day's P&L.

**Fix:** post remainder on last day.

### LOW — Stored `unit_rate` vs `stock_value_change` penny divergence
**Files:** `apps/inventory/models.py:581-586`, `apps/inventory/services.py:389,941`, `apps/accounting/services.py:152`, `apps/reports/sources.py:105`

Receipts pass quantized `unit_rate` (0.01) + exact `inbound_value`. Blend uses exact; GL/P&L recompute `qty × unit_rate`. Drift up to ~₦0.005/unit by construction.

### LOW — Zero-rate receipts legal, dilute WAC toward zero
**File:** `apps/inventory/models.py:891-892` (blocks `< 0`, allows `0`)

Receiving qty at ₦0 depresses COGS, inflates margins. May be legitimate (donations) but unflagged margin lever. Requires backoffice access.

---

## 3. Bad Accounting Setup

### HIGH — Reversals backdated to original period (all except order reversals)
**Files:** `apps/accounting/services.py:604,640`, `apps/inventory/services.py:626,814,868,1032,1074`, `apps/accounting/models.py:406-420`; contrast `services.py:231-257` (`reverse_order_gl` posts at current date, docstring states principle)

Cancelling January invoice in March reposts mirror entries dated January — silently rewriting a reviewed/closed period instead of appearing in March. Only `reverse_order_gl` follows the stated "never retroactively alter period" rule.

**Fix:** post all reversals at cancellation date.

### MEDIUM — Material Transfers post no GL legs; warehouse SIH accounts diverge permanently
**File:** `apps/inventory/services.py:364-441` (GL built only for `MATERIAL_RECEIPT` at `:395-399,431-438`; transfer branch `:400-427` appends nothing; cancel `:459-540` likewise silent)

Store→Kitchen/Bar moves real value between warehouses each carrying own SIH `account` (`apps/inventory/models.py:54-62`) with zero journal. Downstream legs credit those accounts (drink sale credits Bar SIH `accounting/services.py:160-166`; consumption credits Kitchen SIH `inventory/services.py:803-804`; waste credits owning SIH `:747-748`). Bar/Kitchen SIH only credited, never debited; Store only grows. GL stock balances cannot reconcile to `Bin` values. (Net zero only if all warehouses share one SIH account; config permits and seed creates per-warehouse leaves.)

**Fix:** post transfer journals (Dr destination SIH / Cr source SIH at transfer WAC).

### MEDIUM — GL and P&L disagree on non-restockable ("wastage") drink returns; collapse to zero under default seed
**Files:** `apps/accounting/services.py:382-404` (expense-credit + wastage-debit net to zero), `apps/reports/sources.py:136-152` (adds positive WASTAGE on top of return subtraction), `apps/accounting/management/commands/seed_chart_of_accounts.py:284` (defaults `wastage_account = cogs`)

Under default config GL legs net to exactly zero — wasted return leaves no books trace while P&L reports cost. Bartender voiding sales as "wastage" shows margin erosion on P&L but nothing in GL to investigate.

**Fix:** separate default wastage account; align GL/P&L treatment.

### LOW — Silent no-post traps in cash-variance GL
**File:** `apps/accounting/services.py:421-443`

Shortage with only `cash_over_short_account` configured (or excess with only `cash_shortage_account`) returns `None` — no posting, no error. `Restaurant` missing → `None`. Admin configuring only one account → ₦20,000 shortage closes cleanly, never hits expense.

**Fix:** explicit "unposted variance" marker or require both accounts; at minimum log.

### LOW — `Restaurant.default_stock_in_hand_account` configured but never used
**File:** `apps/settings/models.py:135-143`; zero references in accounting/inventory services (invoice stock lines always hit `stock_received_but_not_billed_account`, `services.py:571-574`)

Admins configure an account (seed sets it) affecting nothing → reconciliation confusion.

**Fix:** wire it in or remove field.

### LOW — Submitted shift docs editable at ORM; journal row insert races submit totals
**Files:** `apps/staff/models.py:210-215` (no status guard on `POSClosingEntry.save`; `OpeningPayment`/`ClosingPayment` none), `apps/staff/services.py:189-196`, `apps/accounting/models.py:487-493` (stale in-memory status check; `submit` locks header not rows)

`ClosingPayment.closing_amount` overwritable via ORM post-submit; journal row inserted between `submit()` total computation and `GLEntry.post` lands in DB but not GL. No UI path today; ORM is last defense elsewhere.

**Fix:** status guards on shift `save()`s; `select_for_update` on parent in row `save()` or lock rows in `submit()`.

### LOW — `JournalEntry.save` raises `NameError` not `ValidationError` for direct non-draft creates
**File:** `apps/accounting/models.py:298-312` (`allow_submit`/`allow_cancel` defined only inside `if self.pk:`, dereferenced unconditionally at line 311)

Latent — normal paths always create DRAFT. Fix: init both flags `False` before branch.

### LOW — `reverse_order_gl` is dead production code
**File:** `apps/accounting/services.py:231-257`; only caller is test `apps/accounting/tests/test_order_gl.py:205-213`

Untested-in-production reversal path; reversal rows share `voucher_no` with originals and `is_cancelled=False`, so future existence-check false-positives. Wire to real flow with distinct marker or remove.

---

## 4. Bad Inventory Design

### HIGH — (Covered in §1 HIGH-3) single-actor write-offs + §3 transfer GL silence. Additional items below.

### MEDIUM — Transfer cancellation revalues at destination's current WAC, no variance recorded
**File:** `apps/inventory/services.py:515-540` (`unit_rate=dest_wac` at `:534`; `variance_*` left default, unlike receipt cancel `:562-577,1015-1016`)

Transfer 10 @ ₦100, destination blends to ₦200, cancel pulls 10 back at ₦200 — Store WAC polluted by destination history with no flag.

### MEDIUM — `StockLedgerEntry` + `Bin` lack model-level immutability; Bin editable in admin
**Files:** `apps/inventory/models.py:296-478` (no `save()`/`delete()` override; acknowledged `docs/workflows/inventory.md:70`), `apps/inventory/admin.py:125-134` (plain editable `BinAdmin`), `:137-170` (SLE read-only only when bypass off)

Shell or bypass-enabled admin can create/delete SLE without moving Bin, or edit `Bin.actual_qty/reserved_qty/valuation_rate` with no SLE, GL, or audit. Manager edits Bin down to match pilfered physical — no document, no actor (stock docs record no user), no variance.

**Fix:** mirror `GLEntry.save/delete` guards (raise on update/delete); make `BinAdmin` read-only.

### MEDIUM — P&L drink COGS keys off current `Item.department`, sales off snapshot
**Files:** `apps/reports/sources.py:97-122` vs `:43-50`, `apps/orders/models.py:475-480`, `apps/inventory/models.py:157-219` (no guard on `Item.department` changes)

Reclassifying Item rewrites historical COGS column while sales stay put. Manager reclassifies drink→FOOD after bad-margin day; drink COGS drops, bar margin flattered.

**Fix:** snapshot department on SLE/usage rows; filter P&L by snapshot.

### MEDIUM — N+1 fan-out on P&L/returns path (correctness OK, scaling risk)
**Files:** `apps/inventory/services.py:108-242` (per-ingredient `Item.objects.get` `:224` + Bin lookup `:71-81`), `views.py:896` + `services.py:90-105` (per-recipe re-query), `apps/orders/services.py:1131-1151`, `:1045`, `apps/reports/sources.py:36-40`

Cost grows with menu/order volume on P&L path.

### LOW — `prevent_negative` parameter dead; check unconditional
**File:** `apps/inventory/models.py:355,385,405,413-478` (flag threaded but never read; `new_qty < 0` raise at `:434-436` always applies)

Safe behavior, misleading for future authors relying on `prevent_negative=False`.

### LOW — Ledger ignores `reserved_qty`; cancellations can drive actual below reserved
**Files:** `apps/inventory/models.py:413-478` (never reads `reserved_qty`), `apps/inventory/services.py:1017-1031,515-527`, `apps/orders/services.py:963-996,991-992`

Bar 10 actual / 8 reserved; manager cancels old receipt of 5 → actual 5 < reserved 8. Later settlements fail "Stock reservation data is inconsistent" — self-inflicted denial of drink sales.

### LOW — Transfer lines silently ignore `basic_rate`; no money validation
**File:** `apps/inventory/services.py:404-427`, `apps/inventory/models.py:574-579`

User types rate on transfer line; discarded without warning (value always moves at WAC — economically correct, UI misleading). Transfer lines skip `quantize` + `>0` validation receipt lines get.

### LOW — Post-consumption transfers un-cancellable with bare error
**File:** `apps/inventory/services.py:515-527` (atomic + generic `InsufficientStock`, `models.py:436`)

Fails closed correctly, but no partial-cancel or force-with-variance path; error doesn't explain why.

### LOW — `ProductionUnit.warehouse` repointable under open reservations; only `Restaurant` guarded
**Files:** `apps/settings/models.py:206-229` vs `:304-322`, `apps/orders/services.py:1233-1251`

Manager repoints Drinks unit mid-shift; old drafts settle out of orphaned warehouse.

### LOW — Backdated postings revalue current WAC; cost lands in different period than effect
**Files:** `apps/inventory/models.py:426-427` (only future blocked), `apps/inventory/services.py:164`

By design per `docs/workflows/inventory.md:41`, but backdated receipt moves today's WAC while consumption recs match `posting_date == business_date` — cost and WAC effect in different periods.

### LOW — UOM conversions mutable history; recipe-vs-ledger precision loss
**File:** `apps/inventory/models.py:225+`, `services.py:217-218` (4dp recipe → 2dp ledger quantize)

Editing/deleting `ItemUOMConversion` after use unguarded; historical SLEs keep old blended WAC with no link back.

### INFO — Food has no POS-time stock check by design (load-bearing asymmetry)
**Files:** `apps/orders/services.py:712-713,323-361`, `docs/workflows/inventory.md:29`, `docs/workflows/daily-pnl.md:§8`

POS sells unlimited food regardless of kitchen stock; food COGS entirely downstream of managers filing CONSUMPTION/WASTE recs. Day with food sales + no consumption count reports food COGS ₦0 (reads as excellent margin). No missing-consumption warning on P&L. Documented intent — flagging because absence of warning makes food theft invisible in margins.

### INFO — `dev_admin_bypass` escape hatch
**Files:** `apps/utils/admin.py:4-12`, `apps/inventory/admin.py:31-77,158-170`, `spicy/settings.py:25` (`SPICY_DEV_ADMIN_BYPASS = DEBUG`)

With flag on, superuser can add/edit/delete SLEs and rewrite submitted docs via admin — single switch disabling every ledger control. Verify off in production (tied to DEBUG; see §5).

---

## 5. Security Concerns (Auth, Config, Django)

### HIGH — Known `SECRET_KEY` + insecure fallbacks if `.env` missing
**File:** `spicy/settings.py:16,18,27` (verified)

- `SECRET_KEY` defaults to `django-insecure-HjfWKVIxpdgt4NHh8q56GGVTdjDvKeYV32hlsbl1` — identical value committed in tracked `.env.example:9`.
- `DEBUG` defaults `True`; `ALLOWED_HOSTS` defaults `["*"]`.
- `.env` itself gitignored (`.gitignore:28`) — live secrets not committed. But booting without `.env` → DEBUG + known key + `*` hosts → session/cookie forgery, host-header attacks.
- `docker-compose.yml`: postgres password `postgres`, Redis unauthenticated — fine bound to `127.0.0.1`, unsafe if exposed.

**Fix:** require real `SECRET_KEY` (fail boot if unset); `DEBUG=False`, explicit `ALLOWED_HOSTS` defaults.

### MEDIUM — Open self-signup, no approval gate, no rate-limiting
**Files:** `spicy/settings.py:173-174,193`, `spicy/urls.py:13`, `apps/users/signals.py:11-13`, `apps/users/decorators.py:21-24`

Anyone on WiFi self-registers (username+password only), active immediately; signal only emails admins. Mitigation: no group → role-less users denied by all decorators — spam foothold, not direct escalation. No lockout (`axes`), captcha (TURNSTILE keys in `.env.example` unread in settings), rate-limit.

**Fix:** gate/disable public signup, or manager-approval queue; add login rate-limit + idle timeout (shared terminal + `ACCOUNT_SESSION_REMEMBER=True` + default 2-week session = stale sessions).

### MEDIUM — Missing hardening flags; production settings opt-in
**Files:** `spicy/settings.py` (no `SECURE_HSTS_*`, `NOSNIFF`, `REFERRER_POLICY`, `*_COOKIE_SECURE`, `SESSION_COOKIE_AGE`, CSP), `spicy/settings_production.py:4-10` (sets them, but nothing forces its use — no wiring in `Makefile`/`docker-compose.yml`)

`SESSION_COOKIE_NAME`/`CSRF_COOKIE_NAME` renamed (`settings.py:150-151`) but `HttpOnly`/`SameSite` left at Django defaults.

### LOW-MEDIUM — Email verification off + non-unique email
**File:** `spicy/settings.py:178,193` (effective `"none"`), `:180` (`ACCOUNT_UNIQUE_EMAIL=False`)

Duplicate emails → password-reset ambiguity; same-email impersonation alongside open signup. Login limited to username (`:173`) containing impact.

### LOW — Default password validators; weak seed creds committed
**Files:** `settings.py:155-168`, `apps/orders/management/commands/seed_test_data.py:110,124,443` (`cashier/pos1234`, `manager/manager1234` via `set_password`, bypassing validators; `pos1234` 7 chars fails signup validation)

Only risky if seed run outside dev — known creds on privileged manager account.

### LOW — Role management: no self-demotion/last-admin guard; invalid role strips silently
**File:** `apps/settings/views.py:95-129,132-150`

Unrecognized `role` value falls through after `user.groups.remove(...)` — user left role-less, no error. Admin can demote self/last admin → self-lockout (not escalation). No audit log of role changes.

### LOW-MEDIUM — Avatar upload: extension-only validation
**Files:** `apps/users/models.py:12-14,20` (`FileField`, not `ImageField`; `uuid + "." + name.split('.')[-1]`), `apps/users/helpers.py:22-46` (extension + 5MB only; no MIME/magic-byte/PIL), `spicy/urls.py:26` (Django serves `MEDIA_URL` unconditionally)

Script with `.jpg` extension uploads successfully. Stored-JS execution requires victim opening file URL directly (serving content-type dependent) — current risk low.

**Fix:** content-verified `ImageField`.

### INFO — `promote_user_to_superuser`: no guardrails (shell-only, not remotely exploitable)
**File:** `apps/users/management/commands/promote_user_to_superuser.py:12-20`

Bare username → `is_superuser`/`is_staff`, no confirmation/logging. Fat-finger risk only.

### INFO — `printer_ip` unvalidated CharField (latent SSRF, not exploitable — no client exists)
**File:** `apps/settings/models.py:273`, `apps/settings/forms.py:115-138`, `docs/workflows/receipts-and-printing.md:10`

Validate as IP/hostname when Phase-12 print agent built.

### INFO — Personal data in repo
**Files:** `spicy/settings.py:243,284,291`, `spicy/settings_production.py:14` (`sanusio293@gmail.com` as `DEFAULT_FROM_EMAIL`/`ADMINS`/contact)

Functional but should be env-driven.

### Verified clean (audited scope)
No `|safe`/`mark_safe`/`autoescape off`/`RawSQL`/`.extra()`/`.raw()`/`eval`/`exec`/`subprocess` in `apps/users|settings|payments` or `templates/` (note: `apps/web/templatetags/form_tags.py:10` uses `mark_safe` on internally-rendered values + `|safe` on `help_text` — help text is developer-authored, not user input; low risk, worth noting as the single exception to "zero |safe in templates" which holds for `pos/`+`backoffice/`). No `csrf_exempt` anywhere. All audited POST views use forms + server validation. Payments mode create/update `@manager_required` — cashier cannot enable modes or edit GL mappings. Profile form exposes only `email/first_name/last_name` — no mass-assignment to groups. Singleton DB-backed (`unique=True` on `singleton_key`), not UI-bypassable.

---

## 6. Code Quality: Comments, Docstrings, Dead Code, Style

### Dead code — mostly clean (verified)
- **Unused imports/vars: 0.** `ruff check --select F` and `--select B` both pass.
- **TODO/FIXME/HACK/XXX: 0.**
- **Commented-out code: 0.** Sole grep hit is explanatory prose (`apps/orders/models.py:269`).
- **Obsolete views/URLs: none.** Every public view in inventory/orders/accounting/staff/menu/payments/reports/settings appears in a urls file. Orphan-template spot-check passed.
- **Mock data in production: none.** Seeds are dev-only management commands, never imported by runtime. No `Mock`/`lorem`/`foo` in models/services/views.

### Bad comments — few, low severity
No chain-of-thought blocks or wrong/outdated comments. Style is generally WHY-explanatory (e.g., `apps/orders/models.py:267-268`, `apps/orders/services.py:425`). One borderline restatement: `apps/inventory/models.py:442` explains *what* not *why* (inbound blend fallback).

### Bad docstrings
- **Views almost entirely undocumented (inconsistent):** `apps/inventory/views.py` 56 funcs missing (e.g., `L50 inventory_dashboard`, `L59 uom_list`); `apps/orders/views.py` 5; `apps/menu/views.py` 10+; `apps/payments/views.py` 8/9; `apps/accounting/views.py` + `payables_views.py` ~10 each; `apps/reports/views.py` 10; `apps/staff/views.py` 7; `apps/settings/views.py` 10/13. Trivial CRUD arguably compliant per project "omit if self-explanatory" — but non-trivial helpers lack one-liners: `apps/reports/sources.py` 8 missing (`L28 order_datetime`, `L36 orders_in_window`, `L43 sales_by_department`, `L53 round_off`, `L90 drink_cogs`, `L156 cash_variance`); `apps/orders/models.py:322 _validate_pos_item` undocumented while sibling has one.
- **WHY/HOW in docstrings (violates "Docs state WHAT"):** `apps/utils/admin.py:5-11` (deployment rationale in docstring); `apps/orders/models.py:357-359` (rationale + stale second sentence — method never touches submits/cancellations).
- **Restatement one-liner:** `apps/orders/services.py:399` (`"""Remove a line from a draft order."""`).

### Style and maintainability
- **Files over 200–300 lines (refactor candidates):** `apps/orders/services.py:1268`, `apps/inventory/services.py:1126`, `apps/orders/views_pos.py:1092`, `apps/inventory/views.py:1051`, `apps/inventory/models.py:980`, `apps/orders/models.py:689`, `apps/accounting/services.py:640`, `apps/inventory/forms.py:527`, `apps/accounting/models.py:503`, `apps/reports/services.py:375`, `apps/staff/views.py:364`, `apps/settings/models.py:322`, `spicy/settings.py:321`, `apps/accounting/views.py:308`.
- **Python 3.14-only unparenthesized `except A, B:` (PEP 758) — 10+ sites; breaks parsing on ≤3.13 and most external tooling.** Verified: system Python 3.12 `ast.parse` raises `SyntaxError` on `apps/orders/views_pos.py:166`. Ruff passes only because `target-version = "py314"`. Sites include `apps/orders/views_pos.py:166,317,649,668`; `apps/orders/services.py:88,94,890,907`; `apps/orders/views.py:257`; `apps/inventory/views.py:757,997`. **Recommendation:** parenthesize everywhere (`except (A, B):`) — identical semantics, portable, unblocks editors/CI running older Python.
- **Bare `except Exception:` (5, all swallow-then-redirect with logging — acceptable but broad):** `apps/utils/forms.py:79` (`return None` in `_get_model_field` — hides real errors); `apps/staff/views.py:171,195,338,359`.
- **Missing type hints on services:** zero `def .*->` in `apps/orders/services.py`, `apps/inventory/services.py`, `apps/accounting/services.py`, `apps/reports/services.py`, `apps/staff/services.py`. POS private helpers (`apps/orders/views_pos.py` 15/35: `_render_pos_surface`, `_home_or_redirect`, `_get_open_shift`, …) unannotated while public `pos_*` views fully hinted.
- **Duplicate FOOD/DRINKS validation (4 near-identical ~8-line blocks):** `apps/menu/models.py:58-66`, `:97-108`, `apps/orders/models.py:325-333`, `:339-347` (also `SIM102` nested-if at each site).
- **Duplicated display-string join:** `apps/orders/services.py:195,540` (identical `"Food"/"Drinks"` join twice).
- **`print()` in management commands (2):** `apps/users/management/commands/promote_user_to_superuser.py:20`, `apps/web/management/commands/send_test_email.py:20` (use `stdout.write`).
- **PEP 8 120-char (6, all help_text/messages):** `apps/accounting/payables_models.py:36`, `apps/inventory/forms.py:106`, `apps/inventory/models.py:775`, `apps/menu/models.py:106`, `apps/reports/models.py:24`, `apps/settings/models.py:114`.
- **Hardcoded personal strings:** `spicy/settings.py:243,284` (email), external `wikimedia.org` image URL (legitimate but env-worthy).

### Tests
- **Coverage broad** (30+ files; `test_views.py (inventory)` 78 tests, `test_services.py (orders)` 42; no assert-less files outside helpers).
- **Gap:** `apps/orders/printing.py:1-21` (stub always-success `PrintResult`) has no direct test — existing tests only *mock* it. Real agent swap-in has no contract test.
- **Printing inside settlement transaction:** `settle_order` (`apps/orders/services.py:148-234`) calls `dispatch_tickets(created)` (`:200`, real printer I/O) inside `@transaction.atomic` before payments/GL (`:201-217,232`). Later failure rolls back DB (KOT rows gone) but paper already printed — food made for never-settled order. Move I/O post-commit (`transaction.on_commit`).
- **Audit-log data bug:** `update_order_item` (`apps/orders/services.py:117-127`) records line pk as `item_id` in `ITEM_QUANTITY_CHANGED` metadata (remove branch `:109-116` correct) — misattributes reconciliation.
- **Migration hygiene clean:** no hand-written schema ops; data migrations isolated with `RunPython` + `reverse_noop`/`get_or_create`.

---

## 7. Frontend (Templates, HTMX, Alpine, JS)

### MEDIUM — POS has no global HTMX error state; backoffice does
Backoffice: `templates/web/app/app_base.html:161` (`#htmx-error`) + `:170` (`hx-on::response-error`). POS: zero `response-error|htmx-error` hits. Per-action banners exist (`partials/cart/panel.html:3-4`, `totals.html:4-9`) but nav/filter `hx-get` failures (shell nav, `draft_orders.html:8`, `order_history.html` filters, catalog search) leave stale UI silently.

### LOW — Hardcoded relative URLs in `hx-get` (convention only)
`templates/pos/order_history.html:10,12,14,15,38-40,45-47,111,114` hand-build query strings instead of `{% url %}`. Every other `hx-get`/`hx-post` uses `{% url %}`. Works today; route rename breaks silently.

### LOW — HTMX nav/filter GETs lack loading states (cart mutations have them)
Cart uses `hx-indicator` + `hx-disabled-elt` + spinners (`partials/cart/totals.html:20,125-130`). None on: `shell_navigation.html` links, `draft_orders.html:8`, `order_history.html:10-47,111,114`, `partials/catalog/panel.html:6,10`, `sidebar.html:5,13`, `backoffice/settings/staff_list.html:11`. Mutations all POST so double-click risk limited to duplicate GETs.

### LOW — User strings into `Swal.fire({html})` via `data-confirm-message` (safe today, fragile)
`assets/javascript/confirm.js:15-25` sinks into `html:`; call sites interpolate `{{ entry.user.get_display_name }}` (`backoffice/settings/staff_list.html:58`), `{{ mi.item_name }}` (`backoffice/menu/menu_detail.html:120`). Django autoescape makes it inert today; any future `|safe`/`mark_safe` on display name → stored XSS. Only HTML-sink for user data (zero `|safe` in `pos/`+`backoffice/` templates; zero `innerHTML`/`eval` in `assets/`).

### LOW — Alpine `x-data` numeric interpolation without `escapejs`
`pos/partials/payment/dialog.html:5`, `pos/close_shift.html:8,48` (`Number('{{ order.rounded_total }}')`). Server Decimals safe today; breaks if ever non-numeric. Safe pattern one file away: `partials/catalog/sidebar.html:13` uses `|escapejs`.

### LOW — Raw inline SVGs instead of shared partial/icon tag; `onclick` bypassing central confirm
- Boilerplate SVGs (`web/base.html:56,79`, `landing.html`, `pending_approval.html`) + copy-pasted POS spinner block 6+ times (`pos/partials/cart/totals.html:86,112,126`, `order_history_detail.html:17,32,49,307`, `partials/gates/error.html:4`) instead of shared partial. Icon usage otherwise correct (`{% hgi_stroke %}`, no `class` param).
- `onclick="Swal.fire(...).then(...submit())"` in `backoffice/orders/order_detail.html:18,23,27,34`, `pos/partials/cart/totals.html:55` duplicates `confirm.js`, skips its double-submit guard.
- `style=""` only in Pegasus boilerplate (`web/public_base.html:65`, `pending_approval.html:11,33`) — none in `pos/`/`backoffice/`. No DaisyUI anywhere (hits false positives: `glass-card`, Tom Select `ts-`, icon `alert-02`).

### Verified OK
No `hx-get` mutates state (all mutations `hx-post`/POST); CSRF header + per-form tokens (82 occurrences); empty states everywhere (`{% empty %}`, "No orders found"); no `console.log`/`localStorage` (active card is server session `pos_customer_card_activate`); no `fetch()` (all HTMX/native POST); client totals preview-only, server authoritative (settle recomputes + rejects under/overpayment; close recomputes cutoff); destructive actions confirmed + server-backed (`order_detail.html:16` ↔ `@manager_required`; `staff_list.html:56` ↔ `@admin_required`; reprint ↔ 403 at `views_pos.py:941-942`); dialogs have `role=dialog`, focus trap, `aria-disabled`.

---

## 8. Remediation Priority

| # | Action | Effort | Blocks |
|---|---|---|---|
| 1 | Require reference for electronic payments; reject blank | S | Fraud HIGH-1 |
| 2 | Manager approval for cancelling sent (KOT) orders; cancel counts on close screen | S–M | Fraud HIGH-2 |
| 3 | Actor stamps + mandatory remarks + threshold approval on stock reconciliations; `BinAdmin` read-only | M | Fraud HIGH-3, theft concealment |
| 4 | Non-blank variance threshold; `variance_note` on POS close; blind-count; `min_value=0` on close amounts; ownership checks | M | Fraud HIGH-4, MEDIUMs |
| 5 | Post transfer journals (Dr dest / Cr source SIH); post all reversals at cancel date; disjoint debit/credit accounts | M | Books correctness |
| 6 | Unify WAC rate source (GL = P&L); refund at settle-time WAC + variance leg; fix NULL-department + change double-count + day-clock | M | P&L↔GL reconciliation |
| 7 | Harden boot: no default SECRET_KEY, `DEBUG=False`, explicit hosts; force production settings; gate signup; session idle timeout | S | Config HIGH/MEDIUM |
| 8 | Parenthesize all `except (A, B)`; add model guards (SLE immutability, close-amount validator); move printing to `on_commit` | S | Portability, race safety |
| 9 | POS global HTMX error banner; loading states; `escapejs` on Alpine interpolations; shared spinner partial | S | UX robustness |
| 10 | Docstrings on `reports/sources.py` + validators; dedupe FOOD/DRINKS rule; per-mode variance legs; remainder-day expense | S | Maintainability |

---

## 9. Files Reviewed (top-level)

`apps/accounting/models.py, services.py, payables_models.py, views.py, payables_views.py` · `apps/inventory/models.py, services.py, views.py, forms.py, admin.py` · `apps/orders/models.py, services.py, views.py, views_pos.py, printing.py` · `apps/staff/models.py, services.py, views.py, forms.py` · `apps/users/models.py, views.py, decorators.py, signals.py, adapter.py, helpers.py` · `apps/settings/models.py, views.py, forms.py` · `apps/payments/models.py, views.py, forms.py` · `apps/menu/models.py, views.py` · `apps/reports/models.py, pnl_models.py, services.py, sources.py, views.py` · `apps/web/views.py, middleware` · `apps/utils/models.py, forms.py, admin.py` · `spicy/settings.py, settings_production.py, urls.py` · `templates/pos/`, `templates/backoffice/`, `templates/web/` · `assets/javascript/` · `docker-compose.yml`, `Makefile`, `.env.example`

*End of report. All findings reference code as of 2026-09-10 working tree. Items marked Verified OK were checked and need no action.*
