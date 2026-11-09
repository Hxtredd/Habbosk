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

## D-2026-11-09-1  Interim oversight log location (Option A)
Date: 2026-11-09
Decision: approved (Option A); scope boundary (Option B not approved)
Recorded by: Claude Session 2
Owner's words: "I approve the following:

1. Option A: claude/keen-maxwell-ldizsi is the single authoritative location for oversight logs until the bridge is formally integrated.
2. Session 1 may fetch and read those logs, but must not modify that branch or its logs.
3. Approve a documentation-only interim pointer in CLAUDE.md section 10 directing sessions to claude/keen-maxwell-ldizsi for the oversight files.
4. Record this approval in OWNER_DECISIONS.md, quoting my decision above. Do not backfill earlier decisions.
5. You may make only these documentation changes on your own branch and commit/push them there.

Do not merge, rebase, integrate, or modify Session 1’s branch. Do not change any B6 code or tests. Report the exact commit SHA and changed files when finished.

Option B remains unapproved and must wait for my separate approval."
(Owner's message to Claude Session 2, 2026-11-09.)
Applies to: claude/keen-maxwell-ldizsi from 663fff823235a9d65d562a5cf8734548a73a2a17 onward; docs/oversight/ and CLAUDE.md section 10 only
Related: R-2026-11-09-1, C-2026-11-09-1
Conditions or limits: documentation only; no merge, rebase or integration;
Session 1's branches and B6 code and tests unchanged; Option B (fast-forward
of claude/agent-team-setup) needs a separate owner approval.
