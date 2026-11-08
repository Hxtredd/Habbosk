"""Puzzle Answer Assistant - start here (python app.py)."""
try:    # first of all: finish or undo an update that was interrupted (see updater.recover)
    from puzzle_assistant.updater import recover_at_start
    recover_at_start()
except Exception:   # never stop SK from starting
    pass

from puzzle_assistant.main import main  # noqa: E402

if __name__ == "__main__":
    main()
