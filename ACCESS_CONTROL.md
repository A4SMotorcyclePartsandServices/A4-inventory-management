## Access Control Audit

Current model as of 2026-04-08.

Status:

- Re-checked against the current route set in `app.py`, `auth/utils.py`, and all files under `routes/`.
- The previous 2026-03-13 snapshot was outdated.
- The app now effectively has 5 access levels:
  - `public`
  - authenticated (`staff` and `admin`)
  - stocktake-approved (`admin` or staff with active stocktake access)
  - `admin` only
  - owner-only (`admin` user IDs listed in `OWNER_USER_IDS`)

### Public Routes

- `auth.login` -> `/login`
- `password_reset.forgot_password` -> `/forgot-password`
- `static`

### Authenticated Routes (`staff` and `admin`)

Core pages and inventory:

- `/`
- `/api/search`
- `/analytics`
- `/dead-stock`
- `/low-stock`
- `/export/transactions`
- `/transaction/out`
- `/transaction/in`
- `/transaction/items`
- `/transaction/items/edit/<item_id>`
- `/items/add`
- `/items/<item_id>/edit`
- `/inventory/in`
- `/transaction/out/save`
- `/transaction/refund`
- `/api/sales/<sale_id>/refund-context`
- `/api/sales/<sale_id>/refund`
- `/api/sales/refund-search`
- `/api/search/items`
- `/api/search/services`
- `/api/search/vendors`
- `/api/vendors/add`
- `/api/bundles/sale-options`
- `/api/bundles/<bundle_id>/sale-config`
- `/api/vendors/<vendor_id>/recommended-items`

Purchase orders:

- `/transaction/order`
- `/transaction/order/save`
- `/transaction/orders/list`
- `/api/order/<po_id>`
- `/api/orders/search`
- `/api/orders/archive-month`
- `/api/order/<po_id>/update`
- `/api/order/<po_id>/cancel`
- `/export/purchase-order/<po_id>/csv`
- `/transaction/receive/<po_id>`
- `/transaction/receive/confirm`
- `/purchase-order/details/<po_id>`
- `/reports/purchase-order/<po_id>`

Customers and debt:

- `/api/search/customers`
- `/api/customers/add`
- `/api/customers/<customer_id>/vehicles`
- `/api/customers/<customer_id>/vehicles/add`
- `/api/customers/<customer_id>/transactions`
- `/customers`
- `/export/customers`
- `/reports/customers/points`
- `/utang`
- `/api/debt/<sale_id>`
- `/api/debt/<sale_id>/pay`
- `/api/debt/audit`
- `/api/debt/summary`
- `/api/debt/payments/<sale_id>`
- `/api/debt/customer/<customer_id>/payments`
- `/debt/statement/<sale_id>`
- `/debt/statement/customer/<customer_id>`

Reports and exports:

- `/reports/sales-receipt/<sale_id>`
- `/reports/daily`
- `/reports/range`
- `/reports/sales-summary`
- `/reports/sales-report-summary`
- `/reports/mechanic-supply`
- `/export/inventory-snapshot`
- `/export/items`
- `/export/items-sold-today`
- `/export/services-sold-today`
- `/reports/cash-ledger`
- `/reports/payables`

Cash ledger:

- `/cash-ledger`
- `/api/cash/summary`
- `/api/cash/entries`
- `/api/cash/ledger`
- `/api/cash/panel/pending-payouts`
- `/api/cash/panel/overdue-payouts`
- `/api/cash/panel/pending-non-cash`
- `/api/cash/add`

Payables:

- `/transaction/payables`
- `/api/payables/<payable_id>/cheques`
- `/api/payables/history/summary`
- `/api/payables/history/month`
- `/transaction/payables/manual`
- `/transaction/payables/<payable_id>/cheques`
- `/transaction/payables/cheques/<cheque_id>/status`

Notifications and approvals:

- `/api/notifications/summary`
- `/api/notifications`
- `/api/notifications/<notification_id>/read`
- `/api/notifications/read-all`
- `/api/approvals/<approval_request_id>`
- `/api/approvals/<approval_request_id>/cancel`
- `/api/approvals/<approval_request_id>/resubmit`

Loyalty:

- `/api/loyalty/eligibility/<customer_id>`
- `/api/loyalty/programs` `GET`
- `/api/loyalty/programs` `POST`
- `/api/loyalty/programs/<program_id>/toggle`
- `/api/loyalty/programs/<program_id>/extend`
- `/api/loyalty/redeem`
- `/api/loyalty/customer/<customer_id>/summary`

Account/session:

- `/logout`
- `/change-password`
- `/stocktake/access/request`
- `/users`
- `/mechanics/add`
- `/mechanics/toggle/<mechanic_id>`
- `/mechanics/quota-topup`
- `/mechanics/quota-topup/<override_id>/delete`
- `/services/add`
- `/services/toggle/<service_id>`
- `/bundles/add`
- `/bundles/<bundle_id>/edit`
- `/bundles/toggle/<bundle_id>`
- `/api/bundles/<bundle_id>`
- `/payment-methods/add`
- `/payment-methods/toggle/<pm_id>`
- `/vendors/add`

