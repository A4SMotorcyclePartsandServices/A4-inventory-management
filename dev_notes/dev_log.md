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

## Customer / Item Export Feature

Implemented 2026-03-17

Scope
- Added catalog export for Items as CSV.
- Added customer export as CSV.
- Added customer loyalty PDF export with tier filtering.

Routes / entry points
- `routes/reports_route.py`
- `GET /export/items`
- `routes/customer_route.py`
- `GET /export/customers`
- `GET /reports/customers/points?tier=...`

UI placement
- Item export button lives in `templates/index.html` in the page header beside the item count.
- Customer export controls live in `templates/customers/customers_list.html` in the upper-right action row.
- Customer PDF export uses a dropdown beside the CSV button to avoid adding three separate tier buttons.

Item CSV behavior
- Item CSV exports the full item catalog, not just the limited rows shown on initial page load.
- Export includes practical catalog fields plus computed `current_stock`.
- Export intentionally excludes legacy / low-value columns like `mechanic` and `vendor_id`.
- Vendor display in the CSV now resolves with `COALESCE(v.vendor_name, i.vendor)` so rows using the centralized vendor model still show a readable vendor name.

Customer CSV behavior
- Customer CSV exports active customers only.
- Export columns are aligned to the customer list use case:
- `Customer No.`
- `Customer Name`
- `Total Visits`
- `Loyalty Points`
- `Last Visit`
- `Vehicles`
- `Membership Date`
- `Customer ID` was intentionally excluded from the export.
- Date formatting for export was normalized to `Mon DD, YYYY` style for both `Last Visit` and `Membership Date`.

Customer PDF behavior
- PDF uses the same customer-export dataset as the CSV, then filters by loyalty points tier.
- Supported tiers:
- `0-50`
- `51-99`
- `100+`
- Results are sorted by highest points first, then by customer name.

## Customer Debt Statement / Debt Payments Grouping

Implemented 2026-03-17

Problem solved
- Debt statements were previously tied to one `sale_id`, so one customer with multiple utang sales produced multiple separate statements.
- The Debt Payments tab also listed debt rows per sale instead of per customer, which made the audit harder to read for repeat debt customers.

Current behavior
- Debt statements are now customer-based instead of sale-based.
- The primary printable route is now `/debt/statement/customer/<customer_id>`.
- Legacy `/debt/statement/<sale_id>` links still work, but now redirect to the matching customer statement.
- Each customer statement consolidates all active utang for that customer into one printable account statement.

Statement contents
- Customer header now shows:
- `Customer`
- `Customer No`
- `Last Visit`
- Statement summary now shows:
- `Active Utang`
- `Total Paid on Active Utang`
- `Running Balance`
- Active debt sales are listed together in one section instead of one statement per receipt.
- Payment history is aggregated across the customer's active utang.
- A running balance ledger is built by combining debt postings and debt payments in chronological order.

Sale breakdown details
- Each active debt sale now includes its sold items from `sales_items`.
- Each active debt sale now includes its sold services from `sales_services`.
- Item / service breakdowns are rendered below each receipt in a separate full-width detail row.
- The detail area uses compact mini tables so wide print space is used more efficiently.

Debt Payments tab behavior
- Debt Payments summary API now groups rows by customer instead of one row per debt sale.
- The table now shows one customer row with combined totals for debt, paid amount, and remaining balance.
- Receipt display now acts as a grouped debt-record label, including multi-receipt counts when applicable.
- The payment-history modal can now load aggregated customer debt payments using `customer_id`.
- Print actions from the debt table now open the consolidated customer statement.

Print / formatting updates
- Statement print layout was tightened to reduce wasted space on paper.
- Print view uses smaller margins, reduced padding, and denser table spacing.
- The top information area uses a print-only two-column layout for customer details and summary cards.
- Statement date fields were normalized to date-only display in the printable statement.
- `Member Since` was replaced with `Last Visit`, sourced from the customer's latest sale date.

Implementation notes
- Main route updates live in `routes/debt_route.py`.
- Data shaping and ledger logic live in `services/debt_service.py`.
- Printable statement UI lives in `templates/debt/statement.html`.
- Debt Payments tab UI behavior lives in `templates/users/users.html`.
- PDF template lives in `templates/reports/customer_points_pdf.html`.
- PDF page is browser-print driven, following the same preview / print-to-PDF pattern as the existing report pages.

