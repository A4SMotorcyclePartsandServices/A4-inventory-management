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
- NEW FEATURE. QUICK SALE basically free form customer name, onl record the item sale. restrictions less than 100 pesos only are the items that can be sold. Quick sale default view

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