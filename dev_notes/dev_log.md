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

## Payables Feature

Implemented 2026-03-18

Scope
- Added a new `Payables` page for cheque monitoring and payable tracking.
- Covers both PO-linked supplier payments and fully manual / free-form payables such as rent.
- Page is staff-accessible and admin-accessible.
- PDF report generation is admin-only.

Core business model
- A payable is the obligation record.
- A cheque is the payment instrument linked to that payable.
- Payables do not touch `Cash Ledger`, because cheque handling is treated separately from cash on hand.

PO-based payables behavior
- PO-based payables only come from actual PO delivery batches.
- One delivery batch creates one payable.
- Source of truth is `po_receipts`, not `purchase_orders.received_at`.
- This means one PO can create multiple payable rows when it is received in multiple batches.
- Payable amount for PO-based entries uses the exact receipt-batch total, derived from `po_receipt_items.line_total`.
- The payable stores PO and vendor snapshot details for reporting / UI display:
- `po_number_snapshot`
- `vendor_name_snapshot`
- `po_created_at_snapshot`
- `delivery_received_at_snapshot`

Manual payables behavior
- Manual payables are fully free-form.
- Intended for rental and other non-PO cheque obligations.
- Required fields:
- `payee_name`
- `description`
- `amount_due`
- Optional field:
- `reference_no`

Schema
- Added `payables` table.
- Added `payable_cheques` table.

`payables` table responsibilities
- stores payable source type:
- `PO_DELIVERY`
- `MANUAL`
- stores linked PO receipt batch when applicable via `po_receipt_id`
- stores display snapshot values for vendor / PO metadata
- stores:
- `payee_name`
- `description`
- `reference_no`
- `amount_due`
- `status`

`payable_cheques` table responsibilities
- stores cheque-level data:
- `cheque_no`
- `cheque_date`
- `due_date`
- `cheque_amount`
- `status`
- `notes`
- stores reminder flags:
- `reminded_due_minus_7`
- `reminded_due_today`

Status model
- Payable statuses:
- `OPEN`
- `PARTIAL`
- `FULLY_ISSUED`
- `CANCELLED`
- Cheque statuses:
- `ISSUED`
- `CLEARED`
- `CANCELLED`
- `BOUNCED`

Status sync rules
- Payable status is recalculated from cheque totals.
- Active cheque amount includes cheque statuses:
- `ISSUED`
- `CLEARED`
- `CANCELLED` cheques do not count toward issued amount.
- `OPEN` means no active cheque amount yet.
- `PARTIAL` means active cheque amount is below payable amount.
- `FULLY_ISSUED` means active cheque amount meets or exceeds payable amount.

Automation
- PO receive flow now auto-creates a payable after a receipt batch is saved.
- Integration point is inside `receive_purchase_order()` in `transactions_service.py`.
- This keeps PO receipt creation and payable creation in the same DB transaction.
- Duplicate PO-based payable creation is prevented by unique `po_receipt_id`.

Routes / entry points
- New blueprint: `payables_route.py`
- Page route:
- `GET /transaction/payables`
- Manual payable create:
- `POST /transaction/payables/manual`
- Issue cheque:
- `POST /transaction/payables/<payable_id>/cheques`
- Update cheque status:
- `POST /transaction/payables/cheques/<cheque_id>/status`
- Admin PDF route:
- `GET /reports/payables`

UI behavior
- Navigation link was added below `Receivables` in `base.html`.
- Page shows:
- summary cards
- PO-based payables section
- manual payables section
- cheque history per payable
- cheque issuance modals
- manual payable modal
- PO-based `View PO Page` button deep-links into the PO overview and auto-opens the correct PO modal using:
- `po_id`
- `open_po=1`
- Internal delivery-batch IDs are intentionally hidden from the UI because they are backend-facing.

