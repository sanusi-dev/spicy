# Spicy — Agent Guidelines

## Project Overview

Spicy is a restaurant POS and management system for a Nigerian restaurant, built with Django + HTMX.

**Client context:**
- Three thermal printers: cashier receipt, kitchen ticket, bar ticket
- Bar is a separate business entity sharing the same cashier — sales tracked separately per department (food vs drinks)

**Build philosophy:**
- `FEATURES.md` is the product specification; `PLAN.md` is the roadmap plus decision-only
  detailed plans for unbuilt work; `docs/` describes the implemented system
- The project is NOT a replication of URY or ERPNext — `references/` is reference material only
- For each new feature, follow the workflow in the "PLAN.md and FEATURES.md Protocol" section:
  review comparable systems, research industry practice, propose a plan, iterate with the
  developer, and record only the final agreed plan

**Never agree with the user's claims based on confidence alone.** Verify against
`FEATURES.md`, `PLAN.md`, `docs/`, `references/`, and the code. Push back when mistaken — even
if the user says you previously agreed. If the user explicitly says "I know this isn't best
practice but I want it anyway", respect the decision, proceed, and record the deviation in the
feature's detailed plan in `PLAN.md`.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Django (latest stable), Python 3.14 |
| Frontend | Django templates + HTMX + Tailwind CSS v4 + Alpine.js + SweetAlerts |
| Database | PostgreSQL |
| Package manager | uv (pyproject.toml) — never use bare `pip install` |
| JS build | Vite + django-vite (sources in `/assets/`, loaded via `{% vite_asset %}`) |
| Task queue | Celery + Redis (periodic background tasks) |
| Cache / broker | Redis |
| Thermal printing | Local Python print agent (ESC/POS over LAN) |
| Auth | django-allauth |

The system runs on the local network only. Django runs on the cashier desktop. All operations work on the local network — no internet required. Owner accesses back office from any device on the same WiFi.

**Explicitly NOT used:** Django REST Framework, Vue, React, Socket.io, QZ Tray, DaisyUI. Do not introduce any of these.

## Workspace Structure

```text
Spicy/
├── AGENTS.md                         ← this file
├── PLAN.md                           ← build sequence roadmap + decision-only detailed plans
├── FEATURES.md                       ← product feature specification (what the system does)
├── references/                       ← READ ONLY — never modify (gitignored; clone with:
│   │                                  git clone --depth 1 --branch develop https://github.com/frappe/erpnext.git references/erpnext-develop
│   │                                  git clone --depth 1 --branch develop https://github.com/ury-erp/ury.git references/ury-develop)
│   ├── erpnext-develop/              ← ERPNext source (develop branch — the repo's default; there is no main)
│   └── ury-develop/                  ← URY source (develop branch)
├── .venv/                            ← virtual environment
├── pyproject.toml                    ← dependencies (uv)
├── manage.py
├── apps/                             ← Django apps
│   ├── utils/           ← BaseModel, styled forms
│   ├── users/           ← CustomUser, roles
│   ├── settings/        ← restaurant settings (singleton), production units, printer config
│   ├── inventory/       ← stock ledger, items, warehouses, movements, reconciliations
│   ├── menu/            ← menu items, categories, pricing, variants, add-ons
│   ├── payments/        ← payment modes, GL mappings
│   ├── staff/           ← shifts, opening/closing entries, cashier sessions
│   ├── orders/          ← orders, order items, customer cards, KOT dispatch, returns
│   ├── accounting/      ← chart of accounts, GL, journals, fiscal years, payables
│   ├── reports/         ← Daily P&L document and P&L settings
│   └── web/             ← home, backoffice dashboard, middleware, context processors
│   (planned: printing — deferred: customers, coupons)
├── spicy/                            ← project package (settings.py, urls.py, celery.py, wsgi.py)
└── templates/
    ├── pos/            ← cashier-facing POS screen
    └── backoffice/     ← manager/owner back office
```

App responsibilities and build order are in `PLAN.md §2`.

## Commands

A `Makefile` centralises commands. Run `make` to list them.

