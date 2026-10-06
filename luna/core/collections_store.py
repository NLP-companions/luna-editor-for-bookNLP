"""Collections: what carries over from one book to the next.

A collection (e.g. "Sherlock Holmes") holds books and a list of:
  * characters: name, the names and spellings they go by, pronouns and type, and a profile per book (core/profiles.py);
  * retyped names: a name and the entity type it should have (Baker Street → FAC);
  * saved Find rules: a Find search plus a bulk change, rerun on each new book.
A separate list ("All books") applies to every book, in a collection or not. The settings for matching a book's groups
to these characters (core/matching.py) are kept here too (`settings.matching`), the settings of the other suggestions
(core/suggest.py, `settings.suggestions`), and small display settings (`settings.display`, e.g. how much context to
show around a mention). All of it is edited on the Settings page.

Everything lives in collections.json in the data folder, so it's shared by all working copies.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path

from luna.core import matching, suggest
from luna.core import profiles as P
from luna.core.errors import EditError

ALL = "*"
GENDER_WORDS = {"m": "he", "f": "she", "n": "it", "p": "they"}   # a profile's gender class, as the CSV shows it
KINDS = ("characters", "retypes", "rules")
DISPLAY_DEFAULTS = {"context_sentences": 2}  # sentences of context shown above/below a mention when filtering the annotation view by entity


def _new_id(prefix):
    """A short random id with a one-letter prefix (c collection, k character, r retype, f rule)."""
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def _empty_list():
    """A list with nothing in it for each kind of entry."""
    return {k: [] for k in KINDS}


def _lay_over(old, new):
    """Saved settings with changed values laid over them; nested groups (weights, embedding…) keep the values not given."""
    return {**old, **{k: {**old.get(k, {}), **v} if isinstance(v, dict) else v for k, v in new.items()}}


def _changes(values, defaults):
    """Only the settings that differ from the defaults (nested groups by key), so a default improved later still applies to
    everything you left alone."""
    out = {}
    for k, v in values.items():
        if isinstance(v, dict) and isinstance(defaults.get(k), dict):
            v = _changes(v, defaults[k])
            if v:
                out[k] = v
        elif v != defaults.get(k):
            out[k] = v
    return out


class Collections:
    """The file collections.json: collections, which books belong to which, and each collection's lists. Saved after every change."""
    def __init__(self, path: Path):
        """Load collections.json (an unreadable file is renamed, never overwritten) and make sure every list exists."""
        self.path = Path(path)
        self.lock = threading.RLock()
        self.version = 0  # goes up with every change, so books know when to recompute their matches
        self.data = {"version": 1, "collections": [], "members": {}, "lists": {ALL: _empty_list()}}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except ValueError:
                # keep a copy of the unreadable file rather than overwrite it silently
                self.path.rename(self.path.with_suffix(f".unreadable-{int(time.time())}.json"))
        self.data["lists"].setdefault(ALL, _empty_list())
        for lst in self.data["lists"].values():
            for k in KINDS:
                lst.setdefault(k, [])

    def save(self):
        """Write the file atomically (temp file, then replace)."""
        self.version += 1
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(self._text(), encoding="utf-8")
        os.replace(tmp, self.path)

    def _text(self):
        """The file's text: indented, so it can be read and compared, except that each character's profile of a book stays on
        one line. Profiles are most of the file once a collection has many books, and Python's JSON encoder is about ten
        times faster without indentation, so saving after every change stays quick. (Each profile is swapped for a
        placeholder, the rest is indented, and the compact profiles go back in.)"""
        token = uuid.uuid4().hex
        profiles = []

        def shrink(lst):
            """A copy of a list whose characters carry placeholders instead of their profiles (the data itself is untouched)."""
            chars = []
            for e in lst["characters"]:
                if e.get("profiles"):
                    marks = {}
                    for book, prof in e["profiles"].items():
                        marks[book] = f"{token}:{len(profiles)}"
                        profiles.append(json.dumps(prof, ensure_ascii=False, separators=(",", ":")))
                    e = {**e, "profiles": marks}
                chars.append(e)
            return {**lst, "characters": chars}

        text = json.dumps({**self.data, "lists": {lid: shrink(lst) for lid, lst in self.data["lists"].items()}},
                          indent=1, ensure_ascii=False)
        return re.sub(f'"{token}:(\\d+)"', lambda m: profiles[int(m.group(1))], text)

    # ---- settings
    def matching_settings(self):
        """The settings for matching groups to characters: the defaults with your changes laid over them."""
        with self.lock:
            return matching.settings(self.data.get("settings", {}).get("matching"))

    def set_matching_settings(self, values):
        """Save changed matching settings (checked and cleaned by matching.settings); `None` resets them to the defaults.
        Only values that differ from the defaults are kept."""
        with self.lock:
            st = self.data.setdefault("settings", {})
            new = {} if values is None else _changes(matching.settings(_lay_over(st.get("matching", {}), values)), matching.DEFAULTS)
            if new:
                st["matching"] = new
            else:
                st.pop("matching", None)
            self.save()
            return self.matching_settings()

    def suggestion_settings(self):
        """The settings of every kind of suggestion (core/suggest.py), with collection matching's under "collection"."""
        with self.lock:
            return {"collection": self.matching_settings(), **suggest.settings(self.data.get("settings", {}).get("suggestions"))}

    def set_suggestion_settings(self, typ, values):
        """Save changed settings of one kind of suggestion (`values`: weights, off, thresholds…); `None` resets it. Only values
        that differ from the defaults are kept."""
        if typ == "collection":
            self.set_matching_settings(values)
            return self.suggestion_settings()
        if typ not in suggest.TYPES:
            raise EditError("Unknown kind of suggestion")
        with self.lock:
            st = self.data.setdefault("settings", {}).setdefault("suggestions", {})
            new = {} if values is None else _changes(suggest.settings({typ: _lay_over(st.get(typ, {}), values)})[typ],
                                                     suggest.defaults()[typ])
            if new:
                st[typ] = new
            else:
                st.pop(typ, None)
            self.save()
            return self.suggestion_settings()

    def display_settings(self):
        """Settings for how much the editor shows: the defaults with your changes laid over them."""
        with self.lock:
            return {**DISPLAY_DEFAULTS, **self.data.get("settings", {}).get("display", {})}

    def set_display_settings(self, values):
        """Save changed display settings (currently just `context_sentences`); `None` resets them to the defaults. Only
        values that differ from the defaults are kept."""
        with self.lock:
            st = self.data.setdefault("settings", {})
            if values is None:
                st.pop("display", None)
                self.save()
                return self.display_settings()
            merged = {**self.display_settings(), **values}
            try:
                cs = int(merged["context_sentences"])
            except (TypeError, ValueError):
                raise EditError("Context sentences must be a number")
            if not 0 <= cs <= 20:
                raise EditError("Context sentences must be between 0 and 20")
            merged["context_sentences"] = cs
            new = _changes(merged, DISPLAY_DEFAULTS)
            if new:
                st["display"] = new
            else:
                st.pop("display", None)
            self.save()
            return self.display_settings()

    def entries(self, book_key):
        """The character entries of the lists that apply to a book: {entry id: (list id, list label, entry)}."""
        out = {}
        for lid, label, lst in self.lists_for(book_key):
            for e in lst["characters"]:
                out.setdefault(e["id"], (lid, label, e))
        return out

    # ---- collections and membership
    def _coll(self, cid):
        """One collection by id (EditError when it's gone)."""
        c = next((c for c in self.data["collections"] if c["id"] == cid), None)
        if not c:
            raise EditError("That collection no longer exists")
        return c

    def overview(self):
        """Collections with their books and counts, book → collection membership, and the all-books counts, for the library page."""
        with self.lock:
            out = []
            for c in self.data["collections"]:
                lst = self.data["lists"].get(c["id"], _empty_list())
                out.append({**c, "books": sorted(k for k, v in self.data["members"].items() if v == c["id"]),
                            "counts": {k: len(lst[k]) for k in KINDS}})
            al = self.data["lists"][ALL]
            return {"collections": out, "members": dict(self.data["members"]),
                    "all": {"counts": {k: len(al[k]) for k in KINDS}}}

    def create(self, name):
        """Add a collection (names must be unique, ignoring case) with its empty list."""
        name = (name or "").strip()
        if not name:
            raise EditError("Give the collection a name")
        with self.lock:
            if any(c["name"].lower() == name.lower() for c in self.data["collections"]):
                raise EditError(f"There's already a collection called {name}")
            c = {"id": _new_id("c"), "name": name, "created": time.time()}
            self.data["collections"].append(c)
            self.data["lists"][c["id"]] = _empty_list()
            self.save()
            return c

    def rename(self, cid, name):
        """Rename a collection."""
        name = (name or "").strip()
        if not name:
            raise EditError("Give the collection a name")
        with self.lock:
            self._coll(cid)["name"] = name
            self.save()

    def delete(self, cid):
        """Delete a collection, its list and its memberships (the books are untouched)."""
        with self.lock:
            self._coll(cid)
            self.data["collections"] = [c for c in self.data["collections"] if c["id"] != cid]
            self.data["lists"].pop(cid, None)
            self.data["members"] = {k: v for k, v in self.data["members"].items() if v != cid}
            self.save()

    def set_member(self, book_key, cid):
        """Put a book (by library key: output folder + book ID) in a collection, or take it out (`cid=None`)."""
        with self.lock:
            if cid:
                self._coll(cid)
                self.data["members"][book_key] = cid
            else:
                self.data["members"].pop(book_key, None)
            self.save()

    def collection_of(self, book_key):
        """The collection a book belongs to, or None."""
        cid = self.data["members"].get(book_key)
        if not cid:
            return None
        return next((c for c in self.data["collections"] if c["id"] == cid), None)

    def lists_for(self, book_key):
        """[(list id, label, list)] that apply to a book: its collection's first, then All books."""
        out = []
        c = self.collection_of(book_key)
        if c:
            out.append((c["id"], c["name"], self.data["lists"].setdefault(c["id"], _empty_list())))
        out.append((ALL, "All books", self.data["lists"][ALL]))
        return out

    def get_list(self, lid, profiles=True):
        """A copy of one list (characters, retyped names, rules) for the library. `profiles=False` leaves out each character's
        profiles (the bulk of a big collection; the list dialog shows summaries of them instead, see `summaries`)."""
        with self.lock:
            if lid != ALL:
                self._coll(lid)
            lst = self.data["lists"].setdefault(lid, _empty_list())
            if not profiles:
                lst = {**lst, "characters": [{k: v for k, v in e.items() if k != "profiles"} for e in lst["characters"]]}
            return json.loads(json.dumps(lst))

    # ---- entries
    def _entry(self, lid, kind, eid):
        """Find one entry of a list by kind and id."""
        if kind not in KINDS:
            raise EditError("Unknown kind of entry")
        lst = self.data["lists"].get(lid)
        if lst is None:
            raise EditError("That list no longer exists")
        e = next((e for e in lst[kind] if e["id"] == eid), None)
        if not e:
            raise EditError("That entry no longer exists")
        return lst, e

    def edit_entry(self, lid, kind, eid, fields):
        """Edit an entry from the list dialog: character name/other names/pronouns/type, retyped name/type, or a rule's description."""
        with self.lock:
            _, e = self._entry(lid, kind, eid)
            if kind == "characters":
                if "name" in fields:
                    v = str(fields["name"]).strip()
                    if not v:
                        raise EditError("A character needs a name")
                    e["name"] = v
                if "names" in fields:
                    names = [n.strip() for n in re.split(r"[,\n;]", str(fields["names"])) if n.strip()]
                    e["names"] = {n: e.get("names", {}).get(n, 0) for n in names}
                if "pronoun" in fields:
                    e["pronoun"] = str(fields["pronoun"] or "").strip() or None
                if "cat" in fields:
                    e["cat"] = str(fields["cat"] or "").strip() or None
            elif kind == "retypes":
                if "text" in fields:
                    e["text"] = str(fields["text"]).strip()
                if "cat" in fields:
                    e["cat"] = str(fields["cat"]).strip()
            elif kind == "rules":
                if "label" in fields:
                    e["label"] = str(fields["label"]).strip()
            e["updated"] = time.time()
            self.save()
            return e

    def delete_entry(self, lid, kind, eid):
        """Delete one entry from a list."""
        with self.lock:
            lst, e = self._entry(lid, kind, eid)
            lst[kind] = [x for x in lst[kind] if x["id"] != eid]
            self.save()

    def delete_entries(self, lid, kind, ids):
        """Delete several entries of one list at once; returns how many went."""
        with self.lock:
            lst = self.data["lists"].get(lid)
            if lst is None or kind not in KINDS:
                raise EditError("That list no longer exists")
            ids = set(ids)
            before = len(lst[kind])
            lst[kind] = [x for x in lst[kind] if x["id"] not in ids]
            self.save()
            return before - len(lst[kind])

    # ---- the character list with profiles, and its export
    def summaries(self, lid, n=5):
        """Each character of a list with its profile over all books added up and summarised (core/profiles.py `summary`):
        {entry id: summary}. Other characters are named by their current entry names."""
        with self.lock:
            names = {e["id"]: e["name"] for lst in self.data["lists"].values() for e in lst["characters"]}
            lst = self.data["lists"].get(lid) or _empty_list()
            return {e["id"]: P.summary(P.combine((e.get("profiles") or {}).values()), n, names) for e in lst["characters"]}

    CSV_COLUMNS = [("id", "id"), ("name", "name"), ("other names", "names"), ("pronouns", "pronoun"), ("gender", "gender"),
                   ("type", "cat"), ("books", "books"), ("mentions", "mentions"), ("quotes", "quotes"), ("words spoken", "words"),
                   ("titles", "titles"), ("described as", "nouns"), ("relations", "relations"), ("appears with", "cooccur"),
                   ("places", "places"), ("actions", "actions"), ("done to them", "undergoes"), ("possessions", "has"),
                   ("described by", "described"), ("speech words", "speech_words")]

    def export_list(self, lid, fmt):
        """A list's characters as (file name, media type, text): "csv" one summary row per character (lists joined by "; "),
        "json" every entry in full, with its profile for each book."""
        with self.lock:
            label = "All books" if lid == ALL else self._coll(lid)["name"]
            lst = json.loads(json.dumps(self.data["lists"].get(lid) or _empty_list()))
        ascii_label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()   # a header-safe file name
        stem = re.sub(r"[^A-Za-z0-9_-]+", "-", ascii_label).strip("-").lower() or "list"
        if fmt == "json":
            return (f"{stem}-characters.json", "application/json",
                    json.dumps({"list": label, "exported": time.strftime("%Y-%m-%d %H:%M"), "characters": lst["characters"]},
                               indent=1, ensure_ascii=False))
        if fmt != "csv":
            raise EditError("Export as csv or json")
        sums = self.summaries(lid, n=10)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow([c for c, _ in self.CSV_COLUMNS])
        for e in sorted(lst["characters"], key=lambda e: e["name"].lower()):
            row = {**sums[e["id"]], **e, "names": list(e.get("names") or {})}
            row["gender"] = GENDER_WORDS.get(sums[e["id"]]["gender"], "")
            w.writerow(["; ".join(map(str, v)) if isinstance(v, list) else "" if v is None else v
                        for v in (row.get(k) for _, k in self.CSV_COLUMNS)])
        return f"{stem}-characters.csv", "text/csv", out.getvalue()

    def move_entry(self, lid, kind, eid, to):
        """Move an entry to another list (another collection, or all books)."""
        with self.lock:
            lst, e = self._entry(lid, kind, eid)
            if to != ALL:
                self._coll(to)
            if to == lid:
                return
            lst[kind] = [x for x in lst[kind] if x["id"] != eid]
            self.data["lists"].setdefault(to, _empty_list())[kind].append(e)
            self.save()

    # ---- recording from a book
    def find_character(self, book_key, names, eid=None):
        """The character entry a group belongs to: by its link, or by sharing its main names."""
        for lid, _, lst in self.lists_for(book_key):
            for e in lst["characters"]:
                if eid and e["id"] == eid:
                    return lid, e
        if not names:
            return None, None
        lower = {n.lower(): k for n, k in names.items()}
        main = max(names, key=names.get).lower()
        for lid, _, lst in self.lists_for(book_key):
            for e in lst["characters"]:
                enames = {n.lower() for n in e.get("names", {})} | {e["name"].lower()}
                if main in enames:
                    return lid, e
        for lid, _, lst in self.lists_for(book_key):
            for e in lst["characters"]:
                enames = {n.lower() for n in e.get("names", {})}
                shared = sum(k for n, k in lower.items() if n in enames)
                if shared and shared >= 0.6 * sum(lower.values()):
                    return lid, e
        return None, None

    def record_character(self, book_key, book_id, eid, data, lookup=True):
        """Add or update a character from a group you marked as checked. Returns the entry id, or None.

        The entry is `eid`; without one it is looked up by name (`lookup`), or, when the book already decided by evidence
        that there is none (`lookup=False`), a new entry is made.
        """
        with self.lock:
            c = self.collection_of(book_key)
            lid, e = self.find_character(book_key, data.get("names") or {} if lookup else {}, eid)
            if e is None:
                if not c:
                    return None
                e = {"id": _new_id("k"), "name": data["name"], "names": {}, "pronoun": None, "cat": None, "books": [],
                     "created": time.time()}
                self.data["lists"].setdefault(c["id"], _empty_list())["characters"].append(e)
            names = e.setdefault("names", {})
            for n, k in (data.get("names") or {}).items():
                names[n] = max(names.get(n, 0), k)
            if data.get("pronoun") and (data.get("pronoun_manual") or not e.get("pronoun")):
                e["pronoun"] = data["pronoun"]
            if data.get("cat"):
                e["cat"] = data["cat"]  # the latest confirmation wins
            if data.get("name_manual"):
                e["name"] = data["name"]
            if book_id not in e.setdefault("books", []):
                e["books"].append(book_id)
            if data.get("profile"):
                e.setdefault("profiles", {})[book_id] = data["profile"]
            e["updated"] = time.time()
            self.save()
            return e["id"]

    def update_profiles(self, book_key, book_id, profiles):
        """Replace this book's profile of each character entry given ({entry id: profile}); returns how many were updated."""
        with self.lock:
            n = 0
            for _, _, lst in self.lists_for(book_key):
                for e in lst["characters"]:
                    if e["id"] in profiles:
                        e.setdefault("profiles", {})[book_id] = profiles[e["id"]]
                        if book_id not in e.setdefault("books", []):
                            e["books"].append(book_id)
                        e["updated"] = time.time()
                        n += 1
            if n:
                self.save()
            return n

    def record_retype(self, book_key, book_id, text, cat, old):
        """Record (or update) a retyped name after every mention of it was given a new type by hand; characters known by that name follow."""
        with self.lock:
            c = self.collection_of(book_key)
            for lid, _, lst in self.lists_for(book_key):
                for e in lst["retypes"]:
                    if e["text"] == text:
                        if e["cat"] != cat:
                            e["cat"] = cat
                        self._retype_characters(book_key, text, cat)
                        e["from"] = sorted(set(e.get("from", [])) | set(old))
                        if book_id not in e.setdefault("books", []):
                            e["books"].append(book_id)
                        e["updated"] = time.time()
                        self.save()
                        return e["id"]
            if not c:
                return None
            self._retype_characters(book_key, text, cat)
            e = {"id": _new_id("r"), "text": text, "cat": cat, "from": sorted(set(old)), "books": [book_id],
                 "created": time.time()}
            self.data["lists"].setdefault(c["id"], _empty_list())["retypes"].append(e)
            self.save()
            return e["id"]

    def _retype_characters(self, book_key, text, cat):
        """A character known by a retyped name takes the new type too."""
        for _, _, lst in self.lists_for(book_key):
            for e in lst["characters"]:
                if text in e.get("names", {}) or e["name"] == text:
                    e["cat"] = cat

    def add_rule(self, lid, rule, book_id):
        """Add a saved Find rule to a list (an identical rule is not added twice)."""
        with self.lock:
            if lid != ALL:
                self._coll(lid)
            lst = self.data["lists"].setdefault(lid, _empty_list())
            same = next((r for r in lst["rules"] if all(r.get(k) == rule.get(k) for k in
                                                        ("layer", "sfield", "op", "svalue", "field", "value"))), None)
            if same:
                return same
            e = dict(rule, id=_new_id("f"), books=[book_id], created=time.time())
            lst["rules"].append(e)
            self.save()
            return e
