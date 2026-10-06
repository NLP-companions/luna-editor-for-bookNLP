"""Linking a quote to its speaker: which mention of the speaker's group stands closest to the quote.

Used by `Store._set_speaker` (core/store.py) and the bulk tools (core/tools/find.py); a module of its own so both can use
it without importing each other.
"""
from __future__ import annotations

import bisect


class MentionFinder:
    """The mention of a group nearest to a quote and outside it, for linking a speaker. A group's mentions are read once and
    then every lookup is a binary search, so giving thousands of quotes to a speaker with thousands of mentions doesn't
    scan the group once per quote. Make a new one per batch: it doesn't see edits made after it was built."""

    def __init__(self, db):
        """Start with no group read yet."""
        self.db = db
        self._groups = {}

    def _group(self, cid):
        """A group's mentions twice over, sorted by end (for the nearest one before a quote) and by start (after it)."""
        if cid not in self._groups:
            rows = self.db.execute(
                "SELECT x.uid, x.tok_start, x.tok_end, x.text, a.ord AS s, b.ord AS e FROM entities x "
                "JOIN tokens a ON a.uid=x.tok_start JOIN tokens b ON b.uid=x.tok_end WHERE x.coref=?", (cid,)).fetchall()
            by_end = sorted(rows, key=lambda r: (r["e"], -r["uid"]))       # the lowest uid is last among equal ends
            by_start = sorted(rows, key=lambda r: (r["s"], r["uid"]))
            self._groups[cid] = ([(r["e"], -r["uid"]) for r in by_end], by_end, [r["s"] for r in by_start], by_start)
        return self._groups[cid]

    def nearest(self, cid, s, e):
        """The mention of group `cid` closest to token positions s..e without overlapping them (the lower uid when two are
        equally close), or None."""
        end_keys, by_end, starts, by_start = self._group(cid)
        i = bisect.bisect_left(end_keys, (s, float("-inf")))                 # first mention ending at or after s
        before = by_end[i - 1] if i else None
        j = bisect.bisect_right(starts, e)                                   # first mention starting after e
        after = by_start[j] if j < len(by_start) else None
        if before is None or after is None:
            return before or after
        d_before, d_after = s - before["e"], after["s"] - e
        if d_before != d_after:
            return before if d_before < d_after else after
        return before if before["uid"] < after["uid"] else after
