'use strict';
/* ------------------------------------------------------------ helpers */
/* Short for querySelector. */
const $ = (s, r = document) => r.querySelector(s);
/* HTML-escape a value for putting into markup (every dynamic string goes through this). */
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
/* A stable colour hue (0-359) for a group id, so each group keeps its colour everywhere. */
const hue = id => Math.round((((id * 137.508) % 360) + 360) % 360);
/* Shorten text to n characters with an ellipsis. */
const trim = (s, n = 60) => (s = String(s ?? '')).length > n ? s.slice(0, n - 1).trimEnd() + '…' : s;
/* Display names of the three span layers. */
const LAYER_LABEL = { entities: 'Entity', supersenses: 'Supersense', quotes: 'Quote' };
/* Token fields that can be edited in the table, in column order. */
const EDIT_FIELDS = ['word', 'lemma', 'pos', 'tag', 'dep', 'head', 'event'];
/* Display names of the editable token fields. */
const FIELD_LABEL = { word: 'Word', lemma: 'Lemma', pos: 'POS', tag: 'Fine POS', dep: 'Dependency', head: 'Head', event: 'Event' };
/* The table's columns: key and header label (which ones show is remembered in localStorage). */
const COLS = [
  { k: 'i', label: 'Token' }, { k: 'loc', label: 'In sent.' }, { k: 'word', label: 'Word' },
  { k: 'lemma', label: 'Lemma' }, { k: 'pos', label: 'POS' }, { k: 'tag', label: 'Fine POS' },
  { k: 'dep', label: 'Dep' }, { k: 'head', label: 'Head' }, { k: 'event', label: 'Event' },
  { k: 'entities', label: 'Entities' }, { k: 'supersenses', label: 'Supersense' }, { k: 'quotes', label: 'Quote' },
];

/* The working copy this page shows (sent as X-Book so the server can refuse edits meant for another book). */
let BOOK = null;  // the working copy this page shows; the server refuses edits meant for another one
/* Call the server: JSON in, JSON out; throws an Error with the server's message on failure and tracks the save status. */
async function api(method, url, body) {
  const headers = body ? { 'Content-Type': 'application/json' } : {};
  if (BOOK) headers['X-Book'] = BOOK;
  const write = method !== 'GET';
  if (write) { SAVE.writing++; paintSave(); }
  let r;
  try { r = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined }); }
  catch (e) { if (write) SAVE.failed = true; throw e; }  // the server can't be reached: the change was not saved
  finally { if (write) { SAVE.writing--; paintSave(); } }
  noteSaved(r, write);
  let j = null;
  try { j = await r.json(); } catch { }
  if (r.status === 409) { staleBook(j && j.detail); throw new Error((j && j.detail) || 'No book is open'); }
  if (!r.ok) throw new Error((j && j.detail) || `${r.status} ${r.statusText}`);
  return j;
}

/* ------------------------------------------------------------ save status
   Every edit is committed to the working copy at once. The server sends the time the file was last written
   (X-Last-Saved) with each reply; the header badge shows it, "Saving…" while a change is on its way, and
   "Not saved" if the server failed or can't be reached. */
const SAVE = { ts: null, writing: 0, failed: false };
/* Read X-Last-Saved from a reply and update the badge; a write that got a 5xx counts as not saved. */
function noteSaved(r, write) {
  const t = parseFloat(r.headers.get('X-Last-Saved'));
  if (t) SAVE.ts = t * 1000;
  if (write) SAVE.failed = r.status >= 500;
  paintSave();
}
/* 'just now', '3 min ago' … for the save badge. */
function ago(ms) {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 10) return 'just now';
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ms).toLocaleDateString([], { day: 'numeric', month: 'short' });
}
/* Redraw the header badge: Saving…, Saved <time ago>, or Not saved. */
function paintSave() {
  const el = document.getElementById('saveStat'); if (!el) return;
  el.classList.toggle('busy', SAVE.writing > 0 && !SAVE.failed);
  el.classList.toggle('fail', SAVE.failed);
  if (SAVE.failed) el.textContent = 'Not saved';
  else if (SAVE.writing > 0) el.textContent = 'Saving…';
  else el.textContent = SAVE.ts ? `Saved ${ago(SAVE.ts)}` : '';
  el.title = SAVE.failed ? 'The last change could not be saved. Check that the editor is still running.'
    : SAVE.ts ? `Last saved ${new Date(SAVE.ts).toLocaleString()}. Every change is saved as you make it.` : '';
}
setInterval(paintSave, 30000);
/* Show the banner when this page belongs to a book that is no longer the open one. */
function staleBook(msg) {
  const b = $('#banner');
  b.innerHTML = `<span>${esc(msg || 'This book is no longer open.')}</span><button class="btn sm" onclick="location.href='/'">Reload</button><button class="btn sm" onclick="location.href='/library'">Library</button>`;
  b.hidden = false;
}
/* Show a short message at the bottom, optionally with an action link (e.g. Undo). */
function toast(msg, action) {
  const t = $('#toast');
  t.innerHTML = `<span>${esc(msg)}</span>${action ? `<button class="link">${esc(action.label)}</button>` : ''}`;
  if (action) $('.link', t).onclick = action.run;
  t.classList.add('on');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('on'), action ? 8000 : 3200);
}
/* Show an error (Error object or text) as a toast. */
const fail = e => toast(e.message || String(e));

/* ------------------------------------------------------------ state */
/* The page's state: book info, current page of sentences (win), selection, view, Find, filters. Per-view state hangs off S.ch (Entities) and S.qv (Quotes). */
const S = {
  info: null, clusters: [], clusterById: new Map(), win: null, flags: new Map(),
  sent: 0, n: +(localStorage.getItem('bne.n') || 15),
  cols: JSON.parse(localStorage.getItem('bne.cols') || 'null') || Object.fromEntries(COLS.map(c => [c.k, true])),
  sel: null, cursor: null, pick: null, mode: 'table', msel: new Set(),
  view: localStorage.getItem('bne.view') || 'table',
  textView: localStorage.getItem('bne.textView') || (['table', 'annot'].includes(localStorage.getItem('bne.view')) ? localStorage.getItem('bne.view') : 'table'),
  ch: { cat: '', q: '', hidden: false, sort: 'mentions', sel: null, checked: new Set(), offset: 0, mchecked: new Set(),
    cmpA: null, cmpB: null, cmpPick: null },
  qv: { speaker: 'all', q: '', offset: 0, checked: new Set() },
  layers: Object.assign({ entities: true, supersenses: true, quotes: true, attrib: true, deps: false, lemma: false, pos: false, tag: false, event: false },
    JSON.parse(localStorage.getItem('bne.layers') || '{}')),
  find: { layer: 'tokens', field: 'pos', op: 'eq', value: '', offset: 0, res: null },
};
/* The BookNLP tag list for 'layer.field', or null when the field has none. */
const tagset = key => S.info.tagsets[key] || null;
/* Is this value in the field's tag list (true for fields without a list)? */
const inTagset = (key, v) => { const t = tagset(key); return !t || t.includes(v); };

/* ------------------------------------------------------------ loading */
/* Start-up: load the book's info and groups, restore the last position, draw the first page. */
async function init() {
  try {
    S.info = await api('GET', '/api/info');
    BOOK = S.info.project_name;
  } catch (e) {
    if (/No book is open/.test(e.message)) { location.href = '/library'; return; }
    $('#main').innerHTML = `<p style="padding:20px">Couldn’t reach the editor server: ${esc(e.message)}</p>`; return;
  }
  document.title = `${S.info.book_id} · Luna - a BookNLP editor`;
  $('#bookid').textContent = S.info.book_id;
  $('#perPage').value = String(S.n);
  buildDatalists();
  await loadClusters();
  await loadFlags();
  const saved = JSON.parse(localStorage.getItem(`bne.pos.${BOOK}`) || 'null');
  if (saved) S.sent = saved.sent || 0;
  setupFind();
  await loadWindow();
  renderPanel();
  updateHistoryButtons(S.info.history);
  setPending(S.info.pending);
  setProgress(S.info.review);
}
/* Create the <datalist>s (tag lists plus values seen in the book) that the inputs suggest from. */
function buildDatalists() {
  const lists = [];
  for (const [key, vals] of Object.entries(S.info.tagsets)) {
    const extra = (S.info.observed[key] || []).filter(v => !vals.includes(v));
    lists.push(`<datalist id="dl-${key.replace('.', '-')}">${vals.concat(extra).map(v => `<option value="${esc(v)}">`).join('')}</datalist>`);
  }
  lists.push('<datalist id="dl-clusters"></datalist>');
  lists.push(`<datalist id="dl-pronouns">${['he/him/his', 'she/her', 'they/them/their', 'xe/xem/xyr/xir', 'ze/zem/zir/hir'].map(v => `<option value="${v}">`).join('')}</datalist>`);
  $('#datalists').innerHTML = lists.join('');
}
/* How many groups the type-ahead lists offer (the biggest ones). A long book has thousands of tiny groups, and a drop-down
   with that many entries is slow to open; any group can still be typed by its exact name or as "#number" (parseCluster). */
const DATALIST_GROUPS = 1000;
/* Fetch every coreference group (for pickers and names in labels). */
async function loadClusters() {
  S.clusters = await api('GET', '/api/clusters');
  S.clusterById = new Map(S.clusters.map(c => [c.id, c]));
  $('#dl-clusters').innerHTML = S.clusters.slice(0, DATALIST_GROUPS).map(c => `<option value="${esc(c.name)} #${c.id}">${c.count} mentions, ${esc(c.cat)}</option>`).join('');
}

/* ------------------------------------------------------------ flags ("check this later", with an optional note) */
/* Fetch every flag into S.flags (key "kind:target") and update the Flags tab's count badge. */
async function loadFlags() {
  const r = await api('GET', '/api/flags');
  S.flags = new Map(r.items.map(f => [`${f.kind}:${f.target}`, f]));
  const b = $('#flagsBadge');
  b.textContent = r.items.length ? r.items.length.toLocaleString() : '';
  b.hidden = !r.items.length;
}
/* A small flag toggle for a mention, quote, group or sentence; shows whether it's already flagged. */
function flagButton(kind, target) {
  const f = S.flags.get(`${kind}:${target}`);
  const title = f ? `Flagged${f.note ? ': ' + f.note : ''} — click to change` : 'Flag this to check on later';
  return `<button type="button" class="fgbtn${f ? ' on' : ''}" data-flag="${kind}:${target}" title="${esc(title)}" aria-pressed="${!!f}">${f ? '⚑' : '⚐'}</button>`;
}
/* The popup to add, edit or remove a flag's note. */
function openFlagPopup(rect, kind, target) {
  const f = S.flags.get(`${kind}:${target}`);
  pop.innerHTML = `<div class="gp"><div class="gpt">${f ? 'Edit flag' : 'Flag this'}</div>
    <textarea id="flagNote" rows="3" placeholder="Note (optional)" aria-label="Note">${esc(f ? f.note : '')}</textarea>
    <div class="row"><button class="btn primary sm" data-fp="save">Save</button>${f ? '<button class="btn sm danger" data-fp="remove">Remove flag</button>' : ''}<button class="btn sm" data-fp="cancel">Cancel</button></div></div>`;
  pop.hidden = false;
  pop.style.left = Math.min(innerWidth - pop.offsetWidth - 8, Math.max(8, rect.left)) + 'px';
  pop.style.top = (rect.bottom + pop.offsetHeight + 8 > innerHeight ? Math.max(8, rect.top - pop.offsetHeight - 6) : rect.bottom + 6) + 'px';
  const note = $('#flagNote'); note.focus(); note.setSelectionRange(note.value.length, note.value.length);
  pop.onclick = async e => {
    const b = e.target.closest('[data-fp]'); if (!b) return;
    try {
      if (b.dataset.fp === 'save') { const r = await api('PUT', '/api/flag', { kind, target, note: note.value }); closePop(); await afterFlagChange(r); }
      else if (b.dataset.fp === 'remove') { const r = await api('DELETE', '/api/flag', { kind, target }); closePop(); await afterFlagChange(r); }
      else closePop();
    } catch (err) { fail(err); }
  };
  pop.onkeydown = e => { if (e.key === 'Escape') closePop(); e.stopPropagation(); };
}
/* After a flag changes: refresh the cache, show the undo label, and redraw whatever's on screen. */
async function afterFlagChange(r) {
  updateHistoryButtons(r); toast(r.undo || 'Saved');
  await loadFlags();
  renderMain();
  await renderPanel();
}
$('#app').addEventListener('click', e => {
  const b = e.target.closest('[data-flag]'); if (!b) return;
  e.stopPropagation();
  const i = b.dataset.flag.indexOf(':');
  openFlagPopup(b.getBoundingClientRect(), b.dataset.flag.slice(0, i), +b.dataset.flag.slice(i + 1));
});
/* The Flags view: every flag, oldest first, with its note and a way to jump to it or remove it. */
async function renderFlagsView() {
  const r = await api('GET', '/api/flags');
  const row = f => `<li class="frow"><span class="fk">${esc(f.label)}</span>
      ${f.text != null ? `<span class="mctx">${esc(trim(f.text, 90))}</span>` : ''}
      ${f.note ? `<span class="fnote">${esc(f.note)}</span>` : '<span class="muted">No note</span>'}
      <span class="row"><button class="link" data-frow="${f.kind}:${f.target}" data-fs="${f.s ?? ''}" data-fgroup="${f.kind === 'groups' ? f.target : ''}">${f.kind === 'groups' ? 'Open' : 'Show in text'}</button>
      <button class="link" data-flag="${f.kind}:${f.target}">Edit</button></span></li>`;
  $('#main').innerHTML = `<div class="overview wide"><h2>Flags</h2>
    <p class="note" style="margin-top:0">Everything you flagged to check on later, oldest first. Flag a mention, quote, group or sentence from its side panel, its page, or the ✓ beside a sentence.</p>
    ${r.items.length ? `<ul class="flist">${r.items.map(row).join('')}</ul>` : '<p class="muted">Nothing flagged yet.</p>'}</div>`;
}
$('#main').addEventListener('click', e => {
  const a = e.target.closest('[data-frow]'); if (!a || S.view !== 'flags') return;
  if (a.dataset.fgroup) { openGroup(+a.dataset.fgroup); return; }
  if (a.dataset.fs !== '') goTo(+a.dataset.fs).catch(fail);
});
/* Load one page of sentences (from S.sent, or centred on `tok`..`end`) and redraw; remembers the position per book. */
async function loadWindow(opts = {}) {
  const keep = opts.keepScroll ? $('#main').scrollTop : null;
  const q = opts.tok != null ? `tok=${opts.tok}${opts.end != null ? `&tok_end=${opts.end}` : ''}` : `sent=${S.sent}`;
  S.win = await api('GET', `/api/window?${q}&n=${S.n}`);
  S.pendingSet = new Set(S.win.pending || []);
  S.sent = S.win.first;
  replaceNav();
  localStorage.setItem(`bne.pos.${BOOK}`, JSON.stringify({ sent: S.sent }));
  renderRange();
  if (S.mode === 'table') renderMain();
  if (keep != null) $('#main').scrollTop = keep;
}
/* After an edit: reload groups, suggestions count and the page (keeping scroll), re-run Find if open, redraw the side
   panel, and refresh the pronoun/quote pass bar if a manual edit changed what it's showing. */
async function refresh(touchedSpans = true) {
  if (touchedSpans) await loadClusters();
  api('GET', '/api/pending').then(r => setPending(r.pending)).catch(() => { });
  await loadWindow({ keepScroll: true });
  if (S.mode === 'find' && S.find.res) await runFind(S.find.offset, true);
  await renderPanel();
  await syncPassItems();
}
/* Update the 'S first–last of n' label and the enabled state of the page buttons. */
function renderRange() {
  const w = S.win;
  $('#range').textContent = `S ${w.first}–${w.last} of ${w.n_sentences - 1}`;
  $('#range').title = `Sentences ${w.first} to ${w.last}, of ${w.n_sentences} (numbered from 0)`;
  $('#prevBtn').disabled = w.first === 0;
  $('#nextBtn').disabled = w.last >= w.n_sentences - 1;
}