PDF / reporting
- Added a Payables PDF report page following the same browser print / save-to-PDF pattern used by the existing report pages.
- Report includes cheque-level information such as:
- cheque date
- due date
- cheque number
- payee
- source type
- linked PO number when applicable
- amount
- status
- report filter uses Flatpickr date range on the Payables page.
- If no date range is provided, the backend defaults the report to the current month.

Permissions
- All logged-in users can access the Payables page.
- Only admins can open the Payables PDF report route.
- Non-admin users do not see the `Generate PDF` button in the page UI.

Reminders / notifications
- Cheque reminders are generated for:
- 7 days before due date
- exact due date
- Reminder generation is no longer tied to opening the Payables page.
- A standalone script now exists for scheduled reminder runs:
- `scripts/run_payables_reminders.py`
- Intended production command:
- `python scripts/run_payables_reminders.py`
- Hosted deployment must provide a daily scheduled task for this command.
- Recommended run time is `8:00 AM` Asia/Manila.
- The existing notification bell only reads notifications already stored in the DB; it does not generate payable reminders itself.

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
- Cash in categories were updated to: `Petty Cash`, `From Gcash Account`, `From Bank Account`, `For Payables`, `Others`.
- Cash out categories remain: `Parts Purchase`, `Staff Expense`, `Utilities`, `Supplies`, `Other Expenses`, `Mechanic Payout`.
- Description is now required for:
- cash in category `Others`
- cash out category `Other Expenses`
- cash out category `Utilities`
- When `Utilities` is selected, the description placeholder and submit warning now explicitly say `Please indicate which utility this is for.`
- Required description hints on the Cash Ledger form now turn red for better visibility.

Mechanic payout automation
- Cash Ledger mechanic payout rows now send an `auto_description` field from payout reporting.
- Prefill behavior now uses:
- mechanic name only for normal mechanic payouts
- `mechanic name + quota top up` when quota top-up was applied
- Existing mechanic payout ID/date autofill behavior remains intact.

Sales admin tab updates
- Added a payment-status toggle to the Sales tab in admin/users UI: `All`, `Paid`, `Partial`, `Unpaid`.
- Sales API now accepts `payment_status`.
- Sales admin service now validates and applies the payment status filter server-side.
- Sales summary chips now visually match the selected status:
- green for `Paid`
- yellow for `Partial`
- red for `Unpaid`
- Discount chip styling remains separate from payment-status chip styling.

## Refund / Exchange Feature

Implemented 2026-03-18

Scope
- Added a dedicated staff-facing `Refunds` page for item refunds and item swaps / exchanges.
- Refund processing was moved out of the admin-only `users.html` page.
- Refunds apply only to item lines; services are shown for context only and are not refundable.
- Refund flow updates stock, cash movement, admin history, and reports.

Core business model
- A refund reverses the original sold item quantity back into inventory.
- Refund amount is based on the sold line's stored `final_unit_price`, so item discounts are respected.
- Refunds are sale-linked and can work for both regular sales and quick sales because the feature does not depend on a customer master record.
- Fully paid sales can be refunded; non-refundable states are blocked in the refund flow.

Refund rules
- Refundable quantity is tracked per `sales_item` line.
- Services never contribute to refundable quantity.
- Refund status now depends on remaining refundable item quantity:
- `Not Refunded`
- `Partially Refunded`
- `Fully Refunded`
- This avoids mislabeling mixed item + service sales as only partially refunded after all refundable items are already returned.

Schema / data model
- Added `sale_refunds` as the refund header table.
- Added `sale_refund_items` as the refunded item detail rows.
- Added `sale_exchanges` to link a refund to its replacement sale when a swap is processed.

Refund numbering
- Refund numbers now use `RF-{OR No.}-{MMDD}`.
- If needed for uniqueness on repeated same-day refunds:
- `RF-{OR No.}-{MMDD}-2`
- `RF-{OR No.}-{MMDD}-3`

Exchange numbering
- Exchange numbers now use `EX-{OR No.}`.
- If needed for uniqueness on repeated exchanges:
- `EX-{OR No.}-2`
- `EX-{OR No.}-3`

