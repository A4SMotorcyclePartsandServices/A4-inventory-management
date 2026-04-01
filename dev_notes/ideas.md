## Mobile Version Designing

### Current edited pages

- `templates/base.html`
- `templates/index.html`
- `templates/users/audit.html`
- `templates/sales_analytics.html`

### Current direction

Goal:
Make the existing desktop-first pages mobile-safe without creating a separate mobile version of every page.

Current approach:
- improve shared mobile behavior in `base.html` only when low-risk
- prioritize page-level responsive polish for pages the client/admin actually uses on phone
- keep layouts readable, tappable, and horizontally scrollable when full table conversion is not worth it yet

### Page notes

#### `templates/base.html`

Current status:
- topbar title / notification / profile layout was adjusted for smaller screens

Potential problems:
- mobile sidebar behavior is still fragile and should not be heavily refactored again without a safer isolated pass
- the custom sidebar open/close logic can still be risky on mobile navigation flows

Possible improvements:
- keep future sidebar work minimal unless tested carefully
- if revisited later, do a dedicated isolated prototype branch for mobile nav behavior only
- avoid mixing mobile nav experiments with page-level responsive work

#### `templates/index.html`

Current status:
- header actions stack better on mobile
- search area is more phone-friendly
- admin import controls are easier to use on narrow screens
- inventory table remains scrollable with improved small-screen spacing

Potential problems:
- the inventory table is still dense on very small phones
- price and stock columns may still feel cramped in portrait mode
- admin import area can become tall and push the inventory list too far down

Possible improvements:
- consider hiding or collapsing lower-priority columns on very small screens
- consider a compact item summary card layout for mobile only if the table still feels too heavy
- consider moving admin import tools behind a collapsible section on phone

#### `templates/users/audit.html`

Current status:
- header and back button stack more cleanly
- top tab list is now horizontally scrollable
- cards and tables are less cramped on smaller screens

Known issue:
- the audit tab in `audit.html` is still a little cramped on mobile

Potential problems:
- too many tabs for one row even with horizontal scrolling
- some audit tables are still very wide and require a lot of sideways movement
- filter and action-heavy sections may still feel crowded on smaller devices
- modal content may still be dense for review-heavy admin workflows

Possible improvements:
- consider grouping lower-priority tabs into fewer admin sections later
- add small mobile tab labels or icon-first labels if the tab bar still feels too long
- selectively simplify the heaviest filter rows on phone
- identify the most-used audit sub-tabs and give those the best mobile treatment first

#### `templates/sales_analytics.html`

Current status:
- page already stacked reasonably well on mobile
- spacing, KPI sizing, section headers, filter controls, and chart/table proportions were polished

Potential problems:
- tables still rely on horizontal scrolling for detailed breakdowns
- some profit spotlight content can still feel text-heavy on smaller phones
- charts may still look dense in portrait mode for quick reading

Possible improvements:
- consider shorter helper text on mobile for the profit section
- consider reducing visible table columns for smaller screens if needed later
- consider adding quick-jump links to the major sections of the analytics page

### General reminder

- mobile-safe is the current target, not full mobile redesign
- prefer incremental page-level fixes over shared navigation rewrites
- test each page in portrait width before moving to the next one
- do not attempt another mobile sidebar redesign unless it is isolated and easy to revert

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


## Re order algorithm

## Current algorithm status

Applied:

- [x] Centralized shared restock logic in `services/inventory_service.py`
- [x] Excluded `svc` category from the reorder / restock algorithm
- [x] Classified non-`svc` items by filtered recent `OUT` movement using adaptive windows
  - `active`
    - `15+ OUT in 30 days` and at least `3` sale days
    - or `3-14 OUT in 60 days` and at least `2` sale days
  - `recovering`
    - `1-2 OUT in 90 days`
    - or weak recent movement that does not yet meet the active sale-day rule
  - `dead_stock`
    - `0 OUT in 90 days`
