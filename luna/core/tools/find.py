"""Find: search the book's layers, then change, create or delete what was found, all in one undoable step.

Mixed into Store. A search is (layer, field, op, value) plus two optional context filters:

  where    ''  | 'quote' | 'narration'                  hits that start inside a quote / outside every quote
  without  ''  | 'entities' | 'supersenses' | 'quotes'  hits that don't touch any span of that layer yet

Operators: eq, contains, regex, invalid (outside the tag list), edited (changed by hand) and, on tokens only,
phrase (the words of `value` in a row, compared against `field`; e.g. field=word, value="Baker Street").

Every hit is (uid, s, e, value): the row's uid, the token positions it spans, and the searched field's value.
The same search is used by the results list (`search`), by bulk changes (`bulk_edit`), by creating spans from
the results (`bulk_create`), by deleting them (`bulk_delete`) and by saved rules (`_search_base`, see carry.py).
"""
from __future__ import annotations

import re

from luna.core.errors import EditError
from luna.core.schema import SPAN_TABLES, TRACKED
from luna.core.speakers import MentionFinder
from luna.core.tagsets import TAGSETS

WHERE = ("", "quote", "narration")
NOUNS = {"tokens": ("token", "tokens"), "entities": ("entity", "entities"),
         "supersenses": ("supersense", "supersenses"), "quotes": ("quote", "quotes")}
GROUP_MODES = ("one", "text", "each")


def _plural(n, layer):
    """'1 entity' / '3 entities' for a layer name."""
    one, many = NOUNS[layer]
    return f"{n} {one if n == 1 else many}"


