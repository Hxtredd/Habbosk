"""Keeping SK Assistant's data in one safe place.

Up to v1.9 the memory (data\\habbosk.db), settings and keys were stored in
the program folder: installing a new version into another folder started
with an empty memory. From v2.0 they live in the user profile
(%APPDATA%\\SK Assistant). On start:

  1. settings and keys of an older version are copied over (if missing);
  2. an older memory in the program folder is MERGED in (nothing is lost)
     and the old file is renamed '*.merged' so it is not merged twice;
  3. the starter questions (assets/seed_questions.json - answers the game
     revealed in the owner's recordings) are added once.
"""
from __future__ import annotations

import os
import shutil
from typing import List

from .config import CONFIG_PATH, DATA_DIR, LEGACY_CONFIG_PATH, LEGACY_DATA_DIR, app_dir

SEED_PATH = os.path.join(app_dir(), "assets", "seed_questions.json")
SEED_TAG = "seed_v1"


def migrate_legacy(memory) -> List[str]:
    """Bring data of older versions into DATA_DIR. Returns what was done."""
    notes: List[str] = []
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.abspath(LEGACY_DATA_DIR) == os.path.abspath(DATA_DIR):
        return notes
    old_keys = os.path.join(LEGACY_DATA_DIR, "keys.bin")
    new_keys = os.path.join(DATA_DIR, "keys.bin")
    if os.path.exists(old_keys) and not os.path.exists(new_keys):
        shutil.copy2(old_keys, new_keys)
        notes.append("API keys moved")
    if os.path.exists(LEGACY_CONFIG_PATH) and not os.path.exists(CONFIG_PATH):
        shutil.copy2(LEGACY_CONFIG_PATH, CONFIG_PATH)
        notes.append("settings moved")
    old_db = os.path.join(LEGACY_DATA_DIR, "habbosk.db")
    if os.path.exists(old_db):
        counts = memory.merge_from(old_db)
        os.replace(old_db, old_db + ".merged")
        notes.append(f"merged {counts['new'] + counts['updated']} questions from the old memory")
    return notes


def add_seed(memory, path: str = SEED_PATH) -> int:
    """Add the starter questions once. Returns how many were new."""
    if memory.get_meta(SEED_TAG) or not os.path.exists(path):
        return 0
    counts = memory.merge_from(path)
    memory.set_meta(SEED_TAG, "1")
    return counts["new"]
