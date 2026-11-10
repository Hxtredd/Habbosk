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
| B7a | conversation context, bookmarks, confirmed saving, real deletion | decisions 1, 2, 6 (the limit wording), 8, 9 |
| B7b | backups, deletion records that survive a restore, restore | decisions 3, 4, 5 |
| B7d | withdrawal of approved shared text (optional) | decision 6's design approved |
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
**Recommendation: (b) now; (c) only later, and only as its own approved change.**
**What (b) means exactly.**
- A fifth category, "You told me", used only when the answer rests solely on the asker's OWN
  personal claims. It is never "Established" and never "Documented"; the wording always says
  whose statement it is ("You told me on 3 March: level 3").
- It is always audience personal. `Response.shareable()` refuses it, so it can never be passed on.
- It cannot verify anything, cannot feed a shared answer, and a person's personal "verified"
  stays personal (existing rule).
- If your statement contradicts a shared Established fact, the shared answer is unchanged and
  yours is shown beside it, labelled ("Shared knowledge says X; you told me Y").
- If the asker has several conflicting personal statements, SK shows them and does not choose.
**Why not (a).** Personal memory would feel broken: SK would "know" and yet say "Unknown".
**Why not (c) now.** It needs a reliable test for "this is about me, not the world". A wrong
test could let a mistaken world fact become authoritative for one person and look like truth.
**Privacy.** No new exposure: the category is personal-only and blocked at the shareable()
boundary (tests already pin that a personal response cannot be prepared for sharing).
**Consequences.** One new category and its wording (English and Spanish), a rule in
`respond.py`, and tests for every category-boundary case. Existing categories and the meaning
of "Established" do not change - but because it touches the truth rules, it is yours to approve.
**What I need from you:** yes / no on (b); keep (c) out of scope.

### 2. Allow real deletion of personal data? (ESSENTIAL before B7a)
**Plain question.** Today "nothing is deleted; every change is an event". People must be able to
make SK truly forget something. Allow it?
**Recommendation: yes - for a person's PERSONAL data only, never for shared data, with a
verifiable checklist.**
**What "complete" means (the checklist the code and its tests must prove).** After deleting a
personal claim, none of its text, in any form, remains in the person's personal file: the claim,
its versions and events, links to other claims, evidence rows and quoted passages that only that
claim used, the search index and its word lists (claim index secure-delete), suggestions and
aliases made from it, history rows, and pending share proposals (withdrawn automatically).
Verification is by scanning the raw database bytes (including free pages and the write-ahead
log) for the deleted text and its normalised and word-split forms - a test that must find nothing.
**What stays:** an id-only tombstone (id, kind, time - no text) so a restored backup cannot
quietly bring the item back (see decision 3), and the event "something was deleted".
**Privacy.** Real "forget". The honest limits are stated to the person: older backups until
replaced (decision 3), copies on disk the operating system keeps (SSD remnants, shadow copies),
and anything already approved as shared (decision 6).
**Consequences.** The rule "every change is an event" gets ONE explicit exception - the content,
not the fact of deletion. This changes a Brain rule, so it needs your approval.

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

### 6. Can a person withdraw text a moderator already approved as shared? (ESSENTIAL before B7a)
**Plain question.** An approved shared copy lives in `shared.db`, which users never write.
Today it cannot be withdrawn. Is permanent non-withdrawal the only safe design? **No - a safe
withdrawal can be designed, and the Brain already has the building blocks (B5.2 withdrawal of
a source, B6 purge-pending).**
**Proposed design (for later approval, not built).**
1. **Who:** only the contributor who proposed it, or a moderator (for content problems). Nobody
   else, and never silently.
2. **How:** withdrawal is a *request* through the same moderated channel as sharing (outbox ->
   moderator action). Users still never write `shared.db`.