class FindTools:
    """Search and bulk actions on the results, mixed into Store (see the module header)."""
    # ------------------------------------------------------------ what can be searched and changed
    SEARCH_FIELDS = {
        "tokens": ["word", "lemma", "pos", "tag", "dep", "event"],
        "entities": ["text", "cat", "prop", "coref"],
        "supersenses": ["text", "cat"],
        "quotes": ["text", "char_id"],
    }
    # fields whose value can be set on all results at once (bulk_edit)
    BULK_FIELDS = {"tokens": ["lemma", "pos", "tag", "dep", "event"], "entities": ["cat", "prop", "coref"],
                   "supersenses": ["cat"], "quotes": ["char_id"]}
    CREATE_TARGETS = ("entities", "supersenses", "quotes")

    # ------------------------------------------------------------ searching
    def _search_base(self, layer, field, op, value):
        """The SQL for one search: (FROM/WHERE text, parameters, start-position column, end-position column)."""
        if layer not in self.SEARCH_FIELDS or field not in self.SEARCH_FIELDS[layer]:
            raise EditError("Can't search that field")
        params = []
        if op == "eq":
            cond = f"x.{field} = ?"
            params.append(int(value) if field in ("coref", "char_id") and str(value).lstrip("-").isdigit() else value)
        elif op == "contains":
            cond = f"CONTAINS_CI(x.{field}, ?)"
            params.append(str(value))
        elif op == "regex":
            try:
                re.compile(value)
            except re.error as e:
                raise EditError(f"Invalid pattern: {e}")
            cond = f"x.{field} REGEXP ?"
            params.append(value)
        elif op == "invalid":
            allowed = TAGSETS.get((layer, field))
            if not allowed:
                raise EditError("That field has no fixed tag list")
            cond = f"x.{field} NOT IN ({','.join('?' * len(allowed))})"
            params.extend(allowed)
        elif op == "edited":
            cond = "x.manual LIKE ?"
            params.append(f'%"{field}"%' if field in TRACKED[layer] else "%\"%")
        else:
            raise EditError("Unknown search operator")
        if layer == "tokens":
            return f"FROM tokens x WHERE {cond}", params, "x.ord", "x.ord"
        return (f"FROM {layer} x JOIN tokens s ON s.uid=x.tok_start JOIN tokens e ON e.uid=x.tok_end WHERE {cond}",
                params, "s.ord", "e.ord")

    def _phrase_hits(self, layer, field, value):
        """Runs of tokens whose `field` values equal the words of `value` (ignoring case). Hits never overlap."""
        if layer != "tokens":
            raise EditError("Phrase search works on tokens")
        if field not in self.SEARCH_FIELDS["tokens"]:
            raise EditError("Can't search that field")
        terms = [t.lower() for t in str(value).split()]
        if not terms:
            raise EditError("Type the words to look for")
        rows = self.db.execute(f"SELECT uid, ord, {field} FROM tokens ORDER BY ord").fetchall()
        shown = [str(r[2] or "") for r in rows]
        low = [v.lower() for v in shown]
        k, i, hits = len(terms), 0, []
        while i + k <= len(rows):
            if low[i] == terms[0] and low[i:i + k] == terms:
                hits.append((rows[i][0], rows[i][1], rows[i + k - 1][1], " ".join(shown[i:i + k])))
                i += k
            else:
                i += 1
        return hits

    def _cover_mask(self, tbl):
        """A byte per token position: 1 where some span of that layer covers the token (kept until the next edit)."""
        return self._memo(f"cover_{tbl}", None, lambda: self._build_cover(tbl))

    def _build_cover(self, tbl):
        """Mark every token position covered by at least one span of the layer."""
        mask = bytearray(self.n_tokens)
        for a, b in self.db.execute(f"SELECT s.ord, e.ord FROM {tbl} x JOIN tokens s ON s.uid=x.tok_start "
                                    f"JOIN tokens e ON e.uid=x.tok_end"):
            mask[a:b + 1] = b"\x01" * (b - a + 1)
        return mask

    def _matches(self, layer, field, op, value, where="", without=""):
        """Every hit of a search, in reading order: a list of (uid, s, e, value)."""
        if where not in WHERE or (without and without not in SPAN_TABLES):
            raise EditError("Unknown context filter")
        if op == "phrase":
            hits = self._phrase_hits(layer, field, value)
        else:
            base, params, so, eo = self._search_base(layer, field, op, value)
            hits = [tuple(r) for r in self.db.execute(
                f"SELECT x.uid, {so}, {eo}, x.{field} {base} ORDER BY {so}", params)]
        if where:  # decided by the token the hit starts on
            inside = self._cover_mask("quotes")
            hits = [h for h in hits if bool(inside[h[1]]) == (where == "quote")]
        if without:
            covered = self._cover_mask(without)
            hits = [h for h in hits if not any(covered[h[1]:h[2] + 1])]
        return hits

    def search(self, layer, field, op, value, offset=0, limit=100, where="", without=""):
        """One page of a search's results, each with the words around it."""
        with self.lock:
            hits = self._matches(layer, field, op, value, where, without)
            out = []
            for uid, s, e, val in hits[offset:offset + limit]:
                ca, cb = max(0, s - 8), min(self.n_tokens - 1, e + 8)
                out.append({"uid": uid, "s": s, "e": e, "value": val, "sent": self.sentence_of(s),
                            "context": self.words_between(ca, cb), "context_start": ca})
            return {"total": len(hits), "offset": offset, "hits": out}

    def _targets(self, layer, sfield, op, svalue, uids, where, without):
        """The hits a bulk action applies to: all of them, or only the ticked ones."""
        hits = self._matches(layer, sfield, op, svalue, where, without)
        if uids is not None:
            ticked = {int(x) for x in uids}
            hits = [h for h in hits if h[0] in ticked]
        if not hits:
            raise EditError("None of the selected results match any more")
        return hits

    # ------------------------------------------------------------ change a field on the results
    def bulk_edit(self, layer, sfield, op, svalue, uids, field, value, where="", without=""):
        """Set `field` to `value` on the results (all, or the ticked `uids`) in one step."""
        with self.lock:
            if field not in self.BULK_FIELDS.get(layer, []):
                raise EditError(f"Can't change {field} in bulk")
            targets = [h[0] for h in self._targets(layer, sfield, op, svalue, uids, where, without)]
            if field in ("coref", "char_id"):
                value = None if value in (None, "") else int(value)
                if field == "coref" and value is None:
                    raise EditError("Choose the group to move them to")
                shown = self.cluster_name(value) if value is not None else "none"
            else:
                value = str(value).strip()
                self._check_value(field, value)
                shown = value
            with self.batch(f"Set {field} to {shown} on {_plural(len(targets), layer)}"):
                changed = self._bulk_core(layer, targets, field, value)
                self.set_label(f"Set {field} to {shown} on {_plural(changed, layer)}")
            if layer == "entities" and field == "cat":
                self._carry_retypes_safe(targets)
            return {"history": self.history(1), "changed": changed}

    def _bulk_core(self, layer, targets, field, value):
        """Set the field on each row (inside a batch); returns how many rows actually changed."""
        changed = 0
        finder = MentionFinder(self.db) if layer == "quotes" else None
        for u in targets:
            before = self._row(layer, u)
            if before is None:
                continue
            if layer == "quotes":
                if before["char_id"] != value:
                    self._set_speaker(u, value, finder)
                    changed += 1
                continue
            if before[field] == value:
                continue
            self.update(layer, u, {field: value})
            changed += 1
            if layer == "entities" and field == "coref":
                self._sync_quotes_for_entity(before, {"coref": value})
        return changed

    # ------------------------------------------------------------ create spans over the results
    def bulk_create(self, layer, sfield, op, svalue, uids, target, fields, where="", without=""):
        """Create a `target` span (entities, supersenses or quotes) over every result, in one step.

        fields: entities need `cat` (and `prop`, default PROP) and a group: group_mode 'one' puts all of them in one
        group (`coref`, or a new group), 'text' makes one new group per distinct text (ignoring case), 'each' a new
        group per result. Supersenses need `cat`. Quotes may have a speaker `char_id`.
        Results that already have a span of that layer with the same bounds, or (quotes) that touch an existing quote,
        are skipped. Returns how many were created and skipped."""
        with self.lock:
            if target not in self.CREATE_TARGETS:
                raise EditError("Can't create that from results")
            hits = self._targets(layer, sfield, op, svalue, uids, where, without)
            cat = str(fields.get("cat") or "").strip()
            if target in ("entities", "supersenses"):
                if not cat:
                    raise EditError("Choose the type" if target == "entities" else "Choose the supersense category")
                self._check_value("cat", cat)
            prop = str(fields.get("prop") or "PROP").strip()
            mode = fields.get("group_mode") or "one"
            coref, speaker = fields.get("coref"), fields.get("char_id")
            coref = None if coref in (None, "") else int(coref)
            speaker = None if speaker in (None, "") else int(speaker)
            if target == "entities":
                self._check_value("prop", prop)
                if mode not in GROUP_MODES:
                    raise EditError("Unknown way of grouping")
                if coref is not None and coref not in self.clusters():
                    raise EditError(f"There is no group {coref}")
            if speaker is not None and speaker not in self.clusters():
                raise EditError(f"There is no group {speaker}")
            created = skipped = 0
            texts = set()
            with self.batch(f"Created {target}"):
                taken = self._taken_bounds(target)
                finder = MentionFinder(self.db) if target == "quotes" and speaker is not None else None
                shared, by_text = coref, {}
                quote_cover = bytearray(self._cover_mask("quotes")) if target == "quotes" else None
                for _, s, e, _ in hits:
                    if (s, e) in taken or (quote_cover is not None and any(quote_cover[s:e + 1])):
                        skipped += 1
                        continue
                    text = self._span_text(s, e)
                    row = {"tok_start": self.uid_of_ord(s), "tok_end": self.uid_of_ord(e), "text": text}
                    if target == "entities":
                        key = text.lower()
                        cid = shared if mode == "one" else by_text.get(key) if mode == "text" else None
                        if cid is None:
                            cid = self._new_coref()
                        row.update(cat=cat, prop=prop, coref=cid)
                        if mode == "one":
                            shared = cid
                        elif mode == "text":
                            by_text[key] = cid
                    elif target == "supersenses":
                        row.update(cat=cat)
                    else:
                        row.update(m_start=None, m_end=None, m_phrase=None, char_id=None)
                    new = self.insert(target, row)
                    if target == "quotes":
                        quote_cover[s:e + 1] = b"\x01" * (e - s + 1)
                        if speaker is not None:
                            self._set_speaker(new["uid"], speaker, finder)
                    taken.add((s, e))
                    texts.add(text.lower())
                    created += 1
                if not created:
                    raise EditError("Nothing was created: every result already has one there")
                what = {"entities": f"{cat} {prop} entit{'y' if created == 1 else 'ies'}",
                        "supersenses": f"{cat} supersense{'' if created == 1 else 's'}",
                        "quotes": f"quote{'' if created == 1 else 's'}"}[target]
                if target == "quotes" and speaker is not None:
                    what += f" spoken by {self.cluster_name(speaker)}"
                self.set_label(f"Created {created} {what}" + (f" “{next(iter(texts))}”" if len(texts) == 1 else ""))
            return {"history": self.history(1), "created": created, "skipped": skipped}

    def _taken_bounds(self, tbl):
        """The (start, end) positions of every span already in the layer."""
        return {(a, b) for a, b in self.db.execute(
            f"SELECT s.ord, e.ord FROM {tbl} x JOIN tokens s ON s.uid=x.tok_start JOIN tokens e ON e.uid=x.tok_end")}

    # ------------------------------------------------------------ delete the results
    def bulk_delete(self, layer, sfield, op, svalue, uids, where="", without=""):
        """Delete the results (entities, supersenses or quotes; all, or the ticked `uids`) in one step."""
        with self.lock:
            if layer not in SPAN_TABLES:
                raise EditError("Only entities, supersenses and quotes can be deleted")
            targets = [h[0] for h in self._targets(layer, sfield, op, svalue, uids, where, without)]
            with self.batch(f"Deleted {_plural(len(targets), layer)}"):
                if layer == "entities":
                    self._drop_entities([r for r in (self._row("entities", u) for u in targets) if r])
                else:
                    for u in targets:
                        self.delete(layer, u)
            return {"history": self.history(1), "deleted": len(targets)}
