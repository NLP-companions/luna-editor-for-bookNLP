"""Time the editor on large synthetic corpora (see tests/synthetic.py). Not part of the test suite; run it when changing
anything that reads or writes a whole book, or the collection code.

    python -m tests.scale book [SENTENCES] [GROUPS]       one long book: import, every view, bulk edits, export
    python -m tests.scale library [BOOKS] [SENTENCES]     a collection growing over many books (each with its own cast)

Output is one line per operation with its time; anything over 2 s is marked. On a 2024 laptop a 20,000-sentence book
(190,000 tokens, 34,000 mentions, 9,000 quotes) takes about 2 s to import, opens in 0.2 s, and no single editing action
takes over a second except the first computation of the merge suggestions (about 1 s); see docs/DOCUMENTATION.md §12.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from luna.core.collections_store import Collections  # noqa: E402
from luna.core.store import Store, create_project  # noqa: E402
from tests.synthetic import write_book  # noqa: E402

SLOW = 2.0      # seconds after which a line is marked


def timed(label, fn, *args, **kw):
    """Run `fn`, print how long it took (marked when slow) and return its result (the exception, if it raised one)."""
    t = time.perf_counter()
    try:
        out = fn(*args, **kw)
    except Exception as e:  # noqa: BLE001 - a benchmark reports and goes on
        out, label = e, f"{label}  !! {type(e).__name__}: {str(e)[:80]}"
    dt = time.perf_counter() - t
    print(f"{dt:8.2f}s  {label}{'  <<<<<' if dt > SLOW else ''}", flush=True)
    return out


def book(sentences=20000, groups=3000):
    """One long book with a long tail of small groups: every view, then the heavy edits."""
    tmp = Path(tempfile.mkdtemp(prefix="bne-scale-"))
    try:
        print(f"writing {sentences:,} sentences, {groups:,} characters …", flush=True)
        text = write_book(tmp, "big", sentences, seed=2, groups=groups, zipf=1.05, distinct_names=True)
        db = tmp / "big.sqlite"
        timed("import (create_project)", create_project, db, tmp / "big", text)
        st = timed("open (Store)", Store, db, "no-such-model")
        print(f"          {st.n_tokens:,} tokens, {len(st.clusters()):,} groups", flush=True)
        top = sorted(st.clusters().values(), key=lambda c: -c["count"])
        for label, fn, args in [
                ("info", st.info, ()), ("window of 30 sentences", st.window, (1000, 30)), ("groups list", st.groups_list, ()),
                ("group page", st.group_detail, (top[0]["id"],)), ("quotes list", st.quotes_list, ()),
                ("search, 100,000 hits", st.search, ("tokens", "pos", "eq", "NOUN")),
                ("phrase search", st.search, ("tokens", "word", "phrase", "Baker Street")),
                ("merge suggestions", st.merge_suggestions, ()), ("fragments", st.fragments, (3,)),
                ("speaker suggestions", st.quote_suggestions, ()), ("checks", st.checks, (None, 0, 5)),
                ("pronoun pass", st.pass_step, (-1, "next")), ("quote pass", st.qpass_step, (-1, "next")),
                ("entity excerpts", st.entity_context, (top[0]["id"],)), ("profiles", st._profiles, ()),
                (".book preview", st.book_preview, (top[0]["id"],))]:
            timed(label, fn, *args)
        quotes = [r[0] for r in st.db.execute("SELECT uid FROM quotes ORDER BY uid LIMIT 3000")]
        mentions = [r[0] for r in st.db.execute("SELECT uid FROM entities WHERE coref=? LIMIT 3000", (top[2]["id"],))]
        timed("give 3,000 quotes to one speaker", st.set_speakers, quotes, top[0]["id"])
        timed("  … with the quote rule", st.set_speakers, quotes, top[1]["id"], True)
        timed("move 3,000 mentions to another group", st.regroup, mentions, top[3]["id"])
        timed("merge 50 groups", st.merge_groups, top[4]["id"], [c["id"] for c in top[5:55]])
        timed("bulk: set POS on every noun", st.bulk_edit, "tokens", "pos", "eq", "NOUN", None, "pos", "NOUN")
        timed("bulk: make an entity of every 'said'", st.bulk_create, "tokens", "word", "eq", "said", None, "entities",
              {"cat": "VAR", "prop": "PROP", "group_mode": "text"})
        timed("undo", st.undo)
        timed("redo", st.redo)
        timed("mark 5,000 sentences reviewed", st.set_reviewed, list(range(5000)), True)
        timed("export", st.export, tmp / "exported")
        timed("reopen", Store, db, "no-such-model")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def library(books=60, sentences=4000):
    """A collection growing over many books: mark each book's main characters as checked and review what the collection
    offers, as the editor does; the cost of both grows with the characters already in the collection."""
    tmp = Path(tempfile.mkdtemp(prefix="bne-scale-"))
    try:
        colls = Collections(tmp / "collections.json")
        coll = colls.create("A long series")
        for i in range(1, books + 1):
            text = write_book(tmp, f"b{i}", sentences, seed=100 + i, groups=150, zipf=1.0, distinct_names=True, with_book=False)
            db = tmp / f"b{i}.sqlite"
            create_project(db, tmp / f"b{i}", text)
            st = Store(db, "no-such-model")
            key = f"{(tmp / f'b{i}').resolve()}::b{i}"
            colls.set_member(key, coll["id"])
            st.carry_colls, st.carry_key = colls, key
            main = sorted((c for c in st.clusters().values() if c["cat"] == "PER" and c["count"] >= 8), key=lambda c: -c["count"])[:30]
            t = time.perf_counter()
            slowest = 0.0
            for c in main:
                t1 = time.perf_counter()
                st.edit_group(c["id"], {"checked": True})
                slowest = max(slowest, time.perf_counter() - t1)
            check = time.perf_counter() - t
            t = time.perf_counter()
            review = st.carry_review()
            if i in (1, 5, 10, 20, 30, 45, 60) or i == books:
                chars = sum(len(lst["characters"]) for lst in colls.data["lists"].values())
                size = (tmp / "collections.json").stat().st_size // 1024
                print(f"book {i:2d}: marking {len(main)} groups checked {check:6.2f}s (slowest {slowest:4.2f}s), review "
                      f"{time.perf_counter() - t:5.2f}s ({len(review['characters'])} to apply, {len(review['ambiguous'])} to choose), "
                      f"{chars:,} characters, collections.json {size:,} KB", flush=True)
            st.db.close()
            shutil.rmtree(tmp / f"b{i}")
            (tmp / f"b{i}.sqlite").unlink()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    warnings.filterwarnings("ignore")
    what = sys.argv[1] if len(sys.argv) > 1 else "book"
    nums = [int(x) for x in sys.argv[2:]]
    {"book": book, "library": library}[what](*nums)
