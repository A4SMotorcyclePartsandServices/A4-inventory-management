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
- [X] ~~*PO Page change Approval History to PO History both sides*~~ [2026-03-20]
- [X] ~~*Base.html change Stock in to Stock in (PO)*~~ [2026-03-20]
- [X] ~~*PO Page change ready to receive to Approved PO's*~~ [2026-03-20]
- [X] ~~*PO Page modal add price and total*~~ [2026-03-20]
- [X] ~~*Vendor info in print PDF in PO page modal please insert data*~~ [2026-03-20]
- [X] ~~*PO Page change Partial to Partial Delivery*~~ [2026-03-20]
- [X] ~~*PO Page partial modal print pdf add comparison between ordered quantity and qty delivered with inline notes telling how many will still arrive. also include price in calculation both inline and total price column rename to total price (delivered)*~~ [2026-03-20]
- [X] ~~*Delivery date discrepancy appearing here again. we need a more robust way of tracking this*~~ [2026-03-20]
- [X] ~~*PO Page Completed to Completed Delivery*~~ [2026-03-20]
- [X] ~~*Cancel to Cancelled PO's*~~ [2026-03-20]
- [X] ~~*PO page change it to dropdown*~~ [2026-03-20]

## OUT Page
- [X] ~~*when a redemption is redeemed block price input*~~ [2026-03-20]
- [X] ~~*customer modal do not require vehicle make it only required*~~ [2026-03-20]
- [X] ~~*NEW FEATURE. QUICK SALE basically free form customer name, only record the item sale. restrictions less than 100 pesos only are the items that can be sold. Quick sale default view*~~ [2026-03-20]

## Loyalty Program
- Analytics on which customer availed the most within the loyalty program's
- [X] ~~*Make Loyalty Program Extensible*~~ [2026-03-20]
- [X] ~~*bug in loyalty program status not updating*~~ [2026-03-20]

## Debt Feature
- [X] ~~*Debt feature pdf printing of all current debt*~~ [2026-03-20]
- [X] ~~*format is customer name, OR no, total amount, paid amount, balance*~~ [2026-03-20]

## Cash Ledger
- [X] ~~*Categories for cash in :Petty Cash, From Gcash Account, From Bank Account, For Payables, Others (REQUIRE DESCRIPTION)*~~ [2026-03-20]
- [X] ~~*Categories for cash out: Other Expenses*~~ [2026-03-20]
- [X] ~~*Cash ledger page mechanic payout automation. for mechanics with quota top up applied, make the description use mechanic name plus quota top up*~~ [2026-03-20]

## Refund Feature
- [X] ~~*7 days no return*~~ [2026-03-20]
- [X] ~~*we have to reverse the sale*~~ [2026-03-20]
- [X] ~~*touch the cash ledger page, make an automated cash out*~~ [2026-03-20]
- [X] ~~*touch the inventory return the item*~~ [2026-03-20]
- [X] ~~*admin panel sales and audit tab touch for data update*~~ [2026-03-20]

## Payables Feature
- [X] ~~*Supplier and Rental Payment*~~ [2026-03-20]
- [X] ~~*PO page on Delivered items only*~~ [2026-03-20]
- [X] ~~*Include partial and completed deliveries. extract PO number, Supplier name, date of PO when it was created, total amount for completed total amount for delivered for partial deliveries. get this data automatically*~~ [2026-03-20]
- [X] ~~*Then manual entry for issuing cheque. possible input boxes Fill in date of cheque, cheque number, cheque amount*~~ [2026-03-20]
- [X] ~~*Issuing a cheque for PO order and manual entries for other cheque related needs*~~ [2026-03-20]
- [X] ~~*Issuing a cheque for manual entries for things like rental same input as above but extra payee input and description input*~~ [2026-03-20]
- [X] ~~*PO based payment*~~ [2026-03-20]
- [X] ~~*basically just cheque monitoring for cheque made and alerting user of due cheques*~~ [2026-03-20]
- [X] ~~*PDF report for this. cheques issued this month and stuff as report*~~ [2026-03-20]
- [X] ~~*user has access to this page but the pdf report is only for admin*~~ [2026-03-20]

## Client meeting March 19, 2026

