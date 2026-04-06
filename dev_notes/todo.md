# TODO

## High Priority

- [X] ~~*Admin page Sales tab: filter for payment status*~~ [2026-03-17]
- [X] ~~*Admin page Sales tab: print formatting (view + print)*~~ [2026-03-27]
- [ ] System tracking for all actions (audit improvements)
- [X] ~~*Database enforcement to prevent duplicate records*~~ [2026-03-30]
- [X] ~~*Payabales Page*~~ [2026-03-21]
- [X] ~~*Centralize vendor data (currently in item table and purchase_orders)*~~ [2026-03-15]

## Inventory / Transaction Improvements

- [X] ~~*Transaction order page: make Order header required after vendor system finalized*~~ [2026-03-15]
- [X] ~~*Stock IN page: add date dropdown for filtering completed orders*~~ [2026-03-17]
- [X] ~~*Stock IN page: show only latest orders within one month*~~ [2026-03-17]
- [X] ~~*Add status **Manual Orders** (gray color)*~~ [2026-03-15]

## Debt System

- [X] ~~*Add payment history tracking*~~ [2026-03-17]
- [X] ~~*If payment made same day, show in Sales Details table*~~ [2026-03-15]
- [ ] For ranged reports, exclude detailed entries but include totals
- [X] ~~*Consolidate debt form to one statement per customer*~~ [2026-03-15]

## Reports / Export

- [X] ~~*CSV export for Items*~~ [2026-03-17]
- [X] ~~*CSV export for Customers*~~ [2026-03-17]
- [X] ~~*PDF export with tier filtering*~~ [2026-03-17]
  - 0–50
  - 51–99
  - 100+

## Dashboard
- [X] ~~*Dashboard page*~~ [2026-03-30]

## Security / Deployment

- [X] ~~*Protect against SQL injection*~~ [2026-03-30]
- [ ] Prepare env vars before deployment

## Variance Page
- [X] ~~*CSV import for count sheets*~~ [2026-03-25]
- [X] ~~*Batch count entry for faster physical inventory encoding*~~ [2026-03-25]
- [X] ~~*Warn user if live stock changed after draft session started*~~ [2026-03-25]
- [X] ~~*Correction session flow for fixing mistakes after confirmation*~~ [2026-03-25]
- [X] ~~*Printable stocktake reports / PDF polish*~~ [2026-03-25]
- [X] ~~*Filters by category and vendor for stocktake item selection*~~ [2026-03-25]


## Live Test Changes
- [X] ~~*change out page from percentage based to peso based*~~ [2026-04-06]
- [X] ~~*Quick sale move up to 500 copy the new sale format, add in a mechanic and payment method. basically a redundant version of new sale but with a hard cap of 500 pesos*~~ [2026-04-06]
- [X] ~~*bug at user panel. blocked creation of mechanic when user tried to create it*~~ [2026-04-06]
- [X] ~~*bug in refund page. for some reason it works on my laptop but does not work on client pc. i narrowed the problem down to account blocking. something must be blocking users from accessing the details of a sale after searching*~~ [2026-04-06]
- [X] ~~*change mechanic payout calculation. the threshold is not 500, it is 625 for both 50 and 80 percent cut mechanic*~~ [2026-04-06]
