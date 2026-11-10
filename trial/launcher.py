"""Start the SK Assistant TRIAL: the complete development build (AI changes
A-D) in its own folder with its own data, next to - never over - the
everyday SK Assistant.

What happens, in this order:
  1. Find the normal SK Assistant's data folder (%APPDATA%\\SK Assistant) and
     make sure the trial folder is completely separate from it.
  2. First start only: copy the normal SK's SETTINGS (config.json, read-only;
     never keys.bin) into trial_data\\, with "check for updates" switched off.
  3. Install the isolation guard (guard.py): from now on nothing in the
     trial can open, create, change or delete anything in the normal data
     folder, except READING keys.bin.
  4. Point SK Assistant at trial_data\\ and verify every database, settings and
     log path is inside it - otherwise the trial does not start.
  5. The Gemini key is read from the normal SK's keys.bin when needed
     (read-only, in memory, as the normal app does). Saving keys is switched
     off in the trial.
  6. Updates are switched off (they would replace the trial with v3.3).
  7. Start the normal SK Assistant window, titled "SK Assistant - TRIAL".

Only one of the two can run at a time (the same lock as the normal app).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import guard  # noqa: E402

TITLE = "SK Assistant - TRIAL (test version)"
RUNS_LOG = os.path.join(guard.TRIAL_DATA, "trial_runs.log")


def build_info() -> dict:
    try:
        with open(os.path.join(HERE, "BUILD.json"), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"commit": "unknown (not built with tools/build_trial.py)"}


def note(msg: str) -> None:
    line = f"{_dt.datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line)
    try:
        with open(RUNS_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def seed_settings(normal_dir: str) -> str:
    """First start: the normal SK's settings, read-only; updates off; key never copied."""
    path = os.path.join(guard.TRIAL_DATA, "config.json")
    if os.path.exists(path):
        return "kept"
    data, how = {}, "defaults (no settings found in the normal SK)"
    src = os.path.join(normal_dir, "config.json")
    try:
        with open(src, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            data, how = loaded, "copied from the normal SK (read-only)"
    except (OSError, ValueError):
        pass
    for k in [k for k in data if "key" in k.lower() and k != "ai_timing_log"]:
        data.pop(k, None)                       # keys never live in config.json; be sure
    data["check_updates"] = False
    data["ai_timing_log"] = True
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    return how


def prepare():
    """Steps 1-6. Returns (cfg, guard, context). Raises guard.IsolationError."""
    protected = guard.normal_data_dirs()
    normal_dir = protected[0]
    guard.check_layout(protected)
    os.makedirs(guard.TRIAL_DATA, exist_ok=True)
    how = seed_settings(normal_dir)
    keys_path = os.path.join(normal_dir, "keys.bin")
    g = guard.Guard(protected, allow_read=[keys_path],
                    log_path=os.path.join(guard.TRIAL_DATA, "blocked_access.log"))
    g.install()

    os.environ["SK_ASSISTANT_DATA"] = guard.TRIAL_DATA
    if guard.TRIAL_ROOT not in sys.path:
        sys.path.insert(0, guard.TRIAL_ROOT)
    if "puzzle_assistant" in sys.modules:
        raise guard.IsolationError("SK Assistant was loaded before the trial set its data folder")
    from puzzle_assistant import config, secrets, updater
    from puzzle_assistant.qa import memory
    from puzzle_assistant.world_atlas import db as atlas_db
    from puzzle_assistant import diagnostics  # noqa: F401  (A: the timing log)
    paths = {"data folder": config.DATA_DIR, "settings": config.CONFIG_PATH,
             "question memory": memory.DB_PATH, "World Atlas": atlas_db.DB_PATH,
             "key file setting": secrets.KEYS_PATH}
    for what, p in paths.items():
        if not guard.inside(p, guard.TRIAL_DATA) or g.is_protected(p):
            raise guard.IsolationError(f"the trial's {what} would not be in trial_data ({p})")

    # 5. the saved Gemini key: read from the normal SK, read-only, never saved
    def get_key(name, path=None):
        return secrets._load(keys_path).get(name) or None

    def set_key(name, value, path=None):
        print("[trial] keys are read from your normal SK Assistant and are not saved in the trial")

    secrets.get_key, secrets.set_key = get_key, set_key
    secrets.has_key = lambda name, path=None: bool(get_key(name))

    # 6. no updates in the trial
    def no_updates(*a, **k):
        raise RuntimeError("updates are switched off in the SK trial")
    updater.check = lambda *a, **k: None
    updater.download = updater.install = updater.update_packages = updater.restart = no_updates

    cfg = config.Config.load()
    cfg.check_updates = False
    cfg.ai_timing_log = True
    ctx = {"normal_dir": normal_dir, "settings": how, "keys_path": keys_path, "paths": paths,
           "build": build_info()}
    return cfg, g, ctx


def create_app(cfg):
    from puzzle_assistant.ui import App
    app = App(cfg)
    app.root.title(TITLE)
    return app


def wait_for_threads(app, limit: float = 3.0) -> float:
    """Wait (at most `limit` s) until the screen-reading threads have really
    ended after the window closed. Found while testing the trial (also in
    v3.3, not caused by A-D): if Python exits while such a thread is still in
    OpenCV / OCR, the process can abort at exit ("terminate called without an
    active exception"). The threads end within ~0.3 s. Thread.is_alive() can't
    be used: Engine keeps an Event named _stop, which hides Thread._stop."""
    import time
    t0 = time.monotonic()
    threads = [t for t in (getattr(app, "engine", None), getattr(app, "explorer", None)) if t is not None]
    while time.monotonic() - t0 < limit:
        frames = sys._current_frames()
        if not any(t.ident in frames for t in threads if t.ident is not None):
            break
        time.sleep(0.05)
    return time.monotonic() - t0


def run() -> int:
    print("SK Assistant TRIAL - development build, separate from your normal SK Assistant.\n")
    try:
        cfg, g, ctx = prepare()
    except guard.IsolationError as exc:
        print(f"\nThe trial did NOT start: {exc}\nNothing was changed.")
        return 3
    note(f"start  build {ctx['build'].get('commit', '?')}  python {sys.version.split()[0]}  "
         f"settings: {ctx['settings']}")
    print(f"Trial data folder: {guard.TRIAL_DATA}")
    print(f"Protected (never touched): {', '.join(g.protected)}\n")
    from puzzle_assistant.capture import enable_dpi_awareness
    from puzzle_assistant.main import _single_instance
    enable_dpi_awareness()
    if not _single_instance():
        note("not started: SK Assistant (normal or trial) is already running")
        print("SK Assistant is already running. Close it first (normal or trial), then start the trial again.")
        return 4
    code = 0
    try:
        app = create_app(cfg)
        app.run()
        waited = wait_for_threads(app)
        note(f"window closed; background threads ended after {waited:.2f} s")
    except Exception as exc:
        code = 1
        note(f"stopped with an error: {exc.__class__.__name__}: {exc}")
        raise
    finally:
        note(f"stop   blocked accesses to the normal SK data: {g.blocked}")
    print("\nTrial closed. Your normal SK Assistant was not touched; start it as usual.")
    print("TRIAL_SUMMARY.bat shows the AI timing summary of the trial.")
    return code


if __name__ == "__main__":
    sys.exit(run())