## PO Page
- [X] ~~*Change modal table headers. PO Total to Total PO, Delivered Value to Delivered Amount*~~ [2026-03-19]
- [X] ~~*Show history notes. instead of plainly displaying the notes, add Reason: {notes}. use red font*~~ [2026-03-19]
- [X] ~~*Completed delivery chip displays weirdly in laptop*~~ [2026-03-19]
- [X] ~~*new scenario. per box or per plastic items. as per discussion, this will diverge wherein we will have to record the item's price per box. basically say oil costs 300 a piece, per box contains 12 pieces. we need to make sure the system records that per box is 12 pieces so that means we have 36 oil incoming and their price is still 300 unless edited in est cost in the order page. then the box costs say 10,000. that one will diverge towards the payables page where the po cheque system lives. items quantity that will arrive will come from number of boxes times items per box.*~~ [2026-03-21] 
- [X] ~~*box scenario we have to think about it but i have an idea. start from PO creation via order.html page. give option to choose from by box or by quantity after selecting an item. make per box require est cost input. not auto calculated input needed from user. save it. when in receive we need new inputs to accomodate items received by box. basically at this point the shop has the box and can count before entering it into the system. let us leverage that. after receiving, use the user's inputted box amount as the payable for cheque issuance in payables page then we have a auto calculation wherein the price of the box and the items inside will determine the cost per piece. formula is amount of one box divided by the qty of items inside that box*~~ [2026-03-21]

## Order Page
- [X] ~~*change vendor on items database saving. instead of overwriting, make it add the vendor. then in order page, when a certain vendor is selected, pull out items the shop usually orders then recommend 5 of them. chips form. for easy selection*~~ [2026-03-20]

## Cash Ledger page
- [X] ~~*Change hard delete to soft delete. make it recycle bin style, make the deleted data able to come back, we have to record who deleted data, auto clear recycle bin every month*~~ [2026-03-21]
- for meeting tomorrow, ask client if they want staff to access deleted history. we can even make it similar to PO wherein restoring a deleted entry needs admin approval

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


## Cash Ledger
- [X] ~~*PDF Reporting per month*~~ [2026-03-23]

## PO Page
- [X] ~~*Remove over receive feature*~~ [2026-03-23]

## 
- [X] ~~*Sales print copy existing design. receipt style*~~ [2026-03-23]

## forgot password
- [X] ~~*admin reset user password*~~ [2026-03-25]
- [ ] electricity issues please resolve
- [ ] Sales analytics page check rendering
- [ ] A4S-lipa.com

## Re upload data to current, March 23
- [ ] to determine IN, begbal + Received = IN
- [ ] to determine OUT, begbal + received - sold = OUT  

## Variance Page
- [X] ~~*Remove adjustment column*~~ [2026-03-25]
- [X] ~~*Action after confirming session, instead of applied make it updated*~~ [2026-03-25]
- [X] ~~*edit pdf, for system stock (SS), Counted Stock (CS), Variance (V), extract cost per piece for that item. basically a way for the user to see how much money they are sitting with that stock*~~ [2026-03-25]  

## MARCH 27 2026
## Sales Report Page and Cash Ledger Page
- [ ] We need a way to track other payment methdods during transactions as incoming so that we can track how much is still missing from the cash ledger. show this as well in the sales report. this is applied, check if it applies to utang/debt payments made
- [X] ~~*Sales report pdf, exclude profit card when user generates PDF*~~ [2026-03-27]
- [ ] check current service setup, make that the default starting point. found in services tab in users.html
- [ ] services setup, go to excel file and search for category svc, those are shop services, include that in the starter pack
- [X] ~~*out page during item search, include item description and not only item name. same with receipt in sales modal in users.html. add a subfield for item description*~~ [2026-03-27]
- [X] ~~*some mechanics don't need the top up logic applied, excluded them from top up calculation*~~ [2026-03-27]

## Reorder level
- [ ] change reorder level checking to an algorithm that suggests when an item should be restocked. exclude svc in category

## OUT PAGE
- [X] ~~*Services section, add one more feature to the search, set a keyword that when inputted, shows all the services present in the services table*~~ [2026-03-27]

## Urgent
- [ ] handover of system. we need a way to separate sales from inventory basically a way to reset the sales data and keep the current inventory at the time of handoff
- [ ] customer database needs to keep current points and stamps from loyalty even after the sales data wipeout