# Spicy — Complete-Restaurant Verdict

**Question:** is what is left in `PLAN.md` actually what is left, if the goal is a functioning,
lightweight but feature-rich single-location restaurant system?

**Short verdict:** mostly yes, but not complete. Printing (§4.8) and Reports (§4.7) are the
correct two remaining builds. Three operational gaps and five hardening gaps are not in the
plan at all. This file lists each one in plain language first, then the technical pointer.

Dropped scope stays dropped: multi-branch, floor-plan/tables, customer master with credit.
Those are deliberate and correct for a small–medium single site.

---

## 1. What the plan already covers correctly

### 1.1 Printing — blocking, correctly planned

**What it is:** the three thermal printers (cashier receipt on USB, kitchen ticket on LAN,
bar ticket on LAN) plus the local print agent that turns a Django order into ESC/POS bytes.

**Why it is missing:** `apps/orders/printing.py` always returns success. There is no agent,
no receipt/ticket template, no print-job table, no retry queue beyond per-ticket status.
Printer IP/width fields exist on the production unit but nothing reads them.

**Why it is needed:** without it the kitchen and bar work from memory or shouting. That
fails at volume and during disputes (who ordered what, when).

**Gain:** tickets print automatically on Send and on Settle-guarantee; reprint/retry from
history; takeaway suppression per unit works as configured.

**Technical:** `apps/orders/printing.py:1-21`, `apps/orders/services.py:608-623`,
`apps/settings/models.py:246-293`, `docs/workflows/receipts-and-printing.md:32`.

### 1.2 Sales reports + formal books — correctly planned, wrong priority

**What it is:** everything in `FEATURES.md` #63 / `PLAN.md` §4.7: today/daywise/month-wise,
item-wise, employee-wise, service-wise (dine-in vs takeaway), time-wise, cancelled invoices,
average bill, POS register, trial balance, simple P&L over `GLEntry`.

**Why it is missing:** `apps/reports/` today is Daily P&L only
(`views.py:1-258`, `urls.py:9-31`). No sales aggregation by item, cashier, hour, or order
type exists. Only dept split (`sources.py:43-50`) and per-mode sums exist.

**Why it is needed even though Daily P&L exists:** Daily P&L answers "did we make money
today, split food/drinks?" It does not answer "which dish drives revenue?", "who sold
what?", "what are peak hours?", "dine-in vs takeaway?", "what did we void?", "what is the
average bill?", "do the books balance?" Those are the weekly owner questions.

**Gain:** restock the right items, staff correctly, spot theft/void abuse, give the
accountant a trial balance + simple P&L instead of exporting raw GL.

**Priority split (since we are not rushing go-live, but build in the right order):**
first daywise + item-wise + POS register + cancelled invoices + average bill; then
employee/service/time/month-wise; then trial balance + simple P&L. No balance sheet —
explicitly out of scope in §4.7 and fine for lightweight (owner cares P&L + cash; the
chart already carries `report_type = BALANCE_SHEET / PROFIT_AND_LOSS` in
`apps/accounting/models.py:25-30`, so one can be added later).

**What exists today without it:** today's revenue/count (`orders/views.py:34-70`), single
order drill-down, per-shift drawer balance by mode, per-day food/drinks/net/profit. Shift
gross sales are computed (`staff/services.py:162-197`) but not rendered
(`closing_entry_detail.html:29-72` shows only drawer columns).

---

## 2. What is missing from the plan

### 2.1 POS variant picker — admitted in FEATURES, no plan task

**What it is:** selling "Chicken — Quarter / Half / Full" by choosing a size on the POS,
instead of hunting three separate cards.

**Why we think it is missing:** `FEATURES.md` A2 #9 says "POS variant selection is not yet
implemented." Back office manages variants (`apps/menu/models.py:115-130`,
`views.py:229-276`); POS code contains zero `ItemVariant` references and only prefetches
`item__add_ons`. Template items are barred from the menu, so each size is a flat card.

