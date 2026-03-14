# TODO

## High Priority

- [ ] Admin page Sales tab: filter for payment status
- [ ] Admin page Sales tab: print formatting (view + print)
- [ ] System tracking for all actions (audit improvements)
- [ ] Database enforcement to prevent duplicate records
- [ ] Centralize vendor data (currently in item table and purchase_orders)

## Inventory / Transaction Improvements

- [ ] Transaction order page: make Order header required after vendor system finalized
- [ ] Stock IN page: add date dropdown for filtering completed orders
- [ ] Stock IN page: show only latest orders within one month
- [ ] Add status **Manual Orders** (gray color)

## Debt System

- [ ] Add payment history tracking
- [ ] If payment made same day, show in Sales Details table
- [ ] For ranged reports, exclude detailed entries but include totals
- [ ] Consolidate debt form to one statement per customer

## Reports / Export

- [ ] CSV export for Items
- [ ] CSV export for Customers
- [ ] PDF export with tier filtering
  - 0–50
  - 51–99
  - 100+

## Security / Deployment

- [ ] Protect against SQL injection
- [ ] Prepare env vars before deployment
