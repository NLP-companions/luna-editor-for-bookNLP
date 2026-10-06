"""Luna - a BookNLP editor: a local web app for correcting BookNLP output.

  luna                          (opens the library in your browser; `python -m luna` does the same)
  luna new --output DIR --text FILE [--book-id ID]
  luna open [PROJECT]
  luna list

Layout: this file is the web layer (FastAPI routes + command line). The engine is in core/ (see core/__init__.py),
the pages in static/, localonly.py answers only this computer; the tests are in tests/ and the documentation in docs/
(docs/DOCUMENTATION.md and docs/guide.md).

Your own files live in the folder `luna-data` in the folder you start Luna from, or in the folder given with --data (or
LUNA_DATA): projects/ (working copies, one SQLite file per book), exports/ (cleaned BookNLP files), collections.json (what
carries over between books) and library.json (folders, original texts). Your BookNLP output folder is never modified; use Export to write a cleaned copy.
"""
from __future__ import annotations

from typing import Optional

import argparse
import os
import platform
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from luna.core import embed, matching, suggest
from luna.core.collections_store import DISPLAY_DEFAULTS
from luna.core.errors import EditError
from luna.core.store import Store, create_project
from luna.localonly import LocalOnly

HERE = Path(__file__).resolve().parent  # the package folder: the pages are in HERE / "static"


def data_home(option=None) -> Path:
    """The folder that holds your own files (projects/, exports/, library.json, collections.json): `--data`, else the
    LUNA_DATA environment variable, else `luna-data` in the current folder. It is created if it doesn't exist."""
    chosen = option or os.environ.get("LUNA_DATA") or "luna-data"
    home = Path(chosen).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    return home


