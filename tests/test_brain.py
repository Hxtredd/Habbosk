"""SK Brain B1: knowledge library schema and rules, API, read-only adapters."""
import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from puzzle_assistant.brain import Brain  # noqa: E402
from puzzle_assistant.brain import retrieve as _retrieve  # noqa: E402
_retrieve.VERIFY_FAST = True      # every search here also checks fast == full scoring
from puzzle_assistant.brain.store import BrainError  # noqa: E402
from puzzle_assistant.qa.memory import Memory  # noqa: E402
from puzzle_assistant.qa.model import Answer, Question  # noqa: E402
from puzzle_assistant.world_atlas.db import Atlas  # noqa: E402


def tmp(name):
    return os.path.join(tempfile.mkdtemp(), name)


def file_hash(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class LibraryRulesTests(unittest.TestCase):
    def setUp(self):
        self.b = Brain(tmp("knowledge.db"))

    def tearDown(self):
        self.b.close()

    def test_new_claims_are_never_verified_or_shared_on_creation(self):
        c = self.b.learn("El Gran Casino está junto al Spa.", "imported", "Gran Casino", "lugares")
        self.assertEqual(self.b.claim(c)["status"], "submitted")
        self.assertEqual(self.b.claim(c)["scope"], "personal")
        with self.assertRaises(BrainError):
            self.b.store.add_claim("x", "imported", status="verified")
        with self.assertRaises(BrainError):
            self.b.store.add_claim("x", "imported", scope="shared")
        with self.assertRaises(BrainError):
            self.b.learn("x", "rumour")                      # unknown origin

    def test_repeating_a_claim_never_verifies_it(self):
        text = "La mina está en Avenida Crisol."
        ids = []
        for name in ("Ana", "Luis", "Marta"):                  # three people say it
            who = self.b.store.add_contributor(name)
            c = self.b.learn(text, "user", "Mina", by=who)
            self.b.add_evidence(c, "assertion", by=who)
            self.b.add_evidence(c, "assertion", by=who)        # and repeat it
            ids.append((c, who))
        for c, who in ids:
            s = self.b.explain(c, viewer=who)["support"]
            self.assertEqual((s["direct"], s["assertions"]), (0, 2))
            with self.assertRaises(BrainError):
                self.b.review(c, "verified", reviewer=who)
            self.assertEqual(self.b.claim(c, viewer=who)["status"], "submitted")

    def test_same_material_counts_once_and_direct_evidence_allows_verification(self):
        c = self.b.learn("El Gran Casino es la sala 12411.", "observed", "Gran Casino")
        shot1 = self.b.add_source("screenshot", title="casino", content_hash="abc")
        shot2 = self.b.add_source("screenshot", title="casino (same file, other user)",
                                  content_hash="abc", by=self.b.store.add_contributor("Ana"))
        self.b.add_evidence(c, "direct", shot1, "share panel URL")
        self.b.add_evidence(c, "confirmation", shot2)
        s = self.b.explain(c)["support"]
        self.assertEqual((s["direct"], s["confirmations"]), (1, 0))   # same picture: once
        self.b.review(c, "verified", note="URL readable in the screenshot")
        ev = self.b.explain(c)["events"]
        self.assertEqual(ev[-1]["event"], "reviewed")
        self.assertEqual((ev[-1]["old_status"], ev[-1]["new_status"]), ("submitted", "verified"))

    def test_ai_suggestion_needs_real_evidence(self):
        c = self.b.learn("Leo es el asistente NPC.", "ai", "Leo")
        with self.assertRaises(BrainError):
            self.b.review(c, "verified")
        with self.assertRaises(BrainError):
            self.b.add_evidence(c, "direct")                 # direct evidence needs a source
        game = self.b.add_source("game_observation", title="quiz reveal")
        self.b.add_evidence(c, "direct", game)
        self.b.review(c, "verified")
        self.assertEqual(self.b.claim(c)["origin"], "ai")    # origin is never rewritten

    def test_correction_keeps_the_original(self):
        c = self.b.learn("El banco abre a las 8.", "imported", "Banco")
        v = self.b.correct(c, "El banco abre a las 9.", reason="guide page updated")
        self.assertEqual(v, 2)
        e = self.b.explain(c)
        self.assertEqual(e["claim"]["text"], "El banco abre a las 9.")
        self.assertEqual(e["claim"]["status"], "awaiting_verification")
        self.assertEqual(e["versions"][0]["text"], "El banco abre a las 8.")
        self.assertEqual(e["versions"][0]["reason"], "guide page updated")
        self.assertEqual([x["event"] for x in e["events"]], ["created", "corrected"])

    def test_contradiction_flags_both_and_keeps_both(self):
        a = self.b.learn("El taxi cuesta $5.", "user", "taxi")
        b = self.b.learn("El taxi es gratis.", "user", "taxi")
        self.b.link(b, a, "contradicts")
        self.assertEqual(self.b.claim(a)["status"], "conflicting")
        self.assertEqual(self.b.claim(b)["status"], "conflicting")
        self.assertEqual(len(self.b.store.claims(subject="taxi")), 2)

    def test_private_evidence_blocks_sharing(self):
        c = self.b.learn("Mi contraseña del banco es…", "user", "banco")
        priv = self.b.add_source("screenshot", title="my chat", private=True)
        self.b.add_evidence(c, "direct", priv)
        with self.assertRaises(BrainError):
            self.b.propose_share(c)
        self.assertEqual(self.b.claim(c)["scope"], "personal")
        ok = self.b.learn("El Spa está en Avenida Espumia.", "imported", "Spa")
        self.b.propose_share(ok)
        self.assertEqual(self.b.claim(ok)["scope"], "shared_candidate")
        self.assertEqual(self.b.explain(ok)["events"][-1]["event"], "share_proposed")

    def test_contributors_and_schema(self):
        self.assertTrue(self.b.local_user)
        self.assertEqual(self.b.store.schema_version, 4)
        with self.assertRaises(BrainError):
            self.b.learn("x", "user", by="nobody")           # unknown contributor
        # reopening keeps the same local user
        path = self.b.store.path
        uid = self.b.local_user
        self.b.close()
        self.b = Brain(path)
        self.assertEqual(self.b.local_user, uid)


class IsolationTests(unittest.TestCase):
    """Personal knowledge belongs to its owner; shared knowledge follows the
    approval rules (single-user app today, multi-user ready)."""

    def setUp(self):
        self.b = Brain(tmp("knowledge.db"))
        self.me = self.b.local_user
        self.ana = self.b.store.add_contributor("Ana")
        self.mod = self.b.store.add_contributor("Mod", kind="moderator")

    def tearDown(self):
        self.b.close()

    def test_personal_claims_are_invisible_to_others(self):
        mine = self.b.learn("Mi código del casillero es 4411.", "user", "casillero")
        hers = self.b.learn("El casillero de Ana está en el gimnasio.", "user", "casillero",
                            by=self.ana)
        self.assertIsNone(self.b.claim(mine, viewer=self.ana))
        self.assertIsNone(self.b.claim(hers))                  # default viewer = me
        self.assertEqual([h.record_id for h in self.b.search("casillero", modules=["library"])],
                         [str(mine)])
        self.assertEqual([h.record_id for h in self.b.search("casillero", modules=["library"],
                                                              viewer=self.ana)], [str(hers)])
        self.assertIsNone(self.b.explain(mine, viewer=self.ana)["claim"])
        self.assertEqual(self.b.explain(mine, viewer=self.ana)["events"], [])
        self.assertEqual(self.b.stats(viewer=self.ana)["claims"], 1)   # no counts leak

    def test_others_cannot_change_my_personal_claims(self):
        mine = self.b.learn("El taxi es gratis.", "user", "taxi")
        src = self.b.add_source("screenshot", title="x", by=self.ana)
        for fn in (lambda: self.b.add_evidence(mine, "direct", src, by=self.ana),
                   lambda: self.b.review(mine, "rejected", reviewer=self.ana),
                   lambda: self.b.correct(mine, "El taxi cuesta $5.", by=self.ana),
                   lambda: self.b.propose_share(mine, by=self.ana)):
            with self.assertRaises(BrainError):
                fn()
        self.assertEqual(self.b.claim(mine)["text"], "El taxi es gratis.")

    def test_sharing_needs_owner_verification_and_a_moderator(self):
        c = self.b.learn("El Spa está junto al Gran Casino.", "imported", "Spa")
        self.b.propose_share(c)
        self.assertIsNone(self.b.claim(c, viewer=self.ana))     # candidate: not public yet
        self.assertIsNotNone(self.b.claim(c, viewer=self.mod))  # moderators can review it
        with self.assertRaises(BrainError):
            self.b.approve_share(c, self.mod)                    # not verified yet
        with self.assertRaises(BrainError):
            self.b.review(c, "verified")                         # owner can't self-verify shared
        shot = self.b.add_source("screenshot", title="map", content_hash="m1")
        self.b.add_evidence(c, "direct", shot)
        self.b.review(c, "verified", reviewer=self.mod, note="visible on the map")
        with self.assertRaises(BrainError):
            self.b.approve_share(c, self.ana)                    # not a moderator
        self.b.approve_share(c, self.mod)
        hit = self.b.search("Spa Gran Casino", modules=["library"], viewer=self.ana)[0]
        self.assertTrue(hit.established)
        ev = self.b.explain(c, viewer=self.ana)["events"]
        # others see the claim's history from its proposal on (its personal
        # days - "created" here - stay with the owner)
        self.assertEqual([e["event"] for e in ev],
                         ["share_proposed", "evidence_added", "reviewed", "share_approved"])
        self.assertEqual(ev[2]["by"], self.mod)                  # who reviewed is recorded
        self.assertEqual(ev[3]["by"], self.mod)
        self.assertEqual([e["event"] for e in self.b.explain(c)["events"]][0], "created")

    def test_shared_claims_cannot_get_private_evidence_or_be_edited_in_place(self):
        c = self.b.learn("La mina tiene 3 niveles.", "imported", "Mina")
        self.b.propose_share(c)
        priv = self.b.add_source("screenshot", title="my screen", private=True)
        with self.assertRaises(BrainError):
            self.b.add_evidence(c, "direct", priv)
        with self.assertRaises(BrainError):
            self.b.correct(c, "La mina tiene 4 niveles.")
        new = self.b.propose_correction(c, "La mina tiene 4 niveles.", reason="nivel 4 visto")
        self.assertEqual(self.b.claim(c)["text"], "La mina tiene 3 niveles.")   # unchanged
        self.assertEqual(self.b.claim(new)["status"], "submitted")
        self.assertTrue(any(l["kind"] == "supersedes" and l["to_claim"] == c
                            for l in self.b.explain(new)["links"]))


class PresentationTests(unittest.TestCase):
    """Unverified knowledge is never presented as an established fact."""

    def setUp(self):
        self.b = Brain(tmp("knowledge.db"))

    def tearDown(self):
        self.b.close()

    def test_unverified_and_conflicting_are_labelled(self):
        a = self.b.learn("El taxi cuesta $5.", "user", "taxi")
        b = self.b.learn("El taxi es gratis.", "user", "taxi")
        c = self.b.learn("El taxi tarda 3 segundos.", "ai", "taxi")
        self.b.link(b, a, "contradicts")
        hits = {h.record_id: h for h in self.b.search("taxi", modules=["library"])}
        for h in hits.values():
            self.assertFalse(h.established)
            self.assertTrue(h.display().startswith("["), h.display())
        self.assertIn("Conflicting", hits[str(a)].display())
        self.assertIn("Unverified", hits[str(c)].display())
        self.assertFalse(self.b.ask("¿Cuánto cuesta el taxi?").answered)

    def test_conflict_must_be_resolved_before_verifying(self):
        a = self.b.learn("El taxi cuesta $5.", "user", "taxi")
        b = self.b.learn("El taxi es gratis.", "user", "taxi")
        self.b.link(b, a, "contradicts")
        src = self.b.add_source("game_observation", title="taxi trip, no charge")
        self.b.add_evidence(b, "direct", src)
        with self.assertRaises(BrainError):
            self.b.review(b, "verified")                         # 'a' is still open
        self.b.review(a, "rejected", note="the trip was free")
        self.b.review(b, "verified")
        self.assertEqual(self.b.claim(a)["status"], "rejected")  # kept, not deleted
        self.assertEqual(len(self.b.store.claims(subject="taxi")), 2)

    def test_no_automatic_verification(self):
        sysid = "system"
        c = self.b.learn("Leo es el asistente NPC.", "ai", "Leo", by=sysid)
        src = self.b.add_source("game_observation", title="quiz reveal", by=sysid)
        self.b.add_evidence(c, "direct", src, by=sysid)
        with self.assertRaises(BrainError):
            self.b.review(c, "verified", reviewer=sysid)        # AI origin: never automatic
        obs = self.b.learn("Leo cubre el gimnasio.", "observed", "Leo", by=sysid)
        self.b.add_evidence(obs, "direct", src, by=sysid)
        self.b.review(obs, "verified", reviewer=sysid)           # SK's own game observation
        imp = self.b.learn("El banco abre a las 9.", "imported", "Banco", by="importer")
        page = self.b.add_source("guide_page", title="Guía", by="importer")
        self.b.add_evidence(imp, "direct", page, by="importer")
        with self.assertRaises(BrainError):
            self.b.review(imp, "verified", reviewer="importer")  # importer can't verify

    def test_evidence_is_traceable(self):
        c = self.b.learn("El Spa está cerca del casino.", "derived", "Spa")
        with self.assertRaises(BrainError):
            self.b.add_evidence(c, "derived")                    # no derived_from link
        with self.assertRaises(BrainError):
            self.b.add_evidence(c, "direct", 999)                # unknown source
        base = self.b.learn("El Spa está en Avenida Espumia.", "imported", "Spa")
        self.b.link(c, base, "derived_from")
        self.b.add_evidence(c, "derived")
        self.assertEqual(self.b.explain(c)["support"]["derived"], 1)

    def test_versions_keep_their_status(self):
        c = self.b.learn("El banco abre a las 8.", "imported", "Banco")
        page = self.b.add_source("guide_page", title="Guía v1")
        self.b.add_evidence(c, "direct", page)
        self.b.review(c, "verified")
        self.b.correct(c, "El banco abre a las 9.", reason="guía nueva")
        v = self.b.explain(c)["versions"][0]
        self.assertEqual((v["text"], v["old_status"]), ("El banco abre a las 8.", "verified"))
        self.assertEqual(self.b.claim(c)["status"], "awaiting_verification")


class MigrationTests(unittest.TestCase):
    def test_v1_database_is_upgraded_without_loss(self):
        path = tmp("knowledge.db")
        db = sqlite3.connect(path)
        from puzzle_assistant.brain import schema
        v1 = schema.SCHEMA.replace("    owner_id TEXT REFERENCES contributors(id),  -- whose "
                                   "knowledge it is (personal = only theirs)\n", "")
        v1 = v1.replace("    old_status TEXT,                   -- status the replaced wording "
                        "had\n", "")
        db.executescript(v1)
        db.execute("INSERT INTO contributors VALUES ('u1','Local','local_user','t')")
        db.execute("INSERT INTO meta VALUES ('local_user','u1'), ('schema_version','1')")
        db.execute("INSERT INTO claims (text, origin, status, scope, version, created, "
                   "created_by, updated) VALUES ('old claim','user','submitted','personal',1,"
                   "'t','u1','t')")
        db.commit()
        cols = [r[1] for r in db.execute("PRAGMA table_info(claims)")]
        self.assertNotIn("owner_id", cols)                       # really a v1 file
        db.close()
        # B5: never upgraded in place - refused, unchanged; upgraded into a NEW file
        import hashlib
        from puzzle_assistant.brain.migrate import migrate_file
        from puzzle_assistant.brain.store import NeedsMigration
        sha = lambda: hashlib.sha256(open(path, "rb").read()).hexdigest()  # noqa: E731
        before, folder = sha(), sorted(os.listdir(os.path.dirname(path)))
        with self.assertRaises(NeedsMigration):
            Brain(path)
        self.assertEqual((sha(), sorted(os.listdir(os.path.dirname(path)))), (before, folder))
        new = tmp("knowledge-v4.db")
        report = migrate_file(path, new)
        self.assertEqual((report["from_version"], report["to_version"]), (1, 4))
        self.assertEqual(sha(), before)                          # the original, untouched
        b = Brain(new)
        self.assertEqual(b.store.schema_version, 4)
        self.assertEqual(b.claim(1)["owner_id"], "u1")
        self.assertEqual(b.claim(1)["text"], "old claim")
        b.close()


class ReviewVisibilityTests(unittest.TestCase):
    """A contradiction only influences a review if the WHOLE audience of the
    reviewed claim can see it; hidden claims never change review outcomes."""

    def setUp(self):
        self.b = Brain(tmp("knowledge.db"))
        st = self.b.store
        self.ana = st.add_contributor("Ana")
        self.bob = st.add_contributor("Bob")
        self.mod = st.add_contributor("Mod", kind="moderator")

    def tearDown(self):
        self.b.close()

    def _evidence(self, cid, by, h):
        src = self.b.add_source("screenshot", title="proof", content_hash=h, by=by)
        self.b.add_evidence(cid, "direct", src, by=by)

    def _shared(self, text, h):
        c = self.b.learn(text, "user", "Welcome Hall", by=self.ana)
        self._evidence(c, self.ana, h)
        self.b.review(c, "verified", reviewer=self.ana)
        self.b.propose_share(c, by=self.ana)
        self.b.review(c, "verified", reviewer=self.mod)  # moderator's own review
        self.b.approve_share(c, self.mod)
        return c

    def _hidden_ids_not_in(self, obj, hidden):
        blob = repr(obj)
        self.assertNotIn(f"claim {hidden}", blob)
        self.assertNotIn("locker", blob)

    def test_hidden_private_contradiction_does_not_block_a_moderator(self):
        pub = self._shared("The Welcome Hall is open all day.", "h1")
        priv = self.b.learn("My locker in the Welcome Hall closes at night.", "user",
                            "Welcome Hall", by=self.ana)
        self.b.link(priv, pub, "contradicts", by=self.ana)
        self.assertEqual(self.b.claim(pub, viewer=self.bob)["status"], "verified")
        self.b.review(pub, "outdated", reviewer=self.mod, note="re-check")
        self.b.review(pub, "verified", reviewer=self.mod, note="re-checked")   # not blocked
        self.assertEqual(self.b.claim(pub, viewer=self.bob)["status"], "verified")
        self._hidden_ids_not_in(self.b.explain(pub, viewer=self.bob), priv)
        self._hidden_ids_not_in(self.b.explain(pub, viewer=self.mod), priv)

    def test_moderators_own_private_contradiction_does_not_block(self):
        # the reviewer CAN see the contradiction, but the claim's audience can't
        pub = self._shared("The Welcome Hall is open all day.", "h1")
        mine = self.b.learn("My locker in the Welcome Hall closes at night.", "user",
                            "Welcome Hall", by=self.mod)
        self.b.link(mine, pub, "contradicts", by=self.mod)
        self.b.review(pub, "outdated", reviewer=self.mod)
        self.b.review(pub, "verified", reviewer=self.mod)       # audience rule, not reviewer
        self.assertEqual(self.b.claim(pub, viewer=self.bob)["status"], "verified")

    def test_visible_shared_contradiction_still_blocks(self):
        a = self._shared("The Welcome Hall is open all day.", "h1")
        b = self._shared("The Welcome Hall closes at night.", "h2")
        self.b.link(b, a, "contradicts", by=self.mod)            # both shared: everyone sees it
        #  (linking shared knowledge is a moderator action since B2 validation)
        self.assertEqual(self.b.claim(a, viewer=self.bob)["status"], "conflicting")
        with self.assertRaises(BrainError):
            self.b.review(a, "verified", reviewer=self.mod)
        self.b.review(b, "rejected", reviewer=self.mod, note="closes only on holidays")
        self.b.review(a, "verified", reviewer=self.mod)

    def test_personal_claims_with_private_contradiction_still_block(self):
        a = self.b.learn("The Welcome Hall is open all day.", "user", "Welcome Hall", by=self.ana)
        b = self.b.learn("The Welcome Hall closes at night.", "user", "Welcome Hall", by=self.ana)
        self._evidence(a, self.ana, "h1")
        self.b.link(b, a, "contradicts", by=self.ana)            # same owner: she sees both
        with self.assertRaises(BrainError):
            self.b.review(a, "verified", reviewer=self.ana)
        self.b.review(b, "rejected", reviewer=self.ana)
        self.b.review(a, "verified", reviewer=self.ana)

    def test_someone_elses_claim_contradicting_mine_blocks_only_their_side(self):
        pub = self._shared("The Welcome Hall is open all day.", "h1")
        bobs = self.b.learn("The Welcome Hall closes at night.", "user", "Welcome Hall",
                            by=self.bob)
        self._evidence(bobs, self.bob, "h3")
        self.b.link(bobs, pub, "contradicts", by=self.bob)      # Bob sees both sides
        with self.assertRaises(BrainError):
            self.b.review(bobs, "verified", reviewer=self.bob)  # his claim is blocked
        self.b.review(pub, "outdated", reviewer=self.mod)
        self.b.review(pub, "verified", reviewer=self.mod)       # the shared one is not

    def test_shared_candidate_audience_includes_moderators(self):
        cand = self.b.learn("The Welcome Hall is open all day.", "user", "Welcome Hall",
                            by=self.ana)
        self._evidence(cand, self.ana, "h1")
        self.b.propose_share(cand, by=self.ana)
        priv = self.b.learn("My locker in the Welcome Hall closes at night.", "user",
                            "Welcome Hall", by=self.ana)
        self.b.link(priv, cand, "contradicts", by=self.ana)
        self.assertIsNone(self.b.claim(priv, viewer=self.mod))   # moderators can't see it
        self.assertEqual(self.b.claim(cand, viewer=self.mod)["status"], "submitted")
        self.b.review(cand, "verified", reviewer=self.mod)        # not blocked by it
        self._hidden_ids_not_in(self.b.explain(cand, viewer=self.mod), priv)
        # a shared claim contradicting the candidate IS visible to its audience
        cand2 = self.b.learn("The Welcome Hall has no doors.", "user", "Welcome Hall", by=self.ana)
        self._evidence(cand2, self.ana, "h4")
        self.b.propose_share(cand2, by=self.ana)
        pub = self._shared("The Welcome Hall has two doors.", "h5")
        self.b.link(cand2, pub, "contradicts", by=self.ana)
        with self.assertRaises(BrainError):
            self.b.review(cand2, "verified", reviewer=self.mod)

    def test_hidden_contradiction_cannot_change_a_review_outcome(self):
        def outcome(with_hidden):
            b = Brain(tmp("k.db"))
            st = b.store
            ana, mod = st.add_contributor("Ana"), st.add_contributor("Mod", kind="moderator")
            c = b.learn("The Welcome Hall is open all day.", "user", "Welcome Hall", by=ana)
            src = b.add_source("screenshot", title="p", content_hash="h", by=ana)
            b.add_evidence(c, "direct", src, by=ana)
            b.review(c, "verified", reviewer=ana)
            b.propose_share(c, by=ana)
            b.review(c, "verified", reviewer=mod)  # moderator's own review
            b.approve_share(c, mod)
            if with_hidden:
                p = b.learn("My private note: the hall closes.", "user", "Welcome Hall", by=ana)
                b.link(p, c, "contradicts", by=ana)
            res = []
            for st_ in ("outdated", "verified", "awaiting_verification", "verified"):
                try:
                    b.review(c, st_, reviewer=mod)
                    res.append(("ok", b.claim(c, viewer=mod)["status"]))
                except BrainError as exc:
                    res.append(("refused", str(exc)))
            b.close()
            return res
        self.assertEqual(outcome(False), outcome(True))


class AdapterTests(unittest.TestCase):
    """The quiz memory and the World Atlas are read, never written."""

    def setUp(self):
        self.qpath, self.apath = tmp("habbosk.db"), tmp("atlas.db")
        m = Memory(self.qpath)
        m.save(Question("¿QUIÉN SOY?", "Soy un asistente NPC", ["Rubén", "Mateo", "Leo"]),
               Answer(choice="Leo", explanation="Leo cubre el turno del gimnasio.",
                      source="game", verified=True))
        m.save(Question("PREGUNTA", "¿Capital de Francia?", ["Roma", "París"]),
               Answer(choice="París", source="ai"))
        m.close()
        a = Atlas(self.apath)
        a.import_input()
        a.record_visit(13, "Ayuntamiento de Ensenada", None, None, "start")
        a.record_visit(41, "Avenida Alameda", None, 13, "walk")
        a.close()
        self.before = (file_hash(self.qpath), file_hash(self.apath))
        self.b = Brain(tmp("knowledge.db"), self.qpath, self.apath)

    def tearDown(self):
        self.b.close()

    def test_results_from_both_modules_with_their_meaning(self):
        hits = self.b.search("asistente NPC Leo gimnasio")
        q = [h for h in hits if h.module == "quiz_memory"]
        self.assertEqual((q[0].status, q[0].origin), ("verified", "observed"))
        self.assertIn("Leo", q[0].text)
        ai = [h for h in self.b.search("capital Francia") if h.module == "quiz_memory"][0]
        self.assertEqual((ai.status, ai.origin), ("submitted", "ai"))   # AI stays unverified
        casino = [h for h in self.b.search("Gran Casino") if h.module == "world_atlas"]
        self.assertTrue(any("12411" in h.text for h in casino))
        walk = [h for h in self.b.search("Ayuntamiento Alameda") if h.record_id == "walk:13-41"]
        self.assertEqual(walk[0].status, "submitted")

    def test_adapters_never_change_the_files(self):
        for q in ("Leo", "Gran Casino", "Avenida Alameda", "nada que ver"):
            self.b.search(q)
        self.b.ask("¿Quién es Leo?")
        self.b.close()
        self.assertEqual((file_hash(self.qpath), file_hash(self.apath)), self.before)
        ro = Brain(tmp("k.db"), self.qpath, self.apath)
        with self.assertRaises(sqlite3.OperationalError):          # opened read-only
            ro.sources[0].ro.db.execute("DELETE FROM questions")
        ro.close()
        self.b = Brain(tmp("k2.db"))

    def test_missing_files_are_not_created(self):
        q, a = tmp("none.db"), tmp("none2.db")
        b = Brain(tmp("k.db"), q, a)
        self.assertEqual(b.search("Leo"), [])
        b.close()
        self.assertFalse(os.path.exists(q) or os.path.exists(a))

    def test_ask_does_not_invent_answers_yet(self):
        r = self.b.ask("¿Quién es Leo?")
        self.assertFalse(r.answered)
        self.assertEqual(r.basis, "insufficient")
        self.assertTrue(r.hits)


class IndependenceTests(unittest.TestCase):
    def test_brain_does_not_load_the_interface(self):
        code = ("import sys; sys.path.insert(0, %r); import puzzle_assistant.brain as b; "
                "b.Brain(%r).close(); "
                "print('tkinter' in sys.modules, 'puzzle_assistant.ui' in sys.modules)") % (
                    ROOT, tmp("k.db"))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(out.stdout.strip(), "False False", out.stderr)


if __name__ == "__main__":
    unittest.main()