| Task | Command |
|---|---|
| First-time setup | `make init` |
| Start app (foreground) | `make dev` |
| Start background services | `make start-bg` (or `make start` for foreground) |
| Stop services | `make stop` |
| Django shell | `make shell` |
| Postgres shell | `make dbshell` |
| Run management command | `make manage ARGS='command'` |
| Create migrations | `make migrations` |
| Apply migrations | `make migrate` |
| Run all tests | `make test` |
| Run specific test | `make test ARGS='apps.module.tests.test_file'` |
| Format code | `make ruff-format` |
| Lint + autofix | `make ruff-lint` |
| Both | `make ruff` |
| Add package | `make uv add '<package>'` |
| Run Python cmd | `make uv run '<command> <args>'` |
| Install npm packages | `make npm-install` (or `make npm-install package-name`) |
| Uninstall npm package | `make npm-uninstall package-name` |
| Vite dev server | `make npm-dev` (auto-runs with `make dev`) |
| Build for prod | `make npm-build` |
| TypeScript check | `make npm-type-check` |
| New Django app | `make uv run 'pegasus startapp <app_name> [<Model1> <Model2Name>]'` |

App runs at http://localhost:8000

**Vite dev vs build:** while the Vite dev server (`make npm-dev`) is running, it serves assets directly with HMR — no `make npm-build` needed. Only run `make npm-build` when serving without the dev server (django-vite falls back to the built manifest in `static/`), e.g. after a fresh deploy/restart.

**Important:** Never run `npm build` as part of normal development while the Vite dev server is running. Use the dev server's HMR assets; build only for production or when Django must serve compiled assets without Vite.

## Reference Codebase Navigation

`references/` is READ ONLY: never write, edit, create, delete, or import from it. Use it as
reference material when planning a feature — see the workflow in "PLAN.md and FEATURES.md
Protocol". Never copy architecture from it blindly; weigh it against current industry
practice and the developer's requirements.

**Step 1 — Find the ERPNext doctype:**
- `references/erpnext-develop/erpnext/accounts/doctype/` — financial documents
- `references/erpnext-develop/erpnext/stock/doctype/` — inventory
- `references/erpnext-develop/erpnext/selling/doctype/` — sales

Read the `.json` (the `fields` array is the data model) and the `.py` (business logic:
`validate`, `before_insert`, `on_submit`).

**Step 2 — Find the URY adaptation:**
- `references/ury-develop/ury/ury/doctype/` — URY custom doctypes
- `references/ury-develop/ury/ury_pos/api.py` — POS API (read before planning any POS work)
- `references/ury-develop/ury/ury/hooks/` — document event handlers
- `references/ury-develop/pos/src/` — React POS frontend (UI logic reference)
- `references/ury-develop/mosaic/src/` — kitchen display (reference only)

### Key Reference Files (read first)

| File | Why |
|---|---|
| `references/ury-develop/ury/ury_pos/api.py` | Complete POS API — every endpoint the frontend needs |
| `references/erpnext-develop/erpnext/stock/doctype/stock_ledger_entry/stock_ledger_entry.json` | Core inventory model |
| `references/erpnext-develop/erpnext/accounts/doctype/pos_invoice/pos_invoice.json` | Order/invoice model |
| `references/erpnext-develop/erpnext/accounts/doctype/payment_entry/payment_entry.json` | Payment model |
| `references/ury-develop/ury/ury/doctype/ury_order/ury_order.json` | URY order model |
| `references/ury-develop/ury/ury/doctype/ury_kot/ury_kot.json` | Kitchen ticket model |
| `references/ury-develop/ury/ury/doctype/ury_printer_settings/ury_printer_settings.json` | Printer config model |
| `references/ury-develop/ury/ury/hooks/ury_pos_invoice.py` | Order event logic |

# Communication, Explanation, and Response Structure

The agent must prioritize **clarity, structure, and human comprehension** in every response. The goal is not merely to provide correct information, but to make the information easy to understand and follow.

## 1. Do not expose the investigation process

Do not narrate routine investigation steps such as:

* "Let me verify this..."
* "Let me check..."
* "I'll look at..."
* "Let me double-check..."
* "I found..."
* "I searched..."
* "I need to inspect..."
* "One more thing I want to verify..."

Do the investigation internally and present the result.

Only mention investigation details when they are directly relevant to explaining the conclusion.

Bad:

> Let me verify this against the code rather than assume.
> Let me see the reconciliation path.
> Let me double-check whether food items are stock-tracked.

Better:

> **Yes, food items use FIFO.** However, their FIFO cost is handled differently from drinks in the P&L.

---

## 2. Lead with the answer

For questions about the system, start with the direct answer before providing supporting details.

Use this general structure:

**Answer → How it works → Why it works that way → Important nuance**

Do not make the user extract the answer from several paragraphs of investigation.

Example:

> **Yes. Food items use FIFO, but their FIFO cost is not treated as COGS.**
>
> **How it works**
>
> 1. Food is received into the Store.
> 2. The receipt creates a FIFO layer.
> 3. When food moves to the Kitchen, FIFO determines the transfer cost.
> 4. Kitchen consumption consumes that FIFO layer.
>
> **P&L treatment**
>
> * Drinks → FIFO cost becomes COGS.
> * Food → FIFO cost is reported as kitchen consumption rather than COGS.

---

## 3. Prefer flows over disconnected technical facts

When explaining how something works in the system, use a chronological or logical flow whenever possible.

Prefer:

> Purchase Receipt
> ↓
> FIFO Layer Created
> ↓
> Transfer to Kitchen
> ↓
> FIFO Layer Consumed
> ↓
> Consumption Recorded
> ↓
> P&L Memo

over a collection of unrelated implementation details.

For system behavior, think in terms of:

**Input → Process → State Change → Output**

Where useful, explicitly identify:

* What triggers the process
* What the system does
* What data changes
* What gets created
* What gets consumed
* What the user eventually sees

---

## 4. Explain technical concepts in plain language first

When explaining code or architecture, introduce the concept in simple terms before mentioning implementation details.

Bad:

> `StockLedgerEntry._create_entry_locked()` consumes FIFO batches and writes `outgoing_rate`.

Better:

> When stock leaves a warehouse, the system determines which received batch it came from using FIFO. The resulting cost is stored on the stock ledger entry as `outgoing_rate`.
>
> In code, this is handled by `StockLedgerEntry._create_entry_locked()`.

The implementation detail should support the explanation, not replace it.

---

## 5. Separate "what happens" from "where it happens"

When explaining a system behavior, distinguish between:

### What happens

The business/system behavior.

### Where it happens

The relevant model, service, function, query, or file.

This prevents implementation details from obscuring the actual behavior.

Example:

> **What happens:**
> Food transferred from Store to Kitchen carries its FIFO cost with it.
>
> **Where:**
> The transfer service consumes the Store FIFO layer and creates the Kitchen ledger entry using that outgoing rate.

---

## 6. Use concrete examples for concepts that are difficult to visualize

When a concept involves accounting, inventory, state transitions, relationships, or calculations, use a small concrete example when it improves understanding.

Example:

> Suppose the Store receives:
>
> | Receipt |   Qty |      Cost |
> | ------- | ----: | --------: |
> | Monday  | 10 kg | ₦1,000/kg |
> | Tuesday | 20 kg | ₦1,200/kg |
>
> If 15 kg is transferred to the Kitchen:
>
> * 10 kg comes from Monday → ₦10,000
> * 5 kg comes from Tuesday → ₦6,000
> * Total FIFO cost → ₦16,000
>
> The Kitchen therefore receives the stock with a FIFO cost of ₦16,000.

Do not create examples when they add no explanatory value.

---

## 7. Use progressive disclosure

Do not dump every discovered detail into the initial answer.

Present information in layers:

### Level 1 — Direct answer

The minimum information required to answer the question.

### Level 2 — Explanation

The flow or reasoning necessary to understand it.

### Level 3 — Technical detail

Relevant models, services, functions, queries, or implementation details.

### Level 4 — Edge cases

Only include these when they materially affect the answer.

Do not automatically include all four levels.

