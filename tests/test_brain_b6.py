"""B6: sources and passages in a library - exact quotations, provenance,
challenges without suppression, personal / shared separation, withdrawal
and purging that leave no recoverable text in SK-managed files.

Synthetic data only. No AI, no network."""
import json
import os
import secrets
import shutil
import sqlite3
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from puzzle_assistant.brain import sources as B6  # noqa: E402
from puzzle_assistant.brain import upgrade as UP  # noqa: E402
from puzzle_assistant.brain.importer import ImportRefused  # noqa: E402
from puzzle_assistant.brain.library import Library, mint_grant  # noqa: E402
from puzzle_assistant.brain.store import BrainError  # noqa: E402
import b6_quote_corpus as QC  # noqa: E402

Q_MG = "Who maintains the Lantern Garden?"


def all_files(root):
    out = []
    for d, _, names in os.walk(root):
        out += [os.path.join(d, n) for n in names]
    return out


def where(root, marker):
    b = marker.encode("utf-8") if isinstance(marker, str) else marker
    hits = set()
    for p in all_files(root):
        with open(p, "rb") as fh:
            if b in fh.read():
                hits.add(os.path.relpath(p, root).replace(os.sep, "/"))
    return hits


class World(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="sk_b6_")
        self.files = os.path.join(self.d, "files")
        os.makedirs(self.files)
        self.hk, self.lk = secrets.token_bytes(32), secrets.token_bytes(32)
        self.lib = Library(os.path.join(self.d, "brain"), self.hk, self.lk)
        self.ids = {"ana": self.lib.create_user("Ana"),
                    "luis": self.lib.create_user("Luis", "external_user"),
                    "mod": self.lib.create_user("Moderator", "moderator")}
        self.sessions = []

    def tearDown(self):
        for s in self.sessions:
            s.close()
        self.lib.close()
        shutil.rmtree(self.d, ignore_errors=True)

    def open(self, who):
        s = self.lib.open_session(mint_grant(self.hk, self.lib.installation_id, self.lib.epoch,
                                             self.ids[who], "moderator" if who == "mod"
                                             else "user"))
        self.sessions.append(s)
        return s

    def write(self, name, text, raw=None):
        p = os.path.join(self.files, name)
        with open(p, "wb") as fh:
            fh.write(raw if raw is not None else text.encode("utf-8"))
        return p

    def fid(self, who):
        return self.lib._user(self.ids[who])["file_id"]

    def own_files(self, who):
        f = self.fid(who)
        return {p for p in (os.path.relpath(x, self.lib.root).replace(os.sep, "/")
                            for x in all_files(self.lib.root)) if f in p}

    def imp(self, s, text, name="guide.md", author="named", **kw):
        kw.setdefault("author_name", "Ada Guide")
        kw.setdefault("publisher", "Harbour Press")
        return s.import_file(self.write(name, text), author_kind=author, **kw)["source_id"]


