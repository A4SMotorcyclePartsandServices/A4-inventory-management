# Bugs

## Watchlist

Use this section for things that are not necessarily broken right now, but could become a problem later depending on configuration or workflow.

- Loyalty programs: mixed stamp + points setup is currently not used by the client, so no code change is planned right now.
- If a future loyalty program enables both `stamp_enabled` and `points_enabled`, one qualifying sale can earn both a stamp and points.
- If a future mixed program uses `reward_basis = STAMPS_OR_POINTS`, redemption currently prefers consuming stamps first, which can leave points untouched and allow later eligibility from the points side.
- PO overview: active tabs (`For Approval`, `Approved`, `Partial`) still render full datasets on initial page load, so pagination or tab-level lazy loading may be needed as usage grows.
- PO overview: archive month summaries are lazy loaded, but a very large single month may still need paging inside the month response later.
- PO search: current search caps server-side results at 20 and asks users to keep typing; if PO volume grows further, a normalized `po_number_search` column may be better than repeated SQL normalization.
- PO performance: add lightweight timing logs around `/transaction/orders/list`, `/api/orders/search`, and `/api/orders/archive-month` if users start reporting slowness.
- PO modal: if approval history or delivery history grows large for long-lived purchase orders, modal payload size may need trimming or paged history loading.

## Possible Issues

- Range report for mechanics may miscalculate quota when mechanic was absent

Example:
3 days × ₱300 = ₱900  
Range logic = no top-up  
Daily logic = ₱200 × 3 = ₱600

Need clarification with owner.

## Audit Trail

- Audit tab PO logic may not correctly reflect partial arrivals

## Audit Tab

- PO modal has inconsistent design and status. does not reflect real status. right now a PO that is for revision is marked as for approval
