Tooling for the Windows privacy validation package (not part of the Brain code).
The package under test is built from an exact commit (68b500f3d691f714425497ceb6eac3beac8967ac)
with `git archive`; these files are added AROUND it (tools/, *.bat, START_HERE.txt) and never
inside source/. build_package.py expects pkg/ (tools, *.bat, START_HERE.txt) next to it - copy
this folder's contents into a folder named pkg and run:  python3 build_package.py <commit> <branch>
The zip is checked with tools/verify_package.py (recomputes the git tree id in pure Python).