# ======================================================================== quotations
class Quotations(World):
    def test_documented_only_from_an_exact_current_quote(self):
        s = self.open("ana")
        sid = self.imp(s, "# Garden\n\nThe harbour is calm. Mira maintains the Lantern Garden.\n")
        c = s.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        self.assertEqual(s.respond(Q_MG).reason, "unsupported")
        r = s.cite(c, sid, "Mira maintains the Lantern Garden.")
        self.assertTrue(r["supports"])
        a = s.respond(Q_MG)
        self.assertEqual((a.category, a.value), ("documented", "Mira"))

    def test_quotes_that_are_not_exactly_in_one_passage_are_refused(self):
        s = self.open("ana")
        sid = self.imp(s, "Mira maintains the Lantern Garden. Lio runs the Rope Shed.\n")
        c = s.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        for q in ("Mira maintains the Lantern garden.", "Mira  maintains the Lantern Garden.",
                  "Garden. Lio runs", "Kai maintains the Lantern Garden.", ""):
            with self.assertRaises(BrainError, msg=q):
                s.cite(c, sid, q)
        self.assertEqual(s.respond(Q_MG).reason, "unsupported")

    def test_legacy_add_evidence_cannot_bypass_the_location_check(self):
        s = self.open("ana")
        sid = self.imp(s, "Lio runs the Rope Shed.\n")
        c = s.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        with self.assertRaises(BrainError):
            s.add_evidence(c, "direct", sid, "Mira maintains the Lantern Garden.")

    def test_a_fragment_cannot_drop_a_hedge_or_negation(self):
        s = self.open("ana")
        for text in ("Perhaps Mira maintains the Lantern Garden.",
                     "It is not true that Mira maintains the Lantern Garden.",
                     "If it rains, Mira maintains the Lantern Garden."):
            sid = self.imp(s, text + "\n", name=f"f{abs(hash(text))}.txt")
            c = s.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
            r = s.cite(c, sid, "Mira maintains the Lantern Garden.")
            self.assertFalse(r["supports"], text)
        self.assertNotEqual(s.respond(Q_MG).category, "documented")

    def test_ai_and_self_written_material_is_never_evidence(self):
        s = self.open("ana")
        ai = self.imp(s, "Mira maintains the Lantern Garden.\n", "ai.txt", "ai_generated",
                      author_name="")
        me = self.imp(s, "Mira maintains the Lantern Garden.\n", "me.txt", "self", author_name="")
        c = s.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        with self.assertRaises(BrainError):
            s.cite(c, ai, "Mira maintains the Lantern Garden.")
        with self.assertRaises(BrainError):
            s.cite(c, me, "Mira maintains the Lantern Garden.")
        s.cite(c, me, "Mira maintains the Lantern Garden.", kind="assertion")
        with self.assertRaises(BrainError):
            s.review(c, "verified")
        self.assertEqual(s.respond(Q_MG).reason, "unsupported")
        self.assertEqual(s.suggestions(), [])
        for sid in (ai, me):
            with self.assertRaises(BrainError):
                s.attest_source(sid, "checked")

    def test_hidden_html_text_cannot_be_quoted(self):
        s = self.open("ana")
        sid = s.import_file(self.write("p.html", '<p>Lio runs the Rope Shed.</p>'
                                       '<p style="display:none">Mira maintains the Lantern '
                                       'Garden.</p><!-- Mira maintains the Lantern Garden. -->'),
                            author_kind="unknown")["source_id"]
        c = s.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        with self.assertRaises(BrainError):
            s.cite(c, sid, "Mira maintains the Lantern Garden.")

    def test_invisible_characters_flag_the_passage(self):
        s = self.open("ana")
        sid = self.imp(s, "Mira maintains the Lantern​ Garden. Mira maintains the Lantern "
                          "Garden.\n")
        c = s.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        r = s.cite(c, sid, "Mira maintains the Lantern Garden.")    # the clean second one
        self.assertTrue(r["supports"])
        sid2 = self.imp(s, "Mira maintains the Lantern Garden‮.\n", "b.txt")
        c2 = s.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        self.assertFalse(s.cite(c2, sid2, "Mira maintains the Lantern Garden")["supports"])

    def test_refused_files_store_nothing(self):
        s = self.open("ana")
        before = s.store._db.execute("SELECT COUNT(*) FROM main.sources").fetchone()[0]
        for name, raw in (("a.pdf", b"x"), ("b.txt", b"\xff\x00bad"), ("c.txt", "é".encode("latin-1")),
                          ("d.txt", b"x" * (5 * 1024 * 1024 + 1))):
            with self.assertRaises(ImportRefused):
                s.import_file(self.write(name, "", raw), author_kind="unknown")
        self.assertEqual(s.store._db.execute("SELECT COUNT(*) FROM main.sources").fetchone()[0],
                         before)

    def test_references_lose_query_and_fragment(self):
        self.assertEqual(B6.strip_reference("https://x.example/a/b?session=SECRET#frag"),
                         "https://x.example/a/b")
        self.assertEqual(B6.strip_reference("https://x.example/a?q=1#f", keep_query=True),
                         "https://x.example/a?q=1")
        self.assertEqual(B6.strip_reference("guide page 4?x"), "guide page 4")
        s = self.open("ana")
        sid = self.imp(s, "Lio runs the Rope Shed.\n", reference="https://h.example/p?token=TOK9#x")
        self.assertEqual(s.versions(sid)[0]["reference"], "https://h.example/p")
        self.assertFalse(where(self.lib.root, "TOK9"))


