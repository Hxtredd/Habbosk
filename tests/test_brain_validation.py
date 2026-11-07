"""B2 final validation: regression tests for the problems found in it.

  * read-only opening broke on folder names with '#', '?' or '%' (stray
    empty file created, 0 rows read, empty bench safety backup)
  * FTS5 breaking while running crashed search and writes; an FTS5 table
    written without FTS5 (another PC) could stay out of date
  * a write that failed part-way (file locked by another program, rule
    check after a nested write) was committed later by the next write and
    kept other programs locked out
  * someone else's private source / section could be cited; anyone could
    add sections to any source; non-moderators could link shared claims
    (flipping them to 'conflicting') and add evidence to them
"""
import ntpath
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from puzzle_assistant.brain import Brain  # noqa: E402
from puzzle_assistant.brain import sqlite_ro  # noqa: E402
from puzzle_assistant.brain.adapters import ReadOnlyDB  # noqa: E402
from puzzle_assistant.brain.store import BrainError, KnowledgeStore  # noqa: E402
from puzzle_assistant.devtools import bench_safety  # noqa: E402

AWKWARD_NAMES = tuple(
    n for n in ("SK Assistant", "José's #1 folder", "what? folder", "100% data", "a%20b",
                "ünï & (x) [y] {z};")
    if os.name != "nt" or not set(n) & set('<>:"|?*'))   # '?' is not allowed on Windows


class ReadOnlyPathTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _db_in(self, name):
        d = os.path.join(self.root, name)
        os.makedirs(d)
        path = os.path.join(d, "knowledge.db")
        b = Brain(path, "", "")
        for i in range(5):
            b.learn(f"claim {i}", "user")
        b.close()
        return path

    def test_awkward_folder_names_read_the_real_file_and_create_nothing(self):
        for name in AWKWARD_NAMES:
            with self.subTest(folder=name):
                path = self._db_in(name)
                before = set(os.listdir(self.root))
                ro = ReadOnlyDB(path)
                n = len(ro.rows("SELECT * FROM claims"))
                ro.close()
                self.assertEqual(n, 5)
                self.assertEqual(set(os.listdir(self.root)), before, "stray file created")

    def test_bench_backup_copies_the_real_database(self):
        for name in AWKWARD_NAMES:
            with self.subTest(folder=name):
                path = self._db_in(name)
                bk = bench_safety.backup_database(path)
                con = sqlite3.connect(bk)
                try:
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM claims").fetchone()[0], 5)
                finally:
                    con.close()
                self.assertIsNotNone(bench_safety.schema_version_of(path))

    def test_read_only_never_creates_a_missing_file(self):
        missing = os.path.join(self.root, "#nope", "x.db")
        with self.assertRaises(sqlite3.OperationalError):
            sqlite_ro.connect_readonly(missing)
        self.assertFalse(os.path.exists(missing))
        self.assertEqual(os.listdir(self.root), [])

    def test_windows_style_paths_become_valid_uris(self):
        # String form only: whether Windows' SQLite opens them is checked by
        # the on-PC check (BRAIN_WINDOWS_CHECK.bat), not here.
        with mock.patch.object(sqlite_ro.os.path, "abspath", ntpath.abspath):
            self.assertEqual(
                sqlite_ro.readonly_uri(r"C:\Users\José #1\AppData\Roaming\SK Assistant\k.db"),
                "file:///C:/Users/Jos%C3%A9%20%231/AppData/Roaming/SK%20Assistant/k.db?mode=ro")
            self.assertEqual(sqlite_ro.readonly_uri(r"\\server\share\k.db"),
                             "file:////server/share/k.db?mode=ro")


class FtsFailureTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "k.db")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_fts_table_breaking_while_running_falls_back(self):
        b = Brain(self.path, "", "")
        b.learn("The Demo Garden has room ID 900001.", "user", "Demo Garden")
        q = "room ID of the Demo Garden"
        want = [h.text for h in b.search(q)]
        self.assertTrue(want)
        b.store._db.execute("DROP TABLE claim_fts")
        b.store._db.commit()
        self.assertEqual([h.text for h in b.search(q)], want)          # no crash, same answer
        self.assertFalse(b.store.fts)
        b.learn("The Demo Plaza has room ID 900002.", "user", "Demo Plaza")   # writes still work
        self.assertEqual(b.search("room ID of the Demo Plaza")[0].text,
                         "The Demo Plaza has room ID 900002.")
        b.close()
        b = Brain(self.path, "", "")                                    # next start: rebuilt
        self.assertTrue(b.store.fts)
        self.assertEqual(b.store._db.execute("SELECT COUNT(*) FROM claim_fts").fetchone()[0], 2)
        for q in ("room ID of the Demo Plaza", "room ID of the Demo Garden"):
            self.assertEqual([h.text for h in b.search(q)],
                             [h.text for h in b.search(q, use_fts=False)])
        b.close()

    def test_fts_table_breaking_before_a_write_falls_back(self):
        b = Brain(self.path, "", "")
        b.learn("The Demo Garden has room ID 900001.", "user", "Demo Garden")
        b.store._db.execute("DROP TABLE claim_fts")
        b.store._db.commit()
        c = b.learn("The Demo Plaza has room ID 900002.", "user", "Demo Plaza")   # first touch
        self.assertFalse(b.store.fts)
        self.assertEqual(b.claim(c)["text"], "The Demo Plaza has room ID 900002.")
        self.assertEqual(b.search("room ID of the Demo Plaza")[0].text,
                         "The Demo Plaza has room ID 900002.")
        b.close()
        b = Brain(self.path, "", "")
        self.assertEqual(b.store._db.execute("SELECT COUNT(*) FROM claim_fts").fetchone()[0], 2)
        b.close()

    def test_changes_made_without_fts5_are_picked_up_later(self):
        # e.g. knowledge.db used on a PC whose SQLite has no FTS5, then back
        b = Brain(self.path, "", "")
        c = b.learn("Alpha beta gamma.", "user")
        b.close()
        with mock.patch.object(KnowledgeStore, "_setup_fts", lambda self: False):
            b = Brain(self.path, "", "")
            self.assertFalse(b.store.fts)
            b.correct(c, "Lighthouse beacon flashes nightly.")         # same claim count
            b.close()
        b = Brain(self.path, "", "")
        self.assertTrue(b.store.fts)
        fts = [h.text for h in b.search("lighthouse beacon")]
        self.assertEqual(fts, ["Lighthouse beacon flashes nightly."])
        self.assertEqual(fts, [h.text for h in b.search("lighthouse beacon", use_fts=False)])
        b.close()


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "k.db")
        self.b = Brain(self.path, "", "")

    def tearDown(self):
        self.b.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _status(self, cid):
        con = sqlite3.connect(self.path, timeout=1)
        try:
            return con.execute("SELECT status FROM claims WHERE id=?", (cid,)).fetchone()[0]
        finally:
            con.close()

    def test_a_write_that_cannot_commit_leaves_nothing_behind(self):
        c = self.b.learn("The Demo Garden has room ID 900001.", "user", "Demo Garden")
        self.b.store._db.execute("PRAGMA busy_timeout = 200")
        reader = sqlite3.connect(self.path, timeout=0.2)   # another program, mid-read
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM claims").fetchall()
        with self.assertRaises(sqlite3.OperationalError):
            self.b.review(c, "rejected")
        reader.rollback()
        reader.close()
        self.assertFalse(self.b.store._db.in_transaction)
        other = sqlite3.connect(self.path, timeout=0.2)    # not locked out any more
        other.execute("SELECT COUNT(*) FROM claims").fetchone()
        other.close()
        self.b.learn("Unrelated later claim.", "user")
        self.assertEqual(self._status(c), "submitted")     # the failed review never happens
        self.assertEqual([e["event"] for e in self.b.explain(c)["events"]], ["created"])

    def test_nested_write_failing_late_is_rolled_back_whole(self):
        orig = self.b.learn("The Welcome Hall is open all day.", "user", "Welcome Hall")
        real = self.b.store._event

        def failing(cid, event, *a, **k):
            if event == "correction_received":
                raise sqlite3.OperationalError("disk I/O error")
            return real(cid, event, *a, **k)
        n_before = len(self.b.store.claims(limit=999))
        with mock.patch.object(self.b.store, "_event", failing):
            with self.assertRaises(sqlite3.OperationalError):
                self.b.propose_correction(orig, "The Welcome Hall closes at night.")
        self.b.learn("Unrelated later claim.", "user")
        texts = [c["text"] for c in self.b.store.claims(limit=999)]
        self.assertNotIn("The Welcome Hall closes at night.", texts)
        self.assertEqual(len(texts), n_before + 1)

    def test_failed_open_closes_the_file(self):
        opened = []
        real_connect = sqlite3.connect

        def spy(*a, **k):
            con = real_connect(*a, **k)
            opened.append(con)
            return con
        bad = os.path.join(self.dir, "bad.db")
        with mock.patch("puzzle_assistant.brain.store.sqlite3.connect", spy), \
                mock.patch.object(KnowledgeStore, "_migrate",
                                  side_effect=sqlite3.OperationalError("disk I/O error")):
            with self.assertRaises(sqlite3.OperationalError):
                KnowledgeStore(bad)
        self.assertEqual(len(opened), 1)
        with self.assertRaises(sqlite3.ProgrammingError):   # closed: Windows can delete it
            opened[0].execute("SELECT 1")


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.b = Brain(os.path.join(self.dir, "k.db"), "", "")
        st = self.b.store
        self.ana = self.b.local_user
        self.luis = st.add_contributor("Luis", "external_user")
        self.mod = st.add_contributor("Mod", "moderator")
        self.priv = self.b.add_source("document", title="Ana's diary: meet Leo at 9",
                                      reference="private://ana/diary", private=True,
                                      raw_text="secret")
        self.priv_sec = self.b.add_section(self.priv, "secret paragraph", "Diary")

    def tearDown(self):
        self.b.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _shared(self, text, ref):
        c = self.b.learn(text, "user", "Taxi", by=self.mod)
        s = self.b.add_source("guide_page", title="Guide", reference=ref, by=self.mod)
        self.b.add_evidence(c, "direct", s, by=self.mod)
        self.b.review(c, "verified", reviewer=self.mod)
        self.b.propose_share(c, by=self.mod)
        self.b.review(c, "verified", reviewer=self.mod)  # moderator's own review
        self.b.approve_share(c, self.mod)
        return c

    def test_someone_elses_private_source_cannot_be_cited(self):
        mine = self.b.learn("The Spa is on Avenida Espumia.", "user", "Spa", by=self.luis)
        with self.assertRaises(BrainError) as private:
            self.b.add_evidence(mine, "direct", self.priv, by=self.luis)
        with self.assertRaises(BrainError) as missing:
            self.b.add_evidence(mine, "direct", 999, by=self.luis)
        # same answer as for a source that doesn't exist: its existence isn't revealed
        self.assertEqual(str(private.exception).replace(str(self.priv), "N"),
                         str(missing.exception).replace("999", "N"))
        shown = self.b.explain(mine, viewer=self.luis)["evidence"]
        self.assertFalse(any("diary" in (e["title"] or "") for e in shown))
        with self.assertRaises(BrainError):                   # nor verify with it
            self.b.review(mine, "verified", reviewer=self.luis)
        own = self.b.learn("Leo is at the fountain at 9.", "user")   # the owner still can
        self.b.add_evidence(own, "direct", self.priv)

    def test_someone_elses_private_section_cannot_be_attached(self):
        with self.assertRaises(BrainError):
            self.b.learn("x", "user", section_id=self.priv_sec, by=self.luis)
        self.b.learn("x", "user", section_id=self.priv_sec)   # owner: fine

    def test_only_the_source_owner_adds_sections(self):
        with self.assertRaises(BrainError):
            self.b.add_section(self.priv, "planted text", by=self.luis)
        pub = self.b.add_source("guide_page", title="Guide", reference="g://1")
        with self.assertRaises(BrainError):                   # public source, still Ana's
            self.b.add_section(pub, "planted text", by=self.luis)
        self.b.add_section(pub, "real text")

    def test_regular_users_cannot_change_shared_knowledge(self):
        s1 = self._shared("The taxi costs 5.", "g://a")
        s2 = self._shared("The taxi is free.", "g://b")
        for kind in ("contradicts", "duplicate_of", "supersedes"):
            with self.assertRaises(BrainError):
                self.b.link(s1, s2, kind, by=self.luis)
        page = self.b.add_source("guide_page", title="Luis's page", reference="l://p",
                                 by=self.luis)
        for kind in ("assertion", "direct", "confirmation"):
            with self.assertRaises(BrainError):
                self.b.add_evidence(s1, kind, page, by=self.luis)
        self.assertEqual((self.b.claim(s1)["status"], self.b.claim(s2)["status"]),
                         ("verified", "verified"))
        self.assertEqual(len(self.b.explain(s1)["evidence"]), 1)
        # moderators can; corrections from others still go through propose_correction
        self.b.link(s1, s2, "contradicts", by=self.mod)
        self.assertEqual(self.b.claim(s1)["status"], "conflicting")
        new = self.b.propose_correction(s1, "The taxi costs 6.", by=self.luis)
        self.assertEqual(self.b.claim(new, viewer=self.luis)["scope"], "personal")

    def test_owner_still_supports_their_own_proposal(self):
        c = self.b.learn("The mine has 3 levels.", "user", "Mine", by=self.luis)
        self.b.propose_share(c, by=self.luis)
        shot = self.b.add_source("screenshot", title="mine.png", reference="s://1", by=self.luis)
        self.b.add_evidence(c, "direct", shot, by=self.luis)
        with self.assertRaises(BrainError):                   # but not someone else's proposal
            self.b.add_evidence(c, "assertion", by=self.ana)

    def test_correction_keeps_only_a_section_the_proposer_may_cite(self):
        page = self.b.add_source("guide_page", title="Guide", reference="g://2", by=self.mod)
        pub_sec = self.b.add_section(page, "public paragraph", by=self.mod)
        mod_priv = self.b.add_source("document", title="Mod notes", reference="m://n",
                                     private=True, by=self.mod)
        mod_sec = self.b.add_section(mod_priv, "mod's private note", by=self.mod)
        a = self._shared("The taxi costs 5.", "g://c")
        self.b.store._db.execute("UPDATE claims SET section_id=? WHERE id=?", (pub_sec, a))
        b = self._shared("The taxi is free.", "g://d")
        self.b.store._db.execute("UPDATE claims SET section_id=? WHERE id=?", (mod_sec, b))
        na = self.b.propose_correction(a, "The taxi costs 6.", by=self.luis)
        nb = self.b.propose_correction(b, "The taxi costs 1.", by=self.luis)
        self.assertEqual(self.b.claim(na, viewer=self.luis)["section_id"], pub_sec)
        self.assertIsNone(self.b.claim(nb, viewer=self.luis)["section_id"])

    def test_hostile_search_text_is_harmless(self):
        self.b.learn("Ana's private room ID 123456.", "user", "Secret Room")
        for q in ('") OR 1=1 --', "' OR '1'='1", "*", "NEAR(a b)", 'Secret" OR "Room',
                  "Secret Room AND 1", "%", "_", "\x00", "a" * 5000,
                  "ID; DROP TABLE claims;--", "Secret Room room ID"):
            with self.subTest(q=q[:20]):
                for fts in (True, False):
                    hits = self.b.search(q, viewer=self.luis, use_fts=fts)
                    self.assertFalse([h for h in hits if "private" in h.text])
            for t in ("%", "_", "' OR 1=1 --"):
                self.assertEqual(self.b.store.claims(text_like=t, viewer=self.luis), [])
        self.assertTrue(self.b.search("Secret Room room ID"))  # the owner finds it


