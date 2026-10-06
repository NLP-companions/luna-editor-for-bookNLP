"""Working copy of one BookNLP book, kept in a SQLite file.

Where the data comes from
  BookNLP writes, per book, `ID.tokens` (one row per token: word, lemma, POS, dependency, head, character offsets),
  `ID.entities` (mentions with type, mention type and coreference group), `ID.supersense`, `ID.quotes` (with speaker)
  and `ID.book`. `create_project` copies them into one SQLite file in ./projects (BookNLP's folder is never touched).

How it is stored
  Tokens have a stable `uid` and a position `ord` (0..n-1). Spans (entities, supersenses, quotes) point at tokens by
  uid, so splitting or merging tokens can renumber positions without breaking them. A coreference "group" has no row of
  its own: it is the set of entities with the same `coref` number; the `groups` table only holds what you set on it.

How it is edited
  Every edit runs inside `Store.batch`, which records full before/after rows: that gives undo, redo (across restarts)
  and the change log. Fields you edit by hand are remembered per row (`manual`) so spaCy re-tagging leaves them alone.

Where it goes
  `Store.export` writes BookNLP's file formats again (plus change log, validation and characters files) to ./exports;
  the analyser reads those. See docs/DOCUMENTATION.md for the whole picture.

What is worked out and kept
  Suggestions, profiles, pass lists and the like are derived from the tables and kept in `Store._memo` until an edit makes
  them stale. A step drops all of them, except that review marks (reviewed sentences, checked groups) drop only the pass
  lists and collection matches (`REVIEW_MEMOS`), flags drop nothing, and a step that leaves the tokens alone keeps what
  is read from the parse (`TOKEN_MEMOS`). tests/test_fuzz.py checks after random edits that no cache is ever stale.
  The same file is read by every request through one connection, so every method that touches it takes `Store.lock`.
"""
from __future__ import annotations

import bisect
import glob
import json
import re
import shutil
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path

from luna.core import bookfile, suggest
from luna.core.cleanup_import import apply_cleanup_metadata
from luna.core.tools.coref import CorefTools
from luna.core.errors import EditError
from luna.core.tools.flag import FlagTools
from luna.core.tools.quote import QuoteTools
from luna.core.tools.rule import RuleTools
from luna.core.tools.carry import CarryTools
from luna.core.tools.find import FindTools
from luna.core.tools.profile import ProfileTools
from luna.core.speakers import MentionFinder
from luna.core.schema import (LAYER_NAMES, RETAG_FIELDS, SCHEMA, SPAN_TABLES, STRUCTURAL, TABLES, TOKEN_HEADER,
                         TRACKED)
from luna.core.tagsets import TAGSETS


def filter_ws(text: str) -> str:
    """BookNLP's stand-in for whitespace inside a token (space→S, newline→N, tab→T, as in its common/pipelines.py), used
    to compare tokens with the text."""
    return re.sub("\t", "T", re.sub("[\n\r]", "N", re.sub(" ", "S", text)))


def read_tsv(path: Path):
    """Read a BookNLP tab-separated file: (header names, list of rows). Blank lines are skipped."""
    lines = path.read_text(encoding="utf-8").split("\n")
    header = lines[0].rstrip("\r").split("\t")
    rows = [l.rstrip("\r").split("\t") for l in lines[1:] if l.rstrip("\r") != ""]
    return header, rows


def none_int(v):
    """An int from a BookNLP cell, or None when the cell is empty or says 'None'."""
    return None if v in (None, "", "None") else int(v)


def find_book_ids(out_dir: Path):
    """Book IDs in a BookNLP output folder: one per `<ID>.tokens` file."""
    return sorted(p.name[: -len(".tokens")] for p in Path(out_dir).glob("*.tokens"))


# ---------------------------------------------------------------- import

def create_project(db_path: Path, out_dir: Path, text_path: Path, book_id: str | None = None) -> str:
    """Import: turn one book's BookNLP output into a new working copy (a SQLite file) and return the book ID.

    Reads `<ID>.tokens` (one row per token), `.entities`, `.supersense` and `.quotes` when present, and keeps every other
    `<ID>.*` file (`.book`, `.book.html`, …) as text in `meta.passthrough` so Export can hand it back. Every token's
    character offsets are checked against the original .txt (a wrong text file is caught here). Nothing in the BookNLP
    folder is changed. Failures (a missing file, a wrong text, a malformed row) raise SystemExit with a message for the
    user, and leave no half-made working copy behind.

    If the folder is the output of a cleaning step that also wrote `.groups.tsv` and `.cleanup_log.tsv`, they end up in `meta.passthrough`
    like any other extra file; `apply_cleanup_metadata` (core/cleanup_import.py) then names the plural groups it
    created and flags the dialogue turns it could not settle, so a cleaned import starts with that work already done.
    """
    out_dir, text_path, db_path = Path(out_dir), Path(text_path), Path(db_path)
    if book_id is None:
        ids = find_book_ids(out_dir)
        if not ids:
            raise SystemExit(f"No .tokens file found in {out_dir}")
        if len(ids) > 1:
            raise SystemExit(f"Several books in {out_dir}: {', '.join(ids)}. Choose one with --book-id.")
        book_id = ids[0]
    if not (out_dir / f"{book_id}.tokens").exists():
        raise SystemExit(f"{out_dir / f'{book_id}.tokens'} not found")
    if db_path.exists():
        raise SystemExit(f"{db_path} already exists. Open it, or delete it to start over.")
    try:
        _import_book(db_path, out_dir, text_path, book_id)
        store = Store(db_path)  # a fresh Store, only to apply a cleaned folder's extra files (see cleanup_import)
        try:
            apply_cleanup_metadata(store)
        finally:
            store.db.close()
    except BaseException as e:
        db_path.unlink(missing_ok=True)
        if isinstance(e, (SystemExit, KeyboardInterrupt)):
            raise
        raise SystemExit(f"Couldn't import {book_id} from {out_dir}: {type(e).__name__}: {e}") from e
    return book_id


