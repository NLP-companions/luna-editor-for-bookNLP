"""Tests for core/cleanup_import.py: parsing a cleaned folder's .groups.tsv / .cleanup_log.tsv, and applying them."""
from __future__ import annotations

import json
import unittest

from luna.core.cleanup_import import apply_cleanup_metadata, parse_groups_tsv, parse_review_rows
from luna.core.store import create_project
from tests.fixture import BookCase, MAIN, write_book


class ParsingTests(unittest.TestCase):
    def test_parse_groups_tsv_reads_id_name_and_member_ids(self):
        """A groups.tsv row becomes (group_id, name, [member ids]), skipping the header."""
        text = "group_id\tname\tmember_ids\n3405\tHolmes and Watson\t88,0\n3406\tSir Henry and Watson\t109,0\n"
        self.assertEqual(parse_groups_tsv(text), [(3405, "Holmes and Watson", [88, 0]), (3406, "Sir Henry and Watson", [109, 0])])

    def test_parse_groups_tsv_skips_blank_lines(self):
        """A trailing blank line (as write_text with a final newline produces) isn't a row."""
        self.assertEqual(parse_groups_tsv("group_id\tname\tmember_ids\n1\tA and B\t1,2\n\n"), [(1, "A and B", [1, 2])])

    def test_parse_review_rows_keeps_only_file_equals_review(self):
        """Entities/quotes rows (confident automatic changes) are not review rows."""
        text = ("file\trule\tstart_token\tend_token\ttext\told\tnew\tcontext\n"
                "entities\tname_alias\t1\t2\tHolmes\t5\t6\tsome context\n"
                "review\tturn-taking only (18 people in scene)\t148\t160\t\"quote\"\t88\t88\tparagraph 1\n")
        self.assertEqual(parse_review_rows(text), [(148, 160, "turn-taking only (18 people in scene)")])

    def test_parse_review_rows_empty_without_any_review_row(self):
        """A log with only confident changes gives no review rows."""
        text = "file\trule\tstart_token\tend_token\ttext\told\tnew\tcontext\nentities\tname_alias\t1\t2\tHolmes\t5\t6\tctx\n"
        self.assertEqual(parse_review_rows(text), [])


class ApplyTests(BookCase):
    def test_does_nothing_without_either_file(self):
        """A plain BookNLP import (no .groups.tsv or .cleanup_log.tsv in passthrough) is left alone."""
        before = [dict(r) for r in self.st.db.execute("SELECT * FROM groups")]
        apply_cleanup_metadata(self.st)
        after = [dict(r) for r in self.st.db.execute("SELECT * FROM groups")]
        self.assertEqual(before, after)

    def test_names_a_group_from_groups_tsv(self):
        """A group_id with mentions gets the name groups.tsv gives it."""
        meta = dict(self.st.meta)
        meta["passthrough"] = {**meta.get("passthrough", {}), ".groups.tsv": "group_id\tname\tmember_ids\n1\tHolmes and Watson\t0,2\n"}
        self.st.meta = meta
        apply_cleanup_metadata(self.st)
        self.assertEqual(self.st.clusters()[1]["name"], "Holmes and Watson")

    def test_skips_a_group_id_with_no_mentions(self):
        """A groups.tsv row for an id the imported book never used doesn't raise or create a group."""
        meta = dict(self.st.meta)
        meta["passthrough"] = {**meta.get("passthrough", {}), ".groups.tsv": "group_id\tname\tmember_ids\n999\tNobody\t1,2\n"}
        self.st.meta = meta
        apply_cleanup_metadata(self.st)  # must not raise
        self.assertIsNone(self.st._row("groups", 999))

    def test_flags_the_quote_of_a_review_row(self):
        """A review row whose span matches a quote flags that quote, with the rule as its note."""
        x, y = MAIN.ords(1, 0, 5)  # the fixture book's first quote, `"I am tired," `
        meta = dict(self.st.meta)
        meta["passthrough"] = {**meta.get("passthrough", {}),
                               ".cleanup_log.tsv": ("file\trule\tstart_token\tend_token\ttext\told\tnew\tcontext\n"
                                                     f"review\tturn-taking only\t{x}\t{y}\t\"quote\"\t1\t1\tctx\n")}
        self.st.meta = meta
        apply_cleanup_metadata(self.st)
        quote_uid = self.uid_at(1, 0, 5, table="quotes")
        flag = self.st.flag_of("quotes", quote_uid)
        self.assertIsNotNone(flag)
        self.assertEqual(flag["note"], "turn-taking only")

    def test_skips_a_review_row_whose_span_matches_no_quote(self):
        """A row whose token span isn't a quote in this book is ignored, not an error."""
        meta = dict(self.st.meta)
        meta["passthrough"] = {**meta.get("passthrough", {}),
                               ".cleanup_log.tsv": ("file\trule\tstart_token\tend_token\ttext\told\tnew\tcontext\n"
                                                     "review\tturn-taking only\t9999\t10000\t\"quote\"\t1\t1\tctx\n")}
        self.st.meta = meta
        apply_cleanup_metadata(self.st)  # must not raise
        self.assertEqual(self.st.flags_list(), [])


class ImportIntegrationTests(BookCase):
    """Importing straight from a folder that has those extra files (not just calling apply_cleanup_metadata
    directly): exercises the automatic-on-import wiring in create_project itself."""

    def setUp(self):
        """A second working copy, from a folder that also has .groups.tsv and .cleanup_log.tsv next to it."""
        super().setUp()
        x, y = MAIN.ords(1, 0, 5)
        (self.out / "fix.groups.tsv").write_text("group_id\tname\tmember_ids\n1\tHolmes and Watson\t0,2\n", encoding="utf-8")
        (self.out / "fix.cleanup_log.tsv").write_text(
            "file\trule\tstart_token\tend_token\ttext\told\tnew\tcontext\n"
            f"review\tturn-taking only\t{x}\t{y}\t\"quote\"\t1\t1\tctx\n", encoding="utf-8")
        self.db_path2 = self.tmp / "fix2.sqlite"
        create_project(self.db_path2, self.out, self.text_path)
        from luna.core.store import Store
        self.st2 = Store(self.db_path2, spacy_model="no-such-model")
        self.addCleanup(self.st2.db.close)

    def test_group_is_named_on_import(self):
        """The group named in .groups.tsv already has that name right after create_project."""
        self.assertEqual(self.st2.clusters()[1]["name"], "Holmes and Watson")

    def test_review_quote_is_flagged_on_import(self):
        """The quote a .cleanup_log.tsv review row points at is already flagged right after create_project."""
        a, b = MAIN.ords(1, 0, 5)
        r = self.st2.db.execute("SELECT uid FROM quotes WHERE tok_start=? AND tok_end=?", (a, b)).fetchone()
        self.assertIsNotNone(self.st2.flag_of("quotes", r["uid"]))


if __name__ == "__main__":
    unittest.main()
