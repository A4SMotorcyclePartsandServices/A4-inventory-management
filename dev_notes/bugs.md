# Bugs

## Watchlist

Use this section for things that are not necessarily broken right now, but could become a problem later depending on configuration or workflow.

- Loyalty programs: mixed stamp + points setup is currently not used by the client, so no code change is planned right now.
- If a future loyalty program enables both `stamp_enabled` and `points_enabled`, one qualifying sale can earn both a stamp and points.
- If a future mixed program uses `reward_basis = STAMPS_OR_POINTS`, redemption currently prefers consuming stamps first, which can leave points untouched and allow later eligibility from the points side.
- Refunds + loyalty: if a refunded item participated in an item-based loyalty program, the current refund flow does not yet roll back the earned loyalty stamps/points.
- PO overview: active tabs (`For Approval`, `Approved`, `Partial`) still render full datasets on initial page load, so pagination or tab-level lazy loading may be needed as usage grows.
- PO overview: archive month summaries are lazy loaded, but a very large single month may still need paging inside the month response later.
- PO search: current search caps server-side results at 20 and asks users to keep typing; if PO volume grows further, a normalized `po_number_search` column may be better than repeated SQL normalization.
- PO performance: add lightweight timing logs around `/transaction/orders/list`, `/api/orders/search`, and `/api/orders/archive-month` if users start reporting slowness.
- PO modal: if approval history or delivery history grows large for long-lived purchase orders, modal payload size may need trimming or paged history loading.
- Admin no password reset yet
- Inventory list: row click redirect to item edit is temporarily disabled for non-admin users; restore only if the client wants edit-page access reopened outside admin.

## Possible Issues

- Random admin login redirect to Access Denied on first load, then normal after refresh.

Current theory:
- There were likely 2 overlapping causes.
- Earlier logs pointed to duplicate in-flight `/login` POSTs after session rotation, which could fail with:
  `400 Bad Request: The CSRF session token is missing.`
- That path was mitigated by preserving the session CSRF token during login rotation and replaying duplicate submits through idempotency.
- Newer Railway logs now show a different failure:
  `400 Bad Request: The CSRF token has expired.`
- This points to a stale login page, especially likely on mobile where the browser resumes an older `/login` tab from background or cache.
- In that case the first submit fails before authentication runs, then refresh/retry works because the page gets a fresh CSRF token.
- Temporary tracing was added with `AUTH_TRACE` logs in `app.py`, `auth/utils.py`, and `routes/auth_route.py`.

Related mobile logout report:
- Client reported that on phone, logout sometimes appears to do nothing on the first try.
- She stays on the same page and only reaches logged-out state after trying again.
- This may be the same stale mobile page / resumed-tab family of issue as the login `403` problem, not necessarily a separate auth bug.
- The client is known to keep the web app open on her phone for long periods without clearing it, which increases the chance that the browser restores an older page state.
- Two plausible variants:
  first logout actually succeeds server-side, but the phone keeps showing the previously loaded protected page from cache / back-forward cache until the next real navigation
  first logout POST fails because the old page is carrying stale form / CSRF state, then the second attempt works after the browser refreshes its state
- Why this theory fits:
  `/login` already needed explicit no-store headers because mobile browsers were reusing old login pages with expired CSRF tokens
  authenticated pages may still be more vulnerable to stale restore behavior than the login page
  logout itself is only a simple POST + redirect, so the weak point is more likely page/browser state than the server-side logout logic
- Practical interpretation:
  if the first logout truly cleared the session, then any refresh or new navigation after that point should redirect to `/login`
  if the session was still active after the first logout attempt, then the first POST likely never completed or failed before session clear
- Current combined theory:
  both the intermittent login `403` and the phone logout retry issue may stem from mobile browsers resuming stale pages after the app has been backgrounded for a long time
  the symptom changes depending on which page was resumed and whether the first request hits stale CSRF state or only stale visual state

Tracing added for the mobile logout issue:
- Lightweight logout-specific tracing was added so future incidents can be classified without heavy logging.
- Added server-side `AUTH_TRACE` events:
  `logout_attempt`
  `logout_success`
  `logout_csrf_error`
