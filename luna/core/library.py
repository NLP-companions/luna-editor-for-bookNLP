"""The library: which books there are to edit, and their working copies.

Source folders you add are scanned for BookNLP output (the folder itself and
the folders directly inside it). Each book found is matched to its working
copies in ./projects. Only one book is open at a time.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path

from luna.core.errors import EditError
from luna.core.store import create_project

NAME_RE = re.compile(r"^[\w .\-]{1,80}$")


class Library:
    """The library: the folders to search for BookNLP output, the working copies in ./projects, and the original-text paths (library.json)."""
    def __init__(self, here: Path):
        """Load library.json (folders and remembered text paths) and make sure ./projects exists."""
        self.here = here
        self.projects = here / "projects"
        self.state_path = here / "library.json"
        self.projects.mkdir(exist_ok=True)
        self.state = {"sources": [], "texts": {}, "books": {}}  # books: book key -> title, author, year, tags
        if self.state_path.exists():
            try:
                self.state.update(json.loads(self.state_path.read_text(encoding="utf-8")))
            except ValueError:
                # keep a copy of the unreadable file rather than overwrite it with an empty library at the next save
                self.state_path.rename(self.state_path.with_suffix(f".unreadable-{int(time.time())}.json"))
        self.exports = self._exports_path()
        self._summaries = {}    # working copy path -> ((modified, size), summary), so unchanged files aren't read again

    def _exports_path(self) -> Path:
        """Where Export writes to: the folder set in Settings (`exports_dir`), or `exports/` in the data folder by default."""
        custom = self.state.get("exports_dir")
        return Path(custom).expanduser() if custom else self.here / "exports"

    def set_exports_dir(self, path: str):
        """Change where Export writes to; an empty path goes back to the default `exports/`. The folder is created (its
        parents too) if it doesn't exist yet. Exports already written to the old location are left there, untouched."""
        path = (path or "").strip()
        if not path:
            self.state.pop("exports_dir", None)
        else:
            p = Path(path).expanduser()
            if not p.is_absolute():
                raise EditError("Give a full path, e.g. /Users/you/Books/exports")
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise EditError(f"Can't use that folder: {e}")
            self.state["exports_dir"] = str(p.resolve())
        self.save()
        self.exports = self._exports_path()
        return str(self.exports.resolve())

    def save(self):
        """Write library.json atomically (temp file, then replace), so a crash never leaves half a file."""
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    # ---- book details: title, author, year and tags you give a book, kept per book key in library.json
    def details_of(self, key):
        """A book's title, author, year (or None) and tags; empty when you haven't given any."""
        return {"title": "", "author": "", "year": None, "tags": [], **self.state["books"].get(key, {})}

    def set_details(self, key, fields):
        """Save title, author, year and/or tags of a book found in the folders; only the fields given change.

        Tags may be a list or text separated by commas; the year must be a number (or empty)."""
        if not any(b["key"] == key for b in self.scan()[0]):
            raise EditError("That book is no longer in your source folders")
        d = self.details_of(key)
        for f in ("title", "author"):
            if f in fields:
                d[f] = re.sub(r"\s+", " ", str(fields[f] or "")).strip()
        if "year" in fields:
            y = str(fields["year"] if fields["year"] is not None else "").strip()
            if y and not re.fullmatch(r"-?\d{1,4}", y):
                raise EditError("The year must be a number, for example 1892")
            d["year"] = int(y) if y else None
        if "tags" in fields:
            raw = fields["tags"]
            items = re.split(r"[,;\n]", raw) if isinstance(raw, str) else (raw or [])
            d["tags"] = list(dict.fromkeys(t for t in (str(x).strip() for x in items) if t))
        kept = {k: v for k, v in d.items() if v not in ("", None, [])}
        if kept:
            self.state["books"][key] = kept
        else:
            self.state["books"].pop(key, None)
        self.save()
        return self.details_of(key)

    # ---- sources
    def add_source(self, path: str):
        """Add a folder to search for BookNLP output."""
        p = Path(path).expanduser()
        if not p.is_dir():
            raise EditError(f"Not a folder: {p}")
        p = p.resolve()
        if str(p) not in self.state["sources"]:
            self.state["sources"].append(str(p))
            self.save()

    def remove_source(self, path: str):
        """Forget a folder (its files and working copies stay)."""
        self.state["sources"] = [s for s in self.state["sources"] if s != path]
        self.save()

    def _is_export(self, folder: Path) -> bool:
        """Is this folder inside ./exports? Editor exports must not be listed as BookNLP output."""
        try:
            folder.resolve().relative_to(self.exports.resolve())
            return True
        except ValueError:
            return False

    def scan(self):
        """Find the books in the folders: each folder itself and the folders directly inside it that hold `ID.tokens` and
        `ID.entities`. Returns (books, problems); editor exports (they have `ID.changes.md`) are skipped.
        """
        found, problems, seen = [], [], set()
        for src in self.state["sources"]:
            root = Path(src)
            if not root.is_dir():
                problems.append(f"Folder not found: {root}")
                continue
            try:
                folders = [root] + sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
            except PermissionError:
                problems.append(f"No permission to read {root}")
                continue
            for folder in folders:
                if self._is_export(folder):
                    continue
                for tok in sorted(folder.glob("*.tokens")):
                    bid = tok.name[: -len(".tokens")]
                    if (folder / f"{bid}.changes.md").exists():  # an editor export, not BookNLP output
                        continue
                    if not (folder / f"{bid}.entities").exists():
                        continue
                    key = f"{folder.resolve()}::{bid}"
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append({"key": key, "book_id": bid, "folder": str(folder.resolve()), "source": src,
                                  "modified": tok.stat().st_mtime})
        return found, problems

    # ---- working copies
    def project_info(self, path: Path):
        """Read-only summary of one working copy: book, change count, last edit, review progress, exports found for the book.

        What is read from the file is kept until the file changes (every edit rewrites it, so its modified time and size
        tell), which keeps the library page quick with dozens of long books."""
        stat = path.stat()
        info = {"name": path.stem, "path": str(path), "modified": stat.st_mtime}
        cached = self._summaries.get(path)
        if cached and cached[0] == (stat.st_mtime_ns, stat.st_size):
            info.update(cached[1])
        else:
            summary = self._read_summary(path)
            self._summaries[path] = ((stat.st_mtime_ns, stat.st_size), summary)
            info.update(summary)
        bid = info.get("book_id")
        info["exports"] = sorted((p.name for p in self.exports.glob(f"{glob.escape(bid)}-*") if p.is_dir()), reverse=True) if bid else []
        return info

    @staticmethod
    def _read_summary(path: Path):
        """What the library shows of a working copy and has to open the file for: its book, size, edits and review progress
        (`{"error": …}` when the file can't be read)."""
        out = {}
        try:
            db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)  # as_uri() escapes odd characters in the path
            try:
                meta = {k: json.loads(v) for k, v in db.execute(
                    "SELECT key, value FROM meta WHERE key IN ('book_id','source_dir','text_path','created')")}
                out.update(book_id=meta.get("book_id"), source_dir=meta.get("source_dir"), text_path=meta.get("text_path"),
                           created=meta.get("created"))
                cols = [r[1] for r in db.execute("PRAGMA table_info(batches)")]
                not_review = " AND (kind IS NULL OR kind <> 'review')" if "kind" in cols else ""
                out["changes"] = db.execute(f"SELECT COUNT(*) FROM batches WHERE undone=0{not_review}").fetchone()[0]
                out["last_edit"] = db.execute("SELECT MAX(ts) FROM batches WHERE undone=0").fetchone()[0]
                out["sentences"], reviewed = db.execute(
                    "SELECT COUNT(*), TOTAL(reviewed) FROM tokens WHERE sent_start=1 OR ord=0").fetchone()
                out["reviewed"] = int(reviewed)
                out["tokens"] = db.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
            finally:
                db.close()
        except sqlite3.Error as e:
            out["error"] = str(e)
        return out

    def overview(self, open_name=None):
        """Everything the library page shows: books with their working copies and original text, other working copies, folders, problems."""
        found, problems = self.scan()
        projects = [self.project_info(p) for p in sorted(self.projects.glob("*.sqlite"))]
        by_source = {}
        for pr in projects:
            if pr.get("source_dir") and pr.get("book_id"):
                by_source.setdefault(f"{Path(pr['source_dir']).resolve()}::{pr['book_id']}", []).append(pr)
        books, used = [], set()
        for b in found:
            prs = by_source.get(b["key"], [])
            used.update(pr["name"] for pr in prs)
            b["projects"] = prs
            b["details"] = self.details_of(b["key"])
            saved = self.state["texts"].get(b["key"])
            b["text"] = saved if saved and Path(saved).is_file() else self.guess_text(b)
            books.append(b)
        others = [pr for pr in projects if pr["name"] not in used]
        tags = sorted({t for b in books for t in b["details"]["tags"]}, key=str.lower)
        return {"sources": self.state["sources"], "books": books, "other_projects": others, "problems": problems, "tags": tags,
                "open": open_name, "exports_dir": str(self.exports.resolve()), "exports_dir_custom": bool(self.state.get("exports_dir"))}

    def guess_text(self, b):
        """The original .txt is usually named after the book and sits near its output."""
        name = f"{b['book_id']}.txt"
        folder, src = Path(b["folder"]), Path(b["source"])
        for p in (folder, folder.parent, src, src.parent):
            for sub in ("", "input", "inputs", "texts", "text", "txt"):
                cand = (p / sub / name) if sub else (p / name)
                if cand.is_file():
                    return str(cand.resolve())
        try:
            for cand in sorted(src.parent.glob(f"*/{name}")):
                if cand.is_file():
                    return str(cand.resolve())
        except OSError:
            pass
        return None

    def _unique_name(self, base):
        """A working-copy name not used yet (adds -2, -3, … when needed)."""
        base = re.sub(r"[^\w .\-]", "_", base).strip() or "book"
        name, i = base, 2
        while (self.projects / f"{name}.sqlite").exists():
            name, i = f"{base}-{i}", i + 1
        return name

    def start(self, key, text_path, name=None):
        """Create a working copy of a book found in the folders (checks every token against the text) and return its path."""
        found, _ = self.scan()
        b = next((x for x in found if x["key"] == key), None)
        if not b:
            raise EditError("That book is no longer in your source folders")
        if not text_path:
            raise EditError("Choose the original text file first")
        text = Path(text_path).expanduser()
        if not text.is_file():
            raise EditError(f"Text file not found: {text}")
        if name and not NAME_RE.match(name.strip()):
            raise EditError("Use letters, numbers, spaces, dots, dashes or underscores for the name")
        name = self._unique_name((name or b["book_id"]).strip())
        path = self.projects / f"{name}.sqlite"
        try:
            create_project(path, Path(b["folder"]), text, b["book_id"])
        except SystemExit as e:  # create_project reports problems this way on the command line
            if path.exists():
                path.unlink()
            raise EditError(str(e))
        self.state["texts"][key] = str(text.resolve())
        self.save()
        return path

    def path_of(self, name):
        """The file of a working copy by name (EditError if it doesn't exist)."""
        path = self.projects / f"{name}.sqlite"
        if not NAME_RE.match(name) or not path.exists():
            raise EditError(f"No working copy called {name}")
        return path

    def rename(self, name, new):
        """Rename a working copy's file."""
        new = new.strip()
        if not NAME_RE.match(new):
            raise EditError("Use letters, numbers, spaces, dots, dashes or underscores")
        src = self.path_of(name)
        dst = self.projects / f"{new}.sqlite"
        if dst.exists():
            raise EditError(f"There's already a working copy called {new}")
        src.rename(dst)
        return dst

    def discard(self, name):
        """Move a working copy to projects/.discarded (never deleted, so it can be recovered by hand)."""
        path = self.path_of(name)
        trash = self.projects / ".discarded"
        trash.mkdir(exist_ok=True)
        shutil.move(str(path), str(trash / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.sqlite"))

    # ---- folder browser (a web page can't see file paths, so the server lists them)
    def browse(self, path=None, want="dir"):
        """List a folder's sub-folders (and .txt files when asked) for the page's folder picker; a web page can't see the disk itself."""
        p = Path(path).expanduser() if path else Path.home()
        if p.is_file():
            p = p.parent
        if not p.is_dir():
            raise EditError(f"Not a folder: {p}")
        p = p.resolve()
        dirs, files = [], []
        try:
            for c in sorted(p.iterdir(), key=lambda c: c.name.lower()):
                if c.name.startswith("."):
                    continue
                if c.is_dir():
                    dirs.append(c.name)
                elif want == "txt" and c.suffix.lower() == ".txt":
                    files.append(c.name)
        except PermissionError:
            raise EditError(f"No permission to read {p}")
        return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "dirs": dirs, "files": files,
                "books_here": len(list(p.glob("*.tokens"))), "home": str(Path.home())}
