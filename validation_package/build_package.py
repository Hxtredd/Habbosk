import hashlib, io, os, shutil, subprocess, sys, tarfile, zipfile
S = os.path.dirname(os.path.abspath(__file__))
REPO = "/home/user/priv"
SHA = subprocess.check_output(["git", "-C", REPO, "rev-parse", sys.argv[1]], text=True).strip()
assert SHA == "68b500f3d691f714425497ceb6eac3beac8967ac", SHA
TREE = subprocess.check_output(["git", "-C", REPO, "rev-parse", SHA + "^{tree}"], text=True).strip()
BR = subprocess.check_output(["git", "-C", REPO, "branch", "-r", "--contains", SHA], text=True)
NAME = "SK-Privacy-Validation-" + SHA[:7]
out = os.path.join(S, "build"); shutil.rmtree(out, ignore_errors=True)
root = os.path.join(out, NAME); os.makedirs(root)
tarbytes = subprocess.check_output(["git", "-C", REPO, "archive", "--prefix=source/", SHA])
tarfile.open(fileobj=io.BytesIO(tarbytes)).extractall(root)
shutil.copytree(os.path.join(S, "pkg", "tools"), os.path.join(root, "tools"), ignore=shutil.ignore_patterns("__pycache__"))
for f in os.listdir(os.path.join(S, "pkg")):
    p = os.path.join(S, "pkg", f)
    if os.path.isfile(p):
        shutil.copy(p, root)
sys.path.insert(0, os.path.join(root, "tools"))
import common
fp = subprocess.check_output([sys.executable, "-B", "-c",
    "import sys;sys.path.insert(0,'.');from puzzle_assistant.devtools import brain_windows_check as b;print(b.code_fingerprint())"],
    cwd=os.path.join(root, "source"), text=True).strip()
for d, ds, fs in os.walk(root):
    assert "__pycache__" not in ds and not [f for f in fs if f.endswith(".pyc")], d
files = common.file_list(os.path.join(root, "source"))
assert common.source_tree_sha(os.path.join(root, "source"), files) == TREE
with open(os.path.join(root, "MANIFEST.txt"), "w", encoding="utf-8", newline="\n") as m:
    m.write("# SK Brain privacy validation package - what is in source\\\n".replace("\\\n", "\n"))
    m.write(f"source commit: {SHA}\nsource branch: claude/brain-privacy-shareable\nsource tree: {TREE}\n")
    m.write(f"brain fingerprint: {fp}\nsource files: {len(files)}\n")
    for rel in files:
        m.write("FILE %s %s\n" % (common.sha256_file(os.path.join(root, "source", *rel.split("/"))), rel))
zp = os.path.join(S, f"Habbosk-privacy-validation-{SHA[:7]}.zip")
if os.path.exists(zp): os.remove(zp)
with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
    z.comment = f"SK Brain privacy validation; source commit {SHA}".encode()
    for d, ds, fs in os.walk(root):
        for f in sorted(fs):
            p = os.path.join(d, f)
            z.write(p, os.path.relpath(p, out))
print(zp, os.path.getsize(zp), "bytes; tree", TREE, "fingerprint", fp, "files", len(files))
print("zip sha256", hashlib.sha256(open(zp, "rb").read()).hexdigest())
