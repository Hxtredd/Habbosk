SK ASSISTANT TRIAL  (test version of the WORK HELPER - not a release)
==========================================================================

This folder is a complete SK Assistant (v3.4.1 plus the new work helper)
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
  3. Settings > Work helper: type your Habbo name (SK pauses when someone
     mentions it). Energy level for :beber (40) and minutes (60) can stay.
  4. In the game: clock in at the Armería and leave the job panel open.
     Move SK's window so it doesn't cover the room or the job panel
     (SK never clicks under its own window - it would stop instead).
  5. Press "Start working" in SK and take your hand off the mouse and
     keyboard. Stay at the PC and watch.
     - Any key or mouse movement stops it at once (that is the safety stop).
     - "¿Sigues ahí?" appears now and then: click "Sí, sigo aquí".
     - When someone mentions your name it beeps and pauses: answer them
       yourself, then press "Continue" in SK.
     - Mini-game tasks (Montar un arma, Limpiar el banco) pause it: do them
       yourself, then press "Continue".
     - When it stops, the reason is shown under the buttons.

AFTER PLAYING
  Tell Claude what happened: tasks done, every stop / pause and its reason
  (the text under the buttons), and anything it did that you didn't expect.
  A short screen recording of a stop that looks wrong helps most.

BACK TO YOUR NORMAL SK ASSISTANT
  Close the trial window, then start SK Assistant from its usual icon.
  Nothing has to be undone: your normal app, data and settings were never
  changed. Questions learned during the trial stay in the trial (your normal
  SK does not learn them, and the trial does not see your normal memory).

REMOVE THE TRIAL
  Close it and delete this folder. That removes everything of the trial.

Optional: TRIAL_FULL_SELFTEST.bat runs every automated test (several
minutes) with the guard on.
