'use strict';
/* Library list helpers: which books to show (search text + collection) and in what order.
   Pure functions with no DOM access, so the tests run them under node. The library page uses them through
   window.LibFilter. A "book" is an entry of GET /api/library "books": {key, book_id, folder, text, details, projects: [...]},
   details {title, author, year, tags}, each project {name, sentences, reviewed, last_edit, ...}.
   `members` maps a book key to its collection id. */
(function (root) {
  const SORTS = [['name', 'Name'], ['title', 'Title'], ['author', 'Author'], ['year', 'Year'], ['edited', 'Last edited'],
    ['progress', 'Review progress'], ['collection', 'Collection']];

  /* the details you gave a book (title, author, year, tags); every field may be empty or missing */
  const detailsOf = book => book.details || {};

  /* what a book is called in the list: its title, else its book ID */
  const titleOf = book => detailsOf(book).title || book.book_id;

  /* the collection a book belongs to: the id, or null */
  const collectionOf = (book, members) => members[book.key] || null;

  /* how far the best working copy of a book is reviewed, 0..1; a book without a working copy is at 0 */
  function progressOf(book) {
    return Math.max(0, ...book.projects.map(p => (p.sentences ? p.reviewed / p.sentences : 0)));
  }

  /* the time (seconds) of the latest edit in any working copy of a book; 0 when it was never edited */
  const lastEditOf = book => Math.max(0, ...book.projects.map(p => p.last_edit || 0));

  /* Which books to show. `coll` is 'all', 'none' (books in no collection) or a collection id. `query` is
     matched, word by word and ignoring case, against the book ID, title, author, year, tags, folder, working copies and
     collection. */
  function filterBooks(books, { members, collections, query = '', coll = 'all' }) {
    const names = new Map(collections.map(c => [c.id, c.name]));
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    return books.filter(b => {
      const cid = collectionOf(b, members);
      if (coll === 'none' ? cid : coll !== 'all' && cid !== coll) return false;
      if (!terms.length) return true;
      const d = detailsOf(b);
      const hay = [b.book_id, d.title, d.author, d.year, ...(d.tags || []), b.folder, cid && names.get(cid),
        ...b.projects.map(p => p.name)].filter(x => x != null).join(' ').toLowerCase();
      return terms.every(t => hay.includes(t));
    });
  }

  /* A sorted copy. name: book ID A-Z; title / author: A-Z with books lacking one last; year: oldest first, no year last;
     edited: newest first, never-edited last; progress: least reviewed first (so unstarted books come first);
     collection: by collection name, books in none last. Ties fall back to the name. */
  function sortBooks(books, sort, { members, collections }) {
    const names = new Map(collections.map(c => [c.id, c.name]));
    const byName = (a, b) => a.book_id.localeCompare(b.book_id, undefined, { numeric: true, sensitivity: 'base' });
    const last = '\uffff';  // sorts after every real value
    const cname = b => names.get(collectionOf(b, members)) || last;
    const text = (a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
    const keyed = {
      name: () => 0,
      title: (a, b) => text(detailsOf(a).title || last, detailsOf(b).title || last),
      author: (a, b) => text(detailsOf(a).author || last, detailsOf(b).author || last),
      year: (a, b) => (detailsOf(a).year ?? 1e9) - (detailsOf(b).year ?? 1e9),
      edited: (a, b) => lastEditOf(b) - lastEditOf(a),
      progress: (a, b) => progressOf(a) - progressOf(b),
      collection: (a, b) => cname(a).localeCompare(cname(b), undefined, { sensitivity: 'base' }),
    }[sort] || (() => 0);
    return books.slice().sort((a, b) => keyed(a, b) || byName(a, b));
  }

  const api = { SORTS, collectionOf, detailsOf, titleOf, progressOf, lastEditOf, filterBooks, sortBooks };
  root.LibFilter = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