---

## 8. Distinguish facts, interpretation, and design rationale

When explaining the existing system, clearly separate:

* **Current behavior** — what the code actually does.
* **Design intent** — why the system was designed this way, if documented.
* **Inference** — your interpretation when the reason is not explicitly documented.

Never present an inferred reason as if it were documented system behavior.

Use wording such as:

> **Current behavior:** ...
>
> **Documented design intent:** ...
>
> **Likely reason:** ...

Only include the latter two when relevant.

---

## 9. Keep summaries and task explanations structured

When summarizing a task, feature, implementation, or existing behavior, avoid long unstructured paragraphs.

Use appropriate structures such as:

### Summary

One or two sentences.

### Current behavior

What currently happens.

### Required behavior

What should happen.

### Flow

The sequence of events.

### Key components

Relevant models/services/pages.

### Important rules

Business or technical constraints.

### Edge cases

Only important exceptions.

Do not force every section into every response. Use only the sections that improve comprehension.

---

## 10. Plans must describe execution clearly

When writing an implementation plan, organize it around **what will be built and in what sequence**, rather than reproducing scattered technical discoveries.

Each phase should make it immediately clear:

1. What is being built.
2. Why it is needed at that point.
3. Which application/component is involved.
4. What feature or behavior is delivered.
5. What the next phase depends on.

Avoid turning plans into implementation diaries.

---

## 11. Avoid unnecessary repetition

Do not restate the same conclusion in multiple forms.

If the answer is:

> Food uses FIFO, but food FIFO cost is not included in COGS.

Do not repeat the same conclusion in the introduction, explanation, P&L section, and final paragraph unless each repetition adds new information.

---

## 12. Match response depth to the question

Use the user's question to determine the appropriate depth.

### Simple factual question

Answer directly in a few sentences.

### "How does X work?"

Explain the flow from trigger to result.

### "Why does X work this way?"

Explain the behavior first, then the design rationale.

### "Explain X to me"

Use plain language, structure, and a concrete example when useful.

### Code/architecture question

Explain the system behavior first, then connect it to the implementation.

### Task summary

Provide a structured summary of the objective, behavior, affected components, and important constraints.

### Implementation plan

Use ordered phases and explicit dependencies.

Do not respond to a simple question with a full codebase audit unless the additional detail is necessary.

---

## 13. Prefer diagrams and compact tables when they improve understanding

For processes, state transitions, accounting flows, inventory flows, and architecture relationships, use simple textual diagrams or tables where appropriate.

Example:

```text
Purchase Receipt
      ↓
FIFO Layer
      ↓
Store Stock
      ↓
Transfer
      ↓
FIFO Cost Consumed
      ↓
Kitchen Stock
      ↓
Consumption
```

Use prose when prose is clearer. Do not add diagrams merely for decoration.

---

## 14. Do not add unnecessary closing offers

Do not end every answer with:

* "Want me to..."
* "Would you like me to..."
* "I can also..."
* "Let me know if you want..."

Only offer a follow-up when the next step is genuinely necessary or useful.

---

## 15. Default response principle

For every response, optimize in this order:

**Correctness → Clarity → Structure → Relevance → Brevity**

The response should make it possible for a human to understand the answer quickly without having to reconstruct the agent's investigation or reasoning process.

The agent should behave like a clear technical explainer, not an investigation transcript.


## PLAN.md and FEATURES.md Protocol

`FEATURES.md` — the product specification: what the system does, organised by back office and
POS surface. Planned and deferred features are marked as such.
`PLAN.md` — the build sequence roadmap plus decision-only detailed plans for unbuilt phases.

**New-feature workflow:**

1. Review how the reference material (`references/`) and other comparable systems approach
   the feature.
2. Research current industry practice.
3. Consider the model's knowledge and the developer's requirements.
4. Propose an implementation plan.
5. Review the proposal with the developer through an iterative discussion process.
6. Produce a final, agreed-upon implementation plan.
7. Add only the final plan to the documentation.

Before implementing:

1. Confirm the feature is in `FEATURES.md`.
2. Read the detailed plan in `PLAN.md` if one exists.
3. Implement exactly as the plan describes.
4. If the plan is missing or incomplete, stop and flag it — do not guess.

A `PLAN.md` detailed plan contains only finalized implementation decisions: model fields,
business rules, HTMX frontend behaviour, and tests. No reference commentary, no design
discussion, no history.

Keep `PLAN.md` and `FEATURES.md` synchronized in the same task whenever a change affects:

- Feature scope, including in-scope, deferred, or out-of-scope decisions
- Phase status, completed work, or remaining work in the build sequence
- Application architecture, cross-app dependencies, or implementation decisions
- Anything recorded in a detailed plan

Do not update `PLAN.md` for every minor code edit when none of these planning details change.
The `/docs` documentation rules below still apply whenever the implemented behavior changes.

## Documentation Maintenance

The documentation in `/docs` is a living representation of the current architecture and behavior of the project.

Before changing complex business logic, consult the relevant documentation in `/docs`. The main entry point is `docs/README.md`.

Whenever a code change affects any of the following, update the relevant documentation in the same task:

- Application architecture or cross-app dependencies
- Database models, relationships, constraints, or queries
- Business logic or side effects
- State transitions
- POS workflows, order lifecycle, inventory, payments, or shifts
- Receipt/printing behavior
- Authentication or authorization
- Signals, middleware, or other hidden execution
- HTMX/frontend/backend interactions
- Important execution flows

Update existing documentation instead of creating duplicate pages. Do not regenerate unrelated documentation. If a workflow changes, update its execution-flow page. If a model or relationship changes, update the data-model page. If an app dependency changes, update the dependency page. If a state transition changes, update the state-machine page. If functionality is removed, update or remove obsolete documentation.

Documentation must describe the current implementation, not the intended implementation. Do not silently guess behavior. If the implementation remains genuinely unclear after tracing callers, callees, templates, URLs, models, signals, and configuration, mark the page with `⚠️ Requires verification` and explain the uncertainty.

The documentation index in `docs/README.md` must remain accurate. Add new documentation to the index and do not leave orphan pages.

## Architecture Decisions

### Printing

Three Xprinter thermal printers on the local network, each with a fixed role:
- Cashier printer — customer receipt (USB to desktop)
- Kitchen printer — food order ticket (LAN)
- Bar printer — drinks order ticket (LAN)

A lightweight Python print agent runs as a background service on the cashier desktop. After an order is saved, Django sends a print job payload to the agent via HTTP on localhost. The agent formats ESC/POS commands and sends them to the correct printer IP. LAN printers are identified by static IP — not hostname or DHCP address.

### Customer card / group ordering

Group ordering is first-class. The cashier raises the guest count with the **Guests stepper** in the cart header (always visible) — flipping the cart between flat (single) and grouped (split) presentation at any time. In grouped view the cart renders one **customer group** per customer (Customer 1, Customer 2, …): a tappable header (tap to make it the active guest) listing that guest's items with a per-guest subtotal, then the order grand total once at the bottom. Menu taps always add to the active guest.

**Data model:**
- `Order.guest_count` (Integer, default 1)
- `OrderItem.customer_index` (Integer, 1-based, default 1) — replaces any "seat tag" concept
- When `guest_count` is 1, `customer_index` is always 1 and the grouped UI is hidden

**Backend is one order, one total.** The split is presentation-only: the order settles as one document with one grand total and one payment event. `customer_index` is retained for per-customer analytics and ticket/receipt grouping. Items are never shared between customers — each line belongs to exactly one guest.

**Guests stepper guard:** Lowering the guest count below a guest who still has items is blocked (`Order.change_guest_count` raises; the cart shows an error banner "Remove Customer N's items first"). Raising is always allowed. This keeps per-customer analytics honest — no silent re-tagging of who ordered what.

**Receipts:** One receipt per order, grouping items by customer card with a per-customer subtotal, then the overall total:

```text
ORDER #0042
-----------------------------
Customer 1
  Jollof Rice       x1   1,500
  Chicken           x2   2,000
  Subtotal               3,500

Customer 2
  Fried Rice        x1   1,500
  Coke              x1     500
  Subtotal               2,000
-----------------------------
TOTAL                    5,500
```

