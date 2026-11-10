SK ASSISTANT TRIAL  (test version of the AI speed changes A-D - not a release)
==========================================================================

This folder is a complete SK Assistant (the normal app plus changes A-D)
that runs completely separately from your everyday SK Assistant:

  * Its data (question memory, World Atlas, settings, AI timing log) is in
    trial_data\ in THIS folder. It starts with the 12 starter questions and
    the World Atlas calibration, like a fresh install.
  * It cannot open, change or delete anything in your normal data folder
    (%APPDATA%\SK Assistant). A guard blocks it and counts every attempt
    (trial_data\blocked_access.log; it should stay empty).
    The one exception: it READS your saved Gemini key there (never copies,
    shows or saves it).
  * On the first start it copies your normal SETTINGS (read-only) so the AI,
    text size etc. are the same. Updates are switched off in the trial.
  * It creates no Desktop icon and changes nothing in your normal SK folder.
  * Only one SK can run at a time: close your normal SK Assistant first.

ONCE: SET UP
  Double-click TRIAL_SETUP.bat. It creates the trial's own Python environment
  (.venv in this folder, the same packages as SK), then runs a self-test with
  the guard on. Wait for "Setup finished".

USE THE TRIAL
  1. Close your normal SK Assistant (window closed - check the taskbar).
  2. Double-click TRIAL_START.bat. The window title says
     "SK Assistant - TRIAL (test version)".
  3. Play normally: library questions and Preguntas express are what we want
     to measure. Leave it open as long as you like; close it when done.
     The time under an AI answer is now the real wait.

AFTER PLAYING
  Double-click TRIAL_SUMMARY.bat and send the two files it saves
  (trial_results\TRIAL_SUMMARY_....txt / .json) to Claude. They contain
  timings and counts only - no questions, answers or keys.

BACK TO YOUR NORMAL SK ASSISTANT
  Close the trial window, then start SK Assistant from its usual icon.
  Nothing has to be undone: your normal app, data and settings were never
  changed. Questions learned during the trial stay in the trial (your normal
  SK does not learn them, and the trial does not see your normal memory).

REMOVE THE TRIAL
  Close it and delete this folder. That removes everything of the trial.

Optional: TRIAL_FULL_SELFTEST.bat runs every automated test (several
minutes) with the guard on.