class Gates(World):
    """Each gate on its own: the stored rows are changed behind cite()'s back
    (as another program could), and the evidence must stop counting."""

    def test_each_gate_alone_stops_support(self):
        s = self.open("ana")
        sid = self.imp(s, "Lio runs the Rope Shed.\n\nMira maintains the Lantern Garden.\n")
        c = s.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        e = s.cite(c, sid, "Mira maintains the Lantern Garden.")["evidence_id"]
        db = s.store._db
        loc = dict(db.execute("SELECT * FROM main.evidence_locations WHERE evidence_id=?",
                              (e,)).fetchone())
        v, pid = loc["version_id"], loc["passage_id"]
        other = db.execute("SELECT id FROM main.passages WHERE version_id=? AND id!=?",
                           (v, pid)).fetchone()[0]
        changes = [
            ("UPDATE main.source_versions SET author_kind='ai_generated' WHERE id=?", (v,),
             "UPDATE main.source_versions SET author_kind='named' WHERE id=?", (v,)),
            ("UPDATE main.source_versions SET author_kind='self' WHERE id=?", (v,),
             "UPDATE main.source_versions SET author_kind='named' WHERE id=?", (v,)),
            ("UPDATE main.source_versions SET state='superseded' WHERE id=?", (v,),
             "UPDATE main.source_versions SET state='current' WHERE id=?", (v,)),
            ("UPDATE main.evidence_locations SET start_pos=start_pos+1 WHERE evidence_id=?", (e,),
             "UPDATE main.evidence_locations SET start_pos=start_pos-1 WHERE evidence_id=?", (e,)),
            ("UPDATE main.evidence_locations SET passage_id=? WHERE evidence_id=?", (other, e),
             "UPDATE main.evidence_locations SET passage_id=? WHERE evidence_id=?", (pid, e)),
            ("UPDATE main.passages SET kind='table_row' WHERE id=?", (pid,),
             "UPDATE main.passages SET kind='sentence' WHERE id=?", (pid,)),
            ("UPDATE main.passages SET kind='heading' WHERE id=?", (pid,),
             "UPDATE main.passages SET kind='sentence' WHERE id=?", (pid,)),
            ("UPDATE main.passages SET flags='[\"suspicious\"]' WHERE id=?", (pid,),
             "UPDATE main.passages SET flags='[]' WHERE id=?", (pid,)),
            ("UPDATE main.evidence SET locator='Mira maintains the Lantern Garden!' WHERE id=?",
             (e,), "UPDATE main.evidence SET locator='Mira maintains the Lantern Garden.' "
             "WHERE id=?", (e,)),
            ("DELETE FROM main.evidence_locations WHERE evidence_id=?", (e,),
             "INSERT INTO main.evidence_locations VALUES (?,?,?,?,?,?)",
             (e, v, pid, loc["start_pos"], loc["end_pos"], loc["quote_sha"])),
        ]
        claim = s.store._claim(c)
        self.assertEqual(B6.evidence_check(s.store, e, claim, True), (True, "located"))
        for do, a1, undo, a2 in changes:
            with self.subTest(do=do):
                db.execute(do, a1)
                db.commit()
                self.assertFalse(B6.evidence_check(s.store, e, claim, True)[0])
                self.assertFalse(B6.evidence_check(s.store, e, claim, False)[0])
                self.assertNotEqual(s.respond(Q_MG).category, "documented")
                db.execute(undo, a2)
                db.commit()
                self.assertTrue(B6.evidence_check(s.store, e, claim, True)[0])

    def test_the_passage_must_state_the_fact_and_nothing_else(self):
        claim = {"text": "Mira maintains the Lantern Garden.", "subject": "Lantern Garden"}
        self.assertTrue(B6.tight(claim, "Mira maintains the Lantern Garden."))
        self.assertTrue(B6.tight(claim, "Mira looks after the Lantern Garden."))
        for p in ("Mira maintains the Lantern Garden during the festival.",
                  "Mira maintains the Lantern Garden and Kai the Rope Shed.",
                  "Mira maintains the Lantern Garden?"):
            self.assertFalse(B6.tight(claim, p), p)