- Added a very small authenticated browser restore signal:
  `client_restore_signal`
- The browser signal only fires in likely stale-page situations:
  `pageshow_restore`
  `visibility_resume`
- To keep this lightweight, it does not poll, does not store extra data, and only sends a small GET request when the page is restored from history / bfcache or resumes after being hidden for at least about 60 seconds.

How to check logs in Railway for the logout issue:
- Open Railway.
- Open this project.
- Open the deployed service for the app.
- Go to `Logs`.
- Search for `AUTH_TRACE`.
- Around the reported timestamp, look specifically for:
  `logout_attempt`
  `logout_success`
  `logout_csrf_error`
  `client_restore_signal`
  `login_required_missing_session`

Quick interpretation guide for logout incidents:
- If there is no `logout_attempt` at all, the first logout tap likely never submitted or never reached the server.
- If `logout_attempt` appears but `logout_success` does not, the request likely failed before session clear.
- If `logout_success` appears, the server did clear the session.
- If `logout_success` is followed by later `login_required_missing_session` on the next navigation, the first logout likely worked and the phone was showing a stale protected page.
- If `logout_csrf_error` appears, the logout came from a stale page with expired CSRF state.
- If `client_restore_signal` appears near the same time, that strongly supports the mobile resumed-page / stale-cache theory.

Latest observed log pattern:
- `csrf_error` on `/login` with:
  `error_message: 400 Bad Request: The CSRF token has expired.`
- followed by a later successful `/login` retry
- this is consistent with the user submitting an old login page first, then retrying after refresh or reload

Mitigation added:
- Preserve the current session `csrf_token` during successful login session rotation.
- Disable duplicate login submits on the login page after the first click.
- Login form now sends an `idempotency_key` through the shared submit guard.
- `/login` now uses the server-side idempotency table so repeated submits with the same key replay the first successful redirect instead of reprocessing the login.
- `/login` responses now send no-store/no-cache headers so mobile browsers are less likely to reuse an old page with an expired CSRF token.
- CSRF failures on `POST /login` now redirect back to `/login` with a warning flash instead of showing the generic Access Denied page.

How to check logs in Railway:
- Open Railway.
- Open this project.
- Open the deployed service for the app.
- Go to `Logs`.
- Search for `AUTH_TRACE`.
- Check the lines around the timestamp when the issue happened.
- Look specifically for:
  `login_success`
  `admin_required_forbidden`
  `http_403`
  `http_400`
  `csrf_error`

- Report tracing added for intermittent report hangs:
- Lightweight `REPORT_TRACE` logs were added to the shared sales report builder in `routes/reports_route.py`.
- This covers both `/reports/sales-summary` and `/reports/sales-report-summary`.
- Logged stages:
  `sales_report_data`
  `cash_entries`
  `cash_summary`
  `cash_out_groups`
  plus final `route_complete`
- `route_complete` also logs:
  `sales_count`
  `unresolved_count`
  `cash_entry_count`
  `render_ms`
  `total_ms`

How to check logs in Railway for the report issue:
- Open Railway.
- Open this project.
- Open the deployed service for the app.
- Go to `Logs`.
- Search for `REPORT_TRACE`.
- Compare the slow attempt vs the normal retry.
- Look for which `step=` line has the largest `duration_ms`, or if `route_complete` is missing entirely for the stuck attempt.

Confirmed follow-up finding from Railway logs:
- In at least one "report hang" incident, the report itself finished normally:
  `route_complete ... total_ms=249.35`
- The app then started piling up requests in Waitress:
  `Task queue depth is 1 ... 7`
- The actual failure was repeated `/api/search` requests exhausting the PostgreSQL pool:
  `psycopg2.pool.PoolError: connection pool exhausted`

Current root cause theory:
- This is not always a slow report query.
- The system can look "hung" because the app becomes saturated right after or during normal usage.
- Two likely contributors were identified:
  `/api/search` used nested DB connections inside `search_items_with_stock()`
  the sales `out.html` item/service search debounce was ineffective because a new debounced function was created on every keystroke
- `order.html` search also had no debounce/abort protection, so overlapping searches could stack there too.

Resolution applied:
- Reuse the same DB connection inside `search_items_with_stock()` instead of opening an extra pooled connection through `get_items_with_stock()`.
- Limit stock and pending-PO lookups to only the matched item IDs instead of scanning broader data for every `/api/search`.
- Fix `out.html` to debounce item and service searches per input element.
- Add debounce + request aborting to `order.html` item search.

If the issue appears again:
- Check Railway logs for both `REPORT_TRACE` and `PoolError`.
- If `route_complete` is fast but `waitress.queue` depth climbs and `/api/search` errors appear, the incident is search/pool saturation rather than report generation itself.

Additional tracing added on 2026-04-17:
- Lightweight `REQUEST_TRACE` logs now wrap the main suspect endpoints:
  `/api/search`
  `/transaction/out/save`
  `/api/sales/...`
  `/api/stocktake/...`
  `/reports/...`
- The log format includes:
  `path`
  `method`
  `status`
  `duration_ms`
  `query_len`
  `item_id`
  `user_id`
- Search Railway logs for:
  `REQUEST_TRACE`
  `REPORT_TRACE`
  `PoolError`
  `Task queue depth`
- Quick interpretation guide:
  if `REQUEST_TRACE path=/api/search` appears many times with rising `duration_ms`, the system is likely getting saturated by overlapping search traffic
  if `/transaction/out/save` shows a long duration but still returns `status=200`, the sale may have completed while the client browser timed out or looked stuck
  if `/reports/...` routes stay fast while queue depth still rises, the report page is probably not the true bottleneck
  if none of the server traces are slow during the incident, the problem may be more on the client PC/browser/network side
- Client-side hardening also added:
  abort stale `/api/search` requests in inventory, stock-in, refund replacement search, stocktake item search, and sales item search flows
  ignore stale search responses that return after the user already typed a newer query

- Range report for mechanics may miscalculate quota when mechanic was absent

Example:
3 days × ₱300 = ₱900  
Range logic = no top-up  
Daily logic = ₱200 × 3 = ₱600

Need clarification with owner.

- Box-based PO receiving: under-receive is usually safe for automatic cost-per-piece correction, but over-receive is not always safe.

Discussion / future reference:
- Current box-based receive formula is:
  `cost_per_piece = (boxes received x box cost) / total counted pieces`
- This works well when fewer boxes arrive than ordered, because staff is counting actual delivered pieces from real received boxes.
- Example under-receive:
  ordered `2 boxes`, received `1 box`, counted `12 pcs`, box cost `600`
  result = `600 / 12 = 50 per piece`
- This becomes risky when more boxes arrive than ordered.
- Example over-receive:
  ordered `2 boxes`, received `3 boxes`, counted `36 pcs`, box cost `600`
  current formula would produce `1800 / 36 = 50 per piece`
- Business problem:
  that is only correct if all 3 boxes are billable at full cost.
- If the extra box is free bonus stock, true payable cost is only `1200`, so using all 3 boxes in the cost update would overstate `cost_per_piece`.
- Payables impact:
  current payable amount also follows received box count, not ordered box count.
- Example payable impact:
  ordered `2 boxes @ 600 = 1200`, received `3 boxes`
  current payable display becomes `1800`, which is inflated if the 3rd box was free bonus stock.
- Additional limitation:
  current receive flow only captures one combined counted-piece total for the entire receipt, so the system cannot precisely split counted pieces between ordered boxes and extra boxes.
- Safe future rule candidate:
  allow stock-in for over-received box items, require a note, but do not auto-update `items.cost_per_piece` until billing intent is confirmed.
- Suggested audit / note message:
  "Box over-receive detected. Stock accepted, but cost-per-piece should be reviewed because excess boxes may be bonus stock or separately billable."

## Audit Trail

- Audit tab PO logic may not correctly reflect partial arrivals

## Audit Tab

- PO modal has inconsistent design and status. does not reflect real status. right now a PO that is for revision is marked as for approval
