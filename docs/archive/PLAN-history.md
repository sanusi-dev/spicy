# Spicy — Implementation Plan History (Archive)

Verbatim copies of implemented sections removed from `PLAN.md`. Historical — describes the
design as it was; current product facts live in `PLAN.md` §2/§4, the §3 status table, and
`AGENTS.md`. Section numbers match the originals so old cross-references still resolve.

---

## §6.1–§6.7 Completed phase plans (as originally written)

### 6.1 Settings App — Round 1 (Phase 1)

**Status:** complete — implemented and merged on `main`. (This plan described Branch, Room,
Table, Restaurant, UserRoomAssignment; §6.14 removed Branch, Room, Table, and
UserRoomAssignment entirely — `Restaurant` is now the singleton settings surface.)
**FEATURES.md sections:** A1 (Restaurant Configuration)
**Dependencies:** None — this was the foundation phase.

#### Reference files consulted

| Reference file | What was extracted |
|---|---|
| `references/erpnext-develop/erpnext/setup/doctype/branch/branch.json` | Branch model (single field) |
| `references/erpnext-develop/erpnext/setup/doctype/branch/branch.py` | Branch logic (empty) |
| `references/ury-develop/ury/ury/doctype/ury_restaurant/ury_restaurant.json` | Restaurant fields |
| `references/ury-develop/ury/ury/doctype/ury_restaurant/ury_restaurant.py` | Restaurant logic (empty) |
| `references/ury-develop/ury/ury/doctype/ury_room/ury_room.json` | Room fields |
| `references/ury-develop/ury/ury/doctype/ury_room/ury_room.py` | Room logic (empty) |
| `references/ury-develop/ury/ury/doctype/ury_table/ury_table.json` | Table fields (layout, occupied, takeaway) |
| `references/ury-develop/ury/ury/doctype/ury_table/ury_table.py` | Table logic (broken autoname — JSON is authoritative) |
| `references/ury-develop/ury/ury/doctype/ury_user/ury_user.json` | User-room assignment model |
| `references/ury-develop/ury/ury/doctype/ury_user/ury_user.py` | User logic (empty) |
| `references/ury-develop/ury/fixtures/custom_field.json` | URY custom fields on Branch |
| `references/ury-develop/ury/ury_pos/api.py` | How user→room→branch resolution works |

#### Models

All models extend `apps.utils.models.BaseModel` (adds `created_at`, `updated_at`).

##### Branch (`settings.Branch`)

| Field | Type | Source | Notes |
|---|---|---|---|
| name | CharField, max_length=100, unique | ERPNext Branch.branch | e.g. "Main Branch" |

**Methods:**
- `__str__` returns `name`
- No validation beyond field constraints

##### Room (`settings.Room`)

| Field | Type | Source | Notes |
|---|---|---|---|
| branch | ForeignKey→Branch, on_delete=PROTECT | URY Room.branch | required |
| name | CharField, max_length=100 | URY Room (prompt naming) | e.g. "Main Dining", "Bar" |
| room_type | CharField, max_length=10, choices: AC, NON_AC, blank=True | URY Room.room_type | optional |

**Methods:**
- `__str__` returns `name`
- No validation beyond field constraints
- `Meta.unique_together`: `(branch, name)` — no duplicate room names within a branch

##### Table (`settings.Table`)

| Field | Type | Source | Notes |
|---|---|---|---|
| room | ForeignKey→Room, on_delete=PROTECT | URY Table.restaurant_room | required |
| branch | ForeignKey→Branch, on_delete=PROTECT | URY Table.branch (fetch_from) | denormalized for query efficiency |
| name | CharField, max_length=50 | URY Table (prompt naming) | e.g. "T1" |
| no_of_seats | IntegerField, null=True, blank=True | URY Table.no_of_seats | |
| minimum_seating | IntegerField, null=True, blank=True | URY Table.minimum_seating | |
| table_shape | CharField, max_length=20, choices: RECTANGLE, SQUARE, CIRCLE, blank=True | URY Table.table_shape | |
| is_take_away | BooleanField, default=False | URY Table.is_take_away | virtual tables for takeaway orders |
| occupied | BooleanField, default=False, editable=False | URY Table.occupied | system-managed — set by orders app |
| latest_invoice_time | DateTimeField, null=True, blank=True, editable=False | URY Table.latest_invoice_time | system-managed |
| layout_x | FloatField, null=True, blank=True | URY Table.layout_x | floor plan position |
| layout_y | FloatField, null=True, blank=True | URY Table.layout_y | floor plan position |
| layout_width | FloatField, null=True, blank=True | URY Table.layout_width | floor plan size |
| layout_height | FloatField, null=True, blank=True | URY Table.layout_height | floor plan size |

**Methods:**
- `__str__` returns `name`
- `occupied` and `latest_invoice_time` are `editable=False` — updated by the orders app
- `Meta.unique_together`: `(room, name)` — no duplicate table names within a room

**Deviation from URY:** URY Table links to URY Restaurant (required). Spicy Table links to
Room (required) and Branch (denormalized). The Restaurant link is dropped because Spicy has
a single Restaurant per branch (singleton), and the branch is already reachable via `room.branch`.

##### Restaurant (`settings.Restaurant`)

| Field | Type | Source | Notes |
|---|---|---|---|
| company | CharField, max_length=200 | URY Restaurant.company | no Company model — just a name |
| branch | ForeignKey→Branch, on_delete=PROTECT | URY Restaurant.branch | required |
| invoice_series_prefix | CharField, max_length=20, default="REST-" | URY Restaurant.invoice_series_prefix | #1 |
| address | TextField, blank=True | URY Restaurant.address | simplified to text (no Address model) |
| default_room | ForeignKey→Room, on_delete=PROTECT | URY Restaurant.default_room | required |
| active_menu | ForeignKey→menu.Menu, null=True, blank=True | URY Restaurant.active_menu | set when menu app is built |
| default_tax_template | ForeignKey→settings.TaxTemplate, null=True, blank=True | URY Restaurant.default_tax_template | set in Round 2 |

**Methods:**
- `__str__` returns `company` or `self.branch.name`
- `clean()`: validate only one Restaurant per branch (singleton per branch)
- `clean()`: validate `self.default_room.branch == self.branch`

**Deviations from URY:**
- `room_wise_menu` and `order_type_wise_menu` omitted — FEATURES.md #7 says "There is a single
  menu for the restaurant; every room and order type uses the same menu."
- `company` is a CharField, not a Link→Company — no Company model
- `address` is a TextField, not a Link→Address — no Address model

##### UserRoomAssignment (`settings.UserRoomAssignment`)

| Field | Type | Source | Notes |
|---|---|---|---|
| user | ForeignKey→CustomUser, on_delete=CASCADE | URY User.user | required |
| room | ForeignKey→Room, on_delete=CASCADE | URY User.room | required |
| branch | ForeignKey→Branch, on_delete=CASCADE | derived from room.branch | denormalized for query efficiency |

**Methods:**
- `__str__` returns `f"{user.username} → {room.name}"`
- `clean()`: validate `self.room.branch == self.branch` (consistency check)
- `clean()`: validate no duplicate user+room pairs
- `Meta.unique_together`: `(user, room)`

**Deviation from URY:** URY uses a child table (URY User) on Branch. Spicy uses a standalone
through model — cleaner in Django ORM and easier to query ("which rooms can this user see?").

#### Views & URLs

Function-based views with HTMX partial updates. All require login.

| URL pattern | View function | Purpose |
|---|---|---|
| `/backoffice/settings/` | `settings_dashboard` | Overview landing page |
| `/backoffice/settings/branches/` | `branch_list` | List all branches |
| `/backoffice/settings/branches/create/` | `branch_create` | Create branch (HTMX modal) |
| `/backoffice/settings/branches/<int:pk>/` | `branch_detail` | View/edit branch |
| `/backoffice/settings/branches/<int:pk>/edit/` | `branch_update` | Update branch (HTMX) |
| `/backoffice/settings/rooms/` | `room_list` | List rooms (filterable by branch) |
| `/backoffice/settings/rooms/create/` | `room_create` | Create room |
| `/backoffice/settings/rooms/<int:pk>/` | `room_detail` | View/edit room |
| `/backoffice/settings/rooms/<int:pk>/edit/` | `room_update` | Update room |
| `/backoffice/settings/tables/` | `table_list` | List tables (filterable by room) |
| `/backoffice/settings/tables/create/` | `table_create` | Create table |
| `/backoffice/settings/tables/<int:pk>/` | `table_detail` | View/edit table |
| `/backoffice/settings/tables/<int:pk>/edit/` | `table_update` | Update table |
| `/backoffice/settings/tables/<int:pk>/layout/` | `table_update_layout` | Save floor-plan coordinates (HTMX POST) |
| `/backoffice/settings/restaurant/` | `restaurant_detail` | View restaurant config (singleton per branch) |
| `/backoffice/settings/restaurant/edit/` | `restaurant_update` | Update restaurant config |
| `/backoffice/settings/users/` | `user_room_list` | List user-room assignments |
| `/backoffice/settings/users/create/` | `user_room_create` | Assign user to room |
| `/backoffice/settings/users/<int:pk>/edit/` | `user_room_update` | Edit assignment |
| `/backoffice/settings/users/<int:pk>/delete/` | `user_room_delete` | Remove assignment |

#### Templates

All templates go in `templates/backoffice/settings/`. Pure Tailwind CSS (no DaisyUI). Use Django
6.0 template partials for HTMX response fragments. Base template: `web/app/app_base.html`.

| Template | Purpose |
|---|---|
| `templates/backoffice/settings/dashboard.html` | Overview landing page |
| `templates/backoffice/settings/branch_list.html` | Branch list with `{% partialdef row %}` per row |
| `templates/backoffice/settings/branch_form.html` | Create/edit branch (modal or inline) |
| `templates/backoffice/settings/room_list.html` | Room list with branch filter |
| `templates/backoffice/settings/room_form.html` | Create/edit room |
| `templates/backoffice/settings/table_list.html` | Table list with room filter |
| `templates/backoffice/settings/table_form.html` | Create/edit table |
| `templates/backoffice/settings/table_layout.html` | Floor-plan editor (drag-and-drop) |
| `templates/backoffice/settings/restaurant_detail.html` | Restaurant config view/edit |
| `templates/backoffice/settings/user_room_list.html` | User-room assignment list |
| `templates/backoffice/settings/user_room_form.html` | Assign/edit user-room |

#### Table Layout Editor (#4)

The floor-plan editor is the most complex UI component in Round 1:
- **HTML5 drag API** (via Alpine.js directives) for drag/move/resize
- **HTMX** to save coordinates via `table_update_layout` view (POST with x, y, width, height)
- CSS-grid or absolute-positioned background for the room floor plan
- Zoom/pan via Alpine.js state
- Shape icons (rectangle, square, circle) rendered with Tailwind/SVG

#### Forms

Use Django `ModelForm` for each model. Validation logic in the form's `clean()` and
`clean_<field>()` methods, not in views.

| Form | Model | Notes |
|---|---|---|
| `BranchForm` | Branch | |
| `RoomForm` | Room | Branch field may be hidden if user has only one branch |
| `TableForm` | Table | Branch auto-set from selected room |
| `RestaurantForm` | Restaurant | Branch field read-only (singleton per branch) |
| `UserRoomAssignmentForm` | UserRoomAssignment | Branch auto-set from selected room |

#### Admin

Register all 5 models in `settings/admin.py` with `list_display`, `list_filter`, and
`search_fields` for back-office admin access.

#### Tests

| Test file | What it covers |
|---|---|
| `settings/tests/test_branch.py` | Branch CRUD |
| `settings/tests/test_room.py` | Room CRUD, branch relationship, unique_together |
| `settings/tests/test_table.py` | Table CRUD, shape choices, takeaway flag, layout coordinates, editable=False on occupied |
| `settings/tests/test_restaurant.py` | Restaurant singleton per branch, default_room.branch consistency, prefix defaults |
| `settings/tests/test_user_room_assignment.py` | User-room mapping, duplicate prevention, branch consistency |
| `settings/tests/test_views.py` | View-level tests: list, create, edit, delete for each model; login required; HTMX partial responses |

Use Django's `TestCase` for database tests. Test both happy path and error/edge cases.

#### Deviations from reference — summary