class QuoteCorpus(World):
    """Every labelled quotation of tests/b6_quote_corpus.py: ZERO false support."""

    def doc(self, src, kind):
        if kind == "list_item":
            return f"- {src}\n"
        if kind == "table_row":
            row = src if src.startswith("|") else f"| {src} |"
            return f"| Note |\n| --- |\n{row}\n"
        if kind == "heading":
            return f"# {src}\n"
        if kind == "code":
            return f"```\n{src}\n```\n"
        if kind == "quote":
            return f"> {src}\n"
        return src + "\n"

    def test_corpus(self):
        s = self.open("ana")
        false_support, missed = [], []
        for (cid, cat, claim, subj, src, quote, kind, author, cur, exp) in QC.CASES:
            p = self.write(f"{cid}.md", self.doc(src, kind))
            try:
                sid = s.import_file(p, author_kind=author,
                                    author_name="Ada" if author == "named" else "")["source_id"]
            except ImportRefused:
                got = False
            else:
                c = s.learn(claim, "imported", subj)
                try:
                    got = s.cite(c, sid, quote)["supports"]
                except BrainError:
                    got = False
                if got and not cur:
                    self.write(f"{cid}.md", self.doc(src, kind) + "\nA later line was added.\n")
                    s.reimport(sid)
                    e = s.store._db.execute("SELECT MAX(id) FROM main.evidence WHERE claim_id=?",
                                            (c,)).fetchone()[0]
                    got = B6.evidence_check(s.store, e, s.store._claim(c), strict=True)[0]
            if got and not exp:
                false_support.append(cid)
            if exp and not got:
                missed.append(cid)
        self.assertEqual(false_support, [])
        self.assertEqual(missed, [], "genuine quotations that were not accepted")


# ======================================================================== versions
class Versions(World):
    def test_new_version_marks_quotes_source_changed_until_cited_again(self):
        s = self.open("ana")
        p = self.write("g.txt", "Mira maintains the Lantern Garden.\n")
        sid = s.import_file(p, author_kind="named", author_name="Ada")["source_id"]
        c = s.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        s.cite(c, sid, "Mira maintains the Lantern Garden.")
        s.review(c, "verified")
        self.assertEqual(s.respond(Q_MG).category, "established")
        self.assertEqual(s.reimport(sid)["state"], "unchanged")
        self.write("g.txt", "Mira maintains the Lantern Garden.\nIt opens at nine.\n")
        r = s.reimport(sid)
        self.assertEqual((r["state"], r["version"], r["affected_claims"]), ("new_version", 2, [c]))
        a = s.respond(Q_MG)
        self.assertEqual((a.category, a.reason), ("unknown", "source_changed"))
        self.assertEqual(s.claim(c)["status"], "awaiting_verification")
        self.assertEqual(s.store._db.execute("SELECT status FROM main.claims WHERE id=?",
                                             (c,)).fetchone()[0], "verified")   # stored: unchanged
        s.cite(c, sid, "Mira maintains the Lantern Garden.")
        self.assertEqual(s.respond(Q_MG).category, "established")
        self.assertEqual([v["state"] for v in s.versions(sid)], ["superseded", "current"])
        self.assertEqual(s.versions(sid)[1]["provenance"], "declared")     # not carried over

    def test_purged_version_text_is_gone_from_every_sk_file(self):
        s = self.open("ana")
        p = self.write("g.txt", "Lio runs the Rope Shed. Purgemarkerone keeps secrets here.\n")
        sid = s.import_file(p, author_kind="unknown")["source_id"]
        self.write("g.txt", "Lio runs the Rope Shed.\n")
        s.reimport(sid)
        s.search_passages("purgemarkerone")
        self.assertTrue(where(self.lib.root, "Purgemarkerone"))
        self.assertTrue(where(self.lib.root, "purgemarker"))              # the search index
        r = s.purge_version(sid, 1)
        self.assertEqual(r["state"], "purged")
        self.assertEqual(r["backups"], [])
        for m in ("Purgemarkerone", "purgemarker", "keeps secrets"):     # SK still running
            self.assertEqual(where(self.lib.root, m), set(), m)
        s.close()
        for m in ("Purgemarkerone", "purgemarker", "keeps secrets"):
            self.assertEqual(where(self.lib.root, m), set(), m)
        s = self.open("ana")
        self.assertEqual(s.search_passages("purgemarkerone"), [])
        self.assertEqual([v["state"] for v in s.versions(sid)], ["purged", "current"])
        self.assertTrue(s.search_passages("rope shed"))


