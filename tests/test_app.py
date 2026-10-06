"""The web API and the library, driven through FastAPI's test client."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from luna import app as editor_app
from tests.fixture import write_book

LOCAL = "http://127.0.0.1:8765"  # the address test clients use: Luna only answers requests for this computer (localonly.py)


class ApiCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="bne-home-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.src = self.home / "data" / "output"
        text = write_book(self.src / "fix", name="fix")  # the text file lands next to the output folder
        (self.home / "data" / "input").mkdir()
        text.rename(self.home / "data" / "input" / "fix.txt")  # where BookNLP users usually keep it
        (self.home / "editor").mkdir()
        # a model that doesn't exist, so re-tagging behaves the same on every machine
        self.holder = editor_app.Holder(spacy_model="no-such-model", home=self.home / "editor")
        self.client = TestClient(editor_app.build_app(self.holder), base_url=LOCAL)
        self.addCleanup(self.holder.close)

    def get(self, url, **kw):
        r = self.client.get(url, **kw)
        self.assertEqual(r.status_code, 200, f"GET {url}: {r.text[:200]}")
        return r.json()

    def post(self, url, body=None, status=200, method="post"):
        r = self.client.request(method.upper(), url, json=body if body is not None else {})
        self.assertEqual(r.status_code, status, f"{method.upper()} {url}: {r.text[:300]}")
        return r.json()

    def add_and_start(self):
        ov = self.post("/api/library/source", {"path": str(self.src)})
        book = ov["books"][0]
        self.post("/api/library/start", {"key": book["key"], "text": book["text"], "name": "mybook"})
        self.client.headers["X-Book"] = "mybook"
        return book


class Library(ApiCase):
    def test_a_working_copys_summary_is_read_again_only_when_the_file_changes(self):
        self.add_and_start()
        lib = self.holder.library
        path = lib.path_of("mybook")
        first = lib.project_info(path)
        with mock.patch.object(type(lib), "_read_summary", side_effect=AssertionError("read again")):
            self.assertEqual(lib.project_info(path)["tokens"], first["tokens"])        # unchanged: from memory
        self.holder.store.set_reviewed([0, 1], True)
        self.assertEqual(lib.project_info(path)["reviewed"], first["reviewed"] + 2)    # edited: read again

    def test_an_unreadable_library_file_is_kept_aside_not_overwritten(self):
        home = self.home / "other"
        home.mkdir()
        (home / "library.json").write_text("{ not json")
        from luna.core.library import Library as Lib
        lib = Lib(home)
        self.assertEqual(lib.state["sources"], [])
        self.assertTrue(list(home.glob("library.unreadable-*.json")))
        lib.add_source(str(self.home))
        self.assertEqual(Lib(home).state["sources"], [str(self.home.resolve())])      # and the next save is a good file

    def test_nothing_is_open_at_first(self):
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (307, "/library"))
        self.assertEqual(self.client.get("/api/info").status_code, 409)
        self.assertEqual(self.client.get("/library").status_code, 200)
        self.assertEqual(self.get("/api/library")["books"], [])

    def test_add_a_folder_and_find_the_book_and_its_text(self):
        ov = self.post("/api/library/source", {"path": str(self.src)})
        self.assertEqual([b["book_id"] for b in ov["books"]], ["fix"])
        self.assertEqual(ov["books"][0]["text"], str((self.home / "data" / "input" / "fix.txt").resolve()))
        self.assertTrue(self.post("/api/library/source", {"path": str(self.home / "nope")}, status=400)["detail"].startswith("Not a folder"))
        ov = self.client.request("DELETE", "/api/library/source", json={"path": str(self.src.resolve())}).json()
        self.assertEqual(ov["books"], [])

    def test_start_open_rename_discard(self):
        book = self.add_and_start()
        info = self.get("/api/info")
        self.assertEqual((info["book_id"], info["project_name"]), ("fix", "mybook"))
        ov = self.get("/api/library")
        self.assertEqual(ov["open"], "mybook")
        self.assertEqual(ov["books"][0]["projects"][0]["name"], "mybook")
        self.assertEqual(ov["books"][0]["projects"][0]["changes"], 0)
        # a second working copy of the same book, with a name that's already taken
        self.post("/api/library/start", {"key": book["key"], "text": book["text"], "name": "mybook"})
        self.assertEqual({p["name"] for p in self.get("/api/library")["books"][0]["projects"]}, {"mybook", "mybook-2"})
        self.client.headers["X-Book"] = "mybook-2"
        # rename the open one, keep working on it
        ov = self.post("/api/library/rename", {"name": "mybook-2", "new": "third"})
        self.assertEqual(ov["open"], "third")
        self.client.headers["X-Book"] = "third"
        self.get("/api/info")
        self.post("/api/library/rename", {"name": "third", "new": "mybook"}, status=400)
        self.post("/api/library/rename", {"name": "third", "new": "bad/name"}, status=400)
        ov = self.post("/api/library/discard", {"name": "third"})
        self.assertIsNone(ov["open"])
        self.assertTrue(list((self.home / "editor" / "projects" / ".discarded").glob("third-*.sqlite")))
        self.post("/api/library/open", {"name": "nothing"}, status=400)
        self.assertEqual(self.post("/api/library/open", {"name": "mybook"})["open"], "mybook")

    def test_book_details_title_author_year_and_tags(self):
        ov = self.post("/api/library/source", {"path": str(self.src)})
        key = ov["books"][0]["key"]
        self.assertEqual(ov["books"][0]["details"], {"title": "", "author": "", "year": None, "tags": []})
        d = self.post("/api/library/book", {"key": key, "fields": {"title": "  A Study   in Scarlet ", "author": "Doyle", "year": "1887",
                                                                   "tags": "Holmes, novel;  Holmes ,"}}, method="patch")
        self.assertEqual(d, {"title": "A Study in Scarlet", "author": "Doyle", "year": 1887, "tags": ["Holmes", "novel"]})
        d = self.post("/api/library/book", {"key": key, "fields": {"author": "Conan Doyle"}}, method="patch")  # only what is given changes
        self.assertEqual((d["title"], d["author"], d["year"]), ("A Study in Scarlet", "Conan Doyle", 1887))
        ov = self.get("/api/library")
        self.assertEqual(ov["tags"], ["Holmes", "novel"])
        self.assertEqual(ov["books"][0]["details"]["tags"], ["Holmes", "novel"])
        # kept in library.json, so a new start-up still has it
        again = editor_app.Holder(home=self.home / "editor").library.overview()
        self.assertEqual(again["books"][0]["details"]["title"], "A Study in Scarlet")
        self.post("/api/library/book", {"key": key, "fields": {"year": "eighteen"}}, status=400, method="patch")
        self.post("/api/library/book", {"key": "no/such::book", "fields": {"title": "x"}}, status=400, method="patch")
        d = self.post("/api/library/book", {"key": key, "fields": {"title": "", "author": "", "year": "", "tags": []}}, method="patch")
        self.assertEqual(d, {"title": "", "author": "", "year": None, "tags": []})  # emptied fields are forgotten
        self.assertNotIn(key, editor_app.Holder(home=self.home / "editor").library.state["books"])

    def test_a_page_showing_another_book_is_refused(self):
        self.add_and_start()
        r = self.client.get("/api/info", headers={"X-Book": "some-other-book"})
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.json()["stale"])
        self.assertEqual(self.client.get("/api/library", headers={"X-Book": "some-other-book"}).status_code, 200)

    def test_wrong_text_is_reported_and_leaves_nothing_behind(self):
        book = self.post("/api/library/source", {"path": str(self.src)})["books"][0]
        wrong = self.home / "wrong.txt"
        wrong.write_text("nothing like it " * 100)
        r = self.post("/api/library/start", {"key": book["key"], "text": str(wrong)}, status=400)
        self.assertIn("don't match", r["detail"])
        self.assertEqual(list((self.home / "editor" / "projects").glob("*.sqlite")), [])
        self.post("/api/library/start", {"key": book["key"], "text": ""}, status=400)
        self.post("/api/library/start", {"key": "no::such", "text": book["text"]}, status=400)

    def test_exports_are_left_out_of_the_book_list(self):
        self.add_and_start()
        self.post("/api/export")
        exports = self.home / "editor" / "exports"
        self.assertEqual(len(list(exports.iterdir())), 1)
        # an export that lands inside a source folder is still not offered as a book
        ov = self.post("/api/library/source", {"path": str(exports)})
        self.assertEqual([b["book_id"] for b in ov["books"]], ["fix"])
        self.assertEqual(len(ov["books"][0]["projects"][0]["exports"]), 1)

    def test_reveal_only_opens_library_folders(self):
        self.add_and_start()
        with mock.patch.object(editor_app.subprocess, "Popen") as popen:
            self.post("/api/library/reveal", {"path": str(self.src / "fix")})
            self.assertEqual(popen.call_count, 1)
            self.post("/api/library/reveal", {"path": str(self.home)}, status=400)
            self.post("/api/library/reveal", {"path": "/etc"}, status=400)
            self.assertEqual(popen.call_count, 1)

    def test_folder_browser(self):
        d = self.get("/api/fs?" + "path=" + str(self.home / "data"))
        self.assertEqual({"input", "output"}, set(d["dirs"]))
        d = self.get(f"/api/fs?path={self.home / 'data' / 'input'}&want=txt")
        self.assertEqual(d["files"], ["fix.txt"])
        self.assertEqual(self.get(f"/api/fs?path={self.src / 'fix'}")["books_here"], 1)
        self.assertEqual(self.client.get("/api/fs?path=/no/such/folder").status_code, 400)

    def test_collections_through_the_api(self):
        book = self.add_and_start()
        c = self.post("/api/library/collections", {"name": "Series", "key": book["key"]})["collection"]
        ov = self.get("/api/library")
        self.assertEqual(ov["collections"]["members"], {book["key"]: c["id"]})
        self.assertEqual(self.get("/api/carry")["collection"]["name"], "Series")  # the open book is linked straight away
        self.post("/api/library/collections", {"name": "series"}, status=400)
        self.post(f"/api/library/collections/{c['id']}", {"name": "Renamed"}, method="patch")
        self.post("/api/library/member", {"key": book["key"], "collection": None})
        self.assertIsNone(self.get("/api/carry")["collection"])
        self.post("/api/library/member", {"key": book["key"], "collection": c["id"]})
        # a rule saved from Find (the "Save as rule…" button)
        rule = {"layer": "entities", "sfield": "cat", "op": "eq", "svalue": "FAC", "field": "cat", "value": "LOC"}
        self.post("/api/carry/rule", {"list": c["id"], "rule": rule})
        self.assertEqual(self.get(f"/api/library/list/{c['id']}")["rules"][0]["value"], "LOC")
        self.post("/api/carry/rule", {"list": c["id"], "rule": dict(rule, field="coref")}, status=400)
        entry = self.get(f"/api/library/list/{c['id']}")["rules"][0]
        self.post("/api/library/entry/move", {"list": c["id"], "kind": "rules", "id": entry["id"], "to": "*"})
        self.assertEqual(len(self.get("/api/library/list/*")["rules"]), 1)
        self.post("/api/library/entry", {"list": "*", "kind": "rules", "id": entry["id"], "fields": {"label": "mine"}}, method="patch")
        self.post("/api/library/entry/delete", {"list": "*", "kind": "rules", "id": entry["id"]})
        self.post(f"/api/library/collections/{c['id']}", method="delete")
        self.assertEqual(self.get("/api/library")["collections"]["collections"], [])


    def test_a_list_can_be_exported_and_entries_deleted_together(self):
        c = self.post("/api/library/collections", {"name": "Series"})["collection"]
        with self.holder.colls.lock:
            self.holder.colls.data["lists"][c["id"]]["characters"] += [{"id": "k1", "name": "Holmes", "names": {}}, {"id": "k2", "name": "Watson", "names": {}}]
        lst = self.get(f"/api/library/list/{c['id']}")
        self.assertEqual(sorted(lst["summaries"]), ["k1", "k2"])
        r = self.client.get(f"/api/library/list/{c['id']}/export?format=csv")
        self.assertEqual((r.status_code, r.headers["content-disposition"]), (200, 'attachment; filename="series-characters.csv"'))
        self.assertIn("Holmes", r.text)
        self.assertEqual(self.client.get(f"/api/library/list/{c['id']}/export?format=json").json()["list"], "Series")
        self.assertEqual(self.post("/api/library/entries/delete", {"list": c["id"], "kind": "characters", "ids": ["k1", "k2"]})["deleted"], 2)
        c = self.post("/api/library/collections", {"name": "Détectives 探偵"})["collection"]    # a file name a header can carry
        r = self.client.get(f"/api/library/list/{c['id']}/export?format=csv")
        self.assertEqual((r.status_code, r.headers["content-disposition"]), (200, 'attachment; filename="detectives-characters.csv"'))

    def test_suggestion_settings_can_be_changed_and_reset(self):
        r = self.get("/api/library/suggestions")
        self.assertEqual(r["settings"], r["defaults"])
        self.assertIn("same_name", [e["key"] for e in r["spec"]["merge"]["evidence"]])
        r = self.post("/api/library/suggestions", {"type": "collection", "values": {"match": 0.8, "weights": {"speech": 0}, "off": ["title"]}}, method="patch")
        c = r["settings"]["collection"]
        self.assertEqual((c["match"], c["weights"]["speech"], c["weights"]["name"], c["off"]), (0.8, 0.0, 0.35, ["title"]))
        r = self.post("/api/library/suggestions", {"type": "merge", "values": {"weights": {"same_name": 2.5}, "off": ["talk"]}}, method="patch")
        m = r["settings"]["merge"]
        self.assertEqual((m["weights"]["same_name"], m["weights"]["name_part"], m["off"]), (2.5, 2.0, ["talk"]))
        self.assertEqual(self.holder.colls.suggestion_settings()["merge"]["off"], ["talk"])     # kept in collections.json
        for t in ("collection", "merge"):
            r = self.post("/api/library/suggestions", {"type": t, "reset": True}, method="patch")
        self.assertEqual(r["settings"], r["defaults"])
        self.post("/api/library/suggestions", {"type": "nope", "values": {}}, method="patch", status=400)

    def test_display_settings_can_be_changed_and_reset(self):
        r = self.get("/api/library/display-settings")
        self.assertEqual(r["settings"], {"context_sentences": 2})
        r = self.post("/api/library/display-settings", {"values": {"context_sentences": 5}}, method="patch")
        self.assertEqual(r["settings"]["context_sentences"], 5)
        self.assertEqual(self.holder.colls.display_settings()["context_sentences"], 5)  # kept in collections.json
        self.post("/api/library/display-settings", {"values": {"context_sentences": "many"}}, method="patch", status=400)
        self.post("/api/library/display-settings", {"values": {"context_sentences": 99}}, method="patch", status=400)
        r = self.post("/api/library/display-settings", {"reset": True}, method="patch")
        self.assertEqual(r["settings"], r["defaults"])

    def test_exports_dir_can_be_changed_and_reset(self):
        ov = self.get("/api/library")
        default = ov["exports_dir"]
        self.assertFalse(ov["exports_dir_custom"])
        custom = self.home / "elsewhere"
        ov = self.post("/api/library/exports-dir", {"path": str(custom)}, method="patch")
        self.assertEqual((ov["exports_dir"], ov["exports_dir_custom"]), (str(custom.resolve()), True))
        self.assertTrue(custom.is_dir())  # created for you
        self.add_and_start()
        self.post("/api/export")
        self.assertEqual(len(list(custom.iterdir())), 1)  # the export landed here, not in the default folder
        self.post("/api/library/exports-dir", {"path": "not/absolute"}, method="patch", status=400)
        ov = self.post("/api/library/exports-dir", {"path": ""}, method="patch")
        self.assertEqual((ov["exports_dir"], ov["exports_dir_custom"]), (default, False))

    def test_settings_page_is_served(self):
        self.assertEqual(self.client.get("/settings").status_code, 200)


class Editing(ApiCase):
    def setUp(self):
        super().setUp()
        self.add_and_start()

    def test_every_read_endpoint_answers(self):
        for url in ["/api/info", "/api/window?sent=0&n=5", "/api/window?tok=30", "/api/token/3", "/api/span/entities/0", "/api/span/quotes/0",
                    "/api/span/supersenses/0", "/api/clusters", "/api/search?layer=tokens&field=word&op=eq&value=Holmes", "/api/history",
                    "/api/groups", "/api/groups?cat=FAC&q=violin&sort=name&unchecked=1&hidden=1", "/api/group/0", "/api/merge-suggestions",
                    "/api/merge-suggestions?group=0", "/api/fragments?size=5", "/api/nearby?a=5&b=6&exclude=0,1", "/api/group/0/step?ord=0&dir=next",
                    "/api/quote-suggestions", "/api/narrator", "/api/checks", "/api/checks?kinds=mixed,appos&limit=5", "/api/ppass?ord=-1", "/api/qpass?ord=-1",
                    "/api/carry", "/api/carry/review", "/api/hotkeys", "/api/quotes", "/api/quotes?speaker=none&q=x", "/api/review/next",
                    "/api/book-settings", "/api/book/0", "/api/pending", "/api/proposals", "/api/flags",
                    "/api/group/0/profile", "/api/entity-context/0"]:
            self.get(url)

    def test_edit_undo_redo_over_the_api(self):
        r = self.post("/api/token/0", {"lemma": "changed"}, method="patch")
        self.assertIn("lemma", r["undo"])
        self.assertEqual(self.get("/api/token/0")["lemma"], "changed")
        self.assertEqual(self.post("/api/undo")["history"]["redo"], r["undo"])
        self.assertEqual(self.get("/api/token/0")["lemma"], "Holmes")
        self.post("/api/redo")
        self.post("/api/undo")
        self.post("/api/undo", status=400)

    def test_a_jump_to_a_token_centres_the_page_on_it(self):
        with mock.patch.object(editor_app.Store, "JUMP_MIN_SENTENCES", 3):  # the fixture book has only 8 sentences
            self.assertEqual(self.get("/api/window?tok=0&n=3")["first"], 0)  # nothing above the first sentence
            mid = self.get("/api/window?tok=30&n=3")  # in the middle of the book: the token's sentence is the middle one
            self.assertEqual(mid["last"] - mid["first"], 2)
            self.assertEqual(mid["first"] + 1, self.get("/api/token/30")["sent"])
            self.assertEqual(self.get("/api/window?tok=30&tok_end=30&n=3")["first"], mid["first"])
        with mock.patch.object(editor_app.Store, "JUMP_MIN_SENTENCES", 6):  # a small page size is raised to the minimum
            self.assertEqual(self.get("/api/window?tok=30&n=2")["last"] - self.get("/api/window?tok=30&n=2")["first"], 5)
            self.assertEqual(self.get("/api/window?sent=3&n=2")["last"], 4)  # plain paging keeps the chosen size

    def test_every_reply_says_when_the_working_copy_was_last_saved(self):
        r = self.client.get("/api/info")
        first = float(r.headers["X-Last-Saved"])
        self.assertAlmostEqual(first, r.json()["last_saved"], places=2)
        old = first - 1000
        os.utime(self.holder.path, (old, old))  # pretend the file was last written long ago
        self.assertAlmostEqual(float(self.client.get("/api/info").headers["X-Last-Saved"]), old, places=2)  # reading doesn't count
        self.client.patch("/api/token/0", json={"lemma": "x"})
        self.assertGreater(float(self.client.get("/api/info").headers["X-Last-Saved"]), old + 500)  # an edit does
        self.assertNotIn("X-Last-Saved", self.client.get("/api/library").headers)  # the library isn't about one book

    def test_errors_are_shown_to_the_user_as_messages(self):
        r = self.post("/api/token/0", {"pos": ""}, status=400, method="patch")
        self.assertEqual(r["detail"], "pos can't be empty")
        self.post("/api/span/tokens/0", status=404, method="patch")
        self.assertEqual(self.client.get("/api/span/entities/9999").status_code, 400)
        self.assertEqual(self.client.get("/api/group/999").status_code, 400)

    def test_var_is_offered_and_can_be_listed(self):
        self.assertIn("VAR", self.get("/api/info")["tagsets"]["entities.cat"])
        u = self.get("/api/group/3")["mentions"][0]["uid"]
        self.post("/api/mentions/edit", {"uids": [u], "cat": "VAR"})
        cats = self.get("/api/groups")["cats"]
        self.assertEqual(cats["VAR"], 1)
        self.assertEqual([g["id"] for g in self.get("/api/groups?cat=VAR")["items"]], [3])
        self.assertIn("VAR", self.get("/api/info")["observed"]["entities.cat"])

    def test_group_editing_flow(self):
        self.post("/api/group/0", {"name": "Sherlock", "pronoun": "he/him/his"}, method="patch")
        self.post("/api/groups/merge", {"target": 1, "sources": [0]})
        self.assertNotIn(0, [g["id"] for g in self.get("/api/groups")["items"]])
        u = self.get("/api/group/2")["mentions"][0]["uid"]
        r = self.post("/api/mentions/regroup", {"uids": [u], "target": "", "name": "Housekeeper"})
        self.assertEqual(r["moved"], 1)
        self.post("/api/group/2/variant", {"text": "she", "prop": "PRON", "target": None, "name": "Someone"})
        self.post("/api/mentions/delete", {"uids": [u]})
        self.post("/api/hotkeys", {"key": "1", "group": 1}, status=405)
        self.assertEqual(self.client.put("/api/hotkeys", json={"key": "1", "group": 1}).json()["1"]["id"], 1)

    def test_entity_context_uses_the_display_setting(self):
        r = self.get("/api/entity-context/0")
        self.assertEqual(r["total"], 1)  # Holmes: mentions at sentences 0, 4, 6; default context (2) merges them all
        self.post("/api/library/display-settings", {"values": {"context_sentences": 0}}, method="patch")
        r = self.get("/api/entity-context/0")
        self.assertEqual(r["total"], 3)
        self.assertEqual(self.get("/api/entity-context/0?offset=1&limit=1")["items"][0]["excerpt_first"], 4)

    def test_flags_over_the_api(self):
        self.assertEqual(self.get("/api/flags")["items"], [])
        r = self.client.put("/api/flag", json={"kind": "entities", "target": 0, "note": "check this"}).json()
        self.assertIn("Flagged", r["undo"])
        items = self.get("/api/flags")["items"]
        self.assertEqual((len(items), items[0]["note"]), (1, "check this"))
        self.client.request("DELETE", "/api/flag", json={"kind": "entities", "target": 0})
        self.assertEqual(self.get("/api/flags")["items"], [])
        self.assertEqual(self.client.request("DELETE", "/api/flag", json={"kind": "entities", "target": 0}).status_code, 400)

    def test_group_profile_for_the_compare_pane(self):
        r = self.get("/api/group/1/profile")  # Watson
        self.assertEqual(r["mentions"], 4)
        self.assertEqual(self.client.get("/api/group/999/profile").status_code, 404)

    def test_reassigning_a_quote_can_also_move_its_pronouns(self):
        st = self.holder.store
        i_uid = next(r[0] for r in st.db.execute(
            "SELECT x.uid FROM entities x JOIN tokens t ON t.uid=x.tok_start WHERE t.word='I'"))
        q_uid = next(r[0] for r in st.db.execute("SELECT uid FROM quotes WHERE char_id=1"))
        st.regroup([i_uid], 0)  # break it: "I" (in Watson's quote) now wrongly in Holmes's group
        r = self.post("/api/quotes/speaker", {"uids": [q_uid], "char_id": 1})  # plain assign: no flag, no move
        self.assertEqual(r["moved"], 0)
        self.assertEqual(st._row("entities", i_uid)["coref"], 0)
        r = self.post("/api/quotes/speaker", {"uids": [q_uid], "char_id": 1, "quote_rule": True})
        self.assertEqual(r["moved"], 1)
        self.assertEqual(st._row("entities", i_uid)["coref"], 1)

    def test_spans_and_search_and_bulk(self):
        made = self.post("/api/span/entities", {"s": 44, "e": 44, "cat": "VAR", "prop": "NOM", "coref": None})
        self.post(f"/api/span/entities/{made['uid']}", {"coref": None}, method="patch")
        self.assertEqual(self.client.delete(f"/api/span/entities/{made['uid']}").status_code, 200)
        res = self.get("/api/search?layer=entities&field=cat&op=eq&value=FAC")
        self.assertEqual(res["total"], 2)
        r = self.post("/api/bulk", {"layer": "entities", "sfield": "cat", "op": "eq", "svalue": "FAC", "uids": None, "field": "cat", "value": "VAR"})
        self.assertEqual(r["changed"], 2)
        self.post("/api/bulk", {"layer": "entities", "sfield": "cat", "op": "eq", "svalue": "PER", "field": "coref", "value": ""}, status=400)

    def test_find_phrases_filters_and_creating_or_deleting_results(self):
        q = "layer=tokens&field=word&op=phrase&value=Mrs.+Hudson"
        self.assertEqual(self.get(f"/api/search?{q}")["total"], 1)
        self.assertEqual(self.get("/api/search?layer=tokens&field=word&op=eq&value=Holmes&where=quote")["total"], 1)
        self.assertEqual(self.get("/api/search?layer=tokens&field=pos&op=eq&value=NOUN&without=entities")["total"], 1)
        search = {"layer": "tokens", "sfield": "word", "op": "phrase", "svalue": "Mrs. Hudson smiled"}
        r = self.post("/api/bulk/create", {**search, "target": "quotes", "fields": {"char_id": 2}})
        self.assertEqual((r["created"], r["skipped"]), (1, 0))
        self.assertEqual(self.get("/api/quotes")["total"], 3)
        self.post("/api/bulk/create", {**search, "target": "quotes", "fields": {}}, status=400)  # overlaps the quote just made
        self.post("/api/bulk/create", {"layer": "tokens", "sfield": "pos", "op": "eq", "svalue": "NOUN", "without": "entities",
                                       "target": "entities", "fields": {"cat": "VAR", "prop": "NOM", "group_mode": "one"}})
        self.assertEqual(self.get("/api/search?layer=entities&field=cat&op=eq&value=VAR")["total"], 1)
        d = self.post("/api/bulk/delete", {"layer": "entities", "sfield": "cat", "op": "eq", "svalue": "VAR"})
        self.assertEqual(d["deleted"], 1)
        self.post("/api/bulk/delete", {"layer": "tokens", "sfield": "word", "op": "eq", "svalue": "Holmes"}, status=400)
        self.post("/api/bulk/delete", {"layer": "quotes", "sfield": "text", "op": "eq", "svalue": "Mrs. Hudson smiled", "where": "sideways"}, status=400)

    def test_tokens_sentences_and_review(self):
        self.post("/api/sentence/split", {"ord": 2})
        self.post("/api/sentence/merge", {"sent": 0})
        self.post("/api/paragraph", {"sent": 2, "on": True})
        self.post("/api/tokens/merge", {"a": 7, "b": 8})
        self.post("/api/review", {"sents": [0, 1], "on": True})
        self.assertEqual(self.get("/api/review/next?after=-1")["progress"]["reviewed"], 2)
        r = self.post("/api/retag", {"sent": 0}, status=400)  # no spaCy model here
        self.assertIn("spaCy", r["detail"])

    def test_export_and_settings(self):
        r = self.post("/api/export")
        self.assertTrue((Path(r["path"]) / "fix.tokens").exists())
        with mock.patch.object(editor_app.time, "strftime", return_value="20260101-000000"):
            first, second = self.post("/api/export")["path"], self.post("/api/export")["path"]
        self.assertEqual((Path(first).name, Path(second).name), ("fix-20260101-000000", "fix-20260101-000000-2"))
        self.post("/api/book-settings", status=405)
        self.assertEqual(self.client.put("/api/book-settings", json={"types": ["PER", "VAR"]}).json()["types"], ["PER", "VAR"])
        self.assertEqual(self.client.put("/api/book-settings", json={"types": []}).status_code, 400)
        self.assertEqual(self.client.put("/api/ppass/settings", json={"preset": "check"}).json()["preset"], "check")
        self.post("/api/ppass/confirm", {"uids": [0]})
        self.assertEqual(self.client.put("/api/qpass/settings", json={"preset": "check"}).json()["preset"], "check")
        self.post("/api/qpass/confirm", {"uids": [0]})
        self.assertEqual(self.client.put("/api/narrator", json={"group": 0}).json()["cid"], 0)
        self.post("/api/narrator/passage", {"a": 0, "b": 3, "group": 1})
        self.client.delete("/api/narrator/passage/0")
        self.post("/api/narrator-rule", {})
        self.post("/api/quote-rule", {"scope": "book"})
        self.post("/api/mentions/scope", {"anchors": [0], "scope": "sentence"})
        self.post("/api/mentions/detail", {"uids": [0, 1]})
        self.post("/api/quotes/speaker", {"uids": [0], "char_id": ""})
        self.post("/api/quote-suggestions/apply", {"items": [{"quote": 0, "speaker": 1}]})
        self.post("/api/quote-suggestions/dismiss", {"key": "0:1"})
        self.post("/api/group-suggestions/dismiss", {"key": "0-1"})
        self.post("/api/checks/dismiss", {"key": "appos:1"})
        self.post("/api/carry/offered")
        self.post("/api/carry/apply", {})
        self.post("/api/proposals/accept", {"ids": []}, status=400)
        self.post("/api/proposals/reject", {"ids": []})


class Pages(unittest.TestCase):
    def test_static_files_are_served_and_always_revalidated(self):
        home = Path(tempfile.mkdtemp(prefix="bne-pages-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        client = TestClient(editor_app.build_app(editor_app.Holder(home=home)), base_url=LOCAL)
        for path in ("/static/js/app.js", "/static/js/entities.js", "/static/js/rules.js", "/static/js/quotesug.js",
                     "/static/js/passui.js", "/static/js/corefui.js", "/static/js/library.js", "/static/css/app.css",
                     "/static/css/library.css", "/static/index.html"):
            r = client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(r.headers["cache-control"], "no-cache", path)  # an update must not be hidden by a cached script
        self.assertEqual(client.get("/library").status_code, 200)


class LocalOnlyGuard(unittest.TestCase):
    """Only requests for this computer are answered, and changes only when they come from Luna's own page."""
    HOST = "127.0.0.1:8765"
    CHANGE = ("/api/library/display-settings", {"values": {"context_sentences": 4}})  # a request that changes something

    def setUp(self):
        home = Path(tempfile.mkdtemp(prefix="bne-local-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        self.holder = editor_app.Holder(home=home)
        self.client = TestClient(editor_app.build_app(self.holder), base_url=LOCAL)

    def change(self, **headers):
        """Make the change request with the given headers (the Host is Luna's own unless given)."""
        headers.setdefault("host", self.HOST)
        return self.client.patch(self.CHANGE[0], json=self.CHANGE[1], headers=headers)

    def context(self):
        """The context-sentences setting now stored."""
        return self.holder.colls.display_settings()["context_sentences"]

    def test_served_to_127_0_0_1_and_localhost_with_or_without_a_port(self):
        for host in ("127.0.0.1", "127.0.0.1:8765", "localhost", "localhost:8765"):
            self.assertEqual(self.client.get("/library", headers={"host": host}).status_code, 200, host)
            self.assertEqual(self.client.get("/api/library", headers={"host": host}).status_code, 200, host)

    def test_another_name_for_this_computer_is_refused_whatever_the_method(self):
        """DNS rebinding: a page on evil.example made to resolve to 127.0.0.1 still sends its own name in Host."""
        for host in ("evil.example", "evil.example:8765", "127.0.0.1.evil.example", "127.0.0.1:8765@evil.example", "127.0.0.1:abc", "[::1]:8765", ""):
            self.assertEqual(self.client.get("/api/library", headers={"host": host}).status_code, 403, host)
            self.assertEqual(self.change(host=host).status_code, 403, host)
        self.assertIn("127.0.0.1 or localhost", self.client.get("/", headers={"host": "evil.example"}).json()["detail"])

    def test_a_change_asked_for_by_another_web_page_is_refused_and_does_nothing(self):
        before = self.context()
        for origin in ("https://evil.example", "http://evil.example", "null", "http://localhost:8000",  # another site, a sandboxed page, another program here
                       "http://127.0.0.1:9999", "https://127.0.0.1:8765", "http://localhost:8765"):  # another port, https, another spelling of our host
            r = self.change(origin=origin)
            self.assertTrue(r.status_code == 403 and "another web page" in r.json()["detail"], origin)
        self.assertEqual(self.context(), before)

    def test_a_change_from_our_own_page_or_from_a_script_goes_through(self):
        self.assertEqual(self.change(origin=f"http://{self.HOST}").status_code, 200)  # the browser's own page: Origin is the server itself
        self.assertEqual(self.context(), 4)
        self.holder.colls.set_display_settings({"context_sentences": 2})
        self.assertEqual(self.change().status_code, 200)  # no Origin: curl or a script, not a web page
        self.assertEqual(self.context(), 4)

    def test_reading_is_not_refused_for_its_origin(self):
        """Reading changes nothing, and without permission headers a foreign page can't read the answer anyway."""
        r = self.client.get("/api/library", headers={"host": self.HOST, "origin": "https://evil.example"})
        self.assertTrue(r.status_code == 200 and "access-control-allow-origin" not in r.headers)


class DataFolder(unittest.TestCase):
    def test_option_then_environment_then_luna_data(self):
        base = Path(tempfile.mkdtemp(prefix="bne-data-")).resolve()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        with mock.patch.dict(os.environ, {"LUNA_DATA": str(base / "env")}):
            self.assertEqual(editor_app.data_home(str(base / "opt")), base / "opt")  # --data wins, and is created
            self.assertEqual(editor_app.data_home(), base / "env")
        with mock.patch.dict(os.environ):
            os.environ.pop("LUNA_DATA", None)
            with mock.patch("pathlib.Path.mkdir"):  # nothing is made in the folder the tests run from
                self.assertEqual(editor_app.data_home(), Path("luna-data").resolve())
        self.assertTrue((base / "opt").is_dir() and (base / "env").is_dir())


if __name__ == "__main__":
    unittest.main()
