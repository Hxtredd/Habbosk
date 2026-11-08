# Signed updates

SK Assistant installs an update only if its manifest (`latest.json`) carries a
valid **Ed25519 signature** from a key listed in
`puzzle_assistant/update_keys.py`. The signed manifest names the package's
exact size and SHA-256, so the signature covers the package too. Anyone who
can change the GitHub repository, but does not have an offline private key,
cannot make SK install anything.

## What is checked, in order (nothing in the program folder changes before step 6)
1. `latest.json` is at most 64 KB and signed by a trusted, non-revoked key
   (over `"SK-Assistant update manifest v1\n"` + the exact signed text).
   Only the signed text is used; unsigned copies of the fields exist only for
   SK 3.0.
2. The signed values are sane: product, format, version, a `dist/*.zip`
   path, a SHA-256, and a size within the download limit. It must be newer
   than the installed version (no downgrades).
3. The download stops at the signed size, then size and SHA-256 must match.
4. Right before installing, the file is checked against the signed release
   again.
5. The ZIP is inspected before anything is unpacked. It is refused for:
   - too many files, or files too large (each, or in total);
   - suspicious compression (per file and overall), or overlapping entries;
   - unsafe names: `..`, absolute paths, drive letters, `\`, control
     characters, Windows device names such as `CON`, or names ending in `.`
     or a space;
   - links, encryption, or unknown compression;
   - two names that differ only in case.
   While unpacking, the real bytes are counted and every file's CRC is
   checked. Then the existing checks run: it is an SK package, it contains the
   signed version, and the program code compiles.
6. Only then does the existing transactional install run (journal, backup,
   verification, rollback and recovery at start-up). It is unchanged.

The limits live in `updater.Limits` (`updater.LIMITS`). They come from the
**installed** program, never from the update, and are several times what a
real release needs:

| Limit | Default | SK 3.0 package |
|---|---|---|
| ZIP size | 64 MB | 11.8 MB |
| Unpacked total | 160 MB | 15 MB |
| Files | 2,000 | 135 |
| One file | 32 MB | 2.3 MB |
| Compression ratio, per file over 64 KB | 100:1 | 7:1 at most |
| Compression ratio, overall | 20:1 | 1.3:1 |

## Keys
- **Two keys from the start:** a *primary* and a *backup*. Keep them in
  different places (for example two USB sticks, or an encrypted folder plus
  a password manager). Both public keys go into `TRUSTED_KEYS`.
- **Private keys never go into** either repository, the program folder, a
  release ZIP, a chat or a cloud session. The tools enforce part of this:
  - `keygen` refuses to write inside a git repository or an SK program
    folder, and never overwrites a file;
  - the tools refuse to read a key stored in such a place;
  - `publish_release.py` refuses to build if any packed file contains a
    private key;
  - a test checks that no tracked file contains one.
- On Windows, the file permission set by `keygen` does not restrict access.
  Keep key files on an encrypted drive (for example BitLocker) or removable
  media.
- The signing code is plain Python and not constant-time. Run it only on your
  own computer, ideally offline, never on a shared machine.

### Creating the keys (once, offline)
    python tools/update_signing.py keygen E:\sk-keys\primary.key
    python tools/update_signing.py keygen F:\sk-keys\backup.key

Each command prints a line such as `"0123456789abcdef": "<public key>",`.
Paste both lines into `TRUSTED_KEYS` in `puzzle_assistant/update_keys.py`
and commit that file. It holds public keys only.

### Publishing a release
    python tools/publish_release.py "notes" --key E:\sk-keys\primary.key
    python tools/update_signing.py verify

It refuses to build without trusted keys, with an untrusted or misplaced
key, or if a private key is among the files. `latest.json` is written only
once it has been signed and verified. Commit and push `latest.json` and
`dist/`.

## Rotating keys
A running SK trusts the keys in its **own** `update_keys.py`. A new key
therefore takes effect only after users have installed a release that lists
it.

**Planned rotation** (primary A is replaced by C; backup B stays):
1. Create C offline. Release N lists A, B and C, and is signed with A.
2. Releases after N are signed with **both A and C** for a while. Users
   still on older versions accept A; users on N or later accept either.
3. A later release moves A's id to `REVOKED_KEYS` (and removes it from
   `TRUSTED_KEYS`). From then on, signatures by A are refused.

**If a key is lost:** sign with the other key, and follow the same steps to
add a new second key.

**If a key is exposed:** immediately publish a release signed with the
other key that revokes the exposed one. Users who install it are protected.
Users who have not updated yet still trust the exposed key until they do;
that is unavoidable with a key list built into the program. This is why
there are two independent keys, and why the backup must stay offline.

**If both keys are lost:** SK can no longer be updated automatically. Users
must reinstall manually from the published ZIP. The fail-closed design
prefers this to accepting unsigned updates.

## Migration and when enforcement starts
- **SK 3.0 (all current installations):** has no signature checking and
  cannot be changed remotely. It reads the plain fields in `latest.json`, as
  before. Its one update to the first signed release is protected only by
  HTTPS and the checksum in `latest.json`. This is the single, unavoidable
  trust-on-first-use step. Publish that release promptly, so the window is
  short.
- **The first release containing this code (planned as 3.1) and every later
  one:** signatures are **mandatory from the start**. There is no grace
  period and no fallback to unsigned updates, and no version ever accepts
  unsigned manifests again. With an empty key list, SK refuses all updates.
- **Release gate:** the first signed release must not be published until
  `update_keys.py` contains the primary and backup public keys.
  `publish_release.py` enforces this. In this branch the list is
  deliberately empty: no production key has been created.
- **Rehearse first:**
  1. Install the candidate release into a test folder, with
     `SK_ASSISTANT_DATA` pointing at a separate data folder.
  2. Publish a signed test manifest and test package to a test location.
  3. Check that the test installation accepts them, and refuses an unsigned
     one.

## Known limitations
- **A freeze attack is possible.** Someone who controls the download
  location can keep serving an *old* signed manifest. That only withholds
  updates; downgrades are refused. Manifests do not expire.
- **SK 3.0's one update is not signature-checked** (see above).
- **Protection holds only while the keys stay private.** It does not cover
  someone who can write to the program folder on the user's PC.