# ======================================================================== challenges
class Challenges(World):
    def setUp(self):
        super().setUp()
        self.s = self.open("ana")
        self.c = self.s.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        sid = self.imp(self.s, "Mira maintains the Lantern Garden.\n", "a.txt")
        self.s.cite(self.c, sid, "Mira maintains the Lantern Garden.")

    def test_a_disagreeing_passage_never_changes_an_established_answer(self):
        self.s.review(self.c, "verified")
        self.imp(self.s, "Kai maintains the Lantern Garden.\n", "other.txt", publisher="Other")
        a = self.s.respond(Q_MG)
        self.assertEqual((a.category, a.value), ("established", "Mira"))
        self.assertEqual(len(a.challenges), 1)
        self.assertEqual(a.challenges[0]["values"], ["Kai"])
        self.assertIn("never a confirmation", a.challenges[0]["counts_as"])

    def test_a_disagreeing_passage_makes_a_documented_answer_disagree(self):
        self.assertEqual(self.s.respond(Q_MG).category, "documented")
        self.imp(self.s, "Kai maintains the Lantern Garden.\n", "other.txt", publisher="Other")
        a = self.s.respond(Q_MG)
        self.assertEqual((a.category, a.reason), ("unknown", "sources_disagree"))
        self.assertEqual({x["value"] for x in a.sides}, {"Mira", "Kai"})

    def test_ineligible_passages_do_not_challenge(self):
        self.s.review(self.c, "verified")
        for i, (text, kind) in enumerate((("Kai maintains the Lantern Garden.", "ai_generated"),
                                          ("Kai maintains the Lantern Garden.", "self"),
                                          ("Perhaps Kai maintains the Lantern Garden.", "named"),
                                          ("# Kai maintains the Lantern Garden.", "named"),
                                          ("Kai maintains the Lantern​ Garden.", "named"))):
            self.imp(self.s, text + "\n", f"x{i}.md", kind)
        self.assertEqual(self.s.respond(Q_MG).challenges, [])

    def test_a_flood_of_copies_is_one_challenge(self):
        self.s.review(self.c, "verified")
        for i in range(40):
            self.imp(self.s, f"Kai maintains the Lantern Garden. Copy {i}.\n", f"k{i}.txt",
                     publisher="Flood Press")
        a = self.s.respond(Q_MG)
        self.assertEqual(a.category, "established")
        self.assertEqual(len(a.challenges), 1)
        self.assertEqual((a.challenges[0]["sources"], a.challenges[0]["passages"]), (40, 40))

    def test_material_that_contradicts_itself_is_internal(self):
        self.s.review(self.c, "verified")
        self.imp(self.s, "Mira maintains the Lantern Garden. Kai maintains the Lantern Garden.\n",
                 "both.txt", publisher="Mixed")
        ch = self.s.respond(Q_MG).challenges
        self.assertEqual([g["internal"] for g in ch], [True])


