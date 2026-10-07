# Products and Menu Workflow

## Item Master

`inventory.Item` is the shared product/material record. Its sales, stock, and purchase flags are independent. `department` is `FOOD` or `DRINKS`. Department drives ticket routing and the DRINKS-only POS stock policy. It does not set whether an item is inherently stock-tracked.

`Item.save()` generates an `ITEM-####` code under a lock. It makes variant templates non-sellable/non-stock/purchase. It removes add-on rows when an item becomes non-sellable. `Item.clean()` prevents templates from being stock/sales/purchase items. It validates variant parents. It blocks turning off sales while an enabled menu line exists. It blocks changing `stock_uom` or turning off stock/purchase while UOM conversion rows exist. It also blocks changing `stock_uom` or turning a recipe ingredient sellable/non-stock while recipe rows exist. Stock + purchase items can carry `ItemUOMConversion` rows (bulk unit → stock unit), used only on purchase receipts.

## Menu Resolution

`Restaurant.active_menu` identifies the menu used by POS. `_build_order_context()` requires the menu to be enabled and filters its `MenuItem` rows to `disabled=False`. Search checks menu line name, item name, and item code. Categories come from `ItemGroup`. The special filter checks `MenuItem.special_dish`.

`MenuItem.rate` is the selling rate. `MenuItem.clean()` requires a sellable, enabled, non-template Item. Its denormalized `item_name` fills only when blank, so later Item renames do not automatically update menu lines.

## Add-ons

`ItemAddOn` connects a parent item to an add-on item. The model requires the add-on to be active, sellable, non-template, and present on a menu. The POS dialog loads only add-ons with an enabled active-menu price. `apply_add_on_line()` validates the selected IDs, adds parent and add-ons as separate `OrderItem` rows, applies each active-menu rate, and recalculates totals.

## Variants

`ItemVariant` links a parent item to a variant item and requires the variant on at least one menu. The seed command puts variant items directly on the menu and omits the template line. The current POS has no variant selection endpoint or dialog. A parent selection workflow is not implemented.

## Backoffice

The Menu backoffice groups menu setup into a dashboard, menu register, menu-line register, add-on register, and variant register. The menu detail page identifies the configured enabled `Restaurant.active_menu` as **Live on POS**. Activation remains controlled from Restaurant settings. Menu-line screens show the source inventory code, group, department, customer-facing rate, and availability state.

The add-on register resolves each relationship against the enabled active menu. It displays the effective `MenuItem.rate`, or an explicit unpriced state when no enabled active-menu line exists. The add-on editor explains that the relationship controls availability while the menu line owns the price. `apps/menu/views.py` provides direct login-protected CRUD for menus, menu lines, add-ons, and variant relationships. `Menu` and `MenuItem` have no service layer. Delete endpoints exist for menu lines, add-ons, and variants. There is no menu delete endpoint.

## Recipes

Sellable FOOD items carry ingredient cards under Inventory → Recipes. One active `Recipe` per dish (variants and add-ons hold their own, drinks have none). `RecipeItem` qtys are per output in ingredient `stock_uom`. The form shows a live plate-cost preview at Kitchen WAC. Item and menu-item detail pages link to the dish's card. `seed_menu_catalog` also seeds example cards (Jollof Rice, Egusi Soup, Quarter Chicken).

## Setup Commands

- `seed_menu_catalog` atomically seeds Nigerian restaurant raw/finished items, variant families, menu lines, add-ons, the active menu, and example recipes. It also seeds purchase UOM conversions (drinks: 1 Crate = 24 Bottle, rice: 1 Bag = 50 Kg). Dummy carton/crate SKUs are no longer created.
- `seed_pos_setup` creates Restaurant, Bar/Kitchen/Store warehouses, payment modes/mappings, and production units. It invokes menu seeding when no active menu exists.

- `InventoryConfig.ready()` seeds baseline UOMs and item groups after migrations.

## Catalog Failure Cases

- No Restaurant or no enabled active menu renders a setup error.
- Disabled menu lines are hidden.
- A drink without `is_stock_item`, a missing/disabled Bar warehouse, or no available unreserved quantity is shown disabled with a setup/out-of-stock message.
- The catalog query does not independently filter `Item.disabled` or `Item.is_sales_item`. The add POST rejects those states server-side.
