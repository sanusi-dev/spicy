# Spicy Documentation

This directory is the living technical map of the current Spicy source tree. It describes the implementation that exists in `apps/`, `templates/`, `assets/`, and `spicy/`. It is not a restatement of the intended product scope.

## Read This First

| If you want to... | Read |
|---|---|
| Understand the runtime architecture | [Architecture overview](architecture/overview.md) |
| See what each app owns | [App responsibilities](architecture/apps.md) |
| Trace app-to-app coupling | [Dependencies](architecture/dependencies.md) |
| Understand the database entities | [Data model](architecture/data-model.md) |
| Follow status transitions | [State machines](architecture/state-machines.md) |
| Understand the cashier POS | [POS workflow](workflows/pos.md) |
| Understand Daily P&L | [Daily P&L](workflows/daily-pnl.md) |
| Understand sales and ledger reports | [Query reports](workflows/query-reports.md) |
| Trace an action end to end | [Execution flows](#execution-flows) |
| Debug an unexpected result | [Troubleshooting](debugging/troubleshooting.md) |

## Architecture

- [Overview](architecture/overview.md): project layout, request lifecycle, infrastructure, and system boundaries.
- [Apps](architecture/apps.md): responsibility, models, URLs, forms, templates, and side effects for every project app.
- [Dependencies](architecture/dependencies.md): direct imports, model relationships, service calls, and strong coupling.
- [Data model](architecture/data-model.md): major entities, relationships, constraints, and lifecycle ownership.
- [State machines](architecture/state-machines.md): actual order, ticket, shift, and inventory states and transitions.
- [Hidden side effects](architecture/side-effects.md): signals, model overrides, middleware, context processors, and frontend events.

## Workflows

- [POS](workflows/pos.md): cashier-facing surface, shift gate, catalog, cart, tickets, payment, and history.
- [Shifts](workflows/shifts.md): opening balances, active shift rules, closing reconciliation, and cancellation.
- [Orders](workflows/orders.md): draft lifecycle, guest grouping, tickets, stage exits (delete/cancel/return), returns and refund submission, and audit events.
- [Inventory](workflows/inventory.md): ledger, PWAC valuation, reservations, receipts, transfers, reconciliation, and every mutation path.
- [Products and menu](workflows/products-and-menu.md): item flags, menus, prices, add-ons, variants, and POS catalog resolution.
- [Payments](workflows/payments.md): payment master data, GL mappings, order payment rows, settlement validation, and cash change.
- [Backoffice](workflows/backoffice.md): manager/owner pages and their create, submit, cancel, and filtering workflows.
- [Daily P&L](workflows/daily-pnl.md): the management snapshot for one business day — idea, statement, sources, workflow, and compute pipeline.
- [Query reports](workflows/query-reports.md): sales, POS register, general ledger, trial balance, and simple P&L.
- [Authentication and access](workflows/auth.md): allauth, roles, middleware gates, and view-level permission checks.
- [Receipts and printing](workflows/receipts-and-printing.md): settlement receipt print, KOT/BOT dispatch, retry behavior, and the current printer stub.

## Execution Flows

These pages answer one question: when I perform this action, what happens next? Each page names the frontend trigger, URL, view, service/model path, and response fragment. It also lists database effects and side effects.

- [Open shift](execution-flows/open-shift.md)
- [Close shift](execution-flows/close-shift.md)
- [Load catalog](execution-flows/load-catalog.md)
- [Create order](execution-flows/create-order.md)
- [Add item to cart](execution-flows/add-to-cart.md)
- [Cart mutations](execution-flows/cart-mutations.md)
- [Submit order](execution-flows/submit-order.md)
- [Make payment](execution-flows/payment.md)
- [Print receipt](execution-flows/print-receipt.md)
- [Order history and details](execution-flows/order-history.md)
- [Backoffice order operations](execution-flows/backoffice-orders.md)

## Frontend and Database

- [Frontend overview](frontend/overview.md): template inheritance, Vite entries, and server-rendered surfaces.
- [HTMX](frontend/htmx.md): request targets, swaps, URL history, partials, out-of-band updates, and message events.
- [Alpine.js](frontend/alpine.md): local state, dialogs, focus management, and client-side previews.
- [Transactions](database/transactions.md): atomic boundaries, row locks, rollback behavior, and concurrency risks.
- [Queries](database/queries.md): important `select_related`, `prefetch_related`, aggregations, annotations, and filters.

## Debugging and Vocabulary

- [Troubleshooting](debugging/troubleshooting.md): symptom-to-code tracing guide.
- [Glossary](glossary.md): project domain terms and their actual model meanings.

## Operations

- [Backup and restore](ops/backup-restore.md): nightly database + media snapshots, cron schedule, off-machine copies, and the restore drill.

## Plans and Reviews

Phase 9 (printing) is the only unbuilt phase. Its detailed decisions live once Phase 9 starts
(see `PLAN.md` §4.8). Completed-phase plans are retired in place: PWAC costing (§4.9),
supplier payables (§4.1), UOM conversion (§4.10), and reconciliation (§4.11). Current product
facts live in `FEATURES.md`. The archive keeps the retired plan history verbatim.

## Archive

- [Implementation plan history](archive/PLAN-history.md): implemented phases and decisions, retained verbatim.

## Documentation Rules

Read the relevant page before changing complex behavior. Update the relevant page in the same task when code changes architecture, dependencies, models, state transitions, side effects, or user-visible workflows. Keep this index synchronized when you add or remove documentation. Describe current behavior, and mark unresolved behavior with `⚠️ Requires verification`.