**Why it is needed:** without a picker the catalog grows (every size × every dish), cashiers
mis-tap, and price differences are invisible at tap time.

**Gain:** smaller catalog, faster taps, fewer wrong-size disputes.

**Lightweight fix:** either implement the picker or explicitly defer with "flat variant
cards" as the supported pattern. Do not leave it admitted-but-untracked.

### 2.2 Comps, staff meals, goodwill, price correction — no path at all

**What it is:** the everyday "give it free / reduce it" cases: staff meal, spoilt dish
replacement, VIP goodwill, cashier error correction. Not a coupon engine — a
manager-approved reason with a record.

**Why we think it is missing:** `Order`/`OrderItem` have no discount/comp fields
(`apps/orders/models.py:93-160,379-408`; totals = lines + rounding in
`recalculate_totals():297-312`). Old `discount_amount` / tax fields were removed by
migration and never replaced. POS has only a disabled "Apply Discount / Coming soon"
placeholder (`templates/pos/partials/payment/dialog.html:19-28`). Coupons are deferred
(F #66).

**Why it is needed:** every real restaurant comps daily. With no path, staff misuse
cancel/return or go off-book, which corrupts sales and hides the true cost of goodwill.

**Gain:** honest sales (comps recorded, not deleted), a P&L bucket for "how much did we
give away?", manager accountability via reason + audit event.

**Lightweight fix:** manager-only comp line with reason + audit, posted to its own expense
bucket. Full coupon/pricing-rules engine stays deferred.

### 2.3 Mid-shift cash-out (petty cash / transport / market runs)

**What it is:** cash taken OUT of the drawer during a shift that is not a refund and not a
market stock purchase — e.g. transport fare, emergency ice, petty repairs.

**Why we think it is missing:** `POSOpeningEntry` records float IN; `POSClosingEntry`
records counted vs expected; expected = float + payments − refunds
(`staff/services.py:53-81`). No mid-shift cash-out model exists. `DailyPnLAdHoc`
(`pnl_models.py:227-239`) is a P&L memo with no cash/GL link (`services.py:1` states no GL
posting). A generic JournalEntry could move cash but has no shift/drawer linkage. Only
drawer-aware cash-out is market `StockEntry MATERIAL_RECEIPT` with "Paid from"
(`inventory/models.py:497-503`).

**Why it is needed:** without it the drawer never balances on days with cash errands, and
the cashier looks short.

**Gain:** drawer balances truthfully; each cash-out posts Dr expense / Cr cash and reduces
expected drawer automatically.

### 2.4 Low-stock / reorder signal

**What it is:** a nudge that "Coke is down to 6 bottles" before the POS greys it out.

**Why we think it is missing:** reorder fields were removed (`migrations/0012_remove_reorder_level`);
`inventory/views.py:50-55` dashboard passes only counts; `dashboard.html:1-122` has no
low-stock section. A stale doc line claims a low-stock dashboard that does not exist.

**Why it is needed:** drinks are the stock-tracked sale (`Bins` drive POS availability).
Running out mid-service loses sales; manual bin-checking does not scale with 50+ SKUs.

**Gain:** fewer stock-outs, one glance each morning instead of opening every bin.

**Lightweight fix:** threshold warning list only (no auto-PO, no supplier lead-time logic).

### 2.5 Data export for the accountant and owner

**What it is:** CSV/Excel/PDF download on the order register, GL list, stock ledger, and
Daily P&L.

**Why we think it is missing:** no export view/template exists; `pyproject.toml:62-76`
has no `openpyxl`/`reportlab`/`weasyprint`/`django-import-export`.

**Why it is needed:** the accountant does not log into the POS. Without export, month-end
means screenshots or manual retyping.

**Gain:** painless month-end, auditable paper trail outside the system.

### 2.6 Backoffice user creation

**What it is:** a Manager/Admin page to create a cashier login (username + password + role)
without touching Django admin.

**Why we think it is missing:** `apps/users/views.py:1-42` is profile-only;
`settings/views.py:72-150` assigns/removes roles but never creates users;
`staff_list.html:1-92` has no Add-user UI.

**Why it is needed:** staff turnover is high; requiring Django admin for every hire is
fragile and over-privileged.

**Gain:** safe onboarding/offboarding by the manager on duty.

### 2.7 Backup and restore

**What it is:** a one-command database dump and a tested restore, on a schedule.

**Why we think it is missing:** `Makefile:1-109` has no backup target;
`docker-compose.yml:1-32` has only live volumes; `scripts/` has only dev helpers.
A Docker volume is not a backup.

**Why it is needed:** the whole business (sales, stock, GL) lives on one cashier desktop.
Disk failure, theft, or a bad migration without a dump means starting over.

**Gain:** a restorable file per day; go-live confidence.

### 2.8 Single open shift = single terminal

**What it is:** the system allows exactly one open shift globally
(`staff/models.py:12,57-72`), with one cashier.

**Why it matters:** correct for one cashier desktop today. It silently becomes the ceiling
the day a second terminal or split shift appears — the second cashier cannot open.

**Gain of documenting it:** no surprise later. Keep as-is for lightweight; record that
multi-terminal shifts are separate paid work like multi-branch.

### 2.9 Tax / VAT position needs a signed decision

**What it is:** no sales tax, VAT, or consumption-tax computation anywhere
(`FEATURES.md:15`, `PLAN.md:319`, `recalculate_totals` docstring "no tax"). Supplier
`tax_id` is an info field only.

**Why it flags:** deliberate and fine for a small cash operation — but Nigerian VAT (7.5%)
and Lagos consumption-tax rules change what a "complete" receipt must show. This is the
one dropped feature that can make receipts non-compliant.

**Gain of deciding now:** either a written "prices are tax-inclusive, no tax line" policy,
or a minimal inclusive-tax display later. Do not discover this at audit time.

---

## 3. Correctly dropped — stays out

- **Multi-branch** (`FEATURES.md:170`): separate paid work; singleton `Restaurant` is the
  right call.
- **Tables / floor plan:** order type (Dine-In / Take-Away) + guest count + customer cards
  cover counter service and small dine-in. Table turnover analytics is the accepted loss.
- **Customer master with credit/loyalty:** free-text name suffices while all sales are
  walk-in cash/bank with no receivables ledger (`PLAN.md:319` — no party ledger, correct).
- **Purchase orders:** receipt-first invoices + market receipts cover a single-site kitchen.
  A PO approval chain would add weight without gain.
- **Prep/work orders, finished-plate stock, nested recipes:** correctly out of §4.12 scope;
  recipe cards + consumption/waste counts give true food cost without a factory module.
- **Service charge / tips:** zero code paths exist and none are needed for the stated
  operation; revisit only if the pricing policy changes.
- **Balance sheet:** correctly deferred in §4.7; trial balance + simple P&L suffice for
  lightweight books.

---

## 4. Re-scoped remaining work (no go-live rush — completeness order)

1. **Printing (§4.8)** — agent, formats, routing, job status.
2. **Correctness cleanups:** remove/wire the discount placeholder; render shift sales totals
   already computed; fix stale "no order tracking yet" help text and stale low-stock doc.
3. **Cash integrity:** mid-shift cash-out voucher (drawer + GL); backup/restore target.
4. **Manager self-service:** backoffice user creation; comp-with-reason (minimal, not coupon
   engine); signed tax-inclusive policy note.
5. **Full reporting (§4.7 in this order):** daywise, item-wise, POS register, cancelled
   invoices, average bill → employee/service/time/month-wise → trial balance + simple P&L.
6. **Catalog polish:** variant picker decision (implement or formally defer to flat cards);
   low-stock signal list; CSV/Excel export on the four main registers.
7. **Deferred, recorded:** customer master, coupon engine, multi-branch, multi-terminal
   shifts, balance sheet.
