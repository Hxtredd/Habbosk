"""User-editable settings, stored in config.json next to the program."""
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


CONFIG_PATH = os.path.join(app_dir(), "config.json")
DATA_DIR = os.path.join(app_dir(), "data")
DEBUG_DIR = os.path.join(app_dir(), "debug")


@dataclass
class Config:
    # --- capture ---------------------------------------------------------
    monitor: int = 1                    # 1 = primary monitor, 0 = all monitors
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

    # --- UI --------------------------------------------------------------
    window_geometry: str = "380x300+40+40"
    opacity: float = 0.95
    debug_save: bool = False            # save every analysed frame to ./debug
    config_version: int = 2

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if int(data.get("config_version", 1)) < 2:
                    # v1 forced exactly 8 pieces; the game also uses 7
                    data.pop("expected_pieces", None)
                    data["config_version"] = 2
                for k, v in data.items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)
            except Exception as exc:  # corrupted file -> defaults
                print(f"[config] could not read config.json: {exc}")
        return cfg

    def save(self) -> None:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(asdict(self), fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            print(f"[config] could not save config.json: {exc}")
