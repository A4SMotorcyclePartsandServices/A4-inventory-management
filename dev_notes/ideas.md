# Blind Count Mode For Stocktake

## Section A - Client Discussion

Problem:
We already hid the system stock and variance inside the stocktake page so staff cannot adjust their count based on what the system says. However, if the same user can open another tab like the main item search, the OUT page, or any other page that still shows current stock, they can still see the live stock there. That weakens the purpose of the blind counting workflow.

Suggested direction:
Instead of only hiding stock inside the stocktake page, we may need a special "blind count mode" for users who are actively doing stocktake. While that mode is active, the system would hide stock information across the app for that user, not only in the stocktake page.

Why this matters:
- It keeps the physical count more honest.
- It reduces the chance that staff will adjust counts to match what the system says.
- It makes the stocktake process more consistent.

Decision needed from client:
- Should stocktake staff be fully blocked from seeing live stock anywhere while counting?
- Or do they still need to see stock on some pages for normal operations?
- Should this apply only to selected users/roles, or to anyone who starts a stocktake?

Recommended client-facing option:
Create a temporary blind count mode for stocktake users. While active, stock is hidden across the app for them. Admins and managers can still keep normal visibility if needed.


## Section B - Build Notes

Core issue:
This cannot be solved only in `templates/stocktake/detail.html`. The current stock is still exposed in other screens and shared search endpoints such as:
- `index`
- OUT / transaction flows
- `/api/search`
- any item lookup that returns `current_stock`

If the backend still sends the stock number, a determined user can still see it through another screen or even the network response. So this must be enforced in the shared data layer, not just hidden in HTML.

Best implementation shape:
Introduce a user/session-level "blind stock mode".

Possible behavior:
- When a user starts or opens an active draft stocktake they are working on, set a flag like `session["blind_stock_mode"] = true`
- While this mode is active, shared search/data endpoints should suppress:
  - `current_stock`
  - possibly `pending_stock`
  - any other inventory quantity that could reveal the answer
- When stocktake is confirmed/cancelled/exited, clear the flag

Preferred permission model:
- Admin/manager roles can bypass blind mode if needed
- Stocktake staff / encoders should not receive stock values while blind mode is active

Important note:
If the same user must both:
- perform blind stock counting
- and continue normal OUT/sales operations that require live stock visibility

then this becomes a business-rule conflict, not only a technical one. The client needs to decide whether stocktake users should be truly blind during the session or only partially restricted.

Files / areas likely affected:
- `app.py`
  - `/api/search`
- `services/inventory_service.py`
  - `search_items_with_stock()`
  - `get_items_with_stock()` if any UI path depends on it directly
- transaction / OUT pages that display stock
- any templates that show `current_stock`
- possibly auth/session handling if blind mode becomes a persistent user state

Possible implementation options:

1. Role-based blind visibility
- Add a role such as `stocktake_staff`
- That role never receives stock values on operational screens

2. Temporary blind stock mode
- Keep existing roles
- Turn blind mode on only while doing stocktake
- Probably better fit for current workflow

3. Assignment-based blind sessions
- Only the assigned stocktake user gets blind mode
- More accurate but more work

Recommended path if implemented:
1. Add blind mode flag at session/user level
2. Update shared search APIs to omit stock values when blind mode is active
3. Update item search/result templates to gracefully handle missing stock
4. Keep stock visible for admins unless client says otherwise
5. Add a visible UI indicator like "Blind Count Mode Active" so the user understands why stock is hidden

Main reminder:
Do not treat this as a stocktake-page-only patch. The real leak is the shared stock visibility across the app.