Exchange / swap behavior
- Swaps are implemented as a linked `refund + replacement sale` model.
- Returned item value uses the original sold line price.
- Replacement item uses the current catalog selling price at the time of swap.
- Difference handling rules:
- `EVEN`
- `CUSTOMER_TOPUP`
- `SHOP_CASH_OUT`
- Replacement sale is recorded as a real paid cash sale so inventory, sales totals, and cash adjustments remain auditable.
- Exchange replacement sales remain refundable, because a replacement item may also need to be corrected later.

Search / page behavior
- Refund page does not auto-load recent sales.
- Staff must deliberately search by receipt number or customer name.
- Search filters include date presets and `Has Refundable Items`.
- Search results and detail panel reset cleanly when clearing the search.
- Exchange replacement sales are clearly labeled in search results and detail view so staff can distinguish them from original sales.

UI behavior
- Added new page: `templates/transactions/refund.html`.
- Refund page shows:
- searchable sale list
- sale summary
- refundable item table
- services context block
- refund form
- refund history
- swap mode with replacement item search, quantity, and difference summary
- Browser alerts / confirms were replaced with in-app toast notifications and modal confirmation.
- The page was visually aligned with the rest of the app and no longer auto-generates heavy result sets on load.

Users / admin history
- `users.html` sales and audit modals now show refund-aware sale details and refund history.
- Modal top metadata layout was tightened so payment chip sits beside the date.
- Refund detail text in the item column now wraps correctly instead of truncating.

Inventory / cash integration
- Refunds create stock-in inventory transactions so returned items appear correctly in audit and stock history.
- Cash ledger records cash-out for refunds.
- Exchange-linked refund and replacement-sale entries are labeled distinctly in the cash ledger layer.

Reporting
- Sales reports now show refund events and exchange replacement sales with explicit report labels:
- `Refund`
- `Exchange Refund`
- `Exchange Replacement`
- This applies to both current-day and date-range sales reports.
- Refund item rendering in the report template was fixed to avoid Jinja's `dict.items` collision by using `refund["items"]`.

Operational notes
- A known watchlist item remains for item-based loyalty programs:
- refunded item sales do not yet roll back any earned loyalty stamps / points.
- This was intentionally deferred because item-based loyalty is rare for the client and needs a separate ledger-safe design.

Toast / flash notification system
- The shared flash / toast system in `base.html` was reworked to reduce missed validation errors during live shop use.
- Error and blocking warning toasts now stay visible until the user manually closes them.
- Success and lightweight info toasts now auto-dismiss again after a short delay so routine workflows like sale submission do not get slowed down.
- Flash messages now use one shared client-side helper instead of several duplicated per-page timer implementations.
- Dynamic page alerts now replace earlier dynamic alerts from the same flow instead of endlessly stacking in the corner.
- Flash messages now support optional target focus / highlight behavior so a toast can point the user back to the field or section that needs correction.

SweetAlert2 usage
- SweetAlert2 was added selectively for confirm / destructive actions, not for routine success toasts.
- A shared confirm wrapper was introduced so the app can use SweetAlert2 when available and still fall back to native `confirm()` safely.
- Current SweetAlert2-backed flows include logout confirmation and cash ledger entry deletion.
- Existing richer Bootstrap-based confirmation modals, such as PO cancel / revision flows and refund confirmation, were left in place instead of being replaced.

## Payables Page Overhaul

Implemented 2026-03-20

Scope
- Reworked the payables page to reduce heavy initial rendering and make cheque-driven payable work easier to scan during daily operations.
- Separated active payables from payables history using top-page tabs inspired by the order overview workflow.

Active payables behavior
- Active payables now default to PO-based and manual-based sections only.
- Fully issued payables with all cheques cleared are no longer shown in the active sections.
- Active payables are now filtered by cheque timing:
- payables with no cheques still remain visible
- payables with at least one non-cancelled cheque dated in the current month remain visible
- payables whose cheque dates are only in future months are hidden from the default active view
- Hidden future-dated payables remain searchable by cheque number, PO number, vendor, or payee.

