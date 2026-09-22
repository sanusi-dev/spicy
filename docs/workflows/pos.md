# POS Workflow

## Surface and Access

The active cashier surface is `apps/orders/views_pos.py`, mounted by `apps/orders/pos_urls.py` at `/pos/`. Views are gated by `@staff_required` in `apps/users/decorators.py` — any Spicy role (Cashier included) passes; others get 403. The POS shell is `templates/pos/base.html`; `#pos-main` is the main HTMX target.

```mermaid
flowchart LR
    Gate[Restaurant and active-shift gate]
    Drafts[Draft orders]
    Order[Catalog and cart]
    Payment[Payment dialog]
    History[History and detail drawer]
    Gate --> Drafts --> Order --> Payment
    Order --> History
    Payment --> Drafts
```

## Shift Gate

`pos_home()` first calls `Restaurant.load()`. Missing settings produce the error gate. With settings, `_get_open_shift()` looks for `POSOpeningEntry.status=SUBMITTED` with no closing link. If none exists, the no-shift partial renders enabled payment modes and the opening form. Every order endpoint repeats the active-shift check; the UI gate is not the security boundary.

## Draft Orders

The home surface lists `Order.objects.open_drafts_for(shift, user)`, enriched by `services.open_draft_orders()` with ticket status, item preview, and age: a cashier sees only their own drafts (plus legacy rows with no creator), while Manager/Admin see every draft on the shift. Every POS draft screen and mutation view uses the same filter, so a non-owner gets a 404 even with a direct URL. `Restaurant.max_open_drafts` is enforced against all shift drafts under a Restaurant and shift row lock when `create_draft_order()` runs, so the cap cannot be dodged by ownership. The session stores the current order primary key and per-order active customer card.

## Catalog

`_build_order_context()` resolves `Restaurant.active_menu`, filters enabled `MenuItem` rows, groups them by `ItemGroup`, and applies search/group/special filters. Menu lines that are size variants (`menu.ItemVariant`) collapse into one parent card showing the parent name and the `₦min – ₦max` range; lines without a variant row stay flat. DRINKS receive availability annotations from `drink_stock_available()`; FOOD is not stock-gated. A parent card is unavailable only when all its variants are; search matches parent names, variant names, and item codes. Search and filter controls request `pos_order_screen` and replace `#catalog-workspace`.

## Cart

Menu buttons either POST directly to `pos_order_add_item` or GET the add-on dialog; parent variant cards GET the variant dialog (`pos_order_variant_dialog`), which submits the chosen size to the same add-item endpoint with `variant_item_id` and chains into the add-on dialog when the size has add-ons. Cart quantity controls POST to `pos_order_update_item`; guest/order-type controls POST to `pos_order_update_meta`. The server locks order edits after a KOT is created, even if a stale browser sends a request. Cart responses replace `#cart-panel`, and successful mutations can refresh `#catalog-grid` out of band.

## Send, Pay, Cancel, Discard

- Send to kitchen creates department-specific immutable KOT/BOT snapshots and dispatches each ticket independently.
- Pay loads a dialog, then settlement validates full payment, shift ownership, payment modes, required electronic references, current item availability, and stock before submitting the order.
- Cancel is available for sent/printed unpaid drafts; it preserves the order and sends cancellation tickets.
- Discard is only for an empty untouched draft.
- Receipt printing claims the order as printed before calling the print interface, which intentionally locks further draft edits.

## History

`pos_order_history()` defaults to current-date submitted paid non-return sales. Manager/admin/superuser users, or any user when `Restaurant.pos_allow_full_history=True`, can request all/returns/cancelled/discarded filters. Rows load `pos_order_history_detail()` into `#order-details-drawer`; Alpine controls focus and animation, not business state. Detail and receipt reprint enforce the same visibility: without full history, only submitted paid non-return orders resolve (others 404); with full history, cancelled and discarded orders are viewable and any submitted receipt is reprintable.

## Important Current Gaps

- There is no customer master or customer selection workflow; `customer_name` remains a text field and guest cards are order-local indices.
- Settlement auto-prints the receipt after commit; a printer failure warns but never blocks the sale.
- Return drafts are created and submitted in backoffice; submission restores drink stock and mirrors refund rows.
- Printing is simulated by `apps/orders/printing.py`.
- The payment dialog may show an enabled mapped mode that was not declared at shift opening; settlement rejects it server-side.
