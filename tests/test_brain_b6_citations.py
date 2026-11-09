"""B6 citation presentation (candidate review): PDF page numbers on cited
evidence and challenge examples, a `withheld` flag when a shared source's owner
did not grant its details, and only the source's OWN date - never the date a
copy was made. Nothing new is revealed: no title, reference, text or page of
anything the viewer may not read. Synthetic data only."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import b6_pdf_maker as M  # noqa: E402
from tests.test_brain_b6 import Q_MG, World  # noqa: E402

MG = "Mira maintains the Lantern Garden."


class Citations(World):
    def pdf(self, s, pages, name="g.pdf", **kw):
        p = self.write(name, "", raw=M.make([M.Page(ops) for ops in pages]))
        kw.setdefault("publisher", "Harbour Press")
        return s.import_file(p, author_kind="named", author_name="Ada", **kw)["source_id"]

    def evidence(self, r):
        return [e for c in r.citations for e in c.evidence]

    def test_a_located_pdf_quote_carries_its_page(self):
        s = self.open("ana")
        sid = self.pdf(s, [[M.text(72, 700, "Intro page.")], [M.text(72, 700, MG)]])
        c = s.learn(MG, "imported", "Lantern Garden")
        s.cite(c, sid, MG)
        self.assertEqual([e["page"] for e in s.store.cited_evidence(c)], [2])
        r = s.respond(Q_MG)
        self.assertEqual(r.category, "documented")
        self.assertEqual([(e["page"], e["withheld"]) for e in self.evidence(r)], [(2, False)])

    def test_text_sources_and_legacy_sources_have_no_page(self):
        s = self.open("ana")
        sid = self.imp(s, MG + "\n", "a.txt")
        c = s.learn(MG, "imported", "Lantern Garden")
        s.cite(c, sid, MG)
        leg = s.add_source("document", title="Old guide", captured_at="2026-01-02")
        c2 = s.learn("Lio runs the Rope Shed.", "imported", "Rope Shed")
        s.add_evidence(c2, "direct", leg, "Lio runs the Rope Shed.")
        self.assertEqual([e["page"] for e in s.store.cited_evidence(c)], [None])
        self.assertEqual([(e["page"], e["withheld"]) for e in s.store.cited_evidence(c2)],
                         [(None, False)])
        untitled = s.add_source("document")                # one's own source, no title:
        c3 = s.learn("The Old Pier has room ID 1.", "imported", "Old Pier")   # not withheld
        s.add_evidence(c3, "direct", untitled, "The Old Pier has room ID 1.")
        self.assertEqual([e["withheld"] for e in s.store.cited_evidence(c3)], [False])
        s.store._db.execute("UPDATE main.sources SET title=NULL WHERE id=?", (untitled,))
        s.store._db.commit()                                # even with no title at all, one's
        self.assertEqual([e["withheld"] for e in s.store.cited_evidence(c3)], [False])  # own

    def test_only_the_sources_own_date_is_given(self):
        s = self.open("ana")
        dated = s.add_source("document", title="Dated guide", captured_at="2026-01-02")
        undated = s.add_source("document", title="Undated guide")
        c = s.learn(MG, "imported", "Lantern Garden")
        s.add_evidence(c, "direct", dated, MG)
        s.add_evidence(c, "direct", undated, MG)
        r = s.respond(Q_MG)
        dates = {e["source"]: e["date"] for e in self.evidence(r)}
        self.assertEqual(dates, {"Dated guide": "2026-01-02", "Undated guide": ""})

    def share(self, levels, captured="2026-03-04"):
        ana = self.open("ana")
        sid = self.pdf(ana, [[M.text(72, 700, "Intro page.")], [M.text(72, 700, MG)]],
                       title="Ana's map", captured_at=captured)
        c = ana.learn(MG, "imported", "Lantern Garden")
        ana.cite(c, sid, MG)
        tok = ana.propose(c, {sid: levels})
        m = self.open("mod")
        m.approve(self.fid("ana"), tok)
        return self.open("luis")

    def test_a_shared_copy_without_details_is_withheld_and_shows_nothing_of_it(self):
        luis = self.share(["quote"])
        r = luis.respond(Q_MG)
        ev = self.evidence(r)
        self.assertEqual(len(ev), 1)
        e = ev[0]
        self.assertTrue(e["withheld"])
        self.assertEqual((e["source"], e["date"], e["page"]), ("", "", None))
        self.assertEqual(e["quote"], MG)                    # the granted quote, nothing more
        self.assertNotIn("Ana's map", repr(r))
        self.assertNotIn("2026-03-04", repr(r))
        self.assertNotIn("Harbour Press", repr(r))

    def test_a_stub_only_share_is_withheld_with_no_copy_date(self):
        luis = self.share([])                               # an anonymous stub
        ev = self.evidence(luis.respond(Q_MG))
        self.assertTrue(ev and all(e["withheld"] for e in ev))
        self.assertTrue(all(e["date"] == "" and e["source"] == "" for e in ev))

    def test_granted_details_are_not_withheld(self):
        luis = self.share(["quote", "details"])
        e = self.evidence(luis.respond(Q_MG))[0]
        self.assertEqual((e["withheld"], e["source"], e["date"]), (False, "Ana's map", "2026-03-04"))
        self.assertIsNone(e["page"])                        # no text shared: no page

    def test_a_full_text_share_keeps_the_page_for_others(self):
        luis = self.share(["quote", "full_text"])
        e = self.evidence(luis.respond(Q_MG))[0]
        self.assertEqual((e["page"], e["withheld"]), (2, True))   # details still not granted
        self.assertEqual(e["source"], "")

    def test_withdrawal_removes_everything_page_included(self):
        ana = self.open("ana")
        sid = self.pdf(ana, [[M.text(72, 700, MG)]], title="Ana's map")
        c = ana.learn(MG, "imported", "Lantern Garden")
        ana.cite(c, sid, MG)
        tok = ana.propose(c, {sid: ["quote", "full_text"]})
        self.open("mod").approve(self.fid("ana"), tok)
        self.assertEqual(ana.withdraw_source(sid), "complete")
        r = self.open("luis").respond(Q_MG)
        self.assertEqual(self.evidence(r), [])

    def test_challenge_examples_carry_their_page(self):
        s = self.open("ana")
        c = s.learn(MG, "imported", "Lantern Garden")
        md = self.imp(s, MG + "\n", "a.md")
        s.cite(c, md, MG)
        s.review(c, "verified")
        self.pdf(s, [[M.text(72, 700, "Intro.")], [M.text(72, 700, "Calm day.")],
                     [M.text(72, 700, "Kai maintains the Lantern Garden.")]],
                 name="k.pdf", publisher="Other Press")
        self.imp(s, "Odo maintains the Lantern Garden.\n", "o.txt", publisher="Third Press")
        ch = s.respond(Q_MG).challenges
        pages = {g["publisher"]: [x["page"] for x in g["examples"]] for g in ch}
        self.assertEqual(pages, {"Other Press": [3], "Third Press": [None]})

    def test_private_sources_are_still_never_cited_to_others(self):
        ana = self.open("ana")
        priv = ana.add_source("document", title="ANA-PRIVATE-CITE-MARK", private=True)
        c = ana.learn(MG, "user", "Lantern Garden")
        ana.add_evidence(c, "direct", priv, MG)
        self.assertEqual([e["title"] for e in ana.store.cited_evidence(c)],
                         ["ANA-PRIVATE-CITE-MARK"])            # the owner sees it
        luis = self.open("luis")
        self.assertEqual(luis.store.cited_evidence(c), [])    # nobody else
        self.assertNotIn("ANA-PRIVATE-CITE-MARK", repr(luis.respond(Q_MG)))


if __name__ == "__main__":
    unittest.main()
