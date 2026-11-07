PUZZLE ANSWER ASSISTANT  (v1.2, Windows prototype)
==================================================

A small always-on-top window that watches your screen for the
"Montar un arma" weapon-assembly puzzle (habbosk.us/play), reads the pieces
(7 or 8, lettered A-H), works out the assembly order and shows it immediately.

STRICTLY READ-ONLY
------------------
The program only takes screenshots and shows text in its own window.
It never clicks, never moves the mouse, never presses keys and never talks
to the browser or the game. You do every action yourself.
All OCR runs locally (Tesseract). Nothing is sent over the internet.


1. INSTALLATION
---------------
Requirements: Windows 10 or 11 (64-bit), Python 3.9 - 3.13.

  a) Install Python from https://www.python.org/downloads/windows/
     During setup tick "Add python.exe to PATH" (Tkinter is included).
  b) Unzip this folder somewhere, e.g.  C:\PuzzleAssistant
  c) Double-click INSTALL.bat
       - creates a private Python environment (.venv)
       - installs numpy, opencv-python-headless and mss
       - installs Tesseract OCR with winget if it is missing
         (if winget is not available it tells you where to download it:
          https://github.com/UB-Mannheim/tesseract/wiki , 64-bit installer)
       - the Spanish OCR model is already included in .\tessdata


2. USE
------
  * INSTALL.bat puts a "Puzzle Answer Assistant" icon on your Desktop and in
    the Start menu (CREATE_SHORTCUTS.bat recreates them, e.g. after moving
    the folder). Double-click it (or RUN.bat): the dark floating window appears.
  * Pin to the taskbar: right-click the Desktop icon (Windows 11: then
    "Show more options") > "Pin to taskbar". Or search "Puzzle Answer
    Assistant" in the Start menu > right-click > "Pin to taskbar".
    Windows does not let programs pin themselves, so this one click is manual.
    Clicking the icon while it is already running brings the window forward.
  * Open the game and play normally. When the puzzle appears the window shows:

        PUZZLE ANSWER ASSISTANT
        Status: Puzzle detected ✓ verified
        Target caliber: 12 · Pistola
        RECOMMENDED ORDER:
        H → A → C → D → G → B → E
        Set aside (other caliber): F(9)
        Then: test the weapon (probar el arma)

  * Colours:
       GREEN  = every check passed (all pieces, letters, calibers, arrows read
                with good confidence, no cycles).
       AMBER  = an order was computed but something was read with low confidence.
                The warnings underneath say what. Double-check those pieces.
       RED    = not reliable (a piece is missing, the target caliber is not read,
                circular dependencies, ...). Do not trust it; press F5 to rescan.
  * When the puzzle closes the window goes back to "Waiting for puzzle…".
    The next puzzle is detected automatically; no restart needed.

  Buttons (the window must have focus for F5/F8):
    Rescan (F5)  force a fresh scan of the screen right now
    Region       drag a box around the puzzle window once. Makes detection
                 faster and more reliable (recommended). Saved in config.json.
    Auto         forget the region and search the whole monitor again
    Snapshot     (F8) saves the current screen + what was read to .\debug
                 Useful if recognition is wrong.

  The window can be moved and resized; the answer text scales with the width.
  The window is hidden from screen capture (Windows 10 2004+), so it may
  safely overlap the game.


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
    Unchanged puzzles are never re-read. Once a verified answer is shown it
    stays while you mount the pieces, until the puzzle closes or changes.
  * "Region" is optional: it limits the search to a part of the screen.

4. PUZZLE RULES USED BY THE SOLVER
----------------------------------
  "Monta cada pieza cuando estén puestas las que indica su flecha.
   Aparta las de otro calibre y prueba el arma al acabar."

  * A piece is mounted only after all the pieces its arrow points to.
  * Pieces with a caliber different from the target caliber are set aside.
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

  config.json (created on first exit) settings:
    monitor, region, poll_interval_ms, idle_poll_ms, tesseract_cmd,
    tessdata_dir, ocr_upscale, ocr_threads, min_confidence (60),
    expected_pieces (0 = automatic), read_names, require_layout,
    disappear_after_ms, window_geometry, opacity, debug_save


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
  tests\                     unit tests (incl. two real puzzle panels in tests\fixtures)
  tools\build_glyph_templates.py  regenerates glyph_templates.npz
  tessdata\spa.traineddata   Spanish OCR model (Apache-2.0, tesseract-ocr/tessdata_fast)
