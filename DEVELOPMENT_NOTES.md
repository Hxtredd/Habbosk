# SK Assistant — development notes

Hand-over notes for anyone (or any future session) continuing this project.
Read this first, then `README.txt` (user documentation) and the tests.

## What it is

**SK Assistant** (formerly *Puzzle Answer Assistant*, briefly *Habbosk*) is a
Windows desktop helper for the Habbo retro hotel **habbosk.us/play**. It reads
the screen, recognises puzzles and quiz questions, and **shows** the answer in a
small floating window and as marks drawn on top of the game.

The intended users include people with intellectual disabilities and learning
difficulties: keep the interface simple, plain-worded and calm.

## Hard rules (agreed with the owner — do not change without asking)

1. **Read-only for puzzles and questions.** Never click, move the mouse,
   type, or interact with the browser/game for them. Single exception agreed
   with the owner: World Atlas "Go to Room" types `:taxi <id>` in the hotel
   chat only on the owner's click (one command per click). The player does every action. The owner later asked about
   an "automated" option; it was **declined** (it would be a bot earning in-game
   currency, against the server's rules and other players). Offer accessibility
   help instead (bigger marks, spoken cues, high contrast).
2. **Never present a guess as certain.** A wrong click costs the player a point.
   When unsure, show "Not sure / check X" — never a clickable-looking answer.
3. **Ask before building.** The owner often asks questions first ("don't build
   anything yet"). Only change code when told to go ahead.
4. Keep the puzzle features working when adding anything. Run all tests.
5. No API keys or personal data in the repository (`data/` and `config.json`
   are git-ignored).

## Owner preferences

- Name: **SK Assistant**. Folder/package names stay `PuzzleAnswerAssistant` /
  `puzzle_assistant` (renaming files is avoided on purpose).
- Two monitors. Spanish-language game; mostly Spanish questions.
- Modern dark look (Inter font, anti-aliased badges, chips, status pills).
- Wants low running costs: memory first, built-in solvers, free AI tiers;
  paid AI only if explicitly enabled with a monthly cap.
- Each update gets the next version (v2.7, v2.8, ...): bump
  `puzzle_assistant/__init__.py` and the README title, then run
  `python tools/publish_release.py "notes"` - it builds
  `dist/SK_Assistant_v<x.y>.zip` (top folder "SK Assistant") and
  `latest.json` (size + SHA-256). Commit and push both: the in-app updater
  (`updater.py`, Settings > Updates) reads latest.json from the public repo
  (raw.githubusercontent.com, this branch) and installs in place on click.
  Data lives in `%APPDATA%\SK Assistant`; updates keep `.venv`, config,
  debug; replaced files go to `_previous\`.

## Game rules learned from the owner (confirmed)

**Weapon puzzle "Montar un arma"** (7 or 8 cards A–H)
- Card shows letter badge (teal), name, caliber (yellow; "–" = no caliber),
  and `↳ B H` = pieces that must be mounted **before** this one.
- Pieces of another caliber: **leave them alone** (no click).
- "–" pieces are mounted.
- Clicking a piece too early → error, loses a point.
- When several pieces are possible, **any order is accepted**.
- Mounted pieces turn green with a ✓. Finish with **Probar**.

**Money puzzle "Cuenta el dinero"**
- Coins/bills slide left→right; the answer is the **sum of all that pass**.
- "Vistos N/M" counts an item **when it appears**. Legend line lists the
  possible values (e.g. `5 · 10 · 25`). Coins of different values share a
  colour — the printed number is what matters.

**Questions "Estudiar en la biblioteca"** (4 questions/round, ~150 s)
- Yellow category label: ADIVINANZA, PREGUNTA, ENCUENTRA EL ERROR,
  ¿QUIÉN SOY?, ORDENAR (tap in order, 4–5 items), PAREJAS (match pairs,
  max 3 mistakes).
- After answering the game **always reveals** the correct answer: green ✓
  option (red ✕ = wrong pick), ORDENAR numbers in badges, PAREJAS pairs by
  border colour, plus an explanation box ("No era esa…" / "¡Bien!…").
- Hints ("Ver una pista", "50/50") add bullet lines (• …) under the question;
  using a hint gives fewer points.
- Many questions are about the game world (NPCs, casino, prison, gym,
  fishing) → only learnable from the game itself; questions **repeat**.
- **Preguntas express** = the timed general-knowledge task (15 questions,
  ~20 s each): header "Preguntas express / Pregunta n de 15", question,
  2x2 answers with badges A-D, countdown top right, "Lee la pregunta… n"
  line and a leaderboard below. `locate_express` (2x2 boxes, missing one
  inferred, header OCR cached). How the game reveals the right answer
  there is not recorded yet (assumed green like the library).
- Bug fixed (v2.3): the job panel ("Armería · Prescott", task rows) was
  read as a question and answered by the AI; now a known quiz category is
  required, and rejected panels are not re-read until they change.
- v2.4: a rejected panel also counts as "nothing on screen" (the job panel
  kept the engine 'active', so weapon marks stayed after the puzzle closed);
  the Explorer window is always-on-top (like the main window) and hidden
  from capture.
- v2.5: the Explorer is part of the SK window (owner's request): tab bar
  "Assistant | Explorer", ⧉ menu to dock it right/left/top/bottom
  (PanedWindow, resizable) or detach it as a resizable window; config
  `explorer_dock`, `explorer_size`, `explorer_geometry`. The window grows
  for the Explorer and returns to its size afterwards.
- v2.6 (owner: "slower during everything"): measured per check: Explorer
  read ~160 ms -> ~10 ms when unchanged (name OCR cached by image hash,
  share-panel URL checked at its last position, full search only after a
  change / every 8 s); Explorer motion check 3x/s, full read every 2 s or
  on motion, paused while `engine.busy`; express check ~1x/s (63 ms).
- v2.6: rooms identified by NAME (owner can't always show the ID):
  `Atlas.find_by_name` (exact, else one clearly closest >= 0.9); unknown or
  shared names -> name-only pending entry ("Add ID"); a visible ID wins.
- v2.6: `Atlas.facts_for(text)` gives the AI map/room facts (nearest map
  places, walking routes) for questions naming places.
- v2.6: `scrollframe.ScrollFrame` makes Assistant and Explorer scroll.
- v2.8: both parts can be closed / reopened (`show_assistant`,
  `show_explorer`; one stays open); `App._layout()` rebuilds for what is
  open + `explorer_dock`; the window takes the size the content needs and
  returns to the assistant's own size (`_base_geom`, saved only while the
  assistant alone was shown).

## Architecture (puzzle_assistant/)

| Module | Role |
|---|---|
| `main.py` | entry point, DPI awareness, single instance, `--image` offline mode |
| `ui.py`, `settings_ui.py`, `render.py` | Tk window, settings panel, Pillow-drawn chips/badges |
| `overlay.py` | click-through, capture-excluded marks on the game (Windows `UpdateLayeredWindow`) |
| `engine.py` | background loop: grab every monitor → weapon / money / question detection |
| `capture.py` | mss screen capture, all monitors, display diagnostics |
| `locate.py`, `cardreader.py`, `glyphs.py`, `vision.py` | weapon puzzle: card grid detection, glyph-template reading (5×7 px text) |
| `solver.py`, `parser.py`, `analyzer.py` | weapon puzzle: topological sort, header OCR |
| `money.py` | money puzzle: strip tracking, item reading, Vistos/legend checks |
| `qa/screen.py` | question window: locate, OCR, revealed results |
| `qa/memory.py` | SQLite memory (`%APPDATA%\SK Assistant\habbosk.db`), matching, source priority, history, merge/import |
| `qa/ai.py` | AI providers over urllib (Gemini/Claude/OpenAI), model auto-choice, cost limits, web search |
| `storage.py` | first-start migration of the old `data\` folder, starter questions (`assets/seed_questions.json`) |
| `qa/model.py`, `qa/text.py` | Question/Answer model, normalisation |
| `qa/solvers.py` | built-in solvers (safe arithmetic parser, sequence rules); answer only if exactly one option fits, refuse when two readings fit |
| `secrets.py` | API keys encrypted with Windows DPAPI (`keys.bin` in the data folder) |

Key ideas: detect windows by **layout** (grids of boxes) not by OCR; tiny text
via **glyph templates** (`glyph_templates.npz`, built by
`tools/build_glyph_templates.py`); OCR (local Tesseract, `tessdata/spa`) only
for larger text; answers are stored by **option text**, never position;
source priority **game > user > solver > ai**.

## Stage plan for the question assistant

| Stage | Content | Status |
|---|---|---|
| 1 | Unified window, settings panel, SQLite memory, key storage | done (v1.7) |
| 2 | Read the question window; learn from the game; answer from memory; marks | done (v1.8) |
| 3 | Built-in solvers: ORDENAR sums, letter/number sequences, calculations, wrong equations, simple word problems ("likely" only) | done (v1.9) |
| 4 | Memory in %APPDATA%\\SK Assistant (+ merge of old memories, Import, 12 starter questions); AI providers (Gemini first) + optional web search, cost controls, "AI · unverified" label | done (v2.0) — not yet tested against the real Gemini API from the dev container |
| 5 | Manual corrections, history, statistics in the UI | todo |
| 6 | Timed general-knowledge task (needs a recording), hints, packaging | todo |

## Testing

- `python -m unittest discover -s tests` — everything (needs Tesseract for the
  OCR tests; others run without it).
- Real fixtures (crops of the game windows only, no chat/usernames):
  `tests/fixtures/*.png` (weapon), `money_*.npz` (two recorded money rounds:
  coins = 125, bills = 390 accepted by the game), `fixtures/questions/` (14
  question screens).
- Live end-to-end checks were done by replaying the owner's recordings on a
  virtual X display (Xvfb) and running the real engine against it; screen
  recordings include the mouse pointer, which adds OCR noise that live
  captures don't have.
- Windows-only parts (overlay window, DPAPI, shortcuts, .bat files) cannot be
  run in the Linux dev container — be explicit with the owner about that.

Solver policy: certain answers have confidence 1.0 ("Solved", green mark on
the game); word problems 0.7 ("Probably", amber, no mark). Each solver answer
is compared with what the game reveals (`solver_check` in the history; shown
in Settings > Memory). The owner has not seen other "work it out" question
types so far.

AI policy (Stage 4): asked only after memory and solvers fail, in a
background thread (one worker); reply must be JSON naming option letters,
otherwise discarded. Confidence ≥ 0.6 → "AI · unverified" (amber, no overlay
mark), lower → "Not sure". Never saved as truth; scored against the reveal
(`ai_check` in history). Gemini model auto-picked from `GET /v1beta/models`
(newest stable flash-lite, cached in the `meta` table, re-picked on 404).
Key in header `x-goog-api-key`, never in the URL. Paid providers need
`paid_api` + `monthly_limit`; free use capped by `ai_daily_limit`.
Stage 4.2: `Memory.related(q)` picks up to 5 game-verified entries sharing
topic words (`_topic_words`) and `build_prompt` sends them as "facts
confirmed by this game"; still one request per question.
Data folder override for tests: env `SK_ASSISTANT_DATA`.

Sums on option buttons (`qa/mathread.py`): Tesseract misread ÷ (as '+',
'5' or nothing) and dropped a 0 next to ×, and the solver then showed a
wrong order as "Solved". Now: operators by shape (÷ dot/bar/dot, − flat
bar, × vs + by corner ink), numbers by Tesseract digits-only with a digit
count check; `Screen.text_unsure` blocks the solvers when a number-like
option can't be read exactly; "cuentas" questions need a sign in every
option. Old memory entries with possibly misread sums are forgotten once
(`math_fix_v1`) and skipped on Import (`suspect_sum`).
Text size: named Tk fonts resized in place (`tk scaling` did not redraw on
Windows); the window grows when the bigger text no longer fits.

Question marks: green for memory/solver/AI-sure, yellow ("doubt") for
AI-unsure and "likely" (owner's request); AI marks carry "AI". ORDENAR
progress = "Toca el paso N" (OCR under the options, also in the
fingerprint) -> engine emits `question_progress`. Not yet checked against a
real mid-ordering screenshot (fallback: count of numbered badges).

## World Atlas (v2.3, stage 1 of the owner's World Atlas spec)

Agreed with the owner (2026-11-07):
- Read-only recognition and recording. **Go to Room** (typing `:taxi <id>`
  in the hotel chat) is allowed later, but only one trip per click of the
  owner (hotel rules allow helpers that are not fully autonomous; the
  hotel has its own macro). **No autonomous exploration** - declined.
- Note: the job room "Convocatoria de Trabajo" (46) lists "No usar
  autoclick/type" among its job rules - mention when building Go to Room.

Calibration (all from the owner's screenshots/recordings, `atlas_input/`):
| Item | Method |
|---|---|
| Room name | bar above the chat: yellow pin + name (`find_name_bar`) |
| Room ID | `habbosk.us/room/<id>` in the share panel (any position; it jumps to the middle after a change; the owner drags it back). Read twice at two zooms, must agree |
| Walk | black flash + grey loading (~1 s): big change -> flat screen -> big change within 2.5 s (flatness alone is not enough: some rooms are as flat) |
| Taxi | instant cut; chat "*Pide un Taxi VIP para ir a <name>*", "*Llegó a su destino*"; errors "[Taxi SK] No pudimos encontrar ese lugar.", "A ese lugar solo se llega caminando…"; ~3 s between trips; free |
| Arrows | "Flecha de Suelo" items: pairs of small white floor arrows at doors (stage 3) |
| Map | 53 markers named from hover labels; 52 linked to room IDs; Cementerio has no room |
| Mine | entered from Avenida Crisol (23); levels have generated IDs (1900003897…); blocked in passive mode |

Go to Room (stage 2, v2.9, `world_atlas/navigate.py`): owner's click ->
check cooldown (3.5 s) and room view -> chat input = pink chat icon below
the name bar -> one click into it -> must turn gold (red-blue >= 12 on its
border rows; measured in the taxi recording) or nothing is typed ->
SendInput unicode ":taxi <id>" + Enter -> mouse restored -> wait 10 s for
the tracker to report that room (ID or name). This is the ONLY place SK
sends input; the README's read-only section says so.

Stage plan: 1 = Atlas DB + Live Explorer (done, v2.3); 2 = Go to Room (done, v2.9);
3 = arrow detection + coloured overlay + which exit was used; then
exploration queue, connection graph.

## SK Brain (stage B1 done, not released)

`puzzle_assistant/brain/` - UI-free knowledge foundation (no Tkinter; the app
runs it in its own threads). `knowledge.db` (in the data folder): contributors,
sources (with content hash + private flag), sections (page context), claims
(origin, status, scope, version), evidence (direct / confirmation / derived /
assertion), claim_links (supports, contradicts, derived_from, supersedes,
duplicate_of), claim_versions, claim_events, meta (schema_version, local_user).
Rules in `store.py`: new claims are submitted + personal; only `review()` sets
verified and only with direct/confirmation evidence (same material counts
once, assertions never); corrections add versions; contradictions flag both;
private evidence blocks sharing. Quiz memory and World Atlas are read through
`adapters.py` (SQLite `mode=ro`, never created/modified), statuses mapped.
B1 hardening (schema v2, auto-migrated from v1): `claims.owner_id`,
`claim_versions.old_status`. Isolation: every read takes `viewer`; personal
and shared-candidate claims are visible only to their owner (candidates also
to moderators), only `shared` to everyone; stats per viewer. Others cannot
add evidence / review / correct / link / share someone's personal claim.
Sharing: owner proposes -> moderator verifies -> moderator `approve_share`
(verified only, no private evidence). Shared claims are corrected through
`propose_correction` (new claim, `supersedes` link; original unchanged).
No automatic verification (system/importer cannot verify, except SK's own
direct game observation of an `observed` claim); open contradictions block
verification; derived evidence needs a `derived_from` link; evidence sources
must exist. `Hit.established` (verified only) and `Hit.display()` (labels
unverified/conflicting/outdated) - nothing unverified is shown as fact.
`Brain` API: learn, add_source, add_section, add_evidence, link, review,
correct, propose_share, claim, explain, search (B1: word overlap), ask (B1:
reports hits, never answers). Not wired into the app yet.
TEMPORARY test bench (dev tool, removable): `BRAIN_TEST_BENCH.bat` /
`python -m puzzle_assistant.devtools.brain_bench` - tabs Ask (Brain.ask /
search, why + source + evidence per result, "ask as" a test contributor),
Add material (add_source + one whole section, raw text kept), Claims (learn
+ evidence + review / contradiction / correct, Brain rules apply), Checks
(`devtools/brain_checks.py`: scenario checks on a throwaway DB + read-only
check of the real quiz/Atlas files). Not the chat.
Safety (`devtools/bench_safety.py`): at launch choose Sandbox (disposable
copy via SQLite backup API, deleted on close; test contributors only here),
Real read-only (only if knowledge.db exists at the current schema, so
opening writes nothing; WriteGuard refuses writes) or Real with changes
(confirmed twice; `knowledge.db.backup-YYYYMMDD-HHMMSS[-N]` made with the
backup API and verified BEFORE the Brain opens it / before the first write;
never overwrites; a failed backup disables all writes). Coloured banner per
mode; write buttons marked ✎ and disabled when writes aren't allowed.
B2 retrieval (done, not released): `brain/text.py` (normalise, light es/en
stemming, stop words, numbers + 'id' kept), `lexicon.py` (curated synonyms,
weight 0.7), `query.py` (intent: identifier / route / person / state; known
subject names matched as whole names, partial -> ambiguous), `retrieve.py`
(IDF from the viewer's visible claims only; subject and intent factors;
small verified bonus; outdated last; rejected only with include_rejected;
duplicates merged with no extra weight; conflicting claims shown with their
visible counterparts, adjacent, beyond the limit; Hit.detail["why"]).
Schema v3: `claim_index` (+ `claim_fts` FTS5 if available) - derived data
rebuilt from claims; FTS5 only pre-selects candidates, scoring identical for
the fallback. Quiz memory / Atlas results only for the local user.
`Brain.search()` same call (+ include_rejected, use_fts); `search_b1()` kept.
Speed: 0.3 ms (6 claims), 2 ms (200), 8 ms (1,000), 30-180 ms (10,000).
Regression set = the owner's exact five Demo Garden claims (source
SK-BRAIN-TEST-001), tested with and without subjects.
B2 corrections (owner review): negation kept as polarity (text.polarity:
not/no/never/nunca/jamás/contractions; opposite statements never match);
every question requirement must be met (partial fit noted, never a match);
named (capitalised) phrases in a question are required subjects even when
unknown; "about" != "mentions" (subject field, else first name; route
questions accept "... to <place>"); links() filters both ends, events about
invisible claims are dropped (claim_events.related_claim, v3 migration),
a contradiction only flags a claim whose whole audience can see the other
side; stats() counts only the viewer's own sources. Speed (10,005 claims,
goal 50 ms): FTS5 typical ~17 ms, no-match ~4 ms, worst case ~170 ms;
fallback 57-185 ms.
Next (each needs approval): B3 reasoning, B4 corrections workflow, B5 importer, B6 test bench.

B2 final validation (not released). Fixed, each with a regression test that
was shown to fail without the fix:
- read-only `file:` URIs are escaped (`brain/sqlite_ro.py`): a folder name
  with `#`/`?`/`%` used to drop `?mode=ro`, create a stray empty file, read
  0 rows and make the bench "backup" an empty database;
- FTS5 failing at open, in search or in a write falls back to the plain
  search (same results); writes made without FTS5 set `meta.fts_stale` so
  the next start with FTS5 rebuilds it;
- writes are all-or-nothing (`@_atomic`): a write failing part-way (file
  locked by another program at commit, rule check after a nested write) is
  rolled back instead of being committed by the next write, and no longer
  keeps other programs locked out; a failed open closes the file;
- permissions: someone else's private source/section can't be cited (same
  refusal as a missing one); only a source's owner adds sections; evidence
  and links: personal -> owner, proposed -> owner or moderator, shared ->
  moderators only (regular users: propose_correction);
- retrieval: a direction in the question must be matched; a record matching
  only the name isn't presented as the answer; "connect"/"goes to" are route
  questions; a negative record answers an affirmative yes/no question;
  Spanish frame words (llego/llegar/voy/puedo) are stop words like get/go/can;
  stemming drops a final -e (close/closes/closed meet); INDEX_VERSION 4.
Evaluation: `python tests/brain_eval.py` (fictional town SK-BRAIN-EVAL-001,
31 claims; main + held-out question sets). Windows: `BRAIN_WINDOWS_CHECK.bat`
(temp folder only; real data files opened read-only, hash-compared).
Documented, not changed (need approval / architecture): sequential IDs reveal
how many claims exist; `viewer` is trusted (no authentication yet, None =
local user); a moderator can verify and approve their own claim; store
helpers source()/evidence()/evidence_summary() have no viewer check (only
called behind checked paths); backups and the sandbox temp copy contain all
contributors' data; `qa/memory.py` merge_from builds the same unescaped
read-only URI (main app, untouched).
Stage 1 (engine processing identity) is parked as an unreleased WIP commit.

B2 validation, round 2 (not released). Fixed, each with a regression test
shown to fail without it: a claim's personal-era history (old wordings,
correction notes, evidence events) is no longer shown to others after it is
shared - others see history from `share_proposed` on; `approve_share` needs a
moderator's `verified` review AFTER the proposal (the owner's own
verification no longer reaches the shared library); search (and conflict
partners) shows only shared + own claims, also for moderators (proposals are
reviewed through claim()/claims()/explain(), never found by search); evidence
from a `user_statement` source counts as an assertion; FTS5 failure in word
counts falls back; a knowledge.db from a NEWER version is refused unchanged.
Retrieval: the sentence's first word alone is not a name ("Cuántas",
"Room"); lowercase one-word subjects ("trading") are topics, not required
subjects; a partial name of several known names ("the Faro") is ambiguous,
offered both ways, never resolved, other places ranked below.
Evaluation set 2: `python tests/brain_eval_city.py` (Ciudad Coral,
SK-BRAIN-EVAL-002: 37 claims, 4 users, 47 questions, status audit and a
privacy sweep of every read path).

Multi-user readiness (assessment, nothing built). The data model already
has: owner per claim, personal / shared_candidate / shared scopes, viewer
filtering in every read (in SQL for lists and search), moderator-only review
and approval of shared knowledge, evidence rules, append-only history.
Must change before real multi-user use:
1. Authentication: `viewer` / `by` are trusted arguments and default to the
   local user. A server must take the identity from the session, make it
   mandatory, and never accept it from the client.
2. Contributors / roles: `add_contributor(kind="moderator")` lets the caller
   create moderators. Roles need an admin-only path, an audit trail, and
   revocation (a demoted moderator's past approvals stay recorded).
3. One database per installation today. A server needs a real multi-writer
   database (or one file per user + a shared library), backups that respect
   per-user deletion, and retention rules.
4. Opaque IDs: sequential claim/source IDs reveal how many exist; use random
   public IDs (keep integers internally).
5. Two-person rule (policy decision): a moderator can still propose, review
   and approve their own claim; decide whether a second moderator is needed.
6. Store helpers without a viewer (`source()`, `evidence()`,
   `evidence_summary()`, `_claim()`): fine behind the current API, but a
   server API must expose only the viewer-checked calls.
7. Deletion / "forget me": nothing can be deleted yet (append-only); accounts
   need deletion of personal data, including history and backups.
8. Rate limits / spam for contributions and proposals; moderator queue.
9. Contributor IDs appear in shared history (`by`); decide what others may
   see of who contributed (pseudonymous IDs today, no names).
10. Local adapters (quiz memory, World Atlas) are only for the local user;
    a server would need per-user copies or treat them as shared sources.

B2 search latency pass (not released). Same results, faster: every result
(order, score, status, labels, explanation) is identical to before on all
113 realistic questions x FTS5/fallback (snapshot compared), and the test
suite checks it permanently (see below).
What changed (brain/retrieve.py, query.py, store.py):
- Per-claim search data is cached between searches. Which claims a search
  may use is still asked from the database every time (`visible_keys`,
  same `_visible_sql` rule, ids only); `index_rows` re-applies the rule.
  Entries are dropped when their claim has a new event (`changed_since`:
  every change records an event on that claim in the same transaction,
  also when another program writes).
- Exact scores computed once per signature (question words / synonyms /
  kind-of-question words present, number, polarity, status, whether the
  question's names occur); records containing one of those names get the
  full original scoring; the explanation is built only for returned
  records. `MAX_FACTOR` is shared by scoring and the fast path.
- Known names: per-name word sets memoised; longer-name and first-word
  lookups once per search (`Known`); after new claims only their subjects
  are read (sorted merge == full rebuild); reviews don't rebuild it.
- Cyclic GC paused during a search (state always restored): full
  collections over the cached index cost up to ~200 ms mid-search.
Checks: `retrieve.VERIFY_FAST` (Brain test modules turn it on: every search
also runs full() on every candidate and fails on any difference) and
`retrieve.EXHAUSTIVE` (reference: full() for all, plain sort - the old
way); tests/test_brain_speed.py compares fast vs reference on every
realistic question (with filler and with quiz memory / World Atlas), cache
invalidation (incl. changes by a second program), visibility, the name
list and the GC state.
Benchmark: `python tests/brain_speed.py` (10,000+ claims per scenario:
original speed test, Villa Brisa, Ciudad Coral; 113 questions; median of
5 runs per question; jitter = slowest single run). Same machine, old and
new code alternated, 3 rounds each:
  old aa037f2  FTS5 avg 57-61 ms, p95 163-170, worst 171-215;
               fallback avg 92-99, p95 179-194, worst 192-211
  new          FTS5 avg 11.6-13.0 ms, p95 28-32, worst 33-52;
               fallback avg 15-17, p95 30-32, worst 32-37; jitter <= 62
Outside the warm benchmark: first search after a review ~28 ms, after a
new claim ~38 ms, after a correction ~46 ms; the FIRST search after
opening the Brain builds the cache: ~160-210 ms once per session.
Trade-offs: memory for the cache (~1 entry per claim); `search_rows` kept
for older callers but no longer used by search.

B2 performance review (not released). Found and fixed (each reproduced on
b13b7b7, with a regression test that fails there): commits by another
connection (raw SQL edit, external delete + reused id, restored backup)
left stale - in the reused-id case another user's private - text in the
cache; now the cache is emptied when SQLite's `data_version` moves or the
event log goes backwards. Two searches in different threads could store a
stale row for good; cache upkeep now runs under the store lock. The name
cache moved from a global dict keyed by object id into the per-store
cache. Removed unused `search_rows`, `change_stamp`, `_library_docs`.
Measured (tests/brain_memory.py, Linux container, timings without memory
tracing): 1k claims: first search 19 ms, later ~2 ms; 10k: first 112-182
ms, later median 15 ms (max 47), after add / correct / review ~35 / 43-60
/ 27-34 ms, after another program's change 112-145 ms (full rebuild),
cache ~23 MB for 4 viewers, no growth over 200 searches; 50k: later
median 88 ms, first 860 ms, cache ~118 MB - scale work comes after B3.
Windows check extended: code fingerprint, external change, whole test
suite (packages in .venv-check of the check folder), speed and memory.

## SK Brain B3: connected facts (first pass done, not released)

B2 is provisionally accepted on Linux testing (45/45 deliberate-break checks);
**Windows validation of B2 is still pending - unverified, not passed.**

Design (no schema change; nothing else in B2 changed except as listed):
- `brain/facts.py`: a claim may state ONE fact of a closed set of relations
  (leads_to, caretaker, runs, room_id, quest_reward) in a few exact EN/ES
  sentence forms, incl. explicit absence ("has no caretaker", "no tiene
  encargado"). Anything else gives no fact. A fact has no status, evidence
  or owner of its own - it is part of its claim. Names compare only as
  exact normalised strings (leading article removed). A form without the
  place ("The northern exit leads to ...") takes the claim's subject.
- Facts are read once per cached record (`Doc.fact`), so B2's exact cache
  invalidation and the search visibility rule apply unchanged.
- `brain/reason.py`: a small question grammar turns a question into steps
  (outer ask + room description, nested up to 3). Each step uses only facts
  of claims the asker's search may see; rejected / outdated are ignored.
  Steps connect only through the exact same place name - never a shared
  source, a similar or partial name. Outcomes per step: missing (never read
  as absence), documented absence, conflicting (two values, or a claim
  marked conflicting), ambiguous (partial name / several candidates), value.
- `Brain.ask()`: answered only when ESTABLISHED - every claim used verified
  (or an explicit, verified absence). Otherwise status says why, `possible`
  may show an unverified possibility (labelled), and `hits` are B2 search.
  `audience` is "shared" only if every claim used is shared.
- Search (B2) changes, intended and listed in tests/test_brain_reason.py
  (B2UnchangedTests compares all 218 realistic results with B2's,
  tests/data/b2_search_reference.json, made from 8ab56c2):
  documented absence answers a question about the SAME named place;
  "look(s/ed/ing) after" = maintain (phrase map, INDEX_VERSION 5); synonym
  groups pet/mascota, allowed/permitir..., coin/moneda; a Spanish "¿..."
  question without a question word is yes/no.

B3 extraction is deliberately strict (found by probing tricky claims):
hedged / reported / past / future claims ("maybe", "I think", "used to",
"will", "creo", "probablemente"...) state no fact; a negative claim states
only an explicit absence; a person must be written as a proper name
(capitals; "van der" connectors; no "someone"); a place must contain a
capitalised word ("the garden" never connects two claims).
Results (Linux): realistic sweeps (ask() on all 110 Villa Brisa / Ciudad
Coral questions): no established answer on a wrong claim, none for an
unknown / conflicting / ambiguous question, no other user's claim used;
Coral multi-fact: Rafa and 2104560 established, the Rafael chain refused
(unverified). Search: Villa Brisa main 23/23 (was 21), held-out 15/15 (was
13); Coral unchanged; all other B2 results identical. Search latency same
as B2 (alternated runs, 10,000+ claims: FTS5 p95 30-32 ms, worst 39-47;
fallback worst 51-52 vs B2 43-46). ask() at 10k claims: median ~14 ms
(incl. search), first after opening ~360 ms (facts of every claim read).
Deliberate breaks at d9f1600: 64/65 caught; the 65th (BM9) was reported
as harmless - that turned out to be WRONG, see "B3 audit" below.
B3 limits: the question grammar and sentence forms are small; one fact per
claim; facts only from library claims (not World Atlas / quiz memory);
names must match exactly (no aliases); compound questions ("room ID and
caretaker") are left to search; answers are not stored as derived claims.

### B3 audit (after d9f1600)

BM9, corrected. BM9 changed `extract()` in brain/facts.py from "if the first
matching sentence form has a value that is not a plain name, try the later
forms" (`continue`) to "the claim states no fact" (`return None`). It was
reported as unable to change any result. Fuzzing 300,000 generated
sentences (every form's keywords, proper / lowercase / indefinite names,
negations, connectors) showed it changed 1,242. In every case the CURRENT
code (`continue`) was the one producing junk: a run-on sentence matched a
caretaker form with a bad person, then the room_id form (which ignores text
after the number) gave e.g. room_id("capitana irene") = 123. The fallback
had been added for "Nobody looks after X"; the later rule "a negative claim
states only an explicit absence" handles that before any form is tried.
Why the BM9 behaviour is the safe one: it can only REMOVE a fact (both
versions are identical until the first bad slot, where it stops), never
create or change one - a missing fact makes reasoning say "cannot
establish", never give a wrong answer. Fix: the first matching form now
decides for place and value alike (no realistic claim changed, 74
checked); a claim that says two things states none. BM9 is now the
mutation "re-add the fallback", caught by
test_run_on_and_two_fact_sentences_state_no_fact. What could make this
area harmful in future: adding a broad sentence form, or a form that
ignores trailing text (like room_id's "(?: .*)?"), before narrower ones;
or relaxing the negative-claim / hedge filters. Keep forms ordered narrow
-> broad and keep the fuzz comparison when changing them.

Other audit findings (each with a test that fails on d9f1600):
- An established step listed same-value unverified claims as evidence and
  let them set the audience (a verified shared answer showed "personal").
  Established steps now rest on verified claims only; others are shown as
  "also stated, unverified (not used as evidence)".
- A reverse step whose room came from a conflicting claim said "ambiguous";
  now "conflicting".
- Pre-existing (not B3): tests/test_ai.py wrote "test-key" for ai:gemini
  into the user's REAL keys.bin - reassigning secrets.KEYS_PATH does not
  change get_key/set_key/has_key, whose path is a default argument fixed at
  import. INSTALL.bat's self-test runs the suite. Fixed in the test (file
  functions redirected + a test that the real file is untouched); the
  Windows check now also runs the suite with SK_ASSISTANT_DATA in a temp
  folder. Recommended (app change, needs approval): secrets.py should read
  KEYS_PATH at call time instead of as a default argument.
- Trust assumption for any server: Brain.search/ask treat viewer None and
  "" as the local (desktop) user. A web front end must always pass its own
  server-side identity.
Audit checks that passed (EN + ES, 4 viewers, b3audit): private claims
never reach a shared answer (a private belief only changes its owner's
view); unverified chains are not established; rejected / outdated never
used; conflicts and absence-vs-value give no answer; ambiguous rooms /
people never chosen; nothing of another user in answers, steps,
explanations, search hits or errors (also with unknown / empty viewer);
sharing a source never creates a relation; no slow-regex input found
(12,000 characters <= 45 ms).
Plain-search fallback: worst case 51-52 ms in both B3 runs vs 43-46 ms for
B2 on the same machine (alternated); p95 30-32 ms. Slightly over the 50 ms
target for the slowest question; FTS5 (the normal path) stays within it.

### Web Test Lab (plan only - nothing built or deployed)

Goal: try the real Brain + B3 from an iPhone, desktop off, on synthetic data.
Smallest architecture:
- One small private repository (new; the main repo stays public for the
  updater) with: a copy of puzzle_assistant/brain (stdlib only), the
  synthetic dataset builders (brain_eval*.py-style), and lab/app.py - a
  Flask app (+ gunicorn) of about 150 lines. No desktop app, no adapters
  (quiz memory / World Atlas paths always "").
- At start-up it BUILDS a fresh synthetic knowledge.db in its own temp
  folder (ephemeral disk is fine) - it has no way to reach real data,
  because none exists on the host.
- Routes: GET /login, POST /login, POST /logout, GET / (chat page), POST
  /api/ask {"question"} -> {status, answer, possible, steps, trace}. Nothing
  else: no learn / review / share / admin routes; the Brain object is wrapped
  in a class that only exposes ask().
- Auth: one passphrase (its hash + a session key in the host's secret
  environment variables, never in the repo); signed, HttpOnly, Secure,
  SameSite=Strict session cookie; login rate limit and lockout; every
  route except /login requires the session.
- Identity: the session maps to a FIXED test contributor created at
  start-up; the viewer is never read from the request. The wrapper refuses
  viewer None / "" (see trust assumption above).
- Hardening: question length cap (500 chars), per-session rate limit,
  JSON-only POST with a custom header (CSRF), Content-Security-Policy
  default-src 'self', X-Frame-Options DENY, no request logging of questions
  beyond counts, generic error messages.
- Hosting (free tier, HTTPS on the provider's subdomain): Render free web
  service (sleeps after ~15 min idle, ~1 min wake-up) or PythonAnywhere free
  (one web app, low daily CPU). Terms change: re-check before deploying.
Brain changes needed before deployment: none to the reasoning; add the
ask-only wrapper with a mandatory viewer; keep the B3 tests in the lab repo
and run them in its CI before each deploy.

## SK Brain milestones (cumulative workflow, owner-approved architecture)

Branches: `claude/brain-integration` (created from 39fad57) holds ONLY
owner-approved milestones and moves forward by fast-forward after the
owner's explicit written approval. Each milestone is built on
`claude/brain-<stage>` from the integration head. Never touched by Brain
work: the release/distribution branch, latest.json, the updater branches,
the stable SK Test Lab. Candidate lab: SK-Online `claude/sk-lab-candidate`
(separate Render service, auto-deploy off, synthetic data only).

Milestone log (approved commits only):
| Milestone | Brain commit | Candidate commit | Approved |
|---|---|---|---|
| (start) | 39fad57 | - | release branch head |
| B3.5 | code 9b94600 + this validation record (the commit that adds this row) | 47a1858 | owner, 2026-11-08 |

### B3.5 (APPROVED by the owner, 2026-11-08)

Validation results (reported by the owner):
- Windows 11, Python 3.14.10 64-bit, SQLite 3.50.4 with FTS5:
  BRAIN_WINDOWS_CHECK.bat 10 passed / 0 failed; Brain fingerprint
  2055eb8cd979c00b (= 39fad57); 178 Brain tests and 326 application tests
  passed; locking, concurrent writes, rollback, external changes and FTS5
  recovery passed. B2 and B3 are Windows-validated.
- Real habbosk.db and atlas.db read through safe_snapshot; both files and
  their sidecars unchanged.
- BRAIN_DB_AUDIT.bat: "No knowledge.db found - nothing to audit. Nothing
  was read or changed." Expected: SK 3.0 never creates knowledge.db (only
  the test bench in "Real, changes allowed" mode does, never used). The
  owner checked %APPDATA%\SK Assistant: atlas.db, config.json, habbosk.db,
  keys.bin - no knowledge.db, no bench backups. Migration decision:
  NOTHING to migrate from knowledge.db; B5 starts a new Brain database.
  habbosk.db / atlas.db stay as they are (read-only adapters); importing
  them is a separate, later decision.
- Audit tool coverage on Windows: ONLY the missing-database path ran on
  Windows. The in-memory and temporary-copy paths, cloud-folder refusal,
  drive-type detection and SK-running detection are tested on Linux only.
- Candidate lab: separate Render service sk-lab-candidate (auto-deploy
  off) live at SK-Online 47a1858 (GitHub tests passed); owner tested on
  iPhone: build line "Brain 39fad57 - Lab 47a1858", Lantern Garden ->
  Mira, Glass Library -> documented none, Faro -> ambiguous.
  (The first deploy failed with "No module named app" - most likely
  Render's default start command on a manually created service; the
  correct one is `gunicorn -c gunicorn.conf.py lab.wsgi:app`. No code was
  changed; the service is now live.)
- For B4: the answer-category contract must state how adapter statuses
  (quiz memory / World Atlas) map to the four categories.

Work done in B3.5:
- `devtools/safe_snapshot.py`: reads a SQLite database WITHOUT opening the
  original with SQLite (no recovery, checkpoint or -shm creation): in
  memory when no -wal/-journal holds data (Python 3.11+), otherwise a byte
  copy of the database + -wal/-journal in a private temp folder
  (%LOCALAPPDATA%\Temp, refused inside OneDrive/Dropbox/Google Drive/
  iCloud/Box folders, on network or removable drives), deleted afterwards
  (ordinary deletion, not forensic erasure; leftovers removed next run).
  Fingerprints (size, mtime, SHA-256) of the database and all sidecars are
  taken around the read; a change means retry, then refusal.
- `devtools/knowledge_audit.py` + `BRAIN_DB_AUDIT.bat`: read-only audit of
  knowledge.db; refuses while SK runs; report = counts / kinds / dates /
  sizes / fingerprints / integrity only (unknown values shown as "other",
  paths masked), saved next to the program, never in the data folder;
  verifies the originals and the folder listing are exactly unchanged.
- `brain_windows_check.py`: the real-files check no longer opens real
  files with SQLite (safe_snapshot instead) and no longer reads
  knowledge.db at all (the audit does).
- tests/test_knowledge_audit.py (synthetic databases only): 26 tests;
  22/22 deliberate breaks of the audit safeguards caught (Linux).
- Brain code unchanged (fingerprint 2055eb8cd979c00b = 39fad57).

### B4 Answer reliability (in review - not approved yet)

Owner-approved design (all five decisions): Documented only under the
strict support rule; questions outside B3's grammar are Unknown with
labelled related records; quiz memory / World Atlas are related records
only (answer eligibility deferred); answers in the question's language
(EN/ES, conservative detection); the candidate lab gets the respond() view.

What was built (ask() and search() unchanged - B2 and B3 references identical):
- `brain/respond.py` + `Brain.respond(question, viewer)`: exactly one
  category per answer - established / documented / ai_generated (defined,
  never produced before the AI gateway) / unknown + reason
  (nothing_documented, sources_disagree, ambiguous, only_outdated,
  unsupported, not_understood). Mapping from ask(): established/absent ->
  Established; unverified -> Documented only if EVERY unverified claim used
  has direct/confirmation evidence from a real source (not someone's own
  statement) whose quote, read ON ITS OWN with B3's sentence forms, states
  exactly the same fact - else Unknown(unsupported) with the possibility
  labelled; missing -> nothing_documented, or only_outdated (a separate
  check that never changes the category); conflicting -> both sides;
  ambiguous -> a clarification question with B3's candidates;
  not_reasoned -> not_understood + labelled related records (never answers).
  EN/ES sentences filled only from structured values ("coins" -> "monedas").
- `model.py`: CATEGORIES, UNKNOWN_REASONS, Citation, Response. The
  audience ("shared" only if every claim used is shared) covers the answer
  (value, sentence, possible, citations, steps); `related` is always the
  asker's own view and must never be passed on with a shared answer.
- `store.cited_evidence(claim, viewer)`: read-only, viewer-checked
  citations; a private source is cited only on its owner's own personal
  claim (never on shared knowledge, never to anyone else).
- respond() never writes: save_offer is always None (confirmed saving is
  B7), ai is always "none" (gateway is B8).
Tests (synthetic data only):
- tests/b3_reference.py + data/b3_ask_reference.json: 109 ask() results
  recorded at c9fe6b9, must stay identical.
- tests/brain_answer_eval.py ("Puerto Nube", SK-BRAIN-EVAL-003: 31 claims,
  45 questions incl. 10 everyday personal questions, expectations written
  from the data): 45/45; zero false Established, zero false Documented.
- tests/brain_citation_set.py: 53 labelled quotes (supports / contradicts
  / partial / unrelated: negation, conditions, dates, hedges, reported
  speech, context, lists, tables, EN/ES): zero false support; 2 genuine
  supports refused on purpose (extra clause, table row).
- test_brain_b4_guards.py: B3 reference; the database is byte-identical
  after every answer and every write method can be disabled; respond.py
  only uses viewer-checked store calls; no network code in the Brain and
  answers with the network blocked; AI claims cannot be verified by
  automatic contributors; categories / reasons from the fixed lists.
- test_brain_respond.py: each category / reason, wording EN/ES, quiz memory
  and World Atlas only as related records (their files unchanged), privacy
  markers never reaching another user or a shared answer.
- Deliberate breaks: 24/24 caught (Linux). Speed with ~10,000 claims:
  respond() median ~21 ms = ask() (~20 ms).
Limits (by design, for later milestones): Documented needs a quote in B3's
exact sentence forms (passages: B6); no AI answers (B8); no memory /
confirmation flow (B7); small question grammar (B9/B10); quiz memory and
World Atlas never answer yet.

## Known limits / ideas

- Tk window placement on multi-DPI two-screen setups was unreliable; the
  overlay therefore positions itself via `UpdateLayeredWindow` in screen pixels.
- Question memory key = category + question text + option set (hint lines are
  excluded). Negation words block fuzzy reuse.
- Possible accessibility additions (offered, not requested yet): spoken cues,
  larger marks, high-contrast mode.
