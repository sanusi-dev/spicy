# Execution Flow: Load Catalog

```text
Order screen or filter/search input
  -> templates/pos/partials/catalog/search.html, sidebar.html, panel.html
  -> GET pos:pos_order_screen (/pos/order/<pk>/)
  -> views_pos.pos_order_screen()
  -> _get_open_shift()
  -> query draft order belonging to active shift
  -> _build_order_context()
  -> Restaurant.load() and active Menu
  -> MenuItem + Item + ItemGroup query
  -> search/group/special filtering
  -> orders.services.drink_stock_available()
  -> catalog workspace or full POS surface HTML
  -> HTMX swaps #catalog-workspace or #pos-main
```

## Context Construction

`_build_order_context()` loads enabled active-menu lines, prefetches add-on relationships, builds group names, normalizes the requested group, and filters by menu line/item name/item code. It also loads order items, guest groups, active customer card, payment modes, and ticket state.

## Availability

`drink_stock_available()` annotates each in-memory menu line. FOOD lines remain available regardless of Bin quantity. DRINKS are disabled if they are not stock items, if Restaurant has no enabled Bar/POS warehouse, or if `actual_qty - reserved_qty <= 0`.

The catalog may still render a line whose underlying Item was directly made disabled/non-sales, because the context query only filters menu-line disabled state. The add POST performs the authoritative Item validation and returns a cart error.

## HTMX Behavior

- Search uses `hx-trigger="input changed delay:250ms, search"`, includes filter state, targets `#catalog-workspace`, and pushes the order URL.
- Category/special buttons use the same target and swap.
- Clear filters targets the workspace and pushes the base order URL.
- After cart add/update, `catalog_oob=True` returns `#catalog-grid` with `hx-swap-oob="outerHTML"` so DRINKS availability refreshes.
