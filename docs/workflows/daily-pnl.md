# Daily P&L

**A Daily P&L is a manager-built snapshot of one restaurant business day.** It answers: *how much did food make, how much did drinks make, what did the day cost, and what is left as profit?* Submitting it freezes those numbers on a document. It does **not** post anything to the general ledger.

This page explains the idea, the purpose, the statement shape, every source of numbers, the manager workflow, and how the code actually builds the report.

---

## 1. The idea

A classic profit-and-loss statement is:

```text
Revenue
− Cost of what you sold
− Other costs of running the day
= Profit
```

Spicy does that for **one business day**, split into **FOOD / DRINKS / TOTAL**, because the bar is a separate business sharing the same cashier.

The report is a **management snapshot**, not the accounting books. Settlement already posts income, payment, rounding, and drink COGS to the GL. The Daily P&L re-reads operational data (orders, stock movements, shift closes, plus a few numbers the manager types) and presents a restaurant-shaped day: sales, food and drink cost, theoretical-vs-actual food memos, electricity, gas, wages, rent slice, cash shortage, net profit.

Think of it as the owner's end-of-day sheet, not the accountant's trial balance.

```text
Cashier sells food and drinks all day
Kitchen counts remaining stock (consumption)
Bar stock is deducted automatically on drink sales
Shift closes (cash counted)
        ↓
Manager opens Daily P&L for that business date
Types meter readings, material qty, one-off costs
        ↓
System pulls live sales / drink cost / kitchen usage / cash variance
        ↓
Three-column statement appears (preview)
        ↓
Manager submits → numbers freeze on the document
```

---

## 2. Why it exists

This restaurant has two businesses on one till:

| | Kitchen (FOOD) | Bar (DRINKS) |
|---|---|---|
| What is sold | Prepared plates | Bottles / cans / poured drinks |
| Stock on sale | Food sales do **not** deduct inventory | Drink sales deduct bar stock at current WAC |
| How cost is known | Kitchen consumption + waste counts valued at kitchen WAC | Automatic from the stock ledger at sale |
| On the P&L | Sales minus actual food usage (FOOD COGS); recipe × sales shown as theoretical memo, variance as memo | Sales minus drink COGS |

Without a daily statement, the owner can see that the till took ₦80,000 and still not know:

- how much of that was food vs drinks
- whether the bar's margin is healthy (sales vs drink cost)
- whether the kitchen used a lot of stock relative to food sales
- whether wages, electricity, rent-for-the-day, and a cash shortage ate the profit

The Daily P&L is the document that answers those questions for **one day**, in a form that can be previewed, submitted, cancelled, and amended.

**Documented product intent** (`FEATURES.md` A10): a daily profit-and-loss document — management snapshot, no GL posting — with a departmental split, actual food usage as FOOD COGS, theoretical food cost and variance as memos, prime cost as a highlight, and a configurable business-day start hour.

---

## 3. What it is not

| It is not | What Spicy does instead |
|---|---|
| The formal accounting P&L | A query over `GLEntry` — see [Query reports](query-reports.md). |
| Something that posts journals | `submit_daily_pnl()` writes snapshot rows only. Tests assert `GLEntry` count does not change. |
| Automatic at midnight | A manager creates a draft, fills inputs, previews, and submits. |
| A live dashboard | Drafts recompute from live data; submitted documents are frozen history. |
| A recipe-costed food COGS | Actual kitchen usage (consumption + waste) **is** FOOD COGS; recipe × sales is a theoretical memo beside it. |
| An asset depreciation schedule | `daily_depreciation` is a flat naira amount per day in P&L settings. |

