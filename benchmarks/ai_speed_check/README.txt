SK AI SPEED CHECK  (standalone, read-only)
==========================================

Measures how fast the Gemini AI answers SK Assistant's quiz questions, and
compares the model SK uses with the other current Gemini Flash-Lite models.
It is NOT part of SK Assistant and changes nothing in it.

RUN
  Put this folder anywhere (NOT inside the SK Assistant folder), then
  double-click AI_SPEED_CHECK.bat. It shows the plan and waits for Enter.
  Options (add after the .bat name in a Command Prompt):
    --quick                  only SK's current set-up (about 20 requests)
    --current-model <name>   the model SK shows under Settings > Test AI
    --full                   also try "thinking off" on the other models

WHAT IT READS (read-only)
  %APPDATA%\SK Assistant\keys.bin     your saved Gemini key (decrypted in memory)
  %APPDATA%\SK Assistant\config.json  only the "ai_model" setting

WHAT IT NEVER TOUCHES
  habbosk.db, atlas.db, knowledge.db, SK Assistant's program folder; it never
  writes into %APPDATA%\SK Assistant.

WHAT IT SENDS
  Only to generativelanguage.googleapis.com (Google Gemini): the model list
  request and 20 test questions per comparison (the same format SK uses),
  with your key in the request header - as SK Assistant does. Nothing else.

WHAT IT WRITES
  results\ai_speed_report_<date-time>.txt and .json next to this file.
  They contain timings, model names and right/wrong counts - never the key.

Delete the folder to remove it.
