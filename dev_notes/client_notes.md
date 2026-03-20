# Client Notes

## Vendor Data

Centralized vendor structure implemented on 2026-03-15

Current source of truth:
- vendors table

Linked usage:
- items.vendor_id for default / usual vendor
- purchase_orders.vendor_id for actual PO vendor

PO snapshot fields for analytics stability:
- vendor_name
- vendor_address
- vendor_contact_person
- vendor_contact_no
- vendor_email

vendor fields:
- vendor_name
- address
- contact_person
- contact_no
- email

UI workflow implemented:
- changed item creation and purchase order creation to use dropdown format
- missing vendor can be created inline from the modal

## Sales Rules

Cash on hand only tracks sales marked as **Cash Payment**

## Branch System

branch_id is already present in the system

Will be used when additional branches open

## Security Deployment Variables

FLASK_SECRET_KEY
SESSION_COOKIE_SECURE=1
SESSION_LIFETIME_HOURS
MAX_CONTENT_LENGTH_MB

## Login Throttling

Currently in-memory

Limitations:
- resets on restart
- not shared across multiple workers

Future upgrade: move to Redis or DB


## March 15, 2026 client notes

## PO Page
- PO Page change Approval History to PO History both sides
- Base.html change Stock in to Stock in (PO)
- PO Page change ready to receive to Approved PO's
- PO Page modal add price and total
- Vendor info in print PDF in PO page modal please insert data
- PO Page change Partial to Partial Delivery
- PO Page partial modal print pdf add comparison between ordered quantity and qty delivered with inline notes telling how many will still arrive. also include price in calculation both inline and total price column rename to total price (delivered)
- Delivery date discrepancy appearing here again. we need a more robust way of tracking this
- PO Page Completed to Completed Delivery
- Cancel to Cancelled PO's
- PO page change it to dropdown

## OUT Page
- when a redemption is redeemed block price input
- customer modal do not require vehicle make it only required
- NEW FEATURE. QUICK SALE basically free form customer name, only record the item sale. restrictions less than 100 pesos only are the items that can be sold. Quick sale default view

## Loyalty Program
- Analytics on which customer availed the most within the loyalty program's
- Make Loyalty Program Extensible
- bug in loyalty program status not updating

## Debt Feature
- Debt feature pdf printing of all current debt
- format is customer name, OR no, total amount, paid amount, balance 

## Cash Ledger
- Categories for cash in :Petty Cash, From Gcash Account, From Bank Account, For Payables, Others (REQUIRE DESCRIPTION)
- Categories for cash out: Other Expenses
- Cash ledger page mechanic payout automation. for mechanics with quota top up applied, make the description use mechanic name plus quota top up

## Refund Feature
- 7 days no return
- we have to reverse the sale
- touch the cash ledger page, make an automated cash out
- touch the inventory return the item
- admin panel sales and audit tab touch for data update

## Payables Feature
- Supplier and Rental Payment
- PO page on Delivered items only
- Include partial and completed deliveries. extract PO number, Supplier name, date of PO when it was created, total amount for completed total amount for delivered for partial deliveries. get this data automatically
- Then manual entry for issuing cheque. possible input boxes Fill in date of cheque, cheque number, cheque amount
- Issuing a cheque for PO order and manual entries for other cheque related needs
- Issuing a cheque for manual entries for things like rental same input as above but extra payee input and description input
- PO based payment
- basically just cheque monitoring for cheque made and alerting user of due cheques
- PDF report for this. cheques issued this month and stuff as report
- user has access to this page but the pdf report is only for admin

## Client meeting March 19, 2026

## PO Page
- [X] ~~*Change modal table headers. PO Total to Total PO, Delivered Value to Delivered Amount*~~ [2026-03-19]
- [X] ~~*Show history notes. instead of plainly displaying the notes, add Reason: {notes}. use red font*~~ [2026-03-19]
- [X] ~~*Completed delivery chip displays weirdly in laptop*~~ [2026-03-19]
- new scenario. per box or per plastic items. as per discussion, this will diverge wherein we will have to record the item's price per box. basically say oil costs 300 a piece, per box contains 12 pieces. we need to make sure the system records that per box is 12 pieces so that means we have 36 oil incoming and their price is still 300 unless edited in est cost in the order page. then the box costs say 10,000. that one will diverge towards the payables page where the po cheque system lives. items quantity that will arrive will come from number of boxes times items per box. 
- box scenario we have to think about it but i have an idea. start from PO give option to choose from by box or by quantity. make per box require est cost. not auto calculated input needed from user. save it. when in receive we need new inputs to accomodate items received by box. basically at this point the shop has the box and can count before entering it into the system. let us leverage that. after receiving, use the user's inputted box amount as the payable for cheque issuance in payables page then we have a auto calculation wherein the price of the box and the items inside will determine the cost per piece. formula is amount of one box divided by the qty of items inside that box

## Order Page
- change vendor on items database saving. instead of overwriting, make it add the vendor. then in order page, when a certain vendor is selected, pull out items the shop usually orders then recommend 5 of them. chips form. for easy selection

## Cash Ledger page
- Change hard delete to soft delete. make it recycle bin style, make the deleted data able to come back, we have to record who deleted data, auto clear recycle bin every month

## Refund Feature
- [X] ~~*Payment change to payment method*~~ [2026-03-19]
- [X] ~~*Change sale total to total sale*~~ [2026-03-19]
- [X] ~~*Change sold to quantity*~~ [2026-03-19]
- [X] ~~*Refunded so to amount refunded*~~ [2026-03-19]
- [X] ~~*Refunded to refunded qty*~~ [2026-03-19]
- [X] ~~*Remaining to remaining qty*~~ [2026-03-19]
- [X] ~~*expand search to accomodate by item search and by date*~~ [2026-03-20]
- [X] ~~*change Sale Refund auto generating format from RF-021384-0319 - 021384 - Patrick Jacob Latade to RF-OR NO.-Date - customer name*~~ [2026-03-20]
- [X] ~~*change exchange refund from RF-89032-0319 - 89032 - Patrick Jacob Latade (EX-89032) to RF-89032-0319 - Patrick Jacob Latade (EX-89032)*~~ [2026-03-20]
- [X] ~~*change exchange replacement from EX-89032 — Patrick Jacob Latade (EX-89032) to SW-89032-current month and day — Patrick Jacob Latade (SW-89032)*~~ [2026-03-20]
- [X] ~~*change the RF auto generation to remove the OR No. in the middle RF-89032-0319 - 89032 - Patrick Jacob Latade (EX-89032)*~~ [2026-03-20]
- [X] ~~*from exchange refund to exchange/refund*~~ [2026-03-20]
- [X] ~~*from exchange replacement to exchange/replacement*~~ [2026-03-20]

## Payables Feature
- [X] ~~*change button name from new manual payable to create payable*~~ [2026-03-19]
- [X] ~~*due date is same as cheque date. change that*~~ [2026-03-20]
- [X] ~~*PDF add cleared cheques. add all cheques of all statuses and arrange data from closest to cheque data to farthest*~~ [2026-03-20]
- [X] ~~*Generate PDF should be accessible by staff*~~ [2026-03-19]
- [X] ~~*Collapse the cheque history. only show due cheques this month. hide other cheques not until it is lazy loaded?*~~ [2026-03-20]

## Base page
- [X] ~~*change the notifications system. client requested for it to not be timed and for it to be closed when user presses on it. when user presses it, redirect user to where the error led them. for example they were creating a sale, when they submit and an error pops up, higlight that error so they know where they went wrong*~~ [2026-03-20]