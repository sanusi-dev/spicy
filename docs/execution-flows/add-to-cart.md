# Execution Flow: Add Item to Cart

## Direct Menu Item

```text
Menu card click
  -> templates/pos/partials/catalog/grid.html
  -> hx-post pos:pos_order_add_item
  -> views_pos.pos_order_add_item()
  -> lock active draft order
  -> parse item, quantity, comments, active customer card
  -> orders.services.apply_add_on_line()
  -> resolve active menu price
  -> orders.services.add_order_line()
  -> validate Item and customer index
  -> reserve DRINKS Bin quantity if applicable
  -> merge identical line or create OrderItem
  -> recalculate totals
  -> Order audit ITEM_ADDED
  -> return #cart-panel and optional catalog OOB grid
```

The view first resolves an enabled sales Item. `add_order_line()` snapshots item name, department, stock flag, menu line, rate, customer index, and comments. A line merges only when item, customer index, and comments match.

## Add-on Item

Items with configured add-ons GET `pos_order_add_on_dialog`, which resolves active-menu prices and filters invalid add-ons. The dialog submits the same add-item endpoint with `add_on_ids`. `apply_add_on_line()` validates all IDs and runs parent plus add-on line creation in one atomic block. A failure rolls back the whole group.

## Variant Item

Tapping a grouped dish GETs `pos_order_variant_dialog`, which lists the sellable on-menu sizes ordered by rate with per-size stock state. Submitting posts the parent `item_id` plus the chosen `variant_item_id`. The price resolves server-side from the variant's menu line, and the audit row carries both IDs. When the chosen size itself has add-ons, the submit returns the add-on dialog for that size, preserving qty/comments, instead of adding the line. This creates a two-step variant → add-ons → cart chain.

## Stock Effects

DRINKS reservation changes happen before the service creates/updates the line, and they use locked Bin rows. FOOD bypasses stock. Missing/disabled warehouse or insufficient unreserved stock returns a validation error in the cart without creating the line.

## Frontend Response

`#cart-panel` is replaced with `outerHTML`. On success, `HX-Trigger: close-add-on-dialog` closes the Alpine dialog. `catalog_oob` replaces `#catalog-grid` outside the primary target.
