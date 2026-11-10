# Outstanding owner approvals (SK Brain) - 2026-11-10

Nothing below has happened. Each item names the exact thing to approve.

## Privacy work
1. **Windows results for the privacy validation package** (commit 68b500f; zip SHA-256
   `4cb411195973f14a116fb44bdf846873246a1e0460ddf457d20e7b5c11e8305b`). Pending your run.
2. **Integration of `claude/brain-privacy-shareable`** into `claude/brain-integration`
   (fast-forward only, to an exact SHA - 68b500f unless I report a fix) - after item 1.
   Includes Response.shareable() (a helper only; no sharing feature).
3. **Fast-forward of the record branches** (notes only): `claude/brain-privacy-record`,
   `claude/brain-integration-record`, `claude/brain-b6-integration-record`, `claude/brain-b6-record`.
4. **Committing the deliberate-break harnesses** under `tests/breaks/` - see
   `DELIBERATE_BREAK_HARNESS_SCOPE.md`; not committed anywhere into the product.
5. **`library._atomic_write` shared ".tmp" pattern** - a small hardening change I may not make
   without your approval.

## B7 (documentation only so far)
6. The twelve decisions in `B7_ARCHITECTURE_PROPOSAL.md` (essential ones: 1, 2, 6, 7, 8, 12).
7. Approval to start B6.1 remaining prerequisites (file-access layer, forward-format refusal,
   search-index secure delete) as their own branch.

## Not requested
Releases, tags, deployments, the Verified Learning Engine, desktop SK Assistant changes (owned
by the Hello session), any new dependency, provider or paid service.
