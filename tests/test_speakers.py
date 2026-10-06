"""The nearest-mention finder gives the same answer as the plain rule it replaced (scan every mention of the group)."""
from __future__ import annotations

import random
import sqlite3
import unittest

from luna.core.speakers import MentionFinder


def brute_force(mentions, s, e):
    """The rule in plain form: the closest mention that doesn't overlap s..e; the lowest uid among equally close ones."""
    best = None
    for uid, a, b in sorted(mentions):
        if a <= e and b >= s:
            continue
        d = s - b if b < s else a - e
        if best is None or d < best[0]:
            best = (d, uid)
    return best[1] if best else None


class Nearest(unittest.TestCase):
    def setUp(self):
        """An in-memory book of 200 tokens (uid = position) with one table of random mentions."""
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("CREATE TABLE tokens(uid INTEGER PRIMARY KEY, ord INTEGER);"
                              "CREATE TABLE entities(uid INTEGER PRIMARY KEY, tok_start INTEGER, tok_end INTEGER, coref INTEGER, text TEXT);")
        self.db.executemany("INSERT INTO tokens VALUES (?,?)", [(i, i) for i in range(200)])

    def test_agrees_with_the_brute_force_rule_on_random_books(self):
        rnd = random.Random(7)
        for trial in range(40):
            self.db.execute("DELETE FROM entities")
            mentions = []
            for uid in range(rnd.randint(0, 30)):
                a = rnd.randrange(200)
                b = min(199, a + rnd.choice([0, 0, 0, 1, 2, 5]))
                mentions.append((uid, a, b))
                self.db.execute("INSERT INTO entities VALUES (?,?,?,?,?)", (uid, a, b, 1, f"m{uid}"))
            finder = MentionFinder(self.db)
            for _ in range(60):
                s = rnd.randrange(200)
                e = min(199, s + rnd.randint(0, 15))
                got = finder.nearest(1, s, e)
                self.assertEqual(got["uid"] if got else None, brute_force(mentions, s, e), (trial, s, e, mentions))

    def test_a_group_with_no_mentions_has_no_nearest(self):
        self.assertIsNone(MentionFinder(self.db).nearest(99, 10, 12))

    def test_groups_are_kept_apart(self):
        self.db.executemany("INSERT INTO entities VALUES (?,?,?,?,?)", [(1, 5, 5, 1, "a"), (2, 50, 50, 2, "b")])
        f = MentionFinder(self.db)
        self.assertEqual(f.nearest(1, 40, 42)["uid"], 1)
        self.assertEqual(f.nearest(2, 40, 42)["uid"], 2)


if __name__ == "__main__":
    unittest.main()
