# Oversight bridge (Claude - ChatGPT - Project Owner)

A written channel, kept in this repository, between the Claude sessions, the
independent reviewer (ChatGPT) and the Project Owner. ChatGPT does not take
part in Claude's live sessions; it reads what is published on GitHub. These
files are how its reviews, Claude's answers and the owner's decisions meet.

## Files

| File | Who writes it | What goes in it |
|---|---|---|
| `CHATGPT_REVIEWS.md` | the Project Owner (pasting ChatGPT's review), or a Claude session copying a review the owner supplied, word for word | independent ChatGPT reviews |
| `CLAUDE_RESPONSES.md` | a Claude session (Session 1 or Session 2) | answers to a specific review |
| `OWNER_DECISIONS.md` | the Project Owner, or a Claude session recording the owner's own words | approvals, rejections, deferrals, scope limits |

All three are **append-only**: new dated entries go at the bottom, earlier
entries are never edited or deleted. A correction is a new entry that names
the entry it corrects.

## Roles

- **Project Owner** - final authority. Decides what is approved, rejected or
  deferred. Brings published work to ChatGPT and brings its reviews back.
- **Claude Session 1 - Lead Developer** - runs day-to-day development and the
  specialist agents (see `../../CLAUDE.md`). Answers reviews of the work it
  leads.
- **Claude Session 2 - Parallel Development Lead** - works on separate,
  approved assignments on its own branch. Maintains this bridge, answers
  reviews of its own work, and prepares summaries for Session 1 and the owner.
  Never integrates its own work and never directs Session 1.
- **ChatGPT - Independent Architect and Oversight** - outside Claude. Reviews
  published commits and records, challenges architecture, security and test
  assumptions, and advises the owner. It does not approve anything.

## Process

1. Claude publishes work on a branch with its records (`../reviews/`,
   `../testing/`, `../../DEVELOPMENT_NOTES.md`).
2. The owner asks ChatGPT to review exact commits.
3. The review is added to `CHATGPT_REVIEWS.md` with an ID (`R-YYYY-MM-DD-N`).
4. The Claude session that owns the reviewed work adds an answer to
   `CLAUDE_RESPONSES.md` that names the review ID, the commits it made and the
   tests it actually ran.
5. The owner records the outcome in `OWNER_DECISIONS.md` (or tells a Claude
   session, which records the owner's words with the date).

## Rules

- **Never invent a review.** A Claude session never writes a ChatGPT review,
  summarises one it did not receive, or says ChatGPT approved something.
- **A recommendation is not approval.** Only an entry in
  `OWNER_DECISIONS.md` that quotes or references the owner counts as
  approval. Silence, a positive ChatGPT review, or a passing test run is
  never approval.
- **Label every entry with its author**: `Project Owner`, `ChatGPT (supplied
  by the owner)`, `Claude Session 1 - Lead Developer`, or `Claude Session 2 -
  Parallel Development Lead`. Claude subagents do not write here; their
  reports stay under `../reviews/`.
- **Separate fact from claim.** "Verified" means checked against the
  repository (file, commit, test output). Anything else is reported as a
  claim or an assumption.
- Exact commit SHAs, never "the latest commit". Linux and Windows results are
  reported separately; Windows only from a run on Windows.
- No secrets, keys, passwords or real personal information in any entry.
