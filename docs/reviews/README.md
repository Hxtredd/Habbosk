# Milestone reviews

One folder per milestone, e.g. `reviews/B7/`, with one report per role:

| File | Written by | When |
|---|---|---|
| `proposal.md` | Lead Developer | before implementation |
| `architecture.md` | architecture-reviewer (Claude subagent) | before implementation |
| `security.md` | security-auditor (Claude subagent) | after implementation; rechecks appended |
| `testing.md` | test-engineer (Claude subagent) | at checkpoints; rechecks appended |
| `completion.md` | Lead Developer | when the milestone is ready for the owner |

These are Claude reviews. Independent oversight by ChatGPT or by the owner
is recorded separately in `completion.md` under "Independent review", only
when the owner reports it.

## completion.md - what it must contain

- Branch and exact commit hashes (first and last), base commit.
- Changed files (path - one line each).
- Decisions taken (links to `../decisions/`), and anything still waiting for
  the owner.
- Security findings: severity, status (fixed in <commit> / accepted by the
  owner / open).
- Tests: Linux full-suite counts; deliberate breaks caught / total; Windows -
  either the BRAIN_WINDOWS_CHECK result with its code fingerprint, or
  "not run on Windows".
- Known limitations and unresolved issues.
- Approval history: who approved what, and when (owner messages only).
