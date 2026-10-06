"""Tools for correcting coreference groups quickly.

Mixed into Store: selecting mentions by scope (sentence, paragraph, quote,
conversation, passage, book), the quote rule for first-person pronouns,
per-quote first/second-person pronoun moves used when you reassign a
quote's speaker (`_quote_pronoun_moves`, from `Store.set_speakers`),
nearby groups for the picker, stepping through a group's mentions, hotkey
pins, scored merge suggestions and the fragment clean-up queue.
"""
from __future__ import annotations

import bisect
import difflib
import re
from collections import Counter, defaultdict

from luna.core import matching, suggest
from luna.core.names import name_tokens, title_gender
from luna.core.errors import EditError

# A conversation ends after this many tokens of narration without a quote (same as the analyser).
CONVERSATION_GAP = 100
NEAR_HALF = 20      # fragments: a group mentioned this many words away counts half as near as the next word
FIRST_PERSON = {"i", "me", "my", "mine", "myself"}
SECOND_PERSON = {"you", "your", "yours", "yourself"}
PRONOUN_SETS = {"he": {"he", "him", "his", "himself"}, "she": {"she", "her", "hers", "herself"},
                "they": {"they", "them", "their", "theirs", "themselves", "themself"}}
SCOPES = ("sentence", "paragraph", "quote", "conversation", "passage", "book")


def pronoun_class(text):
    """'he', 'she' or 'they' for a pronoun of that kind (him → 'he'), else None."""
    lw = text.lower()
    for k, v in PRONOUN_SETS.items():
        if lw in v:
            return k
    return None