**Kitchen and bar tickets** also group by `customer_index` so the kitchen knows which items plate together.

**Implementation notes:**
- The grouped UI is HTMX-driven: tapping a guest header sets the active card in the session (`pos_customer_card_activate`); subsequent item additions carry that index. The guest stepper posts `guest_delta` to `pos_order_update_meta`.
- Never use the word "seat" in the UI — use "Customer 1", "Customer 2", etc.
- Cards/groupings are ephemeral presentation; only `customer_index` on each `OrderItem` is persisted.

### Departmental split

Every menu item belongs to `FOOD` or `DRINKS`. A single order can contain both. On checkout, the order total is split by department and recorded separately. Daily reports show food and drinks revenue independently — supports the bar accounting separation.

### Document submit/cancel pattern

Financial documents follow an immutable workflow:
- Orders, payments, and stock ledger entries have a `status` field with choices `DRAFT`, `SUBMITTED`, `CANCELLED`.
- Once submitted, records are never updated — only cancelled, which creates a reversal entry.
- Reversals post on the cancellation date — corrections never rewrite the original period.
- This gives an immutable audit trail.
- Implement as: `status = models.CharField(choices=[DRAFT, SUBMITTED, CANCELLED])`

### Single-location settings

The app is designed for one restaurant, one location, one settings surface. The
`Restaurant` model is a singleton — one row ever, accessed via `Restaurant.load()`.
There is no `Branch` or `POSProfile` model; all terminal config (identity, active
menu, default warehouse, order-number behaviour) lives on `Restaurant`. Multi-branch
is deferred as separate paid work and is not half-supported now. Payment methods are
managed via `ModeOfPayment.enabled` + `is_default` (exactly one default);
`POSProfilePayment` no longer exists.

### Backoffice UI conventions

- **Sidebar structure:** Backoffice group (Manager/Admin only): Dashboard, Settings,
  Inventory, Menu. POS sub-header (under Backoffice): Payments, Shifts. Quick Access group:
  Launch POS, Sign out. The shift item is labelled **"Shifts"** (not "Staff") to avoid
  colliding with the `settings:staff_list` role-assignment page (surfaced on the Settings
  dashboard as the "User Roles" card).
- **Main backoffice dashboard** (`/backoffice/dashboard/`) is a navigator, not a status
  board: a "Your Shortcuts" row (4 quick-action buttons) + a "Masters & Setup" grid (grouped
  link cards: Menu, POS, Inventory, Setup). No live operational panels or charts on Home —
  those live on per-app dashboards (Staff dashboard for shifts, Inventory dashboard for low
  stock). Analytics get a separate operations-dashboard page once orders and reports exist.
- **Payment modes live under POS** (Payments in the sidebar), not under a general setup
  area, because shift floats are the only consumer.

### Icons

Icons come from `django-hugeicons-stroke` (pure Python, inline SVG — no npm
package, no node_modules, nothing to add to Vite or static files). It is already
installed and registered as a template builtin, so the tag works in every
template without `{% load %}`:

```
{% hgi_stroke name="home-01" size="24" color="currentColor" stroke_width="2" %}
```

- Icon names are kebab-case, same naming as hugeicons.com (e.g. `home-01`, `search-01`, `notification-03`).
- Available params: `name` (required), `size` (px, default 24), `color` (HEX or CSS color, default `#000000`), `stroke_width` (default 2).
- There is **no `class` param** — for Tailwind sizing wrap the tag in a sized container, and use `color="currentColor"` so the icon inherits the text color.
- Never copy SVG markup into templates manually; never introduce a different icon library without flagging it first.

## Coding Preferences

- Only make changes that are requested or confidently understood as related to the request.
- When fixing an issue, exhaust the existing implementation before introducing a new pattern/technology. If you do introduce new, remove the old so there's no duplicate logic.
- Avoid scripts in files if the script is likely only run once.
- Avoid files over 200-300 lines — refactor at that point.
- Never add mock data to functions. Mocks only in tests or test-only utilities.
- Never overwrite `.env` without first asking and confirming.

