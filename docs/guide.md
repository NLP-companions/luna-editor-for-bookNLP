# Luna user guide

Luna corrects BookNLP output, token by token and layer by layer. This guide describes what you can do with it. How to install and start it is in the [README](../README.md); how it is built is in [DOCUMENTATION.md](DOCUMENTATION.md).

Your BookNLP output folder is never changed. Luna works on a copy (a `.sqlite` file in `projects/`) and writes cleaned files to `exports/` when you choose Export. Every edit is saved immediately, so there is nothing to save before you stop it with Ctrl+C. (`projects/`, `exports/`, `library.json` and `collections.json` are in the folder `luna-data`, made in the folder you start Luna from, or in the folder you gave with `--data`.)

## The library

- **Search, collections and sort:** the search box finds books by name, folder, working copy or collection (every word must match). The search also finds a book's title, author, year and tags. The sidebar narrows the list to one collection, to the books in none, or shows all; "New collection…" and "List for all books" are there too. Sort by name (book ID), title, author, year, last edited, review progress (least reviewed first) or collection; books without a title, author or year come last. Your choice of collection and sort is remembered.
- **Book details:** "Edit details" on a book sets its title, author, year and tags (saved as you leave each field, kept in `library.json`). A book with a title is listed under it, with the book ID beside it; clicking a tag searches for it. These details are the editor's own; the companion analyser (aLex) keeps its own.
- **Books:** every book found is listed with its original text (found automatically when it's named `BOOK.txt` and sits near the output, e.g. in `input/`; otherwise choose it) and a menu to put it in a collection. "Start editing" checks every token against the text and creates a working copy; BookNLP's files stay as they are.
- **Working copies:** each shows review progress, number of changes, last edit and exports. Open, rename or discard them here; discarded copies go to `projects/.discarded`. A book can have more than one working copy, e.g. to try something out.
- **Collections:** put the books of a series in a collection (the menu on each book, or "New collection…"). Choosing a collection in the sidebar shows its books, its list (Open list) and Rename / Delete. What you confirm in one book is offered in the others; see [Collections](#collections-carrying-corrections-between-books) below.
- **One book at a time:** opening another book switches the editor to it. If an older browser tab still shows the previous book, it stops with a message instead of editing the wrong one. The "‹ Library" button in the editor returns here.

Everything that applies to every book — not just this one — lives on the **Settings** page (linked in the sidebar here, and from its own tab once a book is open):

- **Folders with BookNLP output:** "Add a folder…" adds a folder BookNLP writes its output to; each is searched for output in the folder itself and in the folders directly inside it (the editor's own exports are left out). Add as many as you like; "Remove" only forgets a folder, its files and working copies stay.
- **Where exports are written:** Export writes to `exports/` in your data folder by default; "Change…" points it at another folder instead (created for you if it doesn't exist yet). Exports already written stay where they are; only new ones go to a folder you change to.
- **Display:** how many sentences of context are shown above and below a mention when the annotation view is filtered to one entity.
- **Suggestions:** which evidence each kind of suggestion uses and how much it counts (collection matching, merge suggestions, fragments, speaker suggestions) — described where each of those is, below.

The command line still works if you prefer it: `luna new --output DIR --text FILE`, `luna open [NAME]` and `luna list`. `--port`, `--no-browser` and `--data FOLDER` (where your files are kept; or set `LUNA_DATA`) go with any of them.

## What you can edit

- **Tokens:** lemma, POS, fine POS, dependency relation, head and event, inline in the table or in the side panel.
- **Entities:** type, proper/common/pronoun, coreference group, boundaries. Add and delete. Besides BookNLP's six types there's **VAR** ("various"), which you set by hand for objects and other things that don't fit the others. It's treated like any other type: in the lists, the Entities view, Find, bulk changes, the `.book` type setting and the exported files.
- **Supersenses:** category and boundaries. Add and delete.
- **Quotes:** speaker, speaker mention, boundaries. Add and delete.

Values outside BookNLP's tag lists are allowed but flagged in the table and listed in `BOOK.validation.tsv` on export. Everything you change by hand is marked, so later automatic re-tagging leaves it alone.

History records every change; undo and redo work across restarts.

## Two views

Switch with the Table / Annotation buttons, or press `v`. Both share the side panel, selection, history and paging.

- **Table:** one row per token, edited like a spreadsheet.
- **Annotation:** one block per sentence, with entities, supersenses and quotes drawn as labelled bars above the words, and arcs from each quote to its speaker mention. Toggle layers in the bar at the top; add lemma, POS, fine POS or event as rows under the words and click a value to change it in place. Dependency arcs are off by default; when on, each sentence sits on one line you can scroll sideways.
  - Drag across words (or click one and Shift-click another) to select them, then choose a layer and label in the popup.
  - Click a label to edit it in the side panel. Clicking an entity highlights every mention of its coreference group on the page.
  - Option-drag from a word to another word to make that its head.
  - ⌘-click entity labels to select several at once (⌘-click a selected one to remove it). The side panel then moves them all to another group or a new one, sets their type (e.g. VAR) or mention type, or deletes them, each in one undoable step. The selection stays when you change page, so it can span the book. ⌘-click works on the entity labels in the table too.

## Tokens, sentences and paragraphs

- **Split a token:** edit its word (double-click it in either view, or use the Word field in the side panel) and type a space where it should split, e.g. `cannot` → `can not`. The spelling itself can't change; the text always stays identical to your original `.txt`.
- **Merge tokens:** select them (Shift-click, or drag in the annotation view) and choose Merge. Only tokens that touch in the text can be merged, because BookNLP tokens never contain spaces.
- **Sentences:** in the annotation view, hover the gap before a word and click to start a new sentence there. The buttons beside each sentence merge it with the next one (⤓), toggle a paragraph break (¶) and re-tag it (↻). The same actions are in the side panel, and in the table's sentence headers on hover.

Annotations follow these changes: a span or mention that touched a split token covers all its parts, and boundaries inside merged tokens snap outward. Character offsets are recalculated from the original text, so the exported `.tokens` still lines up with it.

### spaCy suggestions

After each of these changes, the editor re-parses the affected sentences with spaCy (the same model BookNLP uses, `en_core_web_sm`) and lists what it would change in lemma, POS, fine POS, dependency and head. Nothing is applied until you accept it in the Suggestions panel; values you edited by hand are never suggested. You can also re-tag any sentence yourself.

Because each sentence is parsed on its own rather than as part of the whole book, spaCy may also suggest changes to words you didn't touch. Untick those if you'd rather keep BookNLP's original values.

If spaCy or the model is missing, structural edits still work and you get no suggestions. To get them, install spaCy and its model: `pip install spacy`, then `python -m spacy download en_core_web_sm`. Use `--spacy-model NAME` with `open` if you ran BookNLP with a different model.

## Entities and quotes

Two more views sit next to Table and Annotation. The **← Back** button (or ⌘[, or the browser's back button) returns to wherever you were before: the previous group or tool, or from the text back into Entities.

### Entities

The list on the left has every coreference group, of any entity type: filter by type, search by any name a group goes by, show only groups not checked yet. A group is named for its most common type (*PER*), and also listed under every other type it has a mention of: a group of mostly *PER* mentions with one *VAR* one reads *PER +VAR* and shows up under both filters. The right side has an overview and these tools.

- **A group's page:** rename it, set pronouns, pin it to a number key, mark it as checked (`c`), or mark it as "not a character" (hidden, mentions kept). "Possibly the same as" lists likely duplicates with reasons. Move single mentions, ticked mentions, or every use of a name to another group or a new one. With mentions ticked, "Also tick this group's mentions in the same sentence / paragraph / quote / conversation" extends the selection. "Paint in text" and "Step through in text" jump to the text with this group. ↑ and ↓ move through the list.
- **Merge suggestions:** pairs of groups that may be the same, scored with reasons for and against. For: the same name, one name being part of another (*Holmes* ↔ *Sherlock Holmes*), near-identical spellings, a lone *the landlady* next to a name, matching pronouns, and character profiles alike (the same descriptions, relations, actions or speech; *Dr. Mortimer* ↔ *the old-fashioned family practitioner*). Against: different first names, *Mr.* vs *Mrs.*, different pronouns, being named together in sentences, speaking to each other. Each kind of evidence can be switched off or weighted on the Settings page (Suggestions, tab Merge suggestions); "appear with the same people" and "same places" are off by default there, because two groups of one book mentioned in the same paragraphs share those whether or not they are one person. Go through them one by one with `M` merge, `B` keep the other name, `N` not the same (remembered), `S` skip, `P` previous. Sets of groups with exactly the same name and nothing against them are offered as bundles to merge in one go, after reviewing the list.
- **Fragments:** groups with 1–5 mentions (you choose), one at a time in reading order, each with its mentions in context and up to six likely targets, best first, each with a score and its main reasons (hover for all): a merge suggestion pairing them, how near it is mentioned, the same type, an established group, pronouns, and profiles alike (e.g. the same description: *a country practitioner* → Dr. Mortimer). The weights are on the Settings page (Suggestions, tab Fragments). `1`–`6` assign, `G` other group, `K` keep (marks it checked, so it doesn't come back), `H` not a character, `D` delete, `S` skip, `P` previous. The bigger of the two groups keeps its number and settings.
- **Checks:** links that can't be right, found from pronouns and the dependency parse, one at a time:
  - *pronoun doesn't fit*: *she* in a group referred to as he/him, *they* for one person, *it* in a PER group, *he* or *she* in a LOC, FAC, GPE or ORG group. What a group's pronouns should be comes from the pronouns you set, otherwise from its own pronouns (at least three in four the same), otherwise from BookNLP;
  - *he and she in one group*: a group referred to as both, at least twice each, probably two people merged; move the less frequent ones together;
  - *reflexive ≠ subject*: *himself, herself, myself…* in another group than the subject of their verb (*Holmes threw himself into a chair*);
  - *object = subject*: *him, her, them…* as the direct object of a verb whose subject is in the same group (*Mr. Rucastle dismissed him*);
  - *apposition split*: two descriptions side by side for the same thing (*Miss Violet Hunter, the governess*) in different groups.

  Keys: `1`–`6` move to a candidate (groups mentioned nearby, those whose pronouns fit first), `A` make the two the same (move the pronoun or description into the other's group), `U` the other way round, `M` merge the two groups (appositions), `N` new group, `G` other group, `O` open the group, `K` it's right (remembered), `S` skip, `P` previous. Tick which checks to show at the top.
- **Quote rule:** *I, me, my, mine* and *myself* inside a quote belong to its speaker. Lists every one that's in another group, to untick and apply in one step. Also available for one quote or conversation in the quote's side panel, for a selected passage, and from the Quotes view. Reassigning a quote's speaker (its side panel, or the Quotes view) has its own, narrower version of this: "Assign + Quote-Rule" moves that one quote's *I/me/my/mine/myself* to the new speaker and *you/your/yours/yourself* to the other side of the conversation.

- **Narrator rule:** outside quotes, *I, me, my, mine* and *myself* belong to the narrator. The editor suggests the group with most of them; confirm it, choose another, or say there's no narrator. If the narrator's group has no name (BookNLP often keeps the narrator's *I* in a group of its own), merge it into the character's group; the setting follows the merge. For a letter, a diary or a client's long story told in the first person, select the passage in the text and choose "Narrator for this passage…" in the side panel (another group, or none). Then untick anything you want to leave and move the rest in one step.
- **Groups of several characters** (*Holmes and Watson*, and the *they* and *we* BookNLP linked to it) stay ordinary groups here. Which characters such a group stands for is declared in aLex, the optional companion analyser, where its mentions can also count for its members.
- **Compare:** click a group in the list on the left to fill A, then another for B, and see their full profiles in this book side by side (the same titles, descriptions, relations, appears with, places, actions and speech as the collection's "Full profile"). A card's "Change…" lets you replace just that one; otherwise a third click on the list replaces B. Once both are chosen: **Merge A into B**, **Merge B into A**, or **Merge into other…** (search for an existing group, or type a name that doesn't match one to start a new group with it); the survivor takes A's place so you can keep comparing.
- **Collection:** characters, retyped names and saved Find rules from the other books in this book's collection; see below.

Checking a group is a review mark, like sentence review: it can be undone but isn't in the exported change log.

### Faster reassigning in the text

- **Select by scope:** click an entity label, then "Select mentions in the same sentence / paragraph / quote / conversation / whole book", for its group or for every group. Select a passage (drag, or Shift-click) to select the mentions in it, all or one group's. The side panel then shows the words selected (e.g. *he 12, him 4, Holmes 2*): click a word to keep only it, or ✕ to drop it. Then move them, change their type, or delete them.
- **Number keys:** pin groups to keys 1–9 on a group's page, in the side panel or the groups sidebar. Select mentions (click, ⌘-click or by scope) and press the key to move them; select words with no mention and press the key to add a mention of that group.
- **Paint mode:** "Paint with this group" (side panel, group page or ✎ in the sidebar). Every entity label you click moves into that group; dragging across words adds a new mention to it. Esc stops.
- **Groups sidebar:** the Groups button in the annotation view shows the pinned groups, the groups on this page and the biggest or matching groups beside the text. Click one to highlight its mentions on the page; ‹ › step through its mentions anywhere in the book (also in an entity's side panel); double-click opens it in Entities.
- **Nearby groups:** when you choose a group to move mentions to, the picker offers the groups mentioned just before and after, your number keys and your recent choices.
- **Filter by entity:** "Filter by entity" in the annotation view's bar, next to Pronoun pass, Quote pass and Groups, replaces the page with a searchable list of every group; pick one to see only its mentions and quotes, each with a few sentences of context (how many is the Settings page's Display setting) instead of the whole book. Nearby excerpts merge into one so nothing repeats. It pages 20 excerpts at a time for characters with a lot of them, and everything in it is as editable as the normal view — click a token, fix a span, reassign a quote. "Change…" goes back to the list.

### Pronoun pass

Goes through the pronouns one at a time in reading order, in the annotation view. Start it with "Pronoun pass" in the bar above the text (it starts at the selected word, or at the top of the page) or from its card in the Entities overview.

A bar above the text shows the pronoun, the group it's in (and whether its pronouns fit), and the groups mentioned nearby as candidates. `Enter` keeps it, `1`–`6` move it to a candidate, `G` another group or a new one, `→` goes on to the next one, `←` goes back, `Esc` stops. While the pass runs, the number keys choose from its bar; your pinned group keys work again when you stop.

**Settings** decide what you see. Show: *he/she*, *they*, *it*, *I/we*, *you*. Skip:

- settled by the quote or narrator rule (*I, me, my* inside a quote with a speaker, or in narration once the narrator is set);
- only one matching candidate nearby (*he* or *she* whose group is the only one with those pronouns in the last three sentences);
- in sentences marked as reviewed;
- confirmed earlier (kept with `Enter`, or moved in a pass, and still in that group);
- already moved by hand;
- in groups marked as checked.

Two presets fill these in: **Edit pass** (he/she and they; skips what the rules, the context, your confirmations and your own moves already settle) and **Check pass** (he/she and they; skips only reviewed sentences). Change anything after choosing a preset; the settings are saved with the book. Confirmations aren't edits: they're not in the history and not exported.

Below that, **How to advance** decides what `Enter` or a candidate does once you've dealt with a pronoun: **Auto** (the default) also goes straight on to the next one, exactly as above; **Manual** keeps it in place instead, so you can look it over or make other changes to it, and you go on yourself with `→`, now labelled **Next**.

### Quote pass

The same tool as the Pronoun pass, but for a quote's speaker instead of a pronoun's group: goes through every quote one at a time in reading order, in the annotation view. Start it with "Quote pass" in the bar above the text, or from its card in the Entities overview.

A bar above the text shows the quote, who it's spoken by, and the groups mentioned nearby as candidates. `Enter` keeps the speaker as it is, `1`–`6` move it to a candidate, `G` another speaker (or "no one"), `→` goes on to the next one, `←` goes back, `Esc` stops. Picking a speaker — a candidate, a number key, or "Other…" — always also applies the quote rule, moving that quote's own *I/me/my/mine/myself* and *you/your/yours/yourself* to match, the same as a quick pick anywhere else (see Quotes, below).

**Settings**, in a popup on the bar, decide what's skipped: in sentences marked as reviewed; confirmed earlier (kept with `Enter`, or moved in a pass, and still with that speaker); the speaker was already set by hand; the speaker's group is marked as checked; and, to focus only on the gaps, quotes that already have a speaker. Two presets: **Edit pass** (skips confirmed and hand-set speakers) and **Check pass** (skips only reviewed sentences). Confirmations aren't edits: they're not in the history and not exported.

Below that, the same **How to advance** setting as the Pronoun pass: **Auto** (the default) goes on to the next quote after `Enter` or a candidate; **Manual** keeps the current one in place until you press `→`/**Next** yourself.

Both passes share one bar: starting either stops the other (and paint mode), and the bar disappears when you leave the annotation or table view (the pass itself stays paused, picking back up where it left off) — it doesn't follow you to Entities, Quotes or Flags. If you reassign the pass's current pronoun or quote by hand elsewhere (the side panel, the table), the bar updates to match rather than showing what it had before.

### Quotes

Two sub-tabs:

- **All quotes:** every quote in context with its speaker and speaker mention. Filter by speaker, "no speaker" or text; click a quote's speaker chip (or "Assign speaker…" for all ticked quotes) to choose a new one. Picking from the nearby/pinned/recent suggestions, or pressing Enter, does **Assign + Quote-Rule**, which also moves *I/me/my/mine/myself* inside that quote to the new speaker and *you/your/yours/yourself* to the other side of its conversation — the nearest other speaker nearby in the same run of quotes, when there is one; otherwise "you" is left alone. A second button, plain **Assign**, sets the speaker without touching the quote's own pronouns. "Quote rule…" opens the book-wide quote rule in Entities. The Quote side panel in the annotation view has the same speaker chip and the same two buttons.
- **Speaker suggestions:** quotes whose speaker is probably different from BookNLP's, or missing, scored with reasons for and against, like the merge suggestions in Entities. Besides the attribution tag, the quote before, turn-taking and names, a candidate gets "sounds like" evidence: the quote's words compared with the candidate's other quotes in this book, and with a linked character's quotes in the collection's other books (rare words count more; a quote never counts for itself). It's a modest signal (on the Holmes books it picked the tagged speaker among the eight most talkative about twice as often as chance), so by default it only tips close cases. Every kind of evidence, the margin and the minimum can be changed or switched off on the Settings page (Suggestions, tab Speaker suggestions).
  - For a speaker: an attribution tag next to the quote, read from the dependency parse (*"…," said Holmes*; *Holmes said, "…"*; *"…" he asked*), strongest when it names someone and weaker for *he*/*she*; a quote that goes on from the one before it in the same paragraph; turn-taking in a conversation when paragraphs change and there's no tag; for quotes without a speaker, a name just before the quote.
  - Against a speaker: being addressed by name in the quote (*"Watson, you had better stay"*), BookNLP's speaker mention lying inside the quote itself, or in another paragraph or far away.
  - Each card shows the quote in context with BookNLP's mention and the suggested one marked, both speakers with their reasons, and up to six choices. Keys: `A` accept, `1`–`6` choose, `0` no speaker, `G` other speaker, `K` keep BookNLP's (remembered), `S` skip, `P` previous. Accepting links the tag's mention as the speaker mention when there is one.
  - Quotes whose tag names someone clearly (a name or description, confirmed by the parse) are also offered as bundles to apply in one step, after reviewing the list.
  - When the suggested speaker is a different group with the same name as the current one, the card offers to merge the two groups, which fixes every quote like it at once.

## Find and bulk changes

Press `/` or choose Find. Pick a layer (tokens, entities, supersenses, quotes), a field and a condition: *is*, *contains* (ignores case, also for accented letters and ß; `_` and `%` are ordinary characters), *matches pattern*, *is outside the tag list*, *was edited by hand*, and, for tokens, **is the phrase (words in a row)**: `Baker Street` finds the two words together, and works on any token field, so `pos` with `ADJ NOUN` finds every adjective + noun. Two filters narrow a search: **where** (only in narration, or only inside quotes) and **not yet annotated** (leave out what already has an entity, supersense or quote).

The bar above the results acts on all results, or only the ones you've ticked, in one step:

- **Change a field:** set POS, lemma, type, mention type, group, speaker… on every result.
- **Create from the results:** make an **entity** (type, mention type, and a group: one new group for all, one per distinct text, one per result, or an existing group), a **supersense** or a **quote** (with an optional speaker) over every result. Results that already have an entity with the same start and end, and quotes that would overlap another quote, are skipped and counted. Combine with "Not yet an entity" to tag every untagged occurrence of a word or phrase, e.g. all *Baker Street* as LOC in one new group, or all *violin* as VAR.
- **Delete the results:** delete all or the ticked entities, supersenses or quotes.

Changes of more than 25 items (and every delete) ask for confirmation, and every bulk action undoes in one step.

"Save as rule…" keeps the search and the change as a rule for this book's collection or for all books, offered for review in each new book (see below). Only changes to tags and types can be saved this way, since group and speaker numbers differ from book to book; searches with a phrase or a filter can't be saved.

## Collections: carrying corrections between books

A collection (a series, say) is a set of books that share what you confirm. Create one in the library, then choose it on each book that belongs to it. There's also a list for **all books**, which applies to every book, in a collection or not.

Each collection has a list that fills itself as you work:

- **Characters:** when you mark a group of a character type as checked (PER, and any other types you chose for the exported .book), it's added (or updated) with its name, the proper names it goes by, its pronouns, its type and a **profile** of how it appears in this book: titles, descriptions, pronouns, relations (*wife of Holmes*), who and where it appears with, what it does, and how it speaks. It joins an existing character only when the evidence clearly says it's the same one (see below); otherwise it's added as a new one. Groups with no proper name and groups marked as not a character aren't added. "Update profiles from this book" (in the Collection tool) records every checked group and refreshes the profiles after later corrections.
- **Retyped names:** when every mention of a name has been given a new type by hand (*Baker Street* → FAC), the name is added. A character known by that name takes the new type too.
- **Saved rules:** Find searches with a change, saved with "Save as rule…".

When you open a book of the collection for the first time, the **Collection** tool in Entities opens with what applies to it, and it stays available there afterwards (for instance after the list grew while you worked on another book):

- **Characters:** each group is compared with every character by evidence, not by name alone: its names (as written, without titles, or sharing a name word), titles, who it appears with, its relations, descriptions, places, actions and speech, against the character's profile from the other books. Contradictions count against (*he* vs *she*, *Mr.* vs *Mrs.*, *Sherlock* vs *Mycroft*). A name alone isn't enough to be ticked: something else has to back it up, so the one-off *Perkins* of two stories isn't taken for the same person. A clear match is merged into the biggest group, and the group gets the character's name, pronouns and type, and a link to the character. Groups that could be one or more characters (weaker evidence, two close candidates, or no name at all) are listed separately for you to choose. Each proposal shows its score; open "Evidence" to see every kind of evidence with its similarity, weight and reason, and what counts against it. The weights and thresholds can be changed on the Settings page (Suggestions, tab Collection matching; each kind of evidence can also be switched off, and "Reset this tab to defaults" puts them back). Only the settings you change are saved, so everything you left alone follows the defaults, also when a later version improves them.
- **Retyped names:** mentions with that exact text and another type.
- **Rules:** how many items each would change, with examples.

Untick anything that doesn't apply, then apply: everything is one step to undo. Nothing is applied without your review.

**Transformer speech (optional).** "Compute speech embeddings" in the Collection tool turns each speaker's quotes in the open book into one vector with a downloaded transformer model (BookNLP's own BERT by default; any other downloaded model this installation can read can be chosen on the Settings page, Suggestions tab Collection matching). It runs on your computer, without the internet, and takes about half a minute for *The Hound of the Baskervilles*. The vectors go into the book's profiles and the linked characters' profiles, next to the stylometric speech counts; they're used for matching only when "Use transformer speech in matching" is ticked. A vector is dropped once a speaker's quotes change; compute again after correcting speakers. Like the stylometric speech style, it's a weak signal between characters of one author, so it only supports the other evidence.

Edit the lists in the library ("Open list"): rename characters, add or remove names (e.g. add *the landlady* to Mrs. Hudson), change pronouns and types, move entries to the list for all books or to another collection, or delete them (one at a time, or tick several and "Delete ticked"). The Characters tab also shows each character's profile over all its books: a short one in the table (titles, descriptions, who it appears with, places, what it does) and the full one with "Full profile" (relations, what is done to it, what it has, how it is described, pronouns, speech). "Export" saves the characters as a CSV (one row each, with the main profile data) or a JSON file (everything, including each book's full profile). A long series has hundreds of characters: the list shows the first 150, with a filter box (by name, another name or book) and "Show all". "Tick all" ticks what the filter matches. Everything is kept in `collections.json` next to the editor.

The link to a collection's character goes into the export, so an analysis across books can tell that *Holmes* in one book is *Holmes* in the next: `BOOK.characters.tsv` has `collection_id` and `collection_name` columns, and each linked entry in `BOOK.book` has a `collection_id` field.

## Long books and many books

The editor is built and tested for a corpus: books of 150,000–250,000 tokens (tens of thousands of mentions, ten thousand quotes, thousands of groups), and series of dozens of books in one collection. Roughly what to expect on a laptop, for a book of 190,000 tokens:

- importing a book takes a couple of seconds and opening a working copy a fraction of one; pages, the group list, a group's page and Find answer at once;
- editing actions (giving thousands of quotes to a speaker, moving thousands of mentions, a bulk change over every noun) take well under a second to one second, and undo them just as fast;
- the merge suggestions take a second or two to work out after an edit (the fragments, speaker suggestions and checks follow from the same data), and Export, which also rebuilds `.book`, about three seconds;
- in a collection with a thousand characters, marking a group as checked takes about a third of a second, and opening a book to review what the collection offers a couple of seconds. It grows in step with the number of characters.

Things worth knowing with many books: the library page lists every book at once (use the search box, the collection sidebar and Sort); the Collection tool in Entities is only worked out where it is shown; a collection's character list shows 150 at a time with a filter; and `collections.json` stays readable (indented, one line per book's profile of a character) but is a few megabytes for a thousand characters, saved after every change. The word-use statistics behind "speaks like" and the merge/fragment profiles are English function words: for another language they say little, and the rest (names, titles, relations, places, actions) still work.

The numbers come from synthetic books of any size (`python -m tests.synthetic`) and are measured by `python -m tests.scale book` and `python -m tests.scale library`; [DOCUMENTATION.md](DOCUMENTATION.md) §12 has the table and what keeps them there.

## Review tracking

Mark each sentence as reviewed with the ✓ beside it (or press `r` with a word selected). The header shows your progress, and "Next unreviewed" jumps to the next sentence you haven't marked. Review marks can be undone like any edit but are left out of the exported change log.

Next to it, **Settings** opens the [Settings page](#the-library) — everything that applies to every book, not just this one; a table of contents on the left jumps between its sections (Folders, Exports, Display, Suggestions) and follows the one you're scrolled to.

## Flags

⚐ beside a mention, a quote, a group or a sentence flags it to check on later, with an optional note (click it again to change or remove the note). It's a personal marker like a review or checked mark: undoable, but never exported. Find the flag button in a mention's or quote's side panel, a group's page, and beside each sentence (the annotation view's gutter and the table's sentence header).

The **Flags** tab (with a count badge when there's something flagged) lists every flag, oldest first, with its note and "Show in text" (or "Open" for a group) to jump straight to it. A flag on something later deleted just drops off the list.

If a book's folder has `ID.groups.tsv` and `ID.cleanup_log.tsv` files (written by a step that cleans up BookNLP's coreference and speaker attribution before you start editing), starting it also flags every quote that step wasn't confident about, with its reason as the note — a ready-made list of what to check first. It also carries over the names it gave the "we/us/our" groups it created (e.g. "Holmes and Watson").

## Saving

Every change is written to the working copy the moment you make it; there is nothing to save. The header shows when that last happened ("Saved 3 min ago", "Saving…", or "Not saved" in red if the editor stopped running).

## Jumping to the text

"Show in text" (on a quote, an entity, a check, a search result…) opens the text with the excerpt **in the middle of the page**, with the sentences before and after it above and below, so you can judge from context. Clicking the excerpt at the top of the side panel does the same. A jump always shows at least 15 sentences, even if the page size (top right) is smaller.

## Export

Export writes a complete folder to `exports/BOOK-DATE-TIME/` with the same file names and formats as BookNLP. If anything goes wrong half way, the unfinished folder is removed again, so nothing that reads the exports finds a partial one. Besides BookNLP's files it holds:

- `BOOK.changes.md`, a log of every change;
- `BOOK.validation.tsv`, every value outside the tag lists;
- `BOOK.characters.tsv`, every group with its name, type, pronouns, mention and quote counts, whether it's marked "not a character", and the collection character it's linked to (`collection_id`, `collection_name`);
- `BOOK.txt`, a copy of the original text, unchanged.

`BOOK.book` and `BOOK.book.html` are rebuilt from your corrections (see below). The change log says which code was used for the rebuild.

## The rebuilt .book and .book.html

On export, `.book` is rebuilt with BookNLP's own `get_syntax`, taken from the BookNLP installed in your environment, so every corrected head, dependency label, entity and group feeds into the character list, actions, patients, possessions and modifiers. If the installed BookNLP can't be imported, a bundled verbatim copy of the same function is used and the change log says so.

- **Which groups:** BookNLP's rule, groups with at least two mentions of the included entity types. By default that's PER only, as in BookNLP. Choose other types (LOC, FAC, GPE, VEH, ORG) under "Entity types in the exported .book" in the Entities overview. Their mentions are then counted and analysed the same way as people's, and every entry gets a `type` field with the group's main type. "Not a character" only affects the editor.
- **Pronouns:** BookNLP's original values, combined (weighted by BookNLP's evidence) where you merged groups. Pronouns you set by hand replace them. A group split off from another starts with the original group's values; set its pronouns by hand if they differ.
- **Names:** each character gets an extra `name` field with the name you gave it, or BookNLP's most frequent name. Characters linked to a collection character also get a `collection_id` field. The rest of the format is unchanged.
- **.book.html:** BookNLP's report, produced by a port of its own code, using your names in place of BookNLP's where you renamed a group.

With no edits, the rebuilt files are identical to BookNLP's, apart from the added `name` fields.

Each group's page in Entities shows what its `.book` entry will contain, or why it's left out.

BookNLP is MIT-licensed (Copyright (c) 2021 David Bamman); `luna/core/bookfile.py` contains the bundled copy and the port, and [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) has the licence.

## For developers

`luna/app.py` is the web layer; the engine is in `luna/core/` (`luna/core/tools/` holds the feature areas), the pages in `luna/static/`. [DOCUMENTATION.md](DOCUMENTATION.md) explains the code and, in particular, where all data comes from and how it is stored, processed, edited and exported, and lists what every test file covers. Every function has a short description of what it does and how.

The tests (`python -m unittest discover -s tests -t .`, see the [README](../README.md#development)) build a tiny annotated book in BookNLP's file formats and check editing, undo and redo, groups and their filters, the suggestion tools, collections, export (with no edits the exported files are identical to BookNLP's) and the web API. They also check that every API call in the JavaScript matches a real route, that books with next to nothing in them don't break any view, that random sequences of edits keep every invariant and every cache correct (`test_fuzz.py`), and that a synthetic book of 3,000 sentences stays correct under big edits. `BNE_SCALE=1` adds a book of 190,000 tokens with time limits on the operations that used to scan the whole book once per item.

One test set runs on **copies** of your own working copies in `projects/` (the originals are never opened for writing): every view is computed, every stored span still lines up with the text, export → re-import → export is unchanged, and undoing every edit gives back exactly what BookNLP wrote. It uses the two smallest working copies (set `BNE_REAL_BOOKS=all` to use them all) and is skipped when there are none.
