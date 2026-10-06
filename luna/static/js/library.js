'use strict';
/* $, esc, api, toast, fail, modal/closeModal and browse are shared with settings.html: see pageui.js, loaded first. */

/* A date and time from seconds since 1970, or 'never'. */
const when = ts => ts ? new Date(ts * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : 'never';
/* The date and time in an export folder's name (BOOK-20260924-201753 → 2026-09-24 20:17). */
const exportWhen = name => name.replace(/^.*-(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)\d\d(?:-\d+)?$/, '$1-$2-$3 $4:$5');

/* The last library overview from the server (folders, books, collections). */
let L = null;

/* Fetch the library overview and draw the page. */
async function load() {
  L = await api('GET', '/api/library');
  render();
}

/* ------------------------------------------------------------ rendering */
/* One working copy: progress, changes, last edit, exports, and Open / Rename / Discard. */
function wcHTML(p, showFrom) {
  const isOpen = L.open === p.name;
  const pct = p.sentences ? Math.round(100 * p.reviewed / p.sentences) : 0;
  const meta = p.error ? `<span class="err">Can’t read this working copy: ${esc(p.error)}</span>` : `
    <span><span class="prog" title="${p.reviewed} of ${p.sentences} sentences reviewed"><i style="width:${pct}%"></i></span>${p.reviewed.toLocaleString()} / ${p.sentences.toLocaleString()} reviewed</span>
    <span>${p.changes.toLocaleString()} change${p.changes === 1 ? '' : 's'}</span>
    <span>Last edit ${esc(when(p.last_edit))}</span>
    ${p.exports.length ? `<span>${p.exports.length} export${p.exports.length === 1 ? '' : 's'}, latest ${esc(exportWhen(p.exports[0]))}
      <button class="link" data-act="reveal" data-path="${esc(L.exports_dir + '/' + p.exports[0])}">Show</button></span>` : '<span>Not exported yet</span>'}`;
  return `<div class="wc">
    <div><div class="wn">${esc(p.name)}${isOpen ? '<span class="now">Open</span>' : ''}</div><div class="wm">${meta}</div></div>
    <div class="acts">
      <button class="btn ${isOpen ? '' : 'primary'} sm" data-act="open" data-name="${esc(p.name)}">${isOpen ? 'Continue' : 'Open'}</button>
      <button class="btn sm" data-act="rename" data-name="${esc(p.name)}">Rename</button>
      <button class="btn sm danger" data-act="discard" data-name="${esc(p.name)}">Discard</button>
    </div>
    ${showFrom ? `<div class="from">${esc(p.book_id || '')} · from ${esc(p.source_dir || 'an unknown folder')}</div>` : ''}</div>`;
}
/* The drop-down that puts a book in a collection. */
function collSelect(b) {
  const C = L.collections, cur = C.members[b.key] || '';
  return `<label class="bcoll">Collection <select data-member="${esc(b.key)}" aria-label="Collection of ${esc(b.book_id)}">
    <option value="" ${cur ? '' : 'selected'}>None</option>${C.collections.map(c => `<option value="${esc(c.id)}" ${cur === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}
    <option value="__new">New collection…</option></select></label>`;
}
/* "3 characters · 1 retyped name · 2 rules" for a collection's list */
const listCounts = c => `${c.characters.toLocaleString()} character${c.characters === 1 ? '' : 's'} · ${c.retypes.toLocaleString()} retyped name${c.retypes === 1 ? '' : 's'} · ${c.rules.toLocaleString()} rule${c.rules === 1 ? '' : 's'}`;
/* one book: title (else book ID), author and year, collection menu; its tags and the details form; folder and original
   text; then its working copies */
function bookHTML(b) {
  const current = b.projects.some(p => p.name === L.open);
  const d = b.details, title = LibFilter.titleOf(b), editing = VIEW.editing === b.key;
  const byline = [d.author, d.year].filter(x => x != null && x !== '').join(' · ');
  const chips = d.tags.map(t => `<button class="tagchip" data-tag="${esc(t)}" title="Show the books tagged “${esc(t)}”">${esc(t)}</button>`).join('');
  const field = (f, label, w) => `<label>${label} <input data-bf="${f}" value="${esc(d[f] ?? '')}" style="width:${w}px" autocomplete="off"${f === 'tags' ? ' list="dl-tags" placeholder="comma separated"' : ''}></label>`;
  return `<article class="bookcard${current ? ' current' : ''}" data-key="${esc(b.key)}">
    <div class="bhead"><span class="bn">${esc(title)}</span>${d.title ? `<span class="bid">${esc(b.book_id)}</span>` : ''}${byline ? `<span class="bmeta">${esc(byline)}</span>` : ''}
      <span class="grow"></span>${collSelect(b)}</div>
    ${editing
      ? `<div class="bedit">${field('title', 'Title', 220)}${field('author', 'Author', 150)}${field('year', 'Year', 60)}${field('tags', 'Tags', 180)}<button class="btn sm primary" data-act="editdone">Done</button><span class="note">Saved as you leave each field.</span></div>`
      : `<div class="btags">${chips}<button class="link" data-act="editbook" data-key="${esc(b.key)}">${chips || byline || d.title ? 'Edit details' : 'Add title, author, year, tags'}</button></div>`}
    <div class="btext"><span>Folder</span><code title="${esc(b.folder)}">${esc(b.folder)}</code><button class="link" data-act="reveal" data-path="${esc(b.folder)}">Show</button>
      <span>Original text</span>${b.text ? `<code title="${esc(b.text)}">${esc(b.text)}</code>` : '<span class="miss">not found</span>'}</div>
    ${b.projects.length
      ? `<div class="wcs">${b.projects.map(p => wcHTML(p, false)).join('')}</div>
         <div class="startrow"><button class="link" data-act="start" data-key="${esc(b.key)}">Start another working copy</button></div>`
      : `<div class="startrow"><button class="btn primary sm" data-act="start" data-key="${esc(b.key)}">Start editing</button>
         <span class="muted">Creates a working copy. BookNLP’s files stay as they are.</span></div>`}
  </article>`;
}

/* ------------------------------------------------------------ view: collections, search, sort
   The list can be narrowed to one collection (the sidebar) and by search text, and sorted; LibFilter (libfilter.js) does
   the work. The choice of collection and sort is remembered in this browser. */
const VIEW = { q: '', coll: 'all', sort: 'name', editing: null };  // editing: the book key whose details form is open
try { Object.assign(VIEW, JSON.parse(localStorage.getItem('bne.lib') || '{}'), { q: '' }); } catch { }
/* Remember the chosen collection and sort in this browser. */
const saveView = () => { try { localStorage.setItem('bne.lib', JSON.stringify({ coll: VIEW.coll, sort: VIEW.sort })); } catch { } };
/* The current search, collection and collection data for LibFilter. */
const filterCtx = () => ({ members: L.collections.members, collections: L.collections.collections, query: VIEW.q, coll: VIEW.coll });

/* the sidebar: All books, one entry per collection with its number of books, and the books in none */
function navHTML() {
  const C = L.collections, n = id => L.books.filter(b => LibFilter.collectionOf(b, C.members) === id).length;
  const item = (id, label, count) => `<button data-coll="${esc(id)}" class="${VIEW.coll === id ? 'on' : ''}" aria-pressed="${VIEW.coll === id}"><span>${esc(label)}</span><span class="num">${count}</span></button>`;
  return `<nav class="collnav" aria-label="Collections">
    ${item('all', 'All books', L.books.length)}
    ${C.collections.slice().sort((a, b) => a.name.localeCompare(b.name)).map(c => item(c.id, c.name, n(c.id))).join('')}
    ${item('none', 'No collection', L.books.filter(b => !LibFilter.collectionOf(b, C.members)).length)}
    <div class="navfoot"><button class="link" data-act="newcoll">New collection…</button>
      <button class="link" data-act="colllist" data-id="*" title="Characters, retyped names and rules that apply to every book">List for all books</button>
      <a class="link" href="/settings" title="Suggestion settings, BookNLP output folders, display — everything that applies to every book">Settings…</a></div></nav>`;
}

/* above the list: what the chosen collection is, with its list and its rename/delete buttons */
function collHeadHTML() {
  if (VIEW.coll === 'none') return '<p class="note collnote">Books that aren’t in a collection. Choose one on a book to add it.</p>';
  const c = L.collections.collections.find(x => x.id === VIEW.coll);
  if (!c) return '';
  const names = c.books.map(k => { const b = L.books.find(x => x.key === k); return b ? b.book_id : null; }).filter(Boolean);
  return `<div class="coll"><div><div class="cn">${esc(c.name)}</div>
      <div class="cm">${names.length ? `${names.length} book${names.length === 1 ? '' : 's'}: ${names.map(esc).join(', ')}` : 'No books yet: choose this collection on a book'} · ${listCounts(c.counts)}</div>
      <div class="cm">What you confirm in one of these books (checked characters, retyped names, saved rules) is offered for review in the others.</div></div>
    <div class="acts"><button class="btn sm primary" data-act="colllist" data-id="${esc(c.id)}">Open list</button><button class="btn sm" data-act="collrename" data-id="${esc(c.id)}">Rename</button><button class="btn sm danger" data-act="colldel" data-id="${esc(c.id)}">Delete</button></div></div>`;
}

/* the list of books for the current search, collection and sort, and the working copies that belong to none */
function renderList() {
  const shown = LibFilter.sortBooks(LibFilter.filterBooks(L.books, filterCtx()), VIEW.sort, filterCtx());
  $('#books').innerHTML = shown.map(bookHTML).join('') || `<div class="empty">${
    !L.books.length ? (L.sources.length ? 'No BookNLP output in these folders. BookNLP writes a BOOK.tokens and a BOOK.entities file for each book.'
      : 'Add the folder BookNLP writes its output to in <a class="link" href="/settings">Settings</a>, and your books appear here.')
      : VIEW.q ? `No book matches “${esc(VIEW.q)}”${VIEW.coll === 'all' ? '' : ' in this collection'}.` : 'No books in this collection yet.'}</div>`;
  $('#count').textContent = shown.length === L.books.length ? `${L.books.length} book${L.books.length === 1 ? '' : 's'}` : `${shown.length} of ${L.books.length} books`;
  const terms = VIEW.q.toLowerCase().split(/\s+/).filter(Boolean);
  const others = VIEW.coll !== 'all' ? [] : L.other_projects.filter(p => terms.every(t => [p.name, p.book_id, p.source_dir].join(' ').toLowerCase().includes(t)));
  $('#others').innerHTML = others.length ? `<h2 class="sub">Other working copies <span class="num">${others.length}</span></h2>
    <p class="note" style="margin-top:0">Working copies whose BookNLP output isn’t in the folders below. They still open and export normally.</p>
    <div class="bookcard"><div class="wcs" style="margin-top:0">${others.map(p => wcHTML(p, true)).join('')}</div></div>` : '';
}

/* the whole page: sidebar, search and sort, and the list. The folders BookNLP output is read from, and every other
   setting, live on the Settings page now (linked in the sidebar and, when nothing is found yet, in the empty state). */
function render() {
  if (VIEW.coll !== 'all' && VIEW.coll !== 'none' && !L.collections.collections.some(c => c.id === VIEW.coll)) VIEW.coll = 'all';  // a deleted collection
  $('#topActions').innerHTML = (L.open ? `<a class="btn primary" href="/">Back to ${esc(L.open)}</a>` : '') + '<a class="btn sm" href="/settings">Settings</a>';
  $('#lib').innerHTML = `<datalist id="dl-tags">${L.tags.map(t => `<option value="${esc(t)}">`).join('')}</datalist><h1>Library</h1>
    <p class="lede">Your books from the folders in <a class="link" href="/settings">Settings</a>, each with its working copies. One book is open at a time; opening another switches the editor to it.</p>
    <div class="libgrid">${navHTML()}
      <div class="libmain">
        <div class="libtools"><input type="search" id="q" placeholder="Search books, folders, working copies, collections…" value="${esc(VIEW.q)}" aria-label="Search the library" autocomplete="off">
          <label class="sortsel">Sort <select id="sort" aria-label="Sort books">${LibFilter.SORTS.map(([v, l]) => `<option value="${v}" ${VIEW.sort === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
          <span class="num" id="count"></span></div>
        ${collHeadHTML()}
        <div id="books"></div>
        <div id="others"></div>
      </div></div>`;
  renderList();
}
/* Suggestion settings, BookNLP output folders and display settings moved to the Settings page (settings.js). */

/* Saving the details form: each field is sent as you leave it. The list is not redrawn, so typing isn't interrupted. */
$('#lib').addEventListener('change', async e => {
  const f = e.target.dataset.bf; if (!f) return;
  const card = e.target.closest('[data-key]'), book = L.books.find(x => x.key === card.dataset.key);
  try {
    book.details = await api('PATCH', '/api/library/book', { key: book.key, fields: { [f]: e.target.value } });
    e.target.value = f === 'tags' ? book.details.tags.join(', ') : book.details[f] ?? '';  // show what was kept (trimmed, tidied)
    L.tags = [...new Set(L.books.flatMap(x => x.details.tags))].sort((a, b) => a.localeCompare(b));
    const dl = $('#dl-tags'); if (dl) dl.innerHTML = L.tags.map(t => `<option value="${esc(t)}">`).join('');
    toast('Saved');
  } catch (err) { fail(err); e.target.value = f === 'tags' ? book.details.tags.join(', ') : book.details[f] ?? ''; }
});
/* search box, sort menu and collection sidebar */
$('#lib').addEventListener('input', e => { if (e.target.id === 'q') { VIEW.q = e.target.value; renderList(); } });
$('#lib').addEventListener('keydown', e => { if (e.key === 'Escape' && e.target.id === 'q' && VIEW.q) { VIEW.q = ''; e.target.value = ''; renderList(); } });
$('#lib').addEventListener('change', e => { if (e.target.id === 'sort') { VIEW.sort = e.target.value; saveView(); renderList(); } });
$('#lib').addEventListener('click', e => {
  const t = e.target.closest('[data-tag]');  // a tag chip searches for that tag
  if (t) { VIEW.q = t.dataset.tag; $('#q').value = VIEW.q; renderList(); return; }
  const c = e.target.closest('[data-coll]'); if (!c) return;
  VIEW.coll = c.dataset.coll; saveView(); render();
});

/* The dialog to start a working copy: shows the original text file, asks for a name, creates it. */
function startDialog(b) {
  const st = { text: b.text, name: b.projects.length ? `${b.book_id}-${b.projects.length + 1}` : b.book_id, err: null, busy: false };
  const draw = () => {
    modal(`<header><h2 id="mtitle">Start editing ${esc(b.book_id)}</h2></header>
      <div class="mbody">
        <div class="field"><span class="lbl">BookNLP output</span><code>${esc(b.folder)}</code></div>
        <div class="field"><span class="lbl">Original text you ran BookNLP on</span>
          <div class="row">${st.text ? `<code style="flex:1">${esc(st.text)}</code>` : '<span class="miss" style="flex:1">Not found yet</span>'}
            <button class="btn sm" data-s="pick">${st.text ? 'Change…' : 'Choose…'}</button></div></div>
        <div class="field"><label for="wname">Name of the working copy</label><div class="row"><input id="wname" value="${esc(st.name)}" ${st.text ? 'autofocus' : ''}></div></div>
        <p class="note" style="margin:0">Every token is checked against the text, so a wrong file is caught before anything is created.</p>
        ${st.err ? `<p class="err">${esc(st.err)}</p>` : ''}${st.busy ? '<p class="busy">Reading BookNLP’s files and checking them against the text…</p>' : ''}
      </div>
      <footer><button class="btn" data-s="cancel">Cancel</button><button class="btn primary" data-s="go" ${st.text && !st.busy ? '' : 'disabled'}>Start editing</button></footer>`);
    $('#wname').addEventListener('input', e => { st.name = e.target.value; });
    $('#wname').addEventListener('keydown', e => { if (e.key === 'Enter' && st.text) go(); });
    $('#modal').onclick = e => {
      const t = e.target.closest('[data-s]'); if (!t) return;
      if (t.dataset.s === 'cancel') closeModal();
      else if (t.dataset.s === 'pick') browse({ title: `Original text of ${b.book_id}`, want: 'txt', start: st.text || b.folder,
        onPick: p => { st.text = p; st.err = null; draw(); }, onCancel: draw });
      else if (t.dataset.s === 'go') go();
    };
  };
  const go = async () => {
    if (st.busy) return;
    st.busy = true; st.err = null; draw();
    try { await api('POST', '/api/library/start', { key: b.key, text: st.text, name: st.name.trim() }); location.href = '/'; }
    catch (err) { st.busy = false; st.err = err.message; draw(); }
  };
  draw();
}

/* The dialog to rename a working copy. */
function renameDialog(name) {
  modal(`<header><h2 id="mtitle">Rename “${esc(name)}”</h2></header>
    <div class="mbody"><div class="field"><label for="rname">New name</label><div class="row"><input id="rname" value="${esc(name)}" autofocus></div></div>
      <p class="err" id="rerr" hidden></p></div>
    <footer><button class="btn" data-r="cancel">Cancel</button><button class="btn primary" data-r="ok">Rename</button></footer>`);
  const input = $('#rname'); input.select();
  const go = async () => {
    const n = input.value.trim();
    if (!n || n === name) { closeModal(); return; }
    try { L = await api('POST', '/api/library/rename', { name, new: n }); closeModal(); render(); toast(`Renamed to ${n}`); }
    catch (err) { const e = $('#rerr'); e.textContent = err.message; e.hidden = false; }
  };
  input.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
  $('#modal').onclick = e => { const t = e.target.closest('[data-r]'); if (!t) return; if (t.dataset.r === 'ok') go(); else closeModal(); };
}

/* The dialog to discard a working copy (it is moved to projects/.discarded). */
function discardDialog(name) {
  modal(`<header><h2 id="mtitle">Discard “${esc(name)}”?</h2></header>
    <div class="mbody"><p style="margin:0">This removes the working copy and all its edits from the library. Your exports and BookNLP’s files aren’t touched.</p>
      <p class="note" style="margin:0">The file is moved to <code>editor/projects/.discarded</code>, so it can still be recovered by hand.</p></div>
    <footer><button class="btn" data-d="cancel" autofocus>Cancel</button><button class="btn danger" data-d="ok">Discard working copy</button></footer>`);
  $('#modal').onclick = async e => {
    const t = e.target.closest('[data-d]'); if (!t) return;
    if (t.dataset.d === 'cancel') { closeModal(); return; }
    try { L = await api('POST', '/api/library/discard', { name }); closeModal(); render(); toast('Working copy discarded'); }
    catch (err) { fail(err); }
  };
}

/* ------------------------------------------------------------ collections */
/* A dialog asking for a name (new collection, rename collection). */
function nameDialog({ title, value = '', ok, note = '', onOk }) {
  modal(`<header><h2 id="mtitle">${esc(title)}</h2></header>
    <div class="mbody"><div class="field"><label for="cname">Name</label><div class="row"><input id="cname" value="${esc(value)}" autofocus placeholder="e.g. Sherlock Holmes"></div></div>
      ${note ? `<p class="note" style="margin:0">${note}</p>` : ''}<p class="err" id="cerr" hidden></p></div>
    <footer><button class="btn" data-c="cancel">Cancel</button><button class="btn primary" data-c="ok">${esc(ok)}</button></footer>`);
  const input = $('#cname'); input.select();
  const go = async () => {
    try { await onOk(input.value.trim()); closeModal(); await load(); }
    catch (err) { const e = $('#cerr'); e.textContent = err.message; e.hidden = false; }
  };
  input.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
  $('#modal').onclick = e => { const t = e.target.closest('[data-c]'); if (!t) return; if (t.dataset.c === 'ok') go(); else closeModal(); };
}
/* Pronoun suggestions for the character list. */
const PRONOUNS = ['he/him/his', 'she/her', 'they/them/their'];
/* Pronoun classes of the profiles (core/tools/rule.py PRON_CLASS) as words. */
const PRON_WORDS = { m: 'he', f: 'she', n: 'it', p: 'they', 1: 'I', '1p': 'we', 2: 'you' };

/* What the list dialog remembers while it stays on one list: ticked characters, characters whose profile is open, the
   filter text, and whether every matching character is shown (a big collection shows the first LIST_ROWS at first). */
const LIST = { lid: null, ticks: new Set(), open: new Set(), q: '', all: false, data: null };
/* Characters shown in the list dialog before "Show all". A collection of a long series has hundreds, and a table of that many
   editable rows is slow to draw. */
const LIST_ROWS = 150;

/* The characters of a list that match the filter text (every word in the name, another name or a book), by name. */
function listMatching(lst) {
  const terms = LIST.q.toLowerCase().split(/\s+/).filter(Boolean);
  return lst.characters.filter(e => !terms.length || terms.every(t => [e.name, ...Object.keys(e.names || {}), ...(e.books || [])].join(' ').toLowerCase().includes(t)))
    .sort((a, b) => a.name.localeCompare(b.name));
}
/* The Characters tab: tick boxes, bulk delete and export above an editable table, each character with a short profile
   (from its profiles over all books) and a full one to open. */
function charactersHTML(lid, lst, moveSel, del) {
  if (!lst.characters.length) return '<p class="muted">No characters yet. Mark groups of a character type as checked in a book of this collection and they appear here.</p>';
  const sums = lst.summaries, lidq = encodeURIComponent(lid);
  const join = (xs, n = 3) => esc((xs || []).slice(0, n).join(', '));
  const line = (label, xs, n) => xs && xs.length ? `<div><span class="pl">${label}</span> ${join(xs, n)}</div>` : '';
  const short = s => [line('Titles', s.titles), line('Described as', s.nouns), line('With', s.cooccur), line('At', s.places), line('Does', s.actions)].join('')
    || '<span class="muted">No profile yet: check it again in a book, or use “Update profiles from this book”.</span>';
  const full = s => `<div class="lprof">${[['Titles', s.titles], ['Described as', s.nouns], ['Relations', s.relations], ['Appears with', s.cooccur],
    ['Places', s.places], ['Does', s.actions], ['Done to them', s.undergoes], ['Has', s.has], ['Described by', s.described],
    ['Pronouns', Object.entries(s.pronouns || {}).map(([k, v]) => `${PRON_WORDS[k] || k} ${Math.round(100 * v)}%`)],
    ['Speech', s.quotes ? [`${s.quotes} quotes, ${s.words.toLocaleString()} words`, ...s.speech_words] : []]]
    .map(([k, xs]) => `<div><span class="pl">${k}</span> ${xs && xs.length ? join(xs, 12) : '<span class="muted">none</span>'}</div>`).join('')}</div>`;
  const matching = listMatching(lst), terms = LIST.q.trim();
  const shown = LIST.all ? matching : matching.slice(0, LIST_ROWS);
  const rows = shown.map(e => {
    const s = sums[e.id] || {}, open = LIST.open.has(e.id);
    return `<tr data-id="${esc(e.id)}" data-kind="characters">
      <td><input type="checkbox" data-tick="${esc(e.id)}" ${LIST.ticks.has(e.id) ? 'checked' : ''} aria-label="Tick ${esc(e.name)}"></td>
      <td><input data-f="name" value="${esc(e.name)}" aria-label="Name"><div class="num">${s.gender ? esc(PRON_WORDS[s.gender] || s.gender) + ' · ' : ''}${(s.mentions || 0).toLocaleString()} mentions · ${s.quotes || 0} quotes</div></td>
      <td><textarea data-f="names" rows="${Math.min(4, Math.max(1, Math.ceil(Object.keys(e.names || {}).join(', ').length / 34)))}" aria-label="Other names, separated by commas">${esc(Object.keys(e.names || {}).join(', '))}</textarea></td>
      <td><input data-f="pronoun" list="dl-pron" value="${esc(e.pronoun || '')}" aria-label="Pronouns" style="width:110px"><input data-f="cat" value="${esc(e.cat || '')}" aria-label="Type" style="width:56px;margin-top:4px"></td>
      <td class="num">${esc((e.books || []).join(', '))}</td>
      <td class="lshort">${short(s)}<button class="link" data-prof="${esc(e.id)}" aria-expanded="${open}">${open ? 'Hide profile' : 'Full profile'}</button></td>
      <td class="la">${moveSel('characters', e.id)}${del('characters', e.id)}</td></tr>
      ${open ? `<tr class="lprow"><td></td><td colspan="6">${full(s)}</td></tr>` : ''}`;
  }).join('');
  const n = LIST.ticks.size, all = matching.length > 0 && matching.every(e => LIST.ticks.has(e.id));
  return `<div class="ltools"><input type="search" id="lq" placeholder="Filter by name or book" value="${esc(LIST.q)}" aria-label="Filter the characters" autocomplete="off" style="width:200px">
      <label class="chk"><input type="checkbox" data-tick="*" ${all ? 'checked' : ''}> Tick all</label>
      <button class="btn sm danger" data-bulk="del" ${n ? '' : 'disabled'}>Delete ticked${n ? ` (${n})` : ''}</button>
      <span class="grow"></span><span class="note">Export</span>
      <a class="btn sm" href="/api/library/list/${lidq}/export?format=csv" download title="One row per character: names, pronouns, type, books, counts and the main profile data">CSV</a>
      <a class="btn sm" href="/api/library/list/${lidq}/export?format=json" download title="Everything, including each book's full profile">JSON</a></div>
    <div class="ltwrap"><table class="lt"><thead><tr><th></th><th>Name</th><th>Also called</th><th>Pronouns · type</th><th>From</th><th>Profile</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${matching.length > shown.length ? `<p class="note">Showing ${shown.length.toLocaleString()} of ${matching.length.toLocaleString()} characters. <button class="link" data-lall>Show all</button> or filter by name.</p>`
      : terms.length ? `<p class="note">${matching.length.toLocaleString()} of ${lst.characters.length.toLocaleString()} characters match.</p>` : ''}`;
}

/* A collection's list (characters, retyped names, rules) as editable tables. `fresh=false` redraws from what was fetched
   last (ticking, filtering and opening a profile change only what is shown; the server needs asking only after a change). */
async function listDialog(lid, tab = 'characters', fresh = true) {
  const lst = !fresh && LIST.lid === lid && LIST.data ? LIST.data : await api('GET', `/api/library/list/${encodeURIComponent(lid)}`);
  LIST.data = lst;
  const C = L.collections;
  const title = lid === '*' ? 'All books' : (C.collections.find(c => c.id === lid) || {}).name || 'Collection';
  const targets = [['*', 'All books'], ...C.collections.map(c => [c.id, c.name])].filter(([id]) => id !== lid);
  const moveSel = (kind, id) => targets.length ? `<select data-move="${esc(id)}" data-kind="${kind}" aria-label="Move to another list"><option value="">Move to…</option>${targets.map(([t, n]) => `<option value="${esc(t)}">${esc(n)}</option>`).join('')}</select>` : '';
  const del = (kind, id) => `<button class="link danger" data-del="${esc(id)}" data-kind="${kind}">Delete</button>`;
  const tabs = [['characters', 'Characters'], ['retypes', 'Retyped names'], ['rules', 'Saved rules']];
  let body;
  if (LIST.lid !== lid) Object.assign(LIST, { lid, ticks: new Set(), open: new Set(), q: '', all: false, data: lst });
  if (tab === 'characters') {
    body = charactersHTML(lid, lst, moveSel, del);
  } else if (tab === 'retypes') {
    body = lst.retypes.length ? `<table class="lt"><thead><tr><th>Name</th><th>Type</th><th>Was</th><th>From</th><th></th></tr></thead><tbody>
      ${lst.retypes.map(e => `<tr data-id="${esc(e.id)}" data-kind="retypes"><td><input data-f="text" value="${esc(e.text)}" aria-label="Name"></td>
        <td><input data-f="cat" value="${esc(e.cat)}" style="width:56px" aria-label="Type"></td><td class="num">${esc((e.from || []).join(', '))}</td>
        <td class="num">${esc((e.books || []).join(', '))}</td><td class="la">${moveSel('retypes', e.id)}${del('retypes', e.id)}</td></tr>`).join('')}</tbody></table>`
      : '<p class="muted">No retyped names yet. When every mention of a name has been given another type (Baker Street → FAC), it appears here.</p>';
  } else {
    body = lst.rules.length ? `<table class="lt"><thead><tr><th>Rule</th><th>From</th><th></th></tr></thead><tbody>
      ${lst.rules.map(e => `<tr data-id="${esc(e.id)}" data-kind="rules"><td><input data-f="label" value="${esc(e.label)}" aria-label="Description" style="width:100%"></td>
        <td class="num">${esc((e.books || []).join(', '))}</td><td class="la">${moveSel('rules', e.id)}${del('rules', e.id)}</td></tr>`).join('')}</tbody></table>`
      : '<p class="muted">No rules yet. In a book, run a Find, choose a change above the results and click “Save as rule…”.</p>';
  }
  modal(`<header><h2 id="mtitle">${esc(title)}</h2></header>
    <div class="mbody"><nav class="ltabs">${tabs.map(([k, l]) => `<button data-tab="${k}" aria-pressed="${k === tab}">${l} <span class="num">${lst[k].length}</span></button>`).join('')}</nav>
      ${body}<datalist id="dl-pron">${PRONOUNS.map(p => `<option value="${p}">`).join('')}</datalist>
      <p class="note" style="margin:0">Changes are saved as you type. Other names are separated by commas. A group in a new book is matched to a character by evidence: these names and titles, and the profile gathered from the books (who it appears with, relations, places, actions, speech).</p></div>
    <footer><button class="btn primary" data-l="close">Done</button></footer>`);
  $('.mbox').classList.add('wide');
  const box = $('.mbox');
  box.onchange = async e => {
    const t = e.target, tr = t.closest('tr[data-id]');
    try {
      if (t.dataset.move) { if (!t.value) return; await api('POST', '/api/library/entry/move', { list: lid, kind: t.dataset.kind, id: t.dataset.move, to: t.value }); toast('Moved'); listDialog(lid, tab); return; }
      if (t.dataset.f && tr) { await api('PATCH', '/api/library/entry', { list: lid, kind: tr.dataset.kind, id: tr.dataset.id, fields: { [t.dataset.f]: t.value } }); LIST.data = null; toast('Saved'); }
    } catch (err) { fail(err); }
  };
  const lq = $('#lq');
  if (lq) lq.oninput = () => {      // filter as you type, keeping the cursor in the box
    clearTimeout(lq._t);
    lq._t = setTimeout(async () => { LIST.q = lq.value; LIST.all = false; await listDialog(lid, tab, false); const i = $('#lq'); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }, 250);
  };
  $('#modal').onclick = async e => {
    const tb = e.target.closest('[data-tab]'); if (tb) { listDialog(lid, tb.dataset.tab); return; }
    if (e.target.closest('[data-lall]')) { LIST.all = true; listDialog(lid, tab, false); return; }
    const tk = e.target.closest('[data-tick]');
    if (tk) {
      const ids = tk.dataset.tick === '*' ? listMatching(lst).map(c => c.id) : null;     // "Tick all" means all that match the filter
      if (ids) LIST.ticks = tk.checked ? new Set([...LIST.ticks, ...ids]) : new Set([...LIST.ticks].filter(x => !ids.includes(x))); else tk.checked ? LIST.ticks.add(tk.dataset.tick) : LIST.ticks.delete(tk.dataset.tick);
      listDialog(lid, tab, false); return;
    }
    const op = e.target.closest('[data-prof]');
    if (op) { LIST.open.has(op.dataset.prof) ? LIST.open.delete(op.dataset.prof) : LIST.open.add(op.dataset.prof); listDialog(lid, tab, false); return; }
    if (e.target.closest('[data-bulk="del"]')) {
      const ids = [...LIST.ticks];
      if (!ids.length || !confirm(`Delete ${ids.length} character${ids.length === 1 ? '' : 's'} from this list? Groups linked to them in the books keep their names and settings.`)) return;
      try { const r = await api('POST', '/api/library/entries/delete', { list: lid, kind: 'characters', ids }); LIST.ticks.clear(); toast(`Deleted ${r.deleted}`); listDialog(lid, tab); } catch (err) { fail(err); }
      return;
    }
    const d = e.target.closest('[data-del]');
    if (d) { try { await api('POST', '/api/library/entry/delete', { list: lid, kind: d.dataset.kind, id: d.dataset.del }); listDialog(lid, tab); } catch (err) { fail(err); } return; }
    if (e.target.closest('[data-l="close"]')) { closeModal(); load().catch(fail); }
  };
}
$('#lib').addEventListener('change', async e => {
  const t = e.target; if (t.dataset.member == null) return;
  const key = t.dataset.member;
  if (t.value === '__new') {
    t.value = L.collections.members[key] || '';
    nameDialog({ title: 'New collection', ok: 'Create', note: 'This book goes into it. Add the other books of the series with the menu on each book.',
      onOk: n => api('POST', '/api/library/collections', { name: n, key }) });
    return;
  }
  try { await api('POST', '/api/library/member', { key, collection: t.value || null }); await load(); toast(t.value ? 'Added to the collection' : 'Removed from the collection'); }
  catch (err) { fail(err); }
});

/* ------------------------------------------------------------ actions */
$('#lib').addEventListener('click', async e => {
  const a = e.target.closest('[data-act]'); if (!a) return;
  const act = a.dataset.act;
  try {
    if (act === 'editbook') { VIEW.editing = a.dataset.key; renderList(); const i = $(`[data-key="${CSS.escape(a.dataset.key)}"] [data-bf="title"]`); if (i) i.focus(); }
    else if (act === 'editdone') { VIEW.editing = null; renderList(); }
    else if (act === 'reveal') await api('POST', '/api/library/reveal', { path: a.dataset.path });
    else if (act === 'start') startDialog(L.books.find(b => b.key === a.dataset.key));
    else if (act === 'open') { await api('POST', '/api/library/open', { name: a.dataset.name }); location.href = '/'; }
    else if (act === 'rename') renameDialog(a.dataset.name);
    else if (act === 'discard') discardDialog(a.dataset.name);
    else if (act === 'newcoll') nameDialog({ title: 'New collection', ok: 'Create', note: 'Then choose it on each book that belongs to it.',
      onOk: async n => { const r = await api('POST', '/api/library/collections', { name: n }); VIEW.coll = r.collection.id; saveView(); } });
    else if (act === 'collrename') { const c = L.collections.collections.find(x => x.id === a.dataset.id); nameDialog({ title: `Rename “${c.name}”`, value: c.name, ok: 'Rename', onOk: n => api('PATCH', `/api/library/collections/${c.id}`, { name: n }) }); }
    else if (act === 'colldel') {
      const c = L.collections.collections.find(x => x.id === a.dataset.id);
      if (!confirm(`Delete the collection “${c.name}” and its list (${c.counts.characters} characters, ${c.counts.retypes} retyped names, ${c.counts.rules} rules)? The books and their working copies stay as they are.`)) return;
      await api('DELETE', `/api/library/collections/${c.id}`); await load(); toast('Collection deleted');
    }
    else if (act === 'colllist') listDialog(a.dataset.id);
  } catch (err) { fail(err); }
});

load().catch(fail);