When a decision requires user input, use the interactive `question` tool — never assume preferences, design choices, or scope. Present clickable choices with clear labels and descriptions.

## Python

- PEP 8 with 120 char line limit. Double quotes (ruff enforced). isort via ruff.
- Type hints in new code where not burdensome — not enforced on existing code.
- Python 3.14: **unparenthesized `except` with multiple exception types is valid** (PEP 758). `except ValueError, TypeError:` is equivalent to `except (ValueError, TypeError):` — not Python 2, not a `SyntaxError`. Parentheses still required with `as`: `except (ValueError, TypeError) as e:`. Do not "fix" unparenthesized forms unless adding `as`.
- Use Django ORM exclusively. Use `select_related`/`prefetch_related` to avoid N+1.
- Use function-based views by default.
- Use Django signals sparingly; document them well.
- Validate user input server-side. Handle errors explicitly, never silently.
- All models extend `apps.utils.models.BaseModel` (adds `created_at`, `updated_at`).
- The user model is `apps.users.models.CustomUser` — import directly.

## Django Templates (HTML)

- Two-space indent. Standard Django template syntax.
- Multi-line comments: `{% comment %}...{% endcomment %}`. `{# ... #}` is single-line only — never span multiple lines.
- Vite-built JS/CSS: `{% load django_vite %}` then `{% vite_asset %}`. React HMR needs `{% vite_react_refresh %}`.
- Non-vite images/JS/CSS: `{% static %}`.
- Page-level JS via Alpine.js; avoid inline `<script>` tags.
- Django 6.0 template partials: `{% partialdef %}`/`{% partial %}` inline in the template where used — keeps related markup together. Define partials inline, not in scattered files.
- `{% include %}` only for fragments genuinely shared across 3+ unrelated templates — put in `components/`. This is the exception, not the default.
- HTMX responses: use partials with `inline` or direct partial access — one template file serves both the full page and the HTMX fragment. No separate `_partial.html` per endpoint.
- Tailwind v4 utility classes only. **No DaisyUI in new Spicy code** (it exists in Pegasus boilerplate but must not be used in POS or back-office templates). No inline `style=""` attributes.

## JavaScript

- ES6+ syntax. Two-space indent in JS/JSX/HTML. Single quotes. Semicolons. camelCase vars/funcs, PascalCase components. Explicit TS annotations.
- HTMX: follow progressive enhancement. Return minimal HTML fragments from server, never full pages.
- Alpine.js for client-side interactivity without server interaction.
- Avoid inline `<script>` tags where possible.
- Validate input client and server side. Handle errors explicitly in promise chains and async functions.
- Built with Vite, served via django-vite.

## Migrations

Never hand-write schema migrations. Workflow:
1. Write/modify models.
2. Run `uv run manage.py makemigrations`.
3. Review the generated file — check dependencies and destructive ops (field removal, NOT NULL with no default, renames that look like delete+add).
4. Run `uv run manage.py migrate`.

Hand-write only for: data migrations (`RunPython` with explicit forwards/backwards), raw SQL (`RunSQL` for DB-level indexes/constraints/triggers Django can't express), multi-step destructive changes (split into separate migrations to prevent data loss), and `SeparateDatabaseAndState`.

Hard rules: do NOT hand-write `CreateModel`/`AddField`/`AlterField`/`RemoveField`. Do NOT skip `makemigrations`. If a data migration accompanies a schema change, put it in a separate migration file after the schema migration.

## Documentation & Commit Standards

**Core rule:** Code explains HOW. Comments explain WHY. Docs state WHAT. Nothing else belongs.

**Inline comments:** Only for non-obvious logic (algorithms, workarounds, intentional quirks). Never restate what code says. Never explain framework behaviour. One line max — if you need two, refactor the code.

```text
BAD:  # Loop through all payments and add them to the list
GOOD: # Cash modes first — cashier scans left-to-right at speed

BAD:  # Django requires the form to call super().__init__() before field customisation
GOOD: (no comment — standard Django pattern)
```

**Docstrings:** Module: one line on what it contains. Class: one line on what it represents (omit if self-explanatory). Function: one line on what it returns/does, add a second line only for non-obvious params/returns. Never document WHY a business rule exists, HOW an external system works, or WHERE the decision is recorded (that's PLAN.md or git history). Never reference external systems, line numbers, or docs URLs.

**Commits:** `<type>: <what changed> [<scope> if non-obvious]`. Types: feat, fix, refactor, style, chore, docs. Subject 50 chars max, imperative mood, no full stop. No body unless future-you needs to understand a non-obvious decision. Never describe file changes (the diff shows that). Never restate framework mechanics.

**Smell test:** Before writing any comment/docstring/commit, ask "Would a competent Django developer need this to understand or use the code?" If no → delete. If yes → one line.

## Git Commit Behavior

You must always commit changes **atomically**. Do not lump unrelated modifications into a single commit.

### Definition of Atomic Commit

Each commit must represent exactly ONE single, logical task, feature, bug fix, or refactor. A commit must do one thing, do it completely, and leave the repository in a working, compilable state.

### Execution Rules

Whenever the user instructs you to commit, stage modifications, or finalize a task, adhere to the following workflow:

1. **Group by Context:** Analyze the modified files. Separate structural logic fixes, visual styling updates, dependency changes, and documentation cleanups into distinct buckets.
2. **Isolate Changes:** Stage only the specific files (or specific hunks/lines using interactive staging if multiple changes exist in one file) belonging to that specific bucket.
3. **Commit Separately:** Run separate commit commands for each isolated logical unit.
4. **No Monolithic Commits:** Never bundle unrelated fixes (e.g., fixing an accounting bug and updating a button color) into one commit message.

### Commit Message Format

Use clean, concise, descriptive imperative-mood commit messages (or follow Conventional Commits standard if specified by the repo, e.g., `fix: resolve broken income account fallback chain`). The repo's specified format in the "Documentation & Commit Standards" section above takes precedence: `<type>: <what changed>` with types feat/fix/refactor/style/chore/docs, subject max 50 chars, no full stop.

## Git Worktrees — Multi-Agent / Multi-Harness Isolation

One working directory can only have one branch checked out — `git checkout` in any harness moves the branch for every harness sharing that directory.

When multiple agents or harnesses need different branches concurrently (e.g., OpenCode on `feat/pwac` while another harness is on `main`), use `git worktree` instead of switching the shared checkout:

```bash
git worktree add ../Spicy-main main
git worktree add ../Spicy-pwac feat/pwac-inventory-costing
# each harness points at its own directory; switching in one does not affect the other
```

Rule: before creating or switching branches, check `git branch --show-current` and whether another agent is active on a different branch. If so, create a worktree for your work rather than checking out in the shared directory.

## Hard Rules — Never Do

- Modify anything inside `references/`
- Use `pip install` instead of `uv add`
- Import from `references/` into the Django project
- Use Django REST Framework, Vue, React, or Socket.io
- Use DaisyUI — Tailwind CSS only
- Use `FloatField` for money
- Use `CASCADE` delete on orders, payments, or stock ledger entries
- Plan a feature without reviewing the reference material and comparable systems first
- Plan POS work without reviewing URY's POS API (`references/ury-develop/ury/ury_pos/api.py`)
- Write more than one feature at a time — complete and confirm one before the next
- Never run tests after completing a task unless the user explicitly asked for tests to be run
- Run Playwright or browser-based tests only when the user explicitly requests them

## Skills

Load the relevant skill at the start of matching tasks (see `available_skills` in the system prompt):

- Any UI component, page, or interface → `frontend-design` (Tailwind only, no DaisyUI)
- mypy type errors → `fix-types`
- Dependency upgrades → `upgrade-python-deps` or `upgrade-js-deps`
- Pegasus project config via CLI → `pegasus-projects`
- Pegasus upgrade → `upgrade-pegasus` then `resolve-pegasus-conflicts` if the merge conflicts

Project skill constraints override conflicting skill content. Activate all relevant skills together.