The GL already moved when orders settled and when the shift closed. Daily P&L is a **second view** of the same day, shaped for the owner, with extras the GL does not have (meter readings, cooking-gas qty, a day's slice of monthly rent).

---

## 4. Who uses it, and when

- **Who:** Manager / Admin / superuser only. Cashier has no access. Views raise 403 via `@manager_required`.
- **Where:** Back office → **Reports** → Daily P&L. Settings live under **Reports → P&L settings**. Home also has a shortcut to create a draft.
- **When:** After the business day is over enough to have: settled orders, a kitchen consumption count if you care about that memo, a submitted shift close if you want cash variance, and the meter/material numbers you want on the sheet.
- **How many:** At most **one DRAFT** and **one SUBMITTED** Daily P&L per `business_date`. Cancelled ones can coexist; amending a cancelled document creates the next draft for that date.

---

## 5. The statement you see

Every Daily P&L is a table with five columns:

| Line | Food | Drinks | Total | % of gross |
|---|---:|---:|---:|---:|

Rows are built top to bottom in this order:

```text
Gross sales
Round-off
Net sales
Cost of goods sold                  ← FOOD = actual usage, DRINKS = drink WAC
Theoretical food cost               ← FOOD memo; recipe × sales
Food cost variance                  ← FOOD memo; theoretical − actual
Direct expenses
  Electricity (optional)
  Materials (gas, etc.)
  Recurring "direct daily" templates
  Ad-hoc directs
Gross profit
Prime cost                          ← memo: food actual + drink COGS + labor
Employee costs
Depreciation
Cash variance
Other indirects (rent, % of sales, ad-hoc)
Net profit
```

**Memo lines** (amber in the UI, `is_memo=True` in code) are shown so a human can compare them to sales. They are **not** subtracted again when gross profit or net profit is calculated.

Percentages are `amount_total / gross_sales × 100`, stored to three decimal places. If gross sales are ₦0, every percent is 0.

---

## 6. A worked example

Suppose 19 August, start hour midnight. Settled orders that day:

| Line | Qty | Price | Department |
|---|---:|---:|---|
| Jollof Rice | 10 | ₦1,500 | FOOD |
| Coke | 20 | ₦500 | DRINKS |

Coke's current weighted-average cost (WAC) in the bar is ₦200. The kitchen used 10 kg of rice at ₦200/kg (consumption count), and jollof has a recipe of 0.125 kg per plate.

P&L settings: electricity ₦50/unit, daily depreciation ₦50. Manager enters meter 10 → 12 (2 units), cooking gas 2 kg at ₦100/kg, and a wages template of ₦8,000/day.

| Line | Food | Drinks | Total | Notes |
|---|---:|---:|---:|---|
| Gross sales | 15,000 | 10,000 | 25,000 | Sum of submitted order lines |
| Round-off | — | — | 0 | Sum of order rounding |
| Net sales | 15,000 | 10,000 | 25,000 | Gross + round-off |
| Cost of goods sold | 2,000 | 4,000 | 6,000 | Food actual 10 × ₦200; drinks 20 × ₦200 WAC |
| Theoretical food cost *(memo)* | 250 | — | 250 | 10 plates × 0.125 kg × ₦200 |
| Food cost variance *(memo)* | −1,750 | — | −1,750 | 250 − 2,000 |
| Electricity | — | — | 100 | 2 × ₦50 |
| Cooking gas | — | — | 200 | 2 × ₦100 |
| **Gross profit** | 13,000 | 6,000 | 18,700 | See formulas below |
| Prime cost *(memo)* | 2,000 | 4,000 | 14,000 | 6,000 COGS + 8,000 labor |
| Employee costs | — | — | 8,000 | Template or override |
| Depreciation | — | — | 50 | Settings |
| Cash variance | — | — | 0 | No shortage |
| **Net profit** | — | — | 10,650 | 18,700 − 8,050 |

Formulas used:

```text
GP food    = food sales − actual food − food-tagged directs   → 15,000 − 2,000 − 0
GP drinks  = drinks sales − drink COGS − drinks directs → 10,000 − 4,000 − 0
GP total   = net sales − (actual food + drink COGS) − all directs → 25,000 − 6,000 − 300
Net profit = GP total − all indirects                   → 18,700 − (8,000 + 50)
```

Electricity and materials land in **Total** only, unless a recurring/ad-hoc row is tagged FOOD or DRINKS. That is why **GP food + GP drinks (19,000) is not equal to GP total (18,700)** in this example: ₦300 of unallocated directs sits only on the total column.

Actual usage sits inside food gross profit (₦2,000 used vs ₦15,000 sold); theoretical (₦250) and variance (−₦1,750) are memos explaining the gap between recipe expectation and counted usage.

---

## 7. Where every number comes from

Two kinds of input feed the engine.

### Pulled live at preview / submit

These are queried when `compute_daily_pnl()` runs. A draft's preview will change if more orders settle or settings change. A submitted document will not, because the result is copied onto the row.

| Number | Source | Filter |
|---|---|---|
| Food / drinks sales | Submitted `OrderItem.amount` summed by `department`, falling back to the item's department when a line's snapshot is NULL (matching the GL income legs) | Orders whose settlement-stamped `posting_date` + `posting_time` fall in the business-day window |
| Round-off | Sum of `Order.rounding_adjustment` | Same orders |
| Drink COGS | Drink `StockLedgerEntry` rows for those orders | Sales (`POS Order`, qty < 0) add the SLE's booked value (`abs(stock_value_change)`); restock returns (`POS Return`, qty > 0) subtract it; non-restockable return lines subtract the sale's cost and re-add it as wastage (net zero) |
| Food COGS (actual) | Kitchen `CONSUMPTION` + `WASTE_DAMAGE` reconciliation SLEs via `inventory.services.compute_food_usage()` | Rec `posting_date` **equals** the P&L `business_date` (calendar date, not the hour window); `ADJUSTMENT` excluded |
| Theoretical food cost (memo) | Active recipe × submitted FOOD `OrderItem` qty in the window (returns net; NULL department snapshots fall back to the item's department) | Same rate per ingredient as actual (actual-SLE WAC, else bin WAC, else last rate) |
| Food cost variance (memo) | Theoretical − actual | Quantity story in qty and naira |
| Cash variance | Submitted `POSClosingEntry.total_short_excess`, sign flipped | Close `period_end_date` in the business-day window; skipped if the settings toggle is off |
| Recurring expenses | Enabled `PnLRecurringExpense` templates | Daily amount as-is; monthly ÷ days in that month; percent × gross sales |
| Depreciation | `PnLConfiguration.daily_depreciation` | Always a line |
| Electricity rate | `PnLConfiguration.electricity_rate` | Used only if meter readings are entered |

### Typed on the draft

| Number | Field | Rule |
|---|---|---|
| Business date | `DailyPnL.business_date` | Frozen after create |
| Electricity readings | opening + closing | Both blank → no electricity line (₦0). One filled → error. Closing cannot be below opening. Rate must be set in settings if readings exist. |
| Material quantities | `DailyPnLMaterialQty.qty` | Qty × current catalog `PnLMaterial.rate`. Qty ≤ 0 is skipped. |
| Ad-hoc expenses | `DailyPnLAdHoc` | Amount > 0; section DIRECT or INDIRECT; optional FOOD/DRINKS tag |
| Employee override | `employee_cost_override` | If set, **replaces** all employee templates with one "Employee costs" line |
| Remarks | free text | Copied through; not computed |

```text
                    ┌─ live ─┐
Orders ─────────────► sales, round-off
Drink SLEs ─────────► COGS (+ returns / wastage)
Kitchen recs ───────► consumption memo
Shift closings ─────► cash variance
P&L settings ───────► rate, depreciation, templates
                    └────────┘
Meter, materials,   ┌─ draft ─┐
ad-hoc, override ───► inputs  │
                    └─────────┘
                            ▼
                   compute_daily_pnl()
                            ▼
              lines + totals (+ COGS / consumption breakup)
                            ▼
            preview (not saved)     or     submit (frozen on DailyPnL)
```

---

## 8. Food vs drinks — why the P&L looks "uneven"

This is the design that makes Daily P&L confusing if you expect a textbook manufacturing P&L.

### Drinks

Drinks are stock items at the bar. Selling a Coke deducts one unit at the **current weighted-average cost**. That cost is the bar's COGS.

```text
Purchase / transfer into Bar
        ↓
Bin WAC updates
        ↓
POS sale of Coke
        ↓
Stock ledger: −1 × current WAC
        ↓
Daily P&L COGS (DRINKS column)
```

Food sales never touch this column. `test_food_contributes_zero_cogs` locks that in.

Returns:

- Restockable drink return: inventory comes back; Daily P&L **subtracts** `qty × return SLE unit_rate` (the sale's settle-time WAC — the return restores at the same cost the sale removed).
- Non-restockable ("wastage") drink return: stock is not restored; Daily P&L **subtracts** the sale's cost (`RETURN` row) and **adds** the same amount as wastage (`WASTAGE` row) at the sale's settle-time WAC — the same rate the GL refund legs post. The two rows net to zero, so the bottle stays costed once and the wastage stays visible in the breakup.

**Current behavior:** Daily P&L reconstructs drink cost from stock ledger rows of the day's orders. It does not read GL COGS accounts. Accounting reverses the sale's COGS and records the wastage in its own account at the same settle-time WAC; that is a separate path.

### Food

Selling jollof does **not** deduct rice, oil, or chicken. Each sellable dish carries a recipe card (ingredients in `stock_uom`, yield baked into the qty). Cost of food is observed later:

```text
Store receives rice
        ↓
Transfer Store → Kitchen
        ↓
Kitchen cooks (no POS stock move)
        ↓
Manager counts kitchen (CONSUMPTION) + records waste (WASTE_DAMAGE)
        ↓
Ledger: −qty × current WAC
        ↓
Daily P&L FOOD COGS (actual) + theoretical/variance memos
```

**Documented design intent:** actual kitchen usage **is** food COGS and sits inside food gross profit. The theoretical memo (recipe × sales) beside it lets the owner judge "we sold ₦15,000 food, recipes expected ₦250 of ingredients, but the kitchen used ₦2,000" — a quantity story about portioning and waste, not plate-level accounting.

### Direct vs indirect

Restaurant P&Ls usually split:

- **Direct** — costs that vary with the day's operation (electricity used, gas used, a daily stall fee). Subtracted **before** gross profit.
- **Indirect** — running the business (wages, rent slice, depreciation, cash shortage). Subtracted **after** gross profit to get net profit.

**Prime cost** (a hospitality highlight) is food actual + drink COGS + labor. It is a memo so the owner can see "what it cost to put product and people on the floor" without double-counting it in net profit.

---

## 9. The business day

A P&L is dated with a calendar `business_date`, but sales are not "everything with that date on the invoice." They use a **window**:

```text
[business_date at start_hour,  next calendar day at start_hour)
```

`start_hour` lives on `PnLConfiguration` (0–23, default 0 = midnight).

Example with start hour **6**:

```text
Business date 19 Aug covers
  19 Aug 06:00  →  20 Aug 06:00  (end exclusive)

An order settled (submitted) at 20 Aug 01:00 belongs to 19 Aug's P&L.
An order settled at 19 Aug 05:00 belongs to 18 Aug's P&L.
```

**What uses the window**

- Orders: `posting_date` + `posting_time`, re-stamped at settlement (not the draft's creation stamps)
- Cash variance: closing `period_end_date`

**What uses the calendar date only**

- Kitchen consumption and waste reconciliations: `posting_date == business_date`

So a 6am business day and a consumption rec dated the next calendar morning will **not** land on the same P&L even if they feel like the same night. That is current behavior, not a second window.

On create and while the document is still a draft, `period_start` / `period_end` are refreshed from the current start hour. Submit writes the window one last time. Changing the start hour later does not rewrite a submitted document.

---

## 10. Settings (configured once, used every day)

URL: `/backoffice/reports/settings/` — `reports:pnl_settings`.

### Day and rates (`PnLConfiguration` singleton)

| Setting | Meaning |
|---|---|
| Business-day start hour | Hour the window begins. 0 = midnight. |
| Electricity rate | Naira per meter unit. Required if a draft has readings. |
| Daily depreciation | Flat naira, every P&L. Not an asset register. |
| Include cash variance | When on, shift-close short/excess becomes an indirect line. |

`PnLConfiguration.load()` get-or-creates the one row.

### Materials catalog (`PnLMaterial`)

Named consumables that are **not** inventory items: cooking gas, charcoal, etc. Each has a unit, a naira rate, and a disabled flag.

Creating a Daily P&L **seeds** a zero-qty row for every enabled material. The manager types how much was used; compute does `qty × rate`. On submit, `rate` and `amount` are copied onto the qty row so later catalog edits do not rewrite history.

### Recurring expense templates (`PnLRecurringExpense`)

Remembered lines that appear on every day's P&L until disabled.

| Kind | How the day's amount is calculated |
|---|---|
| Direct — daily | `amount` as-is, in the direct section |
| Indirect — daily | `amount` as-is, in the indirect section |
| Indirect — monthly | `amount ÷ days_in_that_month` (August 31,000 ÷ 31 = 1,000); the last day absorbs the rounding remainder so the month sums to `amount` |
| Indirect — % of gross | `percent/100 × gross_sales` |
| Employee — daily | `amount` as-is, in the employee section |
| Employee — monthly | `amount ÷ days_in_that_month`, employee section; last day absorbs the remainder |

Optional `department` (FOOD / DRINKS / blank). Blank means the naira sits on **Total** only.

If the draft has `employee_cost_override`, employee templates are ignored for that day.

Changing settings never rewrites a **submitted** P&L. A **draft** preview uses whatever settings exist at compute time.

---

## 11. The manager workflow

```text
P&L settings (once)
        ↓
Create draft for a business date
        ↓
Edit: meters, material qty, ad-hoc, optional wage override
        ↓
Save  and/or  Refresh preview  (HTMX)
        ↓
Submit  →  frozen statement
        ↓
(optional) Cancel  →  snapshot stays, status CANCELLED
        ↓
(optional) Amend  →  new DRAFT with the same inputs, live recompute
```

### Create

Pick a date. Save. The date cannot change afterwards. Enabled materials are seeded at qty 0. Redirect to the edit form.

Blocked if a draft **or** submitted P&L already exists for that date.

### Edit / preview

The form is the parent (`DailyPnLForm`) plus two inline formsets (materials, ad-hoc). HTMX add/remove row endpoints rebuild the posted formset and return the partial.

**Refresh preview** POSTs the whole form, **saves the draft**, then runs `compute_daily_pnl()` and swaps `#pnl-preview` with `_statement.html`. A GET of the edit page also computes a preview if it can.

### Submit

Allowed from the edit form or the detail page. Confirmation copy: *Totals freeze. Nothing posts to the general ledger.*

Submit requires a fiscal year covering `business_date` (`FiscalYear.get_for`) — same gate other financial documents use — even though no GL rows are written.

### Detail

Shows status, net sales, net profit, the statement, drink COGS breakup, kitchen consumption breakup, remarks.

### Cancel

Submitted → Cancelled. Status only. Snapshot rows stay. Nothing reverses in the ledger.

### Amend

Cancelled → new Draft linked via `amended_from`. Copies electricity, override, remarks, material qtys, ad-hoc rows. Compute runs again against **current** live data and settings, not the cancelled snapshot.

---

## 12. Document lifecycle

```text
                create
                  │
                  ▼
               DRAFT  ←── amend (copy inputs)
                  │
               submit
                  │
                  ▼
             SUBMITTED
                  │
               cancel
                  │
                  ▼
             CANCELLED ──► amend creates a new DRAFT
```

| From | To | What happens |
|---|---|---|
| — | DRAFT | Create; period window filled; materials seeded |
| DRAFT | DRAFT | Edit inputs; delete allowed |
| DRAFT | SUBMITTED | Recompute, write lines/totals/breakups, freeze |
| SUBMITTED | CANCELLED | Flip status; keep snapshot |
| CANCELLED | new DRAFT | Copy inputs; recompute later on submit |

Guards:

- Only drafts can be edited or deleted.
- Business date cannot change after create.
- Submitted rows cannot be edited (save blocked unless `_allow_submit` / `_allow_cancel`).
- Unique: one DRAFT per date, one SUBMITTED per date (DB constraints plus `clean()`).

---

## 13. How the code is put together

`apps.reports` owns this phase. Other apps do not call it. Reports **reads** orders, inventory, staff closings, and fiscal years. It writes only its own tables.

```text
Browser (manager)
    │
    ▼
reports/views.py          access, forms, HTMX, redirects
    │
    ├─► forms.py          DailyPnL + settings + formsets
    │
    ├─► models.py         settings: config, materials, templates
    ├─► pnl_models.py     the document + input rows + snapshot rows
    │
    └─► services.py       compute_daily_pnl / submit_daily_pnl
              │
              └─► sources.py    window, orders, COGS, consumption, meter, templates
```

There are no P&L signals, no Celery jobs, and no GL event handlers. Views do not calculate profit. They save inputs and call the service.

Templates:

| Template | Role |
|---|---|
| `daily_pnl_list.html` | Register with status/date filters |
| `daily_pnl_form.html` | Create + draft edit + preview |
| `daily_pnl_detail.html` | Frozen (or draft) statement + breakups |
| `_statement.html` | Shared three-column table (preview **and** detail) |
| `_material_formset.html` / `_adhoc_formset.html` | HTMX row add/remove |
| `pnl_settings.html` | Singleton + catalogs |

The statement partial accepts either live `LineSpec` dataclasses (preview) or saved `DailyPnLLine` rows (detail). Both expose the same attributes.

---

## 14. Compute, step by step

`compute_daily_pnl(pnl)` in `apps/reports/services.py` builds a `Computation` (totals, lines, cogs_rows, consumption_rows, theoretical_rows, unmapped_rows). Nothing is written yet.

1. Load settings. Build `[start, end)` from `business_date` + start hour.
2. Collect submitted orders in that window (`orders_in_window` then `sales_by_department`, `round_off`).
3. Drink COGS + item rows (`drink_cogs`). Food usage via `inventory.services.compute_food_usage()` — actual, theoretical, variance, unmapped.
4. Append sales / round-off / net sales / COGS (food actual + drinks) / theoretical + variance memo lines.
5. Directs: electricity if readings exist; each material with qty > 0; `DIRECT_DAILY` templates; ad-hoc DIRECT rows.
6. Gross profit from the formulas in §6.
7. Employee: override **or** employee templates.
8. Prime cost memo = food actual + drink COGS + employee total.
9. Depreciation; cash variance.
10. Other indirects: remaining templates + ad-hoc INDIRECT rows.
11. Net profit = GP − all indirects (employee + depreciation + variance + other indirects).
12. Attach `% of gross` to every line and to the totals dict.

`_split(amount, department)` puts a tagged amount in FOOD or DRINKS (and Total). Untagged amounts go to Total only.

### Drink COGS detail (`sources.drink_cogs`)

`start`/`end` are unused. The function uses the **orders already in the window**:

- Sale orders → `StockLedgerEntry` `voucher_type="POS Order"`, qty < 0, item department DRINKS → kind `SALE`, amount positive.
- Return orders → `voucher_type="POS Return"`, qty > 0, drinks → kind `RETURN`, amount negative.
- Return orders with `not_restockable` drink lines → kind `RETURN` (amount negative) plus kind `WASTAGE` (amount positive) at `orders.services.settle_time_rate()` (weighted settle-time WAC — one shared rate helper with the GL refund legs). The pair reclassifies the sale's cost and nets to zero.

### Food usage (`inventory.services.compute_food_usage`)

The single source for the Food usage page and the P&L food numbers — reports call it, never reimplement it. Theoretical explodes active recipes over submitted FOOD order lines in the window (returns net, unmapped dishes listed separately). Actual reads submitted Kitchen `CONSUMPTION` + `WASTE_DAMAGE` SLEs on `posting_date == business_date` (`ADJUSTMENT` excluded). One rate per ingredient values both sides: actual-SLE weighted average, else Kitchen bin WAC, else `last_purchase_rate`, else 0 (flagged on the report).

### Cash variance (`sources.cash_variance`)

Shift-close `total_short_excess` is **negative for a shortage** (counted less than expected). Daily P&L stores `−that`, so a shortage is a **positive expense**. Excess is negative (reduces expense / increases profit). Toggle off → ₦0.

### Electricity (`sources.electricity`)

`(closing − opening) × rate`. Blank/blank → ₦0 and no line unless opening is not None (the service still emits a line when opening was provided). One-sided readings raise. Rate ≤ 0 with readings raises.

### Recurring (`sources.recurring_amount`)

Daily kinds return `amount`. Monthly kinds divide by `calendar.monthrange`. Percent kinds use gross sales.

---

## 15. What submit actually writes

`submit_daily_pnl(pnl, actor)` is one `@transaction.atomic` block.

1. `select_for_update` the document. Must be DRAFT.
2. `FiscalYear.get_for(business_date)` — hard fail if no year.
3. `compute_daily_pnl(locked)` again (never submit stale preview).
4. Snapshot each material row's current catalog `rate` and `qty × rate` onto `DailyPnLMaterialQty`.
5. Delete previous `lines`, `cogs_rows`, `consumption_rows`, `theoretical_rows`, `unmapped_rows` (a re-submit path is not exposed in the UI; this keeps the write idempotent inside the function).
6. Insert `DailyPnLLine` for every `LineSpec`.
7. Insert `DailyPnLCogsRow` / `DailyPnLConsumptionRow` (kind `CONSUMPTION` | `WASTE`) / `DailyPnLTheoreticalRow` / `DailyPnLUnmappedRow` breakups.
8. Copy the totals dict onto `DailyPnL` fields (`gross_sales`, `net_profit`, percents, …).
9. Snapshot `electricity_rate` and the period window.
10. Status `SUBMITTED`, `submitted_at`, `submitted_by`. Save with `_allow_submit`.

After this, editing settings, settling another order, or changing a material rate **does not** change this document. `test_settings_change_does_not_alter_submitted` is the lock.

Cancel does **not** delete those rows. Amend does **not** copy lines — only inputs — so the new draft's submit recomputes.

---

## 16. Pages and URLs

Prefix: `/backoffice/reports/` (`spicy/urls.py`). App namespace `reports`.

| URL name | Path | What it does |
|---|---|---|
| `daily_pnl_list` | `/` | Register; `?status=&from=&to=` |
| `pnl_settings` | `settings/` | Config + material + expense formsets |
| `daily_pnl_create` | `daily-pnl/create/` | New draft |
| `daily_pnl_update` | `daily-pnl/<pk>/edit/` | Draft form + preview |
| `daily_pnl_detail` | `daily-pnl/<pk>/` | Statement + breakups |
| `daily_pnl_preview` | `daily-pnl/<pk>/preview/` | POST, HTMX, save then statement partial |
| `daily_pnl_submit` | `daily-pnl/<pk>/submit/` | POST, freeze |
| `daily_pnl_cancel` | `daily-pnl/<pk>/cancel/` | POST, status only |
| `daily_pnl_amend` | `daily-pnl/<pk>/amend/` | POST, new draft |
| `daily_pnl_material_add` / `_remove` | `.../materials/...` | HTMX formset rows |
| `daily_pnl_adhoc_add` / `_remove` | `.../adhoc/...` | HTMX formset rows |

---

## 17. Models (what is stored)

### Settings — `apps/reports/models.py`

- `PnLConfiguration` — singleton.
- `PnLMaterial` — catalog.
- `PnLRecurringExpense` — templates.

### Document — `apps/reports/pnl_models.py`

**`DailyPnL`** — one day.

- Identity: `business_date`, `period_start`, `period_end`, `status`.
- Draft inputs: electricity opening/closing, `employee_cost_override`, `remarks`.
- Frozen outputs: sales, COGS, consumption, directs, GP, employee, depreciation, variance, indirects, prime cost, net profit, plus matching `*_percent` fields.
- Audit: `submitted_at`, `submitted_by`, `amended_from`.

**Draft children** (editable only while DRAFT):

- `DailyPnLMaterialQty` — qty now; `rate`/`amount` filled on submit.
- `DailyPnLAdHoc` — one-off DIRECT/INDIRECT line.

**Snapshot children** (written on submit):

- `DailyPnLLine` — one statement row: section, label, food/drinks/total, % gross, memo flag, sort order, source (`COMPUTED` / `SETTINGS` / `METER` / `MATERIAL` / `ADHOC` / `VARIANCE`).
- `DailyPnLCogsRow` — drink item breakup (`SALE` / `RETURN` / `WASTAGE`).
- `DailyPnLConsumptionRow` — kitchen item breakup.

---

## 18. Important rules and edge cases

- **Drafts are live.** Preview after more sales land and the numbers move. Submit is the freeze.
- **Only submitted orders count.** A cart still in DRAFT is invisible (`test_draft_orders_ignored`).
- **Returns in the same window** reduce drinks sales (negative line amounts) and adjust COGS as in §14.
- **Multiple shift closes** in the window all contribute to cash variance. There is no link from a P&L to a particular close.
- **GP columns need not sum to GP total** when electricity, materials, round-off, or untagged templates sit on Total only.
- **Net profit is Total only.** The FOOD/DRINKS columns on the net-profit line are zero.
- **Kitchen consumption date ≠ sales window** when start hour is not midnight (see §9).
- **Submit needs a fiscal year** for `business_date` even though it posts no GL.
- **Employee override** is all-or-nothing: one line, no per-template split, Total column only.
- **Disabled materials** cannot be newly added; existing rows on an old draft remain.
- **Electricity line** is omitted when both readings are blank; it appears (possibly ₦0) if opening is present.

---

## 19. How this relates to accounting

```text
Order settlement
    ├─► GL: income, payment, rounding, drink COGS     (accounting)
    └─► stock: drink SLE at WAC                       (inventory)
                                                     ↘
Shift close ─► GL cash variance (if configured)       Daily P&L reads
Kitchen consumption rec ─► stock SLE                  these later
P&L settings / meter / materials                      ↗

Simple P&L report: query over GLEntry (see query-reports.md)
Daily P&L: snapshot document, restaurant-shaped, no posting
```

They can disagree, and that is allowed:

- Daily P&L includes typed costs (gas, meter, a day's rent) that may never have been journalled that way.
- Daily P&L kitchen consumption is a memo; GL may have warehouse/wastage postings from the reconciliation.
- Daily P&L drink COGS is rebuilt from SLEs of that day's orders; GL COGS is posted at settle/return time.

Use Daily P&L to run the restaurant. Use the GL and the simple P&L report to run the books.

---

## 20. File map

| File | Responsibility |
|---|---|
| `FEATURES.md` §A10 | Product definition |
| `PLAN.md` §4.6 | Phase 7 decisions (status: complete) |
| `apps/reports/models.py` | Settings models |
| `apps/reports/pnl_models.py` | Document + children |
| `apps/reports/sources.py` | Window and live queries |
| `apps/reports/services.py` | Compute + submit |
| `apps/reports/views.py` | Manager HTTP surface |
| `apps/reports/forms.py` | Forms and formsets |
| `apps/reports/urls.py` | Routes |
| `apps/reports/admin.py` | Admin, lines read-only inline |
| `apps/reports/tests/` | Window, sales, COGS, memos, templates, submit, views |
| `templates/backoffice/reports/` | UI |
| `docs/workflows/backoffice.md` | Short operations summary |
| `docs/architecture/apps.md` | App entry |
| `docs/architecture/data-model.md` | Entity summary |

Tests worth reading first: `apps/reports/tests/test_compute.py` (behavior) and `test_models.py` (guards).