Ordering / visibility
- Active payable cards are now ordered by the cheque date closest to the current day.
- Example behavior:
- a payable with a cheque dated March 23 appears before one dated March 24 when today is March 20.
- This ordering applies to both PO-based and manual-based active payables.

Cheque history performance
- Cheque history is collapsed by default for each payable.
- The page no longer pre-renders all cheque rows on first load.
- Cheque history is fetched only when the user opens a specific payable's history.
- Within that history:
- issued cheques due this month are shown first
- cleared / older cheque history stays hidden behind a secondary reveal button

Payables history tab
- Added a dedicated `Payables History` tab.
- Payables history is no longer rendered with the main page load.
- Month summaries for cleared payables are fetched only when the history tab is selected.
- Actual payable cards for a month are fetched only when that month is expanded.
- History remains grouped by month, with the current month opened first.

Search behavior
- Search now spans:
- cheque number
- PO number
- vendor name
- payee name
- Search applies across both active payables and payables history.
- If a search has no active matches but does have cleared-history matches, the page switches to the history tab automatically.

Modal / UI optimization
- Replaced one issue-cheque modal per payable with one shared reusable issue-cheque modal.
- The shared modal is populated dynamically with payable id, payee/vendor name, remaining balance, and submit action.
- This reduces DOM weight significantly when many active payables are visible on the page.

Cheque issuing / status updates
- Due date and cheque date remain unified for cheque issuance.
- Cheque status updates still require an actual dropdown change before the update button becomes clickable.
- SweetAlert2 confirmation remains in place for cheque status changes.

Reporting alignment
- Payables PDF already remained sorted by cheque date closest to today, so no new PDF sorting change was required in this pass.

Implementation notes
- Main backend work lives in `services/payables_service.py`.
- Page/API route updates live in `routes/payables_route.py`.
- Main UI and lazy-load behavior lives in `templates/transactions/payables.html`.

## Cash Ledger Soft Delete / Recycle Bin

Implemented 2026-03-21

Scope
- Replaced hard delete behavior for manual cash ledger entries with a soft delete flow.
- Added a recycle-bin style `Deleted` view so removed cash ledger entries remain recoverable for 30 days.

Current behavior
- Deleting a manual cash ledger entry no longer removes the row immediately from `cash_entries`.
- Delete now marks the row as deleted and records:
- `is_deleted`
- `deleted_at`
- `deleted_by`
- Soft-deleted entries are excluded from:
- the active cash ledger table
- active cash ledger pagination counts
- cash on hand / summary calculations
- Deleted entries are still queryable in a dedicated `Deleted` tab on the cash ledger page.
- Deleted entries can be restored back into the active ledger by admin users.
- Restored entries resume affecting cash-on-hand because they become active again.

Deleted tab behavior
- The `Deleted` tab is visible to admins only.
- The deleted view shows:
- original entry details
- who recorded the entry
- who deleted the entry
- when the entry was deleted
- Deleted entries remain filterable by cash in / cash out and date range.
- In deleted mode, date filters apply against `deleted_at` rather than original `created_at`.

Purge behavior
- Soft-deleted cash ledger rows are permanently removed after 30 days.
- Purge is currently triggered opportunistically whenever the cash ledger page or its related APIs are hit.
- This keeps the recycle-bin window correct without adding a separate scheduler yet.

Mechanic payout handling
- Deleted `Mechanic Payout` cash entries no longer count as already-paid payouts.
- This prevents a deleted payout row from blocking the mechanic payout reminder / pending payout logic.

Implementation notes
- Schema changes live in `db/schema.py`.
- Soft-delete / restore / purge logic lives in `services/cash_service.py`.
- Cash ledger route and API updates live in `routes/cash_route.py`.
- Deleted tab UI and restore action live in `templates/cash/cash_ledger.html`.