/* ------------------------------------------------------------ grid */
/* Index spans by token position so each table row can list the spans on it. */
function spansByToken(list) {
  const m = new Map();
  for (const sp of list) for (let i = sp.s; i <= sp.e; i++) { if (!m.has(i)) m.set(i, []); m.get(i).push(sp); }
  return m;
}
/* The entity label button in the table (a compact continuation marker on later tokens of a span). */
function entChip(sp, ord) {
  const h = hue(sp.coref);
  const sel = (S.sel && S.sel.kind === 'span' && S.sel.layer === 'entities' && S.sel.uid === sp.uid ? ' selspan' : '') + (S.msel.has(sp.uid) ? ' msel' : '');
  if (ord !== sp.s) return `<button class="cont${sel}" data-layer="entities" data-uid="${sp.uid}" style="--h:${h}" title="${esc(sp.text)}">${esc(sp.cat)}</button>`;
  return `<button class="chip${sel}" data-layer="entities" data-uid="${sp.uid}" style="--h:${h}" title="${esc(sp.text)} · group ${sp.coref}">${esc(sp.cat)} <span class="k">${esc(sp.prop)}</span> ${esc(trim(sp.name || '#' + sp.coref, 22))}</button>`;
}
/* The supersense label button in the table. */
function ssChip(sp, ord) {
  const h = String(sp.cat).startsWith('verb.') ? 28 : 200;
  const sel = S.sel && S.sel.kind === 'span' && S.sel.layer === 'supersenses' && S.sel.uid === sp.uid ? ' selspan' : '';
  if (ord !== sp.s) return `<button class="cont${sel}" data-layer="supersenses" data-uid="${sp.uid}" style="--h:${h}">${esc(sp.cat)}</button>`;
  const bad = inTagset('supersenses.cat', sp.cat) ? '' : ' title="Not in the supersense list"';
  return `<button class="chip${sel}" data-layer="supersenses" data-uid="${sp.uid}" style="--h:${h}"${bad}>${esc(sp.cat)}</button>`;
}
/* The quote label button in the table (shows the speaker, or 'No speaker'). */
function qChip(sp, ord) {
  const has = sp.char_id != null;
  const h = has ? hue(sp.char_id) : 0;
  const sel = S.sel && S.sel.kind === 'span' && S.sel.layer === 'quotes' && S.sel.uid === sp.uid ? ' selspan' : '';
  if (ord !== sp.s) return `<button class="cont q${has ? '' : ' none'}${sel}" data-layer="quotes" data-uid="${sp.uid}" style="--h:${h}">&nbsp;</button>`;
  return `<button class="chip q${has ? '' : ' none'}${sel}" data-layer="quotes" data-uid="${sp.uid}" style="--h:${h}" title="${esc(sp.text)}">“ ${esc(has ? trim(sp.speaker, 20) : 'No speaker')}</button>`;
}
/* Hide the pronoun/quote pass bar away from the annotation or table view (the pass itself stays paused, not stopped,
   so it picks back up where it left off); show it again on return. Called after every view or mode switch, since the
   bar sits outside #main and so isn't redrawn by renderMain() on its own. */
function syncPassBanner() {
  const b = $('#banner'), active = S.mode === 'table' && (S.view === 'annot' || S.view === 'table');
  if (PASSES.some(p => S[p.key] && b.dataset.kind === p.kind)) b.hidden = !active;
}
/* Draw the current view (table, annotation, entities, quotes or flags) into the main area. */
function renderMain() {
  syncPassBanner();
  document.querySelectorAll('#viewSwitch [data-view]').forEach(b => b.setAttribute('aria-pressed', b.dataset.view === S.view));
  $('#colsBtn').hidden = S.view !== 'table';
  $('#pagerNav').classList.toggle('off', S.view === 'chars' || S.view === 'quotes' || S.view === 'flags' || S.ef.on);
  if (S.view === 'annot' && S.ef.on) renderEntityAnnot().catch(fail);
  else if (S.view === 'annot') renderAnnot();
  else if (S.view === 'chars') renderChars().catch(fail);
  else if (S.view === 'quotes') renderQuotesView().catch(fail);
  else if (S.view === 'flags') renderFlagsView().catch(fail);
  else renderTable();
}
/* Table view: one row per token with sentence headers and the chosen columns. */
function renderTable() {
  const w = S.win, c = S.cols;
  const byEnt = spansByToken(w.entities), bySS = spansByToken(w.supersenses), byQ = spansByToken(w.quotes);
  const mentionOf = new Map();
  for (const q of w.quotes) if (q.ms != null) for (let i = q.ms; i <= q.me; i++) { if (!mentionOf.has(i)) mentionOf.set(i, []); mentionOf.get(i).push(q); }
  const visible = COLS.filter(x => c[x.k]);
  const selRange = selectionRange();
  const rows = [];
  let ti = 0;
  for (const s of w.sentences) {
    const toks = [];
    while (ti < w.tokens.length && w.tokens[ti].ord <= s.end) toks.push(w.tokens[ti++]);
    rows.push(`<tr class="srow${s.reviewed ? ' reviewed' : ''}"><td colspan="${visible.length}"><span class="snum">S ${s.index}</span>${s.para_start ? `<span class="para">¶ ${s.para}</span>` : ''}<span class="stext">${esc(toks.map(t => t.word).join(' '))}</span>${sentActions(s)}${revMark(s)}${toks.length ? flagButton('sentence', toks[0].uid) : ''}</td></tr>`);
    for (const t of toks) {
      const cls = ['trow'];
      if (selRange && t.ord >= selRange[0] && t.ord <= selRange[1]) cls.push('sel');
      if (S.pick && S.pick.type === 'head') cls.push('picktarget');
      const cells = [];
      for (const col of visible) cells.push(cell(col.k, t, s, { byEnt, bySS, byQ, mentionOf }));
      rows.push(`<tr class="${cls.join(' ')}" data-ord="${t.ord}" data-uid="${t.uid}">${cells.join('')}</tr>`);
    }
  }
  $('#main').innerHTML = `<table class="grid"><thead><tr>${visible.map(x => `<th>${esc(x.label)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`;
  paintCursor();
}
/* HTML of one table cell for column k (fields are editable; lists show the spans on the token). */
function cell(k, t, s, m) {
  const cur = S.cursor && S.cursor.ord === t.ord && S.cursor.f === k ? ' cur' : '';
  switch (k) {
    case 'i': return `<td class="i">${t.ord}</td>`;
    case 'loc': return `<td class="i">${t.ord - s.start}</td>`;
    case 'word': return `<td class="w ed${t.manual.includes('word') ? ' man' : ''}${S.pendingSet && S.pendingSet.has(t.uid) ? ' pend' : ''}${cur}" data-f="word" title="${S.pendingSet && S.pendingSet.has(t.uid) ? 'spaCy has suggestions for this token' : 'Add spaces to split this token'}">${esc(t.word)}</td>`;
    case 'head': {
      let txt, cross = '';
      if (t.head_ord == null || t.head_ord === t.ord) txt = 'root';
      else if (t.head_sent === t.sent) txt = `${t.head_ord - s.start}<span class="hw">${esc(t.head_word)}</span>`;
      else { txt = `t${t.head_ord}<span class="hw">${esc(t.head_word)}</span>`; cross = ' cross'; }
      return `<td class="ed hd${cross}${t.manual.includes('head') ? ' man' : ''}${cur}" data-f="head" title="${cross ? 'Head is in another sentence' : ''}">${txt}</td>`;
    }
    case 'lemma': case 'pos': case 'tag': case 'dep': case 'event': {
      const bad = inTagset(`tokens.${k}`, t[k]) ? '' : ' bad';
      return `<td class="ed${t.manual.includes(k) ? ' man' : ''}${bad}${cur}" data-f="${k}"${bad ? ' title="Not in the tag list"' : ''}>${esc(t[k])}</td>`;
    }
    case 'entities': return `<td class="sp">${(m.byEnt.get(t.ord) || []).map(sp => entChip(sp, t.ord)).join('')}</td>`;
    case 'supersenses': return `<td class="sp">${(m.bySS.get(t.ord) || []).map(sp => ssChip(sp, t.ord)).join('')}</td>`;
    case 'quotes': {
      const refs = (m.mentionOf.get(t.ord) || []).map(q => `<button class="mref" data-layer="quotes" data-uid="${q.uid}" style="--h:${hue(q.char_id ?? 0)}" title="Speaker mention of this quote">says “${esc(trim(q.text, 18))}”</button>`);
      return `<td class="sp">${(m.byQ.get(t.ord) || []).map(sp => qChip(sp, t.ord)).join('')}${refs.join('')}</td>`;
    }
  }
  return '<td></td>';
}
/* The selected token range [first, last], or null when nothing is selected. */
function selectionRange() {
  const s = S.sel;
  if (!s) return null;
  if (s.kind === 'token') return [s.ord, s.ord];
  if (s.kind === 'range') return [s.a, s.b];
  if (s.kind === 'span' && s.s != null) return [s.s, s.e];
  return null;
}
/* Mark the spreadsheet cursor cell. */
function paintCursor() {
  document.querySelectorAll('.grid td.cur').forEach(td => td.classList.remove('cur'));
  if (!S.cursor) return;
  const td = document.querySelector(`.grid tr[data-ord="${S.cursor.ord}"] td[data-f="${S.cursor.f}"]`);
  if (td) td.classList.add('cur');
}
/* Re-mark the selected rows/labels without redrawing the whole page. */
function paintSelection() {
  if (!S.sel || S.sel.kind !== 'msel') S.msel.clear();
  if (S.view === 'annot') { paintAnnotSelection(); return; }
  const r = selectionRange();
  document.querySelectorAll('.grid tr.trow').forEach(tr => {
    const o = +tr.dataset.ord;
    tr.classList.toggle('sel', !!r && o >= r[0] && o <= r[1]);
  });
  document.querySelectorAll('.grid [data-layer][data-uid]').forEach(b => {
    b.classList.toggle('selspan', !!S.sel && S.sel.kind === 'span' && S.sel.layer === b.dataset.layer && S.sel.uid === +b.dataset.uid && !b.classList.contains('mref'));
    b.classList.toggle('msel', b.dataset.layer === 'entities' && S.msel.has(+b.dataset.uid) && !b.classList.contains('mref'));
  });
  paintCursor();
}
/* The editable columns that are currently shown, in order (for keyboard movement). */
function editableCols() { return EDIT_FIELDS.filter(f => S.cols[f]); }

/* ------------------------------------------------------------ selection */
/* Select one token. */
function selectToken(ord, uid) { S.sel = { kind: 'token', ord, uid }; }
/* Select an entity, supersense or quote (fetches its bounds) so the side panel shows it. */
async function selectSpan(layer, uid) {
  S.sel = { kind: 'span', layer, uid };
  const d = await api('GET', `/api/span/${layer}/${uid}`);
  S.sel.s = d.s; S.sel.e = d.e;
  return d;
}
/* Jump to token `ord` (or the excerpt ord..end) in the text. The page is loaded so the excerpt sits in the middle with
   context above and below; from a list or from Find results it always re-centres, inside the text it only scrolls. */
async function goTo(ord, then, end) {
  saveNav();
  const fromList = S.view === 'chars' || S.view === 'quotes' || S.view === 'flags' || S.mode === 'find';
  if (S.view === 'chars' || S.view === 'quotes' || S.view === 'flags') { S.view = S.textView || 'annot'; localStorage.setItem('bne.view', S.view); }
  end = end != null && end >= ord ? end : ord;
  if (!S.win || ord < S.win.start || ord > S.win.end || fromList) await loadWindow({ tok: ord, end });
  if (S.mode !== 'table') { S.mode = 'table'; renderMain(); }
  if (then) await then();
  renderMain();
  const mid = end - ord > 120 ? ord : Math.round((ord + end) / 2);  // a very long excerpt is scrolled to its start
  const tr = document.querySelector(`.grid tr[data-ord="${mid}"], #av .tk[data-ord="${mid}"]`) || document.querySelector(`.grid tr[data-ord="${ord}"], #av .tk[data-ord="${ord}"]`);
  if (tr) { tr.scrollIntoView({ block: 'center' }); tr.classList.add('flash'); setTimeout(() => tr.classList.remove('flash'), 1500); }
  renderPanel();
  pushNav();
}

/* ------------------------------------------------------------ inline editing */
/* The inline edit in progress (cell, original value, input), or null. */
let editing = null;
/* Turn a cell (table) or a word/row value (annotation view) into an input; Enter/Tab save, Esc cancels. */
function startEdit(ord, f, initial) {
  const td = S.view === 'annot'
    ? document.querySelector(f === 'word' ? `#av .tk[data-ord="${ord}"] .wd` : `#av .tk[data-ord="${ord}"] .rw[data-f="${f}"]`)
    : document.querySelector(`.grid tr[data-ord="${ord}"] td[data-f="${f}"]`);
  if (!td) return;
  const t = S.win.tokens.find(x => x.ord === ord);
  const s = S.win.sentences.find(x => x.index === t.sent);
  let value;
  if (f === 'head') value = t.head_ord == null || t.head_ord === t.ord ? 'root' : t.head_sent === t.sent ? String(t.head_ord - s.start) : `t${t.head_ord}`;
  else value = t[f] ?? '';
  const list = (f === 'head' || f === 'lemma' || f === 'word') ? '' : `list="dl-tokens-${f}"`;
  td.innerHTML = `<input class="cellin" ${list} value="${esc(initial != null ? initial : value)}" spellcheck="false" aria-label="${FIELD_LABEL[f]}">`;
  const input = $('input', td);
  editing = { ord, f, t, s, input, original: value, done: false };
  input.focus();
  if (f === 'word') { input.value = value; input.setSelectionRange(value.length, value.length); }
  else if (initial == null) input.select(); else input.setSelectionRange(input.value.length, input.value.length);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); commitEdit('down'); }
    else if (e.key === 'Tab') { e.preventDefault(); commitEdit(e.shiftKey ? 'left' : 'right'); }
    else if (e.key === 'Escape') { e.preventDefault(); cancelEdit(); }
    e.stopPropagation();
  });
  input.addEventListener('blur', () => { if (editing && editing.input === input && !editing.done) commitEdit(null); });
}
/* Leave inline editing without saving. */
function cancelEdit() { if (!editing) return; editing.done = true; editing = null; renderMain(); $('#main').focus(); }
/* Read a typed head: a position in the sentence, 't' + token number, or root. */
function parseHead(v, t, s) {
  v = v.trim().toLowerCase();
  if (v === 'root' || v === 'r' || v === 'self') return t.ord;
  let m = v.match(/^t(\d+)$/);
  if (m) return +m[1];
  m = v.match(/^(\d+)$/);
  if (m) { const o = s.start + +m[1]; if (o > s.end) throw new Error(`Sentence ${s.index} has only ${s.end - s.start + 1} tokens`); return o; }
  throw new Error('Head: type a position in the sentence, t followed by a token number, or root');
}
/* Save the inline edit (splitting the token for a word change), then move the cursor if asked. */
async function commitEdit(move) {
  const ed = editing; if (!ed || ed.done) return;
  ed.done = true; editing = null;
  const v = ed.input.value.trim();
  if (ed.f === 'word') {
    const nv = v.replace(/\s+/g, ' ');
    if (nv !== ed.original) {
      try { await splitWord(ed.t, nv); } catch (e) { fail(e); renderMain(); }
    } else renderMain();
    $('#main').focus();
    return;
  }
  if (v !== ed.original) {
    try {
      const body = ed.f === 'head' ? { head_ord: parseHead(v, ed.t, ed.s) } : { [ed.f]: v };
      if (ed.f !== 'head' && ed.f !== 'lemma' && !inTagset(`tokens.${ed.f}`, v)) toast(`“${v}” isn’t in the ${FIELD_LABEL[ed.f]} list. Saved and flagged.`);
      const h = await api('PATCH', `/api/token/${ed.t.uid}`, body);
      updateHistoryButtons(h);
      await refresh(false);
    } catch (e) { fail(e); renderMain(); }
  } else renderMain();
  if (move && S.view === 'table') moveCursor(move);
  $('#main').focus();
}
/* Move the spreadsheet cursor and select that token. */
function moveCursor(dir) {
  if (!S.cursor) return;
  const cols = editableCols();
  let ci = cols.indexOf(S.cursor.f);
  const ords = S.win.tokens.map(t => t.ord);
  let oi = ords.indexOf(S.cursor.ord);
  if (dir === 'down') oi = Math.min(ords.length - 1, oi + 1);
  if (dir === 'up') oi = Math.max(0, oi - 1);
  if (dir === 'right') ci = Math.min(cols.length - 1, ci + 1);
  if (dir === 'left') ci = Math.max(0, ci - 1);
  S.cursor = { ord: ords[oi], f: cols[ci] };
  const t = S.win.tokens[oi];
  if (!S.sel || S.sel.kind !== 'token' || S.sel.ord !== t.ord) { selectToken(t.ord, t.uid); paintSelection(); renderPanel(); }
  else paintCursor();
  const td = document.querySelector('.grid td.cur');
  if (td) td.scrollIntoView({ block: 'nearest' });
}

