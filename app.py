"""Puzzle Answer Assistant - start here (python app.py)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _stop(message: str) -> None:
    """Explain why SK cannot start, then exit without starting it.
    (SK_ASSISTANT_NO_DIALOG=1 prints only - used by the tests.)"""
    print(message, file=sys.stderr)
    if not os.environ.get("SK_ASSISTANT_NO_DIALOG"):
        try:
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, message, "SK Assistant", 0x10)
            else:
                import tkinter
                from tkinter import messagebox
                root = tkinter.Tk()
                root.withdraw()
                messagebox.showerror("SK Assistant", message)
                root.destroy()
        except Exception:
            pass
    sys.exit(3)


# First of all: finish or undo an update that was interrupted. If that cannot
# be completed, SK must not run on a possibly mixed set of program files.
try:
    from puzzle_assistant.updater import startup_check
except Exception as exc:
    if os.path.exists(os.path.join(HERE, "_update_journal.json")):
        _stop("SK Assistant cannot start yet.\n\nAn update was interrupted, and the code that "
              f"restores the previous version could not be loaded ({type(exc).__name__}). "
              "Nothing has been deleted: the information needed to restore it is kept in "
              f"_update_journal.json and the _update_backup folder in\n{HERE}\n"
              "Your settings and personal data are not affected. Leave the folder as it is "
              "and ask for help.")
    startup_check = None
if startup_check is not None:
    problem = startup_check(HERE)
    if problem:
        _stop(problem)

from puzzle_assistant.main import main  # noqa: E402

if __name__ == "__main__":
    main()