Assumptions made
- The `Deleted` tab is admin-only.
- The 30-day permanent purge runs on cash-ledger activity rather than through a dedicated scheduler / cron job.

## Box-Based PO / Receive / Review / Export Pass

Implemented 2026-03-21

Scope
- Added support for PO lines that can be ordered either `piece-based` or `box-based`.
- Updated the order page, receive page, PO overview/review surfaces, printable PO PDF, and PO CSV export so staff can clearly see how an item was ordered.

PO creation behavior
- `templates/transactions/order.html` now lets staff choose `Per Piece` or `Per Box` for each PO line.
- `Unit Est. Cost` now keeps its unit meaning:
- for piece-based lines, it is the estimated cost per piece
- for box-based lines, it is the estimated cost per box
- Line totals are computed separately in real time instead of overwriting the unit cost input.
- The order table now shows a prominent `Overall Total` row below the selected item lines.
- Pending incoming reminders on the order page now distinguish:
- exact pending pieces for piece-based open POs
- pending box count for box-based open POs, with a note that actual pieces are counted during receiving

Data model
- `po_items` now stores `purchase_mode` with values:
- `PIECE`
- `BOX`
- `po_receipt_items` now stores:
- `purchase_mode`
- `stock_quantity_received`
- `effective_piece_cost`
- This allows one PO line to be financially tracked by box while inventory still lands as counted pieces.

Receive behavior
- Piece-based receiving remains the same:
- received quantity is the stock quantity inserted
- unit cost is compared directly against `items.cost_per_piece`
- Box-based receiving now requires the actual counted pieces received today.
- For box-based receipts:
- `quantity_received` = number of boxes received for PO progress
- `stock_quantity_received` = actual counted pieces inserted into inventory
- payable value still comes from `box count x box cost`
- `effective_piece_cost` is derived from `total box value / total counted pieces`
- `items.cost_per_piece` is updated using that derived piece cost when the PO receipt is accepted

Important system behavior notes
- Box-based open POs do **not** contribute fake piece quantities to pending incoming stock, because the actual piece count is unknown until receiving.
- Instead, the order page warns with pending box counts such as `3 box(es) pending`.
- The PO overview modal, full review page, revision modals, and printable outputs now surface whether a line was ordered as `piece-based` or `box-based`.
- Ordered / received / remaining quantity displays across PO review surfaces now show units:
- piece-based lines show `pcs`
- box-based lines show `box(es)`
- Printable PO PDF and PO CSV export now carry those same unit-aware quantity labels.
- When a box-based item is over-received and the boxes are uneven, the system uses the actual total counted pieces for stock / cost correction, but the split between `ordered arrival` and `bonus stock` pieces is proportionally estimated because staff currently enters one combined counted-piece total for that receipt.
- This means:
- inventory and cost-per-piece stay grounded in the real counted quantity
- the audit split between ordered vs bonus stock is still best-effort for uneven mixed-box over-receives

Review / revision UX
- Purchase mode reminders were made more prominent in PO admin views so admins can quickly see whether a quantity is in `pcs` or `box(es)`.
- The revision-request modal on the review page was aligned to the same design and behavior as the order overview page revision modal.

Implementation notes
- Schema updates live in `db/schema.py`.
- PO create / edit / receive / export behavior lives in `services/transactions_service.py`.
- Pending-stock search/recommendation behavior lives in `services/inventory_service.py`.
- PO PDF route shaping lives in `routes/reports_route.py`.
- PO CSV export lives in `routes/transaction_route.py`.
- Main order page UI lives in `templates/transactions/order.html`.
- Receive page UI lives in `templates/transactions/receive.html`.
- PO overview modal UI lives in `templates/transactions/order_overview.html`.
- Admin full review UI lives in `templates/order/review.html`.
- Printable PO PDF lives in `templates/reports/purchase_order_pdf.html`.

## Stock Variance

Implemented 2026-03-22