Architecture notes
- Customer export data was centralized in `_get_customer_export_rows()` inside `customer_route.py` so CSV and tiered PDF use the same source of truth.
- Tier definitions are centralized in `POINT_TIERS` in `customer_route.py`.
- This keeps export rules close to the customer reporting route for now, but if customer reporting grows further it may be worth moving export assembly into a dedicated customer reporting service.

Branding / report design
- Customer points PDF was restyled to mirror existing report pages.
- It now uses the same branded header direction as the other PDFs, including `static/media/logo.png`.

## Debt Statement Consolidation / Print Layout

Implemented 2026-03-17 22:37:54

Scope
- Reworked debt statements from sale-level to customer-level.
- Grouped the Admin `Debt Payments` tab by customer instead of one row per debt sale.
- Improved the printable debt statement layout to use space more efficiently on paper.

Routes / entry points
- `routes/debt_route.py`
- `GET /debt/statement/customer/<customer_id>`
- `GET /debt/statement/<sale_id>` now redirects to the matching customer statement when a customer exists.
- `GET /api/debt/customer/<customer_id>/payments`
- `GET /api/debt/summary` now returns grouped customer rows for the admin debt tab.

Customer statement behavior
- One customer statement now aggregates that customer's active utang instead of generating one statement per sale.
- Statement summary shows:
- active utang count
- total paid on active utang
- running balance
- Active utang section now shows each receipt with:
- receipt number
- sale date
- vehicle
- total / paid / balance
- item breakdown
- service breakdown
- Payment history section now aggregates payments across the customer's active utang.
- Running balance ledger now combines debt postings and payments across the customer timeline.

Customer source of truth
- Statement uses `sales.customer_id` and the real `customers` table instead of grouping by raw `customer_name`.
- `Last Visit` on the printable statement now comes from the same `MAX(s.transaction_date)` customer history signal already used on the customer list.
- Printable statement dates were normalized to date-only display; no times are shown in the statement anymore.

Admin debt tab changes
- Debt summary rows are now grouped per customer.
- The old sale-based print button now opens the consolidated customer statement.
- The eye button now opens aggregated payment history for that customer's active utang.
- Legacy rows without a usable `customer_id` still fall back to the old sale-based behavior to avoid breaking access.

Print / layout notes
- Added print-only compaction for margins, spacing, and table density.
- Header + customer info + summary now use a tighter print layout.
- Item/service breakdowns were moved into a full-width follow-up row under each receipt so they no longer waste the empty side columns.
- Breakdown details now render as mini-tables instead of compact text rows.

## Cash Ledger Rules / Sales Admin Filters

Implemented 2026-03-17

Cash Ledger updates
- Cash in categories were updated to:
- `Petty Cash`
- `From Gcash Account`
- `From Bank Account`
- `For Payables`
- `Others`
- Cash out categories remain:
- `Parts Purchase`
- `Staff Expense`
- `Utilities`
- `Supplies`
- `Other Expenses`
- `Mechanic Payout`
- Description is now required for:
- cash in category `Others`
- cash out category `Other Expenses`
- cash out category `Utilities`
- When `Utilities` is selected, the description placeholder and submit warning now explicitly say:
- `Please indicate which utility this is for.`
- Required description hints on the Cash Ledger form now turn red for better visibility.

Mechanic payout automation
- Cash Ledger mechanic payout rows now send an `auto_description` field from payout reporting.
- Prefill behavior now uses:
- mechanic name only for normal mechanic payouts
- `mechanic name + quota top up` when quota top-up was applied
- Existing mechanic payout ID/date autofill behavior remains intact.

Sales admin tab updates
- Added a payment-status toggle to the Sales tab in admin/users UI:
- `All`
- `Paid`
- `Partial`
- `Unpaid`
- Sales API now accepts `payment_status`.
- Sales admin service now validates and applies the payment status filter server-side.
- Sales summary chips now visually match the selected status:
- green for `Paid`
- yellow for `Partial`
- red for `Unpaid`
- Discount chip styling remains separate from payment-status chip styling.
