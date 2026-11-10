# B7 Personal Memory - architecture proposal (documentation only)

Status: PROPOSAL. Nothing is built, nothing is approved. No B7 code exists. This replaces
proposal v2; it explains the twelve open decisions in plain language.

All existing Brain rules stay as they are: personal and shared knowledge are physically
separate; a person's own verification never verifies shared knowledge; nothing is shared or
promoted automatically; the Brain makes no AI or network call and works without any provider;
every change is a recorded event (decision 2 asks to make ONE deliberate exception).

## What B7 is for

Today SK can answer from what a person has taught it. B7 adds, in small steps:
remembering the conversation (context), saving things the person typed on purpose,
deleting personal data for real, backing up and restoring a person's personal memory, and
(optionally, last) a history and an export.

## The split (decision 12)

| Step | What it adds | Depends on |
|---|---|---|
| B6.1 prerequisites | audience fix (done), key-store hardening (done), one file-access layer, refuse libraries written by a newer format, secure deletion for the claim search index | your approval of decision 7 |
| B7a | conversation context, bookmarks, confirmed saving, real deletion | decisions 1, 2, 6, 8, 9 |
| B7b | backups, deletion records that survive a restore, restore | decisions 3, 4, 5 |
| B7c | history and export (optional, last, most leak surface) | decisions 10, 11 |

Each step is its own branch from `claude/brain-integration`, with the normal team workflow,
regression manifest and a Windows run. Nothing starts without your approval.

---

## The twelve decisions

For each: what it means, my recommendation, why, what it does for privacy, and what follows.

### 1. How should a person's own statements answer? (ESSENTIAL before B7a - a truth-rule decision)
**Plain question.** If you tell SK "I parked on level 3" and ask later "Where did I park?",
today's rule answers "Unknown, possibly level 3", because a statement with no source is never
treated as established. Options: (a) keep that; (b) add a new answer type, "You told me: level 3";
(c) let people be the authority on facts about themselves (preferences, their own things) but
never on world facts.
**Recommendation: (b), with (c) as a later refinement.** A "You told me" category keeps the
truth rule intact (it is not "Established", not verified) but is honest and useful.
**Why.** (a) makes personal memory feel broken. (c) needs a reliable way to tell "about me"
from "about the world", which is hard and could wrongly promote a mistaken world fact.
**Privacy.** "You told me" answers are always audience personal; they can never become shared
or verify a shared fact. A personal statement that contradicts a shared Established fact does
NOT change the Established answer; it appears alongside it, labelled as the person's own.
**Consequence.** New response category and wording; the truth rules (what "Established" means)
do not change. Needs your explicit approval because it touches the truth rule.

### 2. Allow real deletion of personal data? (ESSENTIAL before B7a)
**Plain question.** Today "nothing is deleted; every change is an event". People must be able to
make SK forget something truly (including old versions and events). Allow it?
**Recommendation: yes, for personal data only, never for shared data.**
**Why.** Privacy law and ordinary trust: "delete" has to mean delete. Without it the product
cannot responsibly become multi-user.
**Privacy.** Deletion covers every place the text lives (claims, versions, events, links, evidence,
search index and its tokens, suggestions, aliases, history, pending proposals). Pending
proposals to share it are withdrawn automatically. Already-approved shared copies are a
separate matter (decision 6).
**Consequence.** The "every change is an event" rule gets one explicit, recorded exception.
Deleted items leave only an id-only tombstone (no text).

### 3. How to keep backups clean after a deletion? (ESSENTIAL before B7b)
**Plain question.** A backup made yesterday still holds what you deleted today. Either scrub
every backup, or replace backups.
**Recommendation: replace-on-delete.** After a deletion SK takes a fresh verified backup and
removes the older ones (keep 2-3 generations).
**Why.** Scrubbing copies of a person's data is the most code and the most ways to lose data.
Replacing is simple and verifiable.
**Privacy.** After a deletion no older backup containing the deleted item remains (except as
decision 4 describes). The outbox (proposed shares) is not backed up.
**Consequence.** Fewer old restore points after a deletion.

