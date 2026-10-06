"""Import from BookNLP's files, export back, and the rebuilt .book and .book.html."""
from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from luna.core import bookfile
from luna.core.store import Store, create_project, find_book_ids
from tests import fixture
from tests.fixture import BookCase, SECOND, ords, write_book

FILES = ("tokens", "entities", "supersense", "quotes")


class Import(BookCase):
    def test_counts_and_metadata(self):
        i = self.st.info()
        self.assertEqual(i["counts"], {"tokens": 49, "entities": 15, "supersenses": 3, "quotes": 2})
        self.assertEqual((i["n_sentences"], i["n_paragraphs"], i["book_id"]), (8, 3, "fix"))
        self.assertEqual(self.st.meta["layers"], ["entities", "supersenses", "quotes"])
        self.assertEqual(self.st.meta["n_offset_mismatches"], 0)
        self.assertEqual(self.st.meta["join_style"], {"entities": True, "supersenses": True, "quotes": True})
        self.assertIn(".book", self.st.meta["passthrough"])

    def test_find_book_ids(self):
        self.assertEqual(find_book_ids(self.out), ["fix"])

    def test_refuses_to_overwrite_a_working_copy(self):
        with self.assertRaises(SystemExit):
            create_project(self.db_path, self.out, self.text_path)

    def test_wrong_text_is_caught(self):
        other = self.tmp / "other.txt"
        other.write_text("x" * len(self.st.meta["text"]), encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            create_project(self.tmp / "b.sqlite", self.out, other)
        self.assertIn("don't match", str(cm.exception))
        self.assertFalse((self.tmp / "b.sqlite").exists())  # nothing is created for a wrong text

    def test_token_ids_must_be_consecutive(self):
        p = self.out / "fix.tokens"
        lines = p.read_text().split("\n")
        cols = lines[3].split("\t")
        cols[3] = "99"
        lines[3] = "\t".join(cols)
        p.write_text("\n".join(lines))
        with self.assertRaises(SystemExit):
            create_project(self.tmp / "c.sqlite", self.out, self.text_path)

    def test_a_malformed_file_is_reported_and_leaves_nothing_behind(self):
        """Whatever is wrong inside a BookNLP file, the user gets a message naming the book, not a traceback, and no
        half-made working copy stays in projects/."""
        cases = {
            "fix.tokens": lambda t: t.replace("\t0\t", "\tx\t", 1),                         # a non-number where a position goes
            "fix.entities": lambda t: t.replace("COREF", "GROUP", 1),                          # a missing column
            "fix.quotes": lambda t: "\n".join(l if i != 1 else l.split("\t")[0] for i, l in enumerate(t.split("\n"))),   # a short row
        }
        for name, damage in cases.items():
            with self.subTest(file=name):
                for f in self.out.glob("fix.*"):
                    f.unlink()
                write_book(self.out, name="fix")
                (self.out / name).write_text(damage((self.out / name).read_text()), encoding="utf-8")
                db = self.tmp / f"bad-{name}.sqlite"
                with self.assertRaises(SystemExit) as cm:
                    create_project(db, self.out, self.text_path)
                self.assertIn("fix", str(cm.exception))
                self.assertFalse(db.exists())

    def test_several_books_need_a_choice(self):
        write_book(self.tmp / "output" / "fix", name="other")
        with self.assertRaises(SystemExit):
            create_project(self.tmp / "d.sqlite", self.out, self.text_path)
        self.assertEqual(create_project(self.tmp / "d.sqlite", self.out, self.text_path, "other"), "other")

    def test_extra_token_columns_survive_the_round_trip(self):
        p = self.out / "fix.tokens"
        lines = p.read_text().rstrip("\n").split("\n")
        lines = [lines[0] + "\tcolour"] + [f"{l}\tc{i}" for i, l in enumerate(lines[1:])]
        p.write_text("\n".join(lines) + "\n")
        db = self.tmp / "e.sqlite"
        create_project(db, self.out, self.text_path)
        st = Store(db)
        self.addCleanup(st.db.close)
        st.export(self.tmp / "e-out")
        got = (self.tmp / "e-out" / "fix.tokens").read_text().split("\n")
        self.assertEqual(got[0].split("\t")[-1], "colour")
        self.assertEqual(got[5].split("\t")[-1], "c4")


class Export(BookCase):
    def export(self, name="out"):
        dest = self.tmp / name
        self.st.export(dest)
        return dest

    def test_no_edits_gives_back_bookNLPs_files_exactly(self):
        dest = self.export()
        for ext in FILES:
            self.assertEqual((dest / f"fix.{ext}").read_text(), (self.out / f"fix.{ext}").read_text(), ext)

    def test_rebuilt_book_matches_bookNLPs_apart_from_the_added_names(self):
        dest = self.export()
        rebuilt = json.loads((dest / "fix.book").read_text())
        for ch in rebuilt["characters"]:
            self.assertIn("name", ch)
            del ch["name"]
        self.assertEqual(rebuilt, json.loads((self.out / "fix.book").read_text()))

    def test_report_and_extra_files(self):
        dest = self.export()
        self.assertIn("<h2>Named characters</h2>", (dest / "fix.book.html").read_text())
        for f in ("fix.changes.md", "fix.validation.tsv", "fix.characters.tsv"):
            self.assertTrue((dest / f).exists(), f)
        head = (dest / "fix.characters.tsv").read_text().split("\n")[0]
        self.assertEqual(head, "id\tname\ttype\tpronouns\tmentions\tquotes\tnot_a_character\tcollection_id\tcollection_name")

    def test_a_name_you_type_cannot_inject_markup_into_the_report(self):
        self.st.edit_group(0, {"name": "<b>Holmes</b> & co"})
        html = (self.export() / "fix.book.html").read_text()
        self.assertNotIn("<b>Holmes</b> &", html)
        self.assertIn("&lt;b&gt;Holmes&lt;/b&gt; &amp; co", html)

    def test_original_text_is_copied_and_named_after_the_book(self):
        dest = self.export()
        self.assertEqual((dest / "fix.txt").read_text(), self.st.meta["text"])

    def test_export_refuses_an_existing_folder(self):
        dest = self.export()
        with self.assertRaises(FileExistsError):
            self.st.export(dest)

    def test_a_failed_export_leaves_no_folder_behind(self):
        """The analyser takes the newest folder for an export, so one that stopped half way must not stay."""
        def boom(*args):
            raise OSError("disk full")
        self.st._export_layers = boom
        dest = self.tmp / "half"
        with self.assertRaises(OSError):
            self.st.export(dest)
        self.assertFalse(dest.exists())

    def test_changes_log_and_validation(self):
        self.st.edit_token(0, {"pos": "WEIRD"})
        self.st.edit_group(0, {"name": "Sherlock"})
        self.st.set_reviewed([0], True)
        self.st.edit_entities([self.uid_at(7, 0, 1)], {"cat": "VAR"})
        r = self.st.export(self.tmp / "o")
        log = (self.tmp / "o" / "fix.changes.md").read_text()
        self.assertIn("WEIRD", log)
        self.assertIn("Sherlock", log)
        self.assertNotIn("reviewed", log)
        val = (self.tmp / "o" / "fix.validation.tsv").read_text().split("\n")
        self.assertIn("tokens\t0\tpos\tWEIRD\tHolmes", val)
        self.assertEqual(r["issues"], 1)  # VAR is one of the known entity types

    def test_var_is_exported_like_any_other_type(self):
        self.st.edit_entities([self.uid_at(7, 0, 1)], {"cat": "VAR"})
        dest = self.export()
        self.assertIn("\tNOM\tVAR\tThe violin", (dest / "fix.entities").read_text())
        rows = [l.split("\t") for l in (dest / "fix.characters.tsv").read_text().strip().split("\n")[1:]]
        self.assertEqual({r[0]: r[2] for r in rows}["3"], "VAR")

    def test_supersense_and_quote_layers_are_optional(self):
        (self.out / "fix.supersense").unlink()
        (self.out / "fix.quotes").unlink()
        db = self.tmp / "f.sqlite"
        create_project(db, self.out, self.text_path)
        st = Store(db)
        self.addCleanup(st.db.close)
        st.export(self.tmp / "f-out")
        self.assertFalse((self.tmp / "f-out" / "fix.supersense").exists())
        self.assertFalse((self.tmp / "f-out" / "fix.quotes").exists())

    def test_everything_survives_a_second_import(self):
        """Edit a lot, export, start a new working copy from the export, export again: nothing changes."""
        st = self.st
        st.split_token(st.uid_at[fixture.SENT_START[3]], "Mrs .")
        st.merge_tokens(*ords(1, 7, 8))
        st.split_sentence(fixture.SENT_START[7] + 3)
        st.set_paragraph(6, True)
        st.edit_token(2, {"lemma": "greeted", "dep": "conj"})
        st.regroup([self.uid_at(6, 2)], None, "The maid")
        st.merge_groups(2, [3])
        a, b = ords(2, 2, 2)
        st.create_span("entities", {"s": a, "e": b, "cat": "VAR", "prop": "NOM"})
        st.create_span("supersenses", {"s": a, "e": b, "cat": "noun.location"})
        st.delete_span("supersenses", self.uid_at(0, 1, table="supersenses"))
        st.set_speakers([self.uid_at(1, 0, 5, table="quotes")], 0)
        st.edit_span("entities", self.uid_at(4, 5), {"cat": "LOC", "coref": 4})
        st.edit_span("quotes", self.uid_at(4, 0, 7, table="quotes"), {"e": ords(4, 8)[0] - 1})
        first = self.export("first")
        db = self.tmp / "second.sqlite"
        create_project(db, first, self.text_path, "fix")
        st2 = Store(db)
        self.addCleanup(st2.db.close)
        st2.export(self.tmp / "second-out")
        for ext in FILES:
            self.assertEqual((self.tmp / "second-out" / f"fix.{ext}").read_text(), (first / f"fix.{ext}").read_text(), ext)
        # and every token still lines up with the original text
        text = st.meta["text"]
        for line in (first / "fix.tokens").read_text().strip().split("\n")[1:]:
            c = line.split("\t")
            self.assertEqual(bookfile_word(text, int(c[6]), int(c[7])), c[4])


def bookfile_word(text, a, b):
    from luna.core.store import filter_ws
    return filter_ws(text[a:b])


class RebuiltBook(BookCase):
    def test_renamed_group_shows_up_in_both_files(self):
        self.st.edit_group(0, {"name": "Mr. Sherlock Holmes"})
        chardata, html, used = self.st.build_book()
        names = {c["id"]: c["name"] for c in chardata["characters"]}
        self.assertEqual(names[0], "Mr. Sherlock Holmes")
        self.assertIn("<b>Mr. Sherlock Holmes</b>", html)
        self.assertIn("bundled", used)

    def test_edits_feed_the_character_list(self):
        chardata, _, _ = self.st.build_book()
        holmes = next(c for c in chardata["characters"] if c["id"] == 0)
        self.assertEqual(holmes["count"], 4)
        self.st.regroup([self.uid_at(6, 0)], 1)  # Holmes thanked her → Watson thanked her
        chardata, _, _ = self.st.build_book()
        self.assertEqual(next(c for c in chardata["characters"] if c["id"] == 0)["count"], 3)
        self.st.undo()
        chardata, _, _ = self.st.build_book()
        self.assertEqual(next(c for c in chardata["characters"] if c["id"] == 0)["count"], 4)

    def test_pronouns_combine_when_groups_merge_and_can_be_set_by_hand(self):
        self.st.merge_groups(0, [2])  # Holmes (he) + Mrs. Hudson (she)
        chardata, _, _ = self.st.build_book()
        g = next(c for c in chardata["characters"] if c["id"] == 0)["g"]
        self.assertEqual(set(g["inference"]), {"he/him/his", "she/her", "they/them/their"})
        self.assertAlmostEqual(sum(g["inference"].values()), 1.0, places=6)
        self.st.edit_group(0, {"pronoun": "they/them/their"})
        chardata, _, _ = self.st.build_book()
        g = next(c for c in chardata["characters"] if c["id"] == 0)["g"]
        self.assertEqual((g["argmax"], g["max"]), ("they/them/their", 1.0))

    def test_other_entity_types_can_be_included(self):
        self.st.merge_groups(3, [4])  # two FAC mentions: enough for BookNLP's rule
        self.assertEqual({c["id"] for c in self.st.build_book()[0]["characters"]}, {0, 1, 2})
        self.st.set_book_types(["PER", "FAC"])
        chardata, html, _ = self.st.build_book()
        by = {c["id"]: c for c in chardata["characters"]}
        self.assertEqual({k: v["type"] for k, v in by.items()}, {0: "PER", 1: "PER", 2: "PER", 3: "FAC"})
        self.assertEqual(by[3]["count"], 2)
        with self.assertRaises(Exception):
            self.st.set_book_types([])
        self.st.set_book_types(["PER"])
        self.assertNotIn("type", self.st.build_book()[0]["characters"][0])

    def test_var_can_be_included_and_gets_its_own_report_section(self):
        self.assertNotIn("<h3>VAR</h3>", self.st.build_book()[1])
        self.st.edit_entities([self.uid_at(7, 0, 1), self.uid_at(7, 4, 5)], {"cat": "VAR"})
        self.st.merge_groups(3, [4])
        self.assertIn("VAR", [a["cat"] for a in self.st.book_settings()["available"]])
        self.assertEqual(next(a for a in self.st.book_settings()["available"] if a["cat"] == "VAR")["groups"], 1)
        self.assertIn("<h3>VAR</h3>", self.st.build_book()[1])
        self.st.set_book_types(["PER", "VAR"])
        types = {c["id"]: c["type"] for c in self.st.build_book()[0]["characters"]}
        self.assertEqual(types[3], "VAR")

    def test_group_page_preview(self):
        p = self.st.book_preview(0)
        self.assertTrue(p["in_book"])
        self.assertEqual(p["count"], 4)
        self.assertIn("greeted", [x["w"] for x in p["agent"]])
        p = self.st.book_preview(3)
        self.assertFalse(p["in_book"])
        self.assertEqual((p["cat"], p["included_mentions"]), ("FAC", 0))

    def test_bookNLP_is_optional(self):
        # export keeps working when the book files are missing: nothing to rebuild, nothing copied
        (self.out / "fix.book").unlink()
        (self.out / "fix.book.html").unlink()
        db = self.tmp / "nobook.sqlite"
        create_project(db, self.out, self.text_path)
        st = Store(db)
        self.addCleanup(st.db.close)
        r = st.export(self.tmp / "nobook-out")
        self.assertIsNone(r["book"])
        self.assertFalse((self.tmp / "nobook-out" / "fix.book").exists())


class Parity(unittest.TestCase):
    """The bundled copy of get_syntax must agree with the installed BookNLP's."""

    def test_bundled_matches_installed(self):
        saved = bookfile._impl
        try:
            bookfile._impl = None
            get_installed, Tok, used = bookfile.load()
            if "bundled" in used:
                self.skipTest("BookNLP isn't importable here")
            tmp = Path(__import__("tempfile").mkdtemp(prefix="bne-parity-"))
            self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
            out = {}
            for label, impl in (("installed", None), ("bundled", fixture.BUNDLED)):
                bookfile._impl = impl if impl else (get_installed, Tok, used)
                write_book(tmp / label / "fix", book=SECOND)
                out[label] = json.loads((tmp / label / "fix" / "fix.book").read_text())
            self.assertEqual(out["installed"], out["bundled"])
        finally:
            bookfile._impl = saved


class HelperFunctions(unittest.TestCase):
    def test_combine_g(self):
        a = {"inference": {"he/him/his": 1.0, "she/her": 0.0}, "argmax": "he/him/his", "max": 1.0, "total": 10}
        b = {"inference": {"he/him/his": 0.0, "she/her": 1.0}, "argmax": "she/her", "max": 1.0, "total": 30}
        g = bookfile.combine_g([(a, 1), (b, 1)])
        self.assertEqual(g["argmax"], "she/her")
        self.assertAlmostEqual(g["inference"]["she/her"], 0.75)
        self.assertEqual(g["total"], 40)
        self.assertEqual(bookfile.combine_g([(a, 3)]), a)
        self.assertIsNone(bookfile.combine_g([(None, 3)]))

    def test_manual_g(self):
        g = bookfile.manual_g("she/her", None)
        self.assertEqual((g["argmax"], g["inference"]["she/her"]), ("she/her", 1.0))
        g = bookfile.manual_g("xe/xem", {"inference": {"he/him/his": 1.0}, "total": 4})
        self.assertEqual((g["inference"], g["total"]), ({"he/him/his": 0.0, "xe/xem": 1.0}, 4))


if __name__ == "__main__":
    unittest.main()