class SharingAuditTests(unittest.TestCase):
    """Second audit round: sharing, history, moderators, evidence quality."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.b = Brain(os.path.join(self.dir, "k.db"), "", "")
        st = self.b.store
        self.ana = self.b.local_user
        self.luis = st.add_contributor("Luis", "external_user")
        self.mod = st.add_contributor("Mod", "moderator")

    def tearDown(self):
        self.b.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _evidence(self, c, who, ref):
        s = self.b.add_source("screenshot", title="shot", reference=ref, by=who)
        self.b.add_evidence(c, "direct", s, by=who)

    def test_personal_history_stays_with_its_owner_after_sharing(self):
        c = self.b.learn("Meet me at my house, room 4471023, password tulip.", "user", "Spa")
        self.b.correct(c, "The Spa is on Avenida Espumia.", reason="removed my house 4471023")
        self._evidence(c, self.ana, "s://spa")
        self.b.propose_share(c)
        self.b.review(c, "verified", reviewer=self.mod)
        self.b.approve_share(c, self.mod)
        for who in (self.luis, self.mod):
            e = self.b.explain(c, viewer=who)
            self.assertEqual(e["versions"], [])
            blob = repr(e)
            for secret in ("4471023", "tulip", "house"):
                self.assertNotIn(secret, blob)
            self.assertEqual([x["event"] for x in e["events"]],
                             ["share_proposed", "reviewed", "share_approved"])
        own = self.b.explain(c)                              # the owner keeps everything
        self.assertEqual(own["versions"][0]["text"],
                         "Meet me at my house, room 4471023, password tulip.")
        self.assertEqual(own["events"][0]["event"], "created")

    def test_sharing_needs_a_moderator_review_of_the_proposal(self):
        c = self.b.learn("The mine has 3 levels.", "user", "Mine", by=self.luis)
        self._evidence(c, self.luis, "s://mine")
        self.b.review(c, "verified", reviewer=self.luis)     # the owner's own verification
        self.b.propose_share(c, by=self.luis)
        with self.assertRaises(BrainError):
            self.b.approve_share(c, self.mod)
        self.assertEqual(self.b.claim(c, viewer=self.mod)["scope"], "shared_candidate")
        self.b.review(c, "verified", reviewer=self.mod)
        self.b.approve_share(c, self.mod)
        self.assertEqual(self.b.claim(c, viewer=self.ana)["scope"], "shared")

    def test_a_review_before_the_proposal_does_not_count(self):
        c = self.b.learn("The bank opens at 9.", "user", "Bank", by=self.mod)
        self._evidence(c, self.mod, "s://bank")
        self.b.review(c, "verified", reviewer=self.mod)      # while personal
        self.b.propose_share(c, by=self.mod)
        with self.assertRaises(BrainError):
            self.b.approve_share(c, self.mod)

    def test_moderators_review_proposals_but_never_find_them_by_search(self):
        p = self.b.learn("Leo's secret room is 5550001.", "user", "Leo", by=self.luis)
        self.b.propose_share(p, by=self.luis)
        self.assertIsNotNone(self.b.claim(p, viewer=self.mod))      # reviewable
        self.assertEqual(self.b.search("Leo secret room 5550001", viewer=self.mod), [])
        self.assertEqual(self.b.stats(viewer=self.mod)["claims"], 1)   # stats: review view
        # nor through a contradiction with something the moderator can find
        mine = self.b.learn("Leo's room is 5550002.", "user", "Leo", by=self.mod)
        self.b.link(mine, p, "contradicts", by=self.mod)
        found = [h.record_id for h in self.b.search("Leo room", viewer=self.mod)]
        self.assertIn(str(mine), found)
        self.assertNotIn(str(p), found)

    def test_ones_own_statement_is_not_evidence(self):
        c = self.b.learn("The bank opens at 9.", "user", "Bank")
        said = self.b.add_source("user_statement", title="I said so", reference="me")
        self.b.add_evidence(c, "direct", said)
        self.b.add_evidence(c, "confirmation", said)
        s = self.b.explain(c)["support"]
        self.assertEqual((s["direct"], s["confirmations"], s["assertions"]), (0, 0, 2))
        with self.assertRaises(BrainError):
            self.b.review(c, "verified")
        self._evidence(c, self.ana, "s://bank")              # a real screenshot does count
        self.b.review(c, "verified")

    def test_a_newer_database_is_refused_unchanged(self):
        path = os.path.join(self.dir, "newer.db")
        Brain(path, "", "").close()
        con = sqlite3.connect(path)
        con.execute("UPDATE meta SET value='9' WHERE key='schema_version'")
        con.commit()
        con.close()
        with open(path, "rb") as fh:
            before = fh.read()
        with self.assertRaises(BrainError) as err:
            Brain(path, "", "")
        self.assertIn("newer", str(err.exception))
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)
        os.remove(path)                                      # and the file was released

    def test_fts_breaking_in_word_counts_falls_back(self):
        self.b.learn("The Demo Garden has room ID 900001.", "user", "Demo Garden")
        want = self.b.store.visible_term_counts(None, ["garden", "room"], use_fts=False)
        self.b.store._db.execute("DROP TABLE claim_fts")
        self.b.store._db.commit()
        self.assertEqual(self.b.store.visible_term_counts(None, ["garden", "room"],
                                                          use_fts=True), want)
        self.assertFalse(self.b.store.fts)


if __name__ == "__main__":
    unittest.main()
