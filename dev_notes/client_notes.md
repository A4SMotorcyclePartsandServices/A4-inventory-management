# Client Notes

## Vendor Data

Currently stored in two places:
- item table
- purchase_orders

Need to centralize vendor structure

vendor fields:
- vendor_name
- address
- contact_person
- contact_no
- email

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