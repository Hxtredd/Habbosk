"""Run ONE B5.2 operation in its own process, so a test can cut the power
(SK_TEST_CRASH_AT=<point> makes the process stop dead at that point).

  python b52_child.py withdraw <root> <hk hex> <lk hex> <user id> <source id>
  python b52_child.py split <src db> <data dir> <hk hex> <lk hex>
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.brain.library import Library, mint_grant  # noqa: E402
from puzzle_assistant.brain.split import split_file  # noqa: E402

if __name__ == "__main__":
    what = sys.argv[1]
    if what == "withdraw":
        root, hk, lk, uid, sid = sys.argv[2:7]
        hk, lk = bytes.fromhex(hk), bytes.fromhex(lk)
        lib = Library(root, hk, lk)
        s = lib.open_session(mint_grant(hk, lib.installation_id, lib.epoch, uid, "user"))
        print(s.withdraw_source(int(sid)))
        s.close()
    elif what == "split":
        src, data_dir, hk, lk = sys.argv[2:6]
        print(split_file(src, data_dir, bytes.fromhex(hk), bytes.fromhex(lk))["migration_id"])