/* ------------------------------------------------------------ panel */
/* Counts side-panel draws, so a slow reply for an old selection can't overwrite a newer panel. */
let panelSeq = 0;
/* Draw the side panel for the current selection (token, span, range, several mentions, or the overview). */
async function renderPanel() {
  const p = $('#panel'), seq = ++panelSeq;
  const s = S.sel;
  try {
    let html;
    if (!s) html = overviewHTML();
    else if (s.kind === 'token') html = tokenPanel(await api('GET', `/api/token/${s.uid}`));
    else if (s.kind === 'span') {
      const d = await api('GET', `/api/span/${s.layer}/${s.uid}`);
      s.s = d.s; s.e = d.e;
      html = spanPanel(s.layer, d);
    } else if (s.kind === 'range') html = rangePanel(s.a, s.b);
    else if (s.kind === 'msel') html = await mselPanel();
    if (seq !== panelSeq) return;
    p.innerHTML = html;
    afterPanel();
  } catch (e) {
    if (seq !== panelSeq) return;
    S.sel = null; p.innerHTML = overviewHTML(); toast(e.message);
  }
}
/* The words around a selection with the selected ones in bold. */
function ctxHTML(words, start, a, b) {
  return words.map((w, i) => { const o = start + i; return o >= a && o <= b ? `<b>${esc(w)}</b>` : esc(w); }).join(' ');
}
/* A labelled input for the side panel, flagged 'edited' and marking values outside the tag list. */
function fieldInput(name, key, value, manual, opts = {}) {
  const list = opts.list ? `list="${opts.list}"` : '';
  const bad = opts.tagset && value != null && !inTagset(opts.tagset, value) ? ' bad' : '';
  return `<label for="f-${key}">${esc(name)}${manual ? '<span class="edited" title="Edited by hand">edited</span>' : ''}</label>
    <input id="f-${key}" data-pf="${key}" ${list} value="${esc(value ?? '')}" spellcheck="false" class="${bad.trim()}" ${bad ? 'title="Not in the tag list"' : ''}>`;
}
/* Side panel for one token: its fields, head, dependents, sentence actions and the spans on it. */
function tokenPanel(t) {
  const loc = t.ord - t.sent_start_ord;
  const headTxt = t.head_ord == null || t.head_ord === t.ord ? 'root (itself)' : `t${t.head_ord} “${esc(t.head_word)}”${t.head_ord < t.sent_start_ord || t.head_ord > t.sent_end_ord ? ' — other sentence' : ''}`;
  const spans = [];
  for (const layer of ['entities', 'supersenses', 'quotes']) for (const sp of t.spans[layer]) spans.push(spanCard(layer, sp));
  const extra = Object.entries(t.extra || {});
  return `<h2>${esc(t.word)}</h2>
    <div class="sub">Token ${t.ord} · sentence ${t.sent}, position ${loc}</div>
    <div class="ctx" role="button" tabindex="0" data-act="goto" data-ord="${t.ord}" title="Show in the text">${ctxHTML(t.context.split(' '), t.context_start, t.ord, t.ord)}</div>
    <h3>Token</h3>
    <div class="fields" data-kind="token" data-uid="${t.uid}">
      ${fieldInput('Word', 'word', t.word, t.manual.includes('word'))}
      ${fieldInput('Lemma', 'lemma', t.lemma, t.manual.includes('lemma'))}
      ${fieldInput('POS', 'pos', t.pos, t.manual.includes('pos'), { list: 'dl-tokens-pos', tagset: 'tokens.pos' })}
      ${fieldInput('Fine POS', 'tag', t.tag, t.manual.includes('tag'), { list: 'dl-tokens-tag', tagset: 'tokens.tag' })}
      ${fieldInput('Dependency', 'dep', t.dep, t.manual.includes('dep'), { list: 'dl-tokens-dep', tagset: 'tokens.dep' })}
      <label>Head${t.manual.includes('head') ? '<span class="edited">edited</span>' : ''}</label>
      <div class="row"><span class="mono">${headTxt}</span><button class="btn sm" data-act="pickhead" data-uid="${t.uid}">Pick…</button>
        ${t.head_ord != null && t.head_ord !== t.ord ? `<button class="link" data-act="goto" data-ord="${t.head_ord}">Show</button>` : ''}</div>
      ${fieldInput('Event', 'event', t.event, t.manual.includes('event'), { list: 'dl-tokens-event', tagset: 'tokens.event' })}
      <label>Text offsets</label><div class="ro">${t.char_start}–${t.char_end}</div>
      ${extra.map(([k, v]) => `<label>${esc(k)}</label><div class="ro">${esc(v)}</div>`).join('')}
    </div>
    <p class="note">Add spaces to the word to split the token; the spelling itself can’t change. To merge tokens, select them and choose Merge.</p>
    <h3>Sentence and paragraph</h3>
    ${structureHTML(t, loc)}
    <h3>Dependents</h3>
    ${t.dependents.length ? `<div class="deps">${t.dependents.map(d => `<button data-act="goto" data-ord="${d.ord}"><span class="d">${esc(d.dep)}</span>${esc(d.word)}</button>`).join('')}</div>` : '<p class="note">None.</p>'}
    <h3>Annotations on this token</h3>
    ${spans.length ? `<div class="cards">${spans.join('')}</div>` : '<p class="note">None. Shift-click another token to select a range and add one.</p>'}
    ${t.as_speaker_mention.length ? `<h3>Speaker mention of</h3><div class="cards">${t.as_speaker_mention.map(q => spanCard('quotes', q)).join('')}</div>` : ''}
    <h3>Add on this token</h3>${addForms(t.ord, t.ord)}`;
}
/* A small card describing a span; click it to select the span and show it in the text. */
function spanCard(layer, sp) {
  let meta = '';
  if (layer === 'entities') meta = `${sp.cat} ${sp.prop} · ${esc(sp.name || '#' + sp.coref)}`;
  if (layer === 'supersenses') meta = esc(sp.cat);
  if (layer === 'quotes') meta = `spoken by ${esc(sp.speaker || 'no one')}`;
  return `<div class="card" data-act="span" data-layer="${layer}" data-uid="${sp.uid}"><div class="num">${LAYER_LABEL[layer]} · ${meta} · t${sp.s}–${sp.e}</div><div class="t">${esc(trim(sp.text, 120))}</div></div>`;
}
/* How a group appears in an input: 'Name #id'. */
function clusterValue(id) { if (id == null) return ''; const c = S.clusterById.get(id); return c ? `${c.name} #${id}` : `#${id}`; }
/* Read a group typed as 'Name #id', a number, an exact name, or empty/none (null). */
function parseCluster(v) {
  v = String(v).trim();
  if (!v || /^(none|no one|-)$/i.test(v)) return null;
  let m = v.match(/#(-?\d+)\s*$/) || v.match(/^(-?\d+)$/);
  if (m) return +m[1];
  const c = S.clusters.find(x => x.name.toLowerCase() === v.toLowerCase());
  if (c) return c.id;
  throw new Error(`No group called “${v}”. Pick one from the list, or type its number.`);
}
/* Side panel for an entity, supersense or quote: fields, boundary nudges, extras and actions. */
function spanPanel(layer, d) {
  const man = f => d.manual.includes(f);
  const bounds = `<h3>Boundaries</h3>
    <div class="nudge"><span>Start t${d.s}</span><button class="btn sm" data-act="nudge" data-which="s" data-d="-1" aria-label="Start one token earlier">‹</button><button class="btn sm" data-act="nudge" data-which="s" data-d="1" aria-label="Start one token later">›</button><span></span>
      <span>End t${d.e}</span><button class="btn sm" data-act="nudge" data-which="e" data-d="-1" aria-label="End one token earlier">‹</button><button class="btn sm" data-act="nudge" data-which="e" data-d="1" aria-label="End one token later">›</button><span></span></div>
    <p class="note">Or shift-click a range of tokens and choose “Set as boundaries”.</p>`;
  let fields = '';
  if (layer === 'entities') {
    fields = `<div class="fields" data-kind="span" data-layer="entities" data-uid="${d.uid}">
      <label>Group${man('coref') ? '<span class="edited">edited</span>' : ''}</label>
      <div class="row">${groupFieldChip(d.coref, 'No group')}<button class="btn sm" data-act="newgroup">New</button></div>
      ${fieldInput('Type', 'cat', d.cat, man('cat'), { list: 'dl-entities-cat', tagset: 'entities.cat' })}
      ${fieldInput('Mention', 'prop', d.prop, man('prop'), { list: 'dl-entities-prop', tagset: 'entities.prop' })}
    </div>
    <p class="note">Group ${d.coref} has ${d.cluster_count} mention${d.cluster_count === 1 ? '' : 's'}. <button class="link" data-act="findgroup" data-coref="${d.coref}">List them</button></p>
    ${d.quotes_via ? `<p class="note">${d.quotes_via} quote${d.quotes_via > 1 ? 's are' : ' is'} attributed through this mention and follow${d.quotes_via > 1 ? '' : 's'} its changes.</p>` : ''}`;
  } else if (layer === 'supersenses') {
    fields = `<div class="fields" data-kind="span" data-layer="supersenses" data-uid="${d.uid}">
      ${fieldInput('Category', 'cat', d.cat, man('cat'), { list: 'dl-supersenses-cat', tagset: 'supersenses.cat' })}</div>`;
  } else {
    fields = `<div class="fields" data-kind="span" data-layer="quotes" data-uid="${d.uid}">
      <label>Speaker${man('char_id') ? '<span class="edited">edited</span>' : ''}</label>
      <div class="row">${groupFieldChip(d.char_id, 'No speaker')}</div>
      <label>Mention</label>
      <div class="row">${d.ms != null ? `<button class="link" data-act="goto" data-ord="${d.ms}">“${esc(d.m_phrase)}” t${d.ms}</button>` : '<span class="muted">none</span>'}
        <button class="btn sm" data-act="pickmention" data-uid="${d.uid}">Pick…</button></div>
    </div>
    <p class="note">Choosing a speaker links the nearest mention of that group outside the quote. “Assign + Quote-Rule” also moves I/me/you… inside this quote to match. Use Pick to choose a specific mention.</p>`;
  }
  return `<h2>${LAYER_LABEL[layer]}${layer !== 'supersenses' ? flagButton(layer, d.uid) : ''}</h2>
    <div class="sub">Tokens ${d.s}–${d.e}</div>
    <div class="ctx" role="button" tabindex="0" data-act="goto" data-ord="${d.s}" data-end="${d.e}" title="Show in the text">${ctxHTML(d.context, d.context_start, d.s, d.e)}</div>
    <h3>Fields</h3>${fields}${bounds}
    ${spanExtras(layer, d)}
    <h3>Actions</h3><div class="row"><button class="btn" data-act="goto" data-ord="${d.s}" data-end="${d.e}">Show in text</button><button class="btn danger" data-act="delspan" data-layer="${layer}" data-uid="${d.uid}">Delete ${LAYER_LABEL[layer].toLowerCase()}</button></div>`;
}
/* The 'add entity / supersense / quote' forms for a token or range. */
function addForms(a, b) {
  return `<div class="addform" data-a="${a}" data-b="${b}"><h4>Entity</h4>
      <div class="row"><input data-add="cat" list="dl-entities-cat" value="PER" style="width:84px" aria-label="Entity type">
      <input data-add="prop" list="dl-entities-prop" value="PROP" style="width:84px" aria-label="Mention type">
      <input data-add="coref" list="dl-clusters" placeholder="Group (blank = new)" style="flex:1" aria-label="Group"></div>
      <div><button class="btn sm primary" data-act="add" data-layer="entities">Add entity</button></div></div>
    <div class="addform" data-a="${a}" data-b="${b}"><h4>Supersense</h4>
      <div class="row"><input data-add="cat" list="dl-supersenses-cat" placeholder="e.g. noun.person" style="flex:1" aria-label="Supersense"></div>
      <div><button class="btn sm primary" data-act="add" data-layer="supersenses">Add supersense</button></div></div>
    <div class="addform" data-a="${a}" data-b="${b}"><h4>Quote</h4>
      <div class="row"><input data-add="char_id" list="dl-clusters" placeholder="Speaker (optional)" style="flex:1" aria-label="Speaker"></div>
      <div><button class="btn sm primary" data-act="add" data-layer="quotes">Add quote</button></div></div>`;
}
/* Side panel for a range of tokens: set boundaries, merge, mentions in it, add annotation. */
function rangePanel(a, b) {
  const words = S.win.tokens.filter(t => t.ord >= a && t.ord <= b).map(t => t.word).join(' ');
  const spanSel = S.prevSpan;
  return `<h2>Tokens ${a}–${b}</h2>
    <div class="sub">${b - a + 1} tokens selected</div>
    <div class="ctx">${esc(words || '(outside the current page)')}</div>
    ${spanSel ? `<h3>Selected ${LAYER_LABEL[spanSel.layer].toLowerCase()}</h3><div class="row"><button class="btn primary" data-act="setbounds" data-layer="${spanSel.layer}" data-uid="${spanSel.uid}">Set as boundaries of “${esc(trim(spanSel.text, 30))}”</button></div>` : ''}
    ${mergeTokHTML(a, b)}
    ${rangeExtras(a, b)}
    <h3>Add annotation</h3>${addForms(a, b)}`;
}
/* Side panel when nothing is selected: counts, tips and the keyboard shortcuts. */
function overviewHTML() {
  const i = S.info, c = i.counts;
  return `<h2>${esc(i.book_id)}</h2>
    <div class="sub">Working copy: ${esc(i.project)}</div>
    <div class="stats"><div><b>${c.tokens.toLocaleString()}</b>tokens</div><div><b>${i.n_sentences.toLocaleString()}</b>sentences</div>
      <div><b>${c.entities.toLocaleString()}</b>entities</div><div><b>${c.supersenses.toLocaleString()}</b>supersenses</div>
      <div><b>${c.quotes.toLocaleString()}</b>quotes</div><div><b>${i.n_paragraphs.toLocaleString()}</b>paragraphs</div></div>
    ${i.n_offset_mismatches ? `<p class="note">${i.n_offset_mismatches} tokens didn’t match the original text at their offsets on import.</p>` : ''}
    <h3>Working</h3>
    <p class="note" style="margin-top:0">Click a token to see and edit all its fields. Click a coloured label to edit that entity, supersense or quote. Shift-click a second token to select a range and add annotations. A blue dot marks values you’ve edited by hand; wavy underlines mark values outside the tag lists.</p>
    <h3>Keyboard</h3>
    <div class="keys"><kbd>↑ ↓ ← →</kbd><span>move between cells</span><kbd>Enter</kbd><span>edit cell (or just start typing)</span>
      <kbd>Tab</kbd><span>save and move right</span><kbd>Esc</kbd><span>cancel</span><kbd>[ ]</kbd><span>previous / next page</span>
      <kbd>/</kbd><span>find</span><kbd>⌘[</kbd><span>back to where you were</span><kbd>⌘Z</kbd><span>undo</span><kbd>⇧⌘Z</kbd><span>redo</span><kbd>v</kbd><span>switch between table and annotation view</span></div>
    <h3>Annotation view</h3>
    <div class="keys"><kbd>drag</kbd><span>select words, then choose layer and label</span><kbd>⇧ click</kbd><span>extend the selection</span>
      <kbd>⌥ drag</kbd><span>from a word to its new head</span><kbd>⌘ click</kbd><span>select several entity labels, then move or retype them together (also in the table)</span>
      <kbd>1–9</kbd><span>move the selected mentions, or add the selected words, to the group pinned to that key</span>
      <kbd>Esc</kbd><span>stop painting; clear a highlighted group</span><kbd>← →</kbd><span>previous / next word</span>
      <span></span><span>Click a label to edit it; click a row value (lemma, POS…) to change it in place.</span></div>
    <h3>Pronoun pass, Quote pass</h3>
    <div class="keys"><kbd>Enter</kbd><span>keep: it’s right as it is</span><kbd>1–6</kbd><span>move it to a candidate in the bar</span>
      <kbd>G</kbd><span>another group</span><kbd>→ ←</kbd><span>skip to the next / back to the previous</span><kbd>Esc</kbd><span>stop the pass</span></div>`;
}

/* ------------------------------------------------------------ panel events */
$('#panel').addEventListener('change', async e => {
  const inp = e.target.closest('[data-pf]'); if (!inp) return;
  const box = inp.closest('.fields'); const f = inp.dataset.pf; const v = inp.value.trim();
  try {
    let h;
    if (box.dataset.kind === 'token') {
      if (f === 'word') {
        const t = { uid: +box.dataset.uid, word: inp.defaultValue };
        await splitWord(t, v.replace(/\s+/g, ' '));
        return;
      }
      if (f !== 'lemma' && !inTagset(`tokens.${f}`, v)) toast(`“${v}” isn’t in the list. Saved and flagged.`);
      h = await api('PATCH', `/api/token/${box.dataset.uid}`, { [f]: v });
      updateHistoryButtons(h); await refresh(false); return;
    }
    const layer = box.dataset.layer, uid = box.dataset.uid;
    h = await api('PATCH', `/api/span/${layer}/${uid}`, { [f]: v });
    updateHistoryButtons(h); await refresh(true);
  } catch (err) { fail(err); renderPanel(); }
});
$('#panel').addEventListener('change', e => {
  const cb = e.target.closest('input[type=checkbox][data-sop]');
  if (cb) runSop({ ...cb.dataset, on: cb.checked ? '1' : '0' });
});
$('#panel').addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.matches('input')) { e.preventDefault(); e.target.blur(); }
  else if (e.key === 'Enter' && e.target.matches('.ctx[data-act]')) { e.preventDefault(); e.target.click(); }
});
$('#panel').addEventListener('click', async e => {
  const sop = e.target.closest('[data-sop]');
  if (sop && !sop.matches('input[type=checkbox]')) return runSop(sop.dataset);
  const a = e.target.closest('[data-act]'); if (!a) return;
  const act = a.dataset.act;
  try {
    if (act === 'goto') {
      const ord = +a.dataset.ord;
      if (a.classList.contains('ctx') && String(getSelection())) return;  // selecting the text to copy it is not a click
      await goTo(ord, async () => { if (a.dataset.end != null && S.sel && S.sel.kind === 'span') return; const t = S.win.tokens.find(x => x.ord === ord); if (t) selectToken(ord, t.uid); }, a.dataset.end != null ? +a.dataset.end : undefined);
    } else if (act === 'span') {
      const d = await selectSpan(a.dataset.layer, +a.dataset.uid);
      await goTo(d.s, null, d.e);
    } else if (act === 'pickhead') { startPick({ type: 'head', uid: +a.dataset.uid }); }
    else if (act === 'pickmention') { startPick({ type: 'mention', uid: +a.dataset.uid }); }
    else if (act === 'nudge') {
      const s = S.sel; const which = a.dataset.which; const d = +a.dataset.d;
      const body = { [which]: (which === 's' ? s.s : s.e) + d };
      const h = await api('PATCH', `/api/span/${s.layer}/${s.uid}`, body);
      updateHistoryButtons(h); await refresh(true);
    } else if (act === 'newgroup') {
      const h = await api('PATCH', `/api/span/entities/${S.sel.uid}`, { coref: null });
      updateHistoryButtons(h); await refresh(true);
    } else if (act === 'pickgroup') {
      const box = a.closest('.fields'), layer = box.dataset.layer, uid = box.dataset.uid, rect = a.getBoundingClientRect();
      const near = S.sel.s != null ? { a: S.sel.s, b: S.sel.e } : null;
      if (layer === 'quotes') {
        groupPicker(rect, { title: 'Who says this?', allowNew: false, allowNone: true, near,
          actions: [{ key: 'assign_rule', label: 'Assign + Quote-Rule' }, { key: 'assign', label: 'Assign' }],
          onPick: async (target, name, key) => {
            const r = await api('POST', '/api/quotes/speaker', { uids: [+uid], char_id: target, quote_rule: key === 'assign_rule' });
            updateHistoryButtons(r);
            toast(r.moved ? `${r.undo || 'Assigned'} · moved ${r.moved} pronoun${r.moved === 1 ? '' : 's'}` : (r.undo || 'Assigned'));
            await refresh(true);
          } });
      } else {
        groupPicker(rect, { title: 'Move to group', allowNew: false, near, okLabel: 'Move',
          onPick: async target => {
            const h = await api('PATCH', `/api/span/entities/${uid}`, { coref: target });
            updateHistoryButtons(h); await refresh(true);
          } });
      }
    } else if (act === 'findgroup') {
      openFind({ layer: 'entities', field: 'coref', op: 'eq', value: a.dataset.coref });
    } else if (act === 'delspan') {
      const h = await api('DELETE', `/api/span/${a.dataset.layer}/${a.dataset.uid}`);
      S.sel = null; updateHistoryButtons(h); await refresh(true);
    } else if (act === 'add') {
      const form = a.closest('.addform'); const layer = a.dataset.layer;
      const body = { s: +form.dataset.a, e: +form.dataset.b };
      form.querySelectorAll('[data-add]').forEach(i => {
        const k = i.dataset.add, v = i.value.trim();
        if (k === 'coref' || k === 'char_id') body[k] = parseCluster(v); else body[k] = v;
      });
      if (layer === 'supersenses' && !body.cat) throw new Error('Choose a supersense category first');
      const r = await api('POST', `/api/span/${layer}`, body);
      updateHistoryButtons(r.history);
      S.sel = { kind: 'span', layer, uid: r.uid };
      await refresh(true);
    } else if (act === 'setbounds') {
      const s = S.sel;
      const h = await api('PATCH', `/api/span/${a.dataset.layer}/${a.dataset.uid}`, { s: s.a, e: s.b });
      S.sel = { kind: 'span', layer: a.dataset.layer, uid: +a.dataset.uid }; S.prevSpan = null;
      updateHistoryButtons(h); await refresh(true);
    }
  } catch (err) { fail(err); }
});

