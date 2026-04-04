## Security Audit

Audit date: 2026-03-31

Scope:

- Route protection and role enforcement
- CSRF and session handling
- SQL injection posture
- Input validation and client-side rendering risks
- Internet deployment blockers visible from the repo

### Findings

1. Low: no obvious high-risk DOM XSS sink remains in the main runtime screens that were previously called out.
   - Status:
     - Strict `escapeHtml()` / DOM-node rendering has now been pushed through the highest-risk data-driven screens:
       - [templates/index.html](/c:/Dev/a4_inventory_system/templates/index.html)
       - [templates/transactions/out.html](/c:/Dev/a4_inventory_system/templates/transactions/out.html)
       - [templates/cash/cash_ledger.html](/c:/Dev/a4_inventory_system/templates/cash/cash_ledger.html)
       - [templates/users/users.html](/c:/Dev/a4_inventory_system/templates/users/users.html)
     - The previous problem pattern was large fetched datasets rendered into `innerHTML` tables, modals, and search results. Those paths were converted to DOM creation or escaped rendering.
   - Remaining representative references:
     - [templates/users/users.html](/c:/Dev/a4_inventory_system/templates/users/users.html)
     - [templates/transactions/out.html](/c:/Dev/a4_inventory_system/templates/transactions/out.html)
     - [templates/index.html](/c:/Dev/a4_inventory_system/templates/index.html)
   - Residual note:
     - Some `innerHTML` usage still exists for static skeleton markup, icon wrappers, or already-escaped helper output. That is materially lower risk than the previous state, but future edits should still prefer `textContent` and DOM node creation for untrusted data.
   - Why it matters:
     - If an attacker can get HTML/JS-like content stored in names, notes, reference numbers, service names, item names, or customer fields, any remaining unsafe `innerHTML` path can execute it in another user’s browser.
     - Jinja autoescaping protects server-rendered HTML templates, but it does not protect JavaScript string templates assigned to `innerHTML`.
   - Recommended fix:
     - Continue replacing `innerHTML` usage with DOM node creation plus `textContent` where data is untrusted.
     - Where HTML templating in JS remains necessary, ensure every interpolated value passes through `escapeHtml()`.

2. Low: staff-access scope is intentionally broad by business decision, with only enforcement consistency left to verify over time.
   - Representative references:
     - [ACCESS_CONTROL.md](/c:/Dev/a4_inventory_system/ACCESS_CONTROL.md)
     - [app.py](/c:/Dev/a4_inventory_system/app.py#L81)
   - Status:
     - The current staff-access surface has now been discussed with the client and is considered intentional for this deployment.
     - The remaining security concern is not policy ambiguity, but making sure future route changes continue to match that agreed access model.
   - Residual note:
     - If the business policy changes later, re-check exports, audit views, debt, cash, and financial report routes first.

3. Resolved: report date inputs are now strongly validated before use.
   - Representative references:
     - [routes/reports_route.py](/c:/Dev/a4_inventory_system/routes/reports_route.py)
   - Status:
     - Date query parameters are now validated as strict `YYYY-MM-DD` before report processing.
   - Residual note:
     - Keep using the shared validation helpers when new report endpoints are added.

4. Medium: login throttling is process-local only.
   - Representative references:
     - [auth/utils.py](/c:/Dev/a4_inventory_system/auth/utils.py#L11)
   - Why it matters:
     - It works on one process, but resets on restart and does not coordinate across multiple instances.
   - Recommended fix:
     - Move throttling to Redis or the database before multi-instance deployment.

5. Low: production serving entrypoints now exist, but deployment is still not complete.
   - Representative references:
     - [wsgi.py](/c:/Dev/a4_inventory_system/wsgi.py)
     - [run_waitress.py](/c:/Dev/a4_inventory_system/run_waitress.py)
     - [app.py](/c:/Dev/a4_inventory_system/app.py#L480)
   - Why it matters:
     - The repo now has a production-style entrypoint, but the default dev entrypoint still exists and deployment still requires reverse proxy, HTTPS, secrets, and operational setup.
   - Recommended fix:
     - Use `run_waitress.py` or `wsgi.py` for hosting and keep `python app.py` for local development only.
     - Complete the deployment items in [DEPLOYMENT_CHECKLIST.md](/c:/Dev/a4_inventory_system/DEPLOYMENT_CHECKLIST.md).

6. Resolved: low-stock polling and item-target routing were reduced from a medium availability risk to low residual risk.
   - Representative references:
     - [app.py](/c:/Dev/a4_inventory_system/app.py#L367)
     - [services/analytics_service.py](/c:/Dev/a4_inventory_system/services/analytics_service.py)
     - [templates/base.html](/c:/Dev/a4_inventory_system/templates/base.html)
   - Status:
     - The low-stock summary path now uses a short server-side TTL cache to reduce repeated full recomputation from authenticated topbar polling.
     - The `/low-stock?item_id=...` path now reuses one computed dataset per request instead of doing two full passes for page targeting plus pagination.
     - The browser-side stock-alert loader now ignores force-refresh requests that arrive again within a short cooldown window, reducing duplicate fetches from page load followed immediately by dropdown open.
   - Residual note:
     - The mitigation is still process-local and compute still scales with item count whenever the cache expires.
   - Recommended fix:
     - If inventory size or user concurrency grows further, move low-stock summary generation toward a scheduled snapshot/materialized table or shared cache.

### What is already in better shape

- Non-public routes require login.
- Admin routes in the auth/admin surface are blocked from staff.
- CSRF protection is enabled through `Flask-WTF`.
- Session secret is environment-backed.
- Login now rotates session state and applies basic throttling.
- SQL queries are mostly parameterized.
- A production-oriented WSGI/Waitress startup path now exists.
- The main data-driven DOM XSS hotspots in inventory, sales, debt, cash, and admin screens were reduced to low residual risk.
- The low-stock tray and highlight flow were hardened against unnecessary repeat recomputation.

### Recommended next security work

1. Move rate limiting to shared storage if deployment will use multiple processes or servers.
2. Add centralized audit logging for failed logins and privileged actions.
3. Keep new client-side rendering work on the DOM/textContent path instead of reintroducing raw `innerHTML` for fetched data.
4. Re-verify route enforcement if the staff/admin access policy changes later.
