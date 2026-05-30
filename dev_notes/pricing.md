# Pricing and Change Classification Guide

This file helps classify client requests as Free Fixes, Goodwill Fixes, Paid Maintenance, or Change Requests.

Codex should use this file when reviewing requested changes. It should inspect the codebase, identify affected files/modules, estimate implementation risk, and recommend a category and price range.

Final pricing decision remains with the Developer.

---

## 1. Classification Rules

### A. Free Fix

Use this only when the issue is a confirmed system defect within the included support/acceptance period.

A request may be a Free Fix if:

* An existing feature does not function as documented or intended.
* The issue is caused by developer implementation error.
* The fix does not introduce new workflow, new UI behavior, new reports, or new business rules.
* The issue was reported within the agreed support or acceptance period.

Examples:

* Save button fails due to system error.
* Report total is mathematically wrong due to query logic.
* Page crashes under normal documented usage.
* A documented workflow cannot be completed.

Estimated price: ₱0

---

### B. Goodwill Fix

Use this for very small, low-risk adjustments that help maintain relationship goodwill but do not meaningfully alter system behavior.

A request may be a Goodwill Fix if:

* It is a small display, wording, label, default value, or formatting adjustment.
* It affects only one page or one small section.
* It does not introduce new logic.
* It does not change how data is saved.
* It does not affect reports, analytics, inventory, cash ledger, payables, or audit records.
* It can be completed and tested quickly.

Examples:

* Change display text or label.
* Adjust default date range from 30 days to 90 days.
* Reorder report columns without changing calculation logic.
* Add a simple confirmation modal or warning message.
* Minor visual adjustment.

Estimated price: ₱0–₱1,000 depending on frequency and timing.

Note: Multiple goodwill fixes may be grouped and counted as one minor revision at Developer discretion.

---

### C. Paid Maintenance / Data Correction

Use this when the system is functioning, but the client needs developer intervention due to operational error, staff mistake, data inconsistency, backend correction, or post-support issue.

A request is Paid Maintenance if:

* It requires backend database inspection or correction.
* It involves production data.
* It was caused by incorrect encoding, wrong staff action, duplicate submission, incorrect approval, wrong receiving, or failure to follow documented workflow.
* It occurs after the included support period.
* It requires developer investigation, manual reconciliation, or deployment.
* It fixes a real operational problem but does not create a new system capability.

Examples:

* Staff marked PO as fully delivered instead of partial.
* Duplicate cash ledger entry caused by double submit or unstable connection.
* Incorrect sale, stock movement, payable, or ledger record needs review.
* Backend reversal or reconciliation is needed.
* Account recovery after support period.
* Onsite troubleshooting or urgent support.

Estimated price:

* Simple correction: ₱1,000–₱2,000
* Medium correction: ₱2,000–₱4,000
* High-risk production data correction: ₱4,000–₱8,000+
* Onsite visit: add onsite/service fee if applicable
* Emergency/urgent same-day work: add urgency fee if applicable

---

### D. Paid Change Request / Enhancement

Use this when the request adds, expands, automates, or improves system behavior beyond the delivered scope.

A request is a Change Request if:

* It adds new logic.
* It changes workflow behavior.
* It adds a feature to a page, even if similar functionality exists on another page.
* It affects multiple files, services, templates, routes, models, reports, or JavaScript logic.
* It changes how data is saved, retrieved, summarized, or displayed.
* It introduces new validation rules, automation, reports, filters, exports, or categories.
* It requires regression testing.
* It is meant to make an existing process faster, safer, or more convenient, even if the current process already works.

Examples:

* Batch Manual IN instead of single-item Manual IN.
* Barcode scanning integration.
* New report breakdowns.
* New cash ledger category behavior that affects analytics/reports.
* Adding existing feature from one page to another page.
* New validation workflow before receiving.
* Automating account disable/enable schedules.
* New export or PDF format.
* New dashboard metric.
* New filter that changes query behavior.

Estimated price:

* Small enhancement: ₱1,500–₱3,000
* Medium enhancement: ₱3,000–₱8,000
* Large workflow change: ₱8,000–₱15,000
* Major feature/module: ₱15,000–₱25,000+
* High-risk financial/inventory feature: price higher due to testing and regression risk

---

## 2. “Exists Somewhere Else” Rule

If a feature exists on another page but must be added to a new page, it is still considered a Change Request unless it is purely visual.

Reason:

