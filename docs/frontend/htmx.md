# HTMX Architecture

## Global Setup

`templates/web/base.html` sets `hx-headers` with the CSRF token. `assets/javascript/htmx.js` exposes the imported HTMX module on `window.htmx`. `django_htmx.middleware.HtmxMiddleware` makes `request.htmx` available to views.

## Response Selection

POS views use `_is_htmx()` and `_render_pos_surface()` in `apps/orders/views_pos.py`. For HTMX requests, a template fragment is selected with Django's inline partial syntax, for example `pos/index.html#surface`, `#cart`, `#catalog_workspace`, or `order_history_detail.html#drawer`.

The same URL supports normal progressive enhancement: non-HTMX requests receive a redirect or full page, while HTMX requests receive the smallest surface needed for the target.

## Important Attributes

- `hx-target="#pos-main"` swaps the main POS surface.
- `hx-target="#cart-panel" hx-swap="outerHTML"` refreshes the entire cart after a mutation.
- `hx-target="#catalog-workspace" hx-swap="outerHTML"` refreshes only catalog results.
- `hx-push-url="true"` keeps search/filter/history navigation addressable.
- `hx-push-url="false"` keeps history drawers and print refreshes from changing the URL.
- `hx-include` carries catalog/filter form state into add/cart requests.
- `hx-vals` carries item IDs, guest deltas, order type, or action values without a separate form.
- `hx-swap-oob="outerHTML"` refreshes the catalog grid or shell navigation outside the primary target.
- `hx-trigger="input changed delay:250ms, search"` debounces catalog search.

## Events and Messages

`pos_order_add_item()` sets `HX-Trigger: close-add-on-dialog` on success. `MessagesMiddleware` merges Django messages into `HX-Trigger.showMessages`; `toast.js` listens for that event. Existing trigger values are parsed as JSON and preserved.

### Redirects and toasts

A 3xx redirect's response headers are dropped when the browser follows it, so an `HX-Trigger` toast attached to a redirect is never seen. For HTMX requests that redirect with queued messages (the submit/cancel views), `MessagesMiddleware` instead sets `HX-Redirect`, forcing a full page navigation. The messages persist in Django's message storage and render as toasts via the destination page's `#django-messages` block. Non-HTMX requests keep the plain redirect.

### Global error state

Both shells surface HTMX failures that have no per-action banner. `templates/web/app/app_base.html` reveals `#htmx-error` on `htmx:responseError`; `templates/pos/base.html` reveals its own `#htmx-error` on `htmx:responseError` and `htmx:sendError` (an unreachable server is the common POS failure) and hides it again after the next successful request. Dismissing the banner is manual. Per-action banners in the cart still arrive as 200 responses carrying `error` in the context.

## Formset Partials

Inventory item add/remove endpoints receive the full form POST, rebuild contiguous management-form indices using `inventory.forms.add_formset_row()` or `remove_formset_row()`, and return a fragment from the same form template. They do not save rows until the parent form is submitted. Item UOM conversions use the same pattern (`inventory:item_uom_add` / `item_uom_remove`). Purchase-receipt lines `hx-get` `inventory:purchase_receipt_item_meta` (replace the UOM widget when the item changes) and `inventory:purchase_receipt_stock_qty_preview` (show e.g. `5 Crate = 120 Bottle`).

## HTMX Debugging

Inspect the request's `HX-Request`, `HX-Target`, and `HX-Current-URL` headers, then compare the view branch with the target ID in the template. A correct fragment rendered into the wrong target is a frontend synchronization bug, not a model bug. For a missing toast, inspect the response `HX-Trigger` header and `MessagesMiddleware`.