# ======================================================================== provenance
class Provenance(World):
    def summary(self, s, c):
        return s.store.evidence_summary(c)

    def test_confirmation_needs_two_attested_independent_sources(self):
        s = self.open("ana")
        c = s.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        a = self.imp(s, "Mira maintains the Lantern Garden.\n", "a.txt", publisher="Harbour Press")
        b = self.imp(s, "Mira maintains the Lantern Garden. Written again.\n", "b.txt",
                     publisher="Harbour Press")
        u = self.imp(s, "Mira maintains the Lantern Garden. Elsewhere.\n", "u.txt", "unknown",
                     publisher="Lamp Press")
        o = self.imp(s, "Mira maintains the Lantern Garden. Other words.\n", "o.txt",
                     publisher="Lamp Press")
        s.cite(c, a, "Mira maintains the Lantern Garden.")
        for x in (b, u, o):
            s.cite(c, x, "Mira maintains the Lantern Garden.", kind="confirmation")
        self.assertEqual(self.summary(s, c)["confirmations"], 0)      # nothing attested
        s.attest_source(a, "the publisher's own page, checked")
        s.attest_source(b, "checked")
        self.assertEqual(self.summary(s, c)["confirmations"], 0)      # same publisher
        s.attest_source(o, "checked")
        self.assertEqual(self.summary(s, c)["confirmations"], 1)      # o: independent
        with self.assertRaises(BrainError):
            s.attest_source(u, "")                                    # a note is required

    def test_attestation_never_changes_a_status(self):
        s = self.open("ana")
        c = s.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        a = self.imp(s, "Mira maintains the Lantern Garden.\n")
        s.cite(c, a, "Mira maintains the Lantern Garden.")
        s.attest_source(a, "checked")
        self.assertEqual(s.claim(c)["status"], "submitted")
        self.assertEqual(s.respond(Q_MG).category, "documented")
        self.assertIn("not a check of the facts", B6.label(B6.current_version(s.store, a)))


# ======================================================================== suggestions
class Suggestions(World):
    def test_nothing_is_learnt_without_an_explicit_accept(self):
        s = self.open("ana")
        self.imp(s, "Mira maintains the Lantern Garden.\n\n- Lio runs the Rope Shed.\n")
        n = s.store._db.execute("SELECT COUNT(*) FROM main.claims").fetchone()[0]
        sug = s.suggestions()
        self.assertEqual(len(sug), 2)
        self.assertEqual(s.store._db.execute("SELECT COUNT(*) FROM main.claims").fetchone()[0], n)
        self.assertEqual(s.respond(Q_MG).reason, "nothing_documented")
        cid = s.accept_suggestion(sug[0]["id"])
        self.assertEqual(s.claim(cid)["status"], "submitted")
        self.assertEqual(s.claim(cid)["scope"], "personal")
        self.assertEqual(s.respond(Q_MG).category, "documented")
        s.dismiss_suggestion(sug[1]["id"])
        self.assertEqual(s.suggestions(), [])
        with self.assertRaises(BrainError):
            s.accept_suggestion(sug[1]["id"])
        luis = self.open("luis")
        self.assertEqual(luis.suggestions(), [])


# ======================================================================== separation
class SecureDelete(World):
    def test_every_library_file_deletes_search_words_for_real(self):
        s = self.open("ana")
        self.imp(s, "Lio runs the Rope Shed.\n")
        m = self.open("mod")
        for con, schema in ((s.store._db, "main"), (s.store._db, "shared"), (m.store._db, "main")):
            r = con.execute(f"SELECT v FROM {schema}.passage_fts_config WHERE k='secure-delete'"
                            ).fetchone()
            self.assertEqual(r and r[0], 1, schema)
            self.assertEqual(con.execute("PRAGMA secure_delete").fetchone()[0], 1)