* The feature must be integrated into a different workflow.
* The page may have different permissions.
* It may require different queries or data structures.
* It must be tested in the new context.
* It may affect reports, audit trails, or linked modules.

Reusable code reduces implementation time, but it does not automatically make the request free.

---

## 3. “After Heavy Usage” Rule

Some issues only become visible after real-world usage. This does not automatically make the request free.

Classify based on the actual nature of the work:

* If an existing documented feature is broken due to developer error within support period: Free Fix.
* If the system works but users want smoother workflow: Change Request.
* If the issue came from staff mistake or incorrect operation: Paid Maintenance.
* If it adds a safeguard against future staff mistakes: Change Request.
* If it is a small wording/display/default adjustment: Goodwill Fix.

Key question:

Can the user already complete the business process using the current system?

If yes, the request is likely an enhancement, not a bug.

---

## 4. Technical Impact Scoring

Codex should inspect the request and assign an impact score.

### Low Impact

* 1–2 files affected
* No database changes
* No report changes
* No core workflow changes
* Minimal regression risk

Suggested category:
Goodwill Fix or Small Enhancement

Suggested price:
₱0–₱3,000

---

### Medium Impact

* 3–6 files affected
* Some route/service/template/JS interaction
* Some validation or query changes
* One module affected
* Moderate testing required

Suggested category:
Paid Maintenance or Medium Enhancement

Suggested price:
₱2,000–₱8,000

---

### High Impact

* 7+ files affected
* Multiple modules affected
* Database schema or migration needed
* Reports/analytics affected
* Inventory, sales, cash ledger, payables, or audit trail affected
* Regression testing required

Suggested category:
Change Request

Suggested price:
₱8,000–₱15,000+

---

### Critical Impact

* Production data correction
* Financial records affected
* Inventory history affected
* Audit trail affected
* Sales, refunds, payables, receiving, or cash ledger affected
* Risk of data inconsistency if done wrong

Suggested category:
Paid Maintenance or Major Change Request

Suggested price:
₱4,000–₱25,000+ depending on scope

---

## 5. Modules That Require Extra Caution

Requests touching these areas should be priced carefully:

* Sales
* Refunds
* Void Sales
* Receivables
* Cash Ledger
* Purchase Orders
* Receiving
* Payables
* Stocktake
* Inventory transactions
* Item cost/pricing logic
* Loyalty points/stamps
* Reports and analytics
* Audit trails
* User access and permissions

If a request touches any of these, Codex should mark the request as at least Medium Impact unless proven otherwise.

---

## 6. Codex Output Format

For every request, Codex should output:

```md
# Change Assessment

## Request Summary
[Brief description of requested change]

## Current System Behavior
[What the system currently does]

## Requested Behavior
[What the client wants changed]

## Classification
Free Fix / Goodwill Fix / Paid Maintenance / Change Request

## Reason for Classification
[Explain why]

## Affected Files / Modules
- [file 1]
- [file 2]
- [module]

## Technical Impact
Low / Medium / High / Critical

## Risk Notes
[Possible regression/data/inventory/reporting risks]

## Testing Needed
- [test 1]
- [test 2]

## Suggested Price Range
₱X–₱Y

## Suggested Client Explanation
[Plain-English explanation that can be sent to client/auntie]
```

---

## 7. Pricing Notes

Pricing should consider:

* Number of files affected
* Number of modules affected
* Whether database changes are needed
* Whether production data is involved
* Whether reports or analytics are affected
* Whether inventory/financial records are affected
* Whether onsite work is needed
* Whether the request is urgent
* Whether previous unpaid/pending work exists
* Whether regression testing is required
* Whether the feature reduces staff workload or prevents future mistakes

Developer time is not the only basis for pricing. Pricing also includes responsibility, production risk, testing, and business value.

---

## 8. Default Minimums

Use these as default minimum charges after support period:

* Remote small support: ₱500–₱1,000
* Simple code change: ₱1,500 minimum
* Production database correction: ₱2,000 minimum
* Workflow change: ₱3,000 minimum
* Report/analytics logic change: ₱3,000 minimum
* Inventory/cash/payables-related change: ₱4,000 minimum
* Onsite visit: separate fee or included only if explicitly agreed
* Emergency same-day support: higher rate

---

## 9. Final Rule

If the system already performs the business process correctly, but the request makes it faster, safer, more detailed, more automated, or more convenient, it is not a bug.

It is an enhancement or Change Request.
