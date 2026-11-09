# 0001 Permanent AI development team

Status: approved by the owner on 2026-11-09

Context: the owner wants SK Assistant development to progress through B7 -
B10, the Visual Understanding Engine and the Verified Learning Engine with
less manual coordination, without losing quality, security or control.

Decision: the Project Owner is the final authority. A Lead Developer and
Operational Head (the main Claude session) coordinates four project subagents defined in `.claude/agents/`: brain-builder (sonnet),
test-engineer (sonnet), security-auditor (opus, read-only) and
architecture-reviewer (opus, read-only), following the workflow, approval
boundaries and usage rules in `CLAUDE.md`. ChatGPT is the Independent
Architect and Oversight, outside Claude.
Experimental Agent Teams are not used.

Consequences: every meaningful version is tested and recorded
(`docs/testing/`); milestone records move to `docs/reviews/` and `docs/decisions/`
from B7 on; the existing milestone-branch + fast-forward integration process
is unchanged; protected branches and actions still need the owner.

Approval: the owner approved the revised setup on 2026-11-09 ("I approve
the revised agent-team setup"), including the continuous version-testing
requirements, the regression manifest, the GitHub Actions workflow and
`.claude/settings.json`; not merged into any other branch.