- [x] Added different restock rules per class
  - `dead_stock`
    - only alerts when `current_stock <= 0`
  - `recovering`
    - uses a small fixed fallback floor of `1`
  - `active`
    - uses movement-based formula
- [x] Removed `reorder_level` from the current low-history / recovering fallback path
  - reason: stale historical reorder levels could trigger false restock alerts when an item moves from dead stock to recovering
- [x] Added urgency / restock status output
  - `excluded`
  - `healthy`
  - `warning`
  - `critical`
- [x] Added explainability fields in the shared output
  - `history_status`
  - `historical_out_last_30_days`
  - `historical_out_last_60_days`
  - `historical_out_last_90_days`
  - `sale_days_last_30_days`
  - `sale_days_last_60_days`
  - `sale_days_last_90_days`
  - `selected_lookback_days`
  - `historical_out_in_selected_window`
  - `selected_sale_days`
  - `avg_daily_usage`
  - `effective_lead_time_days`
  - `lead_time_source`
  - `vendor_lead_time_sample_size`
  - `lead_time_demand`
  - `safety_stock`
  - `suggested_restock_point`
  - `restock_basis`
  - `restock_status`
- [x] Wired the shared logic into the inventory page, low-stock page, dead-stock page, and search results
- [x] Added temporary debug mode on `/low-stock?debug=1`
  - hidden again after validation
- [x] Filtered demand sources more intelligently
  - only demand-like `OUT` reasons count toward reorder demand
  - currently includes:
    - `CUSTOMER_PURCHASE`
    - `BUNDLE_PURCHASE`
- [x] Added vendor-derived lead time from completed PO history
  - uses vendor-level median completed PO duration
  - requires at least `3` completed POs
  - falls back to default `7` days when PO history is too thin
- [x] Added adaptive lookback windows
  - fast movers use `30 days`
  - standard movers use `60 days`
  - slow movers / recovery checks use `90 days`
- [x] Added better inactivity / recovery rules
  - active classification now requires multiple distinct sale days, not only total quantity
- [x] Added lazy-loaded debug table on `/low-stock?debug=1`
  - reduces browser lag by loading debug rows in chunks

## Current live formula

- Shared defaults:
  - default lead time = `7 days`
  - default safety window = `7 days`
- Demand source filter:
  - only `OUT` rows with approved demand reasons count toward movement history
- Adaptive history windows:
  - `active` in `30 days` if `OUT >= 15` and `sale_days >= 3`
  - else `active` in `60 days` if `OUT = 3-14` and `sale_days >= 2`
  - else `recovering` in `90 days` if `OUT >= 1`
  - else `dead_stock`
- `dead_stock`
  - `suggested_restock_point = 0`
  - flagged only if `current_stock <= 0`
- `recovering`
  - `suggested_restock_point = 1`
  - flagged only if `current_stock <= 1`
- `active`
  - `lead_time_demand = ceil(avg_daily_usage * effective_lead_time_days)`
  - `safety_stock = ceil(avg_daily_usage * 7)`
  - `suggested_restock_point = lead_time_demand + safety_stock`
  - `critical` if `current_stock <= 0` or `current_stock <= lead_time_demand`
  - `warning` if `current_stock <= suggested_restock_point`

## Remaining work

- [x] Decide whether debug mode should stay, be hidden, or be removed after validation
  - hidden after validation
- [X] ~~*Clean up old `reorder_level` usage in the wider product*~~ [2026-03-30]
  - the current algorithm no longer relies on it for restock alerts
  - field still exists in DB/forms/imports for legacy reasons

## Future upgrades

- [ ] Improve safety stock calculation using demand variability instead of a fixed 7-day buffer
- [ ] Add recommended order quantity
  - target coverage days
  - current stock
  - minimum order quantity
  - case pack / purchase multiple
- [ ] Add item-level restock controls
  - `is_restock_exempt`
  - `restock_strategy`
  - `minimum_order_qty`
  - `case_pack`
- [ ] Optional future upgrade: item-level lead time override
  - current live version uses vendor-level completed PO history only
  - if needed later, item-level lead time can override vendor lead time
