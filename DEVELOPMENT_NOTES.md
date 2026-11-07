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

1. **Strictly read-only.** Never click, move the mouse, type, or interact with
   the browser/game. The player does every action. The owner later asked about
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
- Prefers updates as a ZIP (`dist/`). The folder inside the ZIP is named
  after the version (`SK_Assistant_v2.1`); unzip, run `INSTALL.bat` (moves
  the shortcuts). Data lives in `%APPDATA%\SK Assistant`, so the folder
  can change. Each update gets the next version: v2.1, v2.2, ... (ZIP and folder named
  `SK_Assistant_v2.1` etc.; bump `puzzle_assistant/__init__.py` and README).

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

Stage plan: 1 = Atlas DB + Live Explorer (done, v2.3); 2 = Go to Room;
3 = arrow detection + coloured overlay + which exit was used; then
exploration queue, connection graph.

## Known limits / ideas

- Tk window placement on multi-DPI two-screen setups was unreliable; the
  overlay therefore positions itself via `UpdateLayeredWindow` in screen pixels.
- Question memory key = category + question text + option set (hint lines are
  excluded). Negation words block fuzzy reuse.
- Possible accessibility additions (offered, not requested yet): spoken cues,
  larger marks, high-contrast mode.