### Stocktake-Approved Routes

These routes require `admin` or an active stocktake access grant via `@stocktake_access_required`.

- `/stocktake`
- `/stocktake/new`
- `/stocktake/<session_id>`
- `/api/stocktake/<session_id>/items`
- `/api/stocktake/<session_id>/items/<item_id>`
- `/api/stocktake/<session_id>/save-draft`
- `/api/stocktake/<session_id>/items/<item_id>/delete`
- `/api/stocktake/<session_id>/confirm`
- `/api/stocktake/<session_id>/cancel`
- `/stocktake/<session_id>/report`
- `/stocktake/overall-report`
- `/stocktake/overall-csv`

### Admin-Only Routes

Analytics and debug:

- `/items-analytics`
- `/sales-analytics`
- `/items-analytics/stock-movement`
- `/items-analytics/item-movement`
- `/items-analytics/top-items`
- `/import/items`
- `/import/sales`
- `/import/inventory`
- `/index2`
- `/debug-integrity`

Admin user management and audit:

- `/users/audit`
- `/users/toggle/<user_id>`
- `/password-resets/<request_id>/complete`
- `/password-resets/<request_id>/reject`
- `/stocktake-access/<approval_request_id>/approve`
- `/stocktake-access/<approval_request_id>/reject`
- `/stocktake-access/<approval_request_id>/revoke`
- `/sales/details/<reference_id>`
- `/audit/manual-in/<audit_group_id>`
- `/api/item/<item_id>`
- `/api/audit/trail`
- `/api/audit/item-edits`
- `/api/admin/sales`
- `/api/payables/audit`
- `/api/vendors/<vendor_id>`
- `/api/vendors/<vendor_id>/update`
- `/vendors/toggle/<vendor_id>`
- `/services/toggle-payout/<service_id>`

Approval admin APIs:

- `/api/admin/approvals`
- `/api/admin/approvals/<approval_request_id>`
- `/api/admin/approvals/<approval_request_id>/approve`
- `/api/admin/approvals/<approval_request_id>/revisions`
- `/api/admin/approvals/<approval_request_id>/cancel`
- `/api/order/<po_id>/approval/approve`
- `/api/order/<po_id>/approval/revisions`
- `/transaction/order/<po_id>/review`

Cash admin APIs:

- `/api/cash/delete/<entry_id>`
- `/api/cash/restore/<entry_id>`

Stocktake export admin route:

- `/stocktake/<session_id>/csv`

### Owner-Only Routes

These routes require an authenticated admin whose user ID is listed in the `OWNER_USER_IDS` environment variable.

- `/owner/admin-password-resets`

### Enforcement Notes

- Global authentication is enforced in [app.py](/C:/Dev/a4_inventory_system/app.py) for every route except:
  - `auth.login`
  - `password_reset.forgot_password`
  - `static`
- `must_change_password` is enforced globally in [app.py](/C:/Dev/a4_inventory_system/app.py). A logged-in user flagged for forced reset is restricted to:
  - `/change-password`
  - `/logout`
  - notification APIs
  - `static`
- `@admin_required`, `@owner_required`, and `@login_required` are defined in [auth/utils.py](/C:/Dev/a4_inventory_system/auth/utils.py).
- Stocktake routes use a separate gate, `@stocktake_access_required`, also in [auth/utils.py](/C:/Dev/a4_inventory_system/auth/utils.py).
- The shared `users.html` page lives under the `users_panel` blueprint in [routes/users_panel_route.py](/C:/Dev/a4_inventory_system/routes/users_panel_route.py) and is available to all logged-in users.
- The shared Services tab under `users.html` now has mixed permissions:
  - all logged-in users can add services and toggle service active/inactive status
  - only admins can see or change service payout mode (`Shop share only` vs normal payout)
  - server-side enforcement for payout mode changes is handled by `@admin_required` on `/services/toggle-payout/<service_id>`
- The separate admin audit surface lives under the `admin_audit` blueprint in [routes/admin_audit_route.py](/C:/Dev/a4_inventory_system/routes/admin_audit_route.py) and remains admin-only.
- Loyalty program management endpoints are currently open to all logged-in users.
- `Flask-WTF` CSRF protection applies globally to unsafe methods.

### Drift From Previous Audit

The older 2026-03-13 doc missed or no longer accurately reflected:

- stocktake access routes and approval flow
- payables page, APIs, report, and payables audit API
- notifications APIs
- password reset request admin actions
- bundle admin routes
- mechanics quota top-up admin routes
- sales analytics route
- customer export and points report routes
- sales receipt, sales report summary, mechanic supply, and items export routes
- vendor APIs
- order search/archive APIs and PO review route
- cash panel APIs and cash restore route
- the fact that the app now has a distinct stocktake-access tier
- the old note claiming the entire `auth` blueprint is admin-only, which is no longer accurate
