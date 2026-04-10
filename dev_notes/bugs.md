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

## Possible Issues

- Random admin login redirect to Access Denied on first load, then normal after refresh.

Current theory:
- Probably not a true failed login.
- New strongest theory from repeated logs:
  first `/login` POST succeeds, then a second in-flight or duplicate `/login` POST hits after `session.clear()` removed the session CSRF token.
- That second POST fails in Flask-WTF with:
  `400 Bad Request: The CSRF session token is missing.`
- This can look like a random Access Denied page even though the first login actually worked.
- Temporary tracing was added with `AUTH_TRACE` logs in `app.py`, `auth/utils.py`, and `routes/auth_route.py`.

Latest observed log pattern:
- `login_success` for admin
- immediately followed by `csrf_error`
- same `/login` endpoint and same login referer
- `user_id: None` and `session_role: None` on the CSRF line, which fits a second POST arriving after session reset

Mitigation added:
- Preserve the current session `csrf_token` during successful login session rotation.
- Disable duplicate login submits on the login page after the first click.
- Login form now sends an `idempotency_key` through the shared submit guard.
- `/login` now uses the server-side idempotency table so repeated submits with the same key replay the first successful redirect instead of reprocessing the login.

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
