"""Flags: a "check this later" mark, with an optional note, on a mention, a quote, a group or a sentence.

Mixed into Store. A flag is purely a personal working aid, like a review or checked mark: set_flag/clear_flag are
undoable (`kind="review"`, so ⌘Z works but it never reaches the exported change log). The `flags` table (added by a
migration in Store.__init__, see core/schema.py's docstring) is keyed by (kind, target): the row's own uid for a
mention, a quote or a group, or a sentence's first token's uid for a sentence — the same stable anchor `reviewed`
marks use. A flag on something later deleted (a mention, a merged-away group) just drops out of `flags_list`; the
row itself is left behind rather than hunted down through every path that can remove its target.
"""
from __future__ import annotations

import time

from luna.core.errors import EditError

KINDS = ("entities", "quotes", "groups", "sentence")
# A short label for each kind, for the flags list and the flag button's title.
KIND_LABEL = {"entities": "Mention", "quotes": "Quote", "groups": "Group", "sentence": "Sentence"}


class FlagTools:
    """Setting, clearing and listing flags; mixed into Store."""

    def _flag_row(self, kind, target):
        """The flag on one target, if any."""
        r = self.db.execute("SELECT * FROM flags WHERE kind=? AND target=?", (kind, int(target))).fetchone()
        return dict(r) if r else None

    def flag_of(self, kind, target):
        """The flag on one target (note and when it was set), or None; for the flag button's state."""
        with self.lock:
            return self._flag_row(kind, target)

    def set_flag(self, kind, target, note=""):
        """Flag a mention, quote, group or sentence, with an optional note; updates the note if it's already flagged.
        One undoable step, kept out of the exported change log."""
        if kind not in KINDS:
            raise EditError("Unknown kind of flag")
        target = int(target)
        with self.lock:
            existing = self._flag_row(kind, target)
            label = f"{'Updated the note on' if existing and existing['note'] != note else 'Flagged'} {KIND_LABEL[kind].lower()}"
            with self.batch(label, kind="review"):
                if existing:
                    self.update("flags", existing["uid"], {"note": note}, manual=False)
                else:
                    self.insert("flags", {"kind": kind, "target": target, "note": note, "created": time.time()}, manual=False)
            return self.history(1)

    def clear_flag(self, kind, target):
        """Remove a flag, if there is one. One undoable step, kept out of the exported change log."""
        with self.lock:
            existing = self._flag_row(kind, target)
            if not existing:
                raise EditError("That isn't flagged")
            with self.batch(f"Removed the flag on {KIND_LABEL.get(kind, kind).lower()}", kind="review"):
                self.delete("flags", existing["uid"])
            return self.history(1)

    def flags_list(self):
        """Every flag, oldest first, each with a short description and (where it still exists) a position to jump to.
        A flag whose target no longer exists (a deleted mention, a merged-away group) is left out."""
        with self.lock:
            out = []
            for r in self.db.execute("SELECT * FROM flags ORDER BY created"):
                r = dict(r)
                kind, target = r["kind"], r["target"]
                if kind in ("entities", "quotes"):
                    row = self._row(kind, target)
                    if not row:
                        continue
                    s, e = self.ord_of(row["tok_start"]), self.ord_of(row["tok_end"])
                    label = f"{KIND_LABEL[kind]} “{row['text']}”"
                elif kind == "groups":
                    c = self.clusters().get(target)
                    if not c:
                        continue
                    s = e = None
                    label = f"Group “{c['name']}”"
                elif kind == "sentence":
                    s = self.ord_of(target)
                    if s is None:
                        continue
                    sent = self.sentence_of(s)
                    s, e = self.sentence_bounds(sent)
                    label = f"Sentence {sent}"
                else:
                    continue
                out.append({**r, "label": label, "s": s, "e": e,
                            "text": self.text_between(s, e) if s is not None else None})
            return out
