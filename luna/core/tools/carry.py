"""Carrying corrections over between books of a collection (see collections_store.py).

Mixed into Store. The library sets `carry_colls` (the shared Collections) and `carry_key` (this book's
key in the library) when a book is opened; without them nothing is recorded or offered.

Recorded automatically:
  * a group of a character type (PER, and the types chosen for the exported .book) you mark as checked becomes (or
    updates) a character in the book's collection, with its profile for this
    book (core/tools/profile.py); it joins an existing character only when the evidence clearly says so
    (core/matching.py). `carry_update_profiles` records all checked groups and refreshes the linked groups' profiles;
  * a name whose every mention you've retyped by hand becomes a retyped name.
Offered for review (at the start of a new book, and any time from Entities):
  * groups matched to characters by evidence (core/matching.py): merge the groups that match one character, link them
    to it, and set its name, pronouns and type; weaker or unclear matches are offered as a choice;
  * retype mentions of retyped names;
  * rerun saved Find rules.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from luna.core.errors import EditError
from luna.core.matching import Matcher
from luna.core.tools.rule import pronoun_group_class

# A saved rule has to mean the same thing in every book, so it may only search by text, tag or type and set tags or
# types (group and speaker numbers differ from book to book).
SAFE_SEARCH_FIELDS = {"tokens": {"word", "lemma", "pos", "tag", "dep", "event"}, "entities": {"text", "cat", "prop"},
                      "supersenses": {"text", "cat"}}
SAFE_RULE_FIELDS = {"tokens": {"lemma", "pos", "tag", "dep", "event"}, "entities": {"cat", "prop"}, "supersenses": {"cat"}}


class CarryTools:
    """Collection carry-over mixed into Store. `carry_colls` / `carry_key` are set by the app when a book is opened (see Holder.relink)."""
    carry_colls = None
    carry_key = None

    # ------------------------------------------------------------ state
    def carry_state(self):
        """Is this book in a collection, which lists apply, how many groups are linked to characters, and were the suggestions offered yet."""
        with self.lock:
            if not self.carry_colls or not self.carry_key:
                return {"available": False}
            c = self.carry_colls.collection_of(self.carry_key)
            lists = [{"id": lid, "label": label, "counts": {k: len(lst[k]) for k in ("characters", "retypes", "rules")}}
                     for lid, label, lst in self.carry_colls.lists_for(self.carry_key)]
            linked = sum(1 for g in self.clusters().values() if g.get("carry"))
            return {"available": True, "collection": c, "lists": lists, "linked": linked,
                    "offered": bool(self.meta.get("carry_offered"))}

    def carry_mark_offered(self):
        """Remember that the collection's suggestions were shown for this book (they open automatically only once)."""
        self._set_meta("carry_offered", True)

    def _group_prop_names(self, cid):
        """A group's proper names with how often each occurs."""
        return dict(Counter(r["text"] for r in self._ents()[2].get(cid, []) if r["prop"] == "PROP"))

    # ------------------------------------------------------------ recording
    def _character_types(self):
        """The types a collection character can have: PER, and the other types you chose for the exported .book."""
        return {"PER", *self.book_types()}

    def _carry_candidates(self):
        """The groups worth comparing with the collection's characters, with their profiles: groups you linked, and of the
        character types (`_character_types`) every group with a proper name, people with at least 3 mentions, and groups you
        checked (never those marked "not a character")."""
        cl, prof, by, types = self.clusters(), self._profiles(), self._ents()[2], self._character_types()
        return {cid: prof[cid] for cid, c in cl.items() if not c["hidden"] and (c["carry"] or c["cat"] in types and (
            c["checked"] or (c["cat"] == "PER" and c["count"] >= 3) or any(r["prop"] == "PROP" for r in by.get(cid, ()))))}

    def _elsewhere(self, e):
        """A character entry as this book compares with it: its profiles from the other books only. Groups of this book share
        paragraphs, neighbours and places with the group that made the entry's profile here, which says how close they are
        in the text, not who they are; its names from every book still count."""
        me = self.meta.get("book_id")
        return {**e, "profiles": {b: p for b, p in (e.get("profiles") or {}).items() if b != me}}

    def _carry_matcher(self):
        """A Matcher of this book's candidate groups against the characters in its lists (`_elsewhere`), cached until an edit
        here or a change to the collections."""
        return self._memo("carry_match", self.carry_colls.version, lambda: Matcher(
            {eid: self._elsewhere(e) for eid, (_, _, e) in self.carry_colls.entries(self.carry_key).items()},
            self._carry_candidates(), self.carry_colls.matching_settings()))

    def carry_record_group(self, cid, matcher=None):
        """Called when a group is marked as checked. Returns the character entry id it's linked to, or None.

        Its entry is the one it's linked to, else the character it clearly matches by evidence (core/matching.py), else a
        new one. `matcher` lets the caller record many groups with one Matcher (see carry_update_profiles).
        """
        if not self.carry_colls or not self.carry_key:
            return None
        c = self.clusters().get(cid)
        if not c or c["hidden"] or c["cat"] not in self._character_types():
            return None  # only characters: PER and the types you chose for the exported .book
        names = self._group_prop_names(cid)
        if not names and not c["named"]:
            return None  # a group with no name (only pronouns or descriptions) isn't worth carrying over
        eid = c.get("carry") if c.get("carry") in self.carry_colls.entries(self.carry_key) else None
        m = matcher or self._carry_matcher()
        if eid is None:
            if cid not in m.groups:
                m.add_group(cid, self._profiles()[cid])
            ranked = m.rank(cid)
            if m.decide(ranked) == "match":
                eid = ranked[0]["entry"]
        manual_pron = self.db.execute("SELECT pronoun FROM groups WHERE uid=?", (cid,)).fetchone()
        data = {"name": c["name"], "name_manual": c["named"], "names": names or {c["name"]: 1},
                "pronoun": c["pronoun"], "pronoun_manual": bool(manual_pron and manual_pron[0]), "cat": c["cat"],
                "profile": self.character_profile(cid)}
        new = self.carry_colls.record_character(self.carry_key, self.meta["book_id"], eid, data, lookup=False)
        if new and new != eid and matcher is not None:
            matcher.add_entry(new, self._elsewhere(self.carry_colls.entries(self.carry_key)[new][2]))
        return new

    def _embedding_state(self):
        """Whether matching uses transformer speech, with which model, and what this book has: {use, model, book_model, speakers}."""
        cfg = self.carry_colls.matching_settings()["embedding"]
        emb = self.meta.get("speech_embeddings") or {}
        return {"use": cfg["use"], "model": cfg["model"], "book_model": emb.get("model"), "speakers": len(emb.get("groups") or {})}

    def carry_compute_embeddings(self):
        """Embed this book's speakers with the model chosen in the matching settings (core/embed.py), then refresh the
        linked characters' profiles so the collection gets the vectors too. Returns what was embedded and updated."""
        if not self.carry_colls or not self.carry_key:
            raise EditError("Open this book from the library to use its collection")
        model = self.carry_colls.matching_settings()["embedding"]["model"]
        try:
            out = self.compute_speech_embeddings(model)
        except Exception as ex:  # a model that isn't downloaded, or torch/transformers missing
            raise EditError(f"Couldn't use the model {model}: {ex}")
        return {**out, **self.carry_update_profiles()}

    def carry_update_profiles(self):
        """Record every checked group in the collection (linking it by evidence, or adding a new character) and refresh
        this book's profile of every character a group here is linked to. New links are one undoable step.

        Returns {"recorded": checked groups recorded, "linked": links made or repaired, "updated": profiles refreshed,
        "history"}.
        """
        with self.lock:
            if not self.carry_colls or not self.carry_key:
                raise EditError("Open this book from the library to use its collection")
            cl = self.clusters()
            m = self._carry_matcher()
            recorded, links = 0, {}
            for cid in sorted((g for g, c in cl.items() if c["checked"] and not c["hidden"]), key=lambda g: (-cl[g]["count"], g)):
                eid = self.carry_record_group(cid, matcher=m)
                if eid:
                    recorded += 1
                    if eid != cl[cid].get("carry"):
                        links[cid] = eid
            if links:
                name = (self.carry_colls.collection_of(self.carry_key) or {}).get("name", "All books")
                with self.batch(f"Linked {len(links)} checked group{'s' if len(links) != 1 else ''} to “{name}”", kind="review"):
                    for cid, eid in links.items():
                        self._group_upsert(cid, {"carry": eid})
            cl = self.clusters()
            linked = {}
            for cid, c in cl.items():
                if c.get("carry") and not c["hidden"]:
                    # if several groups are linked to one character, the biggest speaks for it
                    if c["carry"] not in linked or c["count"] > cl[linked[c["carry"]]]["count"]:
                        linked[c["carry"]] = cid
            profiles = {eid: self.character_profile(cid) for eid, cid in linked.items()}
            return {"recorded": recorded, "linked": len(links),
                    "updated": self.carry_colls.update_profiles(self.carry_key, self.meta["book_id"], profiles),
                    "history": self.history(1)}

    def _original_cats(self, uids):
        """The type each mention had before any hand edit, read from the history (None for mentions you added)."""
        out = {}
        uids = list(uids)
        for i in range(0, len(uids), 500):          # SQLite allows only so many ? in one query
            chunk = uids[i:i + 500]
            for r in self.db.execute(
                    "SELECT uid, before FROM changes WHERE id IN (SELECT MIN(id) FROM changes WHERE tbl='entities' AND uid IN "
                    f"({','.join('?' * len(chunk))}) GROUP BY uid)", chunk):
                b = json.loads(r["before"])
                out[r["uid"]] = b["cat"] if b else None
        return out

    def _carry_retypes_safe(self, uids):
        """Note retyped names for the collection after a bulk retype; a failure here must never block the edit."""
        try:
            self.carry_note_retypes(uids)
        except Exception:  # the collection file is a convenience
            pass

    def carry_note_retypes(self, uids):
        """After mentions were retyped: names whose every mention now has the same, new type are recorded."""
        if not self.carry_colls or not self.carry_key or not uids:
            return
        texts = set()
        for u in uids:
            r = self._row("entities", int(u))
            if r and r["prop"] != "PRON":
                texts.add(r["text"])
        for text in texts:
            rows = [dict(r) for r in self.db.execute("SELECT uid, cat, manual FROM entities WHERE text=? AND prop<>'PRON'", (text,))]
            cats = {r["cat"] for r in rows}
            if len(cats) != 1:
                continue
            cat = cats.pop()
            orig = self._original_cats([r["uid"] for r in rows])
            old = {v for v in orig.values() if v and v != cat}
            if not old or not any("cat" in json.loads(r["manual"] or "[]") for r in rows):
                continue
            self.carry_colls.record_retype(self.carry_key, self.meta["book_id"], text, cat, old)

    def carry_save_rule(self, lid, rule):
        """Save a Find search plus a change as a rule for a collection or all books; only tag/type rules that mean the same in every book are allowed."""
        if not self.carry_colls or not self.carry_key:
            raise EditError("Open this book from the library to save rules")
        layer, field, sfield = rule.get("layer"), rule.get("field"), rule.get("sfield")
        if field not in SAFE_RULE_FIELDS.get(layer, set()):
            raise EditError("Only changes to tags and types can be saved as rules (group and speaker numbers differ from book to book)")
        if sfield not in SAFE_SEARCH_FIELDS.get(layer, set()) or rule.get("op") not in ("eq", "contains", "regex"):
            raise EditError("Only searches by text, tag or type can be saved as rules")
        if not str(rule.get("value", "")).strip():
            raise EditError("The rule needs a value to set")
        lid = lid or (self.carry_colls.collection_of(self.carry_key) or {}).get("id")
        if not lid:
            raise EditError("Put this book in a collection first (in the library), or save the rule for all books")
        r = {k: rule.get(k) for k in ("layer", "sfield", "op", "svalue", "field", "value")}
        names = {"word": "word", "lemma": "lemma", "pos": "POS", "tag": "fine POS", "dep": "dependency", "event": "event",
                 "text": "text", "cat": "type", "prop": "mention type"}
        ops = {"eq": "is", "contains": "contains", "regex": "matches"}
        what = {"tokens": "Tokens", "entities": "Entities", "supersenses": "Supersenses"}[layer]
        r["label"] = rule.get("label") or (f"{what} whose {names.get(sfield, sfield)} {ops[rule['op']]} “{rule.get('svalue')}”: "
                                            f"set {names.get(field, field)} to {rule.get('value')}")
        return self.carry_colls.add_rule(lid, r, self.meta["book_id"])

    # ------------------------------------------------------------ review
    def carry_review(self):
        """What the collection would change in this book, for the review list (kept until an edit here or a change to the
        collections, since working it out matches every group with every character). Nothing is changed here; see
        `_carry_review`."""
        with self.lock:
            if not self.carry_colls or not self.carry_key:
                return {"available": False}
            return self._memo("carry_review", self.carry_colls.version, self._carry_review)

    def _carry_review(self):
        """Work out what the collection would change in this book.

        Characters: each group is compared with every character in the lists by evidence (core/matching.py: names, titles,
        neighbours, relations, speech, ...; the characters' profiles from the other books, see `_elsewhere`). A clear match is proposed with its evidence; several groups matching one
        character are proposed for merging into the biggest, and the group gets the character's name, pronouns and type.
        Groups that could be one or more characters (weaker evidence, no name, or two close candidates) are listed as a
        choice ("ambiguous"), best first, each option with its score and evidence.
        Also lists retyped names present in the book and saved rules with how many items each would change.
        """
        lists = self.carry_colls.lists_for(self.carry_key)
        cl = self.clusters()
        by_group = self._ents()[2]
        entries = self.carry_colls.entries(self.carry_key)
        m = self._carry_matcher()
        per_entry, ambiguous = defaultdict(list), []
        for cid, c in cl.items():
            if c["hidden"]:
                continue
            if c.get("carry") in entries:
                per_entry[c["carry"]].append((cid, None))
                continue
            if cid not in m.groups:
                continue
            ranked = m.rank(cid)
            kind = m.decide(ranked)
            if kind == "match":
                per_entry[ranked[0]["entry"]].append((cid, ranked[0]))
            elif kind == "possible":
                prof = self._profiles()[cid]
                ambiguous.append({"group": cid, "name": c["name"], "count": c["count"], "cat": c["cat"],
                                  "names": dict(Counter(prof["names"] or prof["nouns"]).most_common(4)),
                                  "options": [{**r, "name": entries[r["entry"]][2]["name"], "list": entries[r["entry"]][1]}
                                              for r in ranked]})
        ambiguous.sort(key=lambda a: (-a["options"][0]["score"], a["group"]))
        retyped = {e["text"] for _, _, lst in lists for e in lst["retypes"]}
        chars, done = [], 0
        for eid, found in per_entry.items():
            lid, label, e = entries[eid]
            evidence = {g: r for g, r in found if r}
            groups = sorted({g for g, _ in found}, key=lambda g: (-(cl[g].get("carry") == eid), -cl[g]["count"], g))
            target = groups[0]
            t = cl[target]
            sources = groups[1:]
            acts = {}
            if sources:
                acts["merge"] = [{"id": g, "name": cl[g]["name"], "count": cl[g]["count"],
                                  "score": evidence[g]["score"] if g in evidence else None, "evidence": evidence.get(g)}
                                 for g in sources]
            if not t["named"] and e["name"] != t["name"]:
                acts["name"] = {"from": t["name"], "to": e["name"]}
            manual_pron = self.db.execute("SELECT pronoun FROM groups WHERE uid=?", (target,)).fetchone()
            if e.get("pronoun") and not (manual_pron and manual_pron[0] is not None) and \
                    pronoun_group_class(e["pronoun"]) != pronoun_group_class(t["pronoun"]):
                acts["pronoun"] = {"from": t["pronoun"], "to": e["pronoun"]}
            if e.get("cat"):
                # retyped names decide their own type
                n = sum(1 for g in groups for r in by_group.get(g, []) if r["cat"] != e["cat"] and r["text"] not in retyped)
                if n:
                    acts["type"] = {"to": e["cat"], "n": n}
            if t.get("carry") != eid:
                acts["link"] = True
            if not acts:
                done += 1
                continue
            chars.append({"entry": eid, "list": label, "name": e["name"], "pronoun": e.get("pronoun"),
                          "cat": e.get("cat"), "books": e.get("books", []), "target": target,
                          "target_name": t["name"], "target_count": t["count"], "actions": acts,
                          "evidence": evidence.get(target),
                          "ctx": self._ctx(by_group[target][0], 8) if by_group.get(target) else None})
        chars.sort(key=lambda x: (-x["target_count"]))
        # retyped names
        retypes = []
        seen_text = set()
        for lid, label, lst in lists:
            for e in lst["retypes"]:
                if e["text"] in seen_text:
                    continue
                seen_text.add(e["text"])
                rows = self._ent_rows("WHERE x.text=? AND x.cat<>?", (e["text"], e["cat"]))
                if rows:
                    retypes.append({"entry": e["id"], "list": label, "text": e["text"], "cat": e["cat"],
                                    "n": len(rows), "from": sorted({r["cat"] for r in rows}),
                                    "ctx": self._ctx(rows[0], 8)})
        # saved Find rules
        rules = []
        for lid, label, lst in lists:
            for e in lst["rules"]:
                try:
                    n, sample = self._rule_count(e)
                except Exception as ex:  # a rule that no longer fits (e.g. a bad pattern) is shown, not applied
                    rules.append({"entry": e["id"], "list": label, "label": e["label"], "n": 0, "error": str(ex)})
                    continue
                if n:
                    rules.append({"entry": e["id"], "list": label, "label": e["label"], "n": n, "sample": sample})
        return {"available": True, "collection": self.carry_colls.collection_of(self.carry_key),
                "characters": chars, "ambiguous": ambiguous, "retypes": retypes, "rules": rules,
                "already": done, "entries": len(entries), "linked": sum(1 for c in cl.values() if c.get("carry")),
                "embeddings": self._embedding_state()}

    def _rule_targets(self, e):
        """The uids a saved rule would change: its search results whose field isn't already the new value."""
        base, params, so, _ = self._search_base(e["layer"], e["sfield"], e["op"], e["svalue"])
        uids = [r[0] for r in self.db.execute(f"SELECT x.uid {base} ORDER BY {so}", params)]
        field, value = e["field"], str(e["value"])
        return [u for u in uids if (self._row(e["layer"], u) or {}).get(field) != value]

    def _rule_count(self, e):
        """How many items a saved rule would change and a few examples with context."""
        targets = self._rule_targets(e)
        sample = []
        for u in targets[:3]:
            if e["layer"] == "tokens":
                o = self.ord_of(u)
                if o is not None:
                    sample.append(self._ctx({"s": o, "e": o}, 6))
            else:
                r = self._ent_rows("WHERE x.uid=?", (u,)) if e["layer"] == "entities" else []
                if r:
                    sample.append(self._ctx(r[0], 6))
        return len(targets), sample

    # ------------------------------------------------------------ apply
    def carry_apply(self, chars=(), assign=(), retypes=(), rules=()):
        """Apply the ticked parts of the review in one undoable step: merges, names, pronouns, types, links,
        retyped names and rules. Only what was ticked is applied; the summary says what happened.
        """
        with self.lock:
            if not self.carry_colls or not self.carry_key:
                raise EditError("Open this book from the library to use its collection")
            rev = self.carry_review()
            by_entry = {c["entry"]: c for c in rev["characters"]}
            entries = {}
            for _, label, lst in self.carry_colls.lists_for(self.carry_key):
                for kind in ("characters", "retypes", "rules"):
                    for e in lst[kind]:
                        entries.setdefault(e["id"], e)
            cl = self.clusters()
            parts = Counter()
            coll = rev["collection"]["name"] if rev.get("collection") else "All books"
            repoint = []
            with self.batch(f"Carried over from “{coll}”"):
                merged_into = {}
                for ch in chars:
                    c = by_entry.get(ch.get("entry"))
                    if not c:
                        continue
                    e = entries[c["entry"]]
                    target = c["target"]
                    want = set(ch.get("do") or [])
                    acts = c["actions"]
                    if "merge" in want and acts.get("merge"):
                        srcs = [g["id"] for g in acts["merge"] if g["id"] not in set(ch.get("skip_groups") or [])]
                        if srcs:
                            self._merge_core(target, srcs)
                            repoint.append((target, srcs))
                            parts["merged"] += len(srcs)
                            for s in srcs:
                                merged_into[s] = target
                    fields = {}
                    if "name" in want and acts.get("name"):
                        fields["name"] = e["name"]
                        parts["named"] += 1
                    if "pronoun" in want and acts.get("pronoun"):
                        fields["pronoun"] = e["pronoun"]
                        parts["pronouns"] += 1
                    if want:
                        fields["carry"] = e["id"]
                    if fields:
                        self._group_upsert(target, fields)
                    if "type" in want and acts.get("type"):
                        retyped = {x["text"] for _, _, lst in self.carry_colls.lists_for(self.carry_key) for x in lst["retypes"]}
                        for r in self.db.execute("SELECT uid, text FROM entities WHERE coref=? AND cat<>?", (target, e["cat"])).fetchall():
                            if r["text"] in retyped:
                                continue
                            self.update("entities", r["uid"], {"cat": e["cat"]})
                            parts["retyped"] += 1
                for a in assign:
                    g, eid = int(a["group"]), a["entry"]
                    if g not in cl or eid not in entries:
                        continue
                    e = entries[eid]
                    c = by_entry.get(eid)
                    tgt = c["target"] if c else next((x for x, y in ((gid, gg.get("carry")) for gid, gg in cl.items()) if y == eid), None)
                    tgt = merged_into.get(tgt, tgt)
                    if tgt is not None and tgt != g and tgt in self.clusters():
                        self._merge_core(tgt, [g])
                        repoint.append((tgt, [g]))
                        parts["merged"] += 1
                    else:
                        f = {"carry": eid}
                        if not cl[g]["named"]:
                            f["name"] = e["name"]
                        self._group_upsert(g, f)
                        parts["linked"] += 1
                for eid in retypes:
                    e = entries.get(eid)
                    if not e:
                        continue
                    for r in self.db.execute("SELECT uid FROM entities WHERE text=? AND cat<>?", (e["text"], e["cat"])).fetchall():
                        self.update("entities", r["uid"], {"cat": e["cat"]})
                        parts["retyped"] += 1
                for eid in rules:
                    e = entries.get(eid)
                    if not e:
                        continue
                    parts["by rules"] += self._bulk_core(e["layer"], self._rule_targets(e), e["field"], str(e["value"]))
                words = {"merged": "merged {} group{}", "named": "named {} group{}", "pronouns": "set pronouns on {} group{}",
                         "retyped": "retyped {} mention{}", "linked": "linked {} group{}", "by rules": "changed {} item{} by rules"}
                summary = ", ".join(words[k].format(v, "" if v == 1 else "s") for k, v in parts.items() if v)
                self.set_label(f"Carried over from “{coll}”: {summary or 'linked groups'}")
            for t, srcs in repoint:
                self._repoint_narrator(t, srcs)
            try:  # the groups just linked describe their characters in this book too
                self.carry_update_profiles()
            except Exception:  # the collection file is a convenience; never fail the applied edit
                pass
            return {"history": self.history(1), "summary": summary}
