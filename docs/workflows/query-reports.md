# Query Reports

Manager/Admin-only GET reports in `apps.reports`. No new models and no persistent aggregates. The Daily P&L document is a separate snapshot workflow — see [Daily P&L](daily-pnl.md).

## Access and navigation

`@manager_required` on every view (`report_views.py`). The Reports sidebar has a Sales section and an Accounting section. The backoffice dashboard Reports card links Today, POS register, trial balance, and simple P&L.

Prefix: `/backoffice/reports/` (`reports` namespace).

## Sales period

Sales reports filter `Order.posting_date` as a calendar date. Today is `posting_date = today`. The Daily P&L business-day window does not apply; a late-night ticket can land on different days on the two surfaces.

Source is `Order.status=SUBMITTED` only. `DRAFT`, `CANCELLED`, and `DISCARDED` never count as sales. Returns (`is_return=True`) net off on the return's own `posting_date` as negative `grand_total` / `OrderItem.amount`. Net = gross (item amounts) + `rounding_adjustment` (equivalently `Sum(rounded_total)`). Refunded is the absolute value of return `grand_total`.

Department columns come from `OrderItem.department` (FOOD / DRINKS — DB-constrained, so the split can never lose a line to a missing department).

## Sales reports

| Report | URL name | Rows | Filters |
|---|---|---|---|
| Today | `sales_today` | One per posting date (defaults from=to=today) | from, to |
| Daywise | `sales_daywise` | One per posting date | from, to |
| Monthwise | `sales_monthwise` | One per calendar month | fiscal year **or** from/to |
| Item-wise | `sales_itemwise` | One per item: qty, gross, refunded, net | from, to, department, item group |
| Employee-wise | `sales_employeewise` | One per cashier (blank when unset) | from, to |
| Service-wise | `sales_servicewise` | One each for Dine-in and Take-away | from, to |
| Time-wise | `sales_timewise` | 24 hourly buckets from `posting_time` | from, to |
| Cancelled invoices | `sales_cancelled` | One per `CANCELLED` order (returns excluded) | from, to, reason |
| Average bill | `sales_average_bill` | Net / bill count per day or month, plus overall | from, to, grouping |
| POS register | `pos_register` | One per submitted `POSClosingEntry` | from, to, cashier |

Queries live in `apps/reports/sales_reports.py` (period, average bill, cancelled) and `apps/reports/sales_breakdown_reports.py` (item, employee, service, time). Every page is a filter form + table + totals row. Period, average-bill, and service rows link to the order register with status and date filters; cancelled invoice rows link to order detail. Register rows link to closing detail and expand to stored `ClosingPayment` expected/counted/difference (netting is displayed, not recomputed). No charts.

Monthwise is fiscal year **or** from/to: custom dates win and clear the year selection, while choosing a year replaces stale dates with that year's bounds. On the GL and P&L reports a selected fiscal year clamps from/to into the year; dates that fall outside both bounds reset the report to the full year.

## Accounting reports

| Report | URL name | Behaviour |
|---|---|---|
| General ledger | `gl_report` | Chronological `GLEntry` rows with debit, credit, running balance, voucher link, `is_cancelled` flag. Cancelled originals and reversals both display and net to zero. Filters: fiscal year, from/to, account. Running balance appears only when an account is selected, seeded by a brought-forward row from earlier entries in the same fiscal year. |
| Trial balance | `trial_balance` | Cumulative `posting_date <= to` within the fiscal year, opening entries included. One row per leaf account with a non-zero balance, grouped by `account_type`. Debit total equals credit total. |
| Simple P&L | `simple_pnl` | Sums `report_type=PROFIT_AND_LOSS` entries by account. Food vs drinks sales split using production-unit income accounts, falling back to the restaurant default income account when exactly one department has no unit account. Gross profit = total income. Net profit = income − expenses. Cancelled + reversals netted. No typed costs and no memos. |

Queries live in `apps/reports/accounting_reports.py`. GL voucher links resolve Order, Journal Entry, Supplier Invoice, Supplier Payment, Purchase Receipt, Stock Entry, Stock Reconciliation, and Shift Cash-Out (to the related opening entry detail). There is no balance sheet.

This GL report is separate from the accounting GL *register* (`accounting:gl_entry_list`), which is a newest-first document list with CSV export.

## Tests

- `apps/reports/tests/test_sales_reports.py`
- `apps/reports/tests/test_pos_register.py`
- `apps/reports/tests/test_accounting_reports.py`
- `apps/reports/tests/test_report_filters.py`