/* ------------------------------------------------------------ pick modes */
/* Enter 'click the head token / the speaker mention' mode with a banner. */
function startPick(p) {
  S.pick = p;
  const b = $('#banner');
  b.innerHTML = `<span>${p.type === 'head' ? 'Click the token that should be the head. Click the token itself to make it the root.' : 'Click an entity label in the Entities column to make it the speaker mention.'}</span><button class="btn sm" id="pickCancel">Cancel</button>`;
  b.hidden = false;
  $('#pickCancel').onclick = endPick;
  if (S.mode !== 'table') { S.mode = 'table'; }
  renderMain();
}
/* Leave 'pick a head / pick a speaker mention' mode. */
function endPick() { S.pick = null; $('#banner').hidden = true; renderMain(); }

/* ------------------------------------------------------------ table events */
$('#main').addEventListener('click', async e => {
  if (S.mode === 'find') return onFindClick(e);
  const sop = e.target.closest('[data-sop]');
  if (sop) { e.stopPropagation(); return runSop(sop.dataset); }
  const rev = e.target.closest('[data-rev]');
  if (rev) { e.stopPropagation(); return setReviewed([+rev.dataset.rev], rev.dataset.on === '1'); }
  if (S.view === 'chars') return onCharsClick(e);
  if (S.view === 'quotes') return onQuotesClick(e);
  if (S.view === 'annot') return onAnnotClick(e);
  const chip = e.target.closest('[data-layer]');
  const tr = e.target.closest('tr.trow');
  try {
    if (S.pick) {
      if (S.pick.type === 'head' && tr) {
        const uid = S.pick.uid, ord = +tr.dataset.ord; endPick();
        const h = await api('PATCH', `/api/token/${uid}`, { head_ord: ord });
        updateHistoryButtons(h); await refresh(false); return;
      }
      if (S.pick.type === 'mention' && chip && chip.dataset.layer === 'entities') {
        const qid = S.pick.uid; endPick();
        const h = await api('PATCH', `/api/span/quotes/${qid}`, { mention_entity: +chip.dataset.uid });
        updateHistoryButtons(h); await refresh(true); return;
      }
      return;
    }
    if (chip && (e.metaKey || e.ctrlKey) && !chip.classList.contains('mref')) { toggleMention(chip.dataset.layer, +chip.dataset.uid); return; }
    if (chip) {
      const d = await selectSpan(chip.dataset.layer, +chip.dataset.uid);
      S.prevSpan = { layer: chip.dataset.layer, uid: +chip.dataset.uid, text: d.text };
      paintSelection(); renderPanel(); return;
    }
    if (!tr) return;
    const ord = +tr.dataset.ord, uid = +tr.dataset.uid;
    if (e.shiftKey && S.sel && (S.sel.kind === 'token' || S.sel.kind === 'range' || S.sel.kind === 'span')) {
      const anchor = S.sel.kind === 'token' ? S.sel.ord : S.sel.kind === 'range' ? S.sel.anchor : S.sel.s;
      S.sel = { kind: 'range', anchor, a: Math.min(anchor, ord), b: Math.max(anchor, ord) };
      getSelection().removeAllRanges();
    } else {
      selectToken(ord, uid);
      S.prevSpan = null;
      const td = e.target.closest('td[data-f]');
      if (td) S.cursor = { ord, f: td.dataset.f };
    }
    paintSelection(); renderPanel();
  } catch (err) { fail(err); }
});
$('#main').addEventListener('dblclick', e => {
  if (S.view === 'annot') {
    const wd = e.target.closest('#av .wd'); if (!wd || S.pick) return;
    const tk = wd.closest('.tk'); selectToken(+tk.dataset.ord, +tk.dataset.uid); paintSelection(); renderPanel();
    startEdit(+tk.dataset.ord, 'word'); return;
  }
  if (S.view !== 'table') return;
  const td = e.target.closest('td[data-f]'); const tr = e.target.closest('tr.trow');
  if (!td || !tr || S.pick) return;
  S.cursor = { ord: +tr.dataset.ord, f: td.dataset.f };
  startEdit(S.cursor.ord, S.cursor.f);
});
document.addEventListener('keydown', e => {
  if (editing) return;
  const inField = e.target.matches && e.target.matches('input, select, textarea');
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key.toLowerCase() === 'z' && !inField) { e.preventDefault(); e.shiftKey ? doRedo() : doUndo(); return; }
  if (inField) return;
  if (e.key === 'Escape') { if (S.pick) endPick(); else if (!$('#drawer').hidden) closeDrawer(); else if (!$('#pop').hidden) closePop(); else { S.sel = null; S.cursor = null; paintSelection(); renderPanel(); } return; }
  if (e.key === '/') { e.preventDefault(); openFind(); return; }
  if (e.key === '[') { page(-1); return; }
  if (e.key === ']') { page(1); return; }
  if (S.mode !== 'table' || !S.win) return;
  if (e.key === 'v' && !mod) { setView(S.view === 'table' ? 'annot' : 'table'); return; }
  if (e.key === 'r' && !mod && (S.view === 'table' || S.view === 'annot')) { toggleReviewFromSelection(); return; }
  if (S.view === 'chars' || S.view === 'quotes' || S.view === 'flags') return;
  if (S.view === 'annot') { annotKey(e); return; }
  if (!S.cursor && ['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Enter'].includes(e.key)) {
    const t = S.sel && S.sel.kind === 'token' ? S.win.tokens.find(x => x.ord === S.sel.ord) : S.win.tokens[0];
    const f = editableCols()[0];
    if (t && f) { S.cursor = { ord: t.ord, f }; selectToken(t.ord, t.uid); paintSelection(); renderPanel(); }
    e.preventDefault(); return;
  }
  if (!S.cursor) return;
  const map = { ArrowDown: 'down', ArrowUp: 'up', ArrowLeft: 'left', ArrowRight: 'right' };
  if (map[e.key]) { e.preventDefault(); moveCursor(map[e.key]); return; }
  if (e.key === 'Enter' || e.key === 'F2') { e.preventDefault(); startEdit(S.cursor.ord, S.cursor.f); return; }
  if (e.key.length === 1 && !mod && !e.altKey) { e.preventDefault(); startEdit(S.cursor.ord, S.cursor.f, e.key); }
});

/* ------------------------------------------------------------ paging & goto */
/* Go to the previous (-1) or next (+1) page of sentences. */
async function page(d) {
  if (!S.win || (d > 0 && S.win.last >= S.win.n_sentences - 1)) return;
  S.sent = Math.max(0, S.win.first + d * S.n);
  S.cursor = null;
  S.mode = 'table';
  try { await loadWindow(); $('#main').scrollTop = 0; } catch (e) { fail(e); }
}
$('#prevBtn').onclick = () => page(-1);
$('#nextBtn').onclick = () => page(1);
$('#perPage').onchange = e => { S.n = +e.target.value; localStorage.setItem('bne.n', S.n); loadWindow().catch(fail); };
$('#goto').addEventListener('keydown', async e => {
  if (e.key !== 'Enter') return;
  const v = e.target.value.trim().toLowerCase();
  let m;
  try {
    if ((m = v.match(/^t\s*(\d+)$/))) {
      const ord = +m[1];
      if (ord >= S.info.counts.tokens) throw new Error(`The last token is ${S.info.counts.tokens - 1}`);
      await goTo(ord, async () => { const t = S.win.tokens.find(x => x.ord === ord); if (t) { selectToken(ord, t.uid); } });
    } else if ((m = v.match(/^s?\s*(\d+)$/))) {
      S.sent = +m[1]; S.mode = 'table'; await loadWindow(); $('#main').scrollTop = 0;
    } else throw new Error('Type s followed by a sentence number, or t followed by a token number');
    e.target.value = ''; e.target.blur();
  } catch (err) { fail(err); }
});

