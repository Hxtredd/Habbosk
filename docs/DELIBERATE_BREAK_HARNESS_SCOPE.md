# Deliberate-break harness - scope and impact (for review; NOT committed into the product)

**What it is.** A script that edits ONE line of a COPY of the Brain code to switch a protection
off, runs the tests that should notice, and reports CAUGHT or MISSED. It shows that the tests
really guard what they claim to.

**Where it exists today.**
- Development copies in the session scratchpad (six harnesses: B6 178 breaks, keystore 22,
  audience fix 10, privacy 22, shareable 49 - 281 breaks in all).
- A packaged, safer form for Windows in `validation_package/` on branch
  `claude/brain-privacy-validation-tools` (86c5b84): 81 breaks for the privacy work.
  This branch only ADDS a folder; it changes no Brain or test file.

**Safety properties of the packaged form.** Runs on a copy inside the package's test_area;
a baseline run of the unbroken copy must pass first; a break is "caught" only when a test's own
check fails (not when the code merely crashes); each break gets a fresh data/temp/home folder;
network connections are refused; the source tree is re-verified afterwards; exits non-zero on an
unexpected miss. 8 breaks are documented equivalents (the protection is duplicated elsewhere).

**Impact if committed under `tests/breaks/` in the product.**
- No effect on the running application (never imported by it).
- Adds about 1,400 lines of tooling the static guards and the regression manifest would have
  to know about; the existing process guard forbids indirect attribute access and unsafe file
  access, so the tooling must be written to pass it (the packaged form does).
- Breaks hard-code exact source lines, so any refactor of those lines needs the break list
  updated (a MISSED/SETUP error tells you).
- Running all breaks takes 6-7 minutes on Linux.

**What I propose, for your decision (not done).** Commit only the packaged, safe form under
`tests/breaks/`, run on demand (not in the normal suite), documented in the regression
manifest. Until you approve, it stays on the separate tooling branch.
