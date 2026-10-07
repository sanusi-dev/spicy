# Alpine.js Architecture

## Startup and Components

`assets/javascript/alpine.js` exposes Alpine globally and starts it on `DOMContentLoaded`. `assets/javascript/order-details-drawer.js` registers `orderDetailsDrawer` and `posModalDialog` during `alpine:init`.

## POS Local State

- `pos/base.html`: user-menu open state and Ctrl/Cmd+K focus to catalog search.
- `gates/no_shift.html`: opening form visibility, processing flag, and client-side opening total.
- `close_shift.html`: counted/expected per-mode preview and total variance.
- `payment/dialog.html`: entered payment total, processing state, remaining amount/change preview, and focus-managed modal.
- `cart/totals.html`: action menu, clear confirmation, cancel dialog, delete dialog, and processing state.
- `catalog/add_on_dialog.html`: selected add-on IDs, focus management, and removal on close.
- `order_history.html`: selected order ID for row highlighting.

## Drawer and Modal Lifecycle

`orderDetailsDrawer` optionally animates in after two animation frames. It focuses the close button, traps Tab focus, and handles Escape/backdrop close. It then dispatches `order-details-closed`, removes its root node, and restores focus to the history row. `posModalDialog` records previous focus, focuses the first control, and either navigates to a close URL or removes the dialog root.

These components do not fetch data or enforce permissions. HTMX provides the server request. Alpine only controls local presentation and focus.

## Alpine/HTMX Risk

HTMX replaces DOM nodes containing Alpine state. Any state that must survive a swap is stored in the server session or database. It never lives in Alpine. Customer active-card state is server session data. Guest count and item customer indices are database data. If a new fragment omits an Alpine root or target ID, the visible UI may become stale without changing server state.

## Server Values in Alpine Expressions

Alpine runs attribute text as JavaScript. Server values interpolated into JS string literals therefore use `|escapejs`: `Number('{{ value|escapejs }}')` in `payment/dialog.html`, `close_shift.html`, and `shift/cash_out_dialog.html`. Django's default HTML escaping is not sufficient here, because the browser decodes HTML entities while reading the attribute, before Alpine parses the text. `escapejs` leaves ordinary numbers unchanged (`1500.00` stays `1500.00`). A non-numeric value degrades to a `NaN` preview instead of a broken component. The id interpolations in `order_history.html` and `catalog/add_on_dialog.html` are server-generated pks and are not escaped.

## Other Frontend Code

`floor-plan.js` registers a table layout editor and posts to a route/model not present in the current Django tree. Treat it as orphaned-looking code, and do not use it as evidence of an active floor-plan workflow.