def _read_token_rows(tok_path: Path, text: str):
    """The rows of `<ID>.tokens` ready for the `tokens` table: (header, rows, words, offset mismatches).

    A token's character span must reproduce its word in `text` (`filter_ws`); more than max(20, 1%) misses mean this isn't the
    text BookNLP was run on (SystemExit), a few are kept in `meta` for the overview."""
    header, rows = read_tsv(tok_path)
    ci = {name: i for i, name in enumerate(header)}
    missing = [c for c in TOKEN_HEADER if c not in ci]
    if missing:
        raise SystemExit(f"{tok_path.name} is missing columns: {', '.join(missing)}")
    extras = [c for c in header if c not in TOKEN_HEADER]
    token_rows, words, mismatches = [], [], []
    prev_s = prev_p = None
    for n, r in enumerate(rows):
        d = int(r[ci["token_ID_within_document"]])
        if d != n:
            raise SystemExit(f"Token IDs are not consecutive at row {n + 2} of {tok_path.name}")
        s, p = int(r[ci["sentence_ID"]]), int(r[ci["paragraph_ID"]])
        word = r[ci["word"]]
        cs, ce = int(r[ci["byte_onset"]]), int(r[ci["byte_offset"]])
        if filter_ws(text[cs:ce]) != word:
            mismatches.append(n)
        extra = {c: r[ci[c]] for c in extras}
        token_rows.append((d, d, word, r[ci["lemma"]], cs, ce, r[ci["POS_tag"]], r[ci["fine_POS_tag"]],
                           r[ci["dependency_relation"]], int(r[ci["syntactic_head_ID"]]), r[ci["event"]],
                           int(s != prev_s), int(p != prev_p), json.dumps(extra)))
        words.append(word)
        prev_s, prev_p = s, p
    if len(mismatches) > max(20, len(rows) // 100):
        raise SystemExit(
            f"{len(mismatches)} of {len(rows)} tokens don't match the text at their offsets "
            f"(first at token {mismatches[0]}). Is this the text you ran BookNLP on?")
    return header, token_rows, words, mismatches


def _import_book(db_path: Path, out_dir: Path, text_path: Path, book_id: str):
    """Write the working copy: tokens, the span layers that exist, the other kept files and the facts about the book."""
    # BookNLP reads the text with Python's default newline handling; do the same.
    with open(text_path, encoding="utf-8") as f:
        text = f.read()
    header, token_rows, words, mismatches = _read_token_rows(out_dir / f"{book_id}.tokens", text)

    def joined(a, b):
        """The words a..b joined by spaces (how BookNLP writes span text); used to detect books whose span text differs."""
        return " ".join(words[a:b + 1])

    db = sqlite3.connect(db_path)
    try:
        db.executescript(SCHEMA)
        db.executemany("INSERT INTO tokens(uid, ord, word, lemma, char_start, char_end, pos, tag, dep, head, event,"
                       " sent_start, para_start, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", token_rows)
        layers, join_style = [], {}

        ent_path = out_dir / f"{book_id}.entities"
        if ent_path.exists():
            h, rs = read_tsv(ent_path)
            e = {name: i for i, name in enumerate(h)}
            erows = []
            for i, r in enumerate(rs):
                a, b = int(r[e["start_token"]]), int(r[e["end_token"]])
                erows.append((i, a, b, int(r[e["COREF"]]), r[e["prop"]], r[e["cat"]], r[e["text"]]))
            db.executemany("INSERT INTO entities(uid, tok_start, tok_end, coref, prop, cat, text) VALUES (?,?,?,?,?,?,?)", erows)
            join_style["entities"] = all(r[6] == joined(r[1], r[2]) for r in erows)
            layers.append("entities")

        ss_path = out_dir / f"{book_id}.supersense"
        if ss_path.exists():
            h, rs = read_tsv(ss_path)
            e = {name: i for i, name in enumerate(h)}
            srows = [(i, int(r[e["start_token"]]), int(r[e["end_token"]]), r[e["supersense_category"]], r[e["text"]])
                     for i, r in enumerate(rs)]
            db.executemany("INSERT INTO supersenses(uid, tok_start, tok_end, cat, text) VALUES (?,?,?,?,?)", srows)
            join_style["supersenses"] = all(r[4] == joined(r[1], r[2]) for r in srows)
            layers.append("supersenses")

        q_path = out_dir / f"{book_id}.quotes"
        if q_path.exists():
            h, rs = read_tsv(q_path)
            e = {name: i for i, name in enumerate(h)}
            qrows = []
            for i, r in enumerate(rs):
                mp = r[e["mention_phrase"]]
                qrows.append((i, int(r[e["quote_start"]]), int(r[e["quote_end"]]), none_int(r[e["mention_start"]]),
                              none_int(r[e["mention_end"]]), None if mp == "None" else mp, none_int(r[e["char_id"]]),
                              r[e["quote"]]))
            db.executemany("INSERT INTO quotes(uid, tok_start, tok_end, m_start, m_end, m_phrase, char_id, text)"
                           " VALUES (?,?,?,?,?,?,?,?)", qrows)
            join_style["quotes"] = all(r[7] == joined(r[1], r[2]) for r in qrows)
            layers.append("quotes")

        passthrough = {}
        for p in sorted(out_dir.glob(f"{glob.escape(book_id)}.*")):
            suffix = p.name[len(book_id):]
            if suffix in (".tokens", ".entities", ".supersense", ".quotes") or not p.is_file():
                continue
            if p.resolve() == text_path.resolve():
                continue
            try:
                passthrough[suffix] = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                pass
        meta = {
            "book_id": book_id, "source_dir": str(out_dir.resolve()), "text_path": str(text_path.resolve()),
            "text": text, "created": time.time(), "tokens_header": header, "layers": layers,
            "join_style": join_style, "passthrough": passthrough, "offset_mismatches": mismatches[:200],
            "n_offset_mismatches": len(mismatches),
        }
        db.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", [(k, json.dumps(v)) for k, v in meta.items()])
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------- store

class Store(CorefTools, QuoteTools, RuleTools, CarryTools, FindTools, ProfileTools, FlagTools):
    """One open working copy: every read and every edit of a book goes through here.
    
    Data lives in the SQLite file (schema in core/schema.py). Edits run inside `batch()` so each user action is one
    undoable step recorded in the `changes` table. Feature areas are mixed in from core/tools (coref, quote, rule, carry,
    find, profile, flag); this file has the core: history, reading, editing spans and tokens, groups, re-tagging, and Export.
    """
    def __init__(self, path: Path, spacy_model: str | None = None):
        """Open the SQLite file, add any tables or columns newer than the file, and build the position caches."""
        self.path = Path(path)
        self._spacy = None
        self._spacy_error = None
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.create_function("REGEXP", 2, _regexp)
        self.db.create_function("CONTAINS_CI", 2, _contains_ci, deterministic=True)
        self.lock = threading.RLock()
        self._changes = None
        self._clusters = None
        self._bp = None
        self._memos = {}
        self.meta = {r["key"]: json.loads(r["value"]) for r in self.db.execute("SELECT key, value FROM meta")}
        self.spacy_model = spacy_model or self.meta.get("spacy_model") or "en_core_web_sm"
        # Tables added after the first version, and columns added to older ones (working copies made earlier open fine).
        # Suggestions from re-tagging wait in `proposals`; pronoun-pass confirmations sit in `confirmed` and quote-pass
        # confirmations in `confirmed_quotes`; none of these are part of undo.
        # `flags`: a "check this later" mark (kind: entities/quotes/groups/sentence, target: that row's uid or, for a
        # sentence, its first token's uid) with an optional note; set_flag/clear_flag *are* undoable (kind="review").
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS proposals(id INTEGER PRIMARY KEY, uid INTEGER, field TEXT, old TEXT, new TEXT, created REAL);"
            "CREATE INDEX IF NOT EXISTS prop_uid ON proposals(uid);"
            "CREATE INDEX IF NOT EXISTS tokens_char ON tokens(char_start);"
            # lookups that would otherwise scan a whole table: the next free id of a table (insert), the quotes of a speaker
            # or attributed through a mention (merge, regroup, delete), the original value of a row (history)
            "CREATE INDEX IF NOT EXISTS changes_tbl_uid ON changes(tbl, uid);"
            "CREATE INDEX IF NOT EXISTS q_char ON quotes(char_id);"
            "CREATE INDEX IF NOT EXISTS q_mention ON quotes(m_start, m_end);"
            "CREATE INDEX IF NOT EXISTS ent_end ON entities(tok_end);"
            "CREATE TABLE IF NOT EXISTS confirmed(uid INTEGER PRIMARY KEY, coref INTEGER, ts REAL);"
            "CREATE TABLE IF NOT EXISTS confirmed_quotes(uid INTEGER PRIMARY KEY, char_id INTEGER, ts REAL);"
            "CREATE TABLE IF NOT EXISTS groups(uid INTEGER PRIMARY KEY, name TEXT, pronoun TEXT, hidden INTEGER DEFAULT 0,"
            " manual TEXT DEFAULT '[]');"
            "CREATE TABLE IF NOT EXISTS flags(uid INTEGER PRIMARY KEY, kind TEXT, target INTEGER, note TEXT DEFAULT '', created REAL);"
            "CREATE UNIQUE INDEX IF NOT EXISTS flags_target ON flags(kind, target);")
        for table, column, decl in (("groups", "checked", "INTEGER DEFAULT 0"), ("groups", "carry", "TEXT"),
                                    ("batches", "kind", "TEXT"), ("proposals", "sig", "TEXT")):
            if column not in [r[1] for r in self.db.execute(f"PRAGMA table_info({table})")]:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        self.db.commit()
        self._build_cache()

    # ---- caches
    def _build_cache(self):
        """Rebuild the position caches: sentence start positions, each sentence's paragraph, and uid by position."""
        rows = self.db.execute("SELECT uid, ord, sent_start, para_start FROM tokens ORDER BY ord").fetchall()
        self.n_tokens = len(rows)
        self.sent_starts, self.sent_para = [], []
        para = -1
        for i, r in enumerate(rows):
            if r["para_start"] or i == 0:
                para += 1
            if r["sent_start"] or i == 0:
                self.sent_starts.append(i)
                self.sent_para.append(para)
        self.n_paras = para + 1
        self.uid_at = [r["uid"] for r in rows]

    def sentence_of(self, ord_: int) -> int:
        """The sentence number that token position `ord_` belongs to (binary search on the sentence starts)."""
        return max(0, bisect.bisect_right(self.sent_starts, ord_) - 1)

    def sentence_bounds(self, s: int):
        """(first, last) token positions of sentence `s`."""
        a = self.sent_starts[s]
        b = self.sent_starts[s + 1] - 1 if s + 1 < len(self.sent_starts) else self.n_tokens - 1
        return a, b

    def ord_of(self, uid):
        """The position of the token with this uid, or None."""
        if uid is None:
            return None
        r = self.db.execute("SELECT ord FROM tokens WHERE uid=?", (uid,)).fetchone()
        return r["ord"] if r else None

    def uid_of_ord(self, ord_: int) -> int:
        """The uid of the token at this position (EditError when there is none)."""
        if not 0 <= ord_ < self.n_tokens:
            raise EditError(f"There is no token {ord_}")
        return self.uid_at[ord_]

    # ---- batches, undo, redo
    @contextmanager
    def batch(self, label: str, kind: str | None = None):
        """Context manager: everything changed inside it is one undoable step named `label`.
        
        Changes are collected by update/insert/delete, written to `batches` + `changes` (full before/after rows) on success,
        and rolled back if anything raises. Redo history is dropped by a new batch. `kind='review'` marks steps that aren't
        part of the exported change log (review ticks, checked groups).
        """
        with self.lock:
            if self._changes is not None:
                raise RuntimeError("nested batch")
            self._changes = []
            self._label = label
            try:
                yield self
                if self._changes:
                    self.db.execute("DELETE FROM changes WHERE batch IN (SELECT id FROM batches WHERE undone=1)")
                    self.db.execute("DELETE FROM batches WHERE undone=1")
                    bid = self.db.execute("INSERT INTO batches(label, ts, kind) VALUES (?, ?, ?)",
                                          (self._label, time.time(), kind)).lastrowid
                    self.db.executemany(
                        "INSERT INTO changes(batch, tbl, uid, before, after) VALUES (?,?,?,?,?)",
                        [(bid, t, u, _dump(b), _dump(a)) for t, u, b, a in self._changes])
                self.db.commit()
                self._after(self._changes)
            except Exception:
                self.db.rollback()
                self._build_cache()
                self._clusters = None
                self._memos.clear()
                raise
            finally:
                self._changes = None

    def set_label(self, label: str):
        """Rename the batch that is open (used when the final wording depends on what actually changed)."""
        self._label = label

    def _memo(self, name, key, build):
        """A derived value (positions, suggestions, ...) kept until the next edit, undo or redo, or until `key` changes."""
        hit = self._memos.get(name)
        if hit is None or hit[0] != key:
            hit = self._memos[name] = (key, build())
        return hit[1]

    # What a step that only sets review marks (reviewed sentences, checked groups, flags) changes: the pass lists and who a
    # book is matched with. Every other derived value (merge suggestions, profiles, speaker suggestions…) stays valid.
    REVIEW_MEMOS = ("pass", "qpass", "carry_match", "carry_review")
    # Derived from the tokens table alone (the parse as plain lists, who depends on whom): kept by every step that doesn't
    # touch tokens, which is nearly all of them (groups, mentions, speakers); rebuilding them reads the whole book.
    TOKEN_MEMOS = ("tokens", "kids")

    @staticmethod
    def _only_review_marks(changes):
        """Do these recorded changes touch nothing but review marks: `tokens.reviewed` and `groups.checked` (also when its row
        is made by that very step)?"""
        fresh = {"name": None, "pronoun": None, "hidden": 0, "carry": None}       # a group row nothing but "checked" was set on
        for tbl, _, before, after in changes:
            if tbl == "tokens" and before and after and {k for k in after if after[k] != before.get(k)} <= {"reviewed"}:
                continue
            if tbl == "groups" and after:
                base = before or {**fresh, "manual": "[]"}
                if all(after.get(k) == base.get(k) for k in (*fresh, "manual")):
                    continue
            return False
        return True

    def _after(self, changes):
        """After a commit, undo or redo: drop caches, clear stale suggestions, renumber positions after text-level edits."""
        derived = [c for c in changes if c[0] != "flags"]          # flags are read directly: nothing is derived from them
        if derived:
            if self._only_review_marks(derived):
                for name in self.REVIEW_MEMOS:
                    self._memos.pop(name, None)
            else:
                keep = () if any(tbl == "tokens" for tbl, *_ in derived) else self.TOKEN_MEMOS
                self._memos = {name: v for name, v in self._memos.items() if name in keep}
        structural = renumber = False
        for tbl, _, b, a in changes:
            if tbl in ("entities", "quotes", "groups"):
                self._clusters = None
            if tbl == "tokens":
                if b is None or a is None or b.get("char_start") != a.get("char_start") or b.get("word") != a.get("word"):
                    renumber = structural = True
                elif any(b.get(k) != a.get(k) for k in STRUCTURAL):
                    structural = True
        gone = [u for tbl, u, _, a in changes if tbl == "tokens" and a is None]
        if gone:
            self.db.execute(f"DELETE FROM proposals WHERE uid IN ({','.join('?' * len(gone))})", gone)
            self.db.commit()
        if renumber:
            self._renumber()
            self._clusters = None
        if structural:
            self._build_cache()

    def _renumber(self):
        """Token positions follow the text: `ord` is the rank by character offset."""
        rows = self.db.execute("SELECT uid, ord FROM tokens ORDER BY char_start, uid").fetchall()
        upd = [(i, r["uid"]) for i, r in enumerate(rows) if r["ord"] != i]
        if upd:
            self.db.executemany("UPDATE tokens SET ord=? WHERE uid=?", upd)
        # Span texts are derived from the tokens they cover.
        words = {r["uid"]: (r["ord"], r["word"]) for r in self.db.execute("SELECT uid, ord, word FROM tokens")}
        seq = [w for _, w in sorted(words.values())]
        js = self.meta.get("join_style", {})
        for tbl in SPAN_TABLES:
            if not js.get(tbl, True):
                continue
            fix = []
            for r in self.db.execute(f"SELECT uid, tok_start, tok_end, text FROM {tbl}"):
                if r["tok_start"] in words and r["tok_end"] in words:
                    t = " ".join(seq[words[r["tok_start"]][0]:words[r["tok_end"]][0] + 1])
                    if t != r["text"]:
                        fix.append((t, r["uid"]))
            self.db.executemany(f"UPDATE {tbl} SET text=? WHERE uid=?", fix)
        fix = []
        for r in self.db.execute("SELECT uid, m_start, m_end, m_phrase FROM quotes WHERE m_start IS NOT NULL"):
            if r["m_start"] in words and r["m_end"] in words:
                t = " ".join(seq[words[r["m_start"]][0]:words[r["m_end"]][0] + 1])
                if t != r["m_phrase"]:
                    fix.append((t, r["uid"]))
        self.db.executemany("UPDATE quotes SET m_phrase=? WHERE uid=?", fix)
        self.db.commit()

    def _row(self, tbl, uid):
        """One row of a table as a dict, or None."""
        r = self.db.execute(f"SELECT * FROM {tbl} WHERE uid=?", (uid,)).fetchone()
        return dict(r) if r else None

    def _put(self, tbl, row):
        """Insert-or-replace a full row (used to apply recorded before/after states)."""
        cols = list(row)
        self.db.execute(f"INSERT OR REPLACE INTO {tbl}({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [row[c] for c in cols])

    def update(self, tbl, uid, fields: dict, manual=True):
        """Change fields of a row inside a batch; returns the new row.
        
        `manual=True` marks each tracked field that really changed as edited by hand (so re-tagging leaves it alone); a set
        marks only those fields; False marks none. Nothing is recorded when nothing changed.
        """
        before = self._row(tbl, uid)
        if before is None:
            raise EditError(f"{tbl[:-1]} {uid} no longer exists")
        after = dict(before)
        after.update(fields)
        if manual:
            # manual=True marks every tracked field that changed; a set marks only those fields
            m = set(json.loads(before["manual"] or "[]"))
            mark = TRACKED[tbl] if manual is True else set(manual)
            m.update(k for k, v in fields.items() if k in mark and before.get(k) != v)
            after["manual"] = json.dumps(sorted(m))
        if after == before:
            return before
        self._put(tbl, after)
        self._changes.append((tbl, uid, before, after))
        return after

    def insert(self, tbl, row: dict, manual=True):
        """Add a row inside a batch with a fresh uid; returns the new row. The uid is past the highest one in the table and
        in the history, so a deleted row's id is never given to a new one (undo could otherwise collide with it)."""
        uid = 1 + max(self.db.execute(f"SELECT COALESCE(MAX(uid), -1) FROM {tbl}").fetchone()[0],
                      self.db.execute("SELECT COALESCE(MAX(uid), -1) FROM changes WHERE tbl=?", (tbl,)).fetchone()[0])
        row = dict(row, uid=uid)
        if manual:
            row["manual"] = json.dumps(sorted(k for k in row if k in TRACKED[tbl]))
        self._put(tbl, row)
        row = self._row(tbl, uid)
        self._changes.append((tbl, uid, None, row))
        return row

    def delete(self, tbl, uid):
        """Delete a row inside a batch, remembering it for undo."""
        before = self._row(tbl, uid)
        if before is None:
            return
        self.db.execute(f"DELETE FROM {tbl} WHERE uid=?", (uid,))
        self._changes.append((tbl, uid, before, None))

    def _apply(self, batch_id, direction):
        """Replay one recorded batch backwards (undo) or forwards (redo); returns the changes for `_after`."""
        rows = self.db.execute("SELECT tbl, uid, before, after FROM changes WHERE batch=? ORDER BY id " +
                               ("DESC" if direction == "undo" else "ASC"), (batch_id,)).fetchall()
        applied = []
        for r in rows:
            target = json.loads(r["before"] if direction == "undo" else r["after"])
            if target is None:
                self.db.execute(f"DELETE FROM {r['tbl']} WHERE uid=?", (r["uid"],))
            else:
                self._put(r["tbl"], target)
            applied.append((r["tbl"], r["uid"], json.loads(r["before"]) or None, json.loads(r["after"]) or None))
        return applied

    def _replay(self, direction):
        """Undo (the latest step still applied) or redo (the earliest undone one): replay its recorded rows and flip its
        `undone` mark in one commit, so a failure half way leaves the book as it was. Returns the step's label."""
        applied_state = 0 if direction == "undo" else 1
        order = "DESC" if direction == "undo" else "ASC"
        with self.lock:
            b = self.db.execute(f"SELECT id, label FROM batches WHERE undone=? ORDER BY id {order} LIMIT 1",
                                (applied_state,)).fetchone()
            if not b:
                raise EditError(f"Nothing to {direction}")
            try:
                applied = self._apply(b["id"], direction)
                self.db.execute("UPDATE batches SET undone=? WHERE id=?", (1 - applied_state, b["id"]))
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
            self._after(applied)
            return b["label"]

    def undo(self):
        """Undo the latest step still applied; returns its label. Survives restarts because history is in the file."""
        return self._replay("undo")

    def redo(self):
        """Redo the earliest undone step; returns its label."""
        return self._replay("redo")

    def history(self, limit=300):
        """The newest `limit` steps (label, time, undone?), the count applied, and what undo and redo would do."""
        with self.lock:           # one connection is shared by every request: don't read between another request's steps
            rows = self.db.execute("SELECT id, label, ts, undone FROM batches ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            total = self.db.execute("SELECT COUNT(*) FROM batches WHERE undone=0").fetchone()[0]
            undo = self.db.execute("SELECT label FROM batches WHERE undone=0 ORDER BY id DESC LIMIT 1").fetchone()
            redo = self.db.execute("SELECT label FROM batches WHERE undone=1 ORDER BY id ASC LIMIT 1").fetchone()
            return {"items": [dict(r) for r in rows], "count": total,
                    "undo": undo["label"] if undo else None, "redo": redo["label"] if redo else None}

    # ---- clusters (coreference groups)
    def clusters(self):
        """Every coreference group as a dict, computed from the entities and your group settings (cached until an edit).
        
        A group's name is its most common proper name (else noun, else pronoun), unless you renamed it; `cat` is its most
        common type and `cats` all its types; pronoun/hidden/checked/carry come from the `groups` table, and pronouns default
        to BookNLP's `.book` values. Groups exist only while they have a mention.
        """
        with self.lock:
            if self._clusters is None:
                names = defaultdict(Counter)
                surface = defaultdict(Counter)
                count = Counter()
                cats = defaultdict(Counter)
                for r in self.db.execute("SELECT coref, prop, cat, text FROM entities"):
                    w = 10 if r["prop"] == "PROP" else 1 if r["prop"] == "NOM" else 0.001
                    key = r["text"].lower()
                    names[r["coref"]][key] += w
                    surface[r["coref"]][(key, r["text"])] += 1
                    count[r["coref"]] += 1
                    cats[r["coref"]][r["cat"]] += 1
                out = {}
                for cid, c in names.items():
                    key = c.most_common(1)[0][0]
                    best = max((v, t) for (k, t), v in surface[cid].items() if k == key)[1]
                    out[cid] = {"id": cid, "name": best, "auto_name": best, "count": count[cid],
                                "cat": cats[cid].most_common(1)[0][0], "cats": dict(cats[cid]), "hidden": False,
                                "pronoun": None, "named": False, "checked": False, "carry": None}
                quotes = Counter(r[0] for r in self.db.execute("SELECT char_id FROM quotes WHERE char_id IS NOT NULL"))
                bp = self._book_pronouns()
                for cid, c in out.items():
                    c["quotes"] = quotes.get(cid, 0)
                    c["pronoun"] = bp.get(cid)
                for r in self.db.execute("SELECT * FROM groups"):
                    c = out.get(r["uid"])
                    if not c:
                        continue
                    if r["name"]:
                        c["name"], c["named"] = r["name"], True
                    if r["pronoun"] is not None:
                        c["pronoun"] = r["pronoun"] or None
                    c["hidden"] = bool(r["hidden"])
                    c["checked"] = bool(r["checked"])
                    c["carry"] = r["carry"]
                self._clusters = out
            return self._clusters

    def _book_pronouns(self):
        """BookNLP's own pronoun guess per group, from the `.book` file kept in meta.passthrough."""
        if self._bp is None:
            self._bp = {}
            try:
                for c in json.loads(self.meta.get("passthrough", {}).get(".book") or "{}").get("characters", []):
                    g = c.get("g") or {}
                    if g.get("argmax"):
                        self._bp[c["id"]] = g["argmax"]
            except (ValueError, AttributeError, TypeError):
                pass
        return self._bp

    def _set_meta(self, key, value):
        """Save one value in the `meta` table (settings such as dismissed suggestions, hotkeys, narrator)."""
        self.meta[key] = value
        self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, json.dumps(value)))
        self.db.commit()

    def suggest_cfg(self, typ):
        """One kind of suggestion's settings (core/suggest.py): the library's when the book was opened from it, else the
        defaults. `settings_version` changes when they do, so cached suggestions are made again."""
        if self.carry_colls:
            return self.carry_colls.suggestion_settings()[typ]
        return suggest.settings()[typ]

    def settings_version(self):
        """Changes whenever the shared settings (collections.json) change; 0 for a book not opened from the library."""
        return self.carry_colls.version if self.carry_colls else 0

    def cluster_name(self, cid):
        """A group's display name (or '#id' if it has no mentions left); None for None."""
        if cid is None:
            return None
        c = self.clusters().get(cid)
        return c["name"] if c else f"#{cid}"

    def last_saved(self) -> float:
        """When the working copy was last written to disk (seconds). Every edit commits at once, so this is the last change."""
        try:
            return self.path.stat().st_mtime
        except OSError:
            return time.time()

    # ---- reads
    def info(self):
        """Everything the page needs at start-up: counts, tag lists, values seen, history, pending suggestions, progress."""
        with self.lock:
            counts = {t: self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
            return {
                "book_id": self.meta["book_id"], "project": str(self.path), "source_dir": self.meta["source_dir"],
                "text_path": self.meta["text_path"], "layers": self.meta["layers"], "counts": counts,
                "n_sentences": len(self.sent_starts), "n_paragraphs": self.n_paras,
                "n_offset_mismatches": self.meta.get("n_offset_mismatches", 0),
                "tagsets": {f"{t}.{f}": v for (t, f), v in TAGSETS.items()},
                "observed": self.observed_values(), "history": self.history(1),
                "pending": self.pending_count(), "spacy_model": self.spacy_model, "review": self.review_progress(),
                "last_saved": self.last_saved(),
            }

    def observed_values(self):
        """For each tagged field, the values that occur in this book (most common first), for the drop-down lists."""
        with self.lock:
            return {f"{t}.{f}": [r[0] for r in self.db.execute(
                f"SELECT {f}, COUNT(*) c FROM {t} GROUP BY {f} ORDER BY c DESC") if r[0] is not None] for (t, f) in TAGSETS}

    def _span_query(self, tbl, a, b):
        """Spans of a layer that touch token positions a..b, with their start/end positions, in reading order."""
        return self.db.execute(
            f"SELECT x.*, s.ord AS s, e.ord AS e FROM {tbl} x JOIN tokens s ON s.uid=x.tok_start "
            f"JOIN tokens e ON e.uid=x.tok_end WHERE s.ord <= ? AND e.ord >= ? ORDER BY s.ord, e.ord DESC",
            (b, a)).fetchall()

    def _span_out(self, tbl, r):
        """A span row as the API returns it: positions, manual fields, plus the group name or speaker."""
        d = dict(r)
        d["manual"] = json.loads(d["manual"] or "[]")
        d["layer"] = tbl
        if tbl == "entities":
            d["name"] = self.cluster_name(d["coref"])
        if tbl == "quotes":
            d["speaker"] = self.cluster_name(d["char_id"])
            d["ms"] = self.ord_of(d["m_start"])
            d["me"] = self.ord_of(d["m_end"])
        return d

    def window(self, sent: int, n: int):
        """One page of the book for the Table and Annotation views: sentences `sent`..sent+n-1 with their tokens
        (dependency heads resolved to positions), the spans that touch them, and which tokens have suggestions waiting.
        """
        with self.lock:
            sent = max(0, min(sent, len(self.sent_starts) - 1))
            last = min(len(self.sent_starts) - 1, sent + n - 1)
            a, _ = self.sentence_bounds(sent)
            _, b = self.sentence_bounds(last)
            toks = self.db.execute(
                "SELECT t.*, h.ord AS head_ord, h.word AS head_word FROM tokens t LEFT JOIN tokens h ON h.uid=t.head "
                "WHERE t.ord BETWEEN ? AND ? ORDER BY t.ord", (a, b)).fetchall()
            tokens = []
            for t in toks:
                d = dict(t)
                d["manual"] = json.loads(d["manual"] or "[]")
                d["extra"] = json.loads(d["extra"] or "{}")
                d["sent"] = self.sentence_of(d["ord"])
                d["head_sent"] = self.sentence_of(d["head_ord"]) if d["head_ord"] is not None else None
                tokens.append(d)
            sentences = []
            for s in range(sent, last + 1):
                sa, sb = self.sentence_bounds(s)
                sentences.append({"index": s, "start": sa, "end": sb, "para": self.sent_para[s],
                                  "reviewed": bool(tokens[sa - a]["reviewed"]),
                                  "para_start": s == 0 or self.sent_para[s] != self.sent_para[s - 1]})
            spans = {tbl: [self._span_out(tbl, r) for r in self._span_query(tbl, a, b)] for tbl in SPAN_TABLES}
            pending = [r[0] for r in self.db.execute(
                "SELECT DISTINCT p.uid FROM proposals p JOIN tokens t ON t.uid=p.uid WHERE t.ord BETWEEN ? AND ?", (a, b))]
            return {"first": sent, "last": last, "n_sentences": len(self.sent_starts), "start": a, "end": b,
                    "sentences": sentences, "tokens": tokens, "pending": pending, **spans}

    # a jump to an excerpt ("Show in text") always shows at least this many sentences, whatever the page size
    JUMP_MIN_SENTENCES = 15

    def centered_start(self, a, b, n):
        """First sentence of an n-sentence page that puts the excerpt (tokens a..b) in the middle, so there is
        context above and below it. An excerpt longer than the page still starts the page."""
        sa, sb = self.sentence_of(a), self.sentence_of(b)
        first = (sa + sb) // 2 - n // 2
        return max(0, min(first, sa, len(self.sent_starts) - n))

    def token_detail(self, uid):
        """Everything for the side panel of one token: fields, head, dependents, spans on it, quotes it is the speaker mention of."""
        with self.lock:
            t = self._row("tokens", uid)
            if not t:
                raise EditError("That token no longer exists")
            t["manual"] = json.loads(t["manual"] or "[]")
            t["extra"] = json.loads(t["extra"] or "{}")
            t["sent"] = self.sentence_of(t["ord"])
            sa, sb = self.sentence_bounds(t["sent"])
            t["sent_start_ord"], t["sent_end_ord"] = sa, sb
            h = self._row("tokens", t["head"]) if t["head"] is not None else None
            t["head_ord"], t["head_word"] = (h["ord"], h["word"]) if h else (None, None)
            t["dependents"] = [dict(r) for r in self.db.execute(
                "SELECT uid, ord, word, dep FROM tokens WHERE head=? AND uid<>? ORDER BY ord", (uid, uid))]
            t["spans"] = {tbl: [self._span_out(tbl, r) for r in self._span_query(tbl, t["ord"], t["ord"])]
                          for tbl in SPAN_TABLES}
            t["as_speaker_mention"] = [self._span_out("quotes", r) for r in self.db.execute(
                "SELECT x.*, s.ord AS s, e.ord AS e FROM quotes x JOIN tokens s ON s.uid=x.tok_start "
                "JOIN tokens e ON e.uid=x.tok_end JOIN tokens ms ON ms.uid=x.m_start JOIN tokens me ON me.uid=x.m_end "
                "WHERE ms.ord <= ? AND me.ord >= ?", (t["ord"], t["ord"]))]
            t["context"] = self.text_between(max(0, t["ord"] - 12), min(self.n_tokens - 1, t["ord"] + 12))
            t["context_start"] = max(0, t["ord"] - 12)
            return t

    def span_detail(self, tbl, uid):
        """Everything for the side panel of one span, with the words around it."""
        with self.lock:
            r = self.db.execute(
                f"SELECT x.*, s.ord AS s, e.ord AS e FROM {tbl} x JOIN tokens s ON s.uid=x.tok_start "
                f"JOIN tokens e ON e.uid=x.tok_end WHERE x.uid=?", (uid,)).fetchone()
            if not r:
                raise EditError(f"That {LAYER_NAMES[tbl]} no longer exists")
            d = self._span_out(tbl, r)
            if tbl == "entities":
                c = self.clusters().get(d["coref"])
                d["cluster_count"] = c["count"] if c else 0
                d["quotes_via"] = self.db.execute(
                    "SELECT COUNT(*) FROM quotes WHERE m_start=? AND m_end=?", (d["tok_start"], d["tok_end"])).fetchone()[0]
            ca, cb = max(0, d["s"] - 12), min(self.n_tokens - 1, d["e"] + 12)
            d["context"] = self.words_between(ca, cb)
            d["context_start"] = ca
            return d

    def words_between(self, a, b):
        """The words at token positions a..b."""
        return [r[0] for r in self.db.execute("SELECT word FROM tokens WHERE ord BETWEEN ? AND ? ORDER BY ord", (a, b))]

    def text_between(self, a, b):
        """The words at token positions a..b joined with spaces."""
        return " ".join(self.words_between(a, b))

    # ---- edits
    def _check_value(self, field, value):
        """Refuse an empty value for the fields that must always have one."""
        if value is None or value == "":
            if field in ("pos", "tag", "dep", "event", "cat", "prop"):
                raise EditError(f"{field} can't be empty")

    def edit_token(self, uid, fields: dict):
        """Change lemma, POS, fine POS, dependency, event or head (`head_ord`) of one token in one undoable step."""
        allowed = {"lemma", "pos", "tag", "dep", "event", "head_ord"}
        bad = set(fields) - allowed
        if bad:
            raise EditError(f"Can't edit {', '.join(sorted(bad))} here")
        with self.lock:
            t = self._row("tokens", uid)
            if not t:
                raise EditError("That token no longer exists")
            upd, parts = {}, []
            for k, v in fields.items():
                if k == "head_ord":
                    h = self.uid_of_ord(int(v))
                    upd["head"] = h
                    old = self._row("tokens", t["head"]) if t["head"] is not None else None
                    parts.append(f"head {old['ord'] if old else '–'} → {v}")
                else:
                    v = str(v).strip()
                    self._check_value(k, v)
                    upd[k] = v
                    parts.append(f"{k} {t[k]} → {v}")
            with self.batch(f"Token {t['ord']} “{t['word']}”: " + ", ".join(parts)):
                self.update("tokens", uid, upd)
            return self.history(1)

    def _resolve_span_bounds(self, fields):
        """Turn `s`/`e` token positions in a request into the tok_start/tok_end uids stored on a span."""
        out = {}
        if "s" in fields:
            out["tok_start"] = self.uid_of_ord(int(fields["s"]))
        if "e" in fields:
            out["tok_end"] = self.uid_of_ord(int(fields["e"]))
        return out

    def _span_text(self, a_ord, b_ord):
        """The text stored on a span: the words at a..b joined with spaces."""
        return " ".join(self.words_between(a_ord, b_ord))

    def _check_quote_overlap(self, s, e, except_uid=None):
        """Quotes can't overlap: refuse bounds that touch another quote."""
        r = self.db.execute(
            "SELECT x.uid FROM quotes x JOIN tokens a ON a.uid=x.tok_start JOIN tokens b ON b.uid=x.tok_end "
            "WHERE a.ord <= ? AND b.ord >= ? AND x.uid IS NOT ?", (e, s, except_uid)).fetchone()
        if r:
            raise EditError("That would overlap another quote")

    def create_span(self, tbl, fields: dict):
        """Add an entity, supersense or quote over tokens s..e (one undoable step).
        
        Entities take type (`cat`), mention type (`prop`) and a group (`coref`; empty = a new group); quotes may take a speaker
        (`char_id`, linked to the nearest mention of that group). Returns the new uid.
        """
        if tbl not in SPAN_TABLES:
            raise EditError("Unknown layer")
        with self.lock:
            s, e = int(fields["s"]), int(fields["e"])
            if s > e:
                s, e = e, s
            b = self._resolve_span_bounds({"s": s, "e": e})
            text = self._span_text(s, e)
            row = dict(b, text=text)
            if tbl == "entities":
                cat, prop = fields.get("cat") or "PER", fields.get("prop") or "PROP"
                self._check_value("cat", cat)
                coref = fields.get("coref")
                if coref in (None, ""):
                    coref = self._new_coref()
                row.update(cat=cat, prop=prop, coref=int(coref))
                label = f"Added {cat} {prop} entity “{text}” in group {self.cluster_name(int(coref)) or coref}"
            elif tbl == "supersenses":
                cat = fields.get("cat") or ""
                self._check_value("cat", cat)
                row.update(cat=cat)
                label = f"Added supersense {cat} “{text}”"
            else:
                self._check_quote_overlap(s, e)
                row.update(m_start=None, m_end=None, m_phrase=None, char_id=None)
                label = f"Added quote “{_trim(text)}”"
            with self.batch(label):
                new = self.insert(tbl, row)
                if tbl == "quotes" and fields.get("char_id") not in (None, ""):
                    self._set_speaker(new["uid"], int(fields["char_id"]))
                    self.set_label(label + f" spoken by {self.cluster_name(int(fields['char_id']))}")
            return {"uid": new["uid"], "history": self.history(1)}

    def edit_span(self, tbl, uid, fields: dict):
        """Change a span's boundaries, type, mention type, group, speaker or speaker mention (one undoable step).
        
        Quotes attributed through an entity follow its group and boundaries (`_sync_quotes_for_entity`); moving a quote's
        boundaries keeps its speaker mention unless the mention is now inside the quote.
        """
        if tbl not in SPAN_TABLES:
            raise EditError("Unknown layer")
        with self.lock:
            cur = self.span_detail(tbl, uid)
            what = f"{LAYER_NAMES[tbl].capitalize()} “{_trim(cur['text'])}”"
            parts, upd = [], {}
            if "s" in fields or "e" in fields:
                s = int(fields.get("s", cur["s"]))
                e = int(fields.get("e", cur["e"]))
                if s > e:
                    raise EditError("The start can't be after the end")
                if tbl == "quotes":
                    self._check_quote_overlap(s, e, uid)
                upd.update(self._resolve_span_bounds({"s": s, "e": e}))
                upd["text"] = self._span_text(s, e)
                parts.append(f"now tokens {s}–{e} “{_trim(upd['text'])}”")
            for k in ("cat", "prop"):
                if k in fields and tbl != "quotes":
                    if k == "prop" and tbl != "entities":
                        continue
                    v = str(fields[k]).strip()
                    self._check_value(k, v)
                    upd[k] = v
                    parts.append(f"{k} {cur[k]} → {v}")
            if "coref" in fields and tbl == "entities":
                if fields["coref"] in (None, ""):
                    v = self._new_coref()
                else:
                    v = int(fields["coref"])
                upd["coref"] = v
                parts.append(f"group {self.cluster_name(cur['coref'])} → {self.cluster_name(v) if v in self.clusters() else 'new group #' + str(v)}")
            with self.batch(f"{what}: " + ", ".join(parts)):
                if upd:
                    self.update(tbl, uid, upd)
                if tbl == "entities":
                    self._sync_quotes_for_entity(cur, upd)
                if tbl == "quotes":
                    if "char_id" in fields:
                        cid = None if fields["char_id"] in (None, "") else int(fields["char_id"])
                        self._set_speaker(uid, cid)
                        parts.append(f"speaker → {self.cluster_name(cid) or 'none'}")
                    if "mention_entity" in fields:
                        ent = self._row("entities", int(fields["mention_entity"]))
                        if not ent:
                            raise EditError("That mention no longer exists")
                        self.update("quotes", uid, {"m_start": ent["tok_start"], "m_end": ent["tok_end"],
                                                    "m_phrase": ent["text"], "char_id": ent["coref"]})
                        parts.append(f"speaker mention → “{ent['text']}” ({self.cluster_name(ent['coref'])})")
                    elif upd and cur["char_id"] is not None and cur["m_start"] is not None:
                        # Boundaries moved: keep the mention unless it's now inside the quote.
                        ms, me = self.ord_of(cur["m_start"]), self.ord_of(cur["m_end"])
                        s, e = int(fields.get("s", cur["s"])), int(fields.get("e", cur["e"]))
                        if ms is not None and ms <= e and me >= s:
                            self._set_speaker(uid, cur["char_id"])
                self.set_label(f"{what}: " + ", ".join(parts) if parts else f"{what}: no change")
            if tbl == "entities" and "cat" in upd:
                self._carry_retypes_safe([uid])
            return self.history(1)

    def _sync_quotes_for_entity(self, old, upd):
        """Quotes attributed through this mention follow its group and boundaries."""
        if not upd:
            return
        for q in self.db.execute("SELECT uid FROM quotes WHERE m_start=? AND m_end=?",
                                 (old["tok_start"], old["tok_end"])).fetchall():
            qf = {}
            if "coref" in upd:
                qf["char_id"] = upd["coref"]
            if "tok_start" in upd:
                qf["m_start"] = upd["tok_start"]
                qf["m_end"] = upd["tok_end"]
                qf["m_phrase"] = upd["text"]
            if qf:
                self.update("quotes", q["uid"], qf)

    def _set_speaker(self, quote_uid, cid, finder=None):
        """Give a quote a speaker group and link the nearest mention of that group outside the quote (none = no speaker).

        Callers that set many speakers in one batch pass one `finder` (`MentionFinder`), so each group's mentions are read
        once rather than once per quote."""
        q = self._row("quotes", quote_uid)
        if cid is None:
            self.update("quotes", quote_uid, {"char_id": None, "m_start": None, "m_end": None, "m_phrase": None})
            return
        best = (finder or MentionFinder(self.db)).nearest(cid, self.ord_of(q["tok_start"]), self.ord_of(q["tok_end"]))
        if best is not None:
            self.update("quotes", quote_uid, {"char_id": cid, "m_start": best["tok_start"], "m_end": best["tok_end"],
                                              "m_phrase": best["text"]})
        else:
            self.update("quotes", quote_uid, {"char_id": cid, "m_start": None, "m_end": None, "m_phrase": None})

    def delete_span(self, tbl, uid):
        """Delete one entity, supersense or quote; quotes that used a deleted entity as speaker mention lose that link."""
        if tbl not in SPAN_TABLES:
            raise EditError("Unknown layer")
        with self.lock:
            cur = self.span_detail(tbl, uid)
            with self.batch(f"Deleted {LAYER_NAMES[tbl]} “{_trim(cur['text'])}”"):
                self.delete(tbl, uid)
                if tbl == "entities":
                    for q in self.db.execute("SELECT uid FROM quotes WHERE m_start=? AND m_end=?",
                                             (cur["tok_start"], cur["tok_end"])).fetchall():
                        self.update("quotes", q["uid"], {"m_start": None, "m_end": None, "m_phrase": None})
            return self.history(1)

    # ---- groups (coreference chains / characters)
    def _put_new(self, tbl, row):
        """Insert a row and record it as created (for rows with a chosen uid, such as groups)."""
        self._put(tbl, row)
        self._changes.append((tbl, row["uid"], None, dict(row)))

    def _group_upsert(self, cid, fields):
        """Create or update the `groups` row (name, pronouns, hidden, checked) of a coreference group."""
        if self._row("groups", cid):
            self.update("groups", cid, fields)
        else:
            row = {"uid": cid, "name": None, "pronoun": None, "hidden": 0, "checked": 0, "carry": None, "manual": "[]"}
            row.update(fields)
            row["manual"] = json.dumps(sorted(k for k in fields if k in TRACKED["groups"]))
            self._put_new("groups", row)

    def _new_coref(self):
        """The next unused group number."""
        a = self.db.execute("SELECT COALESCE(MAX(coref), -1) FROM entities").fetchone()[0]
        b = self.db.execute("SELECT COALESCE(MAX(uid), -1) FROM groups").fetchone()[0]
        return max(a, b) + 1

    def groups_list(self, cat="", q="", hidden=False, sort="mentions", limit=800, unchecked=False):
        """The Entities list: groups filtered by type/search/hidden/unchecked and sorted, with counts per type and review progress."""
        with self.lock:
            items = list(self.clusters().values())
            if cat:
                items = [c for c in items if cat in c["cats"]]
            if not hidden:
                items = [c for c in items if not c["hidden"]]
            if unchecked:
                items = [c for c in items if not c["checked"]]
            if q:
                ql = q.casefold()
                names = defaultdict(set)
                for r in self.db.execute("SELECT coref, text FROM entities WHERE prop='PROP' AND CONTAINS_CI(text, ?)", (q,)):
                    names[r["coref"]].add(r["text"])
                items = [c for c in items if ql in c["name"].casefold() or c["id"] in names or str(c["id"]) == q.strip()]
            key = {"name": lambda c: (c["name"].lower(), c["id"]), "quotes": lambda c: (-c["quotes"], -c["count"]),
                   "id": lambda c: c["id"]}.get(sort, lambda c: (-c["count"], c["id"]))
            items.sort(key=key)
            # groups per type, counting a group under each type it has a mention of (VAR mentions inside a PER group too)
            cats = Counter(t for c in self.clusters().values() if hidden or not c["hidden"] for t in c["cats"])
            visible = [c for c in self.clusters().values() if not c["hidden"] and (not cat or cat in c["cats"])]
            return {"total": len(items), "items": items[:limit], "cats": dict(cats),
                    "hidden_count": sum(1 for c in self.clusters().values() if c["hidden"]),
                    "progress": {"checked": sum(1 for c in visible if c["checked"]), "total": len(visible)}}

    def group_detail(self, cid, offset=0, limit=150):
        """One group's page: variants (distinct texts), a page of its mentions with context, and its settings."""
        with self.lock:
            c = self.clusters().get(cid)
            if not c:
                raise EditError(f"Group {cid} has no mentions left")
            rows = [dict(r) for r in self.db.execute(
                "SELECT x.*, s.ord AS s, e.ord AS e FROM entities x JOIN tokens s ON s.uid=x.tok_start "
                "JOIN tokens e ON e.uid=x.tok_end WHERE x.coref=? ORDER BY s.ord, e.ord", (cid,))]
            variants = {}
            for r in rows:
                k = (r["prop"], r["text"])
                v = variants.setdefault(k, {"text": r["text"], "prop": r["prop"], "n": 0, "first": r["s"]})
                v["n"] += 1
            rank = {"PROP": 0, "NOM": 1, "PRON": 2}
            vs = sorted(variants.values(), key=lambda v: (rank.get(v["prop"], 3), -v["n"]))
            mentions = []
            for r in rows[offset:offset + limit]:
                ca, cb = max(0, r["s"] - 7), min(self.n_tokens - 1, r["e"] + 7)
                mentions.append({"uid": r["uid"], "s": r["s"], "e": r["e"], "text": r["text"], "prop": r["prop"],
                                 "cat": r["cat"], "sent": self.sentence_of(r["s"]), "context": self.words_between(ca, cb),
                                 "context_start": ca, "manual": json.loads(r["manual"] or "[]")})
            return {**c, "variants": vs, "mentions": mentions, "offset": offset, "total": len(rows)}

    def edit_group(self, cid, fields):
        """Rename a group, set its pronouns, hide it ('not a character') or mark it checked.
        
        Checking a group also records it as a character in the book's collection (carry_tools) when there is one.
        """
        with self.lock:
            c = self.clusters().get(cid)
            if not c:
                raise EditError(f"Group {cid} has no mentions left")
            upd, parts = {}, []
            if "name" in fields:
                v = (fields["name"] or "").strip()
                upd["name"] = v or None
                parts.append(f"renamed to “{v}”" if v else "name reset")
            if "pronoun" in fields:
                v = (fields["pronoun"] or "").strip()
                upd["pronoun"] = v
                parts.append(f"pronouns {v}" if v else "pronouns cleared")
            if "hidden" in fields:
                upd["hidden"] = 1 if fields["hidden"] else 0
                parts.append("marked as not a character" if fields["hidden"] else "listed as a character again")
            if "checked" in fields:
                upd["checked"] = 1 if fields["checked"] else 0
                parts.append("marked as checked" if fields["checked"] else "marked as not checked")
                if fields["checked"] and not upd.get("hidden"):   # "not a character" in the same step: nothing to record
                    try:
                        eid = self.carry_record_group(cid)
                    except Exception:  # the collection file is a convenience; never block the edit
                        eid = None
                    if eid and eid != c.get("carry"):
                        upd["carry"] = eid
            if not upd:
                raise EditError("Nothing to change")
            only_review = set(upd) <= {"checked", "carry"}
            with self.batch(f"Group “{c['name']}” (#{cid}): " + ", ".join(parts), kind="review" if only_review else None):
                self._group_upsert(cid, upd)
            return self.history(1)

    def merge_groups(self, target, sources):
        """Merge several groups into `target` (one undoable step); the narrator setting follows the merge."""
        with self.lock:
            cl = self.clusters()
            if target not in cl:
                raise EditError(f"Group {target} has no mentions")
            sources = [x for x in sources if x != target and x in cl]
            if not sources:
                raise EditError("Choose at least one other group to merge")
            names = ", ".join(f"“{cl[x]['name']}” (#{x})" for x in sources[:4])
            if len(sources) > 4:
                names = f"{len(sources)} groups ({names}, …)"
            with self.batch(f"Merged {names} into “{cl[target]['name']}” (#{target})"):
                self._merge_core(target, sources)
            self._repoint_narrator(target, sources)
            return self.history(1)

    def _merge_core(self, target, sources):
        """Move every mention and quote of the sources into target (inside a batch)."""
        t_row = self._row("groups", target)
        for src in sources:
            for r in self.db.execute("SELECT uid FROM entities WHERE coref=?", (src,)).fetchall():
                self.update("entities", r["uid"], {"coref": target})
            for r in self.db.execute("SELECT uid FROM quotes WHERE char_id=?", (src,)).fetchall():
                self.update("quotes", r["uid"], {"char_id": target})
            s_row = self._row("groups", src)
            if s_row:
                carry = {}
                if s_row["name"] and not (t_row and t_row["name"]):
                    carry["name"] = s_row["name"]
                if s_row["pronoun"] is not None and not (t_row and t_row["pronoun"] is not None):
                    carry["pronoun"] = s_row["pronoun"]
                if s_row.get("carry") and not (t_row and t_row.get("carry")):
                    carry["carry"] = s_row["carry"]
                if carry:
                    self._group_upsert(target, carry)
                    t_row = self._row("groups", target)
                self.delete("groups", src)

    def regroup(self, uids, target=None, name=None):
        """Move mentions (by uid) to another group, or a new one (`target=None`, optionally named); quotes attributed through them follow."""
        with self.lock:
            uids = [int(u) for u in uids]
            if not uids:
                raise EditError("No mentions selected")
            cl = self.clusters()
            new = target is None
            if new:
                target = self._new_coref()
            tname = name or (cl[target]["name"] if target in cl else f"new group #{target}")
            moved = 0
            with self.batch(f"Moved {len(uids)} mention{'s' if len(uids) > 1 else ''} to “{tname}”"):
                if new and name:
                    self._group_upsert(target, {"name": name})
                for u in uids:
                    before = self._row("entities", u)
                    if not before or before["coref"] == target:
                        continue
                    self.update("entities", u, {"coref": target})
                    self._sync_quotes_for_entity(before, {"coref": target})
                    moved += 1
                if moved != len(uids):
                    self.set_label(f"Moved {moved} mention{'s' if moved != 1 else ''} to “{tname}”")
            return {"history": self.history(1), "target": target, "moved": moved}

    def mentions_detail(self, uids):
        """Details (position, text, group, context sentence) of a list of mentions, in reading order."""
        with self.lock:
            out = []
            for u in uids[:500]:
                r = self.db.execute(
                    "SELECT x.*, s.ord AS s, e.ord AS e FROM entities x JOIN tokens s ON s.uid=x.tok_start "
                    "JOIN tokens e ON e.uid=x.tok_end WHERE x.uid=?", (int(u),)).fetchone()
                if r:
                    d = self._span_out("entities", r)
                    d["sent"] = self.sentence_of(d["s"])
                    out.append(d)
            out.sort(key=lambda d: (d["s"], d["e"]))
            return {"items": out, "missing": len(uids) - len(out)}

    def edit_entities(self, uids, fields):
        """Change type and/or mention type of several entities in one step."""
        with self.lock:
            uids = [int(u) for u in uids]
            upd = {}
            for k in ("cat", "prop"):
                if k in fields and fields[k] not in (None, ""):
                    v = str(fields[k]).strip()
                    self._check_value(k, v)
                    upd[k] = v
            if not upd or not uids:
                raise EditError("Nothing to change")
            what = ", ".join(f"{'type' if k == 'cat' else 'mention type'} {v}" for k, v in upd.items())
            with self.batch(f"Set {what} on {len(uids)} mention{'s' if len(uids) > 1 else ''}"):
                for u in uids:
                    if self._row("entities", u):
                        self.update("entities", u, upd)
            if "cat" in upd:
                self._carry_retypes_safe(uids)
            return self.history(1)

    def delete_entities(self, uids):
        """Delete several mentions in one undoable step."""
        with self.lock:
            uids = [int(u) for u in uids]
            rows = [r for r in (self._row("entities", u) for u in uids) if r]
            if not rows:
                raise EditError("Those mentions no longer exist")
            with self.batch(f"Deleted {len(rows)} mention{'s' if len(rows) > 1 else ''}"):
                self._drop_entities(rows)
            return self.history(1)

    def _drop_entities(self, rows):
        """Delete these entity rows (inside a batch); quotes that used one as their speaker mention lose that link."""
        for cur in rows:
            self.delete("entities", cur["uid"])
            for q in self.db.execute("SELECT uid FROM quotes WHERE m_start=? AND m_end=?",
                                     (cur["tok_start"], cur["tok_end"])).fetchall():
                self.update("quotes", q["uid"], {"m_start": None, "m_end": None, "m_phrase": None})

    def move_variant(self, cid, text, prop, target=None, name=None):
        """Move every mention of a group that has this exact text and mention type to another group."""
        uids = [r[0] for r in self.db.execute("SELECT uid FROM entities WHERE coref=? AND text=? AND prop=?", (cid, text, prop))]
        return self.regroup(uids, target, name)

    def dismiss_pair(self, key):
        """Remember that two groups were judged 'not the same', so the merge suggestion doesn't come back."""
        with self.lock:
            d = list(self.meta.get("dismissed_pairs", []))
            if key not in d:
                d.append(key)
            self._set_meta("dismissed_pairs", d)
            return {"ok": True}

    # ---- quotes review
    def quotes_list(self, speaker="all", q="", offset=0, limit=50):
        """The Quotes view: a page of quotes filtered by speaker or text, each with its context, and the speakers' counts."""
        with self.lock:
            cond, params = [], []
            if speaker == "none":
                cond.append("x.char_id IS NULL")
            elif speaker not in ("all", "", None):
                cond.append("x.char_id = ?")
                params.append(int(speaker))
            if q:
                cond.append("CONTAINS_CI(x.text, ?)")
                params.append(q)
            where = ("WHERE " + " AND ".join(cond)) if cond else ""
            base = f"FROM quotes x JOIN tokens s ON s.uid=x.tok_start JOIN tokens e ON e.uid=x.tok_end {where}"
            total = self.db.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
            rows = self.db.execute(f"SELECT x.*, s.ord AS s, e.ord AS e {base} ORDER BY s.ord LIMIT ? OFFSET ?",
                                   params + [limit, offset]).fetchall()
            out = []
            for r in rows:
                d = self._span_out("quotes", r)
                ca, cb = max(0, d["s"] - 25), min(self.n_tokens - 1, d["e"] + 25)
                d["context"], d["context_start"], d["sent"] = self.words_between(ca, cb), ca, self.sentence_of(d["s"])
                out.append(d)
            speakers = Counter(r[0] for r in self.db.execute("SELECT char_id FROM quotes"))
            return {"total": total, "offset": offset, "items": out,
                    "speakers": [{"id": k, "name": self.cluster_name(k), "n": v} for k, v in speakers.most_common() if k is not None],
                    "no_speaker": speakers.get(None, 0)}

    def set_speakers(self, quote_uids, cid, quote_rule=False):
        """Set the speaker of several quotes at once (one undoable step). With `quote_rule`, also move each quote's own
        I/me/my/mine/myself to the new speaker and you/your/yours/yourself to the other side of its conversation, where
        there is one (`_quote_pronoun_moves`); returns how many pronouns moved as `moved`."""
        with self.lock:
            quote_uids = [int(u) for u in quote_uids]
            if not quote_uids:
                raise EditError("No quotes selected")
            who = self.cluster_name(cid) if cid is not None else "no one"
            moved = 0
            with self.batch(f"{len(quote_uids)} quote{'s' if len(quote_uids) > 1 else ''} now spoken by {who}"):
                finder = MentionFinder(self.db)
                for u in quote_uids:
                    if self._row("quotes", u):
                        self._set_speaker(u, cid, finder)
                if quote_rule and cid is not None:
                    for e, target in self._quote_pronoun_moves(quote_uids):
                        before = self._row("entities", e["uid"])
                        if before and before["coref"] != target:
                            self.update("entities", e["uid"], {"coref": target})
                            self._sync_quotes_for_entity(before, {"coref": target})
                            moved += 1
            return {**self.history(1), "moved": moved}

    # ---- review tracking (one flag per sentence, kept on its first token)
    def review_progress(self):
        """How many sentences are marked reviewed, of how many."""
        with self.lock:
            done = self.db.execute("SELECT COUNT(*) FROM tokens WHERE reviewed=1 AND (sent_start=1 OR ord=0)").fetchone()[0]
            return {"reviewed": done, "total": len(self.sent_starts)}

    def set_reviewed(self, sents, on):
        """Mark sentences reviewed or not. The flag sits on a sentence's first token; it is undoable but not in the change log."""
        with self.lock:
            sents = sorted(set(int(x) for x in sents if 0 <= int(x) < len(self.sent_starts)))
            if not sents:
                raise EditError("No sentences given")
            label = (f"Marked sentence{'s' if len(sents) > 1 else ''} {', '.join(map(str, sents[:5]))}"
                     f"{'…' if len(sents) > 5 else ''} as {'reviewed' if on else 'not reviewed'}")
            with self.batch(label, kind="review"):
                for x in sents:
                    self.update("tokens", self.uid_at[self.sent_starts[x]], {"reviewed": 1 if on else 0}, manual=False)
            return {"history": self.history(1), "progress": self.review_progress()}

    def next_unreviewed(self, after):
        """The first sentence after `after` that isn't reviewed (wrapping round), with the progress."""
        with self.lock:
            start = self.sent_starts[after] if 0 <= after < len(self.sent_starts) else -1
            q = "SELECT ord FROM tokens WHERE (sent_start=1 OR ord=0) AND reviewed=0 AND ord > ? ORDER BY ord LIMIT 1"
            r = self.db.execute(q, (start,)).fetchone() or self.db.execute(q, (-1,)).fetchone()
            return {"sent": self.sentence_of(r[0]) if r else None, "progress": self.review_progress()}

    # ---- tokenization and segmentation
    def _sent_tokens(self, s):
        """All token rows of sentence `s`, in order."""
        a, b = self.sentence_bounds(s)
        return [dict(r) for r in self.db.execute("SELECT * FROM tokens WHERE ord BETWEEN ? AND ? ORDER BY ord", (a, b))]

    def _repoint(self, old_uid, new_uid, which=("tok_start", "tok_end", "m_start", "m_end"), only_end=False):
        """Move span boundaries and mention links from one token to another."""
        for tbl in SPAN_TABLES:
            cols = [c for c in which if c in (("tok_start", "tok_end", "m_start", "m_end") if tbl == "quotes" else ("tok_start", "tok_end"))]
            if only_end:
                cols = [c for c in cols if c in ("tok_end", "m_end")]
            for c in cols:
                for r in self.db.execute(f"SELECT uid FROM {tbl} WHERE {c}=?", (old_uid,)).fetchall():
                    self.update(tbl, r["uid"], {c: new_uid}, manual=False)

    def split_token(self, uid, new_word: str):
        """Split a token by typing a space in its word (`cannot` → `can not`); the spelling can't change.
        
        The first part keeps the token's uid and annotations; new parts get placeholder tags (X/XX) and the sentence is
        re-tagged with spaCy afterwards. Spans that ended on the old token now end on the last part.
        """
        with self.lock:
            t = self._row("tokens", uid)
            if not t:
                raise EditError("That token no longer exists")
            parts = new_word.split()
            if "".join(parts) != t["word"]:
                raise EditError("The spelling has to stay as in the original text. Only add spaces where the token should split.")
            if len(parts) < 2:
                raise EditError("Type a space where the token should split")
            sent = self.sentence_of(t["ord"])
            label = f"Split token {t['ord']} “{t['word']}” into " + " + ".join(f"“{p}”" for p in parts)
            with self.batch(label):
                cs = t["char_start"]
                offs = []
                for p in parts:
                    offs.append((cs, cs + len(p)))
                    cs += len(p)
                self.update("tokens", uid, {"word": parts[0], "char_end": offs[0][1], "lemma": parts[0].lower()}, manual={"word"})
                last = uid
                extra = t["extra"]
                for p, (a, b) in zip(parts[1:], offs[1:]):
                    new = self.insert("tokens", {
                        "ord": t["ord"], "word": p, "lemma": p.lower(), "char_start": a, "char_end": b,
                        "pos": "X", "tag": "XX", "dep": "dep", "head": uid, "event": "O",
                        "sent_start": 0, "para_start": 0, "reviewed": 0, "manual": "[]", "extra": extra}, manual=False)
                    last = new["uid"]
                self._repoint(uid, last, only_end=True)
            return self._retag_after([sent])

    def merge_tokens(self, a: int, b: int):
        """Merge touching tokens a..b (no space between them in the text) into one; spans and heads are re-pointed."""
        with self.lock:
            a, b = min(a, b), max(a, b)
            toks = [dict(r) for r in self.db.execute("SELECT * FROM tokens WHERE ord BETWEEN ? AND ? ORDER BY ord", (a, b))]
            if len(toks) < 2:
                raise EditError("Select at least two tokens to merge")
            if any(x["sent_start"] for x in toks[1:]):
                raise EditError("These tokens are in different sentences. Merge the sentences first.")
            for x, y in zip(toks, toks[1:]):
                if x["char_end"] != y["char_start"]:
                    raise EditError(f"“{x['word']}” and “{y['word']}” are separated by a space in the text. "
                                    "BookNLP tokens never contain spaces; use an entity to group words instead.")
            first, rest = toks[0], toks[1:]
            gone = {x["uid"] for x in rest}
            word = "".join(x["word"] for x in toks)
            sent = self.sentence_of(first["ord"])
            with self.batch(f"Merged tokens {a}–{b} into “{word}”"):
                upd = {"word": word, "char_end": toks[-1]["char_end"], "lemma": word.lower(),
                       "event": "EVENT" if any(x["event"] == "EVENT" for x in toks) else first["event"]}
                if first["head"] in gone:
                    upd["head"] = first["uid"]
                self.update("tokens", first["uid"], upd, manual={"word"})
                for x in rest:
                    for r in self.db.execute("SELECT uid FROM tokens WHERE head=?", (x["uid"],)).fetchall():
                        if r["uid"] not in gone and r["uid"] != first["uid"]:
                            self.update("tokens", r["uid"], {"head": first["uid"]}, manual=False)
                    self._repoint(x["uid"], first["uid"])
                    self.delete("tokens", x["uid"])
            return self._retag_after([sent])

    def split_sentence(self, ord_: int):
        """Start a new sentence at token `ord_`; both sentences are re-tagged."""
        with self.lock:
            uid = self.uid_of_ord(ord_)
            t = self._row("tokens", uid)
            s = self.sentence_of(ord_)
            if self.sent_starts[s] == ord_:
                raise EditError("A sentence already starts here")
            with self.batch(f"Split sentence {s} before token {ord_} “{t['word']}”"):
                self.update("tokens", uid, {"sent_start": 1})
            return self._retag_after([s, s + 1])

    def merge_sentence(self, s: int):
        """Merge sentence `s` with the next one (removing a paragraph break between them)."""
        with self.lock:
            if not 0 <= s < len(self.sent_starts) - 1:
                raise EditError("There is no next sentence to merge with")
            a2, _ = self.sentence_bounds(s + 1)
            uid = self.uid_of_ord(a2)
            t = self._row("tokens", uid)
            fields = {"sent_start": 0}
            label = f"Merged sentences {s} and {s + 1}"
            if t["para_start"]:
                fields["para_start"] = 0
                label += " (removing the paragraph break between them)"
            with self.batch(label):
                self.update("tokens", uid, fields)
            return self._retag_after([s])

    def set_paragraph(self, s: int, on: bool):
        """Start or remove a paragraph break before sentence `s`."""
        with self.lock:
            if s == 0:
                raise EditError("The first sentence always starts a paragraph")
            a, _ = self.sentence_bounds(s)
            uid = self.uid_of_ord(a)
            with self.batch(f"{'Started a paragraph at' if on else 'Removed the paragraph break before'} sentence {s}"):
                self.update("tokens", uid, {"para_start": 1 if on else 0})
            return {"history": self.history(1), "proposals": 0, "pending": self.pending_count()}

    # ---- spaCy re-tagging (suggestions you accept or reject)
    def _nlp(self):
        """Load the spaCy model once (without the entity tagger); EditError with the reason if it can't be loaded."""
        if self._spacy is None:
            try:
                import spacy
                self._spacy = spacy.load(self.spacy_model, disable=["ner"])
            except Exception as e:  # spaCy or the model missing
                self._spacy_error = f"{type(e).__name__}: {e}"
                raise EditError(f"spaCy couldn't load {self.spacy_model}: {e}. For re-tagging suggestions, install spaCy "
                                f"(pip install spacy) and the model (python -m spacy download {self.spacy_model}).")
        return self._spacy

    def _retag_after(self, sents):
        """After a structural edit: re-parse the affected sentences and report the suggestions, or why none could be made."""
        out = {"history": self.history(1), "proposals": 0, "retag_error": None}
        try:
            out["proposals"] = self.retag(sents)
        except EditError as e:
            out["retag_error"] = str(e)
        out["pending"] = self.pending_count()
        return out

    def retag(self, sents) -> int:
        """Parse the sentences with spaCy (one sentence at a time, on the book's own tokens) and store what it would change
        in lemma/POS/fine POS/dependency/head as suggestions in `proposals`. Values edited by hand are never suggested.
        """
        with self.lock:
            nlp = self._nlp()  # first: it says what to install if spaCy is missing (importing Doc before it would crash instead)
            from spacy.tokens import Doc
            text = self.meta["text"]
            props, uids = [], []
            for s in sorted(set(x for x in sents if 0 <= x < len(self.sent_starts))):
                toks = self._sent_tokens(s)
                if not toks:
                    continue
                spaces = [bool(text[x["char_end"]:y["char_start"]]) for x, y in zip(toks, toks[1:])] + [False]
                doc = Doc(nlp.vocab, words=[x["word"] for x in toks], spaces=spaces)
                for i, st in enumerate(doc):
                    st.is_sent_start = i == 0
                for _, proc in nlp.pipeline:
                    doc = proc(doc)
                sig = ",".join(str(x["uid"]) for x in toks)
                for x, st in zip(toks, doc):
                    uids.append(x["uid"])
                    manual = set(json.loads(x["manual"] or "[]"))
                    new = {"lemma": st.lemma_, "pos": st.pos_, "tag": st.tag_, "dep": st.dep_, "head": toks[st.head.i]["uid"]}
                    for f in RETAG_FIELDS:
                        if f in manual or str(x[f]) == str(new[f]):
                            continue
                        props.append((x["uid"], f, str(x[f]), str(new[f]), time.time(), sig))
            if uids:
                self.db.execute(f"DELETE FROM proposals WHERE uid IN ({','.join('?' * len(uids))})", uids)
            self.db.executemany("INSERT INTO proposals(uid, field, old, new, created, sig) VALUES (?,?,?,?,?,?)", props)
            self.db.commit()
            return len(props)

    def retag_sentence(self, s: int):
        """Re-tag one sentence on request; returns how many suggestions there are."""
        with self.lock:
            n = self.retag([s])
            return {"proposals": n, "pending": self.pending_count()}

    def _live_proposals(self):
        """Pending suggestions whose token still has the value they were made against."""
        out, stale, sigs = [], [], {}
        for p in self.db.execute("SELECT * FROM proposals ORDER BY id").fetchall():
            t = self._row("tokens", p["uid"])
            if not t or str(t[p["field"]]) != p["old"]:
                stale.append(p["id"])
                continue
            # a suggestion only holds while its sentence has the same tokens it was parsed with
            s = self.sentence_of(t["ord"])
            if s not in sigs:
                sigs[s] = ",".join(str(x["uid"]) for x in self._sent_tokens(s))
            if p["sig"] is not None and p["sig"] != sigs[s]:
                stale.append(p["id"])
                continue
            if p["field"] in json.loads(t["manual"] or "[]"):
                stale.append(p["id"])
                continue
            out.append((dict(p), t))
        if stale:
            self.db.execute(f"DELETE FROM proposals WHERE id IN ({','.join('?' * len(stale))})", stale)
            self.db.commit()
        return out

    def pending_count(self):
        """How many suggestions are waiting."""
        with self.lock:
            return len(self._live_proposals())

    def proposals(self):
        """Waiting suggestions grouped by sentence, for the Suggestions panel."""
        with self.lock:
            live = self._live_proposals()
            groups = {}
            for p, t in live:
                s = self.sentence_of(t["ord"])
                g = groups.setdefault(s, {"sent": s, "items": []})
                item = {"id": p["id"], "uid": t["uid"], "ord": t["ord"], "word": t["word"], "field": p["field"],
                        "old": p["old"], "new": p["new"]}
                if p["field"] == "head":
                    item["old_label"] = self._head_label(t, p["old"])
                    item["new_label"] = self._head_label(t, p["new"])
                g["items"].append(item)
            out = []
            for s in sorted(groups):
                a, b = self.sentence_bounds(s)
                g = groups[s]
                g["text"] = self.text_between(a, b)
                g["start"] = a
                g["items"].sort(key=lambda i: (i["ord"], RETAG_FIELDS.index(i["field"])))
                out.append(g)
            return {"count": len(live), "sentences": out}

    def _head_label(self, t, uid_str):
        """Readable name of a head token in a suggestion ('root' or 't12 “word”')."""
        try:
            h = self._row("tokens", int(uid_str))
        except (TypeError, ValueError):
            h = None
        if not h:
            return "–"
        if h["uid"] == t["uid"]:
            return "root"
        return f"t{h['ord']} “{h['word']}”"

    def accept_proposals(self, ids):
        """Apply the chosen suggestions in one undoable step (not marked as hand edits, so later re-tagging can update them)."""
        with self.lock:
            live = {p["id"]: (p, t) for p, t in self._live_proposals()}
            chosen = [live[i] for i in ids if i in live]
            if not chosen:
                raise EditError("Those suggestions no longer apply")
            with self.batch(f"Accepted {len(chosen)} spaCy suggestion{'s' if len(chosen) > 1 else ''}"):
                for p, t in chosen:
                    v = p["new"]
                    if p["field"] == "head":
                        v = int(v)
                        if not self._row("tokens", v):
                            continue
                    self.update("tokens", t["uid"], {p["field"]: v}, manual=False)
                self.db.execute(f"DELETE FROM proposals WHERE id IN ({','.join('?' * len(chosen))})", [p["id"] for p, _ in chosen])
            return {"history": self.history(1), "pending": self.pending_count()}

    def reject_proposals(self, ids):
        """Discard the chosen suggestions."""
        with self.lock:
            if ids:
                self.db.execute(f"DELETE FROM proposals WHERE id IN ({','.join('?' * len(ids))})", list(ids))
                self.db.commit()
            return {"pending": self.pending_count()}

    # ---- .book rebuild
    def _original_corefs(self):
        """Each entity's coreference group as BookNLP wrote it (None for entities added in the editor)."""
        orig = {}
        for r in self.db.execute("SELECT uid, before FROM changes WHERE id IN "
                                 "(SELECT MIN(id) FROM changes WHERE tbl='entities' GROUP BY uid)"):
            b = json.loads(r["before"])
            orig[r["uid"]] = b["coref"] if b else None
        return orig

    def _original_g(self):
        """BookNLP's original gender evidence per group, from the `.book` file."""
        out = {}
        try:
            for c in json.loads(self.meta.get("passthrough", {}).get(".book") or "{}").get("characters", []):
                out[c["id"]] = c.get("g")
        except (ValueError, AttributeError, TypeError):
            pass
        return out

    def book_types(self):
        """The entity types included in the exported .book (default: PER only, as BookNLP)."""
        return list(self.meta.get("book_types") or ["PER"])

    def book_settings(self):
        """The choice of .book entity types with how many groups each type has."""
        with self.lock:
            counts = Counter()
            for r in self.db.execute("SELECT cat, COUNT(DISTINCT coref) FROM entities GROUP BY cat"):
                counts[r[0]] = r[1]
            cats = [c for c in TAGSETS[("entities", "cat")]] + sorted(c for c in counts if c not in TAGSETS[("entities", "cat")])
            return {"types": self.book_types(), "available": [{"cat": c, "groups": counts.get(c, 0)} for c in cats]}

    def set_book_types(self, types):
        """Choose which entity types the exported .book covers."""
        with self.lock:
            known = {a["cat"] for a in self.book_settings()["available"]}
            types = [t for t in dict.fromkeys(types) if t in known]
            if not types:
                raise EditError("Choose at least one entity type for .book")
            self._set_meta("book_types", types)
            return self.book_settings()

    def build_book(self):
        """Return (chardata, html, description of the code used) for the export. Cached until the next edit."""
        with self.lock:
            return self._memo("book", tuple(self.book_types()), self._build_book)

    def _build_book(self):
        """The `.book` data and the `.book.html` report for the export (see `_book_data`); the report is only made here."""
        chardata, tokens, entities, assignments, quotes, renamed, used = self.book_data()
        return chardata, bookfile.book_html(chardata, tokens, entities, assignments, quotes, renamed), used

    def book_data(self):
        """`.book`'s character data with what the report is made from: (chardata, tokens, entities, assignments, quotes,
        renamed, description of the code used). Cached until the next edit; a group's page needs only the data, not the report."""
        with self.lock:
            return self._memo("book_data", tuple(self.book_types()), self._build_book_data)

    def _build_book_data(self):
        """Rebuild BookNLP's `.book` data from the corrected tokens, entities and groups.

        Runs BookNLP's own get_syntax (see bookfile.py) on the edited data. Chosen extra types are presented to it as PER; the
        group's pronouns are BookNLP's original evidence (combined for merged groups) unless set by hand.
        """
        get_syntax, Tok, used = bookfile.load()
        rows = self.db.execute("SELECT uid, word, lemma, char_start, pos, tag, dep, head, sent_start, para_start "
                               "FROM tokens ORDER BY ord").fetchall()      # plain rows: a dict per token is slow for a whole book
        ord_of = {r[0]: i for i, r in enumerate(rows)}
        words = [r[1] for r in rows]
        tokens, para, sent, within = [], -1, -1, 0
        for i, (_, word, lemma, char_start, pos, tag, dep, head, sent_start, para_start) in enumerate(rows):
            if i == 0 or para_start:
                para += 1
            if i == 0 or sent_start:
                sent += 1
                within = 0
            tokens.append(Tok(para, sent, within, i, word, pos, tag, lemma, dep, ord_of.get(head, i), None, char_start))
            within += 1
        ents = []
        for r in self.db.execute("SELECT uid, tok_start, tok_end, coref, prop, cat, text FROM entities"):
            if r["tok_start"] in ord_of and r["tok_end"] in ord_of:
                s_, e_ = ord_of[r["tok_start"]], ord_of[r["tok_end"]]
                ents.append(((s_, e_, f"{r['prop']}_{r['cat']}", " ".join(words[s_:e_ + 1])), r["coref"], r["uid"]))
        ents.sort(key=lambda x: x[0])
        entities = [e for e, _, _ in ents]
        assignments = [c for _, c, _ in ents]
        # BookNLP's get_syntax only looks at PER mentions. To include other types you've chosen,
        # their mentions are presented to it as PER; the real type goes into a "type" field.
        types = self.book_types()
        include = set(types)
        syntax_entities = [(a, b, f"{cat.split('_')[0]}_PER" if cat.split("_", 1)[1] in include else cat, t)
                           for (a, b, cat, t) in entities]
        type_counts = defaultdict(Counter)
        for (a, b, cat, t), c in zip(entities, assignments):
            ty = cat.split("_", 1)[1]
            if ty in include:
                type_counts[c][ty] += 1

        # referential gender: BookNLP's original values, combined where groups were merged,
        # unless pronouns were set by hand in the editor
        orig = self._original_corefs()
        orig_g = self._original_g()
        contrib = defaultdict(Counter)
        for (_, c, uid) in ents:
            o = orig.get(uid, c)
            if o is not None:
                contrib[c][o] += 1
        genders = {}
        for c, cnt in contrib.items():
            g = bookfile.combine_g([(orig_g.get(o), n) for o, n in cnt.items()])
            if g is not None:
                genders[c] = g
        renamed = {}
        for r in self.db.execute("SELECT * FROM groups"):
            if r["pronoun"] is not None:
                if r["pronoun"]:
                    genders[r["uid"]] = bookfile.manual_g(r["pronoun"], genders.get(r["uid"]))
                else:
                    genders.pop(r["uid"], None)
            if r["name"]:
                renamed[r["uid"]] = r["name"]

        chardata = get_syntax(tokens, syntax_entities, assignments, genders)
        cl = self.clusters()
        for ch in chardata["characters"]:
            ch["name"] = renamed.get(ch["id"]) or (cl[ch["id"]]["name"] if ch["id"] in cl else None)
            if ch["id"] in cl and cl[ch["id"]].get("carry"):
                ch["collection_id"] = cl[ch["id"]]["carry"]
            if types != ["PER"]:
                ch["type"] = type_counts[ch["id"]].most_common(1)[0][0] if type_counts[ch["id"]] else None
        quotes = []
        for r in self.db.execute("SELECT tok_start, tok_end, char_id FROM quotes"):
            if r["tok_start"] in ord_of and r["tok_end"] in ord_of:
                quotes.append((ord_of[r["tok_start"]], ord_of[r["tok_end"]], r["char_id"]))
        quotes.sort()
        return chardata, tokens, entities, assignments, quotes, renamed, used

    def book_preview(self, cid):
        """What a group's entry in .book will contain (top agents, patients, possessions, modifiers), or why it's left out."""
        chardata, *_, used = self.book_data()
        for ch in chardata["characters"]:
            if ch["id"] == cid:
                top = lambda k: [{"w": w, "n": n} for w, n in Counter(x["w"] for x in ch.get(k, [])).most_common(12)]
                return {"in_book": True, "count": ch["count"], "g": ch.get("g"), "name": ch.get("name"),
                        "types": self.book_types(), "type": ch.get("type"),
                        "agent": top("agent"), "patient": top("patient"), "poss": top("poss"), "mod": top("mod"),
                        "n": {k: len(ch.get(k, [])) for k in ("agent", "patient", "poss", "mod")}, "used": used}
        c = self.clusters().get(cid)
        types = self.book_types()
        n = self.db.execute(f"SELECT COUNT(*) FROM entities WHERE coref=? AND cat IN ({','.join('?' * len(types))})",
                            [cid] + types).fetchone()[0]
        return {"in_book": False, "included_mentions": n, "types": types, "cat": c["cat"] if c else None, "used": used}

    # ---- export
    def export(self, dest: Path):
        """Export: write a complete folder in BookNLP's file formats to `dest` and return a summary.

        Writes `.tokens`, `.entities`, `.supersense`, `.quotes` from the current data (span texts rebuilt from the tokens),
        `.book`/`.book.html` rebuilt (or the original copied if that fails), every other kept file unchanged, plus
        `.validation.tsv` (values outside the tag lists), `.changes.md` (the undoable steps still applied, not review ticks),
        `.characters.tsv` (every group) and `.txt` (a copy of the original text BookNLP was run on). The analyser reads
        this folder, so one that failed half way is removed again rather than left looking like an export.
        """
        with self.lock:
            dest = Path(dest)
            dest.mkdir(parents=True, exist_ok=False)
            try:
                return self._write_export(dest)
            except BaseException:
                shutil.rmtree(dest, ignore_errors=True)
                raise

    def _write_export(self, dest: Path):
        """Write every export file into the (new, empty) folder `dest`; returns the summary `export` hands back."""
        bid = self.meta["book_id"]
        toks = [dict(r) for r in self.db.execute("SELECT * FROM tokens ORDER BY ord")]
        ord_of = {t["uid"]: t["ord"] for t in toks}
        words = [t["word"] for t in toks]
        self._export_tokens(dest, bid, toks, ord_of)
        (dest / f"{bid}.txt").write_text(self.meta["text"], encoding="utf-8")        # the original text, unchanged
        self._export_layers(dest, bid, ord_of, words)
        book_note = self._export_book(dest, bid)
        issues = self._export_validation(dest, bid)
        self._export_changes(dest, bid, book_note)
        self._export_characters(dest, bid)
        return {"path": str(dest.resolve()), "issues": issues, "book": book_note}

    def _export_tokens(self, dest, bid, toks, ord_of):
        """`.tokens`: BookNLP's columns, with sentence and paragraph numbers recomputed from the flags and heads as positions;
        columns the editor doesn't know are written back as imported."""
        header = self.meta["tokens_header"]
        lines = ["\t".join(header)]
        para = sent = -1
        within = 0
        for i, t in enumerate(toks):
            if i == 0 or t["para_start"]:
                para += 1
            if i == 0 or t["sent_start"]:
                sent += 1
                within = 0
            std = {
                "paragraph_ID": para, "sentence_ID": sent, "token_ID_within_sentence": within,
                "token_ID_within_document": i, "word": t["word"], "lemma": t["lemma"],
                "byte_onset": t["char_start"], "byte_offset": t["char_end"], "POS_tag": t["pos"],
                "fine_POS_tag": t["tag"], "dependency_relation": t["dep"],
                "syntactic_head_ID": ord_of.get(t["head"], i), "event": t["event"]}
            extra = json.loads(t["extra"] or "{}")
            lines.append("\t".join(str(std[c]) if c in std else str(extra.get(c, "")) for c in header))
            within += 1
        (dest / f"{bid}.tokens").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _export_layers(self, dest, bid, ord_of, words):
        """`.entities`, `.supersense` and `.quotes` in reading order; a layer's span text is rebuilt from the tokens when that is
        how BookNLP wrote it (`meta.join_style`)."""
        join_style = self.meta.get("join_style", {})

        def span_rows(tbl):
            """The layer's rows as dicts sorted by position, with start/end positions (`s`, `e`) and the text to write."""
            rows = []
            for r in self.db.execute(f"SELECT * FROM {tbl}"):
                r = dict(r)
                r["s"], r["e"] = ord_of[r["tok_start"]], ord_of[r["tok_end"]]
                if join_style.get(tbl, True):
                    r["text"] = " ".join(words[r["s"]:r["e"] + 1])
                rows.append(r)
            return sorted(rows, key=lambda r: (r["s"], r["e"]))

        def has(tbl):
            """Does the layer get a file: it was imported, or has rows now."""
            return tbl in self.meta["layers"] or self._count(tbl)

        if has("entities"):
            out = ["COREF\tstart_token\tend_token\tprop\tcat\ttext"]
            out += [f"{r['coref']}\t{r['s']}\t{r['e']}\t{r['prop']}\t{r['cat']}\t{r['text']}" for r in span_rows("entities")]
            (dest / f"{bid}.entities").write_text("\n".join(out) + "\n", encoding="utf-8")
        if has("supersenses"):
            out = ["start_token\tend_token\tsupersense_category\ttext"]
            out += [f"{r['s']}\t{r['e']}\t{r['cat']}\t{r['text']}" for r in span_rows("supersenses")]
            (dest / f"{bid}.supersense").write_text("\n".join(out) + "\n", encoding="utf-8")
        if has("quotes"):
            out = ["\t".join(["quote_start", "quote_end", "mention_start", "mention_end", "mention_phrase", "char_id", "quote"])]
            for r in span_rows("quotes"):
                ms = ord_of.get(r["m_start"]) if r["m_start"] is not None else None
                me = ord_of.get(r["m_end"]) if r["m_end"] is not None else None
                phrase = " ".join(words[ms:me + 1]) if ms is not None and me is not None else r["m_phrase"]
                out.append("\t".join(str(x) for x in (r["s"], r["e"], ms, me, phrase, r["char_id"], r["text"])))
            (dest / f"{bid}.quotes").write_text("\n".join(out) + "\n", encoding="utf-8")

    def _export_book(self, dest, bid):
        """`.book` and `.book.html` rebuilt (or BookNLP's own copied when that fails), then every other kept `BOOK.*` file
        unchanged. Returns the sentence the change log and the page report about the `.book`, or None when there was none."""
        passthrough = dict(self.meta.get("passthrough", {}))
        book_note = None
        if ".book" in passthrough or ".book.html" in passthrough:
            try:
                chardata, html, used = self.build_book()
                (dest / f"{bid}.book").write_text(json.dumps(chardata), encoding="utf-8")
                (dest / f"{bid}.book.html").write_text(html, encoding="utf-8")
                passthrough.pop(".book", None)
                passthrough.pop(".book.html", None)
                types = self.book_types()
                book_note = (f"Rebuilt {bid}.book and {bid}.book.html with {used}. "
                             f"Entity types included in .book: {', '.join(types)}"
                             f"{' (BookNLP’s default)' if types == ['PER'] else '; each entry has a type field'}.")
            except Exception as e:  # keep the export usable; say what happened
                book_note = (f"Couldn't rebuild {bid}.book ({type(e).__name__}: {e}); "
                             f"BookNLP's original .book and .book.html were copied unchanged.")
        for suffix, content in passthrough.items():
            (dest / f"{bid}{suffix}").write_text(content, encoding="utf-8")
        return book_note

    def _export_validation(self, dest, bid):
        """`.validation.tsv`: every tagged value outside the tag lists (`core/tagsets.py`). Returns how many there are."""
        issues = ["layer\tposition\tfield\tvalue\ttext"]
        for (tbl, field), allowed in TAGSETS.items():
            ph = ",".join("?" * len(allowed))
            if tbl == "tokens":
                q = f"SELECT ord AS s, {field} AS v, word AS txt FROM tokens WHERE {field} NOT IN ({ph}) ORDER BY ord"
            else:
                q = (f"SELECT t.ord AS s, x.{field} AS v, x.text AS txt FROM {tbl} x JOIN tokens t ON t.uid=x.tok_start "
                     f"WHERE x.{field} NOT IN ({ph}) ORDER BY t.ord")
            for r in self.db.execute(q, allowed):
                issues.append(f"{tbl}\t{r['s']}\t{field}\t{r['v']}\t{r['txt']}")
        (dest / f"{bid}.validation.tsv").write_text("\n".join(issues) + "\n", encoding="utf-8")
        return len(issues) - 1

    def _export_changes(self, dest, bid, book_note):
        """`.changes.md`: every step still applied, oldest first (review marks are left out)."""
        log = [f"# Changes to {bid}", "",
               f"Exported {time.strftime('%Y-%m-%d %H:%M')} from the BookNLP editor, oldest first.", ""]
        if book_note:
            log[2:2] = [book_note, ""]
        for r in self.db.execute("SELECT label, ts FROM batches WHERE undone=0 AND kind IS NOT 'review' ORDER BY id"):
            log.append(f"- {time.strftime('%Y-%m-%d %H:%M', time.localtime(r['ts']))}  {r['label']}")
        (dest / f"{bid}.changes.md").write_text("\n".join(log) + "\n", encoding="utf-8")

    def _export_characters(self, dest, bid):
        """`.characters.tsv`: every group, biggest first, with its type, pronouns, counts and collection character."""
        rows = ["id\tname\ttype\tpronouns\tmentions\tquotes\tnot_a_character\tcollection_id\tcollection_name"]
        names = self._carry_names()
        for c in sorted(self.clusters().values(), key=lambda c: (-c["count"], c["id"])):
            rows.append("\t".join(str(x) for x in (c["id"], c["name"], c["cat"], c["pronoun"] or "", c["count"], c["quotes"],
                                                   "yes" if c["hidden"] else "", c.get("carry") or "",
                                                   names.get(c.get("carry"), ""))))
        (dest / f"{bid}.characters.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    def _carry_names(self):
        """Names of the collection characters, by id, for the characters export."""
        out = {}
        if self.carry_colls:
            for lst in self.carry_colls.data["lists"].values():
                for e in lst.get("characters", []):
                    out[e["id"]] = e["name"]
        return out

    def _count(self, tbl):
        """Number of rows in a table."""
        return self.db.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]


def _regexp(pattern, value):
    """SQLite REGEXP function: does the pattern match anywhere in the value (False for an invalid pattern)."""
    if value is None:
        return False
    try:
        return re.search(pattern, str(value)) is not None
    except re.error:
        return False


def _contains_ci(value, needle):
    """SQLite function for "contains" searches: does `value` hold `needle`, ignoring case? Case is folded the way Python does
    (SQLite's own LOWER/LIKE only fold ASCII, so "évariste" would miss "Évariste", and `_`/`%` in the text act as wildcards)."""
    return value is not None and str(needle).casefold() in str(value).casefold()


def _dump(v):
    """JSON text of a value for the history tables ('null' for None)."""
    return json.dumps(v) if v is not None else "null"


def _trim(s, n=40):
    """Shorten text to n characters with an ellipsis, for labels."""
    s = s or ""
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"
