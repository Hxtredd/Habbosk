"""One B6 upgrade in its own process, so a test can cut the power at a point
(SK_TEST_CRASH_AT=<point>).   python b6_child.py upgrade <data_dir> <host key hex>"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from puzzle_assistant.brain.upgrade import upgrade_library  # noqa: E402

if __name__ == "__main__":
    if sys.argv[1] == "upgrade":
        print(upgrade_library(sys.argv[2], bytes.fromhex(sys.argv[3]))["upgrade_id"])
