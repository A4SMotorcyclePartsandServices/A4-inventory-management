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