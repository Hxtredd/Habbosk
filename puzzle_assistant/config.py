"""User-editable settings (config.json) and where SK Assistant keeps its data.

Learned questions, settings and API keys live in the user profile
(Windows: %APPDATA%\\SK Assistant), not in the program folder, so they survive
installing a new version into a different folder. Data from older versions
(kept in <program folder>\\data) is merged in on start - see storage.py.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from typing import List, Optional


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def user_data_dir() -> str:
    """%APPDATA%\\SK Assistant on Windows (override: SK_ASSISTANT_DATA)."""
    env = os.environ.get("SK_ASSISTANT_DATA")
    if env:
        return env
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "SK Assistant")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "sk-assistant")


DATA_DIR = user_data_dir()
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
LEGACY_DATA_DIR = os.path.join(app_dir(), "data")          # versions up to 1.9
LEGACY_CONFIG_PATH = os.path.join(app_dir(), "config.json")
DEBUG_DIR = os.path.join(app_dir(), "debug")                # snapshots stay next to the program


@dataclass
class Config:
    # --- capture ---------------------------------------------------------
    monitor: int = 0                    # 0 = all monitors, 1 / 2 ... = only that one
    region: Optional[List[int]] = None  # [left, top, width, height] screen px, or None = auto
    poll_interval_ms: int = 100         # screen check interval while a puzzle is visible
    idle_poll_ms: int = 150             # screen check interval while waiting

    # --- OCR -------------------------------------------------------------
    tesseract_cmd: str = ""             # empty = auto-detect
    tessdata_dir: str = ""              # empty = ./tessdata if it exists
    language: str = "spa"
    ocr_upscale: float = 2.0            # cards are upscaled before OCR
    ocr_threads: int = 8
    min_confidence: float = 60.0        # below this a token is "uncertain"

    # --- puzzle ----------------------------------------------------------
    expected_pieces: int = 0            # 0 = auto (the game uses 7 or 8 pieces)
    read_names: bool = True             # OCR piece names (display only)
    require_layout: bool = True         # only accept the game's card layout
    disappear_after_ms: int = 700       # puzzle gone this long -> reset display

    # --- what SK Assistant helps with -------------------------------------
    assistant_on: bool = True           # master on/off switch
    weapon_puzzle: bool = True          # "Montar un arma"
    money_puzzle: bool = True           # "Cuenta el dinero"
    questions: bool = True              # quiz questions (library, general knowledge)
    question_mode: str = "auto"         # "auto" = detect by itself, "manual" = on Scan

    # --- AI and internet (API keys are stored encrypted, never here) -------
    ai_provider: str = "none"           # none | gemini | anthropic | openai
    paid_api: bool = False              # never pay unless switched on
    monthly_limit: float = 0.0          # USD per month for paid use (0 = nothing)
    web_search: bool = False
    search_provider: str = "none"       # none | tavily | brave

    # --- UI --------------------------------------------------------------
    always_on_top: bool = True
    text_scale: float = 1.0             # 0.8 .. 1.6
    window_geometry: str = "440x360+40+40"
    opacity: float = 0.95
    overlay: bool = True                # draw step numbers on the puzzle cards
    debug_save: bool = False            # save every analysed frame to ./debug
    config_version: int = 3

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        # settings of an older version (program folder) are taken over once
        path = CONFIG_PATH if os.path.exists(CONFIG_PATH) else LEGACY_CONFIG_PATH
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                version = int(data.get("config_version", 1))
                if version < 2:
                    # v1 forced exactly 8 pieces; the game also uses 7
                    data.pop("expected_pieces", None)
                if version < 3:
                    # v1/v2 only watched the first monitor: search all of them
                    data["monitor"] = 0
                data["config_version"] = cls.config_version
                for k, v in data.items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)
            except Exception as exc:  # corrupted file -> defaults
                print(f"[config] could not read config.json: {exc}")
        return cfg

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(asdict(self), fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            print(f"[config] could not save config.json: {exc}")