class Separation(World):
    def test_personal_passages_stay_in_their_owners_file(self):
        ana, luis = self.open("ana"), self.open("luis")
        self.imp(ana, "Kai maintains the Lantern Garden. Anaprivatemark lives here.\n")
        ana.search_passages("anaprivatemark")
        c = luis.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        sid = self.imp(luis, "Mira maintains the Lantern Garden.\n", "l.txt")
        luis.cite(c, sid, "Mira maintains the Lantern Garden.")
        luis.review(c, "verified")
        self.assertEqual(luis.search_passages("anaprivatemark"), [])
        self.assertEqual(luis.search_passages("kai"), [])
        self.assertEqual(luis.respond(Q_MG).challenges, [])            # Ana's passage: unseen
        ana.close()
        luis.close()
        for m in ("Anaprivatemark", "anaprivatemark", os.path.join(self.files, "guide.md")):
            hits = where(self.lib.root, m)
            self.assertTrue(hits, m)
            self.assertEqual(hits - self.own_files("ana"), set(), m)
        with sqlite3.connect(os.path.join(self.lib.root, "shared.db")) as con:
            names = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
        self.assertFalse(names & {"source_files", "claim_candidates"})


# ======================================================================== sharing
class Sharing(World):
    TEXT = ("# Notes\n\nMira maintains the Lantern Garden.\n\n"
            "Sharedonlywithfulltext appears in the rest of the file.\n")

    def share(self, levels, attest_owner=False):
        ana = self.open("ana")
        sid = self.imp(ana, self.TEXT, title="Garden notes")
        c = ana.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        ana.cite(c, sid, "Mira maintains the Lantern Garden.")
        if attest_owner:
            ana.attest_source(sid, "checked by me")
        tok = ana.propose(c, {sid: levels})
        m = self.open("mod")
        shared = m.approve(self.fid("ana"), tok)
        return ana, m, sid, shared

    def shared_rows(self, table):
        con = sqlite3.connect(os.path.join(self.lib.root, "shared.db"))
        try:
            return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            con.close()

    def test_quote_grant_shares_the_quote_alone(self):
        ana, m, sid, shared = self.share(["quote"])
        for s in self.sessions:
            s.close()
        self.sessions = []
        self.assertEqual(where(self.lib.root, "Sharedonlywithfulltext") - self.own_files("ana"),
                         set())
        self.assertEqual(self.shared_rows("passages"), 0)
        self.assertEqual(self.shared_rows("source_versions"), 1)        # the declared origin
        self.assertEqual(self.shared_rows("source_versions WHERE text IS NOT NULL"), 0)
        self.assertIn("shared.db", where(self.lib.root, "Mira maintains the Lantern Garden."))

    def test_full_text_grant_shares_passages_and_needs_a_moderator_attestation(self):
        ana, m, sid, shared = self.share(["quote", "full_text"], attest_owner=True)
        self.assertGreater(self.shared_rows("passages"), 0)
        with self.assertRaises(BrainError):
            m.review(shared, "verified")       # the owner's attestation does not carry over
        ssid = m.store._db.execute("SELECT source_id FROM evidence WHERE claim_id=?",
                                   (shared,)).fetchone()[0]
        m.attest_source(ssid, "publisher page checked by a moderator")
        m.review(shared, "verified")
        luis = self.open("luis")
        a = luis.respond(Q_MG)
        self.assertEqual((a.category, a.audience), ("established", "shared"))
        self.assertTrue(luis.search_passages("sharedonlywithfulltext"))

    def test_withdrawal_removes_every_trace_from_shared_files(self):
        ana, m, sid, shared = self.share(["quote", "details", "full_text"])
        m.search_passages("sharedonlywithfulltext")
        self.assertTrue({h for h in where(self.lib.root, "sharedonlywithfulltext") |
                         where(self.lib.root, "Sharedonlywithfulltext") if h.startswith("shared")})
        self.assertTrue({h for h in where(self.lib.root, "sharedonlywithfull")
                         if h.startswith("shared")})                    # the search index
        self.assertEqual(ana.withdraw_source(sid), "complete")
        mine = {p for p in self.own_files("ana") if p.startswith("personal/")}
        markers = ("Sharedonlywithfulltext", "sharedonlywithfull", "Garden notes",
                   "Harbour Press", "Ada Guide")   # (the shared claim's own wording stays:
        # withdrawal removes the evidence, not the claim)
        for mk in markers:                                     # sessions still open
            self.assertEqual(where(self.lib.root, mk) - mine, set(), mk)
        for s in self.sessions:
            s.close()
        self.sessions = []
        for mk in markers:
            self.assertEqual(where(self.lib.root, mk) - mine, set(), mk)
        for t in ("passages", "source_versions", "passage_index", "passage_facts",
                  "evidence_locations"):
            self.assertEqual(self.shared_rows(t), 0, t)
        luis = self.open("luis")
        self.assertEqual(luis.search_passages("sharedonlywithfulltext"), [])
        self.assertNotEqual(luis.respond(Q_MG).category, "established")

    def test_self_written_material_is_not_proposed(self):
        ana = self.open("ana")
        sid = self.imp(ana, "Mira maintains the Lantern Garden.\n", "me.txt", "self",
                       author_name="")
        c = ana.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        ana.cite(c, sid, "Mira maintains the Lantern Garden.", kind="assertion")
        with self.assertRaises(BrainError):
            ana.propose(c, {sid: ["quote", "full_text"]})


