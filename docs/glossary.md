# Glossary

| Term | Meaning in the current code |
|---|---|
| Restaurant | Singleton `settings.Restaurant` configuration row for identity, active menu, warehouses, and POS limits. |
| Production Unit | `settings.ProductionUnit` station for FOOD or DRINKS with a warehouse and printer metadata. |
| Item | Inventory master record used for both sellable products and internal stock. |
| Stock UOM | `Item.stock_uom` — the countable unit for bins, the ledger, counts, and POS (Bottle, Kg, Plate). |
| UOM conversion | `ItemUOMConversion` row mapping one bulk purchase unit to the stock UOM (e.g. 1 Crate = 24 Bottle). Converted once on purchase-receipt submit. |
| Menu | Named enabled collection of priced `MenuItem` rows. |
| Menu Item | A priced link between a Menu and an inventory Item. Its rate is the POS selling price. |
| Add-on | Separate sellable Item linked through `ItemAddOn` and added as its own order line. |
| Variant | Inventory Item linked to a parent through `ItemVariant`. Modeled but not selected through the current POS UI. |
| Department | `FOOD` or `DRINKS`. Controls production routing and the DRINKS-only POS stock policy. |
| Warehouse | Stock location. Its operational role comes from Restaurant/ProductionUnit references. There is no type field. |
| Bin | Current item/warehouse snapshot of actual, reserved, and valued stock. |
| Stock Ledger Entry / SLE | Signed immutable-by-convention PWAC movement row (`quantity`, `unit_rate`, `stock_value_change`). |
| Stock Entry | Draft document for Material Receipt or Store-to-production transfer. |
| Stock Reconciliation | Draft adjustment document with a structured reason (`OPENING_STOCK`, `ADJUSTMENT`, `CONSUMPTION`, `WASTE_DAMAGE`). |
| Purchase Receipt | Draft supplier receipt into the central Store warehouse. |
| Shift | The globally shared `POSOpeningEntry` period, closed by `POSClosingEntry`. |
| Opening Payment | Payment-mode opening balance for a shift. |
| Closing Payment | Payment-mode counted, expected, and variance values at shift close. |
| Mode of Payment | Configurable payment method master, such as Cash or Bank. |
| GL Mapping | `PaymentGLMapping` account-name mapping required for settlement. |
| Order | Operational POS document containing lines, payments, status, totals, and audit events. |
| Draft | Editable order state before settlement, a KOT, cancellation, or deletion. |
| Order Item | Historical line snapshot with item, price, quantity, department, comments, and customer index. |
| Customer Card | Ephemeral POS session selection represented by an integer customer index. Not a customer model. |
| Guest Count | `Order.guest_count`, from 1 to 50, used to render customer groups. |
| Customer Index | `OrderItem.customer_index`, a 1-based persistent group tag copied to KOT lines. |
| KOT | Kitchen Order Ticket snapshot for FOOD or DRINKS station routing. |
| BOT | Bar ticket. Technically a `KOT` row with `ticket_type=bar`. |
| Ticket Print Status | `PENDING`, `PRINTED`, or `CANCELLED`. Independent from KOT lifecycle status. |
| Receipt Print | `Order.invoice_printed*` written by `settle_order()`. The actual print runs non-blockingly after settlement. |
| Settlement | `orders.services.settle_order()`, which validates payment/stock and submits an order atomically. |
| Return | Negative draft order linked to an original submitted paid order. `submit_return()` restores stock and mirrors refund rows. |
| Discard | Retained `DISCARDED` state for an empty untouched draft. Legacy seed data only. |
| Audit Event | Append-only `OrderAuditEvent` describing an order mutation or lifecycle event. |
| POS History | `services.order_history_rows()` query and its cashier-facing filtered display. |
| Daily P&L | Submitted management snapshot for one business day (`reports.DailyPnL`). Not a GL report and not a GL posting. |
| Query reports | Filter + table reports in `apps.reports` over submitted orders, submitted shift closes, and `GLEntry`. Calendar posting date. No stored aggregates. |
| Trial balance | Leaf-account debit/credit/balance to an as-of date within a fiscal year, including opening entries. Zero-balance accounts omitted. |
| Simple P&L | Income minus expense from `GLEntry` rows with `report_type=PROFIT_AND_LOSS`. Distinct from the Daily P&L snapshot. |
| Recipe | Ingredient card (`inventory.Recipe`) for one sellable FOOD item. One active card per dish, qtys in ingredient `stock_uom`. |
| Theoretical usage | Recipe × submitted FOOD sales for the day (returns netted). Memo beside actual food cost. |
| Actual usage | Kitchen `CONSUMPTION` + `WASTE_DAMAGE` SLEs on the business date. FOOD COGS on the P&L. |
| Business-day window | `[business_date + start_hour, next day + start_hour)` used to pick orders and shift closes for a Daily P&L. |
| Kitchen consumption (P&L) | Actual food-usage breakup (`CONSUMPTION` + `WASTE` rows) backing FOOD COGS. Kept as a stored field and row set, no longer a statement line. |
| Prime cost (P&L) | Memo line: drink COGS + employee costs. Not subtracted again at net profit. |
| P&L material | Catalog consumable (`PnLMaterial`) typed as a quantity on the day's draft (e.g. cooking gas). It is not an inventory item. |
| Full History | Restaurant-controlled access to returns, cancelled, discarded, and all status filters. |