class CorefTools:
    """Coreference tools mixed into Store. Everything here is derived from the entities, quotes and tokens tables
    and cached until the next edit (`_memo`); the only things stored are settings in `meta` (hotkeys, dismissed pairs).
    """
    # ------------------------------------------------------------ positions
    def _ents(self):
        """(mentions sorted by position, their start positions, mentions by group), cached until the next edit."""
        return self._memo("ents", None, self._build_ents)

    def _build_ents(self):
        """Mentions in reading order, their start positions (for bisect) and the mentions of each group."""
        rows = self._ent_rows()
        by_group = defaultdict(list)
        for r in rows:
            by_group[r["coref"]].append(r)
        return rows, [r["s"] for r in rows], by_group

    def _ents_in(self, lo, hi):
        """Mentions lying completely inside token positions lo..hi."""
        rows, starts, _ = self._ents()
        i = bisect.bisect_left(starts, lo)
        out = []
        while i < len(rows) and rows[i]["s"] <= hi:
            if rows[i]["e"] <= hi:
                out.append(rows[i])
            i += 1
        return out

    def _ent_rows(self, where="", params=()):
        """Entity rows with start/end positions, in reading order (optionally with a WHERE clause)."""
        return [dict(r) for r in self.db.execute(
            "SELECT x.uid, x.coref, x.prop, x.cat, x.text, x.tok_start, x.tok_end, s.ord AS s, e.ord AS e "
            "FROM entities x JOIN tokens s ON s.uid=x.tok_start JOIN tokens e ON e.uid=x.tok_end "
            f"{where} ORDER BY s.ord, e.ord", params)]

    def _quote_rows(self):
        """Quote rows with start/end positions, in reading order."""
        return [dict(r) for r in self.db.execute(
            "SELECT x.uid, x.char_id, x.m_start, x.m_end, x.text, s.ord AS s, e.ord AS e FROM quotes x "
            "JOIN tokens s ON s.uid=x.tok_start JOIN tokens e ON e.uid=x.tok_end ORDER BY s.ord")]

    def _para_bounds(self, ord_):
        """(first, last) token positions of the paragraph around a position."""
        s = self.sentence_of(ord_)
        p = self.sent_para[s]
        first = bisect.bisect_left(self.sent_para, p)
        last = bisect.bisect_right(self.sent_para, p) - 1
        return self.sentence_bounds(first)[0], self.sentence_bounds(last)[1]

    def _conversations(self, quotes=None):
        """Runs of quotes: a conversation ends after CONVERSATION_GAP tokens without a quote (as in the analyser)."""
        quotes = quotes if quotes is not None else self._quote_rows()
        convs, cur = [], []
        for q in quotes:
            if cur and q["s"] - cur[-1]["e"] - 1 > CONVERSATION_GAP:
                convs.append(cur)
                cur = []
            cur.append(q)
        if cur:
            convs.append(cur)
        return convs

    def scope_bounds(self, scope, ord_=None, a=None, b=None, quotes=None):
        """The token range (lo, hi) a scope covers around a position. Callers asking about many positions pass the book's
        `quotes` (from `_quote_rows`) so they are read once."""
        if scope not in SCOPES:
            raise EditError("Unknown scope")
        if scope == "book":
            return 0, self.n_tokens - 1
        if scope == "passage":
            if a is None or b is None:
                raise EditError("Select a passage first")
            lo, hi = sorted((int(a), int(b)))
            return max(0, lo), min(self.n_tokens - 1, hi)
        if ord_ is None:
            raise EditError("No position given for the scope")
        if scope == "sentence":
            return self.sentence_bounds(self.sentence_of(ord_))
        if scope == "paragraph":
            return self._para_bounds(ord_)
        quotes = quotes if quotes is not None else self._quote_rows()
        if scope == "quote":
            for q in quotes:
                if q["s"] <= ord_ <= q["e"]:
                    return q["s"], q["e"]
            raise EditError("This mention isn’t inside a quote")
        for conv in self._conversations(quotes):
            if conv[0]["s"] <= ord_ <= conv[-1]["e"]:
                return conv[0]["s"], conv[-1]["e"]
        raise EditError("This mention isn’t inside a conversation (a run of quotes)")

    # ------------------------------------------------------------ select by scope
    def select_scope(self, anchors, scope, groups="same", a=None, b=None):
        """Entity mentions in a scope around each anchor mention.

        groups: "same" (the anchor's group), "all", or a list of group ids.
        """
        with self.lock:
            anchors = [int(u) for u in (anchors or [])]
            if isinstance(groups, list):
                groups = [int(g) for g in groups]
            aset = set(anchors)
            anchor_rows = [r for r in self._ents()[0] if r["uid"] in aset] if anchors else []
            if anchors and not anchor_rows:
                raise EditError("Those mentions no longer exist")
            if scope in ("passage", "book"):
                lo, hi = self.scope_bounds(scope, a=a, b=b)
                want = (set(groups) if isinstance(groups, list)
                        else {r["coref"] for r in anchor_rows} if groups == "same" else None)
                if groups == "same" and not anchor_rows:
                    raise EditError("Choose which group to select")
                ranges = [(lo, hi, want)]
            else:
                ranges = []
                quotes = self._quote_rows() if scope in ("quote", "conversation") else None
                for r in anchor_rows:
                    lo, hi = self.scope_bounds(scope, r["s"], quotes=quotes)
                    want = ({r["coref"]} if groups == "same" else set(groups) if isinstance(groups, list) else None)
                    ranges.append((lo, hi, want))
            hits = {}
            for lo, hi, want in ranges:
                for r in self._ents_in(lo, hi):
                    if want is None or r["coref"] in want:
                        hits[r["uid"]] = r
            items = sorted(hits.values(), key=lambda r: (r["s"], r["e"]))
            spans = sorted({(lo, hi) for lo, hi, _ in ranges})
            return {"uids": [r["uid"] for r in items], "n": len(items), "spans": spans,
                    "forms": Counter(r["text"].lower() for r in items).most_common(),
                    "groups": Counter(r["coref"] for r in items).most_common()}

    # ------------------------------------------------------------ quote rule
    def quote_rule(self, scope="book", quote=None, a=None, b=None, apply=False, limit=400, only=None, exclude=None):
        """First-person pronouns (I, me, my, mine, myself) inside a quote belong to its speaker."""
        with self.lock:
            quotes = self._quote_rows()
            if scope == "book":
                chosen = quotes
            elif scope == "passage":
                lo, hi = self.scope_bounds("passage", a=a, b=b)
                chosen = [q for q in quotes if q["s"] <= hi and q["e"] >= lo]
            elif scope in ("quote", "conversation"):
                q0 = next((q for q in quotes if q["uid"] == int(quote)), None) if quote is not None else None
                if not q0:
                    raise EditError("Choose a quote first")
                if scope == "quote":
                    chosen = [q0]
                else:
                    conv = next(c for c in self._conversations(quotes) if any(x["uid"] == q0["uid"] for x in c))
                    chosen = conv
            else:
                raise EditError("Unknown scope for the quote rule")
            moves, no_speaker = [], 0
            ents, starts, _ = self._ents()
            for q in chosen:
                i = bisect.bisect_left(starts, q["s"])
                inside = []
                while i < len(ents) and ents[i]["s"] <= q["e"]:
                    e = ents[i]
                    if e["e"] <= q["e"] and e["text"].lower() in FIRST_PERSON:
                        inside.append(e)
                    i += 1
                if not inside:
                    continue
                if q["char_id"] is None:
                    no_speaker += 1
                    continue
                for e in inside:
                    if e["coref"] != q["char_id"]:
                        moves.append((e, q))
            if only is not None:
                keep = {int(u) for u in only}
                moves = [(e, q) for e, q in moves if e["uid"] in keep]
            if exclude:
                drop = {int(u) for u in exclude}
                moves = [(e, q) for e, q in moves if e["uid"] not in drop]
            if apply:
                if not moves:
                    raise EditError("Nothing to change")
                with self.batch(f"Quote rule: moved {len(moves)} first-person pronoun{'s' if len(moves) != 1 else ''} "
                                f"to their quotes’ speakers"):
                    for e, q in moves:
                        before = self._row("entities", e["uid"])
                        if before and before["coref"] != q["char_id"]:
                            self.update("entities", e["uid"], {"coref": q["char_id"]})
                            self._sync_quotes_for_entity(before, {"coref": q["char_id"]})
                return {"history": self.history(1), "moved": len(moves)}
            items = []
            for e, q in moves[:limit]:
                ca, cb = max(q["s"], e["s"] - 10), min(q["e"], e["e"] + 10)
                items.append({"uid": e["uid"], "s": e["s"], "e": e["e"], "text": e["text"], "from": e["coref"],
                              "from_name": self.cluster_name(e["coref"]), "to": q["char_id"],
                              "to_name": self.cluster_name(q["char_id"]), "quote": q["uid"],
                              "context": self.words_between(ca, cb), "context_start": ca})
            return {"n": len(moves), "items": items, "quotes": len(chosen), "no_speaker": no_speaker}

    def _other_side(self, conv, q0, exclude_speaker):
        """Who a quote's "you" probably addresses: the speaker of the nearest other quote in its conversation (the same
        run of quotes) that isn't `exclude_speaker`, preferring the quote just before over the one just after. None when
        the conversation has no other speaker to go on (a quote on its own, or every other quote has the same speaker)."""
        idx = next(i for i, x in enumerate(conv) if x["uid"] == q0["uid"])
        for offset in range(1, len(conv)):
            for j in (idx - offset, idx + offset):
                if 0 <= j < len(conv):
                    cand = conv[j]
                    if cand["char_id"] is not None and cand["char_id"] != exclude_speaker:
                        return cand["char_id"]
        return None

    def _quote_pronoun_moves(self, quote_uids):
        """For each given quote, first- and second-person pronouns inside it that don't yet match who they should refer
        to, using the quote's *current* (just-set) speaker: I/me/my/mine/myself belong to the speaker, you/your/yours/
        yourself to the other side of the conversation (see `_other_side`). Returns [(entity row, target group id)]."""
        quotes = self._quote_rows()
        by_uid = {q["uid"]: q for q in quotes}
        conv_of = {}
        for conv in self._conversations(quotes):
            for q in conv:
                conv_of[q["uid"]] = conv
        ents, starts, _ = self._ents()
        moves = []
        for uid in quote_uids:
            q = by_uid.get(int(uid))
            if not q or q["char_id"] is None:
                continue
            i = bisect.bisect_left(starts, q["s"])
            while i < len(ents) and ents[i]["s"] <= q["e"]:
                e = ents[i]
                i += 1
                if e["e"] > q["e"]:
                    continue
                lw = e["text"].lower()
                if lw in FIRST_PERSON:
                    target = q["char_id"]
                elif lw in SECOND_PERSON:
                    target = self._other_side(conv_of.get(q["uid"], [q]), q, q["char_id"])
                else:
                    continue
                if target is not None and e["coref"] != target:
                    moves.append((e, target))
        return moves

    # ------------------------------------------------------------ filtering the annotation view by entity
    def entity_context(self, gid, offset=0, limit=20, context=2):
        """A page of excerpts around every mention and quote (as speaker) of a group, each padded by `context`
        sentences either side; excerpts that touch or overlap merge into one, so nothing is shown twice. Each excerpt
        is a `window()` (same shape the Table/Annotation views page through), plus its own sentence range.
        """
        with self.lock:
            _, _, by_group = self._ents()
            spans = [(r["s"], r["e"]) for r in by_group.get(gid, [])]
            spans += [(q["s"], q["e"]) for q in self._quote_rows() if q["char_id"] == gid]
            if not spans:
                return {"total": 0, "offset": offset, "items": []}
            spans.sort()
            n_sent = len(self.sent_starts)
            blocks = []
            for s, e in spans:
                first, last = max(0, self.sentence_of(s) - context), min(n_sent - 1, self.sentence_of(e) + context)
                if blocks and first <= blocks[-1][1] + 1:
                    blocks[-1] = (blocks[-1][0], max(blocks[-1][1], last))
                else:
                    blocks.append((first, last))
            page = blocks[offset:offset + limit]
            items = [{**self.window(f, last - f + 1), "excerpt_first": f, "excerpt_last": last} for f, last in page]
            return {"total": len(blocks), "offset": offset, "items": items}

    # ------------------------------------------------------------ nearby groups and stepping
    def nearby_groups(self, a, b, window=250, limit=8, exclude=()):
        """Groups mentioned within `window` tokens of a..b, nearest first, for the group picker and candidates.
        
        Distance is weighted by mention type (a nearby name counts more than a nearby pronoun) and ignores the span itself.
        """
        with self.lock:
            a, b = int(a), int(b)
            ex = {int(x) for x in exclude}
            cl = self.clusters()
            rows = self._ents_in(max(0, a - window), min(self.n_tokens - 1, b + window))
            best = {}
            for r in rows:
                if r["coref"] in ex or r["coref"] not in cl:
                    continue
                if r["s"] <= b and r["e"] >= a:  # overlapping the target itself
                    continue
                d, side = (a - r["e"], "before") if r["e"] < a else (r["s"] - b, "after")
                # a name nearby is stronger evidence than a pronoun nearby
                w = d * (0.6 if r["prop"] == "PROP" else 1.0 if r["prop"] == "NOM" else 1.4)
                if r["coref"] not in best or w < best[r["coref"]]["w"]:
                    best[r["coref"]] = {"w": w, "d": d, "side": side}
            out = []
            for cid, v in sorted(best.items(), key=lambda kv: kv[1]["w"])[:limit]:
                c = cl[cid]
                out.append({"id": cid, "name": c["name"], "cat": c["cat"], "count": c["count"], "distance": v["d"],
                            "w": round(v["w"], 1), "side": v["side"], "hidden": c["hidden"]})
            return out

    def step_mention(self, cid, ord_, direction):
        """The next or previous mention of a group after a position, wrapping round; used by the ‹ › steppers."""
        with self.lock:
            rows = self._ents()[2].get(int(cid), [])
            if not rows:
                raise EditError("That group has no mentions")
            starts = [r["s"] for r in rows]
            if direction == "next":
                i = bisect.bisect_right(starts, int(ord_))
                if i >= len(rows):
                    i = 0
            else:
                i = bisect.bisect_left(starts, int(ord_)) - 1
                if i < 0:
                    i = len(rows) - 1
            r = rows[i]
            return {"uid": r["uid"], "s": r["s"], "e": r["e"], "index": i + 1, "total": len(rows)}

    # ------------------------------------------------------------ hotkeys
    def hotkeys(self):
        """The groups pinned to number keys 1-9 (only groups that still have mentions)."""
        with self.lock:
            cl = self.clusters()
            keys = {k: v for k, v in (self.meta.get("hotkeys") or {}).items() if v in cl}
            return {k: {"id": v, "name": cl[v]["name"], "cat": cl[v]["cat"]} for k, v in sorted(keys.items())}

    def set_hotkey(self, key, cid):
        """Pin a group to a number key 1-9, or free the key (`cid=None`). A group holds one key at a time."""
        with self.lock:
            key = str(key)
            if key not in list("123456789"):
                raise EditError("Keys 1 to 9 can be pinned")
            hk = {k: v for k, v in (self.meta.get("hotkeys") or {}).items() if v != cid}
            if cid is None:
                hk.pop(key, None)
            else:
                if int(cid) not in self.clusters():
                    raise EditError("That group has no mentions")
                hk[key] = int(cid)
            self._set_meta("hotkeys", hk)
            return self.hotkeys()

    # ------------------------------------------------------------ merge suggestions
    def _group_profiles(self):
        """What each group is made of, for comparing groups by name (cached until the next edit; see `_build_group_profiles`):
        ({group: parts}, mentions in reading order)."""
        return self._memo("group_profiles", None, self._build_group_profiles)

    def _build_group_profiles(self):
        """Each group's proper names (and their name tokens, first names, titles and the gender those imply), common nouns,
        pronoun classes, the sentences its names occur in, its first mention and first name, and the pronouns it should have
        (set by hand, else from its own pronouns when at least 3 in 4 agree).
        """
        cl = self.clusters()
        ents = self._ents()[0]
        prof = {}
        for r in ents:
            c = cl.get(r["coref"])
            if not c:
                continue
            p = prof.setdefault(r["coref"], {"props": Counter(), "noms": Counter(), "prons": Counter(), "titles": set(),
                                             "tokens": set(), "first_names": set(), "prop_sents": set(), "first": None,
                                             "first_prop": None, "n": 0})
            p["n"] += 1
            if p["first"] is None:
                p["first"] = r
            if r["prop"] == "PROP":
                p["props"][r["text"]] += 1
                titles, toks = name_tokens(r["text"])
                p["titles"] |= titles
                p["tokens"] |= {t for t in toks if len(t) >= 2 and t[0].isupper()}
                if len(toks) >= 2 and toks[0][0].isupper():
                    p["first_names"].add(toks[0])
                p["prop_sents"].add(self.sentence_of(r["s"]))
                if p["first_prop"] is None:
                    p["first_prop"] = r
            elif r["prop"] == "NOM":
                p["noms"][r["text"].lower()] += 1
            else:
                pc = pronoun_class(r["text"])
                if pc:
                    p["prons"][pc] += 1
        for cid, p in prof.items():
            g = cl[cid]["pronoun"]
            if g:
                p["pron"] = g.split("/")[0].lower()
            elif sum(p["prons"].values()) >= 2:
                k, n = p["prons"].most_common(1)[0]
                p["pron"] = k if n / sum(p["prons"].values()) >= 0.75 else None
            else:
                p["pron"] = None
            p["gender_title"] = title_gender(p["titles"])
        return prof, ents

    def _talks_with(self):
        """How often two speakers follow each other inside conversations, per pair of groups."""
        pairs = Counter()
        for conv in self._conversations():
            for x, y in zip(conv, conv[1:]):
                if x["char_id"] is not None and y["char_id"] is not None and x["char_id"] != y["char_id"]:
                    pairs[tuple(sorted((x["char_id"], y["char_id"])))] += 1
        return pairs

    def _ctx(self, r, n=10):
        """A mention with the n words around it, for showing evidence."""
        if not r:
            return None
        ca, cb = max(0, r["s"] - n), min(self.n_tokens - 1, r["e"] + n)
        return {"s": r["s"], "e": r["e"], "words": self.words_between(ca, cb), "start": ca}

    def merge_suggestions(self, limit=150, group=None):
        """Pairs of groups that may be one (scored, with reasons), optionally only those involving one group, plus bundles."""
        with self.lock:
            items = self._merge_items()
            if group is not None:
                mine = [p for p in items if group in (p["a"]["id"], p["b"]["id"])]
                return {"total": len(mine), "items": mine[:limit], "bundles": []}
            return {"total": len(items), "items": items[:limit], "bundles": self._bundles(items)}

    def _merge_items(self):
        """Every merge suggestion, best first (cached until an edit, a dismissal or a change of the settings)."""
        return self._memo("merge", (len(self.meta.get("dismissed_pairs", [])), self.settings_version()), self._merge_suggestions_all)

    def _bundles(self, items, min_size=2):
        """Sets of groups joined by "same name" with no evidence against: merge each set in one go."""
        parent = {}

        def find(x):
            """Union-find: the representative of x's set (with path compression)."""
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        info = {}
        for p in items:
            if all(r["w"] > 0 for r in p["reasons"]) and any(r["text"].startswith("Same name") for r in p["reasons"]):
                x, y = p["a"]["id"], p["b"]["id"]
                info[x], info[y] = (p["a"], p["a_ctx"]), (p["b"], p["b_ctx"])
                parent[find(x)] = find(y)
        comps = defaultdict(list)
        for x in info:
            comps[find(x)].append(x)
        out = []
        for members in comps.values():
            if len(members) < min_size + 1:
                continue
            members.sort(key=lambda c: (-info[c][0]["count"], c))
            keep, drops = members[0], members[1:]
            k = info[keep][0]
            out.append({"keep": keep, "name": k["name"], "cat": k["cat"], "count": k["count"],
                        "drops": [{"id": d, "name": info[d][0]["name"], "count": info[d][0]["count"], "ctx": info[d][1]}
                                  for d in drops],
                        "mentions": sum(info[d][0]["count"] for d in drops)})
        out.sort(key=lambda b: -len(b["drops"]))
        return out

    def _merge_suggestions_all(self):
        """Score candidate pairs of groups and keep those scoring at least "Listed from" (cached until an edit or a change
        of the settings). Every weight below is a default; the settings (core/suggest.py, type "merge") can change or
        switch off each one.

        1. Candidates come from names (same name +3, one name part of the other +2, shared word +1, near-identical spelling
           +1.5) and from lone common-noun groups (a comma apposition +3, a name just before +0.75).
        2. Evidence is added for candidates only: pronouns agree +0.5 / differ -3, titles Mr./Mrs. differ -3, different first
           names -2, named together in sentences up to -2, speaking to each other up to -3, and their character profiles
           alike (descriptions, relations, neighbours, places, actions, speech; similarity × weight).
        3. Pairs you dismissed ('not the same') are dropped; the bigger group is suggested to keep.
        """
        with self.lock:
            cl = self.clusters()
            cfg = self.suggest_cfg("merge")
            W = lambda k: suggest.weight(cfg, k)
            prof, ents = self._group_profiles()
            live = {cid for cid in prof if not cl[cid]["hidden"]}
            cands = defaultdict(list)  # (a, b) -> [(weight, reason)]

            def add(x, y, w, why):
                """Record one piece of evidence (weight, reason) that groups x and y may be the same."""
                if not w or x == y or x not in live or y not in live:
                    return
                cands[(min(x, y), max(x, y))].append((w, why))

            self._merge_by_names(add, W, cl, prof, live)
            self._merge_by_spelling(add, W, cl, prof, live)
            self._merge_by_nominals(add, W, cl, prof, live, ents)
            return self._merge_score(cands, W, cfg, cl, prof)

    def _merge_by_names(self, add, W, cl, prof, live):
        """Candidates from shared name words: the same name, one name part of the other, or just a word in common (and the
        evidence against: different first names). Words shared by many groups pair each group with the three biggest only."""
        index = defaultdict(set)
        for cid in live:
            for t in prof[cid]["tokens"]:
                index[(cl[cid]["cat"], t.lower())].add(cid)
        pairs = set()
        for ids in index.values():
            if len(ids) < 2:
                continue
            if len(ids) <= 12:
                ids = sorted(ids)
                pairs.update((ids[i], ids[j]) for i in range(len(ids)) for j in range(i + 1, len(ids)))
            else:
                # many groups share this name (often fragments of one character): pair each with the biggest ones
                for x in sorted(ids, key=lambda c: -cl[c]["count"])[:3]:
                    pairs.update((min(x, y), max(x, y)) for y in ids if y != x)
        for x, y in pairs:
            px, py = prof[x], prof[y]
            tx, ty = {t.lower() for t in px["tokens"]}, {t.lower() for t in py["tokens"]}
            shared = tx & ty
            nx = " ".join(name_tokens(px["props"].most_common(1)[0][0])[1]).lower() if px["props"] else ""
            ny = " ".join(name_tokens(py["props"].most_common(1)[0][0])[1]).lower() if py["props"] else ""
            if nx and nx == ny:
                add(x, y, W("same_name"), f"Same name: “{cl[x]['name']}” and “{cl[y]['name']}”")
            elif tx and ty and (tx <= ty or ty <= tx):
                small, big = (x, y) if len(tx) <= len(ty) else (y, x)
                add(x, y, W("name_part"), f"“{cl[small]['name']}” is part of “{cl[big]['name']}”")
            else:
                add(x, y, W("shared_word"), "Share the name “" + ", ".join(sorted(t.capitalize() for t in shared)) + "”")
            fx, fy = {f.lower() for f in px["first_names"]}, {f.lower() for f in py["first_names"]}
            if fx and fy and not fx & fy and shared:
                add(x, y, -W("first_names_differ"), f"Different first names ({', '.join(sorted(px['first_names']))} / "
                                                    f"{', '.join(sorted(py['first_names']))}), perhaps relatives")

    SPELLING_RATIO = 0.84       # how alike two name words must be (difflib's ratio) to count as spelled the same
    SPELLING_MAX_WORDS = 4000   # name words of one type and first letter compared with each other; beyond this none are

    def _merge_by_spelling(self, add, W, cl, prof, live):
        """Candidates from name words spelled almost the same ("Stapleton"/"Stapelton"). Words are compared within a type and first
        letter, each distinct pair once and only when their lengths are within two; a group's every word counts for it."""
        blocks = defaultdict(lambda: defaultdict(set))      # (type, first letter) -> name word -> groups that have it
        for cid in live:
            for t in prof[cid]["tokens"]:
                if len(t) >= 4:
                    blocks[(cl[cid]["cat"], t[:1].lower())][t.lower()].add(cid)
        sm = difflib.SequenceMatcher(None, autojunk=False)
        for words in blocks.values():
            if len(words) > self.SPELLING_MAX_WORDS:
                continue
            by_len = defaultdict(list)
            for w in sorted(words):
                by_len[len(w)].append(w)
            for w1 in sorted(words):
                sm.set_seq2(w1)                         # SequenceMatcher keeps what it learned about seq2 between calls
                for n in range(len(w1), len(w1) + 3):   # each pair once: the shorter word first, equal lengths in order
                    for w2 in by_len.get(n, ()):
                        if n == len(w1) and w2 <= w1:
                            continue
                        sm.set_seq1(w2)
                        if sm.real_quick_ratio() < self.SPELLING_RATIO or sm.quick_ratio() < self.SPELLING_RATIO \
                                or sm.ratio() < self.SPELLING_RATIO:
                            continue
                        for c1 in words[w1]:
                            for c2 in words[w2]:
                                add(c1, c2, W("spelling"),
                                    f"“{w1.capitalize()}” and “{w2.capitalize()}” are spelled almost the same")

    def _merge_by_nominals(self, add, W, cl, prof, live, ents):
        """Candidates from lone common-noun groups (at most 3 mentions, no name): one set off from a name by a comma
        ("Holmes, the detective") or a few words after a name of the same type."""
        first_noms = {}
        for r in ents:
            if r["prop"] == "NOM" and r["coref"] not in first_noms:
                first_noms[r["coref"]] = r
        for cid in live:
            p = prof[cid]
            if p["props"] or p["n"] > 3 or not p["noms"]:
                continue
            first_nom = first_noms.get(cid)
            if not first_nom:
                continue
            s = first_nom["s"]
            nearby = [r for r in self._ents_in(max(0, s - 40), min(self.n_tokens - 1, first_nom["e"] + 3)) if r["prop"] == "PROP"]
            appos = None
            for r in nearby:
                if r["coref"] == cid:
                    continue
                gap = self.words_between(r["e"] + 1, s - 1) if r["e"] < s else self.words_between(first_nom["e"] + 1, r["s"] - 1)
                if gap == [","]:
                    appos = r
                    break
            if appos:
                add(cid, appos["coref"], W("apposition"), f"“{first_nom['text']}” stands next to “{appos['text']}”, set off by a comma")
                continue
            before = [r for r in nearby if r["e"] < s and r["coref"] != cid and cl.get(r["coref"], {}).get("cat") == cl[cid]["cat"]]
            if before:
                r = before[-1]
                add(cid, r["coref"], W("name_before"), f"“{first_nom['text']}” comes {s - r['e'] - 1} words after “{r['text']}”")

    def _merge_score(self, cands, W, cfg, cl, prof):
        """Add the evidence for and against to every candidate pair (pronouns, titles, being named or speaking together, profiles
        alike), drop dismissed pairs and those scoring under "Listed from", and order them best first, the bigger group to keep."""
        dismissed = set(self.meta.get("dismissed_pairs", []))
        talks = self._talks_with()
        alike = self._merge_profile_evidence(cands, W)
        out = []
        for (x, y), reasons in cands.items():
            key = f"{x}-{y}"
            if key in dismissed:
                continue
            px, py = prof[x], prof[y]
            reasons = list(reasons)
            if px["pron"] and py["pron"]:
                if px["pron"] == py["pron"]:
                    reasons.append((W("pronouns_agree"), f"Both referred to as {px['pron']}"))
                else:
                    reasons.append((-W("pronouns_differ"), f"Referred to as {px['pron']} and as {py['pron']}"))
            if px["gender_title"] and py["gender_title"] and px["gender_title"] != py["gender_title"]:
                reasons.append((-W("titles_differ"), "Titles don’t match (" + ", ".join(sorted(t.capitalize() for t in px["titles"] | py["titles"])) + ")"))
            together = len(px["prop_sents"] & py["prop_sents"])
            if together >= 2:
                w = W("named_together")
                reasons.append((-min(w, w / 4 * together), f"Named together in {together} sentences"))
            t = talks.get((x, y), 0)
            if t:
                w = W("talk")
                reasons.append((-min(w, w / 2 + w / 12 * t), f"Speak to each other ({t} exchange{'s' if t != 1 else ''})"))
            reasons += alike.get((x, y), [])
            reasons = [(round(w, 2), why) for w, why in reasons if w]
            score = round(sum(w for w, _ in reasons), 2)
            if score < cfg["thresholds"]["min_score"]:
                continue
            big, small = (x, y) if (len(px["props"]), cl[x]["count"]) >= (len(py["props"]), cl[y]["count"]) else (y, x)
            out.append({"key": key, "score": score, "keep": big, "drop": small,
                        "a": cl[big], "b": cl[small],
                        "a_ctx": self._ctx(prof[big]["first_prop"] or prof[big]["first"]),
                        "b_ctx": self._ctx(prof[small]["first_prop"] or prof[small]["first"]),
                        "reasons": [{"w": w, "text": why} for w, why in sorted(reasons, key=lambda r: -r[0])]})
        out.sort(key=lambda p: (-p["score"], -(p["a"]["count"] + p["b"]["count"]), p["key"]))
        return out

    def _book_matcher(self):
        """One Matcher over every group of the book (not hidden), for comparing two groups' profiles: how rare each
        description, action or place is is measured on the whole book, so a generic "man" counts little. Cached until an
        edit or a change of the settings; transformer vectors are compared when this book has them."""
        def build():
            """Make the book-wide Matcher (see above)."""
            cl, prof = self.clusters(), self._profiles()
            cfg = matching.settings(self.carry_colls.data.get("settings", {}).get("matching") if self.carry_colls else None)
            emb = self.meta.get("speech_embeddings") or {}
            cfg["embedding"] = {"use": bool(emb), "model": emb.get("model") or cfg["embedding"]["model"]}
            return matching.Matcher({}, {c: p for c, p in prof.items() if not cl[c]["hidden"]}, cfg)
        return self._memo("book_matcher", self.settings_version(), build)

    def _merge_profile_evidence(self, pairs, W):
        """For pairs of groups, how alike their character profiles are (core/matching.py `Matcher.similarity`, over the
        whole book): {(a, b): [(similarity × weight, reason)]}, leaving out traces under 0.05. Speech compares only speakers
        with enough words, and transformer vectors only where computed for this book. `W` gives each kind's weight."""
        weights = {k[2:]: W(k) for k, *_ in suggest.PROFILE}          # "p_nouns" → "nouns", the kind Matcher reports
        if not pairs or not any(weights.values()):
            return {}
        labels = {k[2:]: label for k, label, *_ in suggest.PROFILE}
        m = self._book_matcher()
        out = {}
        for x, y in pairs:
            if x not in m.groups or y not in m.groups:
                continue
            ev = [(weights[k] * sim, f"{labels[k]} ({round(100 * sim)}%){': ' + why if why else ''}")
                  for k, sim, why in m.similarity(x, y) if weights.get(k)]
            ev = [(w, why) for w, why in ev if w >= 0.05]
            if ev:
                out[(x, y)] = ev
        return out

    # ------------------------------------------------------------ fragments
    def fragments(self, max_count=2, cat="", offset=0, limit=25):
        """The clean-up queue: groups with at most `max_count` mentions (unchecked, not hidden), in reading order.

        Each comes with its mentions in context and up to six candidate targets, best first. Candidates are the groups a merge
        suggestion pairs it with and the groups mentioned nearby; each gets a score from the evidence in the settings
        (core/suggest.py, type "fragments": merge suggestion, nearness, same type, an established group, pronouns, profiles
        alike), with its reasons, strongest first.
        """
        with self.lock:
            cl = self.clusters()
            frag = [c for c in cl.values() if c["count"] <= max_count and not c["hidden"] and not c.get("checked")
                    and (not cat or cat in c["cats"])]
            if not frag:
                return {"total": 0, "offset": 0, "items": []}
            by = self._ents()[2]
            first = {c["id"]: by[c["id"]][0]["s"] for c in frag if by.get(c["id"])}
            frag.sort(key=lambda c: first.get(c["id"], 0))
            page = frag[offset:offset + limit]
            cfg = self.suggest_cfg("fragments")
            W = lambda k: suggest.weight(cfg, k)
            window = int(cfg["thresholds"]["window"])
            pairs = defaultdict(dict)                       # fragment -> {other: (merge score, first reason)}
            for p in self._merge_items():
                for x, y in ((p["a"]["id"], p["b"]["id"]), (p["b"]["id"], p["a"]["id"])):
                    pairs[x][y] = (p["score"], p["reasons"][0]["text"])
            prons = {cid: p["pron"] for cid, p in self._group_profiles()[0].items()}
            near_of = {}
            for c in page:
                m0 = by[c["id"]][0]
                near_of[c["id"]] = {n["id"]: n for n in self.nearby_groups(m0["s"], m0["e"], window=window, limit=12, exclude=[c["id"]])}
            alike = self._merge_profile_evidence({(c["id"], o) for c in page for o in {*pairs[c["id"]], *near_of[c["id"]]}}, W)
            items = []
            for c in page:
                ms = by[c["id"]]
                cands = []
                for other in {*pairs[c["id"]], *near_of[c["id"]]}:
                    o = cl.get(other)
                    if not o or o["hidden"]:
                        continue
                    reasons = []
                    if other in pairs[c["id"]]:
                        score, why = pairs[c["id"]][other]
                        reasons.append((W("merge") * score, why))
                    n = near_of[c["id"]].get(other)
                    if n:
                        reasons.append((W("near") / (1 + n["w"] / NEAR_HALF), f"Mentioned {n['distance']} words {n['side']}"))
                    if o["cat"] == c["cat"]:
                        reasons.append((W("same_type"), f"Also {o['cat']}"))
                    if o["count"] > max_count:
                        reasons.append((W("bigger"), f"{o['count']} mentions"))
                    pa, pb = prons.get(c["id"]), prons.get(other)
                    if pa and pb:
                        reasons.append((W("pronouns_agree"), f"Both {pa}") if pa == pb else (-W("pronouns_differ"), f"{pa} vs {pb}"))
                    reasons += alike.get((c["id"], other), [])
                    reasons = sorted(((round(w, 2), why) for w, why in reasons if w), key=lambda r: -r[0])
                    cands.append({"id": other, "name": o["name"], "cat": o["cat"], "count": o["count"],
                                  "score": round(sum(w for w, _ in reasons), 2), "reason": " · ".join(why for _, why in reasons[:2]),
                                  "reasons": [{"w": w, "text": why} for w, why in reasons]})
                cands.sort(key=lambda x: (-x["score"], x["id"]))
                items.append({**c, "uids": [m["uid"] for m in ms], "mentions": [{"uid": m["uid"], "text": m["text"], "prop": m["prop"], "s": m["s"],
                                                 "e": m["e"], "sent": self.sentence_of(m["s"]),
                                                 "ctx": self._ctx(m, 18)} for m in ms[:3]],
                              "candidates": cands[:6]})
            return {"total": len(frag), "offset": offset, "items": items, "max_count": max_count}