# ======================================================================== backups
class Backups(unittest.TestCase):
    """Withdrawal after an upgrade: the text is still in the SK-managed backups
    until they are released; release only with a verified current library."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="sk_b6_bk_")
        with zipfile.ZipFile(os.path.join(HERE, "fixtures", "b52_library.zip")) as z:
            z.extractall(self.d)
        with open(os.path.join(self.d, "fixture.json"), encoding="utf-8") as fh:
            self.info = json.load(fh)
        os.remove(os.path.join(self.d, "fixture.json"))
        self.hk, self.lk = bytes.fromhex(self.info["hk"]), bytes.fromhex(self.info["lk"])

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_withdrawn_text_is_reported_in_backups_and_gone_after_release(self):
        UP.upgrade_library(self.d, self.hk)
        lib = Library(os.path.join(self.d, "brain"), self.hk, self.lk)
        uid = self.info["ids"]["ana"]
        fid = lib._user(uid)["file_id"]
        s = lib.open_session(mint_grant(self.hk, lib.installation_id, lib.epoch, uid, "user"))
        sid = s.store._db.execute("SELECT id FROM main.sources WHERE title='ANA-TITLE-MARK'"
                                  ).fetchone()[0]
        self.assertEqual(s.withdraw_source(sid), "complete")
        s.close()
        lib.close()
        held = UP.backups_holding(self.d, [b"ANA-FULLTEXT-MARK"])
        self.assertTrue(held)                      # honest: the backups still hold it
        with self.assertRaises(BrainError):
            UP.release_backups(self.d, self.hk, self.lk)              # needs confirm=True
        removed = UP.release_backups(self.d, self.hk, self.lk, confirm=True)
        self.assertTrue(removed)
        self.assertEqual(UP.backups_holding(self.d, [b"ANA-FULLTEXT-MARK"]), [])
        for m in ("ANA-FULLTEXT-MARK", "ANA-TITLE-MARK", "ANA-REF-MARK"):
            hits = where(self.d, m)
            self.assertEqual({h for h in hits if fid not in h}, set(), m)
        self.assertTrue(os.path.isdir(os.path.join(self.d, "brain")))

    def test_release_refused_while_an_upgrade_is_unfinished(self):
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(HERE, "b6_child.py"), "upgrade",
                            self.d, self.hk.hex()],
                           env=dict(os.environ, SK_TEST_CRASH_AT="between_renames"),
                           capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 77, r.stderr)
        listing = sorted(os.listdir(self.d))
        with self.assertRaises(BrainError):
            UP.release_backups(self.d, self.hk, self.lk, confirm=True)
        self.assertEqual(sorted(os.listdir(self.d)), listing)        # nothing deleted


if __name__ == "__main__":
    unittest.main()
