# Today – March 16

## Tasks
- [X] ~~*Change hard delete to soft delete. make it recycle bin style, make the deleted data able to come back, we have to record who deleted data, auto clear recycle bin every month*~~ [2026-03-21]
- [X] ~~*new scenario. per box or per plastic items. as per discussion, this will diverge wherein we will have to record the item's price per box. basically say oil costs 300 a piece, per box contains 12 pieces. we need to make sure the system records that per box is 12 pieces so that means we have 36 oil incoming and their price is still 300 unless edited in est cost in the order page. then the box costs say 10,000. that one will diverge towards the payables page where the po cheque system lives. items quantity that will arrive will come from number of boxes times items per box.*~~ [2026-03-21] 
- [ ] Sales analytics

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
Need clarification on mechanic attendance logic.