3. **What happens:** the shared claim becomes "withdrawn" (an event with id, time, reason code -
   no text). Its text, versions and quoted passages in `shared.db` are purged: immediately if no
   reader holds an open snapshot, otherwise marked purge-pending and purged when the last reader
   closes (the existing B5.2 mechanism). The search index uses secure-delete.
4. **Effect on answers:** anything that depended on it stops being Established; answers fall back
   to "evidence withdrawn / unknown" wording the Brain already has. Other people's personal claims
   are not touched.
5. **Limits stated plainly:** nothing can recall what someone already read, copied, screenshotted
   or exported; installation backups made before the purge keep the old text until replaced
   (decision 3 applies to shared.db backups too); a restored old backup must replay the
   withdrawal record (tombstones, decision 3).
**Recommendation.** B7a states the limit at confirm time ("once approved, this becomes shared; it
can be withdrawn later but copies already seen cannot be recalled" - truthful only once the
design exists; until then: "it cannot be withdrawn yet"). Build withdrawal as its own step
(B7d) after B7a/B7b, with its own audit and Windows run.
**Why.** It changes who may alter shared knowledge and what moderators can destroy - a
privacy-policy decision that needs your approval, and it touches `shared.db`, the most protected
file. Rushing it into B7a would mix it with the personal-memory work.
**What I changed my mind about.** My earlier text said "state the limit, design later". With the
B5.2 precedent in view I now think a safe withdrawal is realistic, so I recommend committing to it
as a planned step rather than leaving "permanent" as the default.

### 7. Minimum SQLite / Python versions (ESSENTIAL before the B6.1 search-index step)
**Plain question.** Real deletion from the claim search index needs SQLite's FTS5 secure-delete
option (SQLite 3.42 or newer). The README still says "Python 3.9 - 3.13", but all Windows Brain
checks have run on Python 3.14 (the Windows check already prints the SQLite version).
**Recommendation.** (1) Decide by what is actually present, not by the Python label: at start,
test `sqlite3.sqlite_version` (and FTS5) and refuse the deletion features - with a plain message
- if the SQLite is too old; the rest of the Brain keeps working. (2) State the TESTED floor as
Python 3.14 on Windows until older versions have been run on Windows; update the README to say
so. (3) If you want older Pythons supported, a Windows run on the oldest one is needed first. I
have not verified which SQLite versions older Windows Pythons ship; I will check before
claiming any.
**Privacy.** Deletion that cannot be made secure is refused, never faked.
**Consequences.** A documented floor; a small start-up check; no new dependency.

### 8. One SK process per library, or several at once? (ESSENTIAL before B7a)
**Plain question.** Restore, deletion and backup rewrite the person's file. Two SK programs open
on the same library could write at once.
**Recommendation: one process per library, enforced by a lock; a second one is refused with a
clear message.** (A per-person exclusive lock inside it for restore/deletion/backup.)
**Why.** On Windows a file in use cannot be replaced; on Linux the other process would silently
keep writing to the removed file and lose data. Multi-process support is a large separate project.
**Privacy.** Prevents a half-deleted or half-restored library - the worst case for "forget".
**Consequences.** If SK crashes it must release the lock safely (stale-lock detection by process
id and start time), which the tests will cover. Multi-user on one machine later means one SK
process serving several people, not several processes.

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
**Recommendation: yes. Order: finish and approve the privacy work (B6.2, waiting for your Windows
run), then B6.1-remaining (file layer, forward-format refusal, search-index secure delete), then
B7a (context, bookmarks, confirmed saving, deletion), B7b (backups, restore), B7d (withdrawal of
approved shared text, if you approve decision 6's design), B7c (history/export, last).**
**Why this order.** Each step is small enough to audit and run on Windows; deletion (B7a) must
exist before backups can be made deletion-aware (B7b); withdrawal touches the protected file so it
comes after personal memory is solid; history/export has the most leak surface so it is last.
**What you decide:** the order and that nothing starts without your approval of its decisions.

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