/* ------------------------------------------------------------ find */
/* Fields to search per layer, with labels. */
const FIND_FIELDS = {
  tokens: [['word', 'Word'], ['lemma', 'Lemma'], ['pos', 'POS'], ['tag', 'Fine POS'], ['dep', 'Dependency'], ['event', 'Event']],
  entities: [['text', 'Text'], ['cat', 'Type'], ['prop', 'Mention type'], ['coref', 'Group number']],
  supersenses: [['text', 'Text'], ['cat', 'Category']],
  quotes: [['text', 'Text'], ['char_id', 'Speaker group']],
};
/* The search conditions (phrase only for tokens). */
const FIND_OPS = [['eq', 'is'], ['contains', 'contains'], ['regex', 'matches pattern'], ['phrase', 'is the phrase (words in a row)'], ['invalid', 'is outside the tag list'], ['edited', 'was edited by hand']];
/* Filter: where in the text a hit may start. */
const FIND_WHERE = [['', 'In narration and quotes'], ['narration', 'Only in narration'], ['quote', 'Only inside quotes']];
/* Filter: leave out hits that already have a span of this layer. */
const FIND_WITHOUT = [['', 'Annotated or not'], ['entities', 'Not yet an entity'], ['supersenses', 'Not yet a supersense'], ['quotes', 'Not yet in a quote']];
/* <option> tags from [value, label] pairs. */
const optionsHTML = list => list.map(([v, l]) => `<option value="${v}">${l}</option>`).join('');
/* Fill the Find bar's selects and keep them consistent (fields per layer, phrase only for tokens, value suggestions). */
function setupFind() {
  $('#fLayer').innerHTML = optionsHTML([['tokens', 'Tokens'], ['entities', 'Entities'], ['supersenses', 'Supersenses'], ['quotes', 'Quotes']]);
  $('#fWhere').innerHTML = optionsHTML(FIND_WHERE);
  $('#fWithout').innerHTML = optionsHTML(FIND_WITHOUT);
  const syncFields = () => {
    $('#fField').innerHTML = optionsHTML(FIND_FIELDS[$('#fLayer').value]);
    const op = $('#fOp').value;  // "phrase" only makes sense for tokens; keep the chosen operator when it still applies
    $('#fOp').innerHTML = optionsHTML(FIND_OPS.filter(([v]) => v !== 'phrase' || $('#fLayer').value === 'tokens'));
    $('#fOp').value = [...$('#fOp').options].some(o => o.value === op) ? op : 'eq';
    syncValue();
  };
  const syncValue = () => {
    const key = `${$('#fLayer').value}.${$('#fField').value}`;
    const op = $('#fOp').value;
    const inp = $('#fValue');
    inp.disabled = op === 'invalid' || op === 'edited';
    inp.placeholder = op === 'phrase' ? 'Words in a row, e.g. Baker Street' : 'Value';
    if (op !== 'phrase' && S.info.tagsets[key]) inp.setAttribute('list', `dl-${key.replace('.', '-')}`); else if (op !== 'phrase' && (key.endsWith('coref') || key.endsWith('char_id'))) inp.setAttribute('list', 'dl-clusters'); else inp.removeAttribute('list');
  };
  $('#fLayer').onchange = syncFields;
  $('#fField').onchange = syncValue;
  $('#fOp').onchange = syncValue;
  syncFields();
  $('#findbar').onsubmit = e => { e.preventDefault(); runFind(0).catch(fail); };
  $('#fClose').onclick = closeFind;
  $('#findBtn').onclick = () => $('#findbar').hidden ? openFind() : closeFind();
}
/* Show the Find bar, optionally with a preset search that runs at once (e.g. 'list this group'). */
function openFind(preset) {
  $('#findbar').hidden = false; $('#findBtn').classList.add('on');
  if (preset) {
    $('#fLayer').value = preset.layer; $('#fLayer').onchange();
    $('#fField').value = preset.field; $('#fOp').value = preset.op; $('#fField').onchange();
    $('#fValue').value = preset.value ?? ''; $('#fWhere').value = preset.where || ''; $('#fWithout').value = preset.without || '';
    runFind(0).catch(fail);
  } else $('#fValue').focus();
}
/* Hide the Find bar and go back to the text. */
function closeFind() { $('#findbar').hidden = true; $('#findBtn').classList.remove('on'); if (S.mode === 'find') { S.mode = 'table'; renderMain(); } }
/* Run the search on the server and show one page of results (100 per page). */
async function runFind(offset, quiet) {
  const layer = $('#fLayer').value, field = $('#fField').value, op = $('#fOp').value;
  let value = $('#fValue').value.trim();
  const where = $('#fWhere').value, without = $('#fWithout').value;
  if ((field === 'coref' || field === 'char_id') && op === 'eq') value = String(parseCluster(value));
  const q = new URLSearchParams({ layer, field, op, value, where, without, offset, limit: 100 });
  const res = await api('GET', `/api/search?${q}`);
  const same = S.find && S.find.layer === layer && S.find.field === field && S.find.op === op && S.find.value === value
    && S.find.where === where && S.find.without === without;
  S.find = { layer, field, op, value, where, without, offset, res, checked: same && S.find.checked ? S.find.checked : new Set(),
    bulk: same && S.find.bulk ? S.find.bulk : null };
  S.mode = 'find';
  syncPassBanner();
  $('#fCount').textContent = `${res.total.toLocaleString()} found`;
  renderFind();
  if (!quiet) $('#main').scrollTop = 0;
}
/* Draw the results with the bulk bar (see bulk.js) and paging. */
function renderFind() {
  const { res, layer, offset } = S.find;
  const pages = res.total > 100 ? `<div class="rpager"><button class="btn sm" data-fp="${offset - 100}" ${offset === 0 ? 'disabled' : ''}>Previous</button>
    <span class="num">${offset + 1}–${Math.min(res.total, offset + 100)} of ${res.total.toLocaleString()}</span>
    <button class="btn sm" data-fp="${offset + 100}" ${offset + 100 >= res.total ? 'disabled' : ''}>Next</button></div>` : '';
  $('#main').innerHTML = `<div class="results"><div class="row" style="justify-content:space-between"><h2>${res.total.toLocaleString()} result${res.total === 1 ? '' : 's'}</h2><button class="btn sm" data-fback>Back to the ${S.textView === 'table' ? 'table' : 'text'}</button></div>${bulkBarHTML()}${pages}
    ${res.hits.map(h => `<div class="hit" data-ord="${h.s}" data-end="${h.e}" data-layer="${layer}" data-uid="${h.uid}"><div><div class="num"><input type="checkbox" data-hchk="${h.uid}" ${S.find.checked && S.find.checked.has(h.uid) ? 'checked' : ''} aria-label="Tick this result"> S ${h.sent} · t${h.s}${h.e !== h.s ? '–' + h.e : ''}</div><div class="val">${esc(layer === 'entities' && S.find.field === 'coref' ? clusterValue(h.value) : h.value)}</div></div>
      <div class="ctx">${h.context_start > 0 ? '… ' : ''}${h.context.map((w, i) => { const o = h.context_start + i; return o >= h.s && o <= h.e ? `<mark>${esc(w)}</mark>` : esc(w); }).join(' ')} …</div></div>`).join('')}${pages}</div>`;
}
/* Clicks in the results: bulk bar, paging, or jump to a hit in the text. */
async function onFindClick(e) {
  if (e.target.closest('.bulk')) { onBulkClick(e); return; }
  if (e.target.matches('input, select')) return;
  if (e.target.closest('[data-fback]')) { S.mode = 'table'; if (S.view === 'chars' || S.view === 'quotes' || S.view === 'flags') S.view = S.textView || 'annot'; renderMain(); return; }
  const fp = e.target.closest('[data-fp]');
  if (fp) { runFind(+fp.dataset.fp).catch(fail); return; }
  const h = e.target.closest('.hit'); if (!h) return;
  const ord = +h.dataset.ord, layer = h.dataset.layer, uid = +h.dataset.uid;
  try {
    await goTo(ord, async () => {
      if (layer === 'tokens') selectToken(ord, uid);
      else await selectSpan(layer, uid);
    }, +h.dataset.end);
  } catch (err) { fail(err); }
}

/* ------------------------------------------------------------ columns */
/* The one small popup element shared by menus and pickers. */
const pop = $('#pop');
/* Close the small popup (columns, pickers, create-annotation). */
function closePop() { if (pop.contains(document.activeElement)) document.activeElement.blur(); pop.hidden = true; pop.onclick = null; pop.onkeydown = null; pop.onchange = null; }
$('#colsBtn').onclick = e => {
  if (!pop.hidden) { closePop(); return; }
  pop.onclick = null; pop.onkeydown = null;
  pop.innerHTML = COLS.map(c => `<label><input type="checkbox" data-col="${c.k}" ${S.cols[c.k] ? 'checked' : ''}> ${esc(c.label)}</label>`).join('');
  const r = e.target.getBoundingClientRect();
  pop.style.top = r.bottom + 6 + 'px'; pop.style.left = Math.min(innerWidth - 240, r.left) + 'px';
  pop.hidden = false;
};
pop.addEventListener('change', e => {
  const k = e.target.dataset.col; if (!k) return;
  S.cols[k] = e.target.checked; localStorage.setItem('bne.cols', JSON.stringify(S.cols));
  if (S.cursor && !S.cols[S.cursor.f]) S.cursor = null;
  if (S.mode === 'table') renderMain();
});
document.addEventListener('mousedown', e => { if (!pop.hidden && !pop.contains(e.target) && e.target !== $('#colsBtn')) closePop(); });

