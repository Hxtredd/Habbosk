"""Step 1: prove the source in this package is exactly the commit named in MANIFEST.txt."""
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def read_manifest():
    head, files = {}, {}
    for line in open(os.path.join(common.PKG, "MANIFEST.txt"), encoding="utf-8"):
        line = line.rstrip("\n")
        if line.startswith("#") or not line.strip():
            continue
        if line.startswith("FILE "):
            _, sha, rel = line.split(" ", 2)
            files[rel] = sha
        else:
            k, _, v = line.partition(":")
            head[k.strip()] = v.strip()
    return head, files


def verify(quiet=False):
    head, files = read_manifest()
    actual = common.file_list(common.SOURCE)
    problems = []
    for d, dirs, fs in os.walk(common.SOURCE):          # compiled leftovers would run unchecked
        for x in dirs + fs:
            if os.path.islink(os.path.join(d, x)):
                problems.append("link in source (not allowed): "
                                + os.path.relpath(os.path.join(d, x), common.SOURCE))
            if x == "__pycache__" or x.endswith((".pyc", ".pyo")):
                problems.append("compiled file/folder in source (not allowed): "
                                + os.path.relpath(os.path.join(d, x), common.SOURCE))
    for rel in sorted(set(files) | set(actual)):
        if rel not in files:
            problems.append(f"EXTRA file not in the manifest: {rel}")
        elif rel not in actual:
            problems.append(f"MISSING file: {rel}")
        else:
            got = common.sha256_file(os.path.join(common.SOURCE, *rel.split("/")))
            if got != files[rel]:
                problems.append(f"CHANGED file: {rel}")
    tree = common.source_tree_sha(common.SOURCE, actual) if not problems else "n/a"
    if not problems and tree != head["source tree"]:
        problems.append(f"git tree id differs: package {tree} != manifest {head['source tree']}")
    if not quiet:
        print("Commit named in the manifest :", head["source commit"])
        print("Branch                       :", head["source branch"])
        print("Source files checked         :", len(actual))
        print("Git tree id (recomputed)     :", tree)
        print("Git tree id (manifest)       :", head["source tree"])
    if problems:
        for p in problems[:20]:
            print("  PROBLEM:", p)
        print("PACKAGE CHECK: FAILED - do not trust results from this folder.")
        return False, head
    if not quiet:
        print("PACKAGE CHECK: OK - the source is byte-for-byte the tree of commit",
              head["source commit"])
        print("(This file list ships inside the zip. For an outside check compare the tree id"
              " above with the one I give you in the chat.)")
    return True, head


if __name__ == "__main__":
    ok, _ = verify()
    sys.exit(0 if ok else 1)
