# Dev Log / Architecture Notes

## Architecture

Search is now handled by `inventory_service`

Index route only loads top 50 rows

Search route in `app.py` below index route

### Service Structure

transaction_services
- handles transactional operations like adding new items

inventory_services
- search and inventory QOL features

routes_api.py
- API routes
- dashboard logic

transaction_route.py
- handles IN and OUT database saving

reports_services
- reusable reporting functions

reports_routes
- reporting endpoints

utils
- date formatting utilities

## UI

base.html is the central design template

## Database

Database migrated from SQLite → PostgreSQL

## Vendor Centralization

Implemented 2026-03-15

Vendor data now lives in a dedicated vendors table.

Current behavior
- Items store `vendor_id` as the default / usual vendor.
- Purchase orders store `vendor_id` plus frozen vendor snapshot fields for reporting and analytics.
- Vendor add/select flow is shared across `items.html` and `order.html`.
- New vendors can be created inline from the item and PO forms.

Validation / UX
- Item creation now requires vendor selection in both frontend and backend.
- PO creation now requires vendor selection in both frontend and backend.
- Missing-item add flow from loyalty, Stock IN, and PO now pre-fills the item name in `items.html`.

Audit / Admin
- Audit trail item detail modal now resolves vendor name from `vendor_id` via the vendor master table.

## PO Page / Review / PDF UI Pass

Implemented 2026-03-16

Status wording alignment
- PO overview page now uses client-facing labels:
- `Approved PO's`
- `Partial Deliveries`
- `Completed Deliveries`
- `Cancelled PO's`
- PO detail modal label changed from `Approval History` to `PO History`.
- Review page section label changed from `Approval Timeline` to `PO History`.
- PO PDF status wording now matches the UI:
- `Approved`
- `Partial Delivery`
- `Completed Delivery`
- `Cancelled PO`

PO detail modal behavior
- Non-partial PO modal now shows item financials with:
- `Item`
- `Price`
- `Total`
- `Ordered`
- `Received`
- Non-partial PO modal now shows a separate summary bar below the table for `Overall Total`.
- Partial-delivery PO modal now uses a dedicated cumulative breakdown with:
- `Price`
- `Ordered Qty`
- `Ordered Total`
- `Delivered Qty`
- `Delivered Total`
- `Remaining Qty`
- `Remaining Balance`
- Partial-delivery PO modal also shows inline per-item arrival notes and summary cards for:
- `PO Total`
- `Delivered Value`
- `Remaining Balance`

Review page behavior
- Review page item breakdown now mirrors the PO modal logic.
- Non-partial review view shows price and ordered total columns plus a separate `Overall Total` summary block.
- Partial-delivery review view shows ordered vs delivered vs remaining value columns and summary cards.
- Review page note display now prefers cancellation notes from PO history when the PO has been cancelled.
- PO modal note display now shows the original PO note entered during PO creation.

PO PDF behavior
- PO PDF now renders vendor snapshot details from the purchase order record:
- `vendor_name`
- `vendor_address`
- `vendor_contact_person`
- `vendor_contact_no`
- Partial-delivery PO PDF now uses a dedicated cumulative breakdown table with delivered vs remaining value columns.
- Partial-delivery PO PDF includes inline item notes plus summary cards for total, delivered value, and remaining balance.

## Vendor Dropdown Workflow

Implemented 2026-03-16

Applies to
- `transactions/items.html`
- `transactions/order.html`

Current behavior
- Vendor selection now uses a dropdown instead of free-text vendor search on the item create page and PO create/edit page.
- Dropdown lists all active vendors from the `vendors` table.
- Dropdown includes a `-- New Vendor --` option.
- Choosing `-- New Vendor --` opens the existing add-vendor modal immediately.
- When a vendor is created successfully from the modal, it is inserted into the dropdown and auto-selected.
- Selected vendor details still render in the vendor summary card below the input.

Route / data flow
- `transaction_route.py` now loads active vendors server-side for `items.html` and `order.html`.
- Vendor validation still depends on `vendor_id`; the change is UX-focused and keeps the centralized vendor model intact.

## PO Receipt History / Partial Delivery Tracking

Implemented 2026-03-16

Problem solved
- `purchase_orders.received_at` only stores one timestamp, so it was not enough to represent multiple arrival batches for partial deliveries.
- Partial POs now use dedicated receipt-history tables instead of relying on a single overwritten PO-level receive date.

Schema
- Added `po_receipts` as the header table for each delivery batch / receive action.
- Added `po_receipt_items` as the item-level rows for each delivery batch.
- `purchase_orders.received_at` is still kept, but now acts as the latest receipt timestamp rather than the full history source.

Receive flow
- One submit from `receive.html` now creates one `po_receipts` row.
- Each received item in that submit creates one `po_receipt_items` row.
- Inventory is still logged to `inventory_transactions` for stock ledger / audit purposes.
- `po_items.quantity_received` is still updated as the cumulative received total.
- The receive flow now rejects all-zero submissions instead of allowing an empty confirmation.

UI / PDF behavior
- PO modal in `order_overview.html` now shows a `Delivery History` section using receipt batches.
- Review page in `order/review.html` now shows the same delivery-history batches.
- PO PDF now includes a `Delivery History` section with receipt batches and per-item delivered amounts.

Source of truth
- Delivery / arrival history: `po_receipts`, `po_receipt_items`
- Stock movement ledger: `inventory_transactions`
- Cumulative PO received quantities: `po_items.quantity_received`
- Latest receipt timestamp for summary use: `purchase_orders.received_at`

## Debt Feature

Relevant files
- debt_service
- debt_route
- utang.html

## Query Example for PO History

SELECT change_reason, quantity, transaction_date, user_name, notes
FROM inventory_transactions
WHERE reference_id = ?
AND reference_type = 'PURCHASE_ORDER'
AND transaction_type = 'IN'
ORDER BY transaction_date ASC

## Loyalty Program Feature
Loyalty note:
- current client usage separates stamps and points, so mixed-program logic is not being fixed now
- if a future loyalty program enables both stamps and points in one program, one sale can earn both
- if reward_basis is STAMPS_OR_POINTS, redemption currently prefers stamps first and can leave points untouched for later eligibility

## Manual IN / PO Receive Cost Naming

Noted 2026-03-17

- Manual `IN` form uses the label `Unit Cost`, but posts `unit_price` to the backend.
- PO receive uses `po_items.unit_cost`.
- Both flows ultimately write the incoming cost into `inventory_transactions.unit_price`.
- Both flows also compare that incoming cost against `items.cost_per_piece`, and if different, they update `items.cost_per_piece` and add a `COST_PER_PIECE_UPDATED` audit row.
- So today the real meaning is:
- `inventory_transactions.unit_price` = generic transaction-level price snapshot
- `items.cost_per_piece` = current master cost
- Naming drift exists because `inventory_transactions.unit_price` is also used on sales, where it acts more like selling price, not purchase cost.
