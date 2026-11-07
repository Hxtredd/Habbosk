SK ASSISTANT  (v2.0 - formerly Puzzle Answer Assistant, Windows prototype)
==================================================

A small always-on-top window that watches your screen for the
"Montar un arma" weapon-assembly puzzle (habbosk.us/play), reads the pieces
(7 or 8, lettered A-H), works out the assembly order and shows it immediately.

STRICTLY READ-ONLY
------------------
The program only takes screenshots and shows text in its own window.
It never clicks, never moves the mouse, never presses keys and never talks
to the browser or the game. You do every action yourself.
All OCR runs locally (Tesseract). Nothing is sent over the internet unless
you switch on an AI in Settings (then only the text of new questions).


1. INSTALLATION
---------------
Requirements: Windows 10 or 11 (64-bit), Python 3.9 - 3.13.

  a) Install Python from https://www.python.org/downloads/windows/
     During setup tick "Add python.exe to PATH" (Tkinter is included).
  b) Unzip it somewhere, e.g. into C:\ . The folder is named after the
     version (SK_Assistant_v2). Updating from an older version: unzip the
     new one, run its INSTALL.bat (it moves the Desktop / Start icons to the
     new folder), then delete the old folder. Your memory, settings and
     keys are kept in your user profile, not in the program folder.
  c) Double-click INSTALL.bat
       - creates a private Python environment (.venv)
       - installs numpy, opencv-python-headless, mss and pillow
       - installs Tesseract OCR with winget if it is missing
         (if winget is not available it tells you where to download it:
          https://github.com/UB-Mannheim/tesseract/wiki , 64-bit installer)
       - the Spanish OCR model is already included in .\tessdata


2. USE
------
  * INSTALL.bat puts an "SK Assistant" icon on your Desktop and in
    the Start menu (CREATE_SHORTCUTS.bat recreates them, e.g. after moving
    the folder). Double-click it (or RUN.bat): the dark floating window appears.
  * Pin to the taskbar: right-click the Desktop icon (Windows 11: then
    "Show more options") > "Pin to taskbar". Or search "SK Assistant"
    in the Start menu > right-click > "Pin to taskbar".
    Windows does not let programs pin themselves, so this one click is manual.
    Clicking the icon while it is already running brings the window forward.
  * Open the game and play normally. When the puzzle appears the window shows:

        Puzzle Assistant                      ● Verified
        Pistola · calibre 38
        ORDER   [1 C] [2 B] [3 A] [4 D] [5 E] [6 G]
        Next: C · Armazón
        Don't click  (F · 9)

  * Numbers on the puzzle (Windows): the step numbers are drawn directly on
    the puzzle cards, on whichever screen the game is - no window to move.
    Click the cards in number order:
        (1) green, glowing     = the piece to click next
        (2) (3) ... blue ring  = the following steps (any piece whose
                                 prerequisites are mounted may go next;
                                 the numbers are one valid order)
        ✓ green                = already mounted
        ✕ red                  = other caliber: don't click
        ? orange               = card not read clearly: NO numbers are shown
        PROBAR ▶ (yellow)      = all mounted, press Probar
    The numbers are click-through: your clicks go to the game underneath.
    The program itself still never clicks. The numbers stay fixed while you
    mount pieces and disappear the moment the puzzle closes or changes.
    "Numbers: on/off" switches them. Needs Windows 10 (2004) or newer, so the
    numbers stay invisible to the program's own screen reading.
  * Several screens: every screen is searched (setting "monitor": 0). Set it
    to 1, 2 ... to watch only one screen.
  * Progress: as you mount pieces (they turn green with a ✓ in the game) the
    window shows "Done ✓ C B" and "NEXT → A → D → E → G", and finally
    "PROBAR ✓". The answer is locked once verified: green cards never change
    it. If a ✓ ever appears on a piece the answer says was not ready yet, a
    warning is shown (the reading would have been wrong).
  * Pieces of another caliber: leave them alone (no click needed).
  * When several pieces are possible at the same time, any order works.
  * Colours:
       GREEN  = verified: every card was read clearly and everything adds up.
                Safe to follow.
       AMBER  = "NOT SURE – check X": some card was not read clearly. No order
                is shown as an answer (a too-early click costs a point); only a
                small "unverified guess". Look at the named card(s) or press F5.
       RED    = "? – check X": not reliable (missing piece, target caliber not
                read, impossible arrows...). Press F5 to read again.
  * When the puzzle closes the window goes back to "Waiting for puzzle…".
    The next puzzle is detected automatically; no restart needed.

  Buttons (the window must have focus for F5/F8):
    ● On / ○ Off  switch the whole assistant on or off
    Rescan (F5)   force a fresh scan of the screen right now
    Numbers       show / hide the numbers and answers drawn on the game
    Settings      the settings panel (below). F8 = save a snapshot to .\debug

  Settings panel:
    General       assistant on/off, keep on top, numbers on screen, text size
    What SK Assistant helps with
                  weapon puzzle, money puzzle, questions (on/off each);
                  recognise questions automatically or only on "Scan"
    AI and internet
                  AI provider (none / Google Gemini / Anthropic Claude / OpenAI),
                  API keys (stored encrypted with your Windows account in
                  keys.bin - never in config.json), paid use on/off
                  (off by default: never pays), monthly limit, internet search
                  on/off, "Test AI", this month's requests and estimated cost.
                  See 2d.
    Memory        how many questions SK Assistant has learned, how often the
                  built-in solving and the AI were right; Export; Import
                  (add questions from a habbosk.db or an exported .json);
                  forget all
    Screen        select a region / watch all screens / save a snapshot

  Since v2.0, learned questions, settings and keys live in your user
  profile:  %APPDATA%\SK Assistant   (habbosk.db, config.json, keys.bin)
  so they survive new versions, wherever you extract the program.
  Coming from v1.x? Settings > Memory > Import... and choose the old
  memory file: <old program folder>\data\habbosk.db (e.g. in the old
  PuzzleAnswerAssistant folder). Its questions are added; nothing already
  known is lost. (If v2 is unzipped into the old folder itself, this
  happens automatically on first start.) 12 starter questions confirmed by
  the game are added on first start.
  Back up with Settings > Memory > Export.

  The window can be moved and resized; the answer text scales with the width.
  The window is hidden from screen capture (Windows 10 2004+), so it may
  safely overlap the game.


2b. MONEY PUZZLE ("Cuenta el dinero")
------------------------------------
  Coins and bills slide across a strip; you type their total. The program
  recognises this puzzle by its keypad and then watches only the strip, about
  40 times a second:
    * every coin / bill is followed as it crosses and counted exactly once;
      its printed value is read many times and the majority wins;
    * the legend ("5 · 10 · 25") must contain every value read;
    * the game's own "Vistos 7/7" counter must match the number counted.
  While items are passing, the window shows the running total (blue
  "290 …"). When the strip is empty and everything checks out it shows the
  total in green ("Verified", and "= 390" on the left of the input box).
  You type it yourself. If the count does not match the game it shows
  "Not sure" ("? 390") - check before typing.

2c. QUESTIONS (library "Estudiar en la biblioteca", more coming)
-----------------------------------------------------------------
  SK Assistant reads the question window by itself: the yellow category
  label, the question, hint lines (• ...) and the options. It recognises
  ADIVINANZA, PREGUNTA, ENCUENTRA EL ERROR, ¿QUIÉN SOY?, ORDENAR (put in
  order) and PAREJAS (match pairs).
    * Already learned: the answer appears in the window ("From memory") and
      on the question itself - a green check on the right option, step
      numbers for ORDENAR, the same number on both items of each PAREJAS pair.
    * Not learned yet: "New question". Answer it in the game: as soon as the
      game shows the correct answer (green option, revealed order or pairs)
      SK Assistant saves it with the game's explanation ("Learned").
      Next time that question appears - even with the options shuffled - the
      answer is shown before you click.
    * Built-in solving (no AI, instant) for brand-new "work it out" questions:
        - ORDENAR with sums (9 + 12, 63 − 8, 10 × 2, 20 ÷ 5 ...), smallest or
          largest first. Numbers and signs are read by their shape; if any
          of them can't be read with certainty, no answer is given.
        - sequences: ¿Qué letra/número sigue? (F, J, N, R -> V; 2, 4, 8 -> 16;
          1, 1, 2, 3, 5 -> 8; missing or previous term)
        - ¿Cuánto es 7 × 8?  and  "which equation is wrong / correct"
        - simple word problems (45 minutos y luego 25 más -> 70): shown as
          "Probably" in orange with "Check before clicking", and no mark on
          the game - wording can be ambiguous.
      An answer is shown only when exactly one option fits; otherwise nothing
      (a wrong click costs a point). "Solved" answers are checked against
      what the game reveals: Settings > Memory shows how many were right.
    * Questions repeat, so the memory fills up quickly.
    * Still unknown and an AI is set up (2d): "Thinking...", then the AI's
      answer in orange, "AI - unverified", with no mark on the game. If the
      AI itself is not sure it says "Not sure". Check before clicking.
  Settings > "Recognise questions": automatically, or only when you press
  Rescan (F5).

2d. AI ANSWERS (optional, off by default)
-----------------------------------------
  Order used for every question: memory -> built-in solving -> AI.
  The AI is asked only for questions the first two can't answer, in the
  background (the window keeps working). One request per new question.

  Set up Google Gemini (has a free tier):
    1. Get a key at aistudio.google.com ("Get API key").
       To be sure you never pay: do NOT enable billing for that Google
       project (free-tier keys simply stop for a moment when the free limit
       is reached).
    2. Settings > AI and internet > choose "Google Gemini", paste the key in
       "Gemini key", press Save. Never paste the key anywhere else (not in
       chats, not in config.json).
    3. Press "Test AI": it should say "Working".
  Learned facts help the AI: with each new question SK Assistant sends up
  to 5 related questions the game already confirmed (same people, places,
  items) with their answers and the game's explanations. Only those few
  entries are sent, never the whole memory. The window shows "used N
  learned facts" under an AI answer.
  SK Assistant picks Google's newest stable "Flash-Lite" model by itself
  (fast and cheap). To force one, set "ai_model" in config.json.

  Cost controls:
    * "Allow paid AI use" is off by default. Then Claude and OpenAI (always
      paid) are never used, and Gemini is limited to ai_daily_limit (200)
      requests per day.
    * With paid use on: the monthly limit ($) stops all paid requests once
      the estimated spending reaches it. Settings shows requests and the
      estimate for this month (an estimate, the provider's bill is exact).
    * "Too many requests" from the provider: the AI pauses for one minute.
    * Internet search (Tavily / Brave, needs its own key) only when switched
      on AND the AI said it is unsure.

  AI answers are never saved as true. Only the game's revealed answer is
  learned; the AI's answer is compared with it (Settings > Memory shows how
  often the AI was right). Many questions are about this game's own world,
  which an AI can't know - memory stays the main source.


3. HOW DETECTION WORKS
----------------------
  * The puzzle is found by its grid of piece cards (7 or 8 cards, each with a
    letter badge, a caliber and a "↳" arrow row). This is pure image
    processing (~15 ms per screen check, no OCR), so it works the moment the
    puzzle opens, whatever is behind the see-through puzzle window.
  * The "Montar un arma" text in the game's task sidebar is ignored: only a
    real grid of piece cards counts as a puzzle.
  * Letters, calibers and arrow letters are tiny (about 5x7 pixels), so they
    are recognised by shape matching (puzzle_assistant\glyph_templates.npz)
    instead of OCR. Tesseract is only used for the line
    "Fusil · calibre 5.56" (target caliber), "Montadas 0/8" (piece count
    check) and the piece names (display only).
  * Speed: typically 0.3 - 0.6 s from the puzzle appearing to the answer.
    Unchanged puzzles are never re-read. A verified answer is locked until
    the puzzle closes or changes (other cards or "calibre" line); mounted
    (green) cards only update the progress.
  * "Region" is optional: it limits the search to a part of the screen.

4. PUZZLE RULES USED BY THE SOLVER
----------------------------------
  "Monta cada pieza cuando estén puestas las que indica su flecha.
   Aparta las de otro calibre y prueba el arma al acabar."

  * A piece is mounted only after all the pieces its arrow points to.
  * Pieces with a caliber different from the target caliber are left alone.
  * Pieces without a caliber number are caliber-neutral and are mounted.
  * The order is a topological sort (Kahn's algorithm, alphabetical tie-break).
  * Circular dependencies, unknown letters, a mounted piece depending on a
    set-aside piece, missing pieces or a missing target caliber are reported
    as problems. Nothing is invented or guessed silently.


5. TROUBLESHOOTING
------------------
  "Tesseract OCR was not found"   -> install it (see 1c) or set "tesseract_cmd"
                                     in config.json to the full path of
                                     tesseract.exe.
  Never detects the puzzle        -> press F8 (Snapshot) with the puzzle visible
                                     and send the debug files. Check the
                                     "monitor" setting if you have several
                                     screens (1 = primary, 2 = second, 0 = all).
                                     Browser zoom between 80 % and 200 % works.
  Wrong/uncertain reading         -> press F8 (Snapshot) while the puzzle is
                                     visible, or run RUN_DEBUG.bat. The .\debug
                                     folder then shows the detected piece boxes
                                     and exactly what OCR read for every piece.
  Test with a saved screenshot    -> drag the image onto TEST_SCREENSHOT.bat

  config.json (in %APPDATA%\SK Assistant, created on first exit) settings:
    monitor, region, poll_interval_ms, idle_poll_ms, tesseract_cmd,
    tessdata_dir, ocr_upscale, ocr_threads, min_confidence (60),
    read_names, require_layout, disappear_after_ms, window_geometry,
    opacity, debug_save, ai_model ("" = automatic), ai_daily_limit (200),
    ai_price_in / ai_price_out ($ per million tokens, for the estimate)


6. FILES
--------
  app.py                     entry point
  puzzle_assistant\capture.py   screen capture + Windows DPI awareness
  puzzle_assistant\locate.py    finds the puzzle by its grid of piece cards
  puzzle_assistant\cardreader.py reads one card: badge, caliber, arrow letters
  puzzle_assistant\glyphs.py    shape matcher for the tiny letters / digits
  puzzle_assistant\vision.py    image preprocessing, box / grid detection
  puzzle_assistant\ocr.py       local Tesseract wrapper (parallel, no temp files)
  puzzle_assistant\parser.py    OCR text -> pieces, calibers, arrows
  puzzle_assistant\solver.py    dependency graph + topological sort
  puzzle_assistant\analyzer.py  ties OCR, parser and solver together
  puzzle_assistant\engine.py    background monitoring loop
  puzzle_assistant\ui.py        floating window
  puzzle_assistant\overlay.py   click-through step numbers drawn on the cards
  puzzle_assistant\render.py    anti-aliased badges, step chips (Pillow)
  puzzle_assistant\money.py     money puzzle: strip tracking, value reading, checks
  puzzle_assistant\qa\         questions: model, text helpers, SQLite memory,
                             screen.py = reading the question window,
                             solvers.py = built-in solving (sums, sequences),
                             ai.py = AI providers, cost limits, web search
  puzzle_assistant\storage.py  memory in %APPDATA%, merging old memories
  assets\seed_questions.json   12 starter questions confirmed by the game
  puzzle_assistant\settings_ui.py  settings panel
  puzzle_assistant\secrets.py   encrypted API key storage (Windows DPAPI)
  assets\fonts\                 Inter font (SIL Open Font License, see OFL.txt)
  tests\                     unit tests; tests\fixtures holds real puzzle panels and
                             two recorded money rounds (coins = 125, bills = 390)
  tools\build_glyph_templates.py  regenerates glyph_templates.npz
  tessdata\spa.traineddata   Spanish OCR model (Apache-2.0, tesseract-ocr/tessdata_fast)