Scope
- Added a dedicated `Stock Variance` / stocktake feature for manual inventory counting and variance correction.
- Feature is designed around the existing inventory ledger model instead of adding a directly editable stock field.
- Current scope supports draft sessions, partial item counting, variance review, confirmation, CSV export, and printable report preview.

Core business model
- A stocktake session represents one counting event.
- Staff manually add only the items they are physically counting into that session.
- Each counted item stores:
- system stock at the time it was added to the session
- counted stock entered by staff
- variance between system and physical count
- notes and adjustment metadata
- Draft saves do **not** change actual inventory.
- Actual stock changes only when the session is confirmed.

Why the feature was implemented this way
- Existing inventory behavior is ledger-based, where stock is derived from transactions instead of edited directly.
- To preserve auditability, variance correction is applied by posting one adjustment transaction per item on confirmation.
- This avoids editing history and avoids creating many fake transactions just to force the stock number to match the physical count.

Schema
- Added `stocktake_sessions` table.
- Added `stocktake_items` table.

`stocktake_sessions` responsibilities
- stores one row per counting session
- stores:
- `session_number`
- `status`
- `count_scope`
- `notes`
- `created_by`
- `created_at`
- `confirmed_by`
- `confirmed_at`
- `cancelled_by`
- `cancelled_at`
- `item_count`
- `variance_item_count`

`stocktake_items` responsibilities
- stores one row per counted item within a session
- stores:
- `session_id`
- `item_id`
- `system_stock`
- `counted_stock`
- `variance`
- `adjustment_type`
- `adjustment_quantity`
- `is_applied`
- item-level notes / transaction link metadata

Session numbering / statuses
- Session numbers are auto-generated in the format:
- `ST-MMDD-###`
- Example:
- `ST-0322-481`
- Supported statuses:
- `DRAFT`
- `CONFIRMED`
- `CANCELLED`

Current workflow
- User opens the Stock Variance page and creates a new session.
- Session starts in `DRAFT`.
- Default counting behavior is `PARTIAL`, but the UI also allows:
- `FULL`
- `CATEGORY`
- `VENDOR`
- Notes can be used to specify the exact category or vendor when needed.
- The session starts empty.
- Staff search for an item, add it to the session, then enter the physical count.
- Variance is shown live while typing.
- User can save the whole session as draft with one `Save Draft` button.
- Saving draft only updates `stocktake_items`; it does not touch real inventory.
- On confirmation, the system applies variance adjustments and locks the session.

Partial session rule
- The feature currently behaves as a partial stocktake workflow.
- Only items added to the current session are affected by confirmation.
- Confirming a session does **not** imply that the entire inventory was counted.
- This is intentionally communicated in the UI with the warning:
- `This is a partial stocktake. Only items added to this session will be adjusted when confirmed.`

Stock computation used by stocktake
- For this feature, stock is based on:
- `SUM(IN) - SUM(OUT)`
- `ORDER` transactions are intentionally ignored for stocktake counting / variance decisions.
- `system_stock` is frozen into the draft row when the item is added, so the session preserves what the system believed at count time.

Variance logic
- Variance is computed as:
- `counted_stock - system_stock`
- Positive variance means physical stock is greater than system stock.
- Negative variance means physical stock is lower than system stock.
- UI behavior:
- positive variance is shown in green
- negative variance is shown in red
- zero variance is muted

Confirmation / inventory adjustment behavior
- Confirmation applies one adjustment transaction per item with non-zero variance.
- If variance is positive:
- one `IN` transaction is posted
- If variance is negative:
- one `OUT` transaction is posted
- If variance is zero:
- no inventory adjustment is posted

Audit trail behavior
- Stocktake adjustments are written into `inventory_transactions`.
- Current audit metadata:
- `reference_type = 'STOCKTAKE'`
- `reference_id = session_id`
- `change_reason = 'STOCKTAKE_VARIANCE_GAIN'` for positive variance
- `change_reason = 'STOCKTAKE_VARIANCE_LOSS'` for negative variance
- This keeps the stock correction aligned with the rest of the transaction ledger and makes the cause of the adjustment visible in audit/history views.

