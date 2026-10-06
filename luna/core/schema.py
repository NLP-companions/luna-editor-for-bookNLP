"""Shared constants of the working copy: its SQLite tables, which fields count as hand-edited, and the export header.

Every book is kept in one SQLite file (see SCHEMA). Tokens have a stable `uid` and a position `ord`; the three span
layers (entities, supersenses, quotes) point at tokens by uid. `manual` on every row lists the fields edited by hand.
"""

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE tokens(
  uid INTEGER PRIMARY KEY, ord INTEGER NOT NULL, word TEXT, lemma TEXT,
  char_start INTEGER, char_end INTEGER, pos TEXT, tag TEXT, dep TEXT, head INTEGER,
  event TEXT, sent_start INTEGER DEFAULT 0, para_start INTEGER DEFAULT 0,
  reviewed INTEGER DEFAULT 0, manual TEXT DEFAULT '[]', extra TEXT DEFAULT '{}');
CREATE INDEX tokens_ord ON tokens(ord);
CREATE INDEX tokens_head ON tokens(head);
CREATE TABLE entities(
  uid INTEGER PRIMARY KEY, tok_start INTEGER, tok_end INTEGER, coref INTEGER,
  prop TEXT, cat TEXT, text TEXT, manual TEXT DEFAULT '[]');
CREATE INDEX ent_start ON entities(tok_start);
CREATE INDEX ent_coref ON entities(coref);
CREATE TABLE supersenses(
  uid INTEGER PRIMARY KEY, tok_start INTEGER, tok_end INTEGER, cat TEXT, text TEXT,
  manual TEXT DEFAULT '[]');
CREATE INDEX ss_start ON supersenses(tok_start);
CREATE TABLE quotes(
  uid INTEGER PRIMARY KEY, tok_start INTEGER, tok_end INTEGER, m_start INTEGER,
  m_end INTEGER, m_phrase TEXT, char_id INTEGER, text TEXT, manual TEXT DEFAULT '[]');
CREATE INDEX q_start ON quotes(tok_start);
CREATE TABLE batches(id INTEGER PRIMARY KEY, label TEXT, ts REAL, undone INTEGER DEFAULT 0);
CREATE TABLE changes(id INTEGER PRIMARY KEY, batch INTEGER, tbl TEXT, uid INTEGER,
  before TEXT, after TEXT);
CREATE INDEX changes_batch ON changes(batch);
"""

SPAN_TABLES = ("entities", "supersenses", "quotes")
TABLES = ("tokens",) + SPAN_TABLES
# Fields whose hand edits are remembered, so later automatic re-tagging keeps them.
TRACKED = {
    "groups": {"name", "pronoun", "hidden"},
    "tokens": {"word", "lemma", "pos", "tag", "dep", "head", "event", "sent_start", "para_start"},
    "entities": {"tok_start", "tok_end", "coref", "prop", "cat"},
    "supersenses": {"tok_start", "tok_end", "cat"},
    "quotes": {"tok_start", "tok_end", "m_start", "m_end", "char_id"},
}
STRUCTURAL = {"ord", "sent_start", "para_start", "char_start", "char_end", "word"}
RETAG_FIELDS = ("lemma", "pos", "tag", "dep", "head")
TOKEN_HEADER = ["paragraph_ID", "sentence_ID", "token_ID_within_sentence", "token_ID_within_document",
                "word", "lemma", "byte_onset", "byte_offset", "POS_tag", "fine_POS_tag",
                "dependency_relation", "syntactic_head_ID", "event"]
LAYER_NAMES = {"entities": "entity", "supersenses": "supersense", "quotes": "quote"}
