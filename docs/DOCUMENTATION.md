# How the editor works: code and data

This is the technical companion to the [user guide](guide.md) (which is about *using* the editor; installing and starting it are in the [README](../README.md)). It explains how the program is put together and, above all, **where every piece of data comes from, how it is stored, processed, edited and passed on**.

Contents: [1 Folder map](#1-folder-map) · [2 The data at a glance](#2-the-data-at-a-glance) · [3 Where the data comes from](#3-where-the-data-comes-from-import) · [4 How it is stored](#4-how-it-is-stored) · [5 Data that is worked out, not stored](#5-data-that-is-worked-out-not-stored) · [6 How edits work](#6-how-edits-work) · [7 Settings and other stores](#7-settings-and-other-stores) · [8 Export and the analyser](#8-export-and-the-analyser) · [9 The web layer](#9-the-web-layer) · [10 Tests](#10-tests) · [11 Adding a feature](#11-adding-a-feature) · [12 Working at scale](#12-working-at-scale)

---

## 1 Folder map

```
luna-booknlp-editor/
  pyproject.toml         the package: name, version, Python version, dependencies (core and optional extras), the `luna` command
  README.md  LICENSE     what it is and how to install and start it; MIT licence
  THIRD_PARTY_NOTICES.md BookNLP's licence, for the code of its that luna/core/bookfile.py contains
  docs/guide.md          how to use it
  docs/DOCUMENTATION.md  this file
  examples/              a short public-domain book with its BookNLP output, to try Luna with (see examples/README.md)
  .github/workflows/     the tests, run on every push

  luna/                  the package (everything below, up to the tests, is inside it)
    app.py               web layer: FastAPI routes + command line (`luna`, `python -m luna`)
    localonly.py         middleware: answers only requests for this computer, and changes only from Luna's own page (§9)

    core/                  the engine; knows nothing about the web
      store.py             Store: one open working copy. Reading, editing, undo/redo,
                           groups, tokens/sentences, spaCy suggestions, Export
      schema.py            the SQLite tables; which fields count as "edited by hand"
      tagsets.py           BookNLP's tag lists (POS, dependency, entity types, supersenses)
      errors.py            EditError = a refused edit, shown to the user as a message
      speakers.py          the mention of a group nearest to a quote, found by binary search (linking many quotes to a speaker)
      library.py           which books and working copies exist (folders, projects/)
      collections_store.py collections and their lists (collections.json)
      profiles.py          character profiles: their layout, adding them up over books, a readable summary
      matching.py          matching a book's groups to collection characters by evidence (scores, settings)
      suggest.py           which evidence each kind of suggestion uses and its weights (settings, defaults)
      names.py             personal names: titles, name words, a name without titles, the gender titles imply
      embed.py             optional transformer speech embeddings (downloaded models only; torch loaded on use)
      bookfile.py          rebuilds BookNLP's .book and .book.html on Export
      cleanup_import.py    names plural groups / flags unsettled turns from the extra files of a pre-cleaned folder, if it has them
      tools/               feature areas, each a mixin class combined into Store
        coref.py             select by scope, quote rule (+ its per-quote pronoun moves), nearby groups, merge suggestions, fragments
        quote.py             speaker suggestions for quotes
        rule.py              narrator rule, checks for impossible links, pronoun pass, quote pass
        carry.py             carrying corrections between the books of a collection
        find.py              Find: search, phrase search, filters, bulk change / create / delete
        profile.py           a character profile of every group, read from the corrected book
        flag.py              flags ("check this later" + an optional note) on a mention, quote, group or sentence

    static/                the pages (plain HTML/JS/CSS, no build step)
      index.html           the editor page   library.html   the library page   settings.html   the Settings page
      js/                  app.js (core), bulk.js, entities.js, corefui.js, rules.js,
                           quotesug.js, passui.js (the passes' shared engine + the pronoun pass), qpassui.js (the quote pass) (editor); library.js + libfilter.js (library);
                           settings.js (settings); pageui.js ($, api, toast, modal, browse…,
                           shared by library.js and settings.js)
      css/                 app.css (all three pages), library.css (library and settings)

  tests/                 unittest suite (see §10); synthetic.py makes books of any size, scale.py times them (§12)

  luna-data/             YOUR DATA: git-ignored, never overwritten by an update; made in the folder you start Luna from
                         (or the folder given with --data / LUNA_DATA)
    projects/              one SQLite file per working copy (+ .discarded/)
    exports/               one folder per Export: exports/BOOK-YYYYMMDD-HHMMSS/
    collections.json       collections, their characters / retyped names / rules
    library.json           the folders to scan, and where each book's original .txt is
```

Rule of thumb: **`core/` decides, `app.py` transports, `static/` displays** (all inside `luna/`). All rules about what an edit does live in `core/`; the browser never changes data by itself, it always asks the server.

---

## 2 The data at a glance

```
 BookNLP run                           this editor                              analyser
 ───────────                           ───────────                              ────────
 BOOK.txt ──┐
            ├─► BookNLP ─► output/BOOK.tokens, .entities,   Library.start()
            │              .supersense, .quotes, .book,     create_project()
            │              .book.html                       (checks tokens against the .txt)
            │                       │                              │
            │                       └──────── read-only ───────────▼
            │                                          projects/NAME.sqlite   ← every edit, undo, redo,
            │                                          (the working copy)        suggestion, setting
            │                                                      │
            │                                          Store.export()          exports/BOOK-DATE/…
            │                                                      └────────────────► reads the newest export
            └─ the original text is only ever READ (offsets are checked against it)
```

* BookNLP's output folder and the `.txt` are **never modified**.
* The working copy (`projects/*.sqlite`) is the only thing edits change. It is a normal SQLite file: you can copy it as a backup while the editor is stopped.
* Export writes a new dated folder every time; earlier exports stay.

---

## 3 Where the data comes from (import)

`core/store.py → create_project()` reads one book's BookNLP output. BookNLP itself produced these files from the plain text: it tokenises and parses with spaCy (`en_core_web_sm`), then adds its own models for entities, coreference, supersenses, quote attribution and events. The editor treats all of it as *BookNLP's opinion, to be corrected*.

| BookNLP file | What is in it | Becomes (SQLite table) | Notes |
|---|---|---|---|
| `BOOK.tokens` | one row per token: word, lemma, character offsets, POS, fine POS, dependency relation, head, event, plus paragraph/sentence numbers | `tokens` | `token_ID_within_document` must run 0…n-1. Sentence and paragraph *starts* become flags (`sent_start`, `para_start`); heads become the head token's `uid`. Unknown extra columns are kept in `extra` and written back on export |
| `BOOK.entities` | mentions: start/end token, type (`cat`: PER, LOC, FAC, GPE, VEH, ORG), mention type (`prop`: PROP name, NOM noun, PRON pronoun), coreference group number (`COREF`), text | `entities` | The group number is BookNLP's coreference chain. There is no table of groups: a group *is* the set of entities sharing a `coref` number |
| `BOOK.supersense` | spans with a WordNet supersense (`noun.person`, `verb.motion`…) | `supersenses` | |
| `BOOK.quotes` | quote span, the speaker's group (`char_id`) and the mention that attributes it (`mention_start/end/phrase`) | `quotes` | The speaker mention is BookNLP's evidence for the speaker |
| `BOOK.book`, `BOOK.book.html`, any other `BOOK.*` | BookNLP's character summary and report | `meta.passthrough` (as text) | Kept so Export can hand them back; `.book` is also read for BookNLP's pronoun guess per group |
| the original `BOOK.txt` | the text BookNLP was run on | `meta.text` + `meta.text_path` | Used to check offsets (a wrong file is refused), to keep the exported text identical, and to recompute offsets after splits |

Checks during import: every token's character span must reproduce the token's word in the text (BookNLP replaces spaces/newlines/tabs inside tokens with `S`/`N`/`T`; `filter_ws` undoes that). More than max(20, 1%) mismatches means "wrong text file" and nothing is created. Any other trouble inside a BookNLP file (a missing column, a short row, a word where a number belongs) is reported as a message naming the book, and the half-made working copy is deleted again (`create_project` → `_import_book`, `_read_token_rows`).

`meta.join_style` records whether each layer's `text` column equals the tokens joined by spaces (true for normal BookNLP output). Export only rebuilds span text for layers where that holds.

**A pre-cleaned folder** (one where a separate step rewrote BookNLP's coreference and speaker attribution before you point the editor at it) needs no changes here: it is the same file formats, so it imports like any other BookNLP folder. Such a step may also write `ID.groups.tsv` (the plural entities it made for "we/us/our": id, name, member ids) and `ID.cleanup_log.tsv` (every change it made, plus `file="review"` rows for dialogue it couldn't settle) — these land in `meta.passthrough` like any other extra file. Right after import, `core/cleanup_import.py → apply_cleanup_metadata` reads them back out and, if present, names those plural groups (`Store.edit_group`) and flags the unsettled turns (`Store.set_flag`, one per `review` row, matched to a quote by token span), so a cleaned import starts with that work already done. Turning `ID.groups.tsv`'s member ids into an actual plural-group declaration is the analyser's job, not this editor's.

**How the library finds books** (`core/library.py`): every folder you add is scanned, plus the folders directly inside it, for `ID.tokens` + `ID.entities`. Folders with `ID.changes.md` are editor exports and are skipped. The `.txt` is guessed (`ID.txt` next to or near the output, in `input/`, `texts/` …) or chosen by you; the choice is remembered in `library.json → texts`.

---

## 4 How it is stored

One SQLite file per working copy. Schema: `core/schema.py`.

### 4.1 Content tables

| Table | Key columns | Meaning |
|---|---|---|
| `tokens` | `uid` (stable id), `ord` (position 0…n-1), `word`, `lemma`, `char_start/end`, `pos`, `tag` (fine POS), `dep`, `head` (a token uid), `event`, `sent_start`, `para_start`, `reviewed`, `manual`, `extra` | The text, token by token. `ord` is always the rank by `char_start`, so it is recomputed after a split or merge; **everything else refers to tokens by `uid`** so positions can change without breaking anything |
| `entities` | `uid`, `tok_start`, `tok_end`, `coref`, `prop`, `cat`, `text`, `manual` | Mentions. `coref` is the coreference group number |
| `supersenses` | `uid`, `tok_start`, `tok_end`, `cat`, `text`, `manual` | |
| `quotes` | `uid`, `tok_start`, `tok_end`, `char_id` (speaker group), `m_start`, `m_end`, `m_phrase` (speaker mention), `text`, `manual` | Quotes never overlap each other. When you set a speaker the editor links the nearest mention of that group *outside* the quote |
| `groups` | `uid` (= the coref number), `name`, `pronoun`, `hidden`, `checked`, `carry`, `manual` | **Only what you set** on a group: its name, pronouns, "not a character", "checked", and the link to a collection character. Created on first use. (Working copies used with an earlier version may have an unused `members` column; groups of several characters are declared in the analyser.) |
| `flags` | `uid`, `kind` (`entities`/`quotes`/`groups`/`sentence`), `target` (that row's uid, or a sentence's first token's uid), `note`, `created` | A "check this later" mark with an optional note, one per (`kind`, `target`); see `core/tools/flag.py`. Like `checked` and `reviewed`, it's a personal marker: undoable but never exported |

`manual` (on every row) is a JSON list of the fields you changed by hand, for the "tracked" fields in `schema.TRACKED`. It has two jobs: spaCy re-tagging never suggests changes to a manually edited field, and the table shows a blue dot for it. `flags` isn't tracked this way (`insert`/`update` are always called with `manual=False` for it): a flag isn't book content, so hand-editing has no meaning for it.

### 4.2 History

| Table | Meaning |
|---|---|
| `batches` | one row per undoable step: `label` (shown in History and the change log), `ts`, `undone`, `kind` (`review` for review ticks / checked marks, which are undoable but left out of the exported change log) |
| `changes` | the full **before** and **after** row (JSON) of every row a batch touched |

Undo replays a batch's `before` rows backwards, redo its `after` rows forwards, and flips the batch's `undone` mark in the same commit, so a replay that fails leaves the book as it was. History survives closing the program. Starting a new edit after an undo discards the undone steps (like any editor).

Besides the primary keys, `Store.__init__` makes sure these indexes exist (added to working copies made before them, at no cost to their data): `tokens(ord)`, `tokens(char_start)`, `tokens(head)`, `entities(coref)`, `entities(tok_start)`, `entities(tok_end)`, `quotes(tok_start)`, `quotes(char_id)`, `quotes(m_start, m_end)` and `changes(tbl, uid)`. The last four are what keep merging a group, moving mentions, linking speakers and handing out the next free id from scanning a whole table once per item.

### 4.3 Suggestions and confirmations (not part of undo)

| Table | Meaning |
|---|---|
| `proposals` | spaCy re-tagging suggestions waiting for you: token, field, old value, new value, and a signature of the sentence they were made for (they vanish if the sentence or the field changes) |
| `confirmed` | pronouns you kept with Enter in the pronoun pass (mention, group at that time). Not an edit: not in History, not exported |
| `confirmed_quotes` | quotes you kept with Enter in the quote pass (quote, speaker at that time). Not an edit: not in History, not exported |

### 4.4 `meta` (key → JSON)

Facts about the book and small settings that belong to *this* book:

| Key | Set by | Meaning |
|---|---|---|
| `book_id`, `source_dir`, `text_path`, `text`, `created`, `tokens_header`, `layers`, `join_style`, `passthrough`, `offset_mismatches`, `n_offset_mismatches` | import | where the book came from and what BookNLP wrote |
| `spacy_model` | `luna open --spacy-model` | model used for re-tagging |
| `narrator` | narrator rule | `{cid, passages:[{a, b, cid}]}`: the book's narrator group and passages with another narrator |
| `hotkeys` | number-key pins | `{"1": group id, …}` |
| `ppass` | pronoun pass | which pronouns to show, what to skip, how to advance |
| `qpass` | quote pass | what to skip, how to advance |
| `dismissed_pairs` | "Not the same" | merge suggestions you rejected |
| `dismissed_quote_sugs` | "Keep BookNLP's" | speaker suggestions you rejected |
| `check_ok` | "It's right" | checks you reviewed |
| `book_types` | Entities overview | entity types in the exported `.book` (default PER) |
| `carry_offered` | Collection tool | the collection's suggestions were shown once |
| `speech_embeddings` | "Compute speech embeddings" | `{model, groups: {group: {vec, quotes, sig}}}`: each speaker's transformer vector and a fingerprint of the quotes it was made from (a profile uses it only while the fingerprint matches). Not an edit, not exported |

---

## 5 Data that is worked out, not stored

Nothing below is saved; it is computed from the tables, cached in memory (`Store._memo`) and thrown away when an edit makes it stale. So it can never disagree with the data. Which edit drops what (`Store._after`):

* an ordinary step (groups, mentions, speakers, tokens…) drops everything, except that a step that leaves the tokens table alone keeps what is read from the parse alone (`TOKEN_MEMOS`: the token columns as lists, and who depends on whom), since rebuilding those reads the whole book;
* a step that only sets review marks (a sentence reviewed, a group checked) drops only the pronoun and quote pass lists and the collection matches (`REVIEW_MEMOS`);
* a flag changes nothing derived and drops nothing.

`tests/test_edits.py` pins this policy down and `tests/test_fuzz.py` checks after random edits that every cache equals a fresh computation, so a rule that keeps too much shows up there.

| What | Computed in | How |
|---|---|---|
| **Groups** (name, type, counts, pronouns) | `Store.clusters` | group entities by `coref`. Name = most common proper name (else noun, else pronoun) unless you renamed it. Type = most common `cat` (`cats` has all of them). Pronouns = yours, else BookNLP's from `.book` |
| **Merge suggestions** | `tools/coref.py` | candidate pairs from shared/nested names, spelling variants, lone nouns beside a name; scored with evidence for (pronouns agree, profiles alike: descriptions, relations, actions, speech) and against (titles Mr./Mrs., different first names, named together, speak to each other). Every weight and the listing threshold come from the settings (`core/suggest.py`, type `merge`) |
| **Fragments** | `tools/coref.py` | groups with ≤ N mentions, in reading order, with candidate targets (merge-suggestion partners and groups mentioned nearby), each scored from the settings (`core/suggest.py`, type `fragments`: merge score, nearness halving every 20 words, same type, established group, pronouns, profiles alike over a book-wide Matcher) |
| **Speaker suggestions** | `tools/quote.py` | for each quote: attribution tag (speech verb + its subject from the dependency parse), continuation in the same paragraph, turn-taking (return to the speaker two turns back), a name just before, "sounds like" (the quote's content words against the candidate's other quotes and its collection character's, tf-idf; `SoundsLike`); evidence against (addressed by name inside the quote, BookNLP's mention inside the quote or far away, the same candidate having spoken the untagged turn right before with nothing narrated in between). Weights, margin and minimum from the settings (`core/suggest.py`, type `speaker`). Listed when the best candidate differs from BookNLP's |
| **Checks** | `tools/rule.py` | pronoun class vs the group's expected pronoun class, reflexive/object-pronoun vs the verb's subject (dependency parse), apposition in two groups |
| **Pronoun pass list** | `tools/rule.py` | all pronouns of the chosen kinds minus those skipped by your settings |
| **`.book` content** | `Store._build_book` | BookNLP's own `get_syntax` run on the corrected tokens/entities/groups (§8) |
| **Character profiles** | `tools/profile.py` | per group: proper names, name words, titles, head nouns of common-noun mentions, pronoun classes and gender, relations from possessives ("his wife"), named characters and places in the same paragraphs, actions from the parse (agent, patient, possessions, descriptions) and stylometric counts of its quotes. Layout in `core/profiles.py`; `profile_summary(cid)` (`core/profiles.py` `summary`) reads as short lists, for `/api/group/{cid}/profile` and the Entities Compare pane |
| **Search results** | `tools/find.py` | SQL against the tables ("contains" is the SQL function `CONTAINS_CI`, registered by `Store`: it folds case the way Python does, so *évariste* finds *Évariste*, and `_`/`%` are ordinary characters); phrase search and the two context filters in Python over cached masks |
| **Collection review** | `tools/carry.py` | every group of the book matched against every character of its collection (`carry_review`, kept until an edit here or a change to the collections) |
| **Entity excerpts** | `tools/coref.py` `entity_context` | a page of `window()`s around a group's mentions and quotes, padded by the display setting's `context_sentences` and merged where they touch; the annotation view's entity-filter mode |

`nearby_groups` (used by every picker) ranks groups by distance to a position, weighting a name as closer than a noun and a noun as closer than a pronoun.

---

## 6 How edits work

### 6.1 The one rule: everything goes through a batch

```python
with store.batch("Set POS to NOUN on 12 tokens"):      # one undoable step
    store.update("tokens", uid, {"pos": "NOUN"})       # records before/after, marks the field as hand-edited
    store.insert("entities", {...})
    store.delete("supersenses", uid)
```

`batch` collects every `update/insert/delete`, writes the batch and its changes on success, rolls back on any error, and clears the caches. Feature code (`core/tools/*`) never writes SQL directly for edits; that is why every feature is undoable, shows up in History and lands in the change log.

### 6.2 Kinds of edit

| Edit | Function | What it changes |
|---|---|---|
| a token's field | `edit_token` | lemma, POS, fine POS, dependency, event, head |
| span fields / bounds | `edit_span`, `create_span`, `delete_span` | boundaries, type, mention type, group, speaker, speaker mention. Quotes attributed through an entity follow its group and bounds (`_sync_quotes_for_entity`) |
| groups | `edit_group`, `merge_groups`, `regroup`, `move_variant`, `edit_entities`, `delete_entities` | name, pronouns, hidden, checked; moving mentions between groups; merging |
| tokenisation | `split_token`, `merge_tokens` | changes the text units. Spans that ended on a split token now cover all its parts; boundaries inside merged tokens snap outward; character offsets are recomputed from the original text; then the sentence is re-tagged |
| sentences, paragraphs | `split_sentence`, `merge_sentence`, `set_paragraph` | flags on the first token; re-tagging follows |
| speakers | `set_speakers`, `_set_speaker` | a quote's speaker group + the nearest mention of that group outside the quote; `set_speakers(quote_rule=True)` also moves I/me/…/you/… inside each reassigned quote (`_quote_pronoun_moves`, `_other_side`, `core/tools/coref.py`) |
| review marks | `set_reviewed` | `tokens.reviewed` on a sentence's first token (`kind='review'`) |
| flags | `set_flag`, `clear_flag` | a row in `flags`, keyed by (kind, target); `kind='review'`, like checked and reviewed marks |
| Find, bulk | `bulk_edit`, `bulk_create`, `bulk_delete` | the same edits over all results in one step |
| rules and rules of thumb | quote rule, narrator rule, checks, pronoun pass, quote pass, collection carry-over | all end in `regroup`/`update` inside one batch |

### 6.3 spaCy suggestions

After a structural edit (split/merge/new sentence) `Store.retag` re-parses the affected sentences with spaCy *on the book's own tokens* and stores differences (lemma, POS, fine POS, dependency, head) in `proposals`. Nothing is applied until you accept it (`accept_proposals` is one undoable step). Fields in `manual` are never suggested. If spaCy or the model is missing, structural edits still work and you get no suggestions.

### 6.4 What "edited by hand" protects

`schema.TRACKED` lists which fields are tracked per table. `update(..., manual=True)` (the default) adds every changed tracked field to the row's `manual` list. Accepting a spaCy suggestion passes `manual=False`, so a later re-tag may improve it again.

---

## 7 Settings and other stores

| Where | What | Scope |
|---|---|---|
| `projects/NAME.sqlite` | everything in §4 | one working copy |
| `library.json` | (written atomically; a file that can't be read is renamed `library.unreadable-TIME.json` rather than overwritten) `sources` (folders to scan), `texts` (book key → original .txt path), `books` (book key → title, author, year, tags; empty fields are not stored), `exports_dir` (where Export writes to; absent means the default `exports/` next to the editor, see `Library.set_exports_dir`) | all books |
| `collections.json` | `collections`, `members` (book key → collection), `lists` (per collection and `*` = all books): `characters` (each with `profiles` per book), `retypes`, `rules`; `settings.matching` (weights, thresholds and switched-off kinds for matching groups to characters, see `core/matching.py`), `settings.suggestions` (the same for the other suggestions, see `core/suggest.py`) and `settings.display` (small display settings, e.g. `context_sentences`, see `core/collections_store.py`'s `DISPLAY_DEFAULTS`) — all three edited on the Settings page (`/api/library/suggestions`, `/api/library/display-settings`) and saved as the differences from the defaults (so improved defaults reach every setting left alone). Saved atomically, indented so it can be read and compared, except that each character's profile of a book sits on one line (profiles are most of the file in a big collection; `Collections._text`). The list dialog shows each character's profiles added up (`Collections.summaries`; the list itself is sent without the profiles, `get_list(profiles=False)`) and exports a list (`export_list`: CSV summary or JSON in full, `/api/library/list/{id}/export`) | all books |
| browser `localStorage` | `bne.view`, `bne.textView`, `bne.n` (sentences per page), `bne.cols`, `bne.layers`, `bne.gside`, `bne.lastLayer`, `bne.pos.<working copy>` (last position), `bne.lib` (library: collection + sort), `bne.theme` (Settings' Appearance toggle: `light` or `dark`; absent means Auto, follow the OS) | this browser only; purely conveniences, safe to clear |

A **book key** is `"<output folder>::<book id>"` (e.g. `/home/you/books/output/emma::emma`). It identifies a *BookNLP book*, not a working copy, so all working copies of a book share collection membership. (These keys contain absolute paths: if you move the output folder, add it again and re-assign collections.)

**Collections** (`core/collections_store.py`, `core/tools/carry.py`): a list fills itself as you work: marking a group of a character type (PER and the types chosen for the `.book`) *checked* adds a character (names + counts, pronouns, type, and its profile for this book in `profiles[book id]`; *Update profiles from this book* and applying the collection's suggestions refresh the profiles of all linked groups); giving *every* mention of a name a new type by hand adds a retyped name; "Save as rule" in Find adds a rule (search + change, tag/type fields only, so it means the same in every book). Opening another book of the collection *offers* these for review (`carry_review`); nothing is applied without `carry_apply`. Groups are matched to characters by evidence (`core/matching.py`), against the characters' names from every book but their profiles from the *other* books only (`CarryTools._elsewhere`): within one book, neighbours and places say how close two groups are in the text, not who they are.

---

## 8 Export and the analyser

`Store.export(dest)` (button *Export*, route `POST /api/export`) writes `exports/BOOK-YYYYMMDD-HHMMSS/`:

| File | Built from |
|---|---|
| `BOOK.tokens`, `.entities`, `.supersense`, `.quotes` | the current tables, in BookNLP's exact column layout (sentence/paragraph numbers recomputed from the flags, heads as positions, span text rebuilt from tokens where `join_style` allows) |
| `BOOK.book`, `BOOK.book.html` | rebuilt: `bookfile.load()` uses the installed BookNLP's `get_syntax` (or the bundled verbatim copy) on the corrected data. Extra entity types you chose are shown to it as PER; each character also gets `name`, `collection_id`, and `type` fields. Pronouns are BookNLP's evidence combined for merged groups, or yours |
| any other `BOOK.*` kept at import | copied unchanged |
| `BOOK.changes.md` | every batch still applied, oldest first (review marks left out) |
| `BOOK.validation.tsv` | every tagged value outside the tag lists (`tagsets.py`) |
| `BOOK.characters.tsv` | every group: id, name, type, pronouns, mentions, quotes, "not a character", collection character |
| `BOOK.txt` | a copy of the original text (`meta.text`), unchanged |

`Store.export` makes the folder and then calls `_write_export`, which does one step at a time (`_export_tokens`, `_export_layers`, `_export_book`, `_export_validation`, `_export_changes`, `_export_characters`). If any step fails the folder is removed again, because the analyser (aLex) takes the newest folder of a book for its export and must never find half of one.

With no edits, `.tokens`, `.entities`, `.supersense` and `.quotes` are identical to BookNLP's, and `.book` is too apart from the added `name` fields (a test checks this).

**The analyser** (aLex, a separate, optional project) never touches the editor's data files. You point it at the folder the exports are written to (`exports/` in your data folder, or the one set on the Settings page); it reads the newest dated export of each book. So the path `exports/BOOK-DATE/` and the file names above are an interface: renaming them breaks it.

---

## 9 The web layer

`luna/app.py` builds a FastAPI app around a `Holder` (the one open book + the library + the collections). Every book route starts with `cur()` (the open `Store`, or 409 "open one from the library") and converts `EditError` to a 400 with a readable message.

* **Local only** (`localonly.py`). The server listens on 127.0.0.1, but a web page open in your browser can still reach it. Two header checks close that: only the `Host` names `127.0.0.1` and `localhost` are answered (against DNS rebinding), and a request that changes something is refused when its `Origin` is not this very server. Requests without an `Origin` (scripts, `curl`) pass.
* **One book at a time.** The page sends `X-Book: <working copy>` with every call; if another book was opened meanwhile the server answers 409 and the page shows a banner instead of editing the wrong book.
* **Save status.** Every `/api/…` reply of an open book carries `X-Last-Saved` (the working copy file's last write time). Every edit is committed immediately, so that *is* the last save. The header badge shows it.
* **Route groups:** `/api/library*` (library, collections, suggestion/display settings, exports folder), `/api/fs` (folder picker) · `/api/info`, `/api/window`, `/api/token`, `/api/span`, `/api/token/…/split` … (reading and editing text) · `/api/groups`, `/api/group`, `/api/mentions/*` (coreference) · `/api/merge-suggestions`, `/api/fragments`, `/api/checks`, `/api/ppass*`, `/api/qpass*`, `/api/narrator*`, `/api/quote-*` (tools) · `/api/search`, `/api/bulk*` (Find) · `/api/carry*` (collections, character profiles) · `/api/flags`, `/api/flag` (flags) · `/api/history`, `/api/undo`, `/api/redo`, `/api/export`. `/api/library*` and `/api/fs` work with no book open (the library and Settings pages need that); every other route needs one.
* **Pages:** `/` the editor (redirects to `/library` when no book is open), `/library` the library, `/settings` the Settings page (`static/settings.html`) — reachable from the library's sidebar and, once a book is open, from the editor header's Settings tab.
* **Front end** (`static/js`): `app.js` holds the state object `S`, the API helper, table and annotation views, the side panel and Find; the other files add tabs and tools to it (`entities.js` the Entities tab and Back navigation, `rules.js` checks/narrator/collection panes, `corefui.js` selection/paint/number keys/groups sidebar/entity filter, `quotesug.js` speaker suggestions, `passui.js` the engine both passes share (`makePass`) with the pronoun pass, `qpassui.js` the quote pass, `bulk.js` bulk actions on Find results). All scripts share one global scope, so names are prefixed by area. Data flow is always: click → `api(...)` → server edits → `refresh()` redraws from the server's answer. A side panel's group/speaker field is always a coloured chip (`groupFieldChip`, `entities.js`) that opens the shared group picker (`groupPicker`); the picker normally has one button, or `actions` (a list of {key, label}) for more than one way to apply the same choice, as the Quote side panel and the Quotes view use for "Assign + Quote-Rule" (the default for a quick pick) / "Assign". The pronoun and quote passes are one piece of code with two descriptions: `makePass(spec)` in `passui.js` is the engine (start, stop, fetch an item, the bar, the settings popup, the keys) and each kind is a `spec` saying what differs (its API calls, what a "move" does, the sentence in the bar, what can be skipped); `PRONOUN_PASS` is in `passui.js`, `QUOTE_PASS` in `qpassui.js`, and every pass registers in `PASSES`, which the rest of the page goes through (`stopPasses`, `syncPassItems`). A spec's API calls are written out with literal URLs so that `tests/test_static.py` can still match them with the routes. The passes share one `#banner` bar and are mutually exclusive with each other and with paint mode; `renderMain`'s `syncPassBanner` hides that bar away from the annotation/table view (the pass itself stays paused, not stopped) and `refresh()` re-fetches each running pass's current item after any edit (`syncPassItems`), so a manual change elsewhere is reflected without leaving the pass. Each pass's settings also carry an `advance` ('auto', the original behaviour: Keep/a candidate calls the engine's `on`, which goes on with `go(ord=…&dir=next)`; or 'manual': they stay put via `go(uid=…)` instead, and the bar's Skip button is relabelled Next — the same `→`/`data-[q]p="next"` action either way, since advancing was already independent of deciding) — set in the settings popup below Skip, independent of the preset and carried over when it changes (`set_pass_settings`/`set_qpass_settings`, `core/tools/rule.py`). Entities' Compare pane (`comparePaneHTML`, `entities.js`) holds two groups picked from the list (`S.ch.cmpA`/`cmpB`) and shows their profiles side by side (`profileHTML`, the same layout as the library's "Full profile"), with a menu to merge them (into either one, or into a third — existing or brand new, via `groupPicker`'s `allowNew`).
* **Entity filter** (`S.ef`, `corefui.js`): picking a group combines its page of `/api/entity-context/{gid}` excerpts into one synthetic `S.win` (same shape `loadWindow` builds), so `renderAnnot` draws it — and everything in it edits — unchanged; a divider marks where two excerpts aren't adjacent sentences. The left-hand picker (`efListHTML`) is the same `/api/groups` list the Entities tab uses.
* **Flags** (`S.flags`, `app.js`): every flag is fetched once (`loadFlags`, alongside `loadClusters` in `init`) into a `Map` keyed `"kind:target"`, so a flag button (`flagButton`) can show its state without a call per target; the Flags tab (`renderFlagsView`) reads `/api/flags` fresh instead. `openFlagPopup` (the shared `pop` element) adds, edits or removes one; any change refetches the map and redraws whatever view is open.
* **Library page** (`library.js`, `libfilter.js`): search box, collection sidebar and sort are pure browser-side filtering (`LibFilter`, unit-tested under node). The server reads each working copy's summary (changes, review progress, token count) only when the file has changed since it last did (`Library.project_info`, keyed by the file's modified time and size), so the page stays quick with dozens of long books.
* **Settings page** (`settings.js`): folders with BookNLP output, where exports are written, display settings, an Appearance toggle (Auto/Light/Dark) and suggestion settings (collection matching, merge, fragments, speaker) — everything that applies to every book, in one place. Shares `pageui.js` ($, `api`, `toast`, the modal, the folder/file browser) with `library.js`, since both are separate pages from the editor. A table of contents (`#settingsToc`, `.setwrap`/`.stoc` in `library.css`) sits beside the scrolling `.lib` column; only `.lib` scrolls, so the table of contents stays in view and highlights the section nearest the top as you scroll (`trackToc`).
* **Theme** (`theme.js`, `app.css`): every page (`index.html`, `library.html`, `settings.html`) loads `theme.js` first, in `<head>`, before its stylesheet — it reads `bne.theme` from `localStorage` and sets `<html data-theme>` before the page paints, so there's no flash of the wrong theme. `app.css`'s colours are CSS variables in `:root` (light) with a dark set under `@media (prefers-color-scheme:dark)`; `data-theme="light"`/`"dark"` (set by the Settings page's Appearance toggle) override that default in either direction. No `data-theme` (Auto, the default) just follows the OS.

---

## 10 Tests

`python -m unittest discover -s tests -t .` (about 20 s; uses Python's `unittest`, and needs only `pip install -e ".[dev]"`: FastAPI's test client wants httpx. Tests that need an optional part (spaCy, torch, node, the working copies in `projects/`) skip themselves when it is missing; CI runs the suite on Python 3.10 and 3.13). `BNE_SCALE=1` adds the 190,000-token book (+15 s); `BNE_REAL_BOOKS=all` runs the real-book checks on every working copy. `tests/synthetic.py` writes BookNLP output of any size (`python -m tests.synthetic OUT SENTENCES`) and `python -m tests.scale book|library` times the editor on it (§12).

| File | Covers |
|---|---|
| `fixture.py` | a tiny hand-annotated book in BookNLP's file formats + the `BookCase` base class |
| `test_edits.py` | tokens, spans, quotes, history/undo/redo, search & bulk basics, tokenisation, suggestions |
| `test_find.py` | phrase search, context filters, batch create/delete of entities, supersenses and quotes |
| `test_groups.py` | groups, merging, moving mentions, scopes, suggestions, fragments, hotkeys, the quote rule and its per-quote pronoun moves on speaker reassignment, entity-filter excerpts |
| `test_embed.py` | transformer speech embeddings: computing, dropping stale ones, combining, matching, settings (a real small BERT when downloaded) |
| `test_imports.py` | every core module imports on its own (no circular imports) |
| `test_matching.py` | matching groups to collection characters by evidence; recording checked groups with it |
| `test_rules.py` | narrator rule, checks, pronoun pass, quote pass |
| `test_carry.py` | collections, carry-over, saved rules |
| `test_profiles.py` | character profiles read from a book, adding them up, keeping them in the collection |
| `test_speakers.py` | the nearest-mention lookup (`core/speakers.py`) against the plain rule, on random books |
| `test_degenerate.py` | books with no mentions or quotes, or a single sentence: every view and tool answers |
| `test_scale.py` | a synthetic book of 3,000 sentences always (big edits undo exactly, speaker links are the nearest mentions, export re-imports); with `BNE_SCALE=1` one of 20,000 sentences with time limits on what used to be slow |
| `test_flags.py` | flags on a mention, quote, group or sentence: set/update/clear, every kind, undo, orphans dropping out of the list |
| `test_cleanup_import.py` | parsing a cleaned folder's `.groups.tsv`/`.cleanup_log.tsv`, naming plural groups and flagging unsettled quotes from them, both directly and through `create_project` |
| `test_export.py` | export files, `.book` rebuild, no-edit export equals BookNLP's, re-import |
| `test_fuzz.py` | random edit sequences keep every invariant (spans line up with the text) |
| `test_app.py` | the web API end to end, library, collections, pages served; the local-only guard; the `--data` folder |
| `test_example.py` | the example book in `examples/` imports (its offsets match its text) and exports |
| `test_static.py` | every script parses; every `api('METHOD', '/api/…')` call matches a route and every route has a caller |
| `test_library_view.py` | library search/sort/collection filter (runs `libfilter.js` under node) |
| `test_real_books.py` | runs on **copies** of your own working copies in `projects/` (originals never opened for writing; skipped when there are none); `BNE_REAL_BOOKS=all` uses all of them |

---

## 11 Adding a feature

1. **Data first.** Decide whether it is *derived* (compute it from the tables in a `core/tools` mixin; cache with `_memo`) or *stored* (a `meta` key for a small setting; a table only if it is real content).
2. **Edits go through `batch`** with a human label; that gives undo, History and the change log for free.
3. **Route** in `luna/app.py`: a thin function calling `cur().your_method(...)`, with a one-line docstring.
4. **Front end**: call it with a literal `api('METHOD', '/api/…')` (the contract test looks for literal URLs), redraw with `refresh()`.
5. **Tests**: store-level in `tests/test_*.py` using `BookCase`; one API-level test in `test_app.py`.
6. **Docs**: a docstring on every function (what and how), a line in this file if it adds data, and `guide.md` if a user should know.

---

## 12 Working at scale

The editor works on one book at a time, but a corpus is many books: a long novel is 150,000–250,000 tokens (a few thousand groups, ten thousand quotes), and a series in one collection can reach dozens of books and a thousand characters. `tests/synthetic.py` writes BookNLP output of any size with a realistic shape (a few large groups and a long tail of small ones, tagged and untagged quotes, names, pronouns and descriptions), `tests/scale.py` times the editor on it and `tests/test_scale.py` guards the results. All numbers below are from one laptop and say what order to expect.

**One long book** (`python -m tests.scale book 20000 3000`: 188,000 tokens, 34,000 mentions, 9,000 quotes, 2,300 groups):

| | time |
|---|---|
| import (`create_project`), open, `info` | 1.6 s, 0.2 s, 0.2 s |
| a page of sentences, the groups list, a group's page, the quotes list | 0.01 s each |
| a search with 100,000 hits (one page of it), a phrase search | 0.06 s, 0.2 s |
| merge suggestions (first time after an edit; 3,000 groups), speaker suggestions | 1.5 s, 0.5 s |
| give 3,000 quotes to a speaker (with the quote rule) | 0.14 s (0.3 s); was 21 s (13 s) |
| move 3,000 mentions, merge 50 groups | 0.1 s, 0.4 s |
| change every noun's POS in bulk, make an entity of every *said* | 0.9 s, 0.2 s; the second was 14 s |
| undo, redo, mark 5,000 sentences reviewed | 0.05 s, 0.07 s, 0.2 s |
| export (including the rebuilt `.book`) | 2.7 s |

**A collection over many books** (`python -m tests.scale library 45`, 4,000 sentences per book, 30 characters marked checked per book): marking a group as checked, which records it in the collection, takes 0.35 s with 988 characters in the collection (0.14 s with 29); opening a book to review what the collection offers takes 2 s (0.1 s); `collections.json` is 3 MB. Both grow in step with the number of characters, not faster.

What makes this hold, and what to keep in mind when changing code:

* **Never scan a table per item.** The slow cases were all one query per quote, mention or new row: finding the nearest mention of the speaker's group for each of 3,000 quotes (now `core/speakers.py`: the group is read once, then a binary search per quote), and choosing the next free id for each of thousands of new rows (now the `changes(tbl, uid)` index). Loops that touch many rows read what they need once before the loop.
* **Derived values are cached, and the cache policy is deliberately fine-grained** (§5): rebuilding the token arrays or the profiles of a 190,000-token book takes a second, so a step that cannot change them must not drop them. Marking a sentence reviewed or a group checked, and flagging, are the most frequent steps and leave the expensive caches alone.
* **Only ask for what is shown.** The Entities view asks for the collection review (every group against every character) only where it is displayed; the `.book` preview of a group builds the character data, not the HTML report; the library list is sent without character profiles and the list dialog draws the first 150 characters that match its filter ("Show all" for the rest); the type-ahead list of groups holds the 1,000 biggest.
* **Write once, write cheaply.** `collections.json` is saved after every change, so its writer keeps profiles on one line (ten times faster than indenting everything); a working copy's summary on the library page is read again only when the file changed.
* **Quadratic by nature, and bounded:** comparing every pair of spelling variants of a name within a type and first letter (merge suggestions) runs only over distinct words of similar length and gives up beyond 4,000 words of one block (`SPELLING_MAX_WORDS`); comparing a group with every character of a collection (`Matcher.rank`) skips building the readable reasons for the thousands it discards.
* **Known limits.** The first merge-suggestion computation after an edit takes a second or two on a book with thousands of groups, and while it runs another request waits (one connection, one lock per book). The profile function-word statistics are English. The library page lists every book at once (searchable and sortable, fine for a hundred books).