/* ------------------------------------------------------------ history */
/* Enable Undo/Redo with what they would do, and show the number of edits. */
function updateHistoryButtons(h) {
  if (!h) return;
  $('#undoBtn').disabled = !h.undo; $('#undoBtn').title = h.undo ? `Undo: ${h.undo}` : 'Nothing to undo';
  $('#redoBtn').disabled = !h.redo; $('#redoBtn').title = h.redo ? `Redo: ${h.redo}` : 'Nothing to redo';
  $('#histBtn').textContent = h.count ? `History (${h.count})` : 'History';
}
/* Undo the latest edit and redraw. */
async function doUndo() {
  try { const r = await api('POST', '/api/undo'); updateHistoryButtons(r.history); toast('Undone: ' + r.label); await refresh(true); reopenDrawer(); } catch (e) { fail(e); }
}
/* Redo the latest undone edit and redraw. */
async function doRedo() {
  try { const r = await api('POST', '/api/redo'); updateHistoryButtons(r.history); toast('Redone: ' + r.label); await refresh(true); reopenDrawer(); } catch (e) { fail(e); }
}
$('#undoBtn').onclick = doUndo;
$('#redoBtn').onclick = doRedo;
/* Show the History drawer (every edit, with undone ones greyed out). */
async function openDrawer() {
  const h = await api('GET', '/api/history');
  const d = $('#drawer');
  d.dataset.kind = 'history';
  d.innerHTML = `<header><h2>History</h2><button class="btn" id="dUndo" ${h.undo ? '' : 'disabled'}>Undo</button><button class="btn" id="dRedo" ${h.redo ? '' : 'disabled'}>Redo</button><button class="btn" id="dClose">Close</button></header>
    <ol>${h.items.length ? h.items.map(i => `<li class="${i.undone ? 'undone' : ''}"><span>${esc(i.label)}</span><span class="num">${new Date(i.ts * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}</span></li>`).join('') : '<li class="muted">No changes yet.</li>'}</ol>`;
  d.hidden = false;
  $('#dUndo').onclick = doUndo; $('#dRedo').onclick = doRedo; $('#dClose').onclick = closeDrawer;
}
/* Hide the History drawer. */
function closeDrawer() { $('#drawer').hidden = true; }
$('#histBtn').onclick = () => ($('#drawer').hidden || $('#drawer').dataset.kind !== 'history') ? openDrawer().catch(fail) : closeDrawer();
/* Redraw the History drawer if it is open. */
function reopenDrawer() {
  const d = $('#drawer'); if (d.hidden) return;
  (d.dataset.kind === 'sug' ? openSuggestions(true) : openDrawer()).catch(fail);
}

/* ------------------------------------------------------------ export */
$('#exportBtn').onclick = async () => {
  const b = $('#exportBtn'); b.disabled = true; b.textContent = 'Exporting…';
  try {
    const r = await api('POST', '/api/export');
    const bookMsg = !r.book ? '' : r.book.startsWith('Couldn') ? ` ${r.book}` : ' .book and .book.html rebuilt.';
    toast(`Exported to ${r.path}.${r.issues ? ` ${r.issues} values outside the tag lists.` : ''}${bookMsg}`, { label: 'Show folder', run: () => api('POST', '/api/library/reveal', { path: r.path }).catch(fail) });
  } catch (e) { fail(e); }
  finally { b.disabled = false; b.textContent = 'Export'; }
};

/* ------------------------------------------------------------ annotation view */
/* Height of one label lane, and of one dependency-arc level, in the annotation view (pixels). */
const LANE_H = 19, ARC_H = 15;
/* Tokens that attach to the word before them (punctuation, 's, n't): drawn without a gap. */
const NSB = /^(?:[.,;:!?%)\]}”’»…]+|'s|'S|n't|N'T|'re|'ve|'ll|'d|'m|’s|’S|’re|’ve|’ll|’d|’m|n’t)$/;
/* Tokens that attach to the word after them (opening quotes and brackets). */
const NSA = /^[(\[{“‘«$]$/;
/* Token fields that can be shown as rows under the words in the annotation view. */
const ROW_FIELDS = [['lemma', 'Lemma'], ['pos', 'POS'], ['tag', 'Fine POS'], ['event', 'Event']];
/* The layers that can be switched on and off in the annotation view. */
const LAYER_TOGGLES = [['entities', 'Entities'], ['supersenses', 'Supersenses'], ['quotes', 'Quotes'], ['attrib', 'Speaker arcs'], ['deps', 'Dependencies']];
/* The annotation view's layout of the current page (lanes and arcs per sentence), read by drawOverlay. */
let AV = null;

/* Switch between Table, Annotation, Entities and Quotes (remembers the choice, keeps the selection in view). */
function setView(v) {
  if (S.view === v) return;
  saveNav();
  if (editing) cancelEdit();
  S.view = v; localStorage.setItem('bne.view', v);
  if (v === 'table' || v === 'annot') { S.textView = v; localStorage.setItem('bne.textView', v); }
  if (S.mode === 'find') { S.mode = 'table'; }
  renderMain();
  const r = selectionRange();
  if (r) { const el = document.querySelector(`#main [data-ord="${r[0]}"]`); if (el) el.scrollIntoView({ block: 'center' }); }
  pushNav();
}
/* A supersense without its noun./verb. prefix. */
function shortSS(cat) { return String(cat).replace(/^(noun|verb)\./, ''); }
/* The text on a span's label bar. */
function spanLabel(layer, sp) {
  if (layer === 'entities') return `${sp.cat} ${sp.name || '#' + sp.coref}`;
  if (layer === 'supersenses') return shortSS(sp.cat);
  return `“ ${sp.char_id != null ? sp.speaker : 'No speaker'}`;
}
/* The colour hue of a span's bar (group colour for entities and quotes, orange verbs, blue nouns). */
function spanHue(layer, sp) {
  if (layer === 'entities') return hue(sp.coref);
  if (layer === 'supersenses') return String(sp.cat).startsWith('verb.') ? 28 : 200;
  return sp.char_id != null ? hue(sp.char_id) : null;
}
/* Give overlapping spans separate lanes (shorter ones closer to the words); returns the number of lanes. */
function assignLanes(spans) {
  // shorter spans sit closer to the words; overlapping spans get separate lanes
  const sorted = spans.slice().sort((a, b) => (a.e - a.s) - (b.e - b.s) || a.s - b.s);
  const lanes = [];
  for (const sp of sorted) {
    let k = 0;
    while (lanes[k] && lanes[k].some(o => o.s <= sp.e && o.e >= sp.s)) k++;
    (lanes[k] ||= []).push(sp);
    sp.lane = k;
  }
  return lanes.length;
}
/* Give dependency arcs a height so nested arcs don't cross; returns the highest level. */
function arcLevels(arcs) {
  const sorted = arcs.slice().sort((x, y) => (x.b - x.a) - (y.b - y.a));
  const done = [];
  for (const arc of sorted) {
    let lv = 1;
    for (const o of done) if (o.a >= arc.a && o.b <= arc.b && !(o.a === arc.a && o.b === arc.b)) lv = Math.max(lv, o.level + 1);
    arc.level = lv; done.push(arc);
  }
  return arcs.reduce((m, x) => Math.max(m, x.level), 0);
}
/* Annotation view: one block per sentence with words, optional rows, and the layer bars drawn by drawOverlay. */
function renderAnnot() {
  const w = S.win, L = S.layers;
  const tokByOrd = new Map(w.tokens.map(t => [t.ord, t]));
  const blocks = [];
  let ti = 0;
  for (const s of w.sentences) {
    const toks = [];
    while (ti < w.tokens.length && w.tokens[ti].ord <= s.end) toks.push(w.tokens[ti++]);
    const clamp = sp => ({ ...sp, s0: sp.s, e0: sp.e, s: Math.max(sp.s, s.start), e: Math.min(sp.e, s.end) });
    const layers = {};
    for (const layer of ['entities', 'supersenses', 'quotes']) {
      layers[layer] = L[layer] ? w[layer].filter(sp => sp.s <= s.end && sp.e >= s.start).map(clamp) : [];
    }
    const nl = { entities: assignLanes(layers.entities), supersenses: assignLanes(layers.supersenses), quotes: assignLanes(layers.quotes) };
    const arcs = [];
    if (L.deps) for (const t of toks) {
      if (t.head_ord == null || t.head_ord === t.ord || t.head_ord < s.start || t.head_ord > s.end) continue;
      arcs.push({ dep: t.ord, head: t.head_ord, a: Math.min(t.ord, t.head_ord), b: Math.max(t.ord, t.head_ord), label: t.dep, uid: t.uid });
    }
    const maxLevel = L.deps ? arcLevels(arcs) + 1 : 0;
    const arcArea = L.deps ? maxLevel * ARC_H + 10 : 0;
    const totalLanes = nl.entities + nl.supersenses + nl.quotes;
    blocks.push({ s, toks, layers, nl, arcs, arcArea, totalLanes, pt: arcArea + totalLanes * LANE_H + 4 });
  }
  AV = { blocks };
  const rows = ROW_FIELDS.filter(([k]) => L[k]);
  const r = selectionRange();
  const ef = S.ef;
  const html = blocks.map((bk, i) => {
    const div = ef.on && i > 0 && bk.s.index !== blocks[i - 1].s.index + 1 ? '<div class="excdiv"><span>⋯</span></div>' : '';
    const single = new Map();
    for (const layer of ['entities', 'supersenses']) for (const sp of bk.layers[layer]) if (sp.s0 === sp.e0) {
      if (!single.has(sp.s)) single.set(sp.s, []);
      single.get(sp.s).push(trim(spanLabel(layer, sp), 12));
    }
    const cells = bk.toks.map((t, i) => {
      const next = bk.toks[i + 1];
      const tight = (next && NSB.test(next.word)) || NSA.test(t.word) ? ' tight' : '';
      const sel = r && t.ord >= r[0] && t.ord <= r[1] ? ' sel' : '';
      const rowHtml = rows.map(([k]) => `<span data-f="${k}" title="${FIELD_LABEL[k]}: click to edit" class="rw${inTagset('tokens.' + k, t[k]) ? '' : ' bad'}${t.manual.includes(k) ? ' man' : ''}">${esc(t[k])}</span>`).join('');
      const wl = (single.get(t.ord) || []).map(x => `<span class="wl">${esc(x)}</span>`).join('');
      const man = t.manual.length ? ' title="Edited by hand: ' + esc(t.manual.join(', ')) + '"' : '';
      const gap = i > 0 ? `<span class="gap" data-sop="splitsent" data-ord="${t.ord}" title="Start a new sentence at “${esc(t.word)}”"></span>` : '';
      const pend = S.pendingSet && S.pendingSet.has(t.uid) ? ' pend' : '';
      return `<span class="tk${tight}${sel}${pend}${t.manual.length ? ' tman' : ''}" data-ord="${t.ord}" data-uid="${t.uid}"${man}>${gap}<span class="wd">${esc(t.word)}</span>${rowHtml}${wl}</span>`;
    }).join('');
    return `${div}<section class="sb${bk.s.reviewed ? ' reviewed' : ''}" data-sent="${bk.s.index}" style="--pt:${bk.pt}px"><div class="gut"><span class="sn">S ${bk.s.index}</span>${bk.s.para_start ? `<span class="pm">¶ ${bk.s.para}</span>` : ''}${revMark(bk.s)}${bk.toks.length ? flagButton('sentence', bk.toks[0].uid) : ''}<span class="gacts">${gutterActions(bk.s)}</span></div>
      <div class="tks">${cells}</div></section>`;
  }).join('');
  const tog = (list, cls) => list.map(([k, l]) => `<button class="tg${S.layers[k] ? ' on' : ''} ${cls}" data-layer-toggle="${k}" aria-pressed="${!!S.layers[k]}">${l}</button>`).join('');
  const efPages = Math.max(1, Math.ceil(ef.total / 20));
  const efbar = ef.on && ef.gid != null ? `<div class="efbar"><span class="sw" style="--h:${hue(ef.gid)}"></span><b>${esc(groupName(ef.gid))}</b>
      <span class="num">${ef.total.toLocaleString()} excerpt${ef.total === 1 ? '' : 's'}</span>
      <button class="btn sm" data-efpage="prev" ${ef.offset > 0 ? '' : 'disabled'} aria-label="Previous excerpts">‹</button>
      <span class="num">Page ${ef.offset + 1} of ${efPages}</span>
      <button class="btn sm" data-efpage="next" ${ef.offset + 1 < efPages ? '' : 'disabled'} aria-label="More excerpts">›</button>
      <button class="link" id="efChange">Change…</button></div>` : '';
  const efEmpty = ef.on && ef.gid == null ? '<p class="note" style="margin:16px">Choose a group on the left to see only its mentions and quotes, in context.</p>'
    : ef.on && !ef.total ? '<p class="note" style="margin:16px">No mentions or quotes for this group.</p>' : '';
  $('#main').innerHTML = `<div class="avbar"><span class="muted">Show</span>${tog(LAYER_TOGGLES, '')}<span class="sep"></span><span class="muted">Rows</span>${tog(ROW_FIELDS, 'rowtg')}
      <span class="grow"></span><span class="hint">Select words to annotate · ⌘-click labels to select several</span>
      <button class="tg${S.pp ? ' on' : ''}" id="ppBtn" aria-pressed="${!!S.pp}" title="Go through the pronouns one by one">Pronoun pass</button>
      <button class="tg${S.qp ? ' on' : ''}" id="qpBtn" aria-pressed="${!!S.qp}" title="Go through the quotes one by one">Quote pass</button>
      <button class="tg${S.gside ? ' on' : ''}" id="gsideBtn" aria-pressed="${!!S.gside}" title="Show the groups beside the text">Groups</button>
      <button class="tg${ef.on ? ' on' : ''}" id="efBtn" aria-pressed="${!!ef.on}" title="Show only one entity's mentions and quotes, in context">Filter by entity</button></div>
    ${efbar}
    <div class="avwrap${S.gside || ef.on ? ' withside' : ''}">${ef.on ? `<aside class="gside efside" id="efside" aria-label="Entities">${ef.listHTML || ''}</aside>`
      : S.gside ? '<aside class="gside" id="gside" aria-label="Groups"></aside>' : ''}
    <div class="av${L.deps ? ' nowrap' : ''}" id="av"><svg class="arcs" id="arcs" aria-hidden="true"></svg><div class="ov" id="ov"></div>${efEmpty || html}</div></div>`;
  drawOverlay();
  if (!ef.on && S.gside) renderGside();
}
/* Position the label bars, dependency arcs and speaker arcs (SVG) from the measured word positions. */
function drawOverlay() {
  const av = $('#av'); if (!av || !AV) return;
  const base = av.getBoundingClientRect();
  const pos = new Map();
  av.querySelectorAll('.tk').forEach(el => {
    const r = el.getBoundingClientRect(), wd = el.firstElementChild.getBoundingClientRect();
    pos.set(+el.dataset.ord, { x: r.left - base.left, y: r.top - base.top, w: r.width, wl: wd.left - base.left, ww: wd.width, wy: wd.top - base.top, pr: el.classList.contains('tight') ? 1 : 6 });
  });
  const bars = [], paths = [], barPos = new Map();
  const selSpan = S.sel && S.sel.kind === 'span' ? S.sel : null;
  const selCoref = S.hl != null ? S.hl : selSpan && selSpan.layer === 'entities' ? (S.win.entities.find(x => x.uid === selSpan.uid) || {}).coref : null;
  const selOrd = S.sel && S.sel.kind === 'token' ? S.sel.ord : null;
  for (const bk of AV.blocks) {
    const offsets = { entities: 0, supersenses: bk.nl.entities, quotes: bk.nl.entities + bk.nl.supersenses };
    for (const layer of ['entities', 'supersenses', 'quotes']) {
      for (const sp of bk.layers[layer]) {
        const runs = [];
        for (let o = sp.s; o <= sp.e; o++) {
          const p = pos.get(o); if (!p) continue;
          const last = runs[runs.length - 1];
          if (last && Math.abs(last.y - p.y) < 2) { last.end = p; } else runs.push({ y: p.y, start: p, end: p });
        }
        const g = offsets[layer] + sp.lane;
        const h = spanHue(layer, sp);
        runs.forEach((run, i) => {
          const x1 = run.start.x, x2 = run.end.x + run.end.w - run.end.pr;
          const y = run.y + bk.arcArea + (bk.totalLanes - 1 - g) * LANE_H + 2;
          const first = i === 0 && sp.s === sp.s0;
          const contL = i > 0 || sp.s !== sp.s0, contR = i < runs.length - 1 || sp.e !== sp.e0;
          const cls = ['sbar', layer === 'quotes' ? 'bq' : layer === 'supersenses' ? 'bs' : 'be'];
          if (h == null) cls.push('none');
          if (contL) cls.push('cl'); if (contR) cls.push('cr');
          if (selSpan && selSpan.layer === layer && selSpan.uid === sp.uid) cls.push('selspan');
          if (layer === 'entities' && S.msel.has(sp.uid)) cls.push('msel');
          if (selCoref != null && layer === 'entities' && sp.coref === selCoref) cls.push('chain');
          const full = layer === 'entities' ? `${sp.cat} ${sp.prop} · ${sp.name || '#' + sp.coref} (group ${sp.coref}) · “${sp.text}”` : layer === 'supersenses' ? sp.cat : `Quote spoken by ${sp.char_id != null ? sp.speaker : 'no one'}`;
          bars.push(`<div class="${cls.join(' ')}" data-layer="${layer}" data-uid="${sp.uid}" style="left:${x1}px;top:${y}px;width:${Math.max(4, x2 - x1)}px;${h != null ? `--h:${h}` : ''}" title="${esc(full)}">${first || i === 0 ? `<span>${esc(spanLabel(layer, sp))}</span>` : ''}</div>`);
          if (i === 0) barPos.set(`${layer}:${sp.uid}`, { x: x1, y, w: x2 - x1 });
        });
      }
    }
    // dependency arcs
    for (const arc of bk.arcs) {
      const d = pos.get(arc.dep), hd = pos.get(arc.head); if (!d || !hd) continue;
      const dx = d.wl + d.ww / 2, hx = hd.wl + hd.ww / 2;
      const top = p => p.y + bk.arcArea - 2;
      const lvY = p => p.y + bk.arcArea - arc.level * ARC_H;
      const cls = 'arc' + (selOrd != null && (selOrd === arc.dep || selOrd === arc.head) ? ' hot' : '') + (S.win.tokens.find(t => t.ord === arc.dep)?.manual.some(f => f === 'head' || f === 'dep') ? ' man' : '');
      let dpath, lx, ly;
      if (Math.abs(d.y - hd.y) < 2) {
        const y0 = top(d), yl = lvY(d), r = 4, sg = hx > dx ? 1 : -1;
        dpath = `M${hx} ${y0} L${hx} ${yl + r} Q${hx} ${yl} ${hx - sg * r} ${yl} L${dx + sg * r} ${yl} Q${dx} ${yl} ${dx} ${yl + r} L${dx} ${y0}`;
        lx = (dx + hx) / 2; ly = yl;
      } else {
        const avW = av.clientWidth;
        const yd = lvY(d), yh = lvY(hd);
        const edgeD = hd.y > d.y ? avW - 4 : 72, edgeH = hd.y > d.y ? 72 : avW - 4;
        dpath = `M${hx} ${top(hd)} L${hx} ${yh} L${edgeH} ${yh} M${edgeD} ${yd} L${dx} ${yd} L${dx} ${top(d)}`;
        lx = (dx + edgeD) / 2; ly = yd;
      }
      paths.push(`<path class="${cls}" d="${dpath}" marker-end="url(#arrow)" data-uid="${arc.uid}" data-ord="${arc.dep}"><title>${esc(arc.label)}</title></path>`);
      paths.push(`<text class="arcl${cls.includes('hot') ? ' hot' : ''}" x="${lx}" y="${ly - 2}" text-anchor="middle" data-uid="${arc.uid}" data-ord="${arc.dep}">${esc(arc.label)}</text>`);
    }
    if (S.layers.deps) for (const t of bk.toks) if (t.head_ord === t.ord || t.head_ord == null) {
      const p = pos.get(t.ord); if (!p) continue;
      const x = p.wl + p.ww / 2;
      paths.push(`<text class="arcl root" x="${x}" y="${p.y + 12}" text-anchor="middle" data-uid="${t.uid}" data-ord="${t.ord}">ROOT</text>`);
    }
  }
  // speaker attribution arcs
  if (S.layers.quotes && S.layers.attrib) for (const q of S.win.quotes) {
    if (q.ms == null) continue;
    const qb = barPos.get(`quotes:${q.uid}`); if (!qb) continue;
    const ent = S.win.entities.find(e => e.s === q.ms && e.e === q.me);
    let mx, my;
    const eb = ent && barPos.get(`entities:${ent.uid}`);
    if (eb) { mx = eb.x + Math.min(eb.w / 2, 20); my = eb.y; }
    else { const p = pos.get(q.ms); if (!p) continue; mx = p.wl + p.ww / 2; my = p.wy - 2; }
    const qx = qb.x + 8, qy = qb.y;
    const cy = Math.min(qy, my) - 22 - Math.min(40, Math.abs(qx - mx) / 12);
    const h = q.char_id != null ? hue(q.char_id) : 0;
    const hot = S.sel && S.sel.kind === 'span' && S.sel.layer === 'quotes' && S.sel.uid === q.uid ? ' hot' : '';
    paths.push(`<path class="attr${hot}" style="--h:${h}" d="M${qx} ${qy} C${qx} ${cy} ${mx} ${cy} ${mx} ${my}" marker-end="url(#arrowq)" data-layer="quotes" data-uid="${q.uid}"><title>Spoken by ${esc(q.speaker || '')} (“${esc(q.m_phrase || '')}”)</title></path>`);
  }
  const svg = $('#arcs');
  svg.setAttribute('width', av.scrollWidth); svg.setAttribute('height', av.scrollHeight);
  svg.innerHTML = `<defs><marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L8 4 L0 8 z" class="arrowhead"/></marker>
    <marker id="arrowq" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L8 4 L0 8 z" class="arrowheadq"/></marker></defs>${paths.join('')}`;
  $('#ov').innerHTML = bars.join('');
}
/* Mark selected words in the annotation view and redraw the bars. */
function paintAnnotSelection() {
  const r = selectionRange();
  document.querySelectorAll('#av .tk').forEach(el => { const o = +el.dataset.ord; el.classList.toggle('sel', !!r && o >= r[0] && o <= r[1]); });
  drawOverlay();
}
new ResizeObserver(() => { if (S.view === 'annot' && S.mode === 'table') { clearTimeout(drawOverlay._t); drawOverlay._t = setTimeout(drawOverlay, 60); } }).observe($('#main'));

/* Clicks in the annotation view: layer toggles, labels, arcs, words (Shift-click extends a range). */
async function onAnnotClick(e) {
  const tg = e.target.closest('[data-layer-toggle]');
  if (tg) { const k = tg.dataset.layerToggle; S.layers[k] = !S.layers[k]; localStorage.setItem('bne.layers', JSON.stringify(S.layers)); renderAnnot(); return; }
  if (suppressClick) return;
  const bar = e.target.closest('.sbar, path.attr');
  const arc = e.target.closest('path.arc, text.arcl');
  const tk = e.target.closest('.tk');
  try {
    if (S.pick) {
      if (S.pick.type === 'head' && tk) {
        const uid = S.pick.uid, ord = +tk.dataset.ord; endPick();
        const h = await api('PATCH', `/api/token/${uid}`, { head_ord: ord });
        updateHistoryButtons(h); await refresh(false); return;
      }
      if (S.pick.type === 'mention' && bar && bar.dataset.layer === 'entities') {
        const qid = S.pick.uid; endPick();
        const h = await api('PATCH', `/api/span/quotes/${qid}`, { mention_entity: +bar.dataset.uid });
        updateHistoryButtons(h); await refresh(true); return;
      }
      return;
    }
    if (bar && (e.metaKey || e.ctrlKey)) { toggleMention(bar.dataset.layer, +bar.dataset.uid); return; }
    if (bar) {
      const d = await selectSpan(bar.dataset.layer, +bar.dataset.uid);
      S.prevSpan = { layer: bar.dataset.layer, uid: +bar.dataset.uid, text: d.text };
      paintSelection(); renderPanel(); return;
    }
    if (arc) { const t = S.win.tokens.find(x => x.ord === +arc.dataset.ord); if (t) { selectToken(t.ord, t.uid); S.prevSpan = null; paintSelection(); renderPanel(); } return; }
    if (!tk) return;
    const ord = +tk.dataset.ord, uid = +tk.dataset.uid;
    const rw = e.target.closest('.rw[data-f]');
    if (rw && !e.shiftKey) { selectToken(ord, uid); S.prevSpan = null; paintSelection(); renderPanel(); startEdit(ord, rw.dataset.f); return; }
    if (e.shiftKey && S.sel) {
      const anchor = S.sel.kind === 'token' ? S.sel.ord : S.sel.kind === 'range' ? S.sel.anchor : S.sel.s;
      S.sel = { kind: 'range', anchor, a: Math.min(anchor, ord), b: Math.max(anchor, ord) };
      paintSelection(); renderPanel();
      if (S.win.tokens.some(t => t.ord === S.sel.a) && S.win.tokens.some(t => t.ord === S.sel.b)) openCreatePop(rangeRect(S.sel.a, S.sel.b), S.sel.a, S.sel.b);
      return;
    } else { selectToken(ord, uid); S.prevSpan = null; }
    paintSelection(); renderPanel();
  } catch (err) { fail(err); }
}
/* Left/right arrow keys move the selected word. */
function annotKey(e) {
  if (!S.sel || S.sel.kind !== 'token') {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { const t = S.win.tokens[0]; selectToken(t.ord, t.uid); paintSelection(); renderPanel(); e.preventDefault(); }
    return;
  }
  const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
  if (!d) return;
  e.preventDefault();
  const i = S.win.tokens.findIndex(t => t.ord === S.sel.ord) + d;
  const t = S.win.tokens[i];
  if (!t) return;
  selectToken(t.ord, t.uid); paintSelection(); renderPanel();
  const el = document.querySelector(`#av .tk[data-ord="${t.ord}"]`); if (el) el.scrollIntoView({ block: 'nearest' });
}

/* selecting words → create annotation (own drag handling; works the same in every browser) */
let dragSel = null, suppressClick = false;
$('#main').addEventListener('mousedown', e => {
  if (S.view !== 'annot' || S.mode !== 'table' || S.pick || e.button !== 0 || e.altKey || e.shiftKey) return;
  if (e.target.closest('.sbar, path, text, .rw, input, button, .gap')) return;
  const tk = e.target.closest('#av .tk'); if (!tk) return;
  e.preventDefault();
  dragSel = { a: +tk.dataset.ord, b: +tk.dataset.ord, moved: false };
});
document.addEventListener('mousemove', e => {
  if (!dragSel) return;
  const tk = e.target.closest && e.target.closest('#av .tk'); if (!tk) return;
  const o = +tk.dataset.ord;
  if (o === dragSel.b) return;
  dragSel.b = o; dragSel.moved = dragSel.b !== dragSel.a;
  const lo = Math.min(dragSel.a, dragSel.b), hi = Math.max(dragSel.a, dragSel.b);
  document.querySelectorAll('#av .tk').forEach(el => { const x = +el.dataset.ord; el.classList.toggle('dsel', x >= lo && x <= hi); });
});
document.addEventListener('mouseup', () => {
  if (!dragSel) return;
  const d = dragSel; dragSel = null;
  document.querySelectorAll('#av .tk.dsel').forEach(el => el.classList.remove('dsel'));
  if (!d.moved) return;
  suppressClick = true; setTimeout(() => { suppressClick = false; }, 0);
  const a = Math.min(d.a, d.b), b = Math.max(d.a, d.b);
  if (onRangeSelected(a, b)) return;
  S.sel = { kind: 'range', anchor: d.a, a, b };
  paintSelection(); renderPanel();
  openCreatePop(rangeRect(a, b), a, b);
});
/* Screen rectangle around a range of words (where to place the popup). */
function rangeRect(a, b) {
  const x = document.querySelector(`#av .tk[data-ord="${a}"] .wd`), y = document.querySelector(`#av .tk[data-ord="${b}"] .wd`);
  const r1 = x.getBoundingClientRect(), r2 = y.getBoundingClientRect();
  return { left: Math.min(r1.left, r2.left), top: Math.min(r1.top, r2.top), bottom: Math.max(r1.bottom, r2.bottom) };
}
/* The layer chosen last in the create-annotation popup (remembered). */
let lastLayer = localStorage.getItem('bne.lastLayer') || 'entities';
/* Guess the mention type of new words: PRON for a pronoun, PROP with a proper noun, else NOM. */
function guessProp(a, b) {
  const words = S.win.tokens.filter(t => t.ord >= a && t.ord <= b);
  if (words.length === 1 && /^(PRON)$/.test(words[0].pos)) return 'PRON';
  if (words.some(t => t.pos === 'PROPN')) return 'PROP';
  return 'NOM';
}
/* The popup that appears after selecting words: create an entity, supersense or quote (or set boundaries / merge). */
function openCreatePop(rect, a, b) {
  const text = S.win.tokens.filter(t => t.ord >= a && t.ord <= b).map(t => t.word).join(' ');
  const ps = S.prevSpan;
  pop.innerHTML = `<div class="cp"><div class="cpt">“${esc(trim(text, 60))}” <span class="num">t${a}${b > a ? '–' + b : ''}</span></div>
    <div class="seg" role="tablist">${[['entities', 'Entity'], ['supersenses', 'Supersense'], ['quotes', 'Quote']].map(([k, l]) => `<button type="button" data-cl="${k}" aria-pressed="${k === lastLayer}">${l}</button>`).join('')}</div>
    <div class="cpf" data-for="entities"><input data-add="cat" list="dl-entities-cat" value="PER" aria-label="Entity type" style="width:70px"><input data-add="prop" list="dl-entities-prop" value="${guessProp(a, b)}" aria-label="Mention type" style="width:70px"><input data-add="coref" list="dl-clusters" placeholder="Group (blank = new)" aria-label="Group" style="flex:1"></div>
    <div class="cpf" data-for="supersenses"><input data-add="cat" list="dl-supersenses-cat" placeholder="e.g. noun.person" aria-label="Supersense" style="flex:1"></div>
    <div class="cpf" data-for="quotes"><input data-add="char_id" list="dl-clusters" placeholder="Speaker (optional)" aria-label="Speaker" style="flex:1"></div>
    <div class="row"><button class="btn primary sm" data-create>Create</button><button class="btn sm" data-cancel>Cancel</button>
      ${ps ? `<button class="link" data-bounds>Make these the boundaries of the selected ${LAYER_LABEL[ps.layer].toLowerCase()}</button>` : ''}
      ${b > a && sameSentence(a, b) ? `<button class="link" data-sop="mergetok" data-a="${a}" data-b="${b}">Merge into one token</button>` : ''}</div></div>`;
  const show = () => pop.querySelectorAll('.cpf').forEach(f => f.hidden = f.dataset.for !== lastLayer);
  show();
  pop.hidden = false;
  const pw = pop.offsetWidth, ph = pop.offsetHeight;
  let x = Math.min(innerWidth - pw - 8, Math.max(8, rect.left)), y = rect.bottom + 8;
  if (y + ph > innerHeight - 8) y = Math.max(8, rect.top - ph - 8);
  pop.style.left = x + 'px'; pop.style.top = y + 'px';
  const focus = () => { const f = pop.querySelector(`.cpf[data-for="${lastLayer}"] input:last-child`); if (f) f.focus(); };
  focus();
  pop.onclick = async ev => {
    const seg = ev.target.closest('[data-cl]');
    if (seg) { lastLayer = seg.dataset.cl; localStorage.setItem('bne.lastLayer', lastLayer); pop.querySelectorAll('[data-cl]').forEach(x => x.setAttribute('aria-pressed', x.dataset.cl === lastLayer)); show(); focus(); return; }
    if (ev.target.closest('[data-cancel]')) { closePop(); getSelection().removeAllRanges(); return; }
    if (ev.target.closest('[data-bounds]')) {
      closePop(); getSelection().removeAllRanges();
      try { const h = await api('PATCH', `/api/span/${ps.layer}/${ps.uid}`, { s: a, e: b }); S.sel = { kind: 'span', layer: ps.layer, uid: ps.uid }; S.prevSpan = null; updateHistoryButtons(h); await refresh(true); } catch (err) { fail(err); }
      return;
    }
    if (ev.target.closest('[data-create]')) await createFromPop(a, b);
  };
  pop.onkeydown = ev => { if (ev.key === 'Enter') { ev.preventDefault(); createFromPop(a, b); } if (ev.key === 'Escape') { closePop(); } ev.stopPropagation(); };
}
/* Create the annotation chosen in the popup and select it. */
async function createFromPop(a, b) {
  const body = { s: a, e: b };
  try {
    pop.querySelectorAll(`.cpf[data-for="${lastLayer}"] [data-add]`).forEach(i => {
      const k = i.dataset.add, v = i.value.trim();
      body[k] = (k === 'coref' || k === 'char_id') ? parseCluster(v) : v;
    });
    if (lastLayer === 'supersenses' && !body.cat) throw new Error('Choose a supersense category first');
    const r = await api('POST', `/api/span/${lastLayer}`, body);
    closePop(); getSelection().removeAllRanges();
    updateHistoryButtons(r.history);
    S.sel = { kind: 'span', layer: lastLayer, uid: r.uid };
    await refresh(true);
  } catch (err) { fail(err); }
}

/* option-drag from a word to its new head */
let dragHead = null;
$('#main').addEventListener('mousedown', e => {
  if (S.view !== 'annot' || !e.altKey) return;
  const tk = e.target.closest('#av .tk'); if (!tk) return;
  e.preventDefault();
  dragHead = { from: +tk.dataset.ord, uid: +tk.dataset.uid };
  document.body.classList.add('draghead');
  tk.classList.add('dragsrc');
});
document.addEventListener('mouseup', async e => {
  if (!dragHead) return;
  const d = dragHead; dragHead = null;
  document.body.classList.remove('draghead');
  document.querySelectorAll('.dragsrc').forEach(x => x.classList.remove('dragsrc'));
  const tk = e.target.closest && e.target.closest('#av .tk'); if (!tk) return;
  const to = +tk.dataset.ord;
  try { const h = await api('PATCH', `/api/token/${d.uid}`, { head_ord: to }); updateHistoryButtons(h); await refresh(false); } catch (err) { fail(err); }
});
$('#viewSwitch').addEventListener('click', e => { const b = e.target.closest('[data-view]'); if (b) setView(b.dataset.view); });


/* ------------------------------------------------------------ tokenization, sentences, suggestions */
/* Are tokens a and b in the same sentence? */
function sameSentence(a, b) {
  const ta = S.win.tokens.find(t => t.ord === a), tb = S.win.tokens.find(t => t.ord === b);
  return !!ta && !!tb && ta.sent === tb.sent;
}
/* The 'merge tokens' block for a selected range. */
function mergeTokHTML(a, b) {
  if (b <= a || !sameSentence(a, b)) return '';
  return `<h3>Tokenization</h3><div class="row"><button class="btn" data-sop="mergetok" data-a="${a}" data-b="${b}">Merge ${b - a + 1} tokens into one</button></div>
    <p class="note">Only tokens that touch in the text (no space between them) can be merged.</p>`;
}
/* The page's data for a sentence number. */
function sentInfo(idx) { return S.win.sentences.find(x => x.index === idx); }
/* Text buttons beside a sentence header (merge with next, paragraph break, re-tag). */
function sentActions(s) {
  const last = s.index >= S.win.n_sentences - 1;
  return `<span class="sact">${last ? '' : `<button class="link" data-sop="mergesent" data-sent="${s.index}">Merge with next</button>`}
    ${s.index > 0 ? `<button class="link" data-sop="para" data-sent="${s.index}" data-on="${s.para_start ? 0 : 1}">${s.para_start ? 'Remove paragraph break' : 'Start paragraph'}</button>` : ''}
    <button class="link" data-sop="retag" data-sent="${s.index}">Re-tag</button></span>`;
}
/* Icon buttons in a sentence's gutter (same actions). */
function gutterActions(s) {
  const last = s.index >= S.win.n_sentences - 1;
  return `${last ? '' : `<button class="gb" data-sop="mergesent" data-sent="${s.index}" title="Merge with the next sentence">⤓</button>`}
    ${s.index > 0 ? `<button class="gb${s.para_start ? ' on' : ''}" data-sop="para" data-sent="${s.index}" data-on="${s.para_start ? 0 : 1}" title="${s.para_start ? 'Remove the paragraph break before this sentence' : 'Start a paragraph with this sentence'}">¶</button>` : ''}
    <button class="gb" data-sop="retag" data-sent="${s.index}" title="Re-tag this sentence with spaCy">↻</button>`;
}
/* Side panel block: new sentence here, merge, paragraph, re-tag. */
function structureHTML(t, loc) {
  const si = sentInfo(t.sent);
  const n = S.win.n_sentences;
  return `<div class="row">
      ${loc > 0 ? `<button class="btn sm" data-sop="splitsent" data-ord="${t.ord}">New sentence from here</button>` : ''}
      ${t.sent < n - 1 ? `<button class="btn sm" data-sop="mergesent" data-sent="${t.sent}">Merge with next sentence</button>` : ''}
      ${t.sent > 0 ? `<button class="btn sm" data-sop="mergesent" data-sent="${t.sent - 1}">Merge with previous</button>` : ''}
    </div>
    ${t.sent > 0 && si ? `<label class="chk" style="margin-top:8px"><input type="checkbox" data-sop="para" data-sent="${t.sent}" ${si.para_start ? 'checked' : ''}> Sentence ${t.sent} starts a paragraph</label>` : ''}
    <div class="row" style="margin-top:8px"><button class="btn sm" data-sop="retag" data-sent="${t.sent}">Re-tag sentence with spaCy</button></div>`;
}
/* Split a token by adding spaces to its word (the spelling can't change). */
async function splitWord(t, nv) {
  if (nv === t.word) return;
  if (nv.replace(/ /g, '') !== t.word) throw new Error('The spelling has to stay as in the original text. Only add spaces where the token should split.');
  await structural('POST', `/api/token/${t.uid}/split`, { word: nv });
}
/* Send a token/sentence structure edit, then show spaCy's re-tag suggestions or why there are none. */
async function structural(method, url, body) {
  const r = await api(method, url, body);
  updateHistoryButtons(r.history); setPending(r.pending);
  await refresh(true);
  if (r.retag_error) toast(`Done. No spaCy suggestions: ${r.retag_error}`);
  else if (r.proposals) await openSuggestions(true);
  else toast('Done. spaCy suggests no changes.');
}
/* Run a sentence/token action from a button (split, merge, paragraph, re-tag). */
async function runSop(d) {
  try {
    if (d.sop === 'splitsent') await structural('POST', '/api/sentence/split', { ord: +d.ord });
    else if (d.sop === 'mergesent') await structural('POST', '/api/sentence/merge', { sent: +d.sent });
    else if (d.sop === 'mergetok') {
      closePop();
      const first = S.win.tokens.find(t => t.ord === +d.a);
      if (first) selectToken(first.ord, first.uid);
      await structural('POST', '/api/tokens/merge', { a: +d.a, b: +d.b });
    } else if (d.sop === 'para') {
      const r = await api('POST', '/api/paragraph', { sent: +d.sent, on: d.on === '1' });
      updateHistoryButtons(r.history); toast(r.history.undo); await refresh(false);
    } else if (d.sop === 'retag') {
      const r = await api('POST', '/api/retag', { sent: +d.sent });
      setPending(r.pending); await loadWindow({ keepScroll: true });
      if (r.proposals) await openSuggestions(true); else toast(`spaCy agrees with the current tags in sentence ${d.sent}.`);
    }
  } catch (e) { fail(e); }
}
/* Show or hide the 'Suggestions' button with the number of waiting suggestions. */
function setPending(n) {
  S.pending = n || 0;
  const b = $('#sugBtn');
  b.hidden = !S.pending;
  b.textContent = `Suggestions (${S.pending})`;
}
/* The Suggestions panel: spaCy's proposed changes by sentence, to accept or reject. */
async function openSuggestions(auto) {
  const p = await api('GET', '/api/proposals');
  setPending(p.count);
  const d = $('#drawer');
  if (!p.count) { if (!auto) toast('No pending suggestions'); if (d.dataset.kind === 'sug') closeDrawer(); return; }
  d.dataset.kind = 'sug';
  d.innerHTML = `<header><h2>spaCy suggestions</h2><button class="btn" id="dClose">Close</button></header>
    <div class="sugbar"><button class="btn primary" id="sAccept">Accept selected</button><button class="btn" id="sReject">Reject selected</button>
      <span class="num" id="sCount"></span></div>
    <p class="note sugnote">What spaCy (${esc(S.info.spacy_model || 'en_core_web_sm')}) would change after re-tagging, parsing each sentence on its own. Values you edited by hand are never suggested. Untick anything you want to keep.</p>
    <div class="sugs">${p.sentences.map(g => `<section class="sg">
      <div class="sgh"><label><input type="checkbox" data-sall="${g.sent}" checked> Sentence ${g.sent}</label><button class="link" data-sgo="${g.start}">Show</button></div>
      <div class="sgt">${esc(g.text)}</div>
      <table class="sgtab">${g.items.map(i => `<tr><td><input type="checkbox" data-sid="${i.id}" data-ss="${g.sent}" checked aria-label="Accept"></td>
        <td class="w">${esc(i.word)} <span class="num">t${i.ord}</span></td><td class="f">${FIELD_LABEL[i.field]}</td>
        <td class="o">${esc(i.old_label || i.old)}</td><td class="ar">→</td><td class="n">${esc(i.new_label || i.new)}</td></tr>`).join('')}</table></section>`).join('')}</div>`;
  d.hidden = false;
  const count = () => { const n = d.querySelectorAll('[data-sid]:checked').length; $('#sCount').textContent = `${n} of ${p.count} selected`; };
  count();
  $('#dClose').onclick = closeDrawer;
  d.onchange = e => {
    const all = e.target.closest('[data-sall]');
    if (all) d.querySelectorAll(`[data-ss="${all.dataset.sall}"]`).forEach(x => { x.checked = all.checked; });
    count();
  };
  d.onclick = e => { const go = e.target.closest('[data-sgo]'); if (go) goTo(+go.dataset.sgo).catch(fail); };
  const ids = () => [...d.querySelectorAll('[data-sid]:checked')].map(x => +x.dataset.sid);
  $('#sAccept').onclick = async () => {
    try {
      const sel = ids(); if (!sel.length) return toast('Nothing selected');
      const r = await api('POST', '/api/proposals/accept', { ids: sel });
      updateHistoryButtons(r.history); setPending(r.pending); toast(r.history.undo);
      await refresh(false); await openSuggestions(true);
    } catch (err) { fail(err); }
  };
  $('#sReject').onclick = async () => {
    try {
      const sel = ids(); if (!sel.length) return toast('Nothing selected');
      const r = await api('POST', '/api/proposals/reject', { ids: sel });
      setPending(r.pending); toast(`Rejected ${sel.length} suggestion${sel.length > 1 ? 's' : ''}`);
      await loadWindow({ keepScroll: true }); await openSuggestions(true);
    } catch (err) { fail(err); }
  };
}
$('#sugBtn').onclick = () => ($('#drawer').hidden || $('#drawer').dataset.kind !== 'sug') ? openSuggestions(false).catch(fail) : closeDrawer();


/* ------------------------------------------------------------ quotes view */
/* The Quotes view: filter by speaker or text, tick quotes, assign speakers in bulk (or the Speaker suggestions tab). */
async function renderQuotesView() {
  if (S.qv.pane === 'sug') return renderQuoteSug();
  const qv = S.qv;
  const q = new URLSearchParams({ speaker: qv.speaker, q: qv.q, offset: qv.offset, limit: 50 });
  const r = await api('GET', `/api/quotes?${q}`);
  if (S.view !== 'quotes' || S.mode !== 'table') return;
  const pages = r.total > 50 ? `<div class="rpager"><button class="btn sm" data-qact="page" data-off="${r.offset - 50}" ${r.offset === 0 ? 'disabled' : ''}>Previous</button><span class="num">${r.offset + 1}–${Math.min(r.total, r.offset + 50)} of ${r.total}</span><button class="btn sm" data-qact="page" data-off="${r.offset + 50}" ${r.offset + 50 >= r.total ? 'disabled' : ''}>Next</button></div>` : '';
  const card = x => {
    const has = x.char_id != null;
    const ctx = x.context.map((w, i) => {
      const o = x.context_start + i;
      let h = esc(w);
      if (x.ms != null && o >= x.ms && o <= x.me) h = `<b class="spk" style="--h:${hue(x.char_id ?? 0)}">${h}</b>`;
      return o >= x.s && o <= x.e ? `<mark>${h}</mark>` : h;
    }).join(' ');
    return `<li class="qcard" data-quid="${x.uid}" data-ord="${x.s}"><input type="checkbox" data-qchk="${x.uid}" ${qv.checked.has(x.uid) ? 'checked' : ''} aria-label="Tick this quote">
      <div class="qhead"><button class="spchip${has ? '' : ' none'}" data-qact="speaker" data-quid="${x.uid}" ${has ? `style="--h:${hue(x.char_id)}"` : ''}>${esc(has ? x.speaker : 'No speaker')}</button>
        <span class="num">S ${x.sent} · t${x.s}–${x.e}${x.ms != null ? ` · mention “${esc(x.m_phrase)}” t${x.ms}` : ''}</span>
        <button class="link" data-qact="goto" data-ord="${x.s}" data-end="${x.e}">Show in text</button><button class="link" data-qact="edit" data-quid="${x.uid}">Edit</button></div>
      <p class="qctx">${x.context_start > 0 ? '… ' : ''}${ctx} …</p></li>`;
  };
  $('#main').innerHTML = `<div class="qwrap">${quotesNav()}
    <div class="qtools">
      <select id="qspk" aria-label="Speaker"><option value="all">All speakers (${r.speakers.reduce((n, x) => n + x.n, 0) + r.no_speaker})</option>
        <option value="none" ${qv.speaker === 'none' ? 'selected' : ''}>No speaker (${r.no_speaker})</option>
        ${r.speakers.map(x => `<option value="${x.id}" ${qv.speaker === String(x.id) ? 'selected' : ''}>${esc(x.name)} (${x.n})</option>`).join('')}</select>
      <input type="search" id="qtext" placeholder="Search quote text" value="${esc(qv.q)}" aria-label="Search quote text">
      <span class="num">${r.total} quote${r.total === 1 ? '' : 's'}</span>
      ${r.items.length ? '<button class="link" data-qact="selpage">Tick this page</button>' : ''}
    </div>
    ${qv.checked.size ? `<div class="qbulk"><span>${qv.checked.size} ticked</span><button class="btn primary sm" data-qact="bulk">Assign speaker…</button><button class="link" data-qact="clear">Clear</button></div>` : ''}
    ${pages}<ol class="qlist">${r.items.map(card).join('')}</ol>${!r.items.length ? '<p class="muted">No quotes match.</p>' : ''}${pages}</div>`;
  S.qv.items = r.items;
}
/* Clicks in the Quotes view: paging, speakers, jump to the text, edit. */
async function onQuotesClick(e) {
  if (onQuoteSugClick(e)) return;
  const qv = S.qv;
  if (e.target.matches('input, select')) return;
  const a = e.target.closest('[data-qact]');
  if (a) {
    const act = a.dataset.qact, rect = a.getBoundingClientRect();
    if (act === 'page') { qv.offset = Math.max(0, +a.dataset.off); await renderQuotesView(); $('#main').scrollTop = 0; }
    else if (act === 'goto') goTo(+a.dataset.ord, null, a.dataset.end != null ? +a.dataset.end : undefined).catch(fail);
    else if (act === 'edit') { try { await selectSpan('quotes', +a.dataset.quid); renderPanel(); } catch (err) { fail(err); } }
    else if (act === 'clear') { qv.checked.clear(); renderQuotesView().catch(fail); }
    else if (act === 'selpage') { (qv.items || []).forEach(x => qv.checked.add(x.uid)); renderQuotesView().catch(fail); }
    else if (act === 'speaker' || act === 'bulk') {
      const uids = act === 'bulk' ? [...qv.checked] : [+a.dataset.quid];
      groupPicker(rect, { title: uids.length > 1 ? `Speaker for ${uids.length} quotes` : 'Who says this?', allowNew: false, allowNone: true,
        actions: [{ key: 'assign_rule', label: 'Assign + Quote-Rule' }, { key: 'assign', label: 'Assign' }],
        onPick: async (t, name, key) => {
          if (act === 'bulk') qv.checked.clear();
          try {
            const r = await api('POST', '/api/quotes/speaker', { uids, char_id: t, quote_rule: key === 'assign_rule' });
            updateHistoryButtons(r);
            toast(r.moved ? `${r.undo || 'Assigned'} · moved ${r.moved} pronoun${r.moved === 1 ? '' : 's'}` : (r.undo || 'Assigned'));
            await refresh(true);
          } catch (err) { fail(err); }
        } });
    }
    return;
  }
  const c = e.target.closest('.qcard');
  if (c) { try { await selectSpan('quotes', +c.dataset.quid); renderPanel(); document.querySelectorAll('.qcard.on').forEach(x => x.classList.remove('on')); c.classList.add('on'); } catch (err) { fail(err); } }
}

/* character/quote view inputs */
let viewInputTimer = null;
$('#main').addEventListener('input', e => {
  const t = e.target;
  if (t.id === 'qtext') { S.qv.q = t.value; S.qv.offset = 0; clearTimeout(viewInputTimer); viewInputTimer = setTimeout(() => renderQuotesView().then(() => { const i = $('#qtext'); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }).catch(fail), 250); }
});
$('#main').addEventListener('change', e => {
  const t = e.target, ch = S.ch;
  if (t.id === 'qspk') { S.qv.speaker = t.value; S.qv.offset = 0; S.qv.checked.clear(); renderQuotesView().catch(fail); }
  else if (t.dataset.qchk != null) { const id = +t.dataset.qchk; t.checked ? S.qv.checked.add(id) : S.qv.checked.delete(id); renderQuotesView().catch(fail); }
  else if (t.dataset.hchk != null) { const id = +t.dataset.hchk; t.checked ? S.find.checked.add(id) : S.find.checked.delete(id); redrawBulk(); }
  else if (t.closest('.bulk')) onBulkChange(t);
});
$('#main').addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  if (e.target.id === 'gname' || e.target.id === 'gpron') { e.preventDefault(); e.target.blur(); }
  else if (e.target.matches('.bulk input')) { e.preventDefault(); runBulk().catch(fail); }
});

/* ------------------------------------------------------------ review tracking */
/* The ✓ button that marks a sentence reviewed. */
function revMark(s) {
  return `<button class="rv${s.reviewed ? ' on' : ''}" data-rev="${s.index}" data-on="${s.reviewed ? 0 : 1}" title="${s.reviewed ? 'Reviewed. Click to unmark' : 'Mark this sentence as reviewed (r)'}" aria-pressed="${s.reviewed}">${s.reviewed ? '✓ reviewed' : '✓'}</button>`;
}
/* Update the 'reviewed / total' counter in the header. */
function setProgress(p) {
  if (!p) return;
  S.progress = p;
  $('#revProg').textContent = `${p.reviewed.toLocaleString()} / ${p.total.toLocaleString()} ✓`;
  $('#revProg').title = `${p.reviewed.toLocaleString()} of ${p.total.toLocaleString()} sentences marked as reviewed`;
}
/* Mark sentences as reviewed (or not) and redraw. */
async function setReviewed(sents, on) {
  try {
    const r = await api('POST', '/api/review', { sents, on });
    updateHistoryButtons(r.history); setProgress(r.progress);
    await loadWindow({ keepScroll: true });
    renderPanel();
  } catch (e) { fail(e); }
}
/* Press r: toggle the reviewed mark of the sentence with the selected word. */
function toggleReviewFromSelection() {
  const r = selectionRange();
  const t = r ? S.win.tokens.find(x => x.ord === r[0]) : null;
  if (!t) { toast('Select a word in the sentence first'); return; }
  const s = S.win.sentences.find(x => x.index === t.sent);
  setReviewed([t.sent], !(s && s.reviewed));
}
$('#nextRevBtn').onclick = async () => {
  try {
    const r = selectionRange();
    const t = r && S.win ? S.win.tokens.find(x => x.ord === r[0]) : null;
    const after = t ? t.sent : (S.win ? S.win.first - 1 : -1);
    const n = await api('GET', `/api/review/next?after=${after}`);
    setProgress(n.progress);
    if (n.sent == null) { toast('Every sentence is marked as reviewed'); return; }
    if (S.view === 'chars' || S.view === 'quotes' || S.view === 'flags') { S.view = S.textView || 'annot'; localStorage.setItem('bne.view', S.view); }
    S.mode = 'table'; S.sent = n.sent; await loadWindow(); $('#main').scrollTop = 0;
    const s = S.win.sentences[0];
    const tok = S.win.tokens.find(x => x.ord === s.start);
    if (tok) { selectToken(tok.ord, tok.uid); paintSelection(); renderPanel(); }
  } catch (e) { fail(e); }
};


