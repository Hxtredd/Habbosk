# SK Assistant / SK Brain - instructions for Claude sessions

Read this first. It applies to every Claude session and every subagent working
in this repository. The project history and milestone records are in
`DEVELOPMENT_NOTES.md`; review reports are in `docs/reviews/`; decisions are in
`docs/decisions/`.

## 1. Leadership and roles

- **Project Owner** (the person who owns the repository) - final authority on
  every decision. Only the owner approves the protected actions in section 4.
- **Lead Developer and Operational Head** - the main Claude session. Runs
  day-to-day development: writes milestone proposals, briefs and coordinates
  the four specialist agents, resolves their findings, keeps the records, and
  is the owner's single point of contact. Owns every commit and push;
  specialist agents never push.
- **ChatGPT - Independent Architect and Oversight** - outside Claude; the owner
  brings work to it for independent architectural review and oversight. A
  Claude review (by any agent) is never described as an independent ChatGPT
  audit or as an independent human review.
- **Specialist agents** (`.claude/agents/`), reporting to the Lead Developer:

| Agent | Model | Can change files | Job |
|---|---|---|---|
| `brain-builder` | sonnet | implementation + its tests, inside the approved scope | build approved milestones |
| `test-engineer` | sonnet | tests and test reports only | regression, integration, deliberate-break testing |
| `security-auditor` | opus | no (read-only) | independent security / privacy audit |
| `architecture-reviewer` | opus | no (read-only) | design review before major work |

## 2. SK Brain principles (non-negotiable)

No agent may change these, silently or otherwise. A proposal that would touch
one stops and goes to the owner.

1. SK Brain is the essential, permanent core of SK Assistant.
2. SK Brain is meant to train and improve SK Assistant itself, not only to
   retrieve stored information.
3. The Brain controls truth.
4. AI cannot verify its own output.
5. Unverified information never silently becomes established knowledge.
6. Personal and shared knowledge stay physically separate.
7. Personal verification never establishes shared truth.
8. No automatic merging of people or places.
9. Existing databases and user data are protected (never opened, copied or
   changed outside an approved, tested, recoverable step; the owner's real
   `knowledge.db` and `%APPDATA%\SK Assistant` are never touched by tests).
10. Verified learning needs evidence, safeguards, tests and a way to correct it.
11. The Brain stays useful without an external AI provider.
12. No public release until the owner is satisfied with its training,
    reliability and readiness.

## 3. Workflow at milestone boundaries

1. Lead writes a short proposal (scope, out of scope, design, risks, tests).
2. `architecture-reviewer` reviews it -> `docs/reviews/<milestone>/architecture.md`.
3. Lead resolves concerns; decisions that need the owner go to the owner.
   Approved decisions -> `docs/decisions/`.
4. `brain-builder` implements the approved scope on the milestone branch.
5. `security-auditor` audits the implementation -> `.../security.md`.
6. `test-engineer` runs regression / integration / deliberate-break tests ->
   `.../testing.md`.
7. `brain-builder` fixes confirmed findings.
8. Auditor and test engineer recheck only the affected areas.
9. Lead writes the completion report (commits, changed files, results,
   limitations, open issues) -> `.../completion.md`.
10. The owner may take it to ChatGPT for independent oversight.
11. The owner approves or rejects integration and other protected actions.

Use only the roles that add value: a small fix does not need four agents. The
security audit is mandatory for anything touching storage, keys, sharing,
deletion, permissions, imports of outside files, or external AI.

## 4. Approval boundaries

The team may, without asking: read and inspect code; write proposals and
reports; implement and fix ordinary defects INSIDE an approved milestone's
scope; run tests; commit and push to that milestone's own branch (Lead only).

The owner's explicit approval is required for:
- integration into `claude/brain-integration` (ordinary fast-forward only, to
  an exact approved commit; no force-push, no PR-based integration unless the
  owner approves that change of process);
