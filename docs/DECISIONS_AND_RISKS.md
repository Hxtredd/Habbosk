# Decisions made independently, risks and blockers (SK Brain lead) - 2026-11-10

## Decisions I made myself (routine; within the approved architecture)
| Date | Decision | Why / assumption |
|---|---|---|
| 11-10 | Created child branch `claude/brain-privacy-ask-labels` from 68b500f for the reviewer's `steps[].ask` concern | Keeps the reviewed candidate (68b500f) intact; the owner asked for a separate child branch |
| 11-10 | `shareable()` verifies every word of step labels/values, value, possible and sentence against the response's own shared material or the fixed wording; fails closed | Defence in depth: no real flow leaked, but the helper trusted label text |
| 11-10 | Validation package tooling is built AROUND the exact source commit, never inside it; stricter break classification (assertion = caught) | The package must prove the exact commit; crashes must not count as caught |
| 11-10 | B7 proposal decision 6: now recommend planning a safe withdrawal design (B7d) instead of leaving non-withdrawal as the default | New evidence: B5.2 withdrawal and purge-pending precedents |
| 11-10 | Decision 7: decide by runtime SQLite capability rather than Python label; tested floor stays Python 3.14 until older versions run on Windows | README says 3.9-3.13 but only 3.14 was ever run on Windows |
| 11-10 | Relayed the desktop v3.4 message to Session 3 instead of acting; withdrew it when the owner said to ignore it | Desktop development belongs to Session 3 |

## Needs the owner (short list)
Windows results for the privacy package; integration of the privacy branch (after the ask-labels
child branch is decided: integrate 68b500f alone, or the child tip); B7 decisions 1, 2, 6, 7, 8,
12; `library._atomic_write` hardening; committing the break harnesses (scope note written).

## Outstanding risks and blockers
- No Windows run of the privacy candidate yet (blocker for integration, not for other work).
- Security Auditor reviews are Claude reviews. The only external review so far is ChatGPT's of
  68b500f (one concern, addressed on the child branch).
- `personal_details` evidence marker is set by respond() and trusted by shareable() (inherent).
- Custom relation labels (added by the owner/moderator) would appear in step labels; they are
  installation configuration, not user content; shareable() would refuse a label word it cannot
  find in the shared material (fails closed, so such answers are refused, not leaked).
- README states Python 3.9-3.13; Brain checks run only on 3.14 (see decision 7).
