"""Runs unittest in a child process with the network blocked. Used by the break runner."""
import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

if __name__ == "__main__":
    root = sys.argv[1]
    common.block_network()
    if not common.network_selftest():
        print('network block is not working in the test process', file=sys.stderr)
        sys.exit(3)
    os.chdir(root)
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "tests"))
    unittest.main(module=None, argv=["unittest"] + sys.argv[2:], verbosity=0)
