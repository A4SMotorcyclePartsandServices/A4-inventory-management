# Session Changelog Rules

When I ask:

> "Give me a bulleted list of all changes we made in this coding session"

You must generate the output using Conventional Commit categories.

## Output Format

Group changes by commit type.

Example:

### feat

* Added barcode scanning support to the Items page.
* Added stocktake CSV export.
* Added loyalty status indicator to customer details.

### fix

* Fixed Helicopter Lift card allowing players from different tiles.
* Fixed Pilot special ability remaining available after normal movement.
* Fixed duplicate transaction creation on slow internet connections.

### refactor

* Moved game logic from `board.dart` into `game_controller.dart`.
* Extracted player actions into separate controller files.
* Simplified inventory analytics query structure.

### docs

* Updated user manual screenshots.
* Added deployment instructions.

### test

* Added validation tests for stock reconciliation logic.

---

## Conventional Commit Mapping

Use these labels:

| Label    | Use When                                     |
| -------- | -------------------------------------------- |
| feat     | New functionality added                      |
| fix      | Bug fixes                                    |
| refactor | Code restructuring without changing behavior |
| perf     | Performance improvements                     |
| docs     | Documentation changes                        |
| style    | Formatting-only changes                      |
| test     | Test additions or corrections                |
| build    | Dependency/build system changes              |
| ci       | CI/CD changes                                |
| chore    | Miscellaneous maintenance                    |
| revert   | Reverted work                                |

---

## Rules

### 1. Summarize Changes

Do not describe every line of code.

Instead summarize the user-visible or developer-relevant change.

Good:

* Added stocktake variance reporting.

Bad:

* Added `calculateVariance()` method.
* Added `variance` variable.
* Updated `stocktake_service.py`.

### 2. Merge Related Changes

Combine related work into a single bullet.

Good:

* Added mechanic payout quota top-up functionality.

Bad:

* Added mechanic quota field.
* Added top-up checkbox.
* Added payout calculation.
* Added UI badge.

### 3. Mention Files Only When Important

Avoid mentioning files unless the change is primarily architectural.

Example:

* Refactored Flutter game logic from `board.dart` into `game_controller.dart`.

### 4. Use Past Tense

Bullets should read as completed work.

Example:

* Added customer loyalty redemption tracking.
* Fixed duplicate payment submissions.

### 5. Prioritize Meaningful Changes

Ignore:

* Temporary debugging code.
* Variable renames unless important.
* Minor formatting edits.

---

## Optional Commit Suggestions

If requested, generate commit messages.

Example:

feat(pos): add barcode scanning support

fix(cards): restrict helicopter lift to players on same tile

refactor(game): move board logic into game controller

---

## Context

The user prefers changelogs that are:

* High-level
* Client-friendly
* Suitable for Git commit history
* Suitable for release notes
* Grouped by Conventional Commit type
* Focused on completed work rather than implementation details
