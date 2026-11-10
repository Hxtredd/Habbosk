# Outstanding owner approvals (SK Brain) - 2026-11-10

Nothing below has happened. Each item names the exact thing to approve.

## Privacy work
1. **Windows results for the privacy validation package.** Run the NEWER package (commit a79e2ed, zip
   SHA-256 `cf69352f921d2b97314042b7081c2b0a81790fdd9915fcebdb8fbf65552d9393`); the 68b500f package
   (`4cb41119...`) is superseded unless you prefer to integrate 68b500f alone.
2. **Integration** into `claude/brain-integration` (fast-forward only, to an exact SHA). Two candidates:
   `claude/brain-privacy-shareable` (68b500f, the externally reviewed commit) or its child
   `claude/brain-privacy-ask-labels` (a79e2ed, my recommendation: it contains 68b500f plus the
   label hardening) - after item 1 on the chosen package.
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
