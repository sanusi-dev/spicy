from django.contrib.auth.decorators import login_not_required, login_required
from django.shortcuts import redirect, render

from apps.users.decorators import backoffice_required, staff_required


@login_not_required
def home(request):
    """Route users by role; signed-out visitors land on the sign-in page."""
    if request.user.is_authenticated:
        if request.user.has_backoffice_access:
            return redirect("web:dashboard")
        if request.user.has_staff_role:
            return redirect("web:pos_index")
        return redirect("web:pending_approval")
    return redirect("account_login")


@backoffice_required
def dashboard(request):
    """Render the backoffice navigation dashboard."""
    return render(
        request,
        "backoffice/dashboard.html",
        {
            "modules": [
                {
                    "title": "Orders",
                    "description": "Sales, tickets, and returns",
                    "icon": "shopping-bag-03",
                    "icon_bg": "bg-orange-50",
                    "icon_color": "text-orange-600",
                    "url": "orders:dashboard",
                    "links": [
                        {"label": "Order register", "url": "orders:order_list"},
                        {"label": "Kitchen & Bar tickets", "url": "orders:kot_list"},
                    ],
                },
                {
                    "title": "Inventory",
                    "description": "Stock and warehouse control",
                    "icon": "package",
                    "icon_bg": "bg-blue-50",
                    "icon_color": "text-blue-600",
                    "url": "inventory:dashboard",
                    "links": [
                        {"label": "Items", "url": "inventory:item_list"},
                        {"label": "Stock ledger", "url": "inventory:stock_ledger_list"},
                        {"label": "Stock entries", "url": "inventory:stock_entry_list"},
                        {"label": "Reconciliations", "url": "inventory:reconciliation_list"},
                        {"label": "Purchase receipts", "url": "inventory:purchase_receipt_list"},
                        {"label": "Stock reports", "url": "inventory:stock_balance_list"},
                    ],
                },
                {
                    "title": "Menu",
                    "description": "Prices and POS catalogue",
                    "icon": "menu-restaurant",
                    "icon_bg": "bg-rose-50",
                    "icon_color": "text-rose-600",
                    "url": "menu:dashboard",
                    "links": [
                        {"label": "Menus", "url": "menu:menu_list"},
                        {"label": "Menu items", "url": "menu:menu_item_list"},
                        {"label": "Add-ons", "url": "menu:add_on_list"},
                        {"label": "Variants", "url": "menu:variant_list"},
                    ],
                },
                {
                    "title": "Accounting",
                    "description": "Ledger and supplier payables",
                    "icon": "book-open-01",
                    "icon_bg": "bg-indigo-50",
                    "icon_color": "text-indigo-600",
                    "url": "accounting:dashboard",
                    "links": [
                        {"label": "Chart of accounts", "url": "accounting:chart_of_accounts"},
                        {"label": "Journal entries", "url": "accounting:journal_entry_list"},
                        {"label": "Supplier invoices", "url": "accounting:supplier_invoice_list"},
                        {"label": "Supplier payments", "url": "accounting:supplier_payment_list"},
                        {"label": "GL entries", "url": "accounting:gl_entry_list"},
                    ],
                },
                {
                    "title": "Reports",
                    "description": "Daily snapshots, sales, and ledger reports",
                    "icon": "analytics-01",
                    "icon_bg": "bg-violet-50",
                    "icon_color": "text-violet-600",
                    "url": "reports:daily_pnl_list",
                    "links": [
                        {"label": "Daily P&L", "url": "reports:daily_pnl_list"},
                        {"label": "Today's sales", "url": "reports:sales_today"},
                        {"label": "POS register", "url": "reports:pos_register"},
                        {"label": "Trial balance", "url": "reports:trial_balance"},
                        {"label": "Profit & loss", "url": "reports:simple_pnl"},
                        {"label": "P&L settings", "url": "reports:pnl_settings"},
                    ],
                },
                {
                    "title": "POS & shifts",
                    "description": "Payments and cash control",
                    "icon": "credit-card",
                    "icon_bg": "bg-emerald-50",
                    "icon_color": "text-emerald-600",
                    "url": "staff:dashboard",
                    "links": [
                        {"label": "Opening entries", "url": "staff:opening_entry_list"},
                        {"label": "Closing entries", "url": "staff:closing_entry_list"},
                        {"label": "Payment modes", "url": "payments:mode_list"},
                        {"label": "GL mappings", "url": "payments:gl_mapping_list"},
                    ],
                },
                {
                    "title": "Settings",
                    "description": "Restaurant and team access",
                    "icon": "setting-06",
                    "icon_bg": "bg-gray-100",
                    "icon_color": "text-gray-600",
                    "url": "settings:dashboard",
                    "links": [
                        {"label": "Restaurant settings", "url": "settings:restaurant_settings"},
                        {"label": "Production units", "url": "settings:production_unit_list"},
                        {"label": "User roles", "url": "settings:staff_list"},
                    ],
                },
            ],
        },
    )


@staff_required
def pos_index(request):
    return render(request, "pos/index.html")


@login_required
def pending_approval(request):
    if request.user.has_staff_role:
        return redirect("web:home")
    return render(request, "web/pending_approval.html")
