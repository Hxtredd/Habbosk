# Project Owner decisions (append-only)

Decisions made by the Project Owner from the start of the oversight bridge
onward. Earlier decisions stay where they are (`../../DEVELOPMENT_NOTES.md`
and `../decisions/`); they are not copied here. Architecture decisions still
get their own file in `../decisions/`; this log records the decision and
links to it.

Rules: newest entry at the bottom; earlier entries are never changed; a
correction or reversal is a new entry naming the one it changes.

- Only the owner makes a decision here. A Claude session may record one only
  by quoting or exactly referencing the owner's own message, with its date.
- **Silence is never approval.** No answer means not approved.
- A ChatGPT recommendation, a Claude recommendation or a passing test run is
  not a decision.

## Template

```
## D-YYYY-MM-DD-N  <short title>
Date: YYYY-MM-DD
Decision: approved | rejected | deferred | scope boundary
Recorded by: Project Owner | Claude Session 1 | Claude Session 2
Owner's words: "<quote>" (or where the message is, e.g. session and date)
Applies to: branch(es) and exact commit SHA(s), or the scope it limits
Related: R-... / C-... / ../decisions/NNNN-...
Conditions or limits:
```

## Decisions

Decisions are appended below this line, oldest first.
