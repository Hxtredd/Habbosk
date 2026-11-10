# Brief for an INDEPENDENT review of the production-code changes (commit a79e2ed)

Please read this first: the Security Auditor reviews I (Claude, the SK Brain lead session) ran are
**my own reviews, not independent**. They found real issues and I fixed them, but they share my
assumptions. This brief is for a reviewer who is NOT part of the Claude sessions (for example the
ChatGPT oversight reviewer, relayed by the owner). Nothing below should be treated as approved.

## What to review
Commit `a79e2ed474bf1d38c73f871d5618dde96ad0e77b` (branch `claude/brain-privacy-ask-labels`),
child of the already externally reviewed `68b500f3d691f714425497ceb6eac3beac8967ac`.
Production code changed since 68b500f - 3 files, 177 diff lines (patch attached:
`production_diff_68b500f_to_a79e2ed.patch`; full `shareable_a79e2ed.py` attached for context):
- `puzzle_assistant/brain/shareable.py` - new provenance check, fixed vocabularies, subject vouching.
- `puzzle_assistant/brain/model.py` - `Citation.subject: str = ""` (one defaulted field).
- `puzzle_assistant/brain/respond.py` - `_cite` now reads the claim's subject via `store.claim()`.
Everything else since 68b500f is tests.

## Background (your earlier finding)
`Response.shareable()` removed the top-level question but kept `steps[].ask`, which reason.py builds
partly from names the asker typed. My trace found no real flow where a response marked
`audience="shared"` has a label with personal text (such labels appear only on answers that are
not shared), but the helper trusted the label text. The change makes `shareable()` verify that
every word of each step label/value, the value, possible value and sentence comes from the
response's own shared material (claim texts, kept evidence and passages, hit texts, citation
subjects) or from the fixed answer wording, and fail closed (NotShareable) otherwise.

## Please try to break these claims
1. No personal string of the asker (typed name, personal alias, personal entity name, personal
   claim text, source title/quote/date, personal passage) survives `sanitize_response` /
   `sanitize_answer` when the input is marked shared - including through the word-level
   corpus (an alias built only from shared/template words), `Citation.subject`, hit texts, and
   template words that could coincide with a name.
2. `Citation.subject` only vouches; it is blanked in the output. Could a personal subject reach
   it? (`_cite` reads `store.claim(id, viewer)`; claims are not projected as stubs in split.py -
   please check the union views.)
3. The `respond._cite` change cannot alter any existing response output, and the extra
   `store.claim` call stays within the guard that pins the store calls respond() may use.
4. Fail-closed behaviour: wrong types, unknown fields, enum slots (outcome, claim/citation/hit
   status, hit origin), malformed containers all raise NotShareable, never another exception that
   a caller might swallow.
5. The original response is never mutated.

## Known limits (stated honestly)
- Word-level provenance is defence in depth, not the main guarantee; the main guarantees are the
  audience marking (`reason.py`/`respond.py`) and the scope checks.
- Fail-closed false refusals remain: `ask()` answers whose place name comes from a claim subject
  (BrainAnswer has no citations), custom relation labels, a shown name taken from an uncited claim.
- `personal_details` on evidence is set by respond() and trusted by shareable().
- Tests: privacy suite 219/219 and 97 deliberate breaks (89 caught, 8 documented equivalents, 0
  unexpected) passed on Windows (Python 3.14.10); Linux whole suite 896 tests, 894 passed, 2
  skipped (Windows-only). A targeted Windows run (key-store Windows-only tests + focused Brain
  tests) is being run separately; the whole-suite Windows run was deliberately not repeated.

## Requested output
A short list of findings ranked by severity, each marked REPRODUCED (with the exact input) or
SUSPECTED, and an explicit statement whether you recommend integration of a79e2ed.