Locking / correction rules
- Draft sessions are editable.
- Confirmed sessions are locked and no longer editable.
- Cancelled sessions are also locked and never apply adjustments.
- If a user confirms a miscount by mistake, the current design does **not** rewrite that confirmed session.
- Correction should be done by creating a new stocktake session and applying a new variance from that later count.
- This preserves audit history instead of silently rewriting it.

Safety guards
- Confirmation includes a guard to prevent double application of the same session.
- Session must still be `DRAFT` to confirm.
- Confirmation logic runs through a controlled backend flow so one session cannot be applied twice accidentally.
- Draft mode also includes a stock-drift safety check:
- `system_stock` is frozen per draft line
- confirmation compares against current live conditions to help detect drift before applying adjustments

Routes / implementation structure
- New route file:
- `routes/stocktake_route.py`
- New service file:
- `services/stocktake_service.py`
- New templates folder:
- `templates/stocktake/`
- Stock variance navigation entry was added to `base.html`.

Main pages
- `templates/stocktake/list.html`
- session list
- create new stocktake session
- `templates/stocktake/detail.html`
- add items by search
- enter counted stock
- live variance preview
- save draft
- confirm or cancel session
- `templates/stocktake/report.html`
- printable report / browser print-to-PDF style preview

Exports / permissions
- Printable report preview is available from the session detail page.
- CSV export is also available from the session detail page.
- CSV export is admin-only:
- non-admin users do not see the CSV button
- backend route is also protected for admin only

Detail page UX behavior
- Draft sessions use one `Save Draft` button for the whole session instead of per-row save buttons.
- Variance updates in real time while staff type the counted quantity.
- Unsaved changes are tracked visually through a badge and save-button state.
- `Save Draft` is gray when disabled and uses the standard system blue when enabled.
- `Confirm Session` is green.
- `Cancel Session` is a solid red action.
- Search / add flow is separated from the item table so staff can build the count list first, then encode the physical counts.

Reporting / summaries
- Session detail page shows summary cards for:
- items counted
- variance items
- shortage items
- overage items
- Session list and reports use stored `item_count` and `variance_item_count` for faster display and export readiness.
- Printable report includes session metadata, summary totals, and counted item lines.

Current limitations / intended Phase 2 follow-ups
- CSV import for count sheets is not implemented yet.
- Batch count entry is not implemented yet.
- Correction-session workflow after confirmation is not yet formalized in UI.
- Additional filtering by category / vendor during item selection can still be improved.
- Report / PDF polish can still be expanded further if client reporting needs grow.

2026-03-23

PO over-receive removal
- Removed the PO over-receive feature from the receive workflow.
- PO receiving now hard-blocks any quantity above the remaining ordered balance.
- Receive page no longer shows the over-receive note / bonus-stock UI.

Backend receive rules
- `receive_purchase_order()` now rejects over-receive submissions at service level even if the frontend is bypassed.
- Removed the `BONUS_STOCK` receive branch and the ordered-vs-bonus stock split logic.
- PO receipt processing now only creates normal `PARTIAL_ARRIVAL` or `PO_ARRIVAL` inventory movements.

Audit / receipt history cleanup
- Removed over-receive-specific receipt serialization from PO receipt history shaping.
- Removed `BONUS_STOCK` movement rendering from PO detail / audit movement views.
- New PO receipts no longer store an over-receive flag.

Database cleanup
- Dropped the legacy `po_receipt_items.is_over_receive` column from schema/init path.
- Added startup cleanup for test-era over-receive batches:
- delete legacy `BONUS_STOCK` inventory rows
- delete linked PO-delivery payables and payable audit rows for those batches
- delete affected receipt batches
- recompute `po_items.quantity_received`
- recompute affected PO `status` and `received_at`