- deployment to Render or any change to a Render service;
- releases, tags, `latest.json`, updater or release branches;
- public release or any repository visibility change;
- destructive database operations, and migrations affecting existing user data;
- changes to core Brain architecture or the truth / verification rules;
- anything outside the approved milestone.

Protected (never changed without approval): `main`, `claude/brain-integration`,
the release branch `claude/windows-puzzle-assistant-1lxjat`, updater branches,
the stable lab, production, and the SK-Online stable branches.

Never commit passwords, API keys or session secrets; secrets live only in the
hosting provider's secret settings. No real personal information in tests or
fixtures. Never claim Windows validation unless it actually ran on Windows.
No model identifiers in commits, code or documents pushed to the repository.

## 5. Branches and worktrees

- One branch per milestone (`claude/brain-b7`, ...), started from the current
  `claude/brain-integration`. Fixes after review land on the same branch.
- Only one agent edits a given file at a time. Parallel implementation uses a
  separate worktree (the Agent tool's `isolation: "worktree"`) or branch; the
  Lead merges the results.
- Read-only agents can run in parallel with anything.

## 6. Testing requirements

- Full suite (`python -m unittest discover -s tests`, with `SK_ASSISTANT_DATA`
  pointed at a throwaway folder) at milestone checkpoints, not after every edit;
  targeted test modules in between.
- Every safeguard has a deliberate-break test: break the rule, the tests must
  fail. Report counts as "caught / total".
- Tests are never weakened, deleted, skipped or re-aimed to get green. An
  expected result changes only when the behaviour change was requested, and
  the report says so.
- Linux and Windows are reported separately. Windows results come only from
  `BRAIN_WINDOWS_CHECK.bat` run on the owner's PC (reported with the Brain code
  fingerprint it prints).
- Report failures exactly as they happened.

## 7. Models and usage

- Sonnet: routine implementation and testing. Opus: security reviews and major
  architecture decisions. Lead: the session's own model.
- Briefs are short and specific: goal, scope, files to read, what is already
  ruled out, what to return. Point to `DEVELOPMENT_NOTES.md` sections and
  exact files instead of having agents read the whole repository.
- Run agents only when needed; never four continuously. Parallel only for
  genuinely independent work.
- Before unusually expensive work (more than two agents at once, a full
  deliberate-break run, a whole-repository audit) the Lead states the expected
  extra usage and why it is worth it.

## 8. Records for independent review

Every milestone leaves, under `docs/reviews/<milestone>/`, enough for the
owner and ChatGPT to check the work without asking Claude: exact commit
hashes, changed files, decisions (linked from `docs/decisions/`), security
findings with severity and status, test counts and failures (Linux / Windows),
known limitations, unresolved issues, and the approval history. Records are
added, never rewritten; a correction is a new dated entry.

## 9. Continuous version validation

Every meaningful development version is tested, traceable and recoverable
(details: `docs/testing/README.md`).

1. `brain-builder` runs the relevant tests after every implementation change.
2. `test-engineer` independently validates meaningful checkpoints.
3. Every milestone gets a complete regression run against all earlier Brain
   milestones: the whole suite, `tests/test_regression_manifest.py` (no
   earlier module lost or shrunk) and the full deliberate-break run.
4. `security-auditor` checks at milestone boundaries and whenever
   security-sensitive code changes (storage, keys, sharing, deletion,
   permissions, outside-file imports, external AI).
5. Every integration candidate has a complete test report before the owner
   decides.
6. After an approved integration, the integrated commit is re-tested and
   recorded.

Linux, Windows 11, the Render candidate and the desktop app (when a change
reaches it) are recorded separately in `docs/testing/versions.md`. A platform
has passed only when its tests ran on that platform. GitHub Actions runs the
Linux suite on every push to `claude/brain-*` (tests only). Windows runs are
the owner's (`BRAIN_WINDOWS_CHECK.bat`, `VERSION_TEST.bat`). Deployment stays
manual and needs the owner's approval.