### 4. What if an old backup cannot be removed (file locked)? (ESSENTIAL before B7b)
**Recommendation: keep it, tell the person it is pending, retry at every start; after N failed
tries, replace it as soon as a fresh verified backup exists.** Keeping it forever would silently
undo a deletion; removing it before a good new backup exists could lose the only copy.
**Privacy.** The person is told honestly which backups still hold deleted data.
**Consequence.** A small "pending clean-up" status the interface can show.

### 5. The big upgrade copies made when the library format changed (before B7b)
**Plain question.** The B6 upgrade left whole-library copies (`pre-b6`, `brain-pre-b6`); B7 would
add `pre-b7`. They hold everything, including things later deleted, and are kept until you remove
them.
**Recommendation: keep them for a verified period, then offer you a one-click release; tell the
person plainly while they exist.** Never delete them automatically.
**Privacy.** These copies are outside deletion's reach until released; saying so is part of
being honest.
**Consequence.** One backup module with kinds (upgrade, daily), each with its own retention.

### 6. Can a person withdraw text that a moderator already approved as shared? (ESSENTIAL before B7a)
**Plain question.** Today an approved shared copy cannot be withdrawn. Add withdrawal, or state
the limit when someone confirms a share?
**Recommendation: state the limit clearly at confirm time in B7a; design withdrawal as its own
reviewed change later.** Withdrawal touches shared.db, which users never write.
**Privacy.** Nobody is surprised: the person is told "once approved, this becomes shared".
**Consequence.** B7a stays out of shared.db.

### 7. Minimum SQLite / Python versions (ESSENTIAL before the B6.1 search-index step)
**Plain question.** Secure deletion in the search index needs SQLite 3.42 or newer. Windows has
only ever run Python 3.14 here.
**Recommendation: require what 3.14 ships, test that, and refuse to run deletion on anything
older.** Do not promise support for older Pythons that were never tested.
**Privacy.** Deletion that cannot be made secure is refused, not faked.
**Consequence.** A documented minimum.

### 8. One SK process per library, or several at once? (ESSENTIAL before B7a)
**Recommendation: one process per library, enforced by a lock.** Restore and deletion must not
race with another open session; on Windows a file in use cannot be replaced.
**Privacy.** Prevents a half-deleted or half-restored library.
**Consequence.** A second SK on the same library is refused with a clear message.

### 9. The typed phrase for export, delete-everything and restore (before B7a/B7b)
**Plain question.** These acts ask the person to type a phrase. It is a deliberate-act check
(prevents an accidental click), not a password: the host program can always supply it.
**Recommendation: accept that, and say so in the interface and docs.**
**Why.** Real authentication is the host's job (and a later multi-user step).
**Consequence.** No false sense of security.

### 10. Export: history only, or all personal memory? (before B7c)
**Recommendation: all personal memory, as a file the host saves** (Brain returns the bytes and
never writes a path itself, so it cannot overwrite shared.db or another person's file).
**Privacy.** The export is the person's own data; it is never shared automatically.

### 11. History default OFF (before B7c)
**Recommendation: confirm OFF.** History stores the question, category, claim ids and time -
never answer text - so a withdrawn shared answer does not live on in someone's history.

### 12. Approve the split and the order (ESSENTIAL first)
**Recommendation: yes - B6.1 first (its privacy part is already done and waiting for you),
then B7a, B7b, B7c.** Smaller steps each get an audit and a Windows run.

---

## What must be decided before any B7 code

Before B6.1's search-index step: **7**. Before B7a: **1, 2, 6, 8, 9, 12**. Before B7b: **3, 4, 5**.
Before B7c: **10, 11**. If you approve only the essentials (1, 2, 6, 7, 8, 12), B7a can start;
the rest can wait for their steps.

## Limits stated plainly
Backups are plain copies (no encryption at rest; that would need a vetted crypto dependency and
your approval). Remnants on SSDs, Windows shadow copies and roaming-profile copies are outside
SK's reach. Shared copies already approved by a moderator stay unless withdrawn. Windows
behaviour (renaming over open files, antivirus locks, long paths, DPAPI after a password reset)
is verified only by running on your PC.
