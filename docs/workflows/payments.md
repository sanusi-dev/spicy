# Payment Workflow

## Payment Layers

The `payments` app owns payment master data only:

- `ModeOfPayment`: name, type (`CASH`, `BANK`, `GENERAL`, `PHONE`), enabled flag, and default flag.
- `PaymentGLMapping`: one-to-one mapping to a leaf `LedgerAccount` (Phase 6 FK; previously a name string).

Sale payment rows are `orders.OrderPayment`, linked to an Order and ModeOfPayment. There is no external payment gateway, refund model, or separate payment-entry document.

## Configuration

`ModeOfPayment` has a conditional unique constraint allowing at most one default. Its `clean()` and `save()` prevent unsetting the only default. Payment views/forms provide login-protected CRUD. GL mapping deletion is POST-only; other ordinary form saves do not use an explicit service transaction.

A GL mapping may only point at a leaf account that is not a sales account: `PaymentGLMapping.clean()` rejects accounts used as `ProductionUnit.income_account`, `ProductionUnit.sales_returns_account`, `Restaurant.default_income_account`, or `Restaurant.default_sales_returns_account`, and the inverse checks on `Restaurant.clean()` and `ProductionUnit.clean()` reject pointing a sales account at an already-mapped payment account. This is the config layer; at posting time `_resolve_payment_account` rejects any payment account that is an income account, and `post_order_gl`/`post_refund_gl` still fail closed if an account ends up on both the debit and credit side of one voucher (such legs would otherwise net to zero and vanish from the GL).

## Shift Opening

`OpeningFloatForm` dynamically creates one non-negative Decimal field per enabled mode. `open_shift()` creates one `OpeningPayment` per submitted mode. Opening does not require GL mappings, although settlement later does.

## Settlement

The POS dialog is GET `/pos/order/<pk>/settle/`; POST extracts `payment_<mode_pk>` and `reference_<mode_pk>` fields. `settle_order()` validates:

- at least one positive payment;
- finite two-decimal amount;
- enabled mode;
- mode declared at shift opening;
- existing non-empty GL mapping;
- reference length <= 100;
- a non-empty reference for every non-cash row when `Restaurant.require_payment_reference` is enabled;
- full coverage of rounded order total.

Electronic reference numbers are unique per payment mode when non-empty. Cash references are not duplicate-checked. The requirement is enforced in `OrderPayment.save()` as well, so a non-cash row cannot be created without a reference outside the settlement flow.

Cash may exceed the total and produces `Order.change_amount`. Any overpayment containing a non-cash row is rejected. Underpayment is rejected. Payments are created inside the settlement transaction, and pre-existing payment rows cause manager review validation.

## Shift Closing

`staff.services.expected_closing_amounts()` sums submitted order payments in the period and subtracts `Order.change_amount` for cash orders and the refund rows of returns submitted in the period (negative `OrderPayment` amounts are proportional to the source net tenders per mode). The result is opening float plus net collected amount. `submit_closing_entry()` stores expected, counted, difference, and total short/excess values.

## Current Non-Features

- No partial payment or outstanding balance: settlement requires full payment.
- No discounts or write-offs: order totals are line sum plus whole-unit rounding.
- No refund/void service: paid returns cannot be submitted.
- No payment provider integration: all electronic modes are manual records. References are optional unless `Restaurant.require_payment_reference` is enabled.
