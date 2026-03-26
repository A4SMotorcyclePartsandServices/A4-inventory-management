# Today – March 26

## Tasks
- [ ] Bundling Feature in Sales
- [ ] Edit Item information with history of item edits
- [ ] Import new master item file
- [ ] Stocktake modal in Audit trail tab in audit.html page. no own modal yet
- [ ] new toggle in sales page for mechanic supplies. get cost per piece not selling price and then show in sales report as expense

## Notes
- for meeting tomorrow, ask client if they want staff to access deleted history. we can even make it similar to PO wherein restoring a deleted entry needs admin approval
- box-based PO receiving explanation for client:
  when fewer boxes arrive than ordered, the system can still calculate cost per piece from the actual delivered box(es), because staff counts the real pieces that arrived today.
- box-based PO receiving explanation for client:
  when more boxes arrive than ordered, stock can still be received, but cost per piece becomes a business decision, not just a math decision, because the extra boxes may be bonus stock or may be billable.
- box-based PO receiving explanation for client:
  over-received box items also affect payables today, because the payable amount follows the received box count. if 3 boxes are received at 600 each, payables currently reads 1800 even if the PO only ordered 2 boxes.
- suggested client-facing note:
  "If extra boxes arrive beyond the PO quantity, the system can accept the stock, but item cost should be reviewed first because the excess boxes may be free bonus stock or vendor-billed stock."

## Blockers
- Possible clarification on how bundles are structured in the shop
- Existing bundle sales that were saved before the new "mechanic required" rule may still distort report totals until those records are cleaned up or corrected.
- In `OUT`, bundle sales now require a mechanic, but the page still also requires at least one service entry when a mechanic is assigned. This needs client confirmation for bundles that may have mechanic/share logic but no actual bundled service rows.
- Bundle report math still needs a calmer reset pass. It is currently functional but has been patched heavily and should be re-verified with one clean test dataset before client demo/use.
- Bundle financial snapshots on the sale side do not store bundled item selling-price snapshots per item. Some report calculations are currently using live item selling prices, which can drift if item prices are edited later.
