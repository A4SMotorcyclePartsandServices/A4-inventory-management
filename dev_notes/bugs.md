# Bugs

## Watchlist

Use this section for things that are not necessarily broken right now, but could become a problem later depending on configuration or workflow.

- Loyalty programs: mixed stamp + points setup is currently not used by the client, so no code change is planned right now.
- If a future loyalty program enables both `stamp_enabled` and `points_enabled`, one qualifying sale can earn both a stamp and points.
- If a future mixed program uses `reward_basis = STAMPS_OR_POINTS`, redemption currently prefers consuming stamps first, which can leave points untouched and allow later eligibility from the points side.

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