def build_app(holder: "Holder"):
    """Build the web app: the routes below are a thin layer over Library, Collections and the open Store (`cur()`).
    
    /api/library*, /api/fs: the library page (books, working copies, collections, folder picker). Everything else works
    on the one open book. Edits return the new history entry (`history(1)`) so the page can update Undo/Redo.
    """
    from fastapi import Body, FastAPI, HTTPException
    from fastapi.responses import FileResponse, JSONResponse, Response
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="Luna - a BookNLP editor")
    app.add_middleware(LocalOnly)  # answer only this computer, and changes only from Luna's own page (see localonly.py)
    lib = holder.library

    def cur() -> Store:
        """The open book, or a 409 telling the page to go back to the library."""
        if holder.store is None:
            raise HTTPException(409, "No book is open. Open one from the library.")
        return holder.store

    # Only one book is open at a time. A page still showing another book must not edit this one.
    @app.middleware("http")
    async def same_book(request, call_next):
        """Refuse edits from a page that still shows another book; add X-Last-Saved and keep static files revalidated."""
        want = request.headers.get("x-book")
        path = request.url.path
        if want and path.startswith("/api/") and not path.startswith(("/api/library", "/api/fs")):
            if want != holder.name():
                return JSONResponse(status_code=409, content={
                    "detail": "Another book was opened from the library. Reload this page to continue with it.",
                    "stale": True})
        response = await call_next(request)
        if path.startswith("/api/") and not path.startswith(("/api/library", "/api/fs")) and holder.store is not None:
            response.headers["X-Last-Saved"] = f"{holder.store.last_saved():.3f}"  # drives the header's "Saved …" badge
        if path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"  # always check for a newer script or stylesheet after an update
        return response
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.exception_handler(EditError)
    async def edit_error(_, exc: EditError):
        """Turn an EditError (a refused edit) into a 400 with its message for the user."""
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/")
    def index():
        """The editor page (the library when no book is open)."""
        if holder.store is None:
            from fastapi.responses import RedirectResponse
            return RedirectResponse("/library")
        return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/library")
    def library_page():
        """The library page."""
        return FileResponse(HERE / "static" / "library.html", headers={"Cache-Control": "no-store"})

    @app.get("/settings")
    def settings_page():
        """The settings page: suggestion settings, library folders, display — reachable from the library and from a book."""
        return FileResponse(HERE / "static" / "settings.html", headers={"Cache-Control": "no-store"})

    def ov():
        """The library overview plus the collections, as the library page needs them."""
        return {**lib.overview(holder.name()), "collections": holder.colls.overview()}

    @app.get("/api/library")
    def library():
        """Library overview: folders, books with working copies, other working copies, collections."""
        return ov()

    # ---- collections (carry-over between books)
    colls = holder.colls

    @app.post("/api/library/collections")
    def create_collection(body: dict = Body(...)):
        """Create a collection (optionally putting a book in it)."""
        c = colls.create(str(body.get("name", "")))
        if body.get("key"):
            colls.set_member(str(body["key"]), c["id"])
        holder.relink()
        return {"collection": c, "collections": colls.overview()}

    @app.patch("/api/library/collections/{cid}")
    def rename_collection(cid: str, body: dict = Body(...)):
        """Rename a collection."""
        colls.rename(cid, str(body.get("name", "")))
        return colls.overview()

    @app.delete("/api/library/collections/{cid}")
    def delete_collection(cid: str):
        """Delete a collection and its list."""
        colls.delete(cid)
        holder.relink()
        return colls.overview()

    @app.post("/api/library/member")
    def set_member(body: dict = Body(...)):
        """Put a book in a collection, or remove it."""
        colls.set_member(str(body["key"]), body.get("collection") or None)
        holder.relink()
        return colls.overview()

    @app.get("/api/library/models")
    def models():
        """The downloaded transformer models that can embed speech."""
        return {"models": embed.available_models(), "default": embed.DEFAULT_MODEL}

    @app.get("/api/library/suggestions")
    def suggestion_settings():
        """The settings of every kind of suggestion, their defaults, and what each kind of evidence is (for the dialog)."""
        return {"settings": colls.suggestion_settings(), "defaults": {"collection": matching.DEFAULTS, **suggest.defaults()},
                "spec": suggest.spec()}

    @app.patch("/api/library/suggestions")
    def set_suggestion_settings(body: dict = Body(...)):
        """Change one kind of suggestion's settings (`type`, `values`), or put it back to the defaults (`reset`)."""
        colls.set_suggestion_settings(str(body.get("type")), None if body.get("reset") else body.get("values") or {})
        return suggestion_settings()

    @app.get("/api/library/display-settings")
    def display_settings():
        """Display settings (e.g. how much context to show around a mention) and their defaults."""
        return {"settings": colls.display_settings(), "defaults": dict(DISPLAY_DEFAULTS)}

    @app.patch("/api/library/display-settings")
    def set_display_settings(body: dict = Body(...)):
        """Change display settings, or put them back to the defaults (`reset`)."""
        colls.set_display_settings(None if body.get("reset") else body.get("values") or {})
        return display_settings()

    @app.patch("/api/library/exports-dir")
    def set_exports_dir(body: dict = Body(...)):
        """Change the folder Export writes to (empty path resets it to the default `exports/`)."""
        lib.set_exports_dir(str(body.get("path", "")))
        return ov()

    @app.get("/api/library/list/{lid}")
    def collection_list(lid: str):
        """One collection's list (characters, retyped names, rules), with each character's profile summary (not the profiles
        themselves: they are most of a big collection and the page doesn't need them; the JSON export has them)."""
        return {**colls.get_list(lid, profiles=False), "summaries": colls.summaries(lid)}

    @app.get("/api/library/list/{lid}/export")
    def export_list(lid: str, format: str = "csv"):
        """A list's characters as a CSV (one summary row each) or JSON (everything, with the profile per book) file."""
        name, media, text = colls.export_list(lid, format)
        return Response(text, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/api/library/entries/delete")
    def delete_entries(body: dict = Body(...)):
        """Delete several entries of a list at once."""
        return {"deleted": colls.delete_entries(str(body["list"]), str(body["kind"]), [str(x) for x in body.get("ids") or []])}

    @app.patch("/api/library/entry")
    def edit_entry(body: dict = Body(...)):
        """Edit an entry of a list."""
        return colls.edit_entry(str(body["list"]), str(body["kind"]), str(body["id"]), body.get("fields") or {})

    @app.post("/api/library/entry/delete")
    def delete_entry(body: dict = Body(...)):
        """Delete an entry of a list."""
        colls.delete_entry(str(body["list"]), str(body["kind"]), str(body["id"]))
        return {"ok": True}

    @app.post("/api/library/entry/move")
    def move_entry(body: dict = Body(...)):
        """Move an entry to another list."""
        colls.move_entry(str(body["list"]), str(body["kind"]), str(body["id"]), str(body["to"]))
        return {"ok": True}

    @app.patch("/api/library/book")
    def book_details(body: dict = Body(...)):
        """Save the title, author, year and tags of a book."""
        return lib.set_details(str(body["key"]), body.get("fields") or {})

    @app.post("/api/library/source")
    def add_source(body: dict = Body(...)):
        """Add a folder to search for BookNLP output."""
        lib.add_source(str(body.get("path", "")))
        return ov()

    @app.delete("/api/library/source")
    def remove_source(body: dict = Body(...)):
        """Remove a folder from the library."""
        lib.remove_source(str(body.get("path", "")))
        return ov()

    @app.post("/api/library/start")
    def start(body: dict = Body(...)):
        """Create a working copy of a book and open it."""
        path = lib.start(str(body["key"]), str(body.get("text") or ""), body.get("name") or None)
        holder.open(path)
        return {"open": holder.name()}

    @app.post("/api/library/open")
    def open_project(body: dict = Body(...)):
        """Open a working copy (closing the current one)."""
        holder.open(lib.path_of(str(body["name"])))
        return {"open": holder.name()}

    @app.post("/api/library/rename")
    def rename(body: dict = Body(...)):
        """Rename a working copy (reopening it when it is the open one)."""
        name = str(body["name"])
        was_open = holder.name() == name
        if was_open:
            holder.close()
        reopen = lib.projects / f"{name}.sqlite"
        try:
            reopen = lib.rename(name, str(body.get("new", "")))
        finally:
            if was_open:
                holder.open(reopen)
        return ov()

    @app.post("/api/library/discard")
    def discard(body: dict = Body(...)):
        """Discard a working copy (moved to projects/.discarded)."""
        name = str(body["name"])
        if holder.name() == name:
            holder.close()
        lib.discard(name)
        return ov()

    @app.post("/api/library/reveal")
    def reveal(body: dict = Body(...)):
        """Show a library folder in Finder/Explorer; only folders the library knows."""
        rp = Path(str(body.get("path", ""))).resolve()
        allowed = [lib.exports.resolve(), lib.projects.resolve()] + [Path(s).resolve() for s in lib.state["sources"]]
        if not rp.exists() or not any(rp == a or a in rp.parents for a in allowed):
            raise HTTPException(400, "Can only show folders from the library")
        opener = {"Darwin": ["open"], "Windows": ["explorer"]}.get(platform.system(), ["xdg-open"])
        subprocess.Popen(opener + [str(rp)])
        return {"ok": True}

    @app.get("/api/fs")
    def browse(path: str = "", want: str = "dir"):
        """List a folder for the folder picker."""
        return lib.browse(path or None, want)

    @app.get("/api/info")
    def info():
        """Start-up data for the open book: counts, tag lists, history, progress, last saved."""
        return {**cur().info(), "project_name": holder.name()}

    @app.get("/api/window")
    def window(sent: int = 0, n: int = 15, tok: Optional[int] = None, tok_end: Optional[int] = None):
        """One page of sentences; `tok` jumps to a token or excerpt (`tok_end`) and puts it in the middle."""
        n = max(1, min(n, 200))
        if tok is not None:  # jump to a token or excerpt: a page of at least JUMP_MIN_SENTENCES with it in the middle
            n = max(n, Store.JUMP_MIN_SENTENCES)
            sent = cur().centered_start(tok, tok_end if tok_end is not None else tok, n)
        return cur().window(sent, n)

    @app.get("/api/entity-context/{gid}")
    def entity_context(gid: int, offset: int = 0, limit: int = 20):
        """A page of excerpts around a group's mentions and quotes, for the annotation view's entity-filter mode.
        How much context surrounds each one comes from the Settings page (`settings.display.context_sentences`)."""
        n = colls.display_settings()["context_sentences"]
        return cur().entity_context(gid, max(0, offset), max(1, min(limit, 100)), n)

    @app.get("/api/token/{uid}")
    def token(uid: int):
        """One token's details for the side panel."""
        return cur().token_detail(uid)

    @app.get("/api/span/{layer}/{uid}")
    def span(layer: str, uid: int):
        """One entity, supersense or quote's details."""
        _layer(layer)
        return cur().span_detail(layer, uid)

    @app.get("/api/clusters")
    def clusters():
        """Every coreference group, biggest first (for pickers and lists)."""
        return sorted(cur().clusters().values(), key=lambda c: (-c["count"], c["id"]))

    @app.get("/api/search")
    def search(layer: str, field: str, op: str, value: str = "", offset: int = 0, limit: int = 100,
               where: str = "", without: str = ""):
        """Find: one page of results for a search (layer, field, op, value, where, without)."""
        return cur().search(layer, field, op, value, offset, max(1, min(limit, 500)), where, without)

    @app.get("/api/history")
    def history():
        """The list of edits, newest first."""
        return cur().history()

    @app.patch("/api/token/{uid}")
    def edit_token(uid: int, body: dict = Body(...)):
        """Edit a token's lemma, POS, fine POS, dependency, event or head."""
        return cur().edit_token(uid, body)

    @app.post("/api/span/{layer}")
    def create_span(layer: str, body: dict = Body(...)):
        """Add an entity, supersense or quote."""
        _layer(layer)
        return cur().create_span(layer, body)

    @app.patch("/api/span/{layer}/{uid}")
    def edit_span(layer: str, uid: int, body: dict = Body(...)):
        """Edit a span's boundaries, type, group, speaker or mention."""
        _layer(layer)
        return cur().edit_span(layer, uid, body)

    @app.delete("/api/span/{layer}/{uid}")
    def delete_span(layer: str, uid: int):
        """Delete a span."""
        _layer(layer)
        return cur().delete_span(layer, uid)

    @app.post("/api/token/{uid}/split")
    def split_token(uid: int, body: dict = Body(...)):
        """Split a token by adding spaces to its word."""
        return cur().split_token(uid, str(body.get("word", "")))

    @app.post("/api/tokens/merge")
    def merge_tokens(body: dict = Body(...)):
        """Merge touching tokens."""
        return cur().merge_tokens(int(body["a"]), int(body["b"]))

    @app.post("/api/sentence/split")
    def split_sentence(body: dict = Body(...)):
        """Start a new sentence at a token."""
        return cur().split_sentence(int(body["ord"]))

    @app.post("/api/sentence/merge")
    def merge_sentence(body: dict = Body(...)):
        """Merge a sentence with the next."""
        return cur().merge_sentence(int(body["sent"]))

    @app.post("/api/paragraph")
    def paragraph(body: dict = Body(...)):
        """Start or remove a paragraph break."""
        return cur().set_paragraph(int(body["sent"]), bool(body["on"]))

    @app.post("/api/retag")
    def retag(body: dict = Body(...)):
        """Re-parse one sentence with spaCy and propose changes."""
        return cur().retag_sentence(int(body["sent"]))

    @app.get("/api/groups")
    def groups(cat: str = "", q: str = "", hidden: int = 0, sort: str = "mentions", unchecked: int = 0):
        """The Entities list (filter by type, search, hidden, unchecked; sort)."""
        return cur().groups_list(cat, q, bool(hidden), sort, unchecked=bool(unchecked))

    @app.get("/api/group/{cid}")
    def group(cid: int, offset: int = 0, limit: int = 150):
        """One group's page."""
        return cur().group_detail(cid, offset, max(1, min(limit, 500)))

    @app.patch("/api/group/{cid}")
    def edit_group(cid: int, body: dict = Body(...)):
        """Rename a group, set pronouns, hide it or mark it checked."""
        return cur().edit_group(cid, body)

    @app.get("/api/group/{cid}/profile")
    def group_profile(cid: int):
        """A group's character profile in this book, summarised for display (titles, relations, actions, speech…),
        for the Compare pane."""
        p = cur().profile_summary(cid)
        if p is None:
            raise HTTPException(404, "That group has no mentions")
        return p

    @app.post("/api/groups/merge")
    def merge_groups(body: dict = Body(...)):
        """Merge groups into one."""
        return cur().merge_groups(int(body["target"]), [int(x) for x in body.get("sources", [])])

    @app.post("/api/mentions/regroup")
    def regroup(body: dict = Body(...)):
        """Move mentions to another (or a new) group."""
        t = body.get("target")
        return cur().regroup(body.get("uids", []), None if t in (None, "") else int(t), body.get("name") or None)

    @app.post("/api/mentions/detail")
    def mentions_detail(body: dict = Body(...)):
        """Details of a list of mentions."""
        return cur().mentions_detail([int(u) for u in body.get("uids", [])])

    @app.post("/api/mentions/edit")
    def mentions_edit(body: dict = Body(...)):
        """Set type and/or mention type on several mentions."""
        return cur().edit_entities(body.get("uids", []), body)

    @app.post("/api/mentions/delete")
    def mentions_delete(body: dict = Body(...)):
        """Delete several mentions."""
        return cur().delete_entities(body.get("uids", []))

    @app.post("/api/group/{cid}/variant")
    def move_variant(cid: int, body: dict = Body(...)):
        """Move every mention with one text and mention type to another group."""
        t = body.get("target")
        return cur().move_variant(cid, body["text"], body["prop"], None if t in (None, "") else int(t), body.get("name") or None)

    @app.get("/api/merge-suggestions")
    def merge_suggestions(limit: int = 150, group: Optional[int] = None):
        """Pairs of groups that may be one, with reasons."""
        return cur().merge_suggestions(max(1, min(limit, 500)), group)

    @app.get("/api/fragments")
    def fragments(size: int = 2, cat: str = "", offset: int = 0, limit: int = 25):
        """The fragment clean-up queue (small groups, with candidate targets)."""
        return cur().fragments(max_count=min(10, max(1, size)), cat=cat, offset=max(0, offset),
                               limit=min(100, max(1, limit)))

    @app.post("/api/mentions/scope")
    def mentions_scope(body: dict = Body(...)):
        """Select mentions by scope (sentence, paragraph, quote, conversation, passage, book)."""
        return cur().select_scope(body.get("anchors") or [], str(body.get("scope", "")), body.get("groups", "same"),
                                  body.get("a"), body.get("b"))

    @app.post("/api/quote-rule")
    def quote_rule(body: dict = Body(...)):
        """The quote rule (I/me/my inside a quote belong to its speaker): preview or apply."""
        return cur().quote_rule(str(body.get("scope", "book")), body.get("quote"), body.get("a"), body.get("b"),
                                bool(body.get("apply")), only=body.get("only"), exclude=body.get("exclude"))

    @app.get("/api/nearby")
    def nearby(a: int, b: int, exclude: str = ""):
        """Groups mentioned near a position."""
        ex = [int(x) for x in exclude.split(",") if x.strip().lstrip("-").isdigit()]
        return cur().nearby_groups(a, b, exclude=ex)

    @app.get("/api/group/{cid}/step")
    def step(cid: int, ord: int, dir: str = "next"):
        """The next or previous mention of a group."""
        return cur().step_mention(cid, ord, "prev" if dir == "prev" else "next")

    @app.get("/api/quote-suggestions")
    def quote_suggestions(limit: int = 300):
        """Speaker suggestions for quotes, with reasons and bundles."""
        return cur().quote_suggestions(max(1, min(limit, 2000)))

    @app.post("/api/quote-suggestions/apply")
    def apply_quote_suggestions(body: dict = Body(...)):
        """Apply chosen speaker suggestions in one step."""
        return cur().apply_quote_suggestions(body.get("items") or [])

    @app.post("/api/quote-suggestions/dismiss")
    def dismiss_quote_suggestion(body: dict = Body(...)):
        """Keep BookNLP's speaker for a quote."""
        return cur().dismiss_quote_suggestion(str(body["key"]))

    # ---- narrator rule
    @app.get("/api/narrator")
    def narrator():
        """The narrator setting and candidates."""
        return cur().narrator_info()

    @app.put("/api/narrator")
    def set_narrator(body: dict = Body(...)):
        """Choose the book's narrator."""
        return cur().set_narrator(body.get("group"))

    @app.post("/api/narrator/passage")
    def narrator_passage(body: dict = Body(...)):
        """Give a passage its own narrator."""
        return cur().add_narrator_passage(int(body["a"]), int(body["b"]), body.get("group"))

    @app.delete("/api/narrator/passage/{i}")
    def narrator_passage_rm(i: int):
        """Remove a narrator passage."""
        return cur().remove_narrator_passage(i)

    @app.post("/api/narrator-rule")
    def narrator_rule(body: dict = Body(...)):
        """The narrator rule: preview or apply."""
        return cur().narrator_rule(apply=bool(body.get("apply")), only=body.get("only"), exclude=body.get("exclude"))

    # ---- checks
    @app.get("/api/checks")
    def checks(kinds: str = "", offset: int = 0, limit: int = 1):
        """Links that can't be right, one page at a time."""
        return cur().checks([k for k in kinds.split(",") if k] or None, offset, limit)

    @app.post("/api/checks/dismiss")
    def dismiss_check(body: dict = Body(...)):
        """Mark a check as reviewed and fine."""
        return cur().dismiss_check(str(body["key"]))

    # ---- pronoun pass
    @app.get("/api/ppass")
    def ppass(ord: int = -1, dir: str = "next", uid: Optional[int] = None):
        """The pronoun pass: the next, previous or a given pronoun."""
        return cur().pass_item(uid) if uid is not None else cur().pass_step(ord, dir)

    @app.put("/api/ppass/settings")
    def ppass_settings(body: dict = Body(...)):
        """Save the pronoun pass settings or choose a preset."""
        return cur().set_pass_settings(body.get("include"), body.get("skip"), body.get("preset"), body.get("advance"))

    @app.post("/api/ppass/confirm")
    def ppass_confirm(body: dict = Body(...)):
        """Remember that these pronouns were kept in their group."""
        return cur().confirm_mentions(body.get("uids") or [])

    # ---- quote pass
    @app.get("/api/qpass")
    def qpass(ord: int = -1, dir: str = "next", uid: Optional[int] = None):
        """The quote pass: the next, previous or a given quote."""
        return cur().qpass_item(uid) if uid is not None else cur().qpass_step(ord, dir)

    @app.put("/api/qpass/settings")
    def qpass_settings(body: dict = Body(...)):
        """Save the quote pass settings or choose a preset."""
        return cur().set_qpass_settings(body.get("skip"), body.get("preset"), body.get("advance"))

    @app.post("/api/qpass/confirm")
    def qpass_confirm(body: dict = Body(...)):
        """Remember that these quotes' speakers were kept."""
        return cur().confirm_quotes(body.get("uids") or [])

    # ---- carrying corrections over from the book's collection
    @app.get("/api/carry")
    def carry():
        """Is the book in a collection, and what applies to it."""
        return cur().carry_state()

    @app.get("/api/carry/review")
    def carry_review():
        """What the collection would change in this book."""
        return cur().carry_review()

    @app.post("/api/carry/apply")
    def carry_apply(body: dict = Body(...)):
        """Apply the ticked collection suggestions."""
        return cur().carry_apply(body.get("characters") or [], body.get("assign") or [], body.get("retypes") or [],
                                 body.get("rules") or [])

    @app.post("/api/carry/profiles")
    def carry_profiles():
        """Refresh this book's character profiles in the collection for every linked group."""
        return cur().carry_update_profiles()

    @app.post("/api/carry/embeddings")
    def carry_embeddings():
        """Embed this book's speakers with the chosen transformer model and refresh the linked characters' profiles."""
        return cur().carry_compute_embeddings()

    @app.post("/api/carry/offered")
    def carry_offered():
        """Remember that the collection suggestions were shown."""
        cur().carry_mark_offered()
        return {"ok": True}

    @app.post("/api/carry/rule")
    def carry_rule(body: dict = Body(...)):
        """Save a Find search and change as a rule."""
        return cur().carry_save_rule(body.get("list"), body.get("rule") or {})

    @app.get("/api/hotkeys")
    def hotkeys():
        """The groups pinned to keys 1-9."""
        return cur().hotkeys()

    @app.put("/api/hotkeys")
    def set_hotkey(body: dict = Body(...)):
        """Pin a group to a key, or free the key."""
        c = body.get("group")
        return cur().set_hotkey(str(body.get("key", "")), None if c in (None, "") else int(c))

    @app.post("/api/group-suggestions/dismiss")
    def dismiss(body: dict = Body(...)):
        """Remember that two groups are not the same."""
        return cur().dismiss_pair(str(body["key"]))

    @app.get("/api/quotes")
    def quotes(speaker: str = "all", q: str = "", offset: int = 0, limit: int = 50):
        """The Quotes view: quotes filtered by speaker or text."""
        return cur().quotes_list(speaker, q, offset, max(1, min(limit, 200)))

    @app.post("/api/quotes/speaker")
    def set_speakers(body: dict = Body(...)):
        """Set the speaker of several quotes; with `quote_rule`, also move I/me/you… pronouns inside each one (see
        `Store.set_speakers`)."""
        c = body.get("char_id")
        return cur().set_speakers(body.get("uids", []), None if c in (None, "") else int(c), bool(body.get("quote_rule")))

    # ---- changes to all Find results at once: the search is sent again (layer, sfield, op, svalue, where, without)
    # and `uids` limits it to the ticked results (null = all)
    @app.post("/api/bulk")
    def bulk(body: dict = Body(...)):
        """Set a field on all (or the ticked) Find results."""
        return cur().bulk_edit(body["layer"], body["sfield"], body["op"], body.get("svalue", ""),
                               body.get("uids"), body["field"], body.get("value"),
                               body.get("where") or "", body.get("without") or "")

    @app.post("/api/bulk/create")
    def bulk_create(body: dict = Body(...)):
        """Create entities, supersenses or quotes over all (or the ticked) Find results."""
        return cur().bulk_create(body["layer"], body["sfield"], body["op"], body.get("svalue", ""), body.get("uids"),
                                 body["target"], body.get("fields") or {}, body.get("where") or "", body.get("without") or "")

    @app.post("/api/bulk/delete")
    def bulk_delete(body: dict = Body(...)):
        """Delete all (or the ticked) Find results."""
        return cur().bulk_delete(body["layer"], body["sfield"], body["op"], body.get("svalue", ""), body.get("uids"),
                                 body.get("where") or "", body.get("without") or "")

    @app.post("/api/review")
    def review(body: dict = Body(...)):
        """Mark sentences reviewed or not."""
        return cur().set_reviewed(body.get("sents", []), bool(body.get("on", True)))

    @app.get("/api/review/next")
    def review_next(after: int = -1):
        """The next sentence not yet reviewed."""
        return cur().next_unreviewed(after)

    @app.get("/api/flags")
    def flags():
        """Every flag ("check this later" mark), for the Flags tab and every flag button's state (kept as one list
        client-side rather than a route per target, since there are usually only a handful)."""
        return {"items": cur().flags_list()}

    @app.put("/api/flag")
    def set_flag(body: dict = Body(...)):
        """Flag a mention, quote, group or sentence, or change its note."""
        return cur().set_flag(str(body["kind"]), int(body["target"]), str(body.get("note", "")))

    @app.delete("/api/flag")
    def clear_flag(body: dict = Body(...)):
        """Remove a flag."""
        return cur().clear_flag(str(body["kind"]), int(body["target"]))

    @app.get("/api/book-settings")
    def book_settings():
        """Which entity types the exported .book covers."""
        return cur().book_settings()

    @app.put("/api/book-settings")
    def set_book_settings(body: dict = Body(...)):
        """Choose the entity types of the exported .book."""
        return cur().set_book_types([str(t) for t in body.get("types", [])])

    @app.get("/api/book/{cid}")
    def book_preview(cid: int):
        """What a group's entry in .book will contain."""
        return cur().book_preview(cid)

    @app.get("/api/pending")
    def pending():
        """How many spaCy suggestions are waiting."""
        return {"pending": cur().pending_count()}

    @app.get("/api/proposals")
    def proposals():
        """The waiting spaCy suggestions, by sentence."""
        return cur().proposals()

    @app.post("/api/proposals/accept")
    def accept(body: dict = Body(...)):
        """Accept spaCy suggestions."""
        return cur().accept_proposals([int(i) for i in body.get("ids", [])])

    @app.post("/api/proposals/reject")
    def reject(body: dict = Body(...)):
        """Reject spaCy suggestions."""
        return cur().reject_proposals([int(i) for i in body.get("ids", [])])

    @app.post("/api/undo")
    def undo():
        """Undo the latest step."""
        return {"label": cur().undo(), "history": cur().history(1)}

    @app.post("/api/redo")
    def redo():
        """Redo the latest undone step."""
        return {"label": cur().redo(), "history": cur().history(1)}

    @app.post("/api/export")
    def export():
        """Write a complete export folder to ./exports/BOOK-DATE-TIME."""
        base = f"{cur().meta['book_id']}-{time.strftime('%Y%m%d-%H%M%S')}"
        dest, n = lib.exports / base, 2
        while dest.exists():  # two exports in the same second
            dest, n = lib.exports / f"{base}-{n}", n + 1
        return cur().export(dest)

    def _layer(layer):
        """404 for a layer name that isn't entities, supersenses or quotes."""
        if layer not in ("entities", "supersenses", "quotes"):
            raise HTTPException(404, "Unknown layer")

    return app


class Holder:
    """The one book open at a time (or none), plus the library."""

    def __init__(self, home: Path, spacy_model=None):
        """Create the library and collections for a home folder; no book is open yet."""
        from luna.core.collections_store import Collections
        from luna.core.library import Library
        self.library = Library(home)
        self.colls = Collections(home / "collections.json")
        self.store = None
        self.path = None
        self.spacy_model = spacy_model

    def name(self):
        """The open working copy's name, or None."""
        return self.path.stem if self.path else None

    def open(self, path: Path):
        """Open a working copy (the previous one is closed only after the new one opens)."""
        new = Store(path, self.spacy_model)
        self.close()
        self.store, self.path = new, Path(path)
        self.relink()

    def relink(self):
        """Tell the open book which collection it's in (its key is the library's: output folder + book ID)."""
        st = self.store
        if st is None:
            return
        st.carry_colls = self.colls
        try:
            st.carry_key = f"{Path(st.meta['source_dir']).resolve()}::{st.meta['book_id']}"
        except (KeyError, OSError):
            st.carry_key = None

    def close(self):
        """Close the open book, if any."""
        if self.store is not None:
            with self.store.lock:
                self.store.db.close()
        self.store, self.path = None, None


def serve(project, port: int, open_browser: bool, spacy_model, home: Path):
    """Run the web server (and open the browser) for the library or one working copy; `home` holds your files."""
    import uvicorn

    holder = Holder(home, spacy_model)
    if project is not None:
        holder.open(project)
    app = build_app(holder)
    url = f"http://127.0.0.1:{port}/" + ("" if project is not None else "library")
    what = f"Editing {holder.store.meta['book_id']} ({project.name})" if project is not None else "Library"
    print(f"\n{what}\nYour files are kept in {home}\nOpen {url} — press Ctrl+C to stop.\n")
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main():
    """The command line: library (default), new, open, list."""
    ap = argparse.ArgumentParser(prog="luna", description="Correct BookNLP output in your browser. Run without a command to open the library.")
    sub = ap.add_subparsers(dest="cmd")
    n = sub.add_parser("new", help="create a working copy from BookNLP output and open it")
    n.add_argument("--output", required=True, help="folder BookNLP wrote its output to")
    n.add_argument("--text", required=True, help="the original .txt file you ran BookNLP on")
    n.add_argument("--book-id", help="book ID, if the folder holds several books")
    n.add_argument("--name", help="project name (default: the book ID)")
    o = sub.add_parser("open", help="open an existing working copy")
    o.add_argument("project", nargs="?", help="project name or .sqlite path")
    sub.add_parser("list", help="list working copies")
    lb = sub.add_parser("library", help="open the library (the default)")
    for p in (ap, n, o, lb):
        p.add_argument("--port", type=int, default=8765)
        p.add_argument("--no-browser", action="store_true")
        p.add_argument("--data", metavar="FOLDER", help="where your working copies, exports, library and collections are kept "
                       "(default: `luna-data` in the current folder; or set LUNA_DATA)")
        p.add_argument("--spacy-model", help="spaCy model for re-tagging (default: en_core_web_sm, as BookNLP uses)")
    args = ap.parse_args()

    home = data_home(args.data)
    projects = home / "projects"
    projects.mkdir(exist_ok=True)
    if args.cmd in (None, "library"):
        serve(None, args.port, not args.no_browser, args.spacy_model, home)
        return
    if args.cmd == "list":
        found = sorted(projects.glob("*.sqlite"))
        print("\n".join(p.stem for p in found) if found else "No projects yet. Create one with: luna new ...")
        return
    if args.cmd == "new":
        from luna.core.store import find_book_ids
        book_id = args.book_id
        if book_id is None:
            ids = find_book_ids(Path(args.output))
            book_id = ids[0] if len(ids) == 1 else None
        name = args.name or book_id or "book"
        path = projects / f"{name}.sqlite"
        print(f"Reading BookNLP output from {args.output} …")
        create_project(path, Path(args.output), Path(args.text), args.book_id)
        print(f"Created working copy {path.name}")
        serve(path, args.port, not args.no_browser, args.spacy_model, home)
        return
    if args.project:
        p = Path(args.project)
        path = p if p.suffix == ".sqlite" and p.exists() else projects / f"{args.project}.sqlite"
    else:
        found = sorted(projects.glob("*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not found:
            sys.exit("No projects yet. Create one with: luna new --output DIR --text FILE")
        path = found[0]
    if not path.exists():
        sys.exit(f"No project called {args.project}. Run: luna list")
    serve(path, args.port, not args.no_browser, args.spacy_model, home)


if __name__ == "__main__":
    main()
