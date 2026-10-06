"""The library page's search, collection filter and sorting (static/js/libfilter.js), run under node."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

LIBFILTER = Path(__file__).resolve().parent.parent / "luna" / "static" / "js" / "libfilter.js"

# Four books: a series of two (one edited and half reviewed), one unstarted, one in another collection.
BOOKS = [
    {"key": "k1", "book_id": "houn", "folder": "/out/holmes",
     "details": {"title": "The Hound of the Baskervilles", "author": "Doyle", "year": 1902, "tags": ["novel", "gothic"]}, "projects": [
        {"name": "houn", "sentences": 100, "reviewed": 50, "last_edit": 2000}]},
    {"key": "k2", "book_id": "stud", "folder": "/out/holmes",
     "details": {"title": "A Study in Scarlet", "author": "Doyle", "year": 1887, "tags": ["novel"]}, "projects": [
        {"name": "stud", "sentences": 100, "reviewed": 100, "last_edit": 1000},
        {"name": "stud-2", "sentences": 100, "reviewed": 0, "last_edit": None}]},
    {"key": "k3", "book_id": "10_emma", "folder": "/out/austen", "details": {"author": "Austen"}, "projects": []},
    {"key": "k4", "book_id": "9_persuasion", "folder": "/out/austen", "projects": [
        {"name": "persuasion", "sentences": 10, "reviewed": 1, "last_edit": 3000}]},
]
COLLS = [{"id": "c1", "name": "Sherlock Holmes"}, {"id": "c2", "name": "Austen"}]
MEMBERS = {"k1": "c1", "k2": "c1", "k3": "c2"}


@unittest.skipUnless(shutil.which("node"), "node isn't installed")
class LibraryView(unittest.TestCase):
    def run_js(self, expr):
        script = (f"const L = require({json.dumps(str(LIBFILTER))});"
                  f"const books = {json.dumps(BOOKS)}, collections = {json.dumps(COLLS)}, members = {json.dumps(MEMBERS)};"
                  f"const ctx = {{ members, collections }};"
                  f"const ids = list => list.map(b => b.book_id);"
                  f"console.log(JSON.stringify({expr}));")
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_collection_filter(self):
        self.assertEqual(self.run_js("ids(L.filterBooks(books, {...ctx, coll: 'all'}))"), ["houn", "stud", "10_emma", "9_persuasion"])
        self.assertEqual(self.run_js("ids(L.filterBooks(books, {...ctx, coll: 'c1'}))"), ["houn", "stud"])
        self.assertEqual(self.run_js("ids(L.filterBooks(books, {...ctx, coll: 'none'}))"), ["9_persuasion"])

    def test_search_matches_book_folder_working_copy_and_collection(self):
        find = lambda q: self.run_js(f"ids(L.filterBooks(books, {{...ctx, query: {json.dumps(q)}}}))")
        self.assertEqual(find("HOUN"), ["houn"])  # ignores case
        self.assertEqual(find("austen"), ["10_emma", "9_persuasion"])  # the folder of both, and the collection of one
        self.assertEqual(find("stud-2"), ["stud"])  # a working copy's name
        self.assertEqual(find("sherlock stud"), ["stud"])  # every word must match, in any field
        self.assertEqual(find("nothing like this"), [])
        self.assertEqual(find("baskervilles"), ["houn"])  # title
        self.assertEqual(find("doyle"), ["houn", "stud"])  # author
        self.assertEqual(find("1887"), ["stud"])  # year
        self.assertEqual(find("gothic"), ["houn"])  # a tag
        self.assertEqual(find("gothic novel"), ["houn"])  # both words must be found, here in two tags
        self.assertEqual(find("novel"), ["houn", "stud"])
        self.assertEqual(self.run_js("ids(L.filterBooks(books, {...ctx, coll: 'c1', query: 'holmes'}))"), ["houn", "stud"])
        self.assertEqual(self.run_js("ids(L.filterBooks(books, {...ctx, coll: 'c2', query: 'holmes'}))"), [])

    def test_sorting(self):
        sort = lambda s: self.run_js(f"ids(L.sortBooks(books, {json.dumps(s)}, ctx))")
        self.assertEqual(sort("name"), ["9_persuasion", "10_emma", "houn", "stud"])  # numbers compare as numbers
        self.assertEqual(sort("edited"), ["9_persuasion", "houn", "stud", "10_emma"])  # never edited last
        self.assertEqual(sort("progress"), ["10_emma", "9_persuasion", "houn", "stud"])  # least reviewed first
        self.assertEqual(sort("collection"), ["10_emma", "houn", "stud", "9_persuasion"])  # Austen, Sherlock Holmes, none
        self.assertEqual(sort("no-such-sort"), sort("name"))

    def test_sorting_by_title_author_and_year_puts_missing_values_last(self):
        sort = lambda s: self.run_js(f"ids(L.sortBooks(books, {json.dumps(s)}, ctx))")
        self.assertEqual(sort("title"), ["stud", "houn", "9_persuasion", "10_emma"])  # A Study…, The Hound…, then no title by ID
        self.assertEqual(sort("author"), ["10_emma", "houn", "stud", "9_persuasion"])  # Austen, Doyle, Doyle (by ID), none
        self.assertEqual(sort("year"), ["stud", "houn", "9_persuasion", "10_emma"])  # 1887, 1902, then no year by ID

    def test_a_book_is_called_by_its_title_or_else_its_id(self):
        self.assertEqual(self.run_js("[L.titleOf(books[0]), L.titleOf(books[2])]"), ["The Hound of the Baskervilles", "10_emma"])

    def test_sorting_does_not_change_the_list_it_is_given(self):
        self.assertEqual(self.run_js("(L.sortBooks(books, 'edited', ctx), ids(books))"), ["houn", "stud", "10_emma", "9_persuasion"])


if __name__ == "__main__":
    unittest.main()
