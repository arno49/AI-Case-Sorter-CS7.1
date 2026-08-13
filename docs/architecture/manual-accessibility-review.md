# Manual Assistive-Technology Review (PI-SWQ-002)

## Why this document exists

`testing-and-quality.md` requires "manual assistive-technology review" as a
pilot entry criterion, and PI-SWQ-002's own fourth bullet asks specifically
for stop/recovery/fault announcements to be documented, with any
remediation, before pilot. This is deliberately **not** something automated
tooling can close: `checkAccessibility` (`appliance/web/src/routes/
accessibility.ts`, PI-SWQ-002) runs axe-core against a layout-less jsdom
document and cannot evaluate whether a screen reader actually announces a
state change at the right moment, in the right words, without being
buried by unrelated chatter - that is a claim only a person operating real
assistive technology can make. This document is the template that review
fills in, not a substitute for having run it. Until a reviewer completes it,
the checklist below stays unchecked, the same "template, not a claim" rule
this project already applies to HIL/DTR/pilot evidence.

## What to check

This project uses exactly two live-region roles throughout, deliberately
not more: `role="alert"` for something that went wrong (an assertive
announcement, interrupting), and `role="status"` for something that
succeeded or is informational (a polite announcement, queued behind
whatever the screen reader is already saying). Every one of them in the
current codebase is listed below, grouped by page. A reviewer's job is to
reach the state that shows each one, using only the assistive technology
and input method named in the session record, and confirm it is announced,
once, in comprehensible words, without duplicating or drowning out another
announcement already in flight.

### Dashboard (`/`) - the highest-priority surface

| Component | Role | Field | Trigger |
| --- | --- | --- | --- |
| `StopControl` | `alert` | `stop-error` | A software stop is refused or fails |
| `StopControl` | `status` | `stop-accepted` | A software stop is accepted |
| `MachineStatus` | `alert` | (unavailable) | The daemon stops answering |
| `MachineStatus` | `status` | (uncertain) | The machine's state is `UNCERTAIN` |
| `RecoveryControl` | `alert` | `recovery-error` | Recovery is refused or fails |
| `RecoveryControl` | `status` | `recovery-accepted` | Recovery is accepted |
| `ManualControls` | `alert` | `command-error` | Connect/home/sort/feed is refused or fails |
| `ManualControls` | `status` | `command-accepted` | Connect/home/sort/feed is accepted |

### `/dataset`, `/routing`, `/system`, `/operations` - secondary surfaces

| Page | Role | Field | Trigger |
| --- | --- | --- | --- |
| `/dataset` | `alert`/`status` | `train-error`/`train-status` | Training triggered/refused |
| `/dataset` | `alert`/`status` | `activate-error`/`activate-status` | Candidate activated/refused |
| `/dataset` | `alert`/`status` | `rollback-error`/`rollback-status` | Rollback performed/refused |
| `/dataset` | `alert`/`status` | `autonomy-review-error`/`-status` | An autonomous attempt reviewed/refused |
| `/routing` | `alert`/`status` | `routing-start-error`/`-status` | A routing run started/refused |
| `/routing` | `alert`/`status` | `routing-stop-error`/`-status` | A routing run stopped/refused |
| `/system`, `/operations`, `/dataset`, `/routing` | `alert` | (unavailable) | The relevant service stops answering |

### `/login`

| Role | Trigger |
| --- | --- |
| `status` | A notice such as "your session ended" is shown |
| `alert` | A failed sign-in attempt |

## Session record

Fill in one row per reviewer session. A single pass with one screen reader
and one keyboard is not sufficient to close this checklist - the pilot
entry criterion is a genuine sample, not one data point.

| Date | Reviewer | Assistive technology + version | Browser | Input method | Pages covered |
| --- | --- | --- | --- | --- | --- |
| _(unfilled)_ | | | | | |

## Findings

For each row in the tables above, record: announced as expected / announced
but wrong wording or timing / not announced / not reached this session.

| Component/page | Field | Result | Notes |
| --- | --- | --- | --- |
| _(unfilled - populate from the tables above during the review session)_ | | | |

## Remediation

Any finding other than "announced as expected" gets a row here, and stays
open until re-verified by a follow-up session, not closed by the same
reviewer's own guess that a fix worked.

| Finding | Owner | Fix | Re-verified (date, reviewer) |
| --- | --- | --- | --- |
| _(unfilled)_ | | | |

## Sign-off

This review is **not complete**, and this document does not represent
completed pilot-entry evidence, until every row above the sign-off line has
a real reviewer, a real session, and every remediation is re-verified.

- [ ] At least one full session completed with a screen reader (NVDA, JAWS, or VoiceOver).
- [ ] At least one full session completed keyboard-only, no pointer device, no assistive technology beyond the OS's own focus indication.
- [ ] Every row in "What to check" has a recorded finding.
- [ ] Every remediation row is re-verified.
