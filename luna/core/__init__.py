"""The editor's engine, independent of the web layer (luna/app.py).

  store.py             one open working copy: reading, editing, undo/redo, groups, re-tagging, Export
  schema.py            the SQLite tables and which fields count as hand-edited
  tools/               feature areas mixed into Store (coreference, quotes, rules, collections, find, profiles, flags)
  speakers.py          the mention of a speaker's group nearest to a quote (linking many quotes to a speaker quickly)
  library.py           which books and working copies exist (folders, ./projects)
  collections_store.py what carries over between books of a series (collections.json)
  profiles.py          character profiles: their layout, adding them up over books, a readable summary
  matching.py          matching a book's groups to a collection's characters by evidence
  suggest.py           which evidence each kind of suggestion uses, and its weights
  names.py             personal names: titles, name words, the gender titles imply
  embed.py             optional transformer speech embeddings (downloaded models only)
  bookfile.py          rebuilds BookNLP's .book and .book.html on export
  cleanup_import.py    names plural groups and flags unsettled quotes from the extra files a pre-cleaned folder may hold
  tagsets.py           BookNLP's tag lists (POS, dependency, entity types, supersenses)
  errors.py            EditError: a refused edit, shown to the user as a message
"""
