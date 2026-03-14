# Scratchpad

## IN Page Form Idea

Fields:
- item search
- quantity ordered
- payment method
- amount
- delivery date

Payment logic:

Cash
- record current system time

Cheque
- custom input fields
- cheque date
- cheque number
- payee

Track:
- arrival time
- system entry time

Include OR number

---

## OUT Page Changes

Fields:
- receipt number
- customer name
- customer number / loyalty ID

Mechanic logic:
- mechanic foreign key in inventory_transactions
- used for payout calculation

---

## Return Item System

Case 1
Customer return

Case 2
Return to supplier

Requires customer table

Need purchase date verification

---

## Sales Table

Add column:
discount