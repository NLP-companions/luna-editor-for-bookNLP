"""Feature areas of the editor, each a mixin class combined into `core.store.Store`.

  coref.py   scope selection, quote rule, nearby groups, merge suggestions, fragment clean-up, entity excerpts
  quote.py   speaker suggestions for quotes
  rule.py    narrator rule, checks for impossible links, pronoun pass, quote pass
  carry.py   carrying corrections over between the books of a collection
  find.py    Find: search, phrase search, filters, bulk change / create / delete of the results
  profile.py character profiles of the groups (names, pronouns, relations, neighbours, actions, speech)
  flag.py    flags: "check this later" marks with a note
All of them read and edit only through Store's own methods (`update`, `insert`, `delete`, `batch`), so every change
they make is undoable. What they work out is kept in `Store._memo` caches until an edit makes it stale (see store.py).
"""