| Deviation | Reason |
|---|---|
| `room_wise_menu` and `order_type_wise_menu` omitted | FEATURES.md #7: single menu for all rooms/order types |
| `UserRoomAssignment` is standalone, not child table on Branch | Django ORM pattern — cleaner queries |
| `address` is TextField, not Link→Address | No Address model |
| `company` is CharField, not Link→Company | No Company model |
| `occupied` and `latest_invoice_time` are `editable=False` | System-managed, prevents manual override |
| Printer settings NOT on Room | Spicy routes by department flag (#68), printer config on ProductionUnit (#16) |
| Table links to Room (not Restaurant) | Branch reachable via room.branch; single Restaurant per branch |
| Branch kept in DB but hidden in the UI; auto-set via `Branch.get_default()` (or derived from room) on Menu, Room, Warehouse, Restaurant; Table/UserRoomAssignment derive branch from room | Single-site restaurant; multi-branch isolation remains available without cashier-facing branch pickers |

#### Implementation steps

1. Create the `settings` app: `make uv run 'pegasus startapp settings Branch Room Table Restaurant UserRoomAssignment'`
2. Write models in `apps/settings/models.py`
3. Write forms in `apps/settings/forms.py`
4. Write views in `apps/settings/views.py`
5. Write URLs in `apps/settings/urls.py`
6. Register in `apps/settings/admin.py`
7. Add `apps.settings` to `INSTALLED_APPS` in `spicy/settings.py`
8. Include settings URLs in `spicy/urls.py`
9. Write templates in `templates/backoffice/settings/`
10. Create and run migrations: `make migrations && make migrate`
11. Write tests: `apps/settings/tests/`
12. Run tests: `make test ARGS='apps.settings'`
13. Run lint: `make ruff`

---

### 6.1b Service Layer Refactor (2026-08-08)

**Deviation from reference (recorded per REFACTOR_SERVICE_LAYER.md §"Reference notes & deviation documentation"):**

> ERPNext/URY keep document workflows as doctype methods (validate/on_submit).
> Spicy deviates deliberately: multi-entity workflows (order settlement,
> cancellation, returns, ticket creation, drink stock accounting, shift closing)
> live in per-app service modules (`apps/orders/services.py`,
> `apps/staff/services.py`). Models retain data, invariants, and simple
> self-contained mutations. Rationale: `Order` had grown to ~1,000 lines mixing
> four concerns, the same drink-stock math was duplicated between the model and
> the POS catalog view, and shift-closing logic was split across two apps. Logic
> is ported 1:1 — no behavior change.

**Extractions beyond the pure model→service move** (approved in REFACTOR_SERVICE_LAYER.md §1.4, each genuine duplication or business logic, ported 1:1):

1. `dispatch_tickets(tickets)` — the print → lock → set `print_status` → save loop was duplicated in `pos_order_sync`, `pos_order_cancel`, `pos_order_ticket_print`, and backoffice `order_cancel`. (Note: `pos_order_history_print` prints a receipt, not a ticket — it was listed in the plan but does not share this loop.)
2. `apply_add_on_line(order, item, add_on_ids, qty, customer_index, comments="")` — add-on pricing/merge block from `pos_order_add_item` (signature extended with `comments` so the base line's special instructions survive; the view's item/menu validation order shifted slightly — an unavailable-menu-item error now surfaces after the comments-length error instead of before, only in the degenerate case where both apply).
3. `order_history_rows(filters)` — filter/queryset building from `pos_order_history`; date parsing and the manager-only status clamp stayed in the view because the context needs the normalized date string and clamped filter value.
4. `open_draft_orders(shift, order_filter, order_search)` — draft-order list query + preview attachment from `pos_home` (signature extended with the filter/search params).

**Deliberate skip:** §1.4 extraction 5 (`_authenticated_user`/`_is_htmx` dedupe into `apps/utils`) was skipped — `_is_htmx` exists only 2× (one in the untouchable `settings` app), so the plan's "3 usages each" condition was not met.

**Sizing targets (soft) that did not land as estimated** — all enumerated moves were done; targets were expectations per the plan:

| Metric | Plan expectation | Actual |
|---|---|---|
| `views_pos.py` | ~1,200 (DoD under ~1,250) | 1,253 |
| `apps/orders/services.py` | ~500 | 934 |
| `inventory/views.py` | ~650 | 736 |
| `staff/views.py` | ~380 | 449 |
| Largest view in `views_pos.py` | ~87, none over ~90 | `pos_close_shift` 152, `pos_open_shift` 106 |

`pos_close_shift` at 152 lines cannot shrink below ~90 with the enumerated moves alone — the two helpers it lost were module-level functions, not view internals; further slimming would need new extractions the plan forbids. Flagged per the plan's "stop and flag it" rule rather than inventing work.

**Caller-refresh note:** `settle_order`/`cancel_order`/`cancel_sent_order`/`discard_order` end with `order.refresh_from_db()` on the caller's instance, preserving the old methods' final `self.refresh_from_db()` semantics (tests like `test_settle_dine_in_without_print` assert post-settle state on the caller's instance).

---

### 6.2 Inventory App (Phase 2)

**Status:** complete — implemented and merged on `main`. (Per §6.20, later revisions: receipts
post to `Restaurant.store_warehouse`, Material Transfer is Store→department only,
`StockReconciliation.reason` is required, Material Issue was removed, and independent
`is_stock_item` / `is_sales_item` / `is_purchase_item` flags were added.)
**FEATURES.md sections:** A12
**Dependencies:** settings

#### Decisions

- **Stock tracking:** Uses FIFO and moving average valuation
- **Item variants:** Fields on Item only (`has_variants`, `variant_of`). No ItemAttribute/ItemVariantAttribute models. Variant selection handled in menu app (Phase 3).
- **Tree structure:** Simple parent ForeignKey (self-referential). No django-mptt or treebeard.
- **UOM:** Separate master model, seeded with common restaurant UOMs.

#### Reference files consulted

| Reference file | What was extracted |
|---|---|
| `references/erpnext-develop/erpnext/stock/doctype/item/item.json` | Item fields (60+ — filtered to ~20 for POS) |
| `references/erpnext-develop/erpnext/stock/doctype/item/item.py` | Item validation logic |
| `references/erpnext-develop/erpnext/stock/doctype/item_barcode/item_barcode.json` | Barcode child table |
| `references/erpnext-develop/erpnext/stock/doctype/item_reorder/item_reorder.json` | Reorder level child table |
| `references/erpnext-develop/erpnext/stock/doctype/uom_conversion_detail/uom_conversion_detail.json` | UOM conversion child table |
| `references/erpnext-develop/erpnext/setup/doctype/item_group/item_group.json` | Item Group tree structure |
| `references/erpnext-develop/erpnext/stock/doctype/warehouse/warehouse.json` | Warehouse doctype |
| `references/erpnext-develop/erpnext/stock/doctype/stock_ledger_entry/stock_ledger_entry.json` | SLE fields |
| `references/erpnext-develop/erpnext/stock/doctype/stock_ledger_entry/stock_ledger_entry.py` | SLE validation |
| `references/erpnext-develop/erpnext/stock/doctype/stock_entry/stock_entry.json` | Stock Entry fields and purposes |
| `references/erpnext-develop/erpnext/stock/doctype/stock_entry/stock_entry.py` | Stock Entry submit/cancel logic |
| `references/erpnext-develop/erpnext/stock/doctype/stock_entry_detail/stock_entry_detail.json` | Stock Entry line items |
| `references/erpnext-develop/erpnext/stock/doctype/bin/bin.json` | Bin cache table fields |
| `references/erpnext-develop/erpnext/stock/doctype/stock_reconciliation/stock_reconciliation.json` | Reconciliation fields |
| `references/erpnext-develop/erpnext/stock/doctype/stock_reconciliation_item/stock_reconciliation_item.json` | Reconciliation line items |
| `references/erpnext-develop/erpnext/selling/doctype/product_bundle/product_bundle.json` | Product bundle parent |
| `references/erpnext-develop/erpnext/selling/doctype/product_bundle_item/product_bundle_item.json` | Product bundle components |
| `references/erpnext-develop/erpnext/stock/stock_ledger.py` | SLE creation and Bin update logic |
| `references/erpnext-develop/erpnext/stock/reorder_item.py` | Reorder level alert logic |
| `references/ury-develop/ury/ury/doctype/ury_order/ury_order.py` | How URY sets update_stock=1 |

#### Models (14 total)

All models extend `apps.utils.models.BaseModel`. Serial number tracking (#114) was removed from FEATURES.md by user decision — not implemented.

##### UOM (`inventory.UOM`)

| Field | Type | Notes |
|---|---|---|
| name | CharField, max_length=50, unique | e.g. "Each", "Kg", "Litre", "Case" |
| is_active | BooleanField, default=True | |

Seed data: Each, Kg, Gram, Litre, Millilitre, Case, Box, Dozen, Pack.

##### ItemGroup (`inventory.ItemGroup`)

| Field | Type | Notes |
|---|---|---|
| name | CharField, max_length=100, unique | e.g. "Food", "Beverages" |
| description | TextField, blank=True | |

**Methods:** `__str__` returns `name`.

##### Warehouse (`inventory.Warehouse`)

| Field | Type | Notes |
|---|---|---|
| name | CharField, max_length=100 | e.g. "Kitchen", "Bar", "Stores" |
| branch | ForeignKey→settings.Branch, on_delete=PROTECT, related_name="warehouses" | |
| disabled | BooleanField, default=False | |

**Methods:** `__str__` returns `name`. Meta: `unique_together = [("name", "branch")]`.

##### Item (`inventory.Item`)

| Field | Type | Notes |
|---|---|---|
| item_code | CharField, max_length=50, unique | The primary identifier |
| item_name | CharField, max_length=200 | Display name (auto-set to item_code if blank) |
| item_group | ForeignKey→ItemGroup, on_delete=PROTECT, related_name="items" | required |
| stock_uom | ForeignKey→UOM, on_delete=PROTECT, related_name="items" | required |
| department | CharField, max_length=10, choices=[("FOOD","Food"),("DRINKS","Drinks")] | required — #260 |
| image | ImageField, null=True, blank=True | Item image for POS |
| description | TextField, blank=True | |
| disabled | BooleanField, default=False | Disabled items can't be selected |
| is_stock_item | BooleanField, default=True | If False, no stock tracking |
| default_warehouse | ForeignKey→Warehouse, null=True, blank=True, on_delete=SET_NULL | |
| valuation_method | CharField, max_length=20, choices=[("FIFO","FIFO"),("MOVING_AVERAGE","Moving Average")], default="FIFO" | |
| has_variants | BooleanField, default=False | Template item — can't be sold directly |
| variant_of | ForeignKey→self, null=True, blank=True, on_delete=PROTECT, related_name="variants" | Set on variant items |
| last_purchase_rate | DecimalField, max_digits=10, decimal_places=2, null=True, blank=True | Last purchase rate |

**Methods:**
- `__str__` returns `item_name or item_code`
- `clean()`: if `has_variants=True`, item cannot be `is_stock_item=True`
- `clean()`: if `variant_of` is set, validate parent has `has_variants=True`


##### ItemBarcode (`inventory.ItemBarcode`)

| Field | Type | Notes |
|---|---|---|
| item | ForeignKey→Item, on_delete=CASCADE, related_name="barcodes" | |
| barcode | CharField, max_length=100, unique | |
| barcode_type | CharField, max_length=20, blank=True | e.g. "EAN", "UPC-A", "CODE-39" |

##### ItemUOMConversion (`inventory.ItemUOMConversion`)

| Field | Type | Notes |
|---|---|---|
| item | ForeignKey→Item, on_delete=CASCADE, related_name="uom_conversions" | |
| uom | ForeignKey→UOM, on_delete=CASCADE | |
| conversion_factor | DecimalField, max_digits=10, decimal_places=4 | How many stock_uom = 1 of this uom |

**Validation:** stock_uom always has conversion_factor=1 (enforced in Item.clean). No duplicate UOMs per item.

##### ReorderLevel (`inventory.ReorderLevel`)

| Field | Type | Notes |
|---|---|---|
| item | ForeignKey→Item, on_delete=CASCADE, related_name="reorder_levels" | |
| warehouse | ForeignKey→Warehouse, on_delete=CASCADE | |
| reorder_level | DecimalField, max_digits=10, decimal_places=2, default=0 | When stock drops below this, alert |
| reorder_qty | DecimalField, max_digits=10, decimal_places=2, default=0 | How much to reorder |

**Meta:** `unique_together = [("item", "warehouse")]`

##### ProductBundle (`inventory.ProductBundle`)

| Field | Type | Notes |
|---|---|---|
| parent_item | ForeignKey→Item, on_delete=CASCADE, related_name="bundles" | The combo item (is_stock_item=False) |
| is_active | BooleanField, default=True | |

**Meta:** `unique_together = [("parent_item",)]` — one active bundle per parent item

##### ProductBundleItem (`inventory.ProductBundleItem`)

| Field | Type | Notes |
|---|---|---|
| bundle | ForeignKey→ProductBundle, on_delete=CASCADE, related_name="items" | |
| item | ForeignKey→Item, on_delete=PROTECT | Must be a leaf item (has_variants=False) |
| qty | DecimalField, max_digits=10, decimal_places=2 | Quantity of this component |

##### Bin (`inventory.Bin`)

Cache table — one row per item+warehouse. Auto-created by the stock ledger logic.

| Field | Type | Notes |
|---|---|---|
| item | ForeignKey→Item, on_delete=CASCADE | |
| warehouse | ForeignKey→Warehouse, on_delete=CASCADE | |
| actual_qty | DecimalField, max_digits=10, decimal_places=2, default=0 | Current stock on hand |
| reserved_qty | DecimalField, max_digits=10, decimal_places=2, default=0 | Reserved for draft POS invoices |
| valuation_rate | DecimalField, max_digits=10, decimal_places=2, default=0 | |
| stock_value | DecimalField, max_digits=12, decimal_places=2, default=0 | actual_qty * valuation_rate |

**Meta:** `unique_together = [("item", "warehouse")]`

**Class method:** `get_or_create(item, warehouse)` — returns existing Bin or creates one.

##### StockLedgerEntry (`inventory.StockLedgerEntry`)

Immutable stock movement record. Never created directly by users.

| Field | Type | Notes |
|---|---|---|
| item | ForeignKey→Item, on_delete=PROTECT, related_name="stock_ledger_entries" | |
| warehouse | ForeignKey→Warehouse, on_delete=PROTECT | |
| posting_datetime | DateTimeField, default=now, editable=False | |
| voucher_type | CharField, max_length=50 | "Stock Entry", "POS Invoice", "Stock Reconciliation" |
| voucher_no | CharField, max_length=100 | Parent document ID |
| voucher_detail_no | CharField, max_length=100, blank=True | Line item ID in parent |
| actual_qty | DecimalField, max_digits=10, decimal_places=2, editable=False | +in, -out |
| qty_after_transaction | DecimalField, max_digits=10, decimal_places=2, editable=False | Running balance |
| incoming_rate | DecimalField, max_digits=10, decimal_places=2, default=0, editable=False | |
| outgoing_rate | DecimalField, max_digits=10, decimal_places=2, default=0, editable=False | |
| valuation_rate | DecimalField, max_digits=10, decimal_places=2, default=0, editable=False | Rate after this entry |
| stock_value | DecimalField, max_digits=12, decimal_places=2, default=0, editable=False | |
| stock_queue | TextField, blank=True, default="" | JSON FIFO queue |
| is_cancelled | BooleanField, default=False, editable=False | |

**Class method:** `create_entry(item, warehouse, actual_qty, voucher_type, voucher_no, rate=0)` — creates SLE, updates Bin, recalculates qty_after_transaction and valuation_rate.

##### StockEntry (`inventory.StockEntry`)

Manual stock movement document. Submit/cancel workflow.

| Field | Type | Notes |
|---|---|---|
| purpose | CharField, max_length=30, choices=[("MATERIAL_RECEIPT","Material Receipt"),("MATERIAL_ISSUE","Material Issue"),("MATERIAL_TRANSFER","Material Transfer"),("REPACK","Repack")] | required |
| posting_date | DateField, default=today | |
| from_warehouse | ForeignKey→Warehouse, null=True, blank=True, on_delete=PROTECT, related_name="outgoing_stock_entries" | Source (for Issue/Transfer/Repack) |
| to_warehouse | ForeignKey→Warehouse, null=True, blank=True, on_delete=PROTECT, related_name="incoming_stock_entries" | Target (for Receipt/Transfer/Repack) |
| status | CharField, max_length=10, choices=[("DRAFT","Draft"),("SUBMITTED","Submitted"),("CANCELLED","Cancelled")], default="DRAFT" | |
| remarks | TextField, blank=True | |

**Methods:**
- `submit()`: set status to SUBMITTED, create SLEs for each detail line
- `cancel()`: set status to CANCELLED, create reversal SLEs
- `clean()`: validate from_warehouse/to_warehouse based on purpose (Receipt needs to, Issue needs from, Transfer needs both)

##### StockEntryDetail (`inventory.StockEntryDetail`)

| Field | Type | Notes |
|---|---|---|
| stock_entry | ForeignKey→StockEntry, on_delete=CASCADE, related_name="items" | |
| item | ForeignKey→Item, on_delete=PROTECT | |
| source_warehouse | ForeignKey→Warehouse, null=True, blank=True, on_delete=PROTECT | Overrides stock_entry.from_warehouse |
| target_warehouse | ForeignKey→Warehouse, null=True, blank=True, on_delete=PROTECT | Overrides stock_entry.to_warehouse |
| qty | DecimalField, max_digits=10, decimal_places=2 | required |
| uom | ForeignKey→UOM, on_delete=PROTECT | required |
| conversion_factor | DecimalField, max_digits=10, decimal_places=4, default=1 | |
| basic_rate | DecimalField, max_digits=10, decimal_places=2, default=0 | Cost per stock UOM |

##### StockReconciliation (`inventory.StockReconciliation`)

| Field | Type | Notes |
|---|---|---|
| purpose | CharField, max_length=20, choices=[("OPENING_STOCK","Opening Stock"),("RECONCILIATION","Stock Reconciliation")], default="RECONCILIATION" | |
| posting_date | DateField, default=today | |
| warehouse | ForeignKey→Warehouse, on_delete=PROTECT | |
| status | CharField, max_length=10, choices=[("DRAFT","Draft"),("SUBMITTED","Submitted"),("CANCELLED","Cancelled")], default="DRAFT" | |
| remarks | TextField, blank=True | |

##### StockReconciliationItem (`inventory.StockReconciliationItem`)

| Field | Type | Notes |
|---|---|---|
| reconciliation | ForeignKey→StockReconciliation, on_delete=CASCADE, related_name="items" | |
| item | ForeignKey→Item, on_delete=PROTECT | |
| warehouse | ForeignKey→Warehouse, on_delete=PROTECT | |
| qty | DecimalField, max_digits=10, decimal_places=2 | The counted quantity |
| current_qty | DecimalField, max_digits=10, decimal_places=2, default=0, editable=False | Auto-filled from Bin |
| valuation_rate | DecimalField, max_digits=10, decimal_places=2, null=True, blank=True | |

#### Business logic — StockLedgerEntry.create_entry

This is the core method that all stock movements funnel through:

```python
@classmethod
def create_entry(cls, item, warehouse, actual_qty, voucher_type, voucher_no, rate=0, voucher_detail_no=""):
    bin = Bin.get_or_create(item, warehouse)
    previous_qty = bin.actual_qty
    new_qty = previous_qty + actual_qty

    # FIFO queue management
    queue = json.loads(bin.stock_queue or "[]") if hasattr(bin, "stock_queue") else []
    # ... update queue based on actual_qty sign and rate

    # Create the SLE
    sle = cls.objects.create(
        item=item, warehouse=warehouse, actual_qty=actual_qty,
        qty_after_transaction=new_qty, voucher_type=voucher_type,
        voucher_no=voucher_no, voucher_detail_no=voucher_detail_no,
        incoming_rate=rate if actual_qty > 0 else 0,
        outgoing_rate=rate if actual_qty < 0 else 0,
        valuation_rate=bin.valuation_rate,
        stock_value=new_qty * bin.valuation_rate,
    )

    # Update Bin
    bin.actual_qty = new_qty
    bin.valuation_rate = ...  # recalculate
    bin.stock_value = bin.actual_qty * bin.valuation_rate
    bin.save()

    return sle
```

##### PurchaseReceipt (`inventory.PurchaseReceipt`)

Per FEATURES.md #123. Records goods received from a supplier. On submit, increases stock levels.

| Field | Type | Notes |
|---|---|---|
| supplier_name | CharField, max_length=200 | Supplier name (no Supplier model) |
| supplier_delivery_note | CharField, max_length=100, blank=True | Supplier's delivery note reference |
| posting_date | DateField, default=today | Date of receipt |
| warehouse | ForeignKey→Warehouse, on_delete=PROTECT, related_name="purchase_receipts" | Store room that receives the entire delivery |
| status | CharField, max_length=10, choices=[("DRAFT","Draft"),("SUBMITTED","Submitted"),("CANCELLED","Cancelled")], default="DRAFT" | |
| total | DecimalField, max_digits=12, decimal_places=2, default=0, editable=False | Sum of item amounts |
| remarks | TextField, blank=True | |

**Methods:**
- `__str__` returns `f"PR {self.supplier_name} {self.posting_date}"`
- `submit()`: set status to SUBMITTED, create SLEs for each item (actual_qty=+received_qty to purchase_receipt.warehouse)
- `cancel()`: set status to CANCELLED, create reversal SLEs
- `clean()`: warehouse is required

##### PurchaseReceiptItem (`inventory.PurchaseReceiptItem`)

| Field | Type | Notes |
|---|---|---|
| purchase_receipt | ForeignKey→PurchaseReceipt, on_delete=CASCADE, related_name="items" | |
| item | ForeignKey→Item, on_delete=PROTECT | |
| received_qty | DecimalField, max_digits=10, decimal_places=2 | Quantity entering stock (omit damaged/refused goods) |
| rate | DecimalField, max_digits=10, decimal_places=2 | Cost per unit |
| amount | DecimalField, max_digits=12, decimal_places=2, default=0, editable=False | received_qty * rate (auto-calculated) |

No per-line warehouse — the whole receipt posts to `PurchaseReceipt.warehouse`. Store → kitchen (etc.) moves use Stock Entry.

**Methods:**
- `save()`: auto-calculate amount = received_qty * rate

#### Business logic — StockLedgerEntry.create_entry

| URL pattern | View | Purpose |
|---|---|---|
| `/backoffice/inventory/` | `inventory_dashboard` | Overview with low-stock alerts |
| `/backoffice/inventory/uoms/` | `uom_list` / `uom_create` / `uom_update` | UOM CRUD |
| `/backoffice/inventory/item-groups/` | `item_group_list` / `item_group_create` / `item_group_detail` / `item_group_update` | Item Group CRUD (tree view) |
| `/backoffice/inventory/warehouses/` | `warehouse_list` / `warehouse_create` / `warehouse_detail` / `warehouse_update` | Warehouse CRUD |
| `/backoffice/inventory/items/` | `item_list` / `item_create` / `item_detail` / `item_update` | Item CRUD (with barcodes, UOMs, reorder levels as inline formsets) |
| `/backoffice/inventory/bundles/` | `product_bundle_list` / `product_bundle_create` / `product_bundle_detail` / `product_bundle_update` | Product Bundle CRUD |
| `/backoffice/inventory/stock-entries/` | `stock_entry_list` / `stock_entry_create` / `stock_entry_detail` / `stock_entry_submit` / `stock_entry_cancel` | Stock Entry create + submit/cancel |
| `/backoffice/inventory/reconciliations/` | `reconciliation_list` / `reconciliation_create` / `reconciliation_detail` / `reconciliation_submit` / `reconciliation_cancel` | Stock Reconciliation create + submit/cancel |
| `/backoffice/inventory/purchase-receipts/` | `purchase_receipt_list` / `purchase_receipt_create` / `purchase_receipt_detail` / `purchase_receipt_submit` / `purchase_receipt_cancel` | Purchase Receipt create + submit/cancel (#123) |
| `/backoffice/inventory/stock-ledger/` | `stock_ledger_list` | SLE report (read-only, filterable by item/warehouse/date) |
| `/backoffice/inventory/stock-balance/` | `stock_balance_list` | Current stock per item per warehouse (from Bin) |

#### Tests

| Test file | What it covers |
|---|---|
| `test_uom.py` | UOM CRUD |
| `test_item_group.py` | Item Group CRUD, parent/child tree, is_group flag |
| `test_warehouse.py` | Warehouse CRUD, branch link |
| `test_item.py` | Item CRUD, department choices, variant validation, barcode/UOM/reorder child tables |
| `test_bin.py` | Bin auto-creation, get_or_create, actual_qty updates |
| `test_stock_ledger_entry.py` | SLE creation, qty_after_transaction running balance, Bin update, cancellation reversal |
| `test_stock_entry.py` | Stock Entry CRUD, submit creates SLEs, cancel reverses, purpose validation (from/to warehouse) |
| `test_stock_reconciliation.py` | Reconciliation submit adjusts stock, cancel reverses |
| `test_product_bundle.py` | Bundle CRUD, component validation |
| `test_views.py` | View-level tests: login required, list/create/submit/cancel, filtering |

#### Deviations from reference

| Deviation | Reason |
|---|---|---|
| ItemAttribute/ItemVariantAttribute excluded | Variants handled via simple parent FK on Item; menu app handles selection |
| No tree library (mptt/treebeard) | Flat models — restaurant categories and warehouses are simple enough not to need deep nesting |
| Accounting fields dropped (expense_account, income_account, cost_center, etc.) | Accounting handled at POS Profile / restaurant config level |
| Purchase Receipt rejected_warehouse / rejected_qty / accepted_qty dropped | Only book what enters sellable stock. Damaged goods at receipt are omitted from the PR; later write-offs use Stock Reconciliation or Material Issue. ERPNext dual accepted/rejected path is overkill for a single-branch restaurant. |
| Purchase Receipt warehouse only on parent (no item warehouse); renamed accepted_warehouse → warehouse | Restaurant receives into one store room per delivery; item-level override is an ERPNext footgun. Internal moves use Stock Entry (Material Transfer / Issue). |
| Manufacturing fields dropped (BOM, work_order, subcontract) | Not applicable to a restaurant |
| Fixed asset fields dropped | Not applicable |
| UOM Conversion dropped | Items use a single stock_uom — the practical unit used in the kitchen (Mudu, Kg, Pieces). No conversions needed |
| Batches, barcodes, product bundles, reorder levels dropped | Removed as unnecessary for restaurant operations — kitchen manager tracks consumption manually |
| Bin simplified (no ordered_qty, indented_qty, planned_qty) | Restaurant doesn't use purchase orders or work orders |
| `department` field added to Item | Spicy-specific: FOOD/DRINKS classification (#260) — not in ERPNext |
| `last_purchase_rate` auto-updated on Purchase Receipt | ERPNext naming: standard_rate = selling price; last_purchase_rate = auto-updated cost from buying transactions |
| 3-tier roles (Admin/Manager/Cashier) | Only superusers can assign roles. Admin → Manager → Cashier hierarchy |

---

### 6.3 Menu App (Phase 3)

**Status:** complete — implemented and merged on `main`. (PriceList/ItemPrice were later
removed — the POS resolves prices from `MenuItem.rate` directly; see §6.17 and §4 menu summary.)

#### Reference files consulted

| Reference file | What was extracted |
|---|---|
| `references/ury-develop/ury/ury/doctype/ury_menu/ury_menu.json` | Menu fields and price list auto-creation |
| `references/ury-develop/ury/ury/doctype/ury_menu/ury_menu.py` | Menu validate + make_price_list logic |
| `references/ury-develop/ury/ury/doctype/ury_menu_item/ury_menu_item.json` | MenuItem fields (rate, special_dish, disabled, course) |
| `references/ury-develop/ury/ury/doctype/ury_menu_course/ury_menu_course.json` | Studied but rejected — MenuCourse not in FEATURES.md |
| `references/ury-develop/ury/ury/doctype/item_add_on/item_add_on.json` | Add-on child table structure |
| `references/ury-develop/ury/ury/doctype/pos_item_variants/pos_item_variants.json` | POS variant child table structure |
| `references/erpnext-develop/erpnext/stock/doctype/item_price/item_price.json` | ItemPrice fields |
| `references/erpnext-develop/erpnext/stock/doctype/item_price/item_price.py` | ItemPrice validation |
| `references/erpnext-develop/erpnext/stock/doctype/price_list/price_list.json` | PriceList fields |
| `references/ury-develop/ury/ury_pos/api.py` | getRestaurantMenu — how POS loads menu items |
| `references/ury-develop/ury/ury/hooks/ury_item.py` | Add-on/variant integrity validation |
| `references/ury-develop/pos/src/components/ProductDialog.tsx` | How add-ons/variants are priced on POS |

#### Models (6 total)

All models extend `apps.utils.models.BaseModel`.

> **Note:** MenuCourse was removed — it is NOT in FEATURES.md. The category sidebar (#195) uses Item Groups, not courses.

##### Menu (`menu.Menu`)

| Field | Type | Notes |
|---|---|---|
| name | CharField, max_length=100 | e.g. "Main Menu", "Lunch Menu" |
| branch | ForeignKey→settings.Branch, on_delete=PROTECT, related_name="menus" | Required in DB; the UI hides it and auto-assigns via `Branch.get_default()` |
| enabled | BooleanField, default=True | Disabled menus hide all items from POS |

**Methods:**
- `__str__` returns `name`
- `save()`: if branch unset, assign `Branch.get_default()`; then sync PriceList / ItemPrice from MenuItem.rate values
- `sync_price_list()`: get-or-create a PriceList linked to this menu; delete old ItemPrice rows; create new ones from MenuItem rows
- Meta: `unique_together = [("name", "branch")]`, `ordering = ["name"]`
- Back-office form fields: `name`, `enabled` only (no branch picker / list filter)

##### MenuItem (`menu.MenuItem`)

| Field | Type | Notes |
|---|---|---|
| menu | ForeignKey→Menu, on_delete=CASCADE, related_name="items" | |
| item | ForeignKey→inventory.Item, on_delete=PROTECT, related_name="menu_items" | |
| item_name | CharField, max_length=200 | Denormalized from Item.item_name (synced on save) |
| rate | DecimalField, max_digits=10, decimal_places=2 | Selling price — source of truth for POS display |
| special_dish | BooleanField, default=False | Highlighted on POS |
| disabled | BooleanField, default=False | Disabled items hidden from POS |

**Methods:**
- `__str__` returns `item_name or item.item_code`
- `save()`: auto-set `item_name` from `item.item_name` if blank
- `clean()`: if `rate` is blank/zero and `item.last_purchase_rate` is set, default to `item.last_purchase_rate`
- Meta: `unique_together = [("menu", "item")]`, `ordering = ["item_name"]`

##### PriceList (`menu.PriceList`)

| Field | Type | Notes |
|---|---|---|
| name | CharField, max_length=100, unique | Auto-set to menu name |
| enabled | BooleanField, default=True | |
| selling | BooleanField, default=True | |
| buying | BooleanField, default=False | |
| menu | ForeignKey→Menu, null=True, blank=True, on_delete=SET_NULL, related_name="price_lists" | Back-link to the menu that auto-created this price list |

**Methods:** `__str__` returns `name`. Meta: `ordering = ["name"]`.

##### ItemPrice (`menu.ItemPrice`)

| Field | Type | Notes |
|---|---|---|
| item | ForeignKey→inventory.Item, on_delete=CASCADE, related_name="prices" | |
| price_list | ForeignKey→PriceList, on_delete=CASCADE, related_name="prices" | |
| price_list_rate | DecimalField, max_digits=10, decimal_places=2 | The stored price |
| uom | ForeignKey→inventory.UOM, on_delete=PROTECT, related_name="prices" | Defaults to item.stock_uom |

**Methods:** `__str__` returns `f"{item.item_code}: {price_list_rate}"`. Meta: `unique_together = [("item", "price_list", "uom")]`.

##### ItemAddOn (`menu.ItemAddOn`)

POS add-on relationship — links a parent Item to add-on Items.

| Field | Type | Notes |
|---|---|---|
| parent_item | ForeignKey→inventory.Item, on_delete=CASCADE, related_name="add_ons" | The item that has add-ons |
| add_on_item | ForeignKey→inventory.Item, on_delete=PROTECT, related_name="add_on_for" | The add-on item itself |

**Methods:**
- `__str__` returns `f"{parent_item.item_name} + {add_on_item.item_name}"`
- `clean()`: validate that `add_on_item` is a member of at least one Menu (otherwise its POS price would be unresolved)
- Meta: `unique_together = [("parent_item", "add_on_item")]`

##### ItemVariant (`menu.ItemVariant`)

POS variant relationship — links a parent Item to variant Items (e.g. Quarter/Half/Full).

| Field | Type | Notes |
|---|---|---|
| parent_item | ForeignKey→inventory.Item, on_delete=CASCADE, related_name="pos_variants" | The parent/template item |
| variant_item | ForeignKey→inventory.Item, on_delete=PROTECT, related_name="pos_variant_of" | The variant item |

**Methods:**
- `__str__` returns `f"{parent_item.item_name} → {variant_item.item_name}"`
- `clean()`: validate that `variant_item` is a member of at least one Menu
- Meta: `unique_together = [("parent_item", "variant_item")]`

#### Cross-app migration: add active_menu to Restaurant

The settings app's Restaurant model needs an `active_menu` FK to Menu. This is done via a migration in the menu app:

```python
# menu/migrations/0002_add_active_menu_to_restaurant.py
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [
        ("settings", "0001_initial"),
        ("menu", "0001_initial"),
    ]
    operations = [
        migrations.AddField(
            model_name="restaurant",
            name="active_menu",
            field=models.ForeignKey(
                "menu.Menu",
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                related_name="active_for_restaurants",
            ),
        ),
    ]
```

#### Views & URLs

| URL pattern | View | Purpose |
|---|---|---|
| `/backoffice/menu/` | `menu_dashboard` | Overview with menu count, item count |
| `/backoffice/menu/menus/` | `menu_list` / `menu_create` / `menu_detail` / `menu_update` | Menu CRUD (with inline formset for MenuItems) |
| `/backoffice/menu/items/` | `menu_item_list` / `menu_item_create` / `menu_item_update` | Standalone MenuItem CRUD (optional — mostly managed via Menu inline) |
| `/backoffice/menu/add-ons/` | `add_on_list` / `add_on_create` / `add_on_update` / `add_on_delete` | ItemAddOn CRUD |
| `/backoffice/menu/variants/` | `variant_list` / `variant_create` / `variant_update` / `variant_delete` | ItemVariant CRUD |
| `/backoffice/menu/price-lists/` | `price_list_list` / `price_list_detail` | PriceList read-only views |

#### Tests

| Test file | What it covers |
|---|---|
| `test_menu.py` | CRUD, enabled toggle, sync_price_list creates PriceList + ItemPrice rows |
| `test_menu_item.py` | CRUD, rate default from item.last_purchase_rate, item_name sync, disabled filter, special_dish flag |
| `test_price_list.py` | Auto-creation from Menu, ItemPrice sync, unique constraint |
| `test_item_add_on.py` | CRUD, unique constraint, validation that add_on_item must be in a Menu |
| `test_item_variant.py` | CRUD, unique constraint, validation that variant_item must be in a Menu |
| `test_views.py` | Login required, list/create/update views, HTMX responses |

#### Deviations from reference

| Deviation | Reason |
|---|---|
| No `room_wise_menu` or `order_type_wise_menu` | FEATURES.md #7: single menu for all rooms/order types |
| PriceList/ItemPrice kept but POS reads from MenuItem.rate | Matches URY pattern |
| `department` not on MenuItem | Already on inventory.Item — inherited via FK |
| ItemAddOn/ItemVariant are standalone models, not child tables on Item | Django ORM pattern — cleaner queries than child tables |
| Branch kept in DB but hidden in the UI across Menu/Room/Warehouse/Restaurant (auto `Branch.get_default()`); Table & UserRoomAssignment derive branch from room | Single-site restaurant; multi-branch isolation remains available without cashier-facing branch pickers |

---

### 6.4 Payments Core App (Phase 4)

**Status:** complete — implemented and merged on `main`. (Per §6.14, later revisions:
`ModeOfPayment` gained `is_default` — exactly one True; `PaymentGLMapping` became OneToOne per
mode with the `company` field dropped.)
**FEATURES.md sections:** A10 (partial — modes and GL mapping only)
**Dependencies:** None (standalone). Promoted ahead of Staff because `OpeningPayment.mode_of_payment`
is a FK to `ModeOfPayment`.
**Key models:** `ModeOfPayment`, `PaymentGLMapping`
**Reference doctypes to consult:** ERPNext Mode of Payment, Mode of Payment Account

#### Reference files consulted

| Reference file | What was extracted |
|---|---|
| `references/erpnext-develop/erpnext/accounts/doctype/mode_of_payment/mode_of_payment.json` | Fields: `mode_of_payment` (name), `type` (Cash/Bank/General/Phone), `enabled` |
| `references/erpnext-develop/erpnext/accounts/doctype/mode_of_payment/mode_of_payment.py` | Empty `Document` class — no validation logic |
| `references/erpnext-develop/erpnext/accounts/doctype/mode_of_payment_account/mode_of_payment_account.json` | Child-table fields: `company`, `default_account` |
| `references/erpnext-develop/erpnext/selling/doctype/pos_profile/pos_profile.json` | How POS Profile lists allowed payment methods per terminal |
| `references/ury-develop/ury/ury_pos/api.py` | How URY resolves `mode_of_payment` against the active POS profile |

#### Decisions

- **Flat master, no tree.** Modes are categorised by a `type` field, not nested under a parent mode.
- **`PaymentGLMapping.default_account` is a CharField, not a Link→Account.** There is no
  `LedgerAccount` model — that lives in Phase 8 (accounting / GL). We store the account name as a
  string here and migrate to a FK when the chart of accounts is introduced.
- **`company` is a CharField** read from `Restaurant.company` (single-company). No per-company
  switcher.
- **No `is_change` flag.** A mode of payment's ability to dispense physical change is inferred
  from `type == "CASH"`. The Order app (Phase 7) handles change calculation, not here.
- **Simple `enabled` toggle.** Disabled modes are hidden from the opening-balance form on the
  staff app while remaining valid historical references on past opening entries. ERPNext hides
  modes via the POS Profile's `payments` child table; Spicy has no POS Profile yet, so we keep
  the toggle on the master.

#### Models (2 total)

All models extend `apps.utils.models.BaseModel`.

##### `ModeOfPayment` (`payments.ModeOfPayment`)

| Field | Type | Notes |
|---|---|---|
| `name` | CharField, max_length=50, unique | e.g. "Cash", "Bank Transfer", "Opay Transfer" |
| `type` | CharField, max_length=10, choices: CASH / BANK / GENERAL / PHONE | required — #94 |
| `enabled` | BooleanField, default=True | disabled modes hidden from new opening entries |

**Methods:**
- `__str__` returns `name`
- Meta: `ordering = ["name"]`

**Seed data (migration 0002):** Cash (CASH), Bank Transfer (BANK), Card (BANK), USSD / Mobile Money (PHONE).
The manager can add specific providers (Opay, Moniepoint, FirstBank POS) or disable unused ones.

##### `PaymentGLMapping` (`payments.PaymentGLMapping`)

| Field | Type | Notes |
|---|---|---|
| `mode_of_payment` | ForeignKey→`payments.ModeOfPayment`, on_delete=PROTECT, related_name="gl_mappings" | required |
| `company` | CharField, max_length=200 | defaults from `Restaurant.company`; single-company |
| `default_account` | CharField, max_length=200 | account name as string — Phase 8 will FK to a real `LedgerAccount` |

**Methods:**
- `__str__` returns `f"{mode_of_payment.name} → {default_account}"`
- `clean()`: default `company` from `Restaurant.objects.first().company` if blank
- Meta: `unique_together = [("mode_of_payment", "company")]`, `ordering = ["mode_of_payment__name"]`

#### Views & URLs

Function-based views, `@login_required`, protected by `BackofficeAccessMiddleware` via the
`/backoffice/...` path prefix. HTMX partials return `"<template>.html#<partialdef>"`.

| URL pattern | View | Purpose |
|---|---|---|
| `/backoffice/payments/` | `payments_dashboard` | Overview: mode count, GL mapping count, disabled count |
| `/backoffice/payments/modes/` | `mode_list` | List modes with HTMX partial for rows |
| `/backoffice/payments/modes/create/` | `mode_create` | Create mode |
| `/backoffice/payments/modes/<int:pk>/` | `mode_detail` | View mode + its GL mappings inline |
| `/backoffice/payments/modes/<int:pk>/edit/` | `mode_update` | Update mode |
| `/backoffice/payments/gl-mappings/` | `gl_mapping_list` | List GL mappings |
| `/backoffice/payments/gl-mappings/create/` | `gl_mapping_create` | Create mapping |
| `/backoffice/payments/gl-mappings/<int:pk>/edit/` | `gl_mapping_update` | Edit mapping |
| `/backoffice/payments/gl-mappings/<int:pk>/delete/` | `gl_mapping_delete` | Remove mapping (`@require_POST`) |

#### Templates

| Template | Purpose |
|---|---|
| `templates/backoffice/payments/dashboard.html` | Overview landing page |
| `templates/backoffice/payments/mode_list.html` with `{% partialdef mode-row %}` + `{% partialdef mode-rows %}` | List with HTMX partials |
| `templates/backoffice/payments/mode_form.html` with `{% partialdef form inline %}` | Create/edit mode |
| `templates/backoffice/payments/mode_detail.html` | Show mode + GL mappings inline |
| `templates/backoffice/payments/gl_mapping_list.html` | List mappings with `{% partialdef gl-mapping-row %}` |
| `templates/backoffice/payments/gl_mapping_form.html` | Create/edit mapping |

#### Forms

| Form | Model | Notes |
|---|---|---|
| `ModeOfPaymentForm` | `ModeOfPayment` | Fields: name, type, enabled |
| `PaymentGLMappingForm` | `PaymentGLMapping` | `mode_of_payment` queryset via `active_choices(ModeOfPayment, self.instance.mode_of_payment_id, enabled=True)`; `company` defaulted in `__init__` from `Restaurant.objects.first().company` |

Both extend `apps.utils.forms.StyledModelForm` via a `PaymentsModelForm(StyledModelForm)` base.

#### Admin

```python
@admin.register(ModeOfPayment)
class ModeOfPaymentAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "enabled", "created_at")
    list_filter = ("type", "enabled")
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(PaymentGLMapping)
class PaymentGLMappingAdmin(admin.ModelAdmin):
    list_display = ("mode_of_payment", "company", "default_account", "created_at")
    list_filter = ("company",)
    list_select_related = ("mode_of_payment",)
    search_fields = ("mode_of_payment__name", "default_account")
    ordering = ("mode_of_payment__name",)
```

#### Tests

| Test file | What it covers |
|---|---|
| `test_mode_of_payment.py` | CRUD, type choices, `enabled` flag, unique name, seed migration creates 4 defaults |
| `test_payment_gl_mapping.py` | CRUD, `unique_together(mode, company)`, company defaulting from `Restaurant`, PROTECT on mode delete |
| `test_views.py` | Login required, list/create/update/delete, HTMX partial responses, `@require_POST` on delete (GET → 405) |

#### Deviations from reference

| Deviation | Reason |
|---|---|
| `default_account` is CharField, not Link→Account | No `LedgerAccount` model; Phase 8 introduces one and migrates to FK |
| `company` is CharField, not Link→Company | No Company model — single `Restaurant` singleton per branch with a company name string |
| No per-warehouse or per-branch isolation on modes | The project is single-branch; modes are shared restaurant-wide |
| No `is_change` flag | Change capability inferred from `type == "CASH"`; only cash modes dispense physical notes |
| Simple `enabled` toggle on mode | ERPNext hides modes via POS Profile's `payments` child table. Spicy has no POS Profile yet, so the toggle lives on the master. |
| Seed includes "USSD / Mobile Money" (PHONE) | Common in Nigerian restaurant context; matches #94 ("Phone (mobile money / USSD)") |

#### Implementation steps

1. Create the app: `make uv run 'pegasus startapp payments ModeOfPayment PaymentGLMapping'`
2. Write models in `apps/payments/models.py`
3. Write forms in `apps/payments/forms.py`
4. Write views in `apps/payments/views.py`
5. Write URLs in `apps/payments/urls.py`
6. Register in `apps/payments/admin.py`
7. Add `apps.payments` to `INSTALLED_APPS` in `spicy/settings.py` (after `apps.menu`)
8. Include payments URLs in `spicy/urls.py`
9. Write templates in `templates/backoffice/payments/`
10. Create and run migrations: `make migrations && make migrate`
11. Write seed migration `0002_seed_payment_modes.py` — creates Cash, Bank Transfer, Card, USSD
12. Write tests: `apps/payments/tests/`
13. Run tests: `make test ARGS='apps.payments'`
14. Run lint: `make ruff`

---

### 6.5 Staff App (Phase 5)

**Status:** complete — implemented and merged on `main`. (The plan below is the original
Phase 5 design; §6.14 later removed the `branch` and `pos_profile` FKs — shifts are now
global, one open shift at a time. `POSClosingEntry.submit()` now sums order payments per mode,
see §4 staff summary.)
**FEATURES.md sections:** A9 (POS session / cashier shift), A17 (role enforcement on shift ops;
the role-group conventions themselves already live in `apps/users` and the settings-app staff list)
**Dependencies:** settings, payments core (ModeOfPayment)
**Key models:** `POSOpeningEntry`, `OpeningPayment`, `POSClosingEntry`, `ClosingPayment`
**Reference doctypes to consult:** ERPNext POS Opening Entry + Detail, POS Closing Entry + Detail +
Taxes, URY User, Role Permitted, URY hooks for opening/closing validation

#### Reference files consulted

| Reference file | What was extracted |
|---|---|
| `references/erpnext-develop/erpnext/accounts/doctype/pos_opening_entry/pos_opening_entry.json` | Submittable doctype — fields: period_start_date, period_end_date, posting_date, company, pos_profile, user, balance_details, status, amended_from |
| `references/erpnext-develop/erpnext/accounts/doctype/pos_opening_entry/pos_opening_entry.py` | `validate_pos_profile_and_cashier`, `check_open_pos_exists`, `check_user_already_assigned`, `validate_payment_method_account`, submit/cancel logic, `check_poe_is_cancellable` |
| `references/erpnext-develop/erpnext/accounts/doctype/pos_opening_entry_detail/pos_opening_entry_detail.json` | Child table: `mode_of_payment` (Link), `opening_amount` (Currency) |
| `references/erpnext-develop/erpnext/accounts/doctype/pos_closing_entry/pos_closing_entry.json` | Submittable — fields: period dates, posting_date/time, pos_opening_entry (Link), pos_invoices, sales_invoices, taxes, totals, payment_reconciliation, status, amended_from |
| `references/erpnext-develop/erpnext/accounts/doctype/pos_closing_entry/pos_closing_entry.py` | `validate_pos_opening_entry`, `validate_duplicate_pos_invoices`, `validate_pos_invoices`, `on_submit` (consolidates), `on_cancel` (unconsolidates, does NOT reopen opening), `get_invoices` |
| `references/erpnext-develop/erpnext/accounts/doctype/pos_closing_entry_detail/pos_closing_entry_detail.json` | Child table: mode_of_payment, opening_amount, expected_amount, closing_amount, difference |
| `references/erpnext-develop/erpnext/controllers/status_updater.py` (lines 150–162) | Derived `status` rules for both doctypes: opening `Draft→Open→Closed→Cancelled` keyed on `docstatus` + presence of `pos_closing_entry`; closing `Draft→Submitted→Queued/Failed→Cancelled` |
| `references/ury-develop/ury/ury/doctype/ury_user/ury_user.json` | Child table of URY User (user + room) on Branch — single shared session per branch |
| `references/ury-develop/ury/ury/hooks/ury_pos_opening_entry.py` | `set_cashier_room`, `main_pos_open_check` (sub-cashier must wait for main) — **not ported**; Spicy uses single-session model |
| `references/ury-develop/ury/ury/hooks/ury_pos_closing_entry.py` | `calculate_closing_amount`, `validate_cashier` — **not ported**; single-session model |
| `references/ury-develop/ury/ury_pos/api.py` `getPosProfile` / `posOpening` / `pos_opening_check` | How URY exposes shift state to the POS frontend — pattern for `staff_dashboard` view |
| `references/ury-develop/ury/ury/doctype/sub_pos_closing/sub_pos_closing.json` | Sub-cashier close — **not ported**; single-session model |

#### Decisions

- **Single shared session per branch (FEATURES.md #85).** One Open shift per branch; any permitted
  cashier rings sales on it; orders are attributed to whoever is logged in. No multi-cashier mode,
  no `Sub POS Closing`, no main/sub hierarchy. The session is shared; the shift's `cashier` field
  records who opened it for audit.
- **POSProfile FK deferred to Phase 6.** `POSProfile` is built in Phase 6 (settings R2). In Phase 5
  the shift is identified by `(branch, status="SUBMITTED" AND closing_entry IS NULL)`. Phase 6 adds
  a nullable `pos_profile` FK via a migration.
- **Synchronous close only.** No `QUEUED`/`FAILED` statuses, no Celery task, no `error_message`,
  no Retry button. Phase 5's close-time work is a SQL `SUM` across the shift's `OpeningPayment`
  rows plus the cashier-entered `closing_amount` — milliseconds even for 1,000+ orders. Add async
  only when a future phase introduces heavy close-time write work (e.g. Phase 8 accounting
  posting GL entries on close, or a real `Sales Invoice` consolidation).
- **Daily-close enforcement deferred.** The ERPNext `validate_pos_opening_entry` "outdated shift"
  check and URY's `validate_pos_close` 5 AM day boundary live in the Order-app `validate` path
  (Phase 7), not on the opening entry. Mirrors ERPNext's placement in
  `sales_invoice/services/pos.py` and avoids coupling Phase 5 to order-creation concerns.
- **Order FK linkage deferred to Phase 7.** Phase 5 builds the opening/closing shell with totals
  defaulting to 0. Phase 7 (orders) adds `Order.pos_opening_entry` and `Order.pos_closing_entry`
  FKs, and extends `POSClosingEntry.submit()` to sum order payments into `expected_amount`.
- **Derived status via property.** `POSOpeningEntry.is_open` returns `True` iff
  `status == "SUBMITTED" AND closing_entry_id IS NULL`. The "Open" / "Closed" labelling is
  implied by `status` + `closing_entry`, not stored as a separate field.
- **Cancel does NOT reopen the opening entry** (matches ERPNext). Once a closing entry exists,
  the opening entry stays closed. Cancellation of a closing entry is blocked if a new Open shift
  exists for the branch.
- **Manager + Cashier can both open/close.** Whoever is logged in with `has_staff_role` can act.
  Admin can always act. `cashier` field is the user who opened (audit), not a single permitted user.
- **GL-account check on opening balance deferred.** ERPNext requires every `mode_of_payment` in
  the opening balance to have a GL account mapped. Spicy has no `LedgerAccount`, so
  this check is deferred — the manager ensures each enabled mode has a `PaymentGLMapping` row via
  the payments core CRUD before opening a shift.
- **Round two-decimal precision only.** `opening_amount`, `expected_amount`, `closing_amount`,
  `difference` are all `DecimalField(max_digits=12, decimal_places=2)`. The restaurant deals in
  whole naira (see FEATURES #100 — rounding is a Phase 7 / 9 concern).

#### Models (4 total)

All models extend `apps.utils.models.BaseModel`.

##### `POSOpeningEntry` (`staff.POSOpeningEntry`)

| Field | Type | Notes |
|---|---|---|
| `branch` | ForeignKey→`settings.Branch`, on_delete=PROTECT, related_name="pos_opening_entries" | required; auto-set via `Branch.get_default()` in `save()` if blank |
| `period_start_date` | DateTimeField, default=timezone.now, editable=False | shift start |
| `period_end_date` | DateTimeField, null=True, blank=True, editable=False | set when closing entry submits |
| `posting_date` | DateField, default=timezone.localdate | calendar day of shift |
| `cashier` | ForeignKey→`users.CustomUser`, on_delete=PROTECT, related_name="pos_opening_entries" | the user who opened the shift (audit) |
| `closing_entry` | OneToOneField→`staff.POSClosingEntry`, on_delete=SET_NULL, null=True, blank=True, related_name="opening_entry_ref" | set when shift closes — status driver |
| `status` | CharField, max_length=10, choices: DRAFT / SUBMITTED / CANCELLED, default="DRAFT" | submit/cancel workflow |
| `remarks` | TextField, blank=True | optional notes |
| `cancelled_by` | ForeignKey→`users.CustomUser`, on_delete=SET_NULL, null=True, blank=True, related_name="cancelled_opening_entries" | audit |
| `cancelled_at` | DateTimeField, null=True, blank=True, editable=False | audit |

**Methods:**
- `__str__` returns `f"Opening #{self.pk} — {self.branch.name} {self.posting_date}"`
- `save()`: auto-assign `branch` from `Branch.get_default()` if blank; raise `ValidationError` if
  no default branch exists (caller must create a branch first). Follows the `Warehouse` pattern in
  `apps/inventory/models.py`.
- `clean()`:
  - if no other validation, call `super().clean()` first
  - if `status == "SUBMITTED"` and `closing_entry_id` is None: verify no other submitted
    opening entry with a null closing entry exists for the same branch (one Open shift per branch)
- `is_open` → `@property` returning `self.status == "SUBMITTED" and self.closing_entry_id is None`
- `is_closed` → `@property` returning `self.status == "SUBMITTED" and self.closing_entry_id is not None`
- `submit()`: idempotency guard `if self.status != "DRAFT": return`; set `status = "SUBMITTED"`;
  `self.save(update_fields=["status", "updated_at"])`. No side effects — no SLE creation, no
  revenue posting. The shift is now "Open" and any permitted cashier can ring sales on it (the
  Order-app validate path enforces the "exactly one Open per branch" rule at order-create time).
- `cancel()`: idempotency guard `if self.status != "DRAFT": return`; set `status = "CANCELLED"`,
  `cancelled_at = timezone.now()`; caller is responsible for setting `cancelled_by` (the view
  passes the current user). Cannot cancel a shift that has a `closing_entry` (closed shifts are
  immutable — matches ERPNext). The view raises a `ValidationError` if `closing_entry_id` is not
  None.

**Meta:** `ordering = ["-period_start_date"]`.

##### `OpeningPayment` (`staff.OpeningPayment`) — child table

| Field | Type | Notes |
|---|---|---|
| `opening_entry` | ForeignKey→`POSOpeningEntry`, on_delete=CASCADE, related_name="opening_payments" | required |
| `mode_of_payment` | ForeignKey→`payments.ModeOfPayment`, on_delete=PROTECT, related_name="opening_payments" | required |
| `opening_amount` | DecimalField, max_digits=12, decimal_places=2, default=0 | the float entered by the cashier |

**Methods:**
- `__str__` returns `f"{mode_of_payment.name}: {opening_amount}"`
- Meta: `unique_together = [("opening_entry", "mode_of_payment")]`,
  `ordering = ["mode_of_payment__name"]`

##### `POSClosingEntry` (`staff.POSClosingEntry`)

| Field | Type | Notes |
|---|---|---|
| `branch` | ForeignKey→`settings.Branch`, on_delete=PROTECT, related_name="pos_closing_entries" | denormalized from opening_entry for query efficiency |
| `opening_entry` | OneToOneField→`POSOpeningEntry`, on_delete=PROTECT, related_name="closing_entry_for" | required; validated to be `is_open` in `clean()` |
| `period_start_date` | DateTimeField, editable=False | denormalized from opening_entry |
| `period_end_date` | DateTimeField, default=timezone.now | when the close is being submitted |
| `posting_date` | DateField, default=timezone.localdate | calendar day of close |
| `cashier` | ForeignKey→`users.CustomUser`, on_delete=PROTECT, related_name="pos_closing_entries" | user submitting the close |
| `total_quantity` | DecimalField, max_digits=12, decimal_places=2, default=0, editable=False | Phase 7 backfills from Order totals |
| `net_total` | DecimalField, max_digits=14, decimal_places=2, default=0, editable=False | Phase 7 backfills |
| `total_taxes` | DecimalField, max_digits=14, decimal_places=2, default=0, editable=False | Phase 7 backfills |
| `grand_total` | DecimalField, max_digits=14, decimal_places=2, default=0, editable=False | Phase 7 backfills |
| `total_short_excess` | DecimalField, max_digits=14, decimal_places=2, default=0, editable=False | sum of `difference` across all `ClosingPayment` rows |
| `status` | CharField, max_length=10, choices: DRAFT / SUBMITTED / CANCELLED, default="DRAFT" | |
| `remarks` | TextField, blank=True | |
| `cancelled_by` | ForeignKey→`users.CustomUser`, on_delete=SET_NULL, null=True, blank=True, related_name="cancelled_closing_entries" | audit |
| `cancelled_at` | DateTimeField, null=True, blank=True, editable=False | audit |

**Methods:**
- `__str__` returns `f"Closing #{self.pk} — {self.branch.name} {self.posting_date}"`
- `save()`: if `opening_entry_id` is set, auto-assign `branch`, `period_start_date`, and
  `posting_date` from it. Avoids the user re-entering values the opening already has.
- `clean()`:
  - call `super().clean()` first
  - validate `opening_entry.is_open` is True (cannot close a draft, cancelled, or already-closed
    opening). Use field-keyed `ValidationError({...})` for form binding.
  - validate `self.branch_id == self.opening_entry.branch_id`
- `submit()`: see "Business logic" below.
- `cancel()`: idempotency guard; raise `ValidationError` if a new Open shift exists for this
  branch (caller passes the field-keyed dict). Set `status = "CANCELLED"`, `cancelled_at = now()`.
  **Does NOT reopen the opening entry** — matches ERPNext.

**Meta:** `ordering = ["-period_end_date"]`.

##### `ClosingPayment` (`staff.ClosingPayment`) — child table

| Field | Type | Notes |
|---|---|---|
| `closing_entry` | ForeignKey→`POSClosingEntry`, on_delete=CASCADE, related_name="closing_payments" | required |
| `mode_of_payment` | ForeignKey→`payments.ModeOfPayment`, on_delete=PROTECT, related_name="closing_payments" | required |
| `opening_amount` | DecimalField, max_digits=12, decimal_places=2, default=0, editable=False | denormalized from the matching `OpeningPayment` |
| `expected_amount` | DecimalField, max_digits=12, decimal_places=2, default=0, editable=False | Phase 5: `= opening_amount`. Phase 7: `+ sum of order payments in this method` |
| `closing_amount` | DecimalField, max_digits=12, decimal_places=2, default=0 | the cashier's counted amount — only editable field on the row |
| `difference` | DecimalField, max_digits=12, decimal_places=2, default=0, editable=False | `= closing_amount − expected_amount` |

**Methods:**
- `__str__` returns `f"{mode_of_payment.name}: closing {closing_amount} / expected {expected_amount}"`
- `clean()`: call `super().clean()` first; if `closing_entry_id` and `opening_entry_id` are both
  set, validate `mode_of_payment` exists in `closing_entry.opening_entry.opening_payments` (the
  mode must be one declared at shift-open time).
- Meta: `unique_together = [("closing_entry", "mode_of_payment")]`,
  `ordering = ["mode_of_payment__name"]`

#### Business logic — `POSOpeningEntry.submit()`

```python
def submit(self):
    """Mark the opening entry as submitted (the shift is now Open)."""
    if self.status != "DRAFT":
        return
    self.status = "SUBMITTED"
    self.save(update_fields=["status", "updated_at"])
```

#### Business logic — `POSOpeningEntry.cancel()`

```python
def cancel(self, by_user=None):
    """Cancel a draft or open shift. Cannot cancel a closed shift (closing_entry set)."""
    if self.status != "DRAFT":
        return
    self.status = "CANCELLED"
    self.cancelled_at = timezone.now()
    if by_user is not None:
        self.cancelled_by = by_user
    self.save(update_fields=["status", "cancelled_at", "cancelled_by", "updated_at"])
```

#### Business logic — `POSClosingEntry.submit()`

```python
def submit(self):
    """Compute expected amounts, validate, then mark the opening entry as closed."""
    if self.status != "DRAFT":
        return
    opening_modes = {op.mode_of_payment_id: op for op in self.opening_entry.opening_payments.all()}
    for cp in self.closing_payments.all():
        if cp.mode_of_payment_id not in opening_modes:
            raise ValidationError({
                "mode_of_payment": f"{cp.mode_of_payment} was not declared at shift open.",
            })
        cp.opening_amount = opening_modes[cp.mode_of_payment_id].opening_amount
        cp.expected_amount = cp.opening_amount  # Phase 7 will add: + sum(OrderPayment) for this method
        cp.difference = cp.closing_amount - cp.expected_amount
        cp.save(update_fields=["opening_amount", "expected_amount", "difference", "updated_at"])
    self.total_short_excess = sum((cp.difference for cp in self.closing_payments.all()), Decimal("0"))
    self.status = "SUBMITTED"
    self.save(update_fields=["total_short_excess", "status", "updated_at"])
    # Flip opening entry to Closed
    self.opening_entry.closing_entry = self
    self.opening_entry.period_end_date = self.period_end_date
    self.opening_entry.save(update_fields=["closing_entry", "period_end_date", "updated_at"])
```

#### Business logic — `POSClosingEntry.cancel()`

```python
def cancel(self, by_user=None):
    """Cancel a submitted closing entry. Does NOT reopen the opening entry (matches ERPNext).
    Blocked if a new Open shift exists for this branch — a fresh shift would already
    reference its own opening entry, and unconsolidating an old close mid-shift would
    corrupt the new session's accounting.
    """
    if self.status != "DRAFT":
        return
    # Check no new Open shift for this branch
    new_open_exists = POSOpeningEntry.objects.filter(
        branch=self.branch,
        status="SUBMITTED",
        closing_entry__isnull=True,
    ).exclude(pk=self.opening_entry_id).exists()
    if new_open_exists:
        raise ValidationError({
            "branch": (
                f"Cannot cancel this closing entry — a new shift is open for {self.branch.name}. "
                "Close or cancel the new shift first."
            ),
        })
    self.status = "CANCELLED"
    self.cancelled_at = timezone.now()
    if by_user is not None:
        self.cancelled_by = by_user
    self.save(update_fields=["status", "cancelled_at", "cancelled_by", "updated_at"])
    # Note: opening entry stays Closed (closing_entry_id still set) — matches ERPNext.
```

#### Circular FK handling

`POSOpeningEntry.closing_entry` (`OneToOneField→POSClosingEntry`) and
`POSClosingEntry.opening_entry` (`OneToOneField→POSOpeningEntry`) form a circular dependency.
Django handles this with string references and `related_name` — declare both fields normally and
the migration generates two `AddField` operations in the order Django can resolve (one FK added in
each model's initial migration, the other added in a follow-up migration that depends on the
first). Pattern: define `POSOpeningEntry` first with `closing_entry` as a nullable OneToOneField
using `to="staff.POSClosingEntry"` (string), then define `POSClosingEntry` with
`opening_entry = OneToOneField(POSOpeningEntry, ...)`. Tests assert the bidirectional linkage
with `opening.closing_entry == closing` and `closing.opening_entry == opening`.

#### Views & URLs

Function-based views, `@login_required`, protected by `BackofficeAccessMiddleware` via the
`/backoffice/...` path prefix. Shift-management views also require `request.user.has_staff_role`
(view-level guard returning 302 → `web:pending_approval` if false). Mutating endpoints
(`submit`/`cancel`/`create`/`update`/`delete`) use `@require_POST`.

| URL pattern | View | Purpose |
|---|---|---|
| `/backoffice/staff/` | `staff_dashboard` | Overview: current shift state per branch (Open / Closed / None), recent closes |
| `/backoffice/staff/opening-entries/` | `opening_entry_list` | List with HTMX partial for rows |
| `/backoffice/staff/opening-entries/create/` | `opening_entry_create` | Open a new shift (inline formset for `OpeningPayment` rows) |
| `/backoffice/staff/opening-entries/<int:pk>/` | `opening_entry_detail` | View shift + opening payments + action buttons |
| `/backoffice/staff/opening-entries/<int:pk>/edit/` | `opening_entry_update` | Edit draft shift (no edits after submit) |
| `/backoffice/staff/opening-entries/<int:pk>/submit/` | `opening_entry_submit` | Submit (open the shift) — `@require_POST` |
| `/backoffice/staff/opening-entries/<int:pk>/cancel/` | `opening_entry_cancel` | Cancel draft/open shift — `@require_POST` |
| `/backoffice/staff/closing-entries/` | `closing_entry_list` | List with HTMX partial for rows |
| `/backoffice/staff/closing-entries/create/` | `closing_entry_create` | Start a close (selects the branch's Open opening entry; seeds `ClosingPayment` rows from `OpeningPayment`) |
| `/backoffice/staff/closing-entries/<int:pk>/` | `closing_entry_detail` | View shift close + reconciliation form |
| `/backoffice/staff/closing-entries/<int:pk>/edit/` | `closing_entry_update` | Edit `closing_amount` per payment method (HTMX recompute of `difference` on blur) |
| `/backoffice/staff/closing-entries/<int:pk>/submit/` | `closing_entry_submit` | Submit (close the shift) — `@require_POST` |
| `/backoffice/staff/closing-entries/<int:pk>/cancel/` | `closing_entry_cancel` | Cancel — `@require_POST` |

#### Templates

| Template | Purpose |
|---|---|
| `templates/backoffice/staff/dashboard.html` | Current shift state per branch (Open / Closed / None) + recent closes |
| `templates/backoffice/staff/opening_entry_list.html` with `{% partialdef opening-row %}` + `{% partialdef opening-rows %}` | List with HTMX partials |
| `templates/backoffice/staff/opening_entry_form.html` with `{% partialdef form inline %}` | Create/edit with `OpeningCashFloatForm` (cash-only); renders a no-cash error card if no active CASH-type `ModeOfPayment` exists |
| `templates/backoffice/staff/opening_entry_detail.html` | Show shift + opening payments + Submit/Cancel action buttons |
| `templates/backoffice/staff/closing_entry_list.html` with `{% partialdef closing-row %}` + `{% partialdef closing-rows %}` | List with HTMX partials |
| `templates/backoffice/staff/closing_entry_form.html` | Reconciliation form (one row per `mode_of_payment`; read-only opening/expected/diff; editable closing_amount) |
| `templates/backoffice/staff/closing_entry_detail.html` | Show shift close summary + reconciliation |

#### Forms

| Form | Model | Notes |
|---|---|---|
| `POSOpeningEntryForm` | `POSOpeningEntry` | Fields: `posting_date`, `remarks`. Branch auto-assigned by `save()`. Cashier auto-set in view from `request.user`. |
| `OpeningPaymentForm` | `OpeningPayment` | Fields: `mode_of_payment` (active_choices, `enabled=True`), `opening_amount`. `clean()`: at least one row required; no duplicate modes. |
| `POSClosingEntryForm` | `POSClosingEntry` | Fields: `opening_entry` (filtered to `is_open` entries for the branch). Branch, dates, cashier auto-set in view. |
| `ClosingPaymentForm` | `ClosingPayment` | Fields: `mode_of_payment` (read-only), `opening_amount` (read-only), `expected_amount` (read-only), `closing_amount`, `difference` (read-only). The HTMX `hx-post` on `closing_amount` blur recomputes `difference` server-side. |

Forms extend `apps.utils.forms.StyledModelForm` via a `StaffModelForm(StyledModelForm)` base
(for the closing-entry forms). The opening-entry form is a plain `forms.Form`
(`OpeningCashFloatForm`) that dynamically renders one `DecimalField` per active
CASH-type `ModeOfPayment` — see §6.5 Deviations "Opening-float UI is cash-only".
On submit, `opening_entry_create` / `opening_entry_update` auto-seed
`OpeningPayment` rows for **every** active `ModeOfPayment` (cash modes get the
entered amounts; non-cash modes get `0`), preserving ERPNext's one-row-per-MOP
data model for closing reconciliation. The closing-form seed step (creating one
`ClosingPayment` row per `OpeningPayment` row on the opening) happens in the
`closing_entry_create` view after the parent form is saved.

#### Admin

```python
@admin.register(POSOpeningEntry)
class POSOpeningEntryAdmin(admin.ModelAdmin):
    list_display = ("pk", "branch", "cashier", "posting_date", "period_start_date", "period_end_date", "status", "closing_entry")
    list_filter = ("branch", "status", "posting_date")
    list_select_related = ("branch", "cashier", "closing_entry")
    search_fields = ("pk", "cashier__username", "remarks")
    readonly_fields = ("period_start_date", "period_end_date", "cancelled_at", "created_at", "updated_at")
    date_hierarchy = "posting_date"
    ordering = ("-period_start_date",)


@admin.register(OpeningPayment)
class OpeningPaymentAdmin(admin.ModelAdmin):
    list_display = ("opening_entry", "mode_of_payment", "opening_amount")
    list_filter = ("mode_of_payment",)
    list_select_related = ("opening_entry", "mode_of_payment")
    search_fields = ("opening_entry__pk", "mode_of_payment__name")
    ordering = ("opening_entry__pk", "mode_of_payment__name")


@admin.register(POSClosingEntry)
class POSClosingEntryAdmin(admin.ModelAdmin):
    list_display = ("pk", "branch", "cashier", "opening_entry", "period_end_date", "status", "total_short_excess", "grand_total")
    list_filter = ("branch", "status", "posting_date")
    list_select_related = ("branch", "cashier", "opening_entry")
    search_fields = ("pk", "opening_entry__pk", "remarks")
    readonly_fields = (
        "period_start_date", "total_quantity", "net_total", "total_taxes",
        "grand_total", "total_short_excess", "cancelled_at", "created_at", "updated_at",
    )
    date_hierarchy = "posting_date"
    ordering = ("-period_end_date",)


@admin.register(ClosingPayment)
class ClosingPaymentAdmin(admin.ModelAdmin):
    list_display = ("closing_entry", "mode_of_payment", "opening_amount", "expected_amount", "closing_amount", "difference")
    list_filter = ("mode_of_payment",)
    list_select_related = ("closing_entry", "mode_of_payment")
    search_fields = ("closing_entry__pk", "mode_of_payment__name")
    ordering = ("closing_entry__pk", "mode_of_payment__name")
```

#### Tests

| Test file | What it covers |
|---|---|
| `test_pos_opening_entry.py` | CRUD, `is_open` / `is_closed` properties, submit/cancel idempotency guards, "one Open shift per branch" `clean()` enforcement, branch auto-default in `save()` |
| `test_opening_payment.py` | Child rows, `unique_together(entry, mode)`, PROTECT on mode delete, queryset ordering |
| `test_pos_closing_entry.py` | CRUD, auto-fill from opening on `save()`, `clean()` opening-must-be-Open check, submit flow (computes `expected_amount` = `opening_amount`, sets `difference`, marks opening closed), cancel blocked by new open shift, cancel does NOT reopen opening |
| `test_closing_payment.py` | `difference` recomputed on submit, `clean()` validates mode matches an opening payment, `unique_together(entry, mode)` |
| `test_views.py` | Login required, role enforcement (`has_staff_role`), HTMX partial responses for list rows, `@require_POST` for submit/cancel (GET → 405), reconciliation-form seed step, hidden branch (no branch picker), opening-edit blocked after submit, dashboard reflects current shift state |
| `test_circular_fk.py` | `opening.closing_entry == closing` and `closing.opening_entry == opening` (the bidirectional linkage survives round-trips) |

#### Deviations from reference

| Deviation | Reason |
|---|---|
| No `pos_profile` FK on `POSOpeningEntry` | `POSProfile` is Phase 6 (settings R2). Phase 5 identifies shifts by branch alone for the single-site restaurant. Phase 6 migration adds the nullable FK. |
| No multi-cashier mode, no `Sub POS Closing` | FEATURES.md #85: one shared session per branch, shared across users. URY's main+sub hierarchy is overkill for a single shared session. |
| No `QUEUED`/`FAILED` statuses, no async consolidation, no Celery task, no `error_message`, no Retry button | Phase 5's close-time work is a SQL `SUM` across `OpeningPayment` rows + a cashier-entered `closing_amount` — milliseconds even for 1,000+ orders. Add async only when a future phase introduces heavy close-time write work. |
| No daily-close "outdated shift" / "5 AM day boundary" check | Lives in the Order-app `validate` path (Phase 7), not on the opening entry. Mirrors ERPNext's placement in `sales_invoice/services/pos.py` and avoids coupling Phase 5 to order-creation concerns. |
| No `pos_invoices` / `sales_invoices` child table on `POSClosingEntry` | `Order` is Phase 7. Phase 5's closing entry has the `total_*` summary fields defaulting to 0; Phase 7 backfills them via computation on close. |
| `closing_entry` is a `OneToOneField`, not ERPNext's `Data` field | Django supports the FK cleanly. ERPNext used `Data` because Frappe uses the field's presence as a status driver for the parent opening's `Open→Closed` transition. In Django we set the FK in `closing_entry.submit()`. |
| Cancel of opening entry allowed iff no `closing_entry` exists (simpler than ERPNext's "no unconsolidated invoices" check) | No invoices exist before Phase 7. |
| `cashier` = creating user (audit), not a single permitted user | Single-session per branch means whoever's logged in with a staff role can act; `cashier` is who opened the shift, recorded for audit. |
| Manager + Cashier roles can both open/close | Single-session model — whoever is logged in with `has_staff_role` acts. Admin can always act. |
| GL account validation on opening balance deferred | No `LedgerAccount` model; the manager ensures each enabled `ModeOfPayment` has a `PaymentGLMapping` row via the payments core CRUD before opening a shift. |
| `expected_amount` in Phase 5 = `opening_amount` only | Phase 7 extends `POSClosingEntry.submit()` to add Σ order payments in the same mode. The Phase 5 plan explicitly notes the extension point. |
| `period_start_date` defaults to `timezone.now()` on creation, not on submit | The cashier expects the shift to start when they hit "Open", not when they finished typing the form. Matches ERPNext's `pos_opening_entry.js` `period_start_date: now_datetime()` on form load. |
| **Opening-float form: all-methods with 0 default.** `OpeningFloatForm` renders one `DecimalField` per active `ModeOfPayment` (CASH-type sorted first), every field pre-filled with `0.00` and `required=False`. This is a faithful carbon-copy of ERPNext's Desk JS pre-population (`pos_opening_entry.js` lines 42-54, which adds one row per configured payment method with `opening_amount=0`) and the Frappe docs ("Opening balances for other payment methods (e.g., Card, UPI, Wallet) can be entered if applicable"). The cashier typically only fills the cash drawer count; electronic fields are left at 0 when the bank/POS balance is not accessible at shift-open time, and can be overridden with the actual opening balance when it is. The ERPNext data model is preserved exactly — `OpeningPayment` has one row per configured `ModeOfPayment` so `POSClosingEntry.submit()` (which iterates `entry.opening_payments.all()`) works unchanged. `posting_date` (defaults to today on the model) and `remarks` (blank by default) are no longer exposed in the cashier-facing form. If no active `ModeOfPayment` exists at all, the form renders a no-modes error card and blocks shift open. | (1) Cashier friction: the original ERPNext Desk UI shows an editable table with a mode-of-payment dropdown per row, requiring the cashier to add/remove rows manually. A pre-populated grid with one numeric input per active mode is faster and eliminates the duplicate-mode / missing-mode risk of free-form rows. (2) Field validation (`min_value=0`, `step=0.01`, `inputmode=decimal`) gives mobile-friendly numeric input without needing the custom `OpeningPaymentForm`+`OpeningPaymentFormSet` machinery — the per-row mode is rendered as a display label, not a FK dropdown, because the cashier is filling amounts for pre-determined modes, not choosing which modes to declare. (3) All fields default to 0 and are `required=False` so a POST with no entered amounts doesn't fail form validation but creates fully-reconcilable rows — matches ERPNext's `reqd: 1 + default: "0"` semantics (a row must exist, 0 is a valid amount). |
| **Closing flow: auto-create-or-reuse draft, inline-edit detail page, no manual shift selection.** Both ERPNext Desk (`pos_closing_entry.js` lines 5-9 — `frm.set_query("pos_opening_entry", ...)` filtered to `status="Open", docstatus=1`) and URY's `sub_pos_closing.js` (lines 37-39, same filter + `user=session.user`) make the cashier manually pick which open shift to close from a filtered Link dropdown. Spicy **does not** — `closing_entry_create` is now a GET-only endpoint that immediately auto-creates (or reuses an existing) DRAFT `POSClosingEntry` for the single Open shift and redirects to its detail page. `select_for_update()` on the open shift serialises concurrent double-clicks on the "Close Shift" button so they cannot create duplicate drafts; a second GET when a DRAFT already exists just redirects to it. The detail page then renders the reconciliation table as an **inline-editable form** (POSTs back to the same detail URL — PRG pattern) for DRAFT entries, and a read-only `Difference` column for SUBMITTED/CANCELLED entries. The "Submit & Close Shift" button is wired to a SweetAlert confirmation dialog (`data-confirm-title` / `data-confirm-body` / `data-confirm-button` attributes — existing pattern used in `opening_entry_detail.html`, `gl_mapping_list.html`, `staff_list.html`) so the cashier must explicitly confirm before the close finalises. The separate `closing_entry_update` view + URL are removed; `closing_entry_form.html` and `closing_entry_reconcile.html` templates are deleted. The "Close Shift" action is available from the staff dashboard (existing), the opening-entry list (new `Close shift` link on `is_open` rows), and the closing-entry list (detail view). | (1) Spicy enforces "one Open shift per branch" (see `POSOpeningEntry.clean()` + `submit()` re-check inside `select_for_update`), so a dropdown of open shifts is a list-of-one and pure friction. ERPNext/URY require manual selection only because their architecture permits multi-open-shifts per user and multi-cashier per profile — neither applies here. (2) Industry consensus for single-shift-per-register POS systems (Lightspeed S-Series, Dynamics 365 Commerce `Tender declaration`→`Close shift`, StoreHub, ConnectPOS) is one-click close against the current shift, no selection step. (3) The auto-reuse-existing-draft guard prevents the double-click-on-`Close-Shift` race and the page-refresh-after-creating-draft race from leaking orphan drafts. (4) Folding the edit form into the detail page (one page instead of two) halves click count and matches the existing `opening_entry_form.html` symmetry. (5) The SweetAlert confirmation on submit honours ERPNext's submit-then-immutable pattern (the closing entry cannot be edited after submit, only cancelled) — the cashier explicitly agrees before the irreversibility kicks in. |
| **Opening flow: inline-edit detail page for DRAFT, no separate edit form.** Symmetric with the closing-flow change. `opening_entry_detail` now accepts POST for DRAFT entries (re-uses `_save_opening_entry` to persist the edited amounts via the same `OpeningFloatForm`), renders the float table as an inline-editable form for DRAFT entries (POST back to the same detail URL — PRG), and a read-only two-column table for SUBMITTED/CANCELLED/Open entries. The separate `opening_entry_update` view + URL are removed; `opening_entry_form.html` is kept ONLY for `opening_entry_create` (the "Open Shift" action on the dashboard creates the initial draft, then redirects to the detail page for editing — same pattern as `closing_entry_create`). The "Submit & Open Shift" button is now wired to a SweetAlert confirmation dialog (symmetry with "Submit & Close Shift"). The legacy `confirm_empty` branch in `opening_entry_submit` is removed because `_save_opening_entry` now always seeds one row per active `ModeOfPayment` — a draft with no rows only exists if no modes are configured, in which case the create form blocks it at the form level. | (1) Symmetry: closing detail already had inline editing; opening detail now matches — both draft pages edit in place, both submitted pages are read-only. (2) Cuts one navigation hop per draft edit (no separate Edit button + form page). (3) SweetAlert confirmation on submit mirrors the closing submit — the cashier explicitly confirms before the irreversible shift-open. (4) The `confirm_empty` path was dead code — removing it eliminates an untestable branch. |
| **Closing reconciliation: clarify `expected_amount` column to the cashier.** A tooltip is rendered on both the closing-detail page and the opening-detail page's closing-reconciliation table explaining that `Expected = Opening + collected sales during the shift (there is no order tracking yet, so expected equals opening)`, and `Difference = closing − expected` (negative = short, positive = excess). | Without this, cashiers see `expected = opening` and assume it's a bug (it's not — it's correct for Phase 5's scope; Phase 7's `POSClosingEntry.submit()` extension adds Σ collected sales per method, making `expected ≠ opening` and `difference ≈ 0` for honest shifts). The tooltip makes the Phase 5 / Phase 7 expansion point visible to the user, not just an internal PLAN.md note. |

#### Implementation steps

1. Create the app: `make uv run 'pegasus startapp staff POSOpeningEntry POSClosingEntry OpeningPayment ClosingPayment'`
2. Write models in `apps/staff/models.py` (handle the circular FK with string references)
3. Write forms in `apps/staff/forms.py` (`OpeningFloatForm` — see §6.5 Deviations; `ClosingPaymentForm` for inline detail-page edit)
4. Write views in `apps/staff/views.py` (with `has_staff_role` guard, `@require_POST` for mutating endpoints, HTMX partial returns)
5. Write URLs in `apps/staff/urls.py`
6. Register in `apps/staff/admin.py`
7. Add `apps.staff` to `INSTALLED_APPS` in `spicy/settings.py` (after `apps.payments`)
8. Include staff URLs in `spicy/urls.py`
9. Write templates in `templates/backoffice/staff/`
10. Create and run migrations: `make migrations && make migrate`
11. Write tests: `apps/staff/tests/`
12. Run tests: `make test ARGS='apps.staff'`
13. Run lint: `make ruff`

---

### 6.6 Settings App — Round 2 (Phase 6)

**Status:** complete — implemented and merged on `main`. (This plan described POSProfile/
TaxTemplate/ProductionUnit as separate models; §6.14 removed POSProfile, POSProfileUser,
POSProfilePayment, TaxTemplate, and TaxRate entirely — only `ProductionUnit` survives, and
`Restaurant` is the singleton settings surface. See §6.14 and the §4 settings summary.)
**FEATURES.md sections:** A3 (Production / Kitchen Station Configuration), A4 (POS Profile /
Terminal Configuration), A5 partial (Tax template — later removed)
**Dependencies:** menu, inventory, payments, staff (later simplified by §6.14)
**Key models:** `POSProfile`, `POSProfileUser`, `POSProfilePayment`, `ProductionUnit`,
`TaxTemplate`, `TaxRate`

#### Reference files consulted

| Reference file | What was extracted |
|---|---|
| `references/erpnext-develop/erpnext/selling/doctype/pos_profile/pos_profile.json` | POS Profile core fields (44 fields across 4 tabs: identity, accounting, POS configurations, more info) |
| `references/erpnext-develop/erpnext/selling/doctype/pos_profile/pos_profile.py` | Validation: `validate_disabled`, `validate_default_profile`, `validate_all_link_fields`, `validate_duplicate_groups`, `validate_payment_methods`, `validate_accounting_dimensions`; lifecycle `on_update`/`on_trash` → `set_defaults` |
| `references/erpnext-develop/erpnext/selling/doctype/pos_profile_user/pos_profile_user.json` | Applicable-users child: `user` (Link→User), `default` (Check) |
| `references/erpnext-develop/erpnext/selling/doctype/pos_payment_method/pos_payment_method.json` | Payment-methods child: `mode_of_payment` (Link), `default` (Check), `allow_in_returns` (Check) |
| `references/erpnext-develop/erpnext/selling/doctype/pos_item_group/pos_item_group.json` | Item-group filter child: `item_group` (Link) |
| `references/erpnext-develop/erpnext/selling/doctype/pos_customer_group/pos_customer_group.json` | Customer-group filter child (studied and dropped — no Customer model) |
| `references/ury-develop/ury/fixtures/custom_field.json` | 38 URY custom fields on POS Profile (verified); 1 on POS Profile User (`custom_main_cashier`); 3 on URY Printer Settings (`custom_kot_print`, `custom_kot_print_format`, `custom_block_takeaway_kot`); 5 on Branch (aggregator block — dropped) |
| `references/ury-develop/ury/ury/doctype/ury_production_unit/ury_production_unit.json` | Production Unit fields: `production` (autoname), `pos_profile`, `branch` (fetch_from), `warehouse` (fetch_from), `item_groups` child, `printer_settings` child, KDS fields (dropped) |
| `references/ury-develop/ury/ury/doctype/ury_production_unit/ury_production_unit.py` | Empty — no validation logic |
| `references/ury-develop/ury/ury/doctype/ury_printer_settings/ury_printer_settings.json` | Printer-settings child: `bill` (Check), `printer` (Link→Network Printer Settings) + 3 URY custom fields |
| `references/ury-develop/ury/ury/doctype/ury_restaurant/ury_restaurant.json` | `default_tax_template` (core Link → Sales Taxes and Charges Template) — URY's restaurant-wide tax default |
| `references/erpnext-develop/erpnext/accounts/doctype/sales_taxes_and_charges_template/sales_taxes_and_charges_template.json` | TaxTemplate fields: `title`, `is_default`, `disabled`, `company`, `tax_category`, `taxes` (Table→Sales Taxes and Charges) |
| `references/erpnext-develop/erpnext/accounts/doctype/sales_taxes_and_charges_template/sales_taxes_and_charges_template.py` | Validation: default exclusivity per company, disabled-not-default, tax_category uniqueness, per-row account/cost_center validation; `autoname` = `f"{title} - {company_abbr}"` |
| `references/erpnext-develop/erpnext/accounts/doctype/sales_taxes_and_charges/sales_taxes_and_charges.json` | TaxRate row: `charge_type`, `rate`, `account_head`, `description`, `cost_center`, `included_in_print_rate`, `row_id` + computed `*_base_*` fields (deferred to Phase 8) |
| `references/erpnext-develop/erpnext/accounts/doctype/pos_opening_entry/pos_opening_entry.json` | `pos_profile` is a required Link on POS Opening Entry (Phase 6 adds nullable FK to Spicy POSOpeningEntry) |
| `references/ury-develop/ury/ury_pos/api.py` | Fields the POS frontend consumes from POSProfile (§G of research): `branch`, `warehouse`, `company`, `paid_limit`, `custom_edit_order_type`, `custom_enable_kot_reprint`, `printer_settings`, `payments`, `applicable_for_users`, `custom_daily_pos_close`. Discount / table-attention / warehouse-switch / role-billing / KOT-delay / multi-cashier fields reviewed and **dropped** — see deviations. |
| `references/ury-develop/ury/ury/doctype/aggregator_settings/aggregator_settings.json` | Reviewed and dropped — third-party food-delivery aggregator integration is out of scope |

#### Decisions

- **TaxTemplate attached only to Restaurant.** ERPNext puts the tax template on
  POSProfile (`taxes_and_charges`); URY puts a `default_tax_template` on the
  Restaurant. Spicy uses only the Restaurant-level attachment — simpler for
  single-site (one restaurant = one tax config). Phase 7 reads
  `restaurant.default_tax_template` at order time. The POSProfile `taxes_and_charges`
  and `tax_category` fields are not ported.

- **Printer config lives on ProductionUnit as string fields for Phase 6.** Phase 12 owns `PrinterConfig`
  as a standalone model. To avoid a cross-phase stub, Phase 6 stores `printer_ip` (CharField),
  `printer_paper_width` (choices), and `printer_cut_mode` (choices) directly on `ProductionUnit`.
  Phase 12 extracts these into a `PrinterConfig` model and migrates. Documented deviation from URY's
  child-table `printer_settings` approach.

- **`POSOpeningEntry.pos_profile` is a nullable FK in Phase 6.** ERPNext requires it (`reqd:1`).
  Spicy adds it as nullable to avoid breaking existing Phase 5 opening entries. The FK is not
  enforced as NOT NULL until Phase 7 (orders) requires it for order creation. Additive migration —
  no data backfill needed since Phase 5 entries predate POSProfile.

- **Role-permission M2Ms dropped.** URY's `role_allowed_for_billing`, `role_restricted_for_table_order`,
  `transfer_role_permissions`, and `notification_recipients` are not ported. Cashier-only POS (no
  waiter login), no table-order role split, no captain/table transfer, no KOT delay notifications.

- **`POSProfile.applicable_users` uses a through model `POSProfileUser`.** Preserves the per-user
  `is_default` flag (ERPNext enforces one default per user per company) and `is_main_cashier`
  (URY custom — flags the primary cashier for the API's `get_cashier` logic).

- **`POSProfile.payments` uses a through model `POSProfilePayment`.** Preserves `is_default`
  (exactly one default per profile — ERPNext validation) and `allow_in_returns`.

- **`ProductionUnit.branch` and `warehouse` are stored, auto-set from `pos_profile` in `save()`.**
  Matches the Spicy denormalization pattern (see `Table.branch`).
  Avoids stale data by re-setting on every save. Deviates from URY which uses `fetch_from` display
  fields.

- **`ProductionUnit.item_groups` child table dropped.** FEATURES.md A3 #14 explicitly states Spicy
  routes tickets by the `department` flag on each item, not by item-group mappings. The URY
  `item_groups` child is redundant.

- **Aggregator Settings, QZ printing, KDS/Mosaic, KOT audio alert — all dropped.** Out of scope
  per AGENTS.md.

- **KOT-related config fields modeled on POSProfile now, enforced in Phase 7/8.** `kot_naming_series`,
  `reset_order_number_daily`, `enable_kot_reprint`, `reprint_kot_format` — config toggles consumed by
  the orders/printing apps. KOT delay trio (`kot_warning_time`, `notify_kot_delay`,
  `notification_recipients`) dropped — no delay notification feature.

- **Accounting fields kept as CharField, enforcement deferred to Phase 8.** `cost_center`,
  `income_account`, `expense_account`, `write_off_account`, `write_off_cost_center`,
  `account_for_change_amount` — stored as strings (account names). Phase 8 introduces `LedgerAccount`
  and migrates to FK. FEATURES #40 says cost center is mandatory — Phase 6 keeps the field optional
  with a documented deviation (no model to FK to yet); Phase 8 enforces mandatory.

- **`currency` defaults to "NGN".** Single-currency. `selling_price_list` kept as nullable
  FK to `menu.PriceList` for ERPNext alignment, but Phase 7 resolves pricing from the active menu
  regardless.

- **`customer` and `customer_groups` dropped.** No Customer model (walk-in customer is a
  default string handled in Phase 7 if needed).

- **`utm_source`, `utm_campaign`, `utm_medium`, `ignore_pricing_rule`, `letter_head`, `tc_name`,
  `select_print_heading` dropped.** Marketing analytics irrelevant to a local restaurant POS / no
  ERPNext pricing-rule engine / ERPNext print cosmetics. `print_format` kept (consumed by URY API;
  Phase 12 defines Spicy print formats).

#### Models (6 total)

All models extend `apps.utils.models.BaseModel`.

##### `TaxTemplate` (`settings.TaxTemplate`)

| Field | Type | Source | Notes |
|---|---|---|---|
| title | CharField, max_length=100, required | ERPNext `title` | used in display name |
| is_default | BooleanField, default=False | ERPNext `is_default` | one default per company enforced in `clean()` |
| disabled | BooleanField, default=False | ERPNext `disabled` | disabled templates can't be assigned |
| company | CharField, max_length=200, required | ERPNext `company` | defaults from `Restaurant.company` in `clean()`; no Company model |
| tax_category | CharField, max_length=100, blank=True | ERPNext `tax_category` | Phase 8 migrates to FK; uniqueness enforced in `clean()` |

**Methods:**
- `__str__` returns `title`
- `clean()`: if `is_default` and `disabled`, raise `ValidationError("Disabled template must not be default")`
- `clean()`: if `is_default`, unset `is_default` on other templates with the same `company` (default exclusivity)
- `clean()`: if `tax_category` set, no other non-disabled template in same `company` may share it
- Meta: `ordering = ["title"]`, `unique_together = [("title", "company")]`

##### `TaxRate` (`settings.TaxRate`) — child table

| Field | Type | Source | Notes |
|---|---|---|---|
| tax_template | ForeignKey→TaxTemplate, on_delete=CASCADE, related_name="rates" | ERPNext `taxes` | |
| charge_type | CharField, max_length=30, choices: ACTUAL / ON_NET_TOTAL / ON_PREVIOUS_ROW_AMOUNT / ON_PREVIOUS_ROW_TOTAL / ON_ITEM_QUANTITY, default=ON_NET_TOTAL | ERPNext `charge_type` | |
| rate | DecimalField, max_digits=8, decimal_places=4, default=0 | ERPNext `rate` | percentage for ON_NET_TOTAL |
| account_head | CharField, max_length=200, required | ERPNext `account_head` | account name string; Phase 8 migrates to FK→LedgerAccount |
| description | CharField, max_length=255, required | ERPNext `description` | |
| cost_center | CharField, max_length=200, blank=True | ERPNext `cost_center` | Phase 8 migrates to FK |
| included_in_print_rate | BooleanField, default=False | ERPNext `included_in_print_rate` | |
| row_id | PositiveIntegerField, null=True, blank=True | ERPNext `row_id` | for previous-row charge types |

**Methods:**
- `__str__` returns `f"{charge_type} {rate}% → {account_head}"`
- Meta: `ordering = ["pk"]`

##### `POSProfile` (`settings.POSProfile`)

| Field | Type | Source | Notes |
|---|---|---|---|
| name | CharField, max_length=100, required | ERPNext Prompt naming | e.g. "Main Cashier", "Bar POS" |
| company | CharField, max_length=200, required | ERPNext core | defaults from `Restaurant.company` in `clean()` |
| branch | ForeignKey→settings.Branch, on_delete=PROTECT, related_name="pos_profiles" | URY custom | auto-set via `Branch.get_default()` in `save()` if blank |
| restaurant | ForeignKey→settings.Restaurant, on_delete=PROTECT, null=True, blank=True, related_name="pos_profiles" | URY custom | auto-set from `branch.restaurants.first()` in `save()` if blank |
| warehouse | ForeignKey→inventory.Warehouse, on_delete=PROTECT, related_name="pos_profiles" | ERPNext core | required — default stock deduction warehouse |
| disabled | BooleanField, default=False | ERPNext core | |
| currency | CharField, max_length=3, default="NGN" | ERPNext core | single-currency |
| selling_price_list | ForeignKey→menu.PriceList, on_delete=SET_NULL, null=True, blank=True, related_name="pos_profiles" | ERPNext core | Phase 7 resolves from active menu |
| taxes_and_charges | — | dropped | tax template lives on Restaurant only, not POSProfile |
| tax_category | CharField, max_length=100, blank=True | ERPNext core | Phase 8 |
| cost_center | CharField, max_length=200, blank=True | ERPNext core | Phase 8 migrates to FK; FEATURES #40 mandatory deferred |
| income_account | CharField, max_length=200, blank=True | ERPNext core | Phase 8 |
| expense_account | CharField, max_length=200, blank=True | ERPNext core | Phase 8 |
| write_off_account | CharField, max_length=200, blank=True | ERPNext core | Phase 8 |
| write_off_cost_center | CharField, max_length=200, blank=True | ERPNext core | Phase 8 |
| write_off_limit | DecimalField, max_digits=12, decimal_places=2, default=Decimal("1.00") | ERPNext core | FEATURES #23 |
| account_for_change_amount | CharField, max_length=200, blank=True | ERPNext core | Phase 8 |
| set_grand_total_to_default_mop | BooleanField, default=True | ERPNext core | FEATURES #25 |
| action_on_new_invoice | CharField, max_length=40, choices: ALWAYS_ASK / SAVE_AND_LOAD_NEW / DISCARD_AND_LOAD_NEW, default=ALWAYS_ASK | ERPNext core | FEATURES #24 |
| validate_stock_on_save | BooleanField, default=False | ERPNext core | |
| hide_images | BooleanField, default=False | ERPNext core | FEATURES #22 |
| hide_unavailable_items | BooleanField, default=False | ERPNext core | FEATURES #22 |
| auto_add_item_to_cart | BooleanField, default=False | ERPNext core | FEATURES #21 |
| print_receipt_on_order_complete | BooleanField, default=False | ERPNext core | FEATURES #20 |
| view_all_status | BooleanField, default=False | URY `view_all_status` | FEATURES #32 |
| paid_limit | IntegerField, default=20 | URY `paid_limit` | FEATURES #33 |
| edit_order_type | BooleanField, default=False | URY `custom_edit_order_type` | FEATURES #34 |
| remove_items | BooleanField, default=False | URY `remove_items` | FEATURES #35 |
| show_image | BooleanField, default=True | URY `show_image` | POS item cards |
| require_daily_pos_close | BooleanField, default=False | URY `custom_daily_pos_close` | FEATURES #29 |
| kot_naming_series | CharField, max_length=50, default="KOT-####" | URY `custom_kot_naming_series` | FEATURES #27; consumed in Phase 7 |
| reset_order_number_daily | BooleanField, default=False | URY `custom_reset_order_number_daily` | FEATURES #30 |
| enable_kot_reprint | BooleanField, default=False | URY `custom_enable_kot_reprint` | FEATURES #28 |
| reprint_kot_format | CharField, max_length=100, blank=True | URY `custom_reprint_kot_format` | Phase 12 defines print formats |
| print_format | CharField, max_length=100, blank=True | ERPNext core | Phase 12 defines |
| applicable_users | ManyToManyField→CustomUser, through=POSProfileUser, related_name="pos_profiles", blank=True | ERPNext core child | |
| payments | ManyToManyField→payments.ModeOfPayment, through=POSProfilePayment, related_name="pos_profiles", blank=True | ERPNext core child | |
| item_groups | ManyToManyField→inventory.ItemGroup, related_name="pos_profiles", blank=True | ERPNext core child | empty = all groups visible |

**Methods:**
- `__str__` returns `name`
- `save()`: auto-assign `branch` from `Branch.get_default()` if blank; auto-assign `restaurant`
  from `branch.restaurants.first()` if blank
- `clean()`:
  - if `disabled` and an open `POSOpeningEntry` exists for this profile → raise
    `ValidationError("POS Profile cannot be disabled as there are ongoing POS sessions.")`
  - `payments` must be non-empty → raise
    `ValidationError("Payment methods are mandatory. Please add at least one payment method.")`
  - exactly one `POSProfilePayment` row with `is_default=True` → raise
    `ValidationError("Please select exactly one default mode of payment.")`
  - for every `mode_of_payment` in `payments`, a `PaymentGLMapping` must exist for the current
    `company` → raise `ValidationError("Please set default account for mode(s) of payment: {list}")`
  - no duplicate `item_groups` → raise `ValidationError("Duplicate item group found.")`
  - for each `POSProfileUser` with `is_default=True`, no other non-disabled POSProfile in the same
    `company` may have that user as default → raise
    `ValidationError("User {user} already has a default POS Profile in {company}.")`
- Meta: `ordering = ["name"]`, `unique_together = [("name", "branch")]`

##### `POSProfileUser` (`settings.POSProfileUser`) — through model

| Field | Type | Source | Notes |
|---|---|---|---|
| pos_profile | ForeignKey→POSProfile, on_delete=CASCADE, related_name="user_links" | ERPNext `applicable_for_users` | |
| user | ForeignKey→CustomUser, on_delete=PROTECT, related_name="profile_links" | ERPNext core | |
| is_default | BooleanField, default=False | ERPNext core | one default per user per company (enforced in POSProfile.clean) |
| is_main_cashier | BooleanField, default=False | URY `custom_main_cashier` | flags the primary cashier for API `get_cashier` logic |

**Methods:**
- `__str__` returns `f"{user.username} @ {pos_profile.name}"`
- Meta: `unique_together = [("pos_profile", "user")]`, `ordering = ["user__username"]`

##### `POSProfilePayment` (`settings.POSProfilePayment`) — through model

| Field | Type | Source | Notes |
|---|---|---|---|
| pos_profile | ForeignKey→POSProfile, on_delete=CASCADE, related_name="payment_links" | ERPNext `payments` | |
| mode_of_payment | ForeignKey→payments.ModeOfPayment, on_delete=PROTECT, related_name="profile_links" | ERPNext core | |
| is_default | BooleanField, default=False | ERPNext core | exactly one default per profile (enforced in POSProfile.clean) |
| allow_in_returns | BooleanField, default=False | ERPNext core | |

**Methods:**
- `__str__` returns `f"{mode_of_payment.name} @ {pos_profile.name}"`
- Meta: `unique_together = [("pos_profile", "mode_of_payment")]`,
  `ordering = ["mode_of_payment__name"]`

##### `ProductionUnit` (`settings.ProductionUnit`)

| Field | Type | Source | Notes |
|---|---|---|---|
| name | CharField, max_length=100, required | URY `production` (autoname source) | e.g. "Kitchen", "Bar" |
| pos_profile | ForeignKey→POSProfile, on_delete=PROTECT, null=True, blank=True, related_name="production_units" | URY core | nullable for Spicy flexibility (unit can exist without a profile) |
| branch | ForeignKey→settings.Branch, on_delete=PROTECT, related_name="production_units" | URY `fetch_from pos_profile.branch` | stored (denormalized); auto-set from `pos_profile.branch` or `Branch.get_default()` in `save()` |
| warehouse | ForeignKey→inventory.Warehouse, on_delete=PROTECT, related_name="production_units" | URY `fetch_from pos_profile.warehouse` | stored (denormalized); auto-set from `pos_profile.warehouse` in `save()` if blank |
| department | CharField, max_length=10, choices: FOOD / DRINKS, required | Spicy-specific | drives ticket routing per FEATURES #14 |
| block_takeaway_kot | BooleanField, default=False | URY `custom_block_takeaway_kot` (on printer settings) | FEATURES #15 — suppresses ticket for takeaway orders |
| printer_ip | CharField, max_length=50, blank=True | Spicy-specific (replaces URY `printer_settings` child) | LAN printer static IP; Phase 12 migrates to FK→PrinterConfig |
| printer_paper_width | CharField, max_length=10, choices: WIDTH_58MM / WIDTH_80MM, default=WIDTH_80MM | Spicy-specific | ESC/POS paper width |
| printer_cut_mode | CharField, max_length=15, choices: FULL_CUT / PARTIAL_CUT / NO_CUT, default=FULL_CUT | Spicy-specific | ESC/POS cut mode |

**Methods:**
- `__str__` returns `name`
- `save()`: auto-assign `branch` from `pos_profile.branch` if blank, else `Branch.get_default()`;
  auto-assign `warehouse` from `pos_profile.warehouse` if blank
- `clean()`: if `pos_profile` is set, validate `self.branch_id == self.pos_profile.branch_id`
- Meta: `ordering = ["name"]`, `unique_together = [("name", "branch")]`

**Dropped from URY:**
- `item_groups` child table (Spicy routes by department, not item groups)
- `enable_order_type_wise_display_on_mosaic` (KDS/Mosaic out of scope)
- `order_type` child table (KDS out of scope)
- `printer_settings` child table (folded into `printer_*` fields; Phase 12 may extract)

#### Cross-app migration: add `pos_profile` FK to `POSOpeningEntry`

Phase 5 deferred the `pos_profile` FK on `POSOpeningEntry` (it lives in `apps.staff`). Phase 6 adds
it via a migration in `apps.staff` that depends on the new `apps.settings` migration creating
`POSProfile`. `makemigrations` handles the cross-app dependency automatically. The migration is
additive (`AddField nullable`) — no data backfill needed since Phase 5 entries predate POSProfile.
Do NOT enforce NOT NULL in Phase 6; Phase 7 may enforce when orders require a profile.

```python
# apps/staff/migrations/000X_posopeningentry_pos_profile.py
# Generated by makemigrations — do NOT hand-write the AddField.
# Dependencies: ("settings", "0003_posprofile_..."), ("staff", "<latest>")
operations = [
    migrations.AddField(
        model_name="posopeningentry",
        name="pos_profile",
        field=models.ForeignKey(
            "settings.POSProfile",
            on_delete=models.PROTECT,
            null=True,
            blank=True,
            related_name="opening_entries",
        ),
    ),
]
```

#### Business logic — `POSProfile.clean()`

```python
def clean(self):
    super().clean()
    if self.disabled and self.pk:
        open_exists = POSOpeningEntry.objects.filter(
            pos_profile=self, status="SUBMITTED", closing_entry__isnull=True
        ).exists()
        if open_exists:
            raise ValidationError("POS Profile cannot be disabled as there are ongoing POS sessions.")
    if not self.payment_links.exists():
        raise ValidationError("Payment methods are mandatory. Please add at least one payment method.")
    defaults = self.payment_links.filter(is_default=True)
    if defaults.count() != 1:
        raise ValidationError("Please select exactly one default mode of payment.")
    modes_without_gl = []
    for link in self.payment_links.all():
        if not PaymentGLMapping.objects.filter(
            mode_of_payment=link.mode_of_payment, company=self.company
        ).exists():
            modes_without_gl.append(link.mode_of_payment.name)
    if modes_without_gl:
        raise ValidationError(
            f"Please set default account for mode(s) of payment: {', '.join(modes_without_gl)}"
        )
    seen = set()
    for ig in self.item_groups.all():
        if ig.pk in seen:
            raise ValidationError("Duplicate item group found.")
        seen.add(ig.pk)
    for link in self.user_links.filter(is_default=True):
        other = POSProfileUser.objects.filter(
            user=link.user, is_default=True,
            pos_profile__company=self.company, pos_profile__disabled=False,
        ).exclude(pos_profile=self).exists()
        if other:
            raise ValidationError(
                f"User {link.user.username} already has a default POS Profile in {self.company}."
            )
```

#### Views & URLs

Function-based views, `@login_required`, protected by `BackofficeAccessMiddleware`. Mutating
endpoints (create/edit/delete) require `request.user.is_manager or request.user.is_admin` (view-level
guard returning 302 → `web:pending_approval` if false). Delete endpoints use `@require_POST`.

| URL pattern | View | Purpose |
|---|---|---|
| `/backoffice/settings/pos-profiles/` | `pos_profile_list` | List POS profiles (HTMX partial for rows) |
| `/backoffice/settings/pos-profiles/create/` | `pos_profile_create` | Create profile (inline formsets for users, payments) |
| `/backoffice/settings/pos-profiles/<int:pk>/` | `pos_profile_detail` | View profile + tabs |
| `/backoffice/settings/pos-profiles/<int:pk>/edit/` | `pos_profile_update` | Update profile |
| `/backoffice/settings/pos-profiles/<int:pk>/delete/` | `pos_profile_delete` | Delete profile — `@require_POST` (blocked if open shifts exist) |
| `/backoffice/settings/production-units/` | `production_unit_list` | List units (filterable by branch/department) |
| `/backoffice/settings/production-units/create/` | `production_unit_create` | Create unit |
| `/backoffice/settings/production-units/<int:pk>/` | `production_unit_detail` | View/edit unit |
| `/backoffice/settings/production-units/<int:pk>/edit/` | `production_unit_update` | Update unit |
| `/backoffice/settings/production-units/<int:pk>/delete/` | `production_unit_delete` | Delete unit — `@require_POST` |
| `/backoffice/settings/tax-templates/` | `tax_template_list` | List templates |
| `/backoffice/settings/tax-templates/create/` | `tax_template_create` | Create template (inline formset for rates) |
| `/backoffice/settings/tax-templates/<int:pk>/` | `tax_template_detail` | View template |
| `/backoffice/settings/tax-templates/<int:pk>/edit/` | `tax_template_update` | Update template |
| `/backoffice/settings/tax-templates/<int:pk>/delete/` | `tax_template_delete` | Delete template — `@require_POST` |

#### Templates

| Template | Purpose |
|---|---|
| `templates/backoffice/settings/pos_profile_list.html` | List with `{% partialdef row %}` |
| `templates/backoffice/settings/pos_profile_form.html` | Create/edit with inline formsets for users, payments; M2M widgets for groups/item_groups |
| `templates/backoffice/settings/pos_profile_detail.html` | Tabs: identity, payments, permissions, KOT, print |
| `templates/backoffice/settings/production_unit_list.html` | List with department filter |
| `templates/backoffice/settings/production_unit_form.html` | Create/edit |
| `templates/backoffice/settings/production_unit_detail.html` | View unit + printer config |
| `templates/backoffice/settings/tax_template_list.html` | List |
| `templates/backoffice/settings/tax_template_form.html` | Create/edit with inline formset for rates |
| `templates/backoffice/settings/tax_template_detail.html` | View template + rates |

#### Forms

| Form | Model | Notes |
|---|---|---|
| `POSProfileForm` | `POSProfile` | Excludes `company` (auto from Restaurant), `branch` (auto from `Branch.get_default()`). Inline formsets for `POSProfileUser` and `POSProfilePayment`. M2M widgets for `item_groups`, `role_*` groups. |
| `POSProfileUserForm` | `POSProfileUser` | Fields: `user`, `is_default`, `is_main_cashier` |
| `POSProfilePaymentForm` | `POSProfilePayment` | `mode_of_payment` queryset via `active_choices(ModeOfPayment, ..., enabled=True)`; fields: `is_default`, `allow_in_returns` |
| `ProductionUnitForm` | `ProductionUnit` | `pos_profile` queryset via `active_choices(POSProfile, ..., disabled=False)`. Branch/warehouse auto-set on save. |
| `TaxTemplateForm` | `TaxTemplate` | Fields: `title`, `is_default`, `disabled`, `tax_category`. `company` auto-defaulted in `__init__`. Inline formset for `TaxRate`. |
| `TaxRateForm` | `TaxRate` | Fields: `charge_type`, `rate`, `account_head`, `description`, `cost_center`, `included_in_print_rate`, `row_id` |

Forms extend `apps.utils.forms.StyledModelForm` via the existing `SettingsModelForm` base.

#### Admin

Register all 6 models with `list_display`, `list_filter`, `search_fields`, `list_select_related`.
`POSProfileAdmin` shows key toggles in list. `ProductionUnitAdmin` filters by department.
`TaxTemplateAdmin` filters by company, shows `is_default`. `POSProfileUserAdmin` and
`POSProfilePaymentAdmin` show the parent profile and the linked user/mode.

#### Tests

| Test file | What it covers |
|---|---|
| `test_tax_template.py` | CRUD, default exclusivity per company, disabled-not-default, tax_category uniqueness, TaxRate child CRUD, PROTECT on template delete, `unique_together(title, company)` |
| `test_pos_profile.py` | CRUD, branch auto-default, restaurant auto-default, `clean()` validation (disabled-with-open-shift, payments non-empty, one default payment, GL mapping check, duplicate item groups, default-per-user), `unique_together(name, branch)` |
| `test_pos_profile_user.py` | Through-model CRUD, `is_default` uniqueness per user per company, `is_main_cashier` flag, PROTECT on user delete, `unique_together(profile, user)` |
| `test_pos_profile_payment.py` | Through-model CRUD, `is_default` exactly-one validation, `allow_in_returns`, PROTECT on mode delete, `unique_together(profile, mode)` |
| `test_production_unit.py` | CRUD, department choices, branch/warehouse auto-set from `pos_profile`, `block_takeaway_kot` flag, `printer_ip`/`printer_paper_width`/`printer_cut_mode` fields, `unique_together(name, branch)` |
| `test_pos_opening_entry_pos_profile_fk.py` | The Phase 6 migration adds nullable `pos_profile` FK; existing opening entries still work without one; new entries can reference a profile |
| `test_views.py` | Login required, manager-only on mutating endpoints, HTMX partial responses, `@require_POST` on delete (GET → 405), list filtering (department, branch) |

#### Deviations from reference

| Deviation | Reason |
|---|---|
| TaxTemplate attached to Restaurant only (not POSProfile) | ERPNext = per-profile; URY = per-restaurant. Spicy uses restaurant-only — simpler for a single-site restaurant. POSProfile `taxes_and_charges` and `tax_category` dropped. |
| Printer config as string fields on ProductionUnit (not child table) | Phase 12 owns PrinterConfig model; storing strings now avoids a cross-phase stub. Phase 12 migrates. |
| `POSOpeningEntry.pos_profile` nullable (not required) | Phase 5 entries predate POSProfile. ERPNext requires it; Phase 7 may enforce NOT NULL. |
| Role-permission M2Ms and KOT delay trio dropped from POSProfile | Cashier-only POS; no waiter/table-order role split, no transfer roles, no KOT delay alerts (#36–#38, #59, #74). |
| `POSProfileUser` and `POSProfilePayment` as through models (not child tables) | Django M2M-through pattern; preserves per-row flags (`is_default`, `is_main_cashier`, `allow_in_returns`). `POSProfileUser` dropped in single-profile refactor. |
| `multiple_cashier` field not ported onto `POSProfile` | URY `custom_enable_multiple_cashier` drives a main/sub-cashier shift hierarchy (separate shifts, sub-cashier sub-closings, main-shown reconciliation). The project has one shared session per branch (§6.5), so the field carried no behaviour and was removed to avoid dead schema. The supporting `POSProfileUser.is_main_cashier` flag is gone with `POSProfileUser` (above). |
| POSProfile limited to one per Restaurant | ERPNext/URY allow multiple profiles per branch (multi-terminal configs). Spicy is single-site — one POS terminal = one profile. Singleton enforced via `unique_together`. POS profile UI is a settings page, not a list/create/detail CRUD. |
| `ProductionUnit.branch`/`warehouse` stored (not `fetch_from` display) | Spicy denormalization pattern (matches `Table.branch`); auto-set in `save()`. |
| `item_groups` child on ProductionUnit dropped | Spicy routes by department flag, not item-group mappings (FEATURES #14). |
| Aggregator Settings, QZ printing, KDS/Mosaic, KOT audio alert dropped | Out of scope per AGENTS.md. |
| `customer`, `customer_groups`, `utm_*`, `ignore_pricing_rule`, `letter_head`, `tc_name`, `select_print_heading` dropped | No Customer model / irrelevant to local restaurant POS / ERPNext print cosmetics. |
| Accounting fields (`cost_center`, `income_account`, etc.) as CharField, optional | Phase 8 introduces `LedgerAccount` and migrates to FK; FEATURES #40 mandatory deferred. |
| `currency` hardcoded to "NGN" default | Single-currency. |
| `selling_price_list` kept but Phase 7 ignores it | ERPNext alignment; Spicy resolves pricing from active menu. |
| `restaurant` field on POSProfile auto-set from branch | URY has it as user-selected; Spicy single-site auto-derives. |
| KOT config fields modeled now, enforced in Phase 7/8 | Config toggles consumed by orders/printing apps; modeling now keeps POSProfile complete. |

#### Implementation steps

1. Write models in `apps/settings/models.py` (`TaxTemplate`, `TaxRate`, `POSProfile`,
   `POSProfileUser`, `POSProfilePayment`, `ProductionUnit`)
2. Write forms in `apps/settings/forms.py` (with inline formsets for through models and `TaxRate`)
3. Write views in `apps/settings/views.py` (manager-only mutating endpoints; `@require_POST` on delete)
4. Add URL patterns in `apps/settings/urls.py`
5. Register all 6 models in `apps/settings/admin.py`
6. Create and run migrations: `make migrations && make migrate` (`apps.settings` gets new models;
   `apps.staff` gets `pos_profile` FK — `makemigrations` handles cross-app deps automatically).
   Review the generated `apps.staff` migration to confirm it depends on the new `apps.settings` one.
7. Optional: write a seed migration for a default tax template ("Nigerian VAT 7.5%" with one
   `TaxRate` row: `charge_type=ON_NET_TOTAL`, `rate=7.5`, `account_head="VAT Payable"`)
8. Write templates in `templates/backoffice/settings/`
9. Write tests: `apps/settings/tests/`
10. Run tests: `make test ARGS='apps.settings'`
11. Run lint: `make ruff`
12. Update §3 status table: Phase 6 → `complete`; Phase 7 → `planned` or `not started` (depending
    on whether §6.7 plan exists)

---

### 6.7 Orders App (Phase 7)

**Status:** complete — implemented and merged on `main`. POS workbench, continuous numbering,
tickets, drinks-only stock, returns, and backoffice control room all live. The owner-confirmed
decisions in §6.14–§6.22 supersede the original plan below where they conflict.
**FEATURES.md sections:** A6, A7, A18 (core order lifecycle, KOT, settle, cancel)
**Dependencies:** settings, menu, staff, payments
**Split (original):** 7a (core flow — this plan) + 7b (table transfer, KOT reprint,
duplicate ticket detection — deferred; captain/waiter transfer and KOT delay out of scope).
The original 7a/7b split is now historical: table transfer, KOT diffing, and duplicate
detection were never built — the §6.17 decision (cancel-and-replace, no diffing) replaced them.

> **POS-screen shift UI is a Phase 7 deliverable (implemented).** Per user decision (Phase 5
> follow-up), the POS screen checks for an open shift on load and shows an inline "Open Shift"
> form (opening float per mode) if none is open. The order screen has a "Close Shift" button
> that walks the cashier through reconciliation. The backoffice Staff app (Phase 5) remains the
> manager audit/reconciliation surface. This is a documented deviation from URY, which puts shift
> open/close entirely in the backoffice and gates the POS screen with a "Switch to Desk" blocker.

#### Reference files consulted

| Reference file | What was extracted |
|---|---|
| `references/ury-develop/ury/ury/doctype/ury_order/ury_order.py` | Whitelisted order endpoints: get_order_invoice, sync_order, make_invoice, cancel_order, table_transfer, captain_transfer, pos_opening_check |
| `references/ury-develop/ury/ury/doctype/ury_order/ury_order.json` | URY Order single doctype fields (UI shell — not persisted per-row) |
| `references/ury-develop/ury/ury/doctype/ury_order_item/ury_order_item.json` | Order item child table fields (item, qty, rate, comments) |
| `references/ury-develop/ury/ury/doctype/ury_kot/ury_kot.json` | KOT fields (type, production, order_no, restaurant_table, original_kot, status) |
| `references/ury-develop/ury/ury/doctype/ury_kot/ury_kot.py` | KOT submit logic, printer routing cascade |
| `references/ury-develop/ury/ury/doctype/ury_kot_items/ury_kot_items.json` | KOT item fields (item, item_name, quantity, cancelled_qty, comments, course) |
| `references/ury-develop/ury/ury/api/ury_kot_generate.py` | KOT diffing engine (studied; §6.17 replaced diff-sync with cancel-and-replace) |
| `references/ury-develop/ury/ury/hooks/ury_pos_invoice.py` | POS Invoice event hooks: before_insert, validate, before_submit, on_cancel — invoice print enforcement, item modification lock |
| `references/ury-develop/ury/ury/hooks/ury_kot_order_number.py` | Sequential order number logic (session-based in URY; Spicy implements continuous numbering via §6.15) |
| `references/erpnext-develop/erpnext/accounts/doctype/pos_invoice/pos_invoice.json` | POS Invoice fields: totals, discount, rounding, payments, outstanding, write-off, status |
| `references/erpnext-develop/erpnext/accounts/doctype/pos_invoice/pos_invoice.py` | validate, validate_change_amount, set_outstanding_amount, set_status, before_submit, on_cancel |
| `references/erpnext-develop/erpnext/accounts/doctype/pos_invoice_item/pos_invoice_item.json` | Invoice item fields: qty, rate, amount, warehouse, item_group |
| `references/ury-develop/pos/src/components/OrderPanel.tsx` | Cart UI structure, sync flow |
| `references/ury-develop/pos/src/components/PaymentDialog.tsx` | Split payment dialog, discount, rounding, auto-fill remaining |
| `references/ury-develop/pos/src/components/MenuList.tsx` | Menu grid, category filtering |

#### Phase 7a scope (historical — see §6.14–§6.22 for current product fact)

**In scope (original):** Order model + items, customer cards (group ordering), KOT generation with
diffing, departmental ticket routing, settle (split payment, tax, rounding, change,
stock deduction), cancel (with reversal), sequential order numbering, concurrent modification
check, invoice print enforcement, POS shift check, full POS screen UI, backoffice order/KOT
views. Cashier free-form discount is out of scope (coupon system later).

**What actually shipped (superseding 7a):** counter-based orders (no tables), continuous
numbering (§6.15), one-time ticket creation with print state and cancel-and-replace (§6.17),
drinks-only reservation/deduction (§6.20), POS workbench + add-on dialog (§6.21), shell
navigation (§6.22), backoffice control room (§6.18), returns, and audit events. No tax system,
no table transfer, no KOT diffing, no waiter roles.

#### Models (current implementation)

All models extend `apps.utils.models.BaseModel`.

##### OrderSequence (`orders.OrderSequence`) — §6.15

| Field | Type | Notes |
|---|---|---|
| name | CharField, max_length=50, unique | one row, `name="order"` |
| current_value | PositiveIntegerField | bumped atomically under `select_for_update()` |

##### Order (`orders.Order`)

| Field | Type | Notes |
|---|---|---|
| invoice_number | CharField, max_length=50, unique, null=True, blank=True, editable=False | auto-generated on first save `{prefix}{pk}` |
| order_number | PositiveIntegerField, db_index, null=True, blank=True, editable=False | from `OrderSequence` — always +1, no daily reset (§6.15) |
| order_type | CharField, max_length=20, choices: DINE_IN, TAKE_AWAY, DELIVERY, PHONE_IN, default=DINE_IN | set from the POS start screen (no table) |
| customer_name | CharField, max_length=200, default="Walk-in Customer" | |
| guest_count | PositiveIntegerField, default=1 | 1 = single customer, >1 = group ordering |
| cashier | FK→users.CustomUser, SET_NULL, null=True, blank=True, related_name="settled_orders" | set on settle |
| status | CharField, max_length=10, choices: DRAFT/SUBMITTED/CANCELLED/DISCARDED, default=DRAFT | submit/cancel workflow + POS discard |
| is_paid | BooleanField, default=False | true when fully settled |
| invoice_printed | BooleanField, default=False | receipt print is optional (§6.17/§6.19) |
| posting_date / posting_time | DateField / TimeField, default now | |
| net_total / grand_total | DecimalField, editable=False | no tax system — grand_total = net (rounded) |
| rounded_total | DecimalField | |
| rounding_adjustment | DecimalField, default=0, editable=False | |
| paid_amount | DecimalField, editable=False | |
| change_amount | DecimalField | cash-only change |
| cancel_reason | CharField, choices: wrong order / customer changed mind / cashier error / other | required when CANCELLED |
| cancel_reason_note | TextField, blank=True | |
| cancelled_by / cancelled_at | FK→CustomUser / DateTime | audit |
| discarded_by / discarded_at | FK→CustomUser / DateTime | audit for empty drafts |
| opening_entry | FK→staff.POSOpeningEntry, SET_NULL, null=True, blank=True, related_name="orders" | the active shift the order belongs to |
| stock_warehouse | FK→inventory.Warehouse, SET_NULL, null=True, blank=True | snapshot of the Bar/POS warehouse for drinks (§6.20) |
| arrived_time / submitted_at / invoice_printed_at / invoice_printed_by | timestamps | audit |
| is_return | BooleanField, default=False, editable=False | return order |
| return_against | FK→self, SET_NULL, null=True, blank=True, related_name="return_orders" | original submitted order |

**Methods (current):**
- `save()`: generate `invoice_number` on first save; enforce immutability (submitted/cancelled/
  discarded orders cannot be modified, except via the internal `_allow_*` flags during settle/
  cancel/discard).
- `clean()`: cancel requires a `cancel_reason`; a return must reference a submitted order.
- `delete()`: blocked for submitted/cancelled/printed/sent orders — cancel instead.
- `assign_order_number()`: atomic `select_for_update()` bump of `OrderSequence` (always +1).
- `recalculate_totals()`: `net_total = sum(items.amount)`; `grand_total = rounded_total` (no tax).
- `add_item()` / `update_item_quantity()` / `remove_item()` / `clear_items()`: line mutation with
  `_ensure_editable()` guard and drink reservation updates.
- `change_guest_count(new_count)`: cannot drop below a guest who still has items (1–50 clamp).
- `settle(payments_data, cashier, opening_entry)`: atomic — revalidate lines + drink stock,
  resolve/validate payment rows (enabled modes declared on the open shift, GL mapping present,
  non-cash overpayment rejected, unique electronic reference), assign order number, create
  `OrderPayment` rows, submit, convert drink reservations into negative SLEs. Returns/paid orders
  use the deferred refund flow.
- `cancel(reason, cancelled_by, reason_note)`: atomic — restore stock (submitted) or release
  reservations (draft), create one cancellation ticket per production unit (`CNCL-*`), preserve
  payment rows for audit.
- `cancel_sent_order()`: POS path for cancelling a draft after tickets were sent (no new
  cancellation ticket for unpaid drafts — §6.17).
- `discard(discarded_by)`: mark an empty, untouched draft `DISCARDED`.
- `make_return()`: create a manager-reviewed draft return mirroring source lines as negative qty.
- `create_tickets(created_by)`: one immutable kitchen/bar ticket per department from the order
  snapshot (`KOT-*` / `BOT-*`), rejecting mixed/missing production-unit config; `block_takeaway_kot`
  respected.
- `audit(event_type, actor, metadata)`: append an immutable `OrderAuditEvent`.

##### OrderItem (`orders.OrderItem`)

| Field | Type | Notes |
|---|---|---|
| order | FK→Order, PROTECT, related_name="items" | |
| item | FK→inventory.Item, PROTECT | |
| item_name | CharField, max_length=200 | auto-filled from item.item_name |
| qty | DecimalField | > 0 (or negative on return lines) |
| rate | DecimalField | from `MenuItem.rate` (no price list) |
| amount | DecimalField, default=0, editable=False | qty * rate |
| department | CharField, choices: FOOD/DRINKS, null=True, blank=True | snapshot from item.department |
| stock_item | BooleanField, null=True, blank=True, editable=False | snapshot from item.is_stock_item |
| customer_index | PositiveIntegerField, default=1 | 1-based; groups items by customer card |
| comments | CharField, max_length=200, blank=True | |
| menu_item | FK→menu.MenuItem, SET_NULL, null=True, blank=True | traceability to menu rate source |
| return_against_item | FK→self, SET_NULL, null=True, blank=True, related_name="return_items" | return lines reference the original line |

**Methods:** `save()` auto-fills `item_name`, `department`, `stock_item`, computes `amount`;
validates qty/rate and return-line invariants (return line must reference an item from the
original order, negative qty, cannot exceed quantity sold).

##### OrderPayment (`orders.OrderPayment`)

| Field | Type | Notes |
|---|---|---|
| order | FK→Order, PROTECT, related_name="payments" | PROTECT per AGENTS.md hard rule |
| mode_of_payment | FK→payments.ModeOfPayment, PROTECT | |
| amount | DecimalField, max_digits=12, decimal_places=2 | |
| reference_no | CharField, max_length=100, blank=True | for bank transfers, card auth codes; unique per electronic mode |

> **OrderTax was removed.** The tax system (`TaxTemplate`/`TaxRate`/`OrderTax`) was dropped in
> the single-location cleanup (§6.14). Prices are tax-inclusive; `grand_total = net_total`
> (rounded). No `taxes_and_charges_template`, no `total_taxes`.

##### KOT (`orders.KOT`) — kitchen/bar ticket (§6.17)

| Field | Type | Notes |
|---|---|---|
| order | FK→Order, PROTECT, related_name="kots" | |
| production_unit | FK→settings.ProductionUnit, PROTECT, related_name="kots" | routes kitchen vs bar |
| type | CharField, max_length=25, choices: New Order / Cancelled | from URY KOT.type (no Modified/Partially Cancelled — no diffing) |
| kot_number | CharField, max_length=50, unique | `KOT-{pk:04d}` / `BOT-{pk:04d}` / `CNCL-{...}` |
| ticket_type | CharField, max_length=10, choices: kitchen / bar | snapshot — §6.17 |
| status | CharField, max_length=15, choices: SUBMITTED/CANCELLED, default=SUBMITTED | document lifecycle |
| print_status | CharField, max_length=15, choices: PENDING/PRINTED/CANCELLED, default=PENDING | printer state — §6.17 |
| created_by | FK→users.CustomUser, SET_NULL, null=True, blank=True, related_name="created_kots" | cashier who sent the ticket |
| posting_datetime | DateTimeField, auto_now_add=True | |
| order_number | PositiveIntegerField, null=True, blank=True | copied from Order for ticket display |
| original_kots | TextField, blank=True | comma-joined kot_numbers of source KOTs (for cancel KOTs) |
| cancelled_by / cancelled_at | FK→CustomUser / DateTime | audit |

**Immutability:** KOT and KOTItem snapshots cannot be edited or deleted after creation
(`save()`/`delete()` guards). Index on `(status, print_status, -posting_datetime)`.

##### KOTItem (`orders.KOTItem`)

| Field | Type | Notes |
|---|---|---|
| kot | FK→KOT, PROTECT, related_name="items" | |
| item | FK→inventory.Item, PROTECT | |
| item_name | CharField, max_length=200 | |
| qty | DecimalField | line quantity (0 on cancellation tickets) |
| cancelled_qty | DecimalField, default=0 | quantity cancelled (on cancellation tickets) |
| comments | CharField, max_length=200, blank=True | |
| customer_index | PositiveIntegerField, default=1 | groups items by customer card on ticket |

##### OrderAuditEvent (`orders.OrderAuditEvent`)

| Field | Type | Notes |
|---|---|---|
| order | FK→Order, PROTECT, related_name="audit_events" | |
| event_type | CharField, max_length=50 | SUBMITTED / CANCELLED / DISCARDED / KOTS_CREATED / RETURN_CREATED … |
| actor | FK→users.CustomUser, SET_NULL, null=True, blank=True | |
| metadata | JSONField, default=dict | immutable; cannot be edited/deleted |

#### Business logic — key flows (current)

**Order creation flow:**
1. Cashier starts an order from the POS start screen (no table grid — order type + guest count).
2. `pos_order_new` creates the draft, stamps `opening_entry` from the active shift, and assigns
   the continuous order number (`OrderSequence`).

**Add item flow (HTMX):**
1. Cashier clicks a menu card → HTMX POST to `pos_order_add_item`.
2. View resolves the MenuItem from the active menu, gets the rate; add-on dialog posts parent +
   selected add-ons atomically (§6.21).
3. `order.add_item(item, qty, customer_index, comments, rate)` — creates OrderItem (or increments
   qty if same item+guest+comments) and updates drink reservations (§6.20).
4. `order.recalculate_totals()` — `net_total`, `grand_total = rounded_total` (no tax).
5. View returns only the cart fragment (not the full page).

**Send to kitchen/bar flow (§6.17):**
1. Cashier clicks the Send action → `pos_order_sync` → `order.create_tickets(created_by)`.
2. One immutable `KOT` per department (kitchen `KOT-*`, bar `BOT-*`) from the order snapshot;
   missing production-unit config rejects the send; mixed orders are never partially dispatched.
3. Each ticket groups items by `customer_index`. `print_status=PENDING`; the stub
   `apps/orders/printing.py` currently always succeeds.
4. After the first ticket, the draft is immutable: quantity controls, menu additions, and guest
   changes are blocked. Corrections cancel the order and create a new one — no diff-sync, no
   "Order Modified"/"Partially Cancelled" tickets.

**Grouped cart UI (guest stepper + split presentation):**
- **References:** AGENTS.md "Customer card / group ordering"; FEATURES.md #214–#218; URY `OrderPanel.tsx` cart structure (Spicy-specific group ordering — no URY reference exists, URY hardcodes `no_of_pax=1`).
- **Model:** no schema change — reuses `Order.guest_count` + `OrderItem.customer_index`.
- **New logic:** `Order.change_guest_count(new_count)` — raises `ValidationError` when lowering below the highest `customer_index` that still has items (error message "Remove Customer N's items first"); otherwise saves the new count. `pos_order_update_meta` accepts either an absolute `guest_count` or a signed `guest_delta` (from the stepper), clamps to 1–50, and surfaces guard errors as a cart error banner.
- **View context:** `_group_items_by_guest(order)` builds `guest_groups` (per-index items + subtotal) for the cart template; `active_card` stays the session-driven active guest.
- **Frontend:** cart header "Guests − n +" stepper (always visible, disabled − at 1). With `guest_count > 1` the item list renders grouped: a tappable header per guest (HTMX POST to `pos_customer_card_activate`, visually highlights the active guest) followed by that guest's items and a "Customer N subtotal" row; the order grand total shows once above Pay. With `guest_count = 1` the flat list renders as before. Menu taps always add to the active guest.
- **Scope boundary (explicit):** presentation-only. The order still settles as one document with one grand total and one payment event (`settle()` untouched). Per-customer partial payment is out of scope — the cashier can accept partial sums but the system records one payment occurrence for bank reconciliation.
- **Deviations:** none — this implements the AGENTS.md group-ordering spec as originally designed (stepper-based toggle, no silent re-tagging, grouped receipts deferred to the printing app).

**Settle flow:**
1. Cashier clicks "Pay" → payment dialog opens (inline HTMX partial, §6.21).
2. Dialog shows: subtotal, grand total, payment fields per enabled mode, live
   Entered/Remaining/Change summary (client-side; `Order.settle` re-validates).
3. On submit → `order.settle(payments_data, cashier, opening_entry)` — atomic:
   revalidate lines + drink stock → validate payment rows (enabled modes declared on the open
   shift, GL mapping present, non-cash overpayment rejected, unique electronic reference) →
   assign order number → create `OrderPayment` rows → submit → convert drink reservations into
   negative SLEs. Cash-only change. Receipt printing is optional and never blocks settlement.
4. Session cleared, POS returns to the start screen.

**Cancel flow (POS, §6.17):**
1. Cashier opens the inline cancel confirmation (structured reason dropdown + optional note).
2. If tickets were sent: `cancel_sent_order()` — mark the order and its existing tickets
   `CANCELLED` (no second cancellation ticket for unpaid drafts), release drink reservations.
3. If submitted (backoffice): `cancel()` — reverse drink SLEs, create one `CNCL-*` cancellation
   ticket per production unit, preserve payment rows for audit.
4. Successful cancellation redirects to the POS order list without creating a replacement draft.

**Discard flow:**
- `discard()` — an empty, untouched draft (no items, no print, no tickets, unpaid) is marked
  `DISCARDED` with `discarded_by`/`discarded_at` audit, instead of cancellation.

**Return flow (backoffice):**
- `make_return()` — manager creates a draft return mirroring the submitted paid order's lines as
  negative qty, referencing `return_against`; submit follows the refund flow (Phase 11 completes
  the GL postings).

#### Views & URLs — POS (current)

Dedicated full-screen POS layout (no backoffice sidebar). All require login + open shift.

| URL pattern | View function | Purpose |
|---|---|---|
| `/pos/` | `pos_home` | Start screen (order type + guest count), or active order |
| `/pos/history/` | `pos_order_history` | Cashier order history |
| `/pos/history/<int:pk>/` | `pos_order_history_detail` | Order history detail |
| `/pos/history/<int:pk>/print/` | `pos_order_history_print` | Reprint receipt from history |
| `/pos/open-shift/` | `pos_open_shift` | Inline shift-open form (float per mode) |
| `/pos/close-shift/` | `pos_close_shift` | Shift reconciliation + close |
| `/pos/order/new/` | `pos_order_new` | Create a new draft order |
| `/pos/order/<int:pk>/` | `pos_order_screen` | Main POS workbench (catalogue + cart) |
| `/pos/order/<int:pk>/meta/` | `pos_order_update_meta` | Guest count / order type updates |
| `/pos/order/<int:pk>/add-item/` | `pos_order_add_item` | HTMX — add item to active customer card |
| `/pos/order/<int:pk>/add-on-dialog/<int:item_id>/` | `pos_order_add_on_dialog` | HTMX — add-on chooser dialog |
| `/pos/order/<int:pk>/update-item/<int:item_pk>/` | `pos_order_update_item` | HTMX — update qty or remove item |
| `/pos/order/<int:pk>/customer-card/<int:idx>/` | `pos_customer_card_activate` | HTMX — set active customer card |
| `/pos/order/<int:pk>/sync/` | `pos_order_sync` | HTMX — send kitchen/bar tickets |
| `/pos/order/<int:pk>/clear/` | `pos_order_clear` | Clear items (before any ticket exists) |
| `/pos/order/<int:pk>/settle/` | `pos_order_settle` | HTMX — payment dialog + process payment |
| `/pos/order/<int:pk>/cancel/` | `pos_order_cancel` | HTMX — cancel with reason |
| `/pos/order/<int:pk>/discard/` | `pos_order_discard` | Discard empty draft |
| `/pos/order/<int:pk>/print/` | `pos_order_print` | Print/reprint receipt |
| `/pos/order/<int:pk>/ticket/<ticket_type>/<action>/` | `pos_order_ticket_print` | Retry/reprint a ticket |

#### Views & URLs — Backoffice (current)

| URL pattern | View function | Purpose |
|---|---|---|
| `/backoffice/orders/dashboard/` | `orders_dashboard` | Control-room dashboard (today's revenue, drafts, tickets, pending prints) |
| `/backoffice/orders/` | `order_list` | Order register (filters/search) |
| `/backoffice/orders/<int:pk>/` | `order_detail` | Order detail (items, payments, tickets, audit) |
| `/backoffice/orders/<int:pk>/cancel/` | `order_cancel` | Cancel order (manager only) |
| `/backoffice/orders/<int:pk>/return/` | `order_return` | Create a return draft |
| `/backoffice/orders/kots/` | `kot_list` | Ticket register (filters/search) |
| `/backoffice/orders/kots/<int:pk>/` | `kot_detail` | Ticket detail |

#### Templates — POS (current)

Dedicated `pos/base.html` — full-screen, no backoffice nav, full-width footer navigation (§6.22).

| Template | Purpose |
|---|---|
| `templates/pos/base.html` | Full-screen POS base (wordmark, cashier dropdown, footer nav) |
| `templates/pos/index.html` | Start screen / active order workbench |
| `templates/pos/draft_orders.html` | Open drafts list |
| `templates/pos/close_shift.html` | Shift reconciliation + close |
| `templates/pos/order_history.html` / `order_history_detail.html` | Cashier history |
| `templates/pos/partials/gates/error.html`, `no_shift.html` | Pre-order gates |
| `templates/pos/partials/catalog/{panel,sidebar,grid,search,add_on_dialog}.html` | Catalogue (sidebar, search, grid, add-on dialog) |
| `templates/pos/partials/cart/{panel,guests,items,totals}.html` | Cart panel regions |
| `templates/pos/partials/payment/dialog.html` | Settle dialog |
| `templates/pos/partials/shell_navigation.html` | Footer navigation |

#### Templates — Backoffice (current)

Match the established inventory/menu template patterns exactly: breadcrumbs, page header
with title + subtitle, white `rounded-[1.5rem]` cards with `shadow-[0_4px_20px_rgb(0,0,0,0.05)]`,
status badges, empty states.

| Template | Purpose |
|---|---|
| `templates/backoffice/orders/dashboard.html` | Control-room landing |
| `templates/backoffice/orders/order_list.html` | Order register |
| `templates/backoffice/orders/order_detail.html` | Order detail (items/payments/tickets/audit) |
| `templates/backoffice/orders/kot_list.html` | Ticket register |
| `templates/backoffice/orders/kot_detail.html` | Ticket detail |

#### Tests

| Test file | What it covers (current) |
|---|---|
| `tests/test_order_model.py` | Creation, invoice_number, order_number (continuous), totals (no tax), rounding, settle (status, payments, change, stock conversion, table-less), cancel (status, reversal), discard, invoice_printed optional |
| `tests/test_order_item.py` | Add item, increment existing (same item+customer+comments), different comments creates separate line, customer_index, auto-fill fields, amount calculation, return-line validation |
| `tests/test_kot.py` | Creation (New Order), department routing (food→kitchen, drinks→bar), customer_index grouping, cancellation tickets, immutability, no diffing |
| `tests/test_pos_views.py` | Shift check (blocked without open shift), order start, add item (HTMX), update item, customer card activation, send (ticket creation), settle (split payment, change), cancel, discard, receipt print |
| `tests/test_backoffice_views.py` | Dashboard, order list (filters, search), order detail, order cancel (manager only), return, KOT list, KOT detail |

#### Deviations from reference — summary (current)

| Deviation | Reason |
|---|---|
| Dedicated `Order` model instead of extending POS Invoice | Django has no Frappe doctype system; a dedicated model is cleaner in Django ORM |
| `customer_index` on OrderItem and KOTItem | Spicy-specific group ordering feature — no URY reference exists (URY hardcodes no_of_pax=1) |
| Continuous order numbering instead of URY's session-based / daily reset | Client decision (§6.15): every new order = last + 1, forever, via `OrderSequence` |
| KOT routing by `Item.department` (FOOD/DRINKS) not by item_group | Spicy uses a department flag on Item, not production-unit item_group mappings (#68, #260) |
| `ticket_type` / `print_status` snapshot on KOT (separate from document `status`) | §6.17: separate document lifecycle from printer state; deliberate deviation from a single status field |
| Send-to-kitchen is one-time (no diffing / no Order Modified / Partially Cancelled) | §6.17: after the first ticket, a draft is immutable — cancel and create a new order |
| POS screen is full-screen with inline shift open | URY gates POS with backoffice "Switch to Desk"; Spicy puts shift open inline (Phase 5 decision) |
| Payment dialog is inline (HTMX partial) not a separate page | Better POS UX — cashier never leaves the order screen |
| No `Order.waiter` field; no captain/waiter transfer; no waiter login | Waiters use physical dockets only. Cashier enters all orders and runs all POS activities. URY models waiters as system users (#59, #180, #189) — Spicy does not. |
| Table transfer, KOT reprint (real transport), duplicate detection deferred (Phase 12) | Core flow first; printer transport is Phase 12 |
| No KOT delay config or role-permission M2Ms on POSProfile | Cashier-only POS; delay alerts and waiter/table role gates not used |
| No cashier % discount; no `Order.discount_amount`; no `enable_discount` / `apply_discount_on` / `allow_discount_change` | URY cashier discount dropped. Future coupon system will re-introduce discounts with fixed % codes, not free-form cashier entry. |
| No `allow_partial_payment` / `allow_rate_change` | Settle requires full payment (underpay rejected). Item rate always from menu — cashier cannot override. |
| No `Room` / `Table` models; no table-based POS start | Single hall, docket-to-cashier workflow. POS starts an order directly (order type + guest count); no floor grid. |
| No `Order.table` / `Order.room` / `Order.restaurant` / `Order.branch` | Orders are counter-built, not bound to a table; single-location cleanup (§6.14) stripped branch/profile/restaurant FKs |
| No tax system (`TaxTemplate`, `TaxRate`, `OrderTax`, `total_taxes`) | Prices are tax-inclusive / no VAT calculation. Grand total = item net total (rounded). Dropped in §6.14. |
| FOOD POS lines bypass stock; DRINKS reserve/deduct from `Restaurant.default_warehouse` | §6.20 owner-confirmed separation: food stock is operational counts, not automatic sale consumption |

#### Implementation steps (historical — complete)

This was the original build sequence for Phase 7, executed on `feat/phase7-orders-rewrite` and
merged to `main`. The orders app is now complete; the current product fact is §6.14–§6.22.

1. Create feature branch `feat/phase7-orders-rewrite`
2. Delete existing orders migrations (0001–0003) and untracked slop scripts
3. Rewrite `apps/orders/models.py` — all models with proper logic
4. Run `make migrations` — generate single fresh `0001_initial.py`
5. Review migration, run `make migrate`
6. Rewrite `apps/orders/views_pos.py` — all POS views with shift check, HTMX fragments
7. Rewrite `apps/orders/pos_urls.py` — full URL set
8. Create `templates/pos/base.html` — dedicated full-screen POS base
9. Rewrite `templates/pos/index.html` — start screen + workbench
10. Create the POS workbench partials (catalogue/cart/payment)
11. Rewrite `apps/orders/views.py` — backoffice views
12. Rewrite `apps/orders/urls.py` — backoffice URLs
13. Write the backoffice control-room templates (dashboard/order/kot)
14. Rewrite `apps/orders/forms.py` — settle form, cancel form
15. Rewrite `apps/orders/admin.py` — register all models
16. Write `apps/orders/tests/`
17. Run `make ruff` — lint + format
18. Run `make test ARGS='apps.orders'` — verify all tests pass
19. Run `make test` — verify no regressions in other apps
20. Update PLAN.md §3 status table

---


---

## §6.14–§6.23 Completed decisions & refactors (as originally written)

### 6.14 Single-Location Settings Cleanup

**Status:** complete — implemented 2026-07-30.
**Depends on:** nothing (orders core flow is complete enough; do this before printing/reports so they
build on the clean settings surface).
**FEATURES.md sections touched:** A1, A3, A4, A9, D2 (scope annotations — see "Docs" below).

#### Locked decisions (owner-approved)

1. **Keep the `Restaurant` model as the single settings record** (no rename to `SiteSettings`).
   Fold the four live `POSProfile` concerns into it. UI label: "Restaurant Settings".
2. **Prune all dead `POSProfile` fields now** — including `enable_kot_reprint`, `reprint_kot_format`,
   `print_format`. The printing/reports work re-adds the 2–3 knobs it needs when it lands;
   FEATURES.md preserves the intent rows.
3. **Delete `Branch` entirely.** No hidden row, no `get_default()`, no Location abstraction "for later".
4. **Payment-mode truth lives on `ModeOfPayment`**: `enabled` (accept this mode) + new `is_default`
   (exactly one). `POSProfilePayment` through model deleted.
5. **Multi-branch is deferred** — separate paid work. The clean single-site domain is the intended
   base; no schema hooks kept for it.

#### Why (verified findings)

- Every `Branch` FK is `PROTECT` and required, yet every row in every DB is one "Main Branch".
  `Branch.get_default()` has 7 call sites (`settings/models.py` ×4, `inventory/models.py:56`,
  `menu/models.py:25`, `staff/models.py:71`) all auto-assigning the same row.
- `POSProfile.Meta.unique_together = [("restaurant",)]` already enforces one profile per restaurant —
  it is a singleton with an identity crisis. Only 4 things on it are consumed at runtime: `warehouse`
  (`Order._deduct_stock`), `reset_order_number_daily`, `payments` M2M + `is_default`
  (`views_pos._get_payment_modes`), and `branch` (shift lookup + FK stamping). The other 25+ fields
  are write-only (verified: zero reads outside model/form/admin/tests).
- Live defects caused by the split:
  - `pos_profile_settings` form excludes `payments`, but `POSProfile.clean()` requires payment links —
    the settings page can become unsaveable with no UI path to fix it (only seed/admin can link modes).
  - `_get_pos_profile()` = `POSProfile.objects.first()` under `ordering=["name"]` — arbitrary if the
    singleton invariant is ever broken.
  - Payment-mode truth is split: POS reads profile links, the shift-float form reads
    `ModeOfPayment.enabled` (`staff/forms.py:35`). A mode can be visible at shift-open but not checkout.
  - `POSOpeningEntry.pos_profile` is set only by the POS-side shift open (`views_pos.py:154`), never by
    the backoffice path — the profile disable-guard is evadable.
  - `kot_naming_series` survives on `POSProfile` though `KOT.naming_series` was deleted (`orders/0006`).
- Already removed earlier (do not redo): Room, Table, UserRoomAssignment, TaxTemplate/TaxRate, OrderTax,
  `Order.waiter`, `Order.discount_amount`, `POSProfileUser`, multi-cashier/KOT-delay/discount toggles.
  PLAN.md §3/§6.1/§6.6/§6.7 still describe some of these as present — fix in the docs step.

#### Target model shape

```
Restaurant (singleton — one row ever; clean() rejects a second; Restaurant.load() classmethod)
  company, address, invoice_series_prefix          (unchanged)
  active_menu                FK → menu.Menu        (unchanged)
  currency                   CharField default "NGN"
  default_warehouse          FK → inventory.Warehouse, null, SET_NULL
  reset_order_number_daily   BooleanField default False
  # dropped: branch FK

ModeOfPayment
  name, type, enabled        (unchanged)
  is_default                 BooleanField — exactly one True, enforced in clean()

PaymentGLMapping
  mode_of_payment FK (now unique alone), default_account
  # dropped: company CharField + (mode, company) unique_together

ProductionUnit               # real single-site rows: Kitchen (FOOD), Bar (DRINKS)
  name (unique alone), department, warehouse (user-chosen FK, no longer auto-copied),
  block_takeaway_kot, printer_ip, printer_paper_width, printer_cut_mode
  # dropped: pos_profile FK, branch FK

Warehouse  name (unique alone), disabled          # branch FK dropped
Menu       name (unique alone), enabled           # branch FK dropped

Order      # dropped: branch, restaurant, pos_profile FKs (+ branch Meta index)
KOT        # dropped: branch FK
POSOpeningEntry / POSClosingEntry
           # dropped: branch, pos_profile FKs; "one Open shift" becomes a global invariant
```

Tradeoffs accepted: (a) no historical "which profile/branch" on old orders — profile was terminal
config, not financial data; `invoice_number` strings are frozen at creation. (b) Singleton enforced at
application level (`clean()` + `.load()`), matching codebase style — no DB CHECK constraint. (c) After
the merge, POS payment modes = all `enabled` modes; seeded-but-unwanted modes must be disabled by the
manager (note in seed output + docs).

#### Migration plan and risks

1. Schema migration A (settings/payments): add `Restaurant.currency/default_warehouse/
   reset_order_number_daily`, `ModeOfPayment.is_default`; drop `PaymentGLMapping.company`.
2. Data migration (settings, hand-written `RunPython`, separate file, backwards raises
   `IrreversibleError`): assert `Branch.objects.count() <= 1` and `POSProfile.objects.count() <= 1`
   (abort loudly otherwise); assert no name collisions that would violate the new name-only uniques
   (Warehouse/Menu/ProductionUnit across branches); copy profile `warehouse` →
   `restaurant.default_warehouse`, `reset_order_number_daily` → restaurant, `POSProfilePayment.
   is_default` → `ModeOfPayment.is_default`.
3. Rewire code to the singleton (no schema change): `views_pos` helpers
   (`_get_pos_profile` → `Restaurant.load()`; `_get_open_shift()` global; `_get_payment_modes` →
   `enabled=True` ordered `-is_default, name`; `_get_menu_data` → `active_menu`),
   `Order.assign_order_number` (global per-day/continuous), `_deduct_stock`/`_restore_stock`
   (`default_warehouse` → `item.default_warehouse` fallback preserved), KOT queries drop branch filter,
   staff `clean()`/`submit()`/`cancel()` drop the branch predicate, `staff_dashboard` single-shift card.
4. Schema migration B (per app, `makemigrations`, review each): orders (drop 3 FKs on Order, branch on
   KOT, branch index), staff (branch ×2, pos_profile), inventory (Warehouse.branch; unique name), menu
   (Menu.branch; unique name), settings (ProductionUnit.pos_profile/branch, Restaurant.branch, then
   `DeleteModel` POSProfile/POSProfilePayment/Branch).
   - All branch FKs are `PROTECT` — RemoveField must land before `DeleteModel(Branch)`; verify the
     generated settings migration's `dependencies` include every other app's RemoveField migration.
   - `Order.Meta` branch index drops implicitly with the field — confirm in the generated file.
   - Do not squash migrations; history stays append-only.

#### Implementation steps

1. `make test` — freeze baseline (~490 tests); note the files expected to be deleted/rewritten.
2. `git checkout -b feat/single-location-settings` (per git workflow rules; do not commit to main).
3. Migration A + data migration (above). Verify copy on a seeded dev DB.
4. Rewire consumers (step 3 above). POS/backoffice tests should still pass against the old FK columns.
5. Migration B per app, in dependency order: orders → staff → inventory → menu → settings.
6. UI cleanup: delete branch views/urls/forms/templates + Branch/POSProfile/POSProfilePayment admins;
   merge `pos_profile_form` into one manager-guarded Restaurant Settings page (also fixes the
   GET-unguarded asymmetry at `settings/views.py:265` and the duplicate `restaurant_detail`/
   `restaurant_update` POST handling); settings dashboard cards → Restaurant Settings / Production
   Units / User Roles; drop Branch columns (staff lists, production-unit templates); drop the Branches
   link in `templates/backoffice/dashboard.html`; fix the `pos/index.html` "No POS profile" error copy.
7. Tests: delete `settings/tests/test_branch.py`, `test_pos_profile.py`, `test_pos_profile_payment.py`,
   `test_pos_opening_entry_pos_profile_fk.py`; strip `branch=` kwargs everywhere via a shared
   `make_settings()` fixture helper; convert multi-branch tests to name-uniqueness / global-one-shift
   tests (`menu/test_menu.py` branch2, `inventory/test_warehouse.py`, staff "Other Branch" cases);
   add singleton-guard and exactly-one-default-mode tests.
8. Seeds + scripts: rewrite `seed_pos_setup.py` (settings singleton, 3 warehouses, modes + default,
   2 production units, active menu), `seed_menu_catalog.py:215–218`, `scripts/inspect_db.py`.
9. Docs: FEATURES.md scope preamble + per-row scope tags (out-of-scope: #2–6, #36–39, #49–51, #56,
   #58–59, #65, #128–129, #170/#173 clauses, B2 #182–192, D2 #269/#271/#272, branch clauses in #1/#7/
   #14/#57/#84/#85/#92/#94/#121/#145/#146/#164/#274; waiter mentions in #48/#152/#180/#231/#235);
   PLAN.md §2 app table, §3 status table, §4 summaries, and stale model lists in §6.1/§6.6/§6.7;
   AGENTS.md workspace line for `settings` + a "Single-location settings" architecture note.
   Describe everything as current product fact — no project-era language.
10. Gate: `make ruff`, `make test`, `make manage ARGS='check'`, then manual smoke: seed → open shift
    from POS → order → sync KOTs → mark printed → settle → close shift.

#### Deviations from reference (to record)

| Deviation | Reason |
|---|---|
| `Branch` removed; all branch FKs dropped | Single location; ERPNext Branch exists for multi-location isolation the client does not have |
| `POSProfile` merged into the `Restaurant` singleton; 25+ unconsumed fields pruned | One till, one settings surface; ERPNext POS Profile is multi-terminal config. Fields re-added only when a feature consumes them |
| Payment-mode selection folded onto `ModeOfPayment` (`enabled` + `is_default`); `POSProfilePayment` deleted | Two switches drove one concept and could disagree (shift-open vs checkout) |
| `Restaurant.reset_order_number_daily` removed; numbering is one continuous counter | Client decision — every order increments the last by one, always (§6.15) |
| Orders/KOTs/shift entries carry no branch/profile FKs | One world — documents follow the single settings record |
| `PaymentGLMapping.company` dropped | Pseudo-company key; only ever held `Restaurant.company` |
| Multi-branch deferred (paid add-on); no schema hooks kept | Clean single-site domain is a better extension base than a fake multi-branch skeleton |

#### Non-goals

- Do not delete Warehouse, ProductionUnit, ModeOfPayment, PaymentGLMapping, OpeningPayment/
  ClosingPayment, the department split, customer cards, or the submit/cancel workflow.
- Do not rename `Restaurant` or introduce a Location abstraction.
- Do not touch the PEP 758 `except ValueError, TypeError:` forms in `views_pos.py` (valid Python 3.14).
- Do not squash or rewrite migration history.

### 6.15 Order Numbering — Continuous Sequence (deviation)

**Reference:** ERPNext Naming Series (`frappe.core.doctype.series`) — a persistent counter row that
the controller bumps atomically; URY `custom_reset_order_number_daily` (dropped).

**Client decision:** every new order's number is the previous order's number + 1, forever. No daily
reset, no per-branch series, no gaps by design.

**Implementation:**

- `OrderSequence` (orders app): `name` unique + `current_value` PositiveInteger. One row, `name="order"`,
  seeded by `orders/0013_seed_order_sequence` from `MAX(order_number)` over existing orders.
- `Order.assign_order_number()` runs inside `transaction.atomic()`:
  `OrderSequence.objects.select_for_update().get(name="order")`, `current_value += 1`, saves the order
  with the new number — same lock-serialised read-modify-write ERPNext does with `FOR UPDATE` on `Series`.
  Concurrent settles each get a distinct number (proven by `OrderSequenceConcurrencyTest`).
- `Order.order_number` now has `db_index=True` so history lookups stay fast.
- `Restaurant.reset_order_number_daily` field, form entry, and settings-page heading removed
  (`settings/0020`).

**Notes:** drafts consume a number at creation (`pos_order_new` calls `assign_order_number()`), so
deleted drafts leave gaps — same as ERPNext, where the Series counter never rewinds.

### 6.16 POS Template Fragmentation (deviation)

**Client decision:** break `templates/pos/index.html` (was ~410 lines) into fragment files under
`templates/pos/partials/{gates,cart,catalog,payment}/`, joined back together with `{% include %}`.

**Deviation from AGENTS.md:** the documented convention says partials stay inline in the template
where used and `{% include %}` is reserved for components shared across 3+ templates. Both are
overridden here for readability; each fragment is used by exactly one parent template.

**Preserved invariant:** the HTMX endpoints still render `pos/index.html#cart` and
`#payment_dialog` — `index.html` keeps thin `{% partialdef %}` wrappers whose bodies are a single
`{% include %}`. Views are untouched. Each fragment that uses template filters declares its own
`{% load humanize %}` (included templates do not inherit loads from the parent).

**Layout:**

- `partials/gates/error.html`, `no_shift.html` — pre-order gates (full-screen cards)
- `partials/cart/panel.html` — the `#cart-panel` HTMX target, includes the three cart regions
- `partials/cart/guests.html` — guest stepper
- `partials/cart/items.html` — item rows (grouped by customer, flat, or empty state)
- `partials/cart/totals.html` — success banners, grand total, actions dropdown, Pay button
  (always visible, including on an empty cart; Pay is disabled when the cart has no items)
- `partials/catalog/panel.html` — search + group chips, includes the grid
- `partials/catalog/grid.html` — menu item cards
- `partials/payment/dialog.html` — settle dialog

### 6.17 Send to Kitchen & Bar — Ticket Print State and POS Cancellation (deviation)

**References consulted:**

- URY `ury_kot/ury_kot.json` and `ury_kot.py` — one ticket doctype routed by production unit;
  printing occurs on submit but has no persisted print result or retry state.
- URY `ury_kot_generate.py` — existing departmental routing and ticket item snapshots.
- URY `ury_order.py` and `ury_pos_invoice.py` — cancellation and invoice lifecycle rules.
- Spicy `apps/orders/models.py`, `views_pos.py`, and `templates/pos/partials/cart/` — existing
  KOT diffing, clear, print, and action-bar implementation.

**Client decision:** after the first kitchen/bar ticket is created, a draft order is immutable.
The cashier cancels it and creates a new order instead of generating `Order Modified` or
`Partially Cancelled` tickets in place. This supersedes the diff-sync behavior documented in
§6.7 for the POS workflow.

**Model changes:**

- `Order.cancelled_by`, `cancelled_at`, `cancel_reason`, and `cancel_reason_note` record the
  lightweight unpaid POS cancellation. The reason choices are wrong order, customer changed mind,
  cashier error, and other.
- Existing `KOT` remains the shared KOT/BOT document; no separate BOT model is introduced, matching
  URY. `ticket_type` snapshots `kitchen` or `bar`, while `production_unit` remains the routing FK.
- Existing KOT document `status` (`SUBMITTED`/`CANCELLED`) is preserved. `print_status` separately
  tracks `PENDING`, `PRINTED`, or `CANCELLED`; this separation is a deliberate deviation from the
  requested single status field and avoids mixing document lifecycle with printer state.
- Existing `KOTItem` is the immutable item snapshot. `BaseModel.created_at` supplies ticket creation
  time, and `created_by` records the cashier who sent the ticket.

**Business logic:**

- Initial send creates at most one ticket per department, with a KOT number for kitchen and BOT
  number for bar. Missing production-unit configuration rejects the send rather than silently
  claiming the order was dispatched; mixed orders are never partially dispatched. Existing
  `block_takeaway_kot` production settings are respected.
- The print interface is `apps/orders/printing.py`; its stub returns `PrintResult(success, ticket_type)`
  and currently succeeds. Kitchen and bar calls are independent.
- Failed attempts leave `print_status=PENDING`, exposing separate retry actions. Successful tickets
  expose separate reprint actions; reprint never creates a new record.
- Clear deletes items only before any ticket exists. After a ticket exists, POS cancellation marks
  the order and its existing tickets cancelled without creating a second cancellation ticket.
  The existing full cancellation method remains available to the backoffice lifecycle.
- Order and ticket snapshot model methods reject post-send edits; admin ticket records are exposed
  as audit data rather than an edit path.

**HTMX/UI behavior:**

- The action row is `[Action] [Receipt] [Pay]` with 25%/25%/50% widths. Receipt is available for
  any draft order with items and changes to Reprint Receipt after a successful print.
- The Action dropup shows Send before tickets, independent kitchen/bar retry or reprint actions
  afterward, and Clear versus Cancel Order based on ticket state.
- Item quantity controls, menu additions, and guest changes are hidden/blocked after send. The
  entire `#cart-panel` remains the swap target, so totals and ticket state update without OOB swaps.
- Cancel opens an inline confirmation form with the structured reason dropdown and optional note;
  successful cancellation redirects to the POS order list without creating a replacement draft.

**Confirmed receipt-printing deviation:** Receipt printing is optional for both Dine In and Take
Away draft orders. Payment does not depend on printing; the current draft screen offers print and
reprint actions, while the in-process stub remains until Phase 12.

### 6.18 Orders Backoffice Control Room

**Client decision:** the backoffice Orders navigation has its own dashboard, order register, and
Kitchen & Bar ticket register. The dashboard is a query-based overview; no reporting model or
stored aggregates are introduced.

**View/query behavior:**

- `orders:dashboard` shows today’s paid revenue, order counts, open drafts, cancelled orders,
  ticket volume, and the current pending-print queue, plus recent orders and pending tickets.
- The order register annotates item count, ticket count, and pending-print count in one query and
  supports invoice/customer/order search plus status and order-type filters.
- The ticket register supports search, ticket type, ticket kind, document status, and print-status
  filters. It uses `created_at`, `created_by`, `ticket_type`, and `print_status` from the current
  ticket model rather than the former KOT-only display.

**Templates/navigation:**

- `templates/backoffice/orders/dashboard.html` is the Orders control-room landing page.
- Order and ticket tables link directly to their own detail records and to each other.
- Order detail now shows customer/department item data, payment/totals state, ticket document and
  print badges, ticket creator/timestamps, and structured cancellation audit information.
- Ticket detail now shows KOT/BOT type, production unit, document status, print status, creator,
  timestamps, source order, and immutable item snapshot lines.
- The shared backoffice navigation exposes Dashboard, Order Register, and Kitchen & Bar Tickets
  under Orders on desktop and mobile.

### 6.19 Orders POS review decisions

- Receipt printing is optional for Dine In and Take Away and never blocks settlement. A successful
  receipt print freezes the draft snapshot; corrections require cancellation and a new order or an
  audited manager override.
- Payment settlement accepts only enabled methods declared on the active opening entry. Non-cash
  overpayments are rejected until a refund/credit workflow exists; cash may produce change.
- Sent-order cancellation creates one cancellation ticket per affected production unit and sends it
  through that unit's print interface. Original tickets retain their print state and record the
  cancellation actor.
- Stock deduction and cancellation restoration rules in this section are superseded by §6.20:
  only DRINKS use the Restaurant singleton's default warehouse; FOOD POS lines bypass inventory.
- The POS allows at most 50 open normal drafts per active shift. The manager may change this limit in
  Restaurant Settings.
- POS JavaScript is an intentional runtime requirement. Full no-JavaScript fallbacks are not planned.
- KOT modification/diffing is removed. A sent order is cancelled and replaced with a new order; old
  persisted dummy data does not require compatibility behavior.
- Real printer-agent transport, deployment security hardening, and GL/accounting postings remain
  deferred to their planned phases. Phase 7 still records operational payments and shift totals.

### 6.20 Inventory and POS stock rules (owner-confirmed, superseding)

This section supersedes conflicting stock behavior in §6.2, §6.7, §6.14, and the original §6.19
stock bullet. Implementation must update those workflows to these rules without introducing BOM,
recipe, manufacturing, warehouse-role, or legacy-compatibility abstractions.

#### Reference files consulted

| Reference file | What is retained or deliberately changed |
|---|---|
| `references/erpnext-develop/erpnext/stock/doctype/item/item.json` and `item.py` | Independent sales, stock, and purchase flags; Spicy applies the confirmed receipt/POS rules below |
| `references/erpnext-develop/erpnext/stock/doctype/bin/bin.json` | `actual_qty`, `reserved_qty`, valuation, and item+warehouse cache semantics |
| `references/erpnext-develop/erpnext/stock/doctype/stock_entry/stock_entry.json` and `stock_entry.py` | Submit/cancel ledger workflow and transfer valuation; Spicy narrows allowed purposes/routes |
| `references/erpnext-develop/erpnext/stock/doctype/stock_entry/services/material_transfer.py` | Required source/target warehouses and rejection of same-warehouse transfers |
| `references/erpnext-develop/erpnext/stock/doctype/stock_reconciliation/stock_reconciliation.json` and `stock_reconciliation.py` | Count-to-ledger adjustment and cancellation reversal behavior |
| `references/erpnext-develop/erpnext/stock/doctype/stock_reconciliation_item/stock_reconciliation_item.json` | Counted quantity/current quantity/valuation line shape |
| `references/erpnext-develop/erpnext/stock/doctype/purchase_receipt/purchase_receipt.json` and `purchase_receipt.py` | Purchase receipt posting lifecycle and accepted warehouse concept |
| `references/erpnext-develop/erpnext/accounts/doctype/pos_invoice/pos_invoice.json` and `pos_invoice.py` | POS item warehouse/update-stock and submit/cancel stock lifecycle used as the comparison point |
| `references/ury-develop/ury/ury/doctype/ury_order/ury_order.py` | URY sets `update_stock=1` for the whole POS invoice; FOOD bypass below is an explicit deviation |
| `references/ury-develop/ury/ury_pos/api.py` | URY exposes one POS Profile warehouse to POS; Spicy retains one configured Bar/POS warehouse on Restaurant |
| `references/ury-develop/ury/ury/doctype/ury_production_unit/ury_production_unit.json` | Production-unit warehouse linkage; Spicy reuses it for Kitchen/Bar configuration rather than adding Warehouse roles |
| `references/ury-develop/pos/src/components/MenuCard.tsx` and `MenuList.tsx` | Disabled-card interaction pattern; Spicy applies it specifically to unavailable drinks |

#### Model and configuration changes

- Keep `Item.is_sales_item`, `Item.is_stock_item`, and `Item.is_purchase_item` independent.
  `department` and MenuItem linkage do not imply stock or purchase eligibility. FOOD supports:
  sellable/non-stock/non-purchase; sellable+stock, optionally purchasable; and internal
  stock+purchasable. Receipt lines require `is_stock_item=True` and `is_purchase_item=True`.
- Add nullable `Restaurant.store_warehouse` (`ForeignKey` to `Warehouse`, `SET_NULL`) for central
  Store. Keep `Restaurant.default_warehouse`, but document and label its real meaning as Bar/POS
  deduction warehouse. Do not add `Warehouse.role`, `Warehouse.type`, or equivalent flags.
- Reuse `ProductionUnit.warehouse`: FOOD must identify Kitchen; DRINKS must equal
  `Restaurant.default_warehouse`. Restaurant settings/production-unit validation rejects disabled,
  duplicate, or inconsistent Store/Bar/Kitchen configuration. Store must be distinct from both
  operational targets; Bar and Kitchen must be distinct.
- Keep `Bin.reserved_qty`; it becomes the authoritative draft DRINKS reservation total for each
  item+Bar/POS warehouse. `actual_qty - reserved_qty` is available-to-sell stock.
- Add required `StockReconciliation.reason` choices: `PHYSICAL_COUNT`, `CONSUMPTION`,
  `WASTE_DAMAGE`, `CORRECTION`. Keep `remarks` optional and `posting_date` user-selectable.
- Remove `MATERIAL_ISSUE` from `StockEntry.purpose` and all related form, view, validation, template,
  admin, service, and test branches. No replacement issue model is introduced.
- Keep the existing order warehouse snapshot only for DRINKS deduction/reversal audit. FOOD lines
  never use it. Existing `OrderItem.department` and `stock_item` snapshots remain historical item
  facts, but `stock_item=True` does not make a FOOD line stock-affecting.
- No BOM, recipe, ProductBundle-as-BOM, ingredient-consumption, production, or repack workflow is
  introduced. Kitchen consumption is represented only by Stock Reconciliation.

#### Business rules and atomic workflows

**Receiving:**

- Material Receipt and Purchase Receipt always post into `Restaurant.store_warehouse`; users cannot
  choose or override a different target. Missing/disabled Store configuration blocks draft submit.
- Both receipt types accept only Items where `is_stock_item` and `is_purchase_item` are true. FOOD,
  DRINKS, sellability, and menu membership do not otherwise affect receipt eligibility.

**Material Transfer:**

- Preserve ERPNext's paired source-out/target-in ledger semantics and source valuation on the target
  entry. The source deduction, target receipt, document status change, and cancellation reversals run
  in one `transaction.atomic()` block with affected Bins locked in deterministic order.
- Normal source is always `Restaurant.store_warehouse`. Target is derived from item department:
  DRINKS → `Restaurant.default_warehouse` (configured Bar); FOOD → FOOD ProductionUnit warehouse
  (configured Kitchen). Every line is validated against its department-derived target.
- Reject user-overridden targets, Store→Store, Bar/Kitchen→Store, Bar↔Kitchen, Kitchen→Kitchen,
  Bar→Bar, reverse, and cross-operational routes. Prevent negative Store stock before any line posts.
- Transfer cancellation reverses both sides atomically and must also prevent an invalid negative
  balance at the operational warehouse when later stock activity has consumed the transferred qty.

**Stock Reconciliation:**

- Reconciliation is the only one-sided stock adjustment path and supports all configured warehouses.
  The entered count is compared with the locked current Bin and posts only the signed difference.
- `CONSUMPTION` is the Kitchen consumption-count workflow and is valid only for the configured FOOD
  ProductionUnit warehouse and FOOD stock items. It remains a count-to-actual adjustment, not an
  inferred BOM issue. `PHYSICAL_COUNT`, `WASTE_DAMAGE`, and `CORRECTION` retain their general
  adjustment meaning.
- No weekly cadence is enforced. Posting dates are flexible. Submit and cancel each run atomically;
  cancellation uses reversal SLEs and fails rather than leaving a partially reversed document.

**POS reservation and settlement:**

- FOOD POS lines never validate stock, reserve stock, deduct stock, or restore stock, even when their
  Item or order snapshot has `is_stock_item=True`. Item/MenuItem disable and sellable/menu checks still
  apply. FOOD remains inventory-available in catalog responses.
- Every DRINKS Item exposed for POS sale must have `is_stock_item=True`; invalid menu/configuration is
  rejected rather than silently bypassing stock. DRINKS use only `Restaurant.default_warehouse`.
- Adding a DRINKS line or increasing its qty atomically locks its Bin, verifies
  `actual_qty - reserved_qty >= increase`, and increases `reserved_qty`. Decreasing qty, removing a
  line, clearing a draft, cancelling a draft, or deleting a draft atomically releases the exact
  reservation. These paths are idempotent and never permit negative `reserved_qty`.
- Settlement locks the order and all affected drink Bins, revalidates reservations and actual stock,
  decreases `reserved_qty`, creates the negative POS SLEs, and submits payment/order in one atomic
  transaction. A failure rolls back payment, status, reservation, and stock together. This conversion,
  not an independent release followed by deduction, prevents overselling.
- Submitted-order stock reversal/refund applies only to the snapshotted DRINKS lines and warehouse.
  Draft cancellation/deletion releases reservations but creates no SLE. FOOD cancellation/refund
  creates no stock restoration automatically.

#### HTMX and reporting behavior

- POS catalog queries annotate DRINKS availability from the configured Bar/POS Bin. An unavailable
  drink remains in the grid with a greyed disabled card, an out-of-stock indication, no add/detail
  action, and appropriate disabled/ARIA state. FOOD cards do not use Bin availability.
- Server-side add, increment, detail-dialog submit, decrement, remove, clear, cancel, delete, and pay
  endpoints enforce the same rules; disabled markup is not the security boundary. Reservation or
  configuration errors return the existing cart/catalog HTMX fragment with an actionable banner and
  no partial quantity change.
- After any drink quantity mutation, affected cart and catalog fragments refresh so availability and
  disabled state reflect the committed reservation. Concurrent requests cannot both reserve the last
  unit.
- Stock Reconciliation create/edit requires the reason dropdown and optional remarks. List/report
  filters support date range, warehouse, and reason. The Kitchen consumption comparison report shows
  FOOD sales quantities/revenue beside Kitchen `CONSUMPTION` reconciliation quantities for the same
  selected date range; it does not claim recipe-level variance.

#### Migrations and data policy

- Generate schema migrations with `makemigrations`: add `Restaurant.store_warehouse`, add required
  reconciliation reason, and alter StockEntry purpose choices. Review dependencies because settings
  references inventory while inventory workflows read the Restaurant singleton.
- Current data is dummy. No legacy compatibility layer or preservation migration is required for
  Material Issue. Before dropping the choice, delete dummy Material Issue documents and their related
  dummy SLE/Bin effects using an explicit development data-reset policy; do not reinterpret them as
  transfers or reconciliations.
- Existing dummy reconciliation rows may be reset rather than guessed into reasons. New databases
  require an explicit reason. Warehouse records are reused and assigned through Restaurant and
  ProductionUnit configuration; do not infer or persist warehouse roles.

#### Test plan

- Item/receipt tests cover every independent flag combination, including internal FOOD stock purchase
  items, sellable non-stock FOOD, invalid non-purchase receipt lines, and menu eligibility independent
  of purchase eligibility.
- Settings tests cover Store/Bar/Kitchen required relationships, disabled warehouses, distinctness,
  and DRINKS ProductionUnit equality with `Restaurant.default_warehouse`.
- Stock Entry tests cover Store-only receipts; department-derived Store→Bar/Kitchen transfers; mixed
  transfers; source valuation preservation; every forbidden reverse/cross route; negative-stock
  prevention; and all-or-nothing submit/cancel failures.
- Reconciliation tests cover all four required reasons, optional remarks, arbitrary posting dates,
  Kitchen-only consumption validation, count differences, report filters, and atomic cancellation.
- POS model/view tests cover FOOD stock bypass, explicit disable enforcement, DRINKS stock requirement,
  visible disabled out-of-stock cards, add/increment reservation, decrement/remove/clear/cancel/delete
  release, atomic settlement conversion, rollback on payment/SLE failure, refund/reversal scope, and
  two concurrent attempts for the last drink unit.
- Reporting tests compare Kitchen consumption with FOOD sales over date filters without BOM-derived
  expectations. Run inventory, settings, orders, reports, full tests, Ruff, Django checks, and the
  frontend type/build checks after implementation.

#### Deviations from prior plan and references

| Deviation | Reason |
|---|---|
| Material Issue is removed completely, superseding §6.2 and ERPNext Stock Entry purposes | One-sided operational reductions belong to structured Stock Reconciliation; current data is dummy and needs no compatibility path |
| FOOD POS bypasses validation, reservation, deduction, and restoration even when `is_stock_item=True`, unlike URY's invoice-wide `update_stock=1` and the prior §6.7/§6.19 plan | Owner-confirmed separation: food stock represents Store/Kitchen operational counts, not automatic sale consumption |
| POS reserves/deducts only DRINKS from `Restaurant.default_warehouse` | Bar stock is directly countable and must prevent overselling; the field's semantic is Bar/POS deduction warehouse |
| `Restaurant.store_warehouse` added; receipts cannot choose a warehouse | All inbound goods first enter central Store before controlled operational transfer |
| No `Warehouse.role/type`; configured Restaurant and ProductionUnit FKs define Store, Bar, and Kitchen | Avoid duplicate warehouse classification and reuse the existing single-location model |
| Normal transfers are restricted to Store→department target | The restaurant does not use ERPNext's unrestricted warehouse graph; reverse/cross routes would bypass controlled counts |
| Stock Reconciliation gains required structured reasons and owns Kitchen consumption counts | Supports auditable adjustment reporting without BOM or Material Issue |
| Out-of-stock drinks remain visible but disabled instead of using URY/earlier hide-unavailable behavior | Cashier needs catalogue visibility while the server still prevents selection and overselling |
| Purchase eligibility requires both stock and purchase flags, not department/menu linkage | Item capabilities are independent and internal purchasable FOOD items need not be sellable |

### 6.21 POS Workbench Redesign — Three-Column Layout, Catalogue Search, and Add-On Dialog

**Reference source:** URY POS frontend (`references/ury-develop/pos/src/`) for layout and
interaction patterns only. No URY source code is reused — the workbench stays server-authoritative
Django + HTMX + Alpine + Tailwind v4.

#### Layout (agreed design direction)

| Zone | Position | Contents | Source |
|---|---|---|---|
| Category sidebar | Left, fixed width `w-52 lg:w-60` | `All items`, `Specials`, one button per `ItemGroup`; counts per group; active state via `aria-pressed`; dark `slate-900` on the otherwise light workbench | URY category rail → replaces the horizontal `ItemGroup` strip |
| Catalogue | Center, fills remaining width | Persistent search box (Ctrl/Cmd+K focuses, Escape clears), group/scoped filter label, menu-card grid | URY menu list grid |
| Cart/workbench | Right, fixed `w-[360px]…2xl:w-[420px]` | Guests stepper, order type, grouped/flat items, totals, Send/Action/Receipt/Pay | URY cart panel |

The old horizontal `ItemGroup` strip was removed. Cross-checked against the prior review note that
formal tabs/pills in the backoffice were not to be reused for the POS catalogue; the sidebar is a
dedicated POS shell component.

#### Catalogue caching, search, and specials

- `panel.html` search is a persistent input `x-model="search"`; `index.html` keeps the
  `activeGroup`, `search`, `specialsOnly`, and `noMatches` Alpine state scoped to `pos_content`.
- `grid.html` filters with `x-show`: `(activeGroup === 'All' || matches group)` AND
  `(!specialsOnly || special_dish)` AND (`search` empty OR item name contains search).
- `noMatches` is computed in `x-effect` after every state change with `$nextTick`, counting
  visible card elements; when the grid is empty the server-side "Menu unavailable" card renders.
- Beginners/persistence: filter state is purely client-side and resets on any cart-partial swap;
  catalogue remains static between orders.

#### Menu cards

- Cards are semantic `<button>` elements (attribution: image/initials fallback with `line-clamp-2`,
  Special badge, rate, `Add +`/`Unavailable` label, HTMX request spinner). Cards with add-ons show a
  "Choose add-ons" affordance and open the dialog; cards without add-ons post directly to
  `pos_order_add_item` with `qty=1`.
- Items with `stock_unavailable`, sent orders, and printed invoices render `disabled` with reduced
  opacity and no HTMX action — the catalogue is a read-only map of what can still be ordered.

#### Add-On dialog (new)

- New URL `pos_order_add_on_dialog` (`order/pk/add-on-dialog/item_id/`) renders
  `add_on_dialog.html` — an optional add-on chooser (only enabled, sales-ready add-ons on the
  active menu), quantity stepper, and optional comments (max 200 chars).
- The dialog submits a normal `POST` to `pos_order_add_item`; the parent item and each selected
  add-on become separate cart lines on the active customer card, each with its own resolved
  `MenuItem` rate, inside one `transaction.atomic()`.
- Server-side validation in `pos_order_add_item`: add-on IDs must parse; every selected add-on must
  be a configured `ItemAddOn` of the parent; every add-on must be on the active menu, enabled and
  sales-ready. Any failure aborts the whole add (including the parent — no partial cart state).
- Add-ons are priced like menu items: resolved from `MenuItem.rate` on the active menu, never from
  client input.

#### Cart improvements

- Cart wrapper renders the ticket paper (`#FFF9ED`) and reuses `catalog_oob` to refresh the fetched
  catalogue grid after quantity-affecting actions, carrying the active server-side filter state.
- Guests stepper posts `guest_delta` ±1 (blocked when it would strand higher-numbered guests' items);
  order-type buttons post `order_type`; both go through `pos_order_update_meta` which locks rows
  `select_for_update` and refuses edits once tickets exist or the receipt was printed.
- Success/error banners are rendered from context flags (`sync_success`, `print_failures`,
  `ticket_print_success`, `clear_success`, `receipt_print_*`).
- Pay anchor href + hx-get double binding opens the settle dialog; disabled while the cart is empty.

#### Payment dialog

- Live Entered/Remaining/Change summary is computed client-side from the payment-mode inputs;
  `total` is the server-rendered rounded total (authoritative `Order.settle` still validates).
- Non-cash modes gain an optional reference field; the submit button shows a processing state
  and disables against double submission.
- Autofill on focus fills the balance of the total minus other inputs and dispatches an `input`
  event so the summary updates.

#### Backend additions

- `_build_order_context` prefetches `item__add_ons__add_on_item__menu_items`, passes
  `item_groups`, `menu_items` (filtered `disabled=False`), `special_item_count`, and marks
  DRINKS items `stock_unavailable` from the Bar/POS warehouse Bin available quantity.
- `pos_order_update_item` refuses quantity edits on printed/sent orders via the shared
  print/send guards; cart rows disable the `±` controls accordingly.
- `pos_close_shift` view and `close-shift/` route were added alongside the redesign so the POS
  screen exposes a Close-shift link in the top bar (matching the shift lifecycle in §6.5).

#### Deviations from reference

| Deviation | Reason |
|---|---|
| URY POS is React; Spicy is Django templates + HTMX + Alpine | Project hard rule — no React/Vue/DRF |
| Add-ons are optional and become separate cart lines, not a bundled line | Keep item snapshots and ledger lines independent; server re-resolves every rate |
| Parent + add-ons commit in one atomic block after full server validation | Prevents a partial cart write when an add-on is stale or removed from the menu |
| Variants are templates excluded from the catalogue; sellable variants are standalone cards | User-confirmed scope decision (§6.19 review); avoids a variant-group dialog |

### 6.22 POS Shell Navigation — Cashier Menu and Full-Width Footer

The POS shell keeps the three-column workbench and cart actions unchanged while moving
shell navigation into a full-width footer. The navbar uses the `Spicy` text wordmark on
the left and a cashier dropdown on the right. The dropdown contains the existing
permission-aware Backoffice link, cashier Close shift link, and POST-preserving Sign out
link. The footer is present across POS pages and centers icon-over-label navigation for
Orders and the active order's Checkout screen. Checkout remains visible but disabled when
no active order is available.

This is a layout-only change: Checkout continues to link to `pos_order_screen`; payment
continues to be initiated by the cart's Pay action. Hugeicons are used for new controls,
and the existing `data-logout-link` behavior is preserved.

### 6.23 Paid-Order Ticket Guarantee — Auto Ticket Creation on Settlement (deviation)

**Status:** complete — implemented 2026-08-15. `settle_order` auto-creates and dispatches
departmental tickets when an order has none; `pos_order_ticket_print` retry/reprint now
accepts SUBMITTED orders; `order_history_detail.html` shows retry actions for pending
NEW_ORDER tickets on paid orders; seven tests in `POSSettleTest` cover the guarantee.
(`ExpectedClosingAmountsTest`/`SubmitClosingEntryTest` fixtures in `apps/staff/tests/`
gained a FOOD `ProductionUnit` — their settle calls now require one.)

**Client decision:** a paid (SUBMITTED) order must always have a kitchen/bar ticket record for
every production unit its items belong to. Ticket creation at settlement is mandatory — it is
not a configurable setting. The send-to-kitchen button remains the primary path (dine-in sends
at order time, payment later); settlement is the backstop that guarantees the record when the
cashier never clicked send. Receipt printing stays fully manual — no manager setting is added.
The cashier flow is unchanged: the auto-creation happens server-side inside `settle_order`.

**References consulted:**

- URY `pos/src/pages/Orders.tsx` (line 326) — cancel/edit window covers Draft, Unbilled, and
  Recently Paid orders; URY dispatches to production via the send action, not at payment.
- URY `ury_pos_invoice.py` / `ury_order.py` — invoice submission does not create KOTs;
  the ticket is a production artifact with its own lifecycle (the industry's
  "auto-dispatch on payment" pattern exists in Toast/MISA/Vendion as a configurable
  behaviour, never as a payment gate).
- Spicy `apps/orders/services.py` (`settle_order`, `create_tickets`, `dispatch_tickets`),
  `apps/orders/views_pos.py` (`pos_order_settle`, `pos_order_ticket_print`), and
  `templates/pos/order_history_detail.html` — current behaviour verified: `settle_order`
  has zero ticket logic; `create_tickets` is called only from `pos_order_sync`;
  `pos_order_ticket_print` allows retry only for `status__in=[DRAFT, CANCELLED]`.

**Why a deviation:** ERPNext/URY never gate payment on tickets and leave dispatch to the send
action. Spicy keeps that (send stays the primary path) but adds the mandatory backstop
because the bar is a separate business entity — an unsent paid bar order means the bar's
production for that shift is unaccounted in its own P&L. The industry default (configurable
auto-dispatch) is rejected: the client decided the rule is unconditional, and no setting
surface is added for it.

#### Business logic

1. **`settle_order`** (services.py): after `locked.assign_order_number()` and before payment-row
   creation, when `not locked.kots.exists()`:
   - Compute the planned tickets with the same departmental routing as `create_tickets`
     (production-unit lookup, `block_takeaway_kot` respected, mixed orders never partially
     dispatched).
   - If the plan is **empty** (e.g. takeaway where every unit blocks KOTs) → skip silently;
     no ticket is required by config, so the rule has no department to cover. Settlement
     proceeds.
   - If a department has items but **no production unit is configured** → raise the same
     `ValidationError` the send button raises ("Configure a production unit before sending:
     …"). Settlement is blocked: a ticket record cannot be guaranteed, and this is a config
     error that would also break the send action. Consistent with §6.17.
   - Otherwise create the `KOT`/`KOTItem` snapshots exactly as `create_tickets` does
     (KOT number from `_ticket_prefix_for_type`, `print_status=PENDING`, `created_by=cashier`,
     `KOTS_CREATED` audit event) — then `dispatch_tickets(created)` outside the ticket-building
     block so a printer failure never blocks settlement (leaves `PENDING`, covered by retry).
   - Implementation note: extract the planned-ticket computation (items grouped by department,
     production-unit map, `block_takeaway_kot` skip, missing-department detection) from
     `create_tickets` into a shared module helper. `create_tickets` keeps its exact current
     behaviour and error messages; the settle path uses the helper and treats an empty plan as
     a skip instead of raising "No kitchen or bar ticket is required for this order."
   - Placed after `assign_order_number()` so tickets carry the final order number; the whole
     settle is one transaction, so any later failure rolls the tickets back with it.

2. **`pos_order_ticket_print`** (views_pos.py): extend the order lookup from
   `status__in=[DRAFT, CANCELLED]` to `status__in=[DRAFT, CANCELLED, SUBMITTED]`. The existing
   ticket filter (`print_status=PENDING`, `status=SUBMITTED`) already matches the auto-created
   NEW_ORDER ticket on a submitted order, and the CANCELLED-order path (cancellation tickets)
   is untouched. Two consequences to accept:
   - Retry now works for pending tickets on submitted orders — the gap this phase closes.
   - Reprint (manager-gated) also becomes available for submitted orders' PRINTED tickets;
     harmless — same snapshot, no new record — and consistent with the history-detail
     "Reprint receipt" for submitted orders.
   - Generalise the failure message ("No {ticket_type} cancellation ticket is ready…" →
     "No {ticket_type} ticket is ready for that action.") since it now serves both contexts.

3. **`order_history_detail.html`**: for `order.status == "SUBMITTED"`, add a retry form
   mirroring the CANCELLED block, shown for tickets with `type == "New"` and
   `print_status == "PENDING"`, labelled "Retry kitchen ticket" / "Retry bar ticket", posting
   to `pos_order_ticket_print` with `action="retry"`.

4. **Backoffice:** the ticket register (`orders:kot_list`, §6.18) already filters pending-print
   tickets and is read-only — manager visibility exists; no change.

**No model changes, no settings changes, no UI-flow changes.** The cashier's cart, send,
pay, and history screens are identical; the only visible difference is that printers may fire
at settle for a never-sent order.

#### Tests (apps/orders/tests/test_pos_views.py)

| Test | Asserts |
|---|---|
| `test_settle_auto_creates_tickets` | Settling a never-sent food order creates 1 kitchen KOT (`type="New Order"`, `print_status="PRINTED"` via stub, `created_by=cashier`, carries the order number), order SUBMITTED, `KOTS_CREATED` then `SUBMITTED` audit events |
| `test_settle_existing_tickets_no_duplicates` | Send first, then settle → `kots.count()` unchanged |
| `test_settle_skips_when_no_ticket_required` | Takeaway with `block_takeaway_kot` on the only unit → settles with 0 tickets |
| `test_settle_missing_production_unit_blocks` | No ProductionUnit for the department → `ValidationError`, order stays DRAFT, no payments |
| `test_retry_submitted_pending_ticket` | Settle with patched failing print → PENDING; POST retry → PRINTED |
| `test_retry_submitted_no_pending_ticket` | Settle with successful print → retry yields the generic message, no crash |
| `test_history_detail_retry_button_submitted` | Submitted order with pending ticket shows the retry form; without it, no form |

Existing settle tests need no changes — the test base configures both production units
(`POSViewTestBase.setUpTestData`) and none of the settle tests assert an absence of tickets.
`test_settle_takeaway_no_print_needed` still passes because a ProductionUnit exists for FOOD
(the auto-created ticket does not affect its assertions).
