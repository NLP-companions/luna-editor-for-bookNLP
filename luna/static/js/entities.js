'use strict';
/* The Entities tab: groups list, group pages, merge suggestions, fragment clean-up and the quote rule.
   Also the navigation history behind the Back button, and the group picker used everywhere. */

Object.assign(S.ch, { pane: 'overview', unchecked: false, mq: 0, fsize: 2, fcat: '', fskip: 0, bopen: null, bexcl: new Set(), qrExcl: new Set() });
S.recent = [];
/* more tools register here (rules.js): { label, badge, html(), click(e) → handled?, key(k, e) → handled? } */
const EXTRA_PANES = {};
S.hotkeys = {};

/* ------------------------------------------------------------ navigation history (Back button, ⌘[) */
/* Navigation state: restoring (ignore pushes while going back) and ready (start-up finished). */
const NAV = { restoring: false, ready: false };
/* The place the user is at (view, tool, group, page), stored in the browser history for Back. */
function navState() {
  return { v: S.view, p: S.ch.pane, g: S.ch.sel, o: S.ch.offset, qs: S.qv.speaker, qp: S.qv.pane || 'list', s: S.sent, d: (history.state && history.state.d) || 0 };
}
/* The address hash for a place, so reloading or sharing a link returns to it. */
function navHash(st) {
  const q = new URLSearchParams({ v: st.v });
  if (st.v === 'chars') { q.set('p', st.p); if (st.g != null) q.set('g', st.g); }
  else if (st.v === 'quotes') { q.set('qp', st.qp || 'list'); if ((st.qp || 'list') === 'list') q.set('qs', st.qs); }
  else q.set('s', st.s);
  return '#' + q.toString();
}
/* Are two places the same (then no new history entry is added)? */
function sameNav(a, b) {
  if (!a || a.v !== b.v) return false;
  if (b.v === 'chars') return a.p === b.p && a.g === b.g;
  if (b.v === 'quotes') return a.qs === b.qs && (a.qp || 'list') === (b.qp || 'list');
  return a.s === b.s;
}
/* Store the current place in the current history entry (before leaving it). */
function saveNav() {
  if (NAV.restoring || !NAV.ready || !S.info) return;
  const st = navState();
  history.replaceState(st, '', navHash(st));
}
/* Alias of saveNav. */
const replaceNav = saveNav;
/* Add the current place as a new history entry (after arriving), enabling Back. */
function pushNav() {
  if (NAV.restoring || !NAV.ready || !S.info) return;
  const st = navState();
  if (sameNav(history.state, st)) { history.replaceState({ ...st, d: history.state.d }, '', navHash(st)); return; }
  st.d = ((history.state && history.state.d) || 0) + 1;
  history.pushState(st, '', navHash(st));
  updateBack();
}
/* Enable the Back button when there is somewhere to go back to. */
function updateBack() { const b = $('#backBtn'); if (b) b.disabled = !(history.state && history.state.d > 0); }
/* Go to a stored place (Back button, ⌘[, or a reloaded address). */
async function restoreNav(st) {
  if (!st) return;
  NAV.restoring = true;
  try {
    closePop();
    S.ch.pane = st.p || 'overview'; S.ch.sel = st.g ?? null; S.ch.offset = st.o || 0;
    if (st.qs) S.qv.speaker = st.qs;
    if (st.qp) S.qv.pane = st.qp;
    if (st.v && st.v !== S.view) {
      S.view = st.v; localStorage.setItem('bne.view', st.v);
      if (st.v === 'table' || st.v === 'annot') S.textView = st.v;
    }
    S.mode = 'table';
    if ((S.view === 'table' || S.view === 'annot') && st.s != null && S.win && st.s !== S.win.first) { S.sent = st.s; await loadWindow(); }
    else renderMain();
  } catch (e) { fail(e); }
  finally { NAV.restoring = false; updateBack(); }
}
window.addEventListener('popstate', e => restoreNav(e.state));
$('#backBtn').onclick = () => history.back();
/* Read a place from the address hash. */
function parseNavHash() {
  const q = new URLSearchParams(location.hash.slice(1));
  if (!q.get('v')) return null;
  return { v: q.get('v'), p: q.get('p') || 'overview', g: q.has('g') ? +q.get('g') : null, o: 0, qs: q.get('qs') || 'all', qp: q.get('qp') || 'list',
    s: q.has('s') ? +q.get('s') : null, d: (history.state && history.state.d) || 0 };
}
// read before start-up can change the address
const INITIAL_NAV = history.state && history.state.v ? history.state : parseNavHash();
/* After start-up: go to the place in the address (if any) and start recording history. */
async function startNav() {
  if (INITIAL_NAV) await restoreNav(INITIAL_NAV);
  NAV.ready = true;
  saveNav();
  updateBack();
}

/* ------------------------------------------------------------ hotkeys and recent groups */
/* Fetch the groups pinned to number keys. */
async function loadHotkeys() { try { S.hotkeys = await api('GET', '/api/hotkeys'); } catch { S.hotkeys = {}; } }
/* Pin a group to a number key (or free the key) and remember it in the book. */
async function setHotkey(key, gid) {
  S.hotkeys = await api('PUT', '/api/hotkeys', { key, group: gid });
  toast(gid == null ? `Key ${key} cleared` : `Key ${key} → ${S.clusterById.get(gid)?.name || '#' + gid}`);
}
/* The number key a group is pinned to, or null. */
function keyOf(gid) { for (const [k, v] of Object.entries(S.hotkeys)) if (v.id === gid) return k; return null; }
/* Remember a group as recently chosen (offered first in pickers). */
function noteRecent(gid) { if (gid == null) return; S.recent = [gid, ...S.recent.filter(x => x !== gid)].slice(0, 6); }
/* A drop-down to pin a group to a number key. */
function keySelect(gid, id) {
  const cur = keyOf(gid);
  return `<select class="keysel" id="${id}" data-keygid="${gid}" aria-label="Pin to a number key" title="Pin this group to a number key, then press it to assign selected mentions">
    <option value="">Key –</option>${'123456789'.split('').map(k => `<option value="${k}" ${cur === k ? 'selected' : ''}>Key ${k}${S.hotkeys[k] && S.hotkeys[k].id !== gid ? ` (now ${esc(trim(S.hotkeys[k].name, 14))})` : ''}</option>`).join('')}</select>`;
}
document.addEventListener('change', async e => {
  const t = e.target.closest('[data-keygid]'); if (!t) return;
  const gid = +t.dataset.keygid, k = t.value;
  try {
    if (k) await setHotkey(k, gid);
    else { const cur = keyOf(gid); if (cur) await setHotkey(cur, null); }
    if (S.view === 'chars') renderChars().catch(fail);
    if (S.gside) renderGside();
  } catch (err) { fail(err); }
});

/* ------------------------------------------------------------ group picker (with nearby, keys and recent) */
/* A small clickable group name with its colour and number. */
function groupChip(g, extra = '') {
  const c = S.clusterById.get(g.id) || g;
  return `<button type="button" class="cchip pchip" data-pick="${g.id}" title="${esc(c.name)} · group ${g.id} · ${c.count ?? ''} mentions${extra ? ' · ' + esc(extra) : ''}"><span class="sw" style="--h:${hue(g.id)}"></span>${esc(trim(c.name, 22))}<span class="num">#${g.id}</span></button>`;
}
/* A side panel's group/speaker field, shown as a coloured chip (like a quote's speaker in the Quotes tab) that opens
   the group picker, instead of a plain text field; `noneLabel` is shown when there's no group yet. Used for both the
   Entity side panel's Group field and the Quote side panel's Speaker field, so they look and work the same way. */
function groupFieldChip(id, noneLabel) {
  if (id == null) return `<button type="button" class="spchip none" data-act="pickgroup">${esc(noneLabel)}</button>`;
  const c = S.clusterById.get(id);
  return `<button type="button" class="spchip" style="--h:${hue(id)}" data-act="pickgroup" title="group ${id}">${esc(c ? c.name : '#' + id)}</button>`;
}
/* The popup for choosing a group: type a name, or pick from nearby, pinned and recent groups.

   Normally has one button (`okLabel`); pass `actions` instead (a list of {key, label}) for more than one way to
   apply the same choice (e.g. quote speakers: "Assign" vs "Assign + Quote-Rule") — `onPick(target, name, key)` then
   gets the clicked action's `key` as a third argument (undefined for a plain `okLabel` picker). Picking a chip
   (nearby/keys/recent) always uses the first action, since a click is meant to be quick. */
function groupPicker(rect, { title, allowNone = false, allowNew = true, exclude = [], near = null, placeholder = null, okLabel = null, actions = null, onPick }) {
  const keys = Object.entries(S.hotkeys).filter(([, v]) => !exclude.includes(v.id));
  const recent = S.recent.filter(id => !exclude.includes(id) && S.clusterById.has(id));
  const buttons = actions
    ? actions.map((a, i) => `<button class="btn sm${i === 0 ? ' primary' : ''}" data-gp="act" data-gpk="${esc(a.key)}">${esc(a.label)}</button>`).join('')
    : `<button class="btn primary sm" data-gp="ok">${esc(okLabel || (allowNew ? 'Move' : 'Apply'))}</button>`;
  pop.innerHTML = `<div class="gp"><div class="gpt">${esc(title)}</div>
    <input id="gpi" list="dl-clusters" placeholder="${esc(placeholder || (allowNew ? 'Group name or #number; empty = new group' : allowNone ? 'Speaker; empty = no one' : 'Group name or #number'))}" aria-label="Group">
    ${near ? '<div class="gps"><span class="gpl">Nearby</span><span id="gpNear" class="gpc muted">…</span></div>' : ''}
    ${keys.length ? `<div class="gps"><span class="gpl">Keys</span><span class="gpc">${keys.map(([k, v]) => groupChip(v, 'key ' + k).replace('<span class="sw"', `<kbd>${k}</kbd><span class="sw"`)).join('')}</span></div>` : ''}
    ${recent.length ? `<div class="gps"><span class="gpl">Recent</span><span class="gpc">${recent.map(id => groupChip({ id })).join('')}</span></div>` : ''}
    <div class="row">${buttons}<button class="btn sm" data-gp="cancel">Cancel</button></div>
    ${allowNew ? '<p class="note">Type a new name to start a new group with that name.</p>' : ''}</div>`;
  pop.hidden = false;
  const place = () => {
    const pw = pop.offsetWidth, ph = pop.offsetHeight;
    pop.style.left = Math.min(innerWidth - pw - 8, Math.max(8, rect.left)) + 'px';
    pop.style.top = (rect.bottom + ph + 8 > innerHeight ? Math.max(8, rect.top - ph - 6) : rect.bottom + 6) + 'px';
  };
  place();
  const inp = $('#gpi'); inp.focus();
  if (near) {
    api('GET', `/api/nearby?a=${near.a}&b=${near.b}&exclude=${exclude.join(',')}`).then(list => {
      const el = $('#gpNear'); if (!el) return;
      el.classList.remove('muted');
      el.innerHTML = list.filter(g => !g.hidden).slice(0, 6).map(g => groupChip(g, `${g.distance} words ${g.side}`)).join('') || '<span class="muted">No other groups nearby</span>';
      place();
    }).catch(() => { const el = $('#gpNear'); if (el) el.textContent = ''; });
  }
  const finish = (target, name, key) => {
    if (target != null && exclude.includes(target)) { toast('That is the same group'); return; }
    closePop(); noteRecent(target); onPick(target, name, key);
  };
  const go = key => {
    const v = inp.value.trim();
    let target = null, name = null;
    if (!v) { if (!allowNew && !allowNone) return toast('Choose a group'); }
    else {
      try { target = parseCluster(v); }
      catch (e) { if (!allowNew) { toast(e.message); return; } name = v.replace(/\s*#-?\d+\s*$/, ''); }
    }
    finish(target, name, key);
  };
  pop.onclick = e => {
    const c = e.target.closest('[data-pick]'); if (c) { finish(+c.dataset.pick, null, actions ? actions[0].key : undefined); return; }
    const b = e.target.closest('[data-gp]'); if (!b) return;
    if (b.dataset.gp === 'ok') go(); else if (b.dataset.gp === 'act') go(b.dataset.gpk); else closePop();
  };
  pop.onkeydown = e => { if (e.key === 'Enter' && e.target === inp) { e.preventDefault(); go(actions ? actions[0].key : undefined); } if (e.key === 'Escape') closePop(); e.stopPropagation(); };
}
/* Run an edit, then show its undo label and refresh everything. */
async function charsDo(fn) {
  try {
    const r = await fn();
    if (r && (r.history || r.undo !== undefined)) { updateHistoryButtons(r.history || r); toast((r.history || r).undo || 'Done'); }
    await refresh(true);
  } catch (e) { fail(e); }
}

/* ------------------------------------------------------------ list */
// "PER +VAR": a group's main type, then the other types it has mentions of (they're listed under those types too)
function typeLabel(c) {
  const others = Object.keys(c.cats || {}).filter(t => t !== c.cat);
  return esc(c.cat) + (others.length ? ` <span class="also" title="Also has ${esc(others.join(', '))} mentions">+${esc(others.join(' +'))}</span>` : '');
}
/* One row of the groups list (colour, name, key, checked mark, types, counts); in the Compare pane, an A/B badge
   instead of the checkbox marks the two groups being compared. */
function groupRow(c) {
  const ch = S.ch, k = keyOf(c.id);
  const cmp = ch.pane === 'compare' ? (c.id === ch.cmpA ? 'A' : c.id === ch.cmpB ? 'B' : null) : null;
  return `<li class="crow${ch.sel === c.id && ch.pane === 'group' ? ' sel' : ''}${cmp ? ' sel' : ''}${c.hidden ? ' hid' : ''}" data-gid="${c.id}">
    ${ch.pane === 'compare' ? `<span class="cmpmark">${cmp || ''}</span>` : `<input type="checkbox" data-gchk="${c.id}" ${ch.checked.has(c.id) ? 'checked' : ''} aria-label="Tick ${esc(c.name)} for merging">`}
    <span class="sw" style="--h:${hue(c.id)}"></span>
    <span class="cn">${esc(c.name)}</span>
    <span class="ct">${k ? `<kbd title="Key ${k}">${k}</kbd>` : ''}${c.checked ? '<span class="ok" title="Checked">✓</span>' : ''}${typeLabel(c)}</span>
    <span class="num">${c.count}${c.quotes ? ` · ${c.quotes}q` : ''}</span></li>`;
}
/* The tool tabs above the Entities pane (overview, merge suggestions, fragments, quote rule, plus tools from rules.js). */
function paneNav() {
  const p = S.ch.pane;
  const b = (k, l, id) => `<button data-pane="${k}" aria-pressed="${p === k}">${l}${id ? ` <span class="badge" id="${id}"></span>` : ''}</button>`;
  const ex = Object.entries(EXTRA_PANES);
  const before = ex.filter(([, v]) => v.before).map(([k, v]) => b(k, v.label, v.badge)).join('');
  const after = ex.filter(([, v]) => !v.before).map(([k, v]) => b(k, v.label, v.badge)).join('');
  return `<nav class="enav" aria-label="Entity tools">${b('overview', 'Overview')}${b('merge', 'Merge suggestions', 'bdMerge')}${b('fragments', 'Fragments', 'bdFrag')}${before}${b('quoterule', 'Quote rule', 'bdQr')}${b('compare', 'Compare')}${after}</nav>`;
}
/* The Entities view: groups list on the left, the chosen tool or group page on the right. */
async function renderChars() {
  const ch = S.ch;
  const seq = renderChars.seq = (renderChars.seq || 0) + 1;
  if (ch.pane === 'group' && ch.sel == null) ch.pane = 'overview';
  const q = new URLSearchParams({ cat: ch.cat, q: ch.q, hidden: ch.hidden ? 1 : 0, sort: ch.sort, unchecked: ch.unchecked ? 1 : 0 });
  const [list] = await Promise.all([api('GET', `/api/groups?${q}`), Object.keys(S.hotkeys).length ? null : loadHotkeys()]);
  ch.list = list;
  const cats = Object.entries(list.cats).sort((a, b) => b[1] - a[1]);
  if (ch.cat && !(ch.cat in list.cats)) cats.push([ch.cat, 0]);  // keep showing the filter that's on
  let paneHTML;
  try {
    paneHTML = ch.pane === 'group' ? await groupDetailHTML(ch.sel)
      : ch.pane === 'merge' ? await mergePaneHTML()
      : ch.pane === 'fragments' ? await fragPaneHTML()
      : ch.pane === 'quoterule' ? await quoteRulePaneHTML()
      : ch.pane === 'compare' ? await comparePaneHTML()
      : EXTRA_PANES[ch.pane] ? await EXTRA_PANES[ch.pane].html()
      : await overviewPaneHTML();
  } catch (e) {
    if (ch.pane === 'group') { ch.sel = null; ch.pane = 'overview'; paneHTML = await overviewPaneHTML(); toast(e.message); }
    else throw e;
  }
  const pr = list.progress;
  const html = `<div class="cv">
    <aside class="cl">
      <div class="ctools">
        <input type="search" id="gq" placeholder="Find a name" value="${esc(ch.q)}" aria-label="Find a group by name">
        <select id="gcat" aria-label="Entity type"><option value="">All types</option>${cats.map(([k, n]) => `<option value="${esc(k)}" ${ch.cat === k ? 'selected' : ''}>${esc(k)} (${n})</option>`).join('')}</select>
        <select id="gsort" aria-label="Sort">${[['mentions', 'Most mentions'], ['quotes', 'Most quotes'], ['name', 'Name'], ['id', 'Group number']].map(([v, l]) => `<option value="${v}" ${ch.sort === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
        <label class="chk small"><input type="checkbox" id="gunchk" ${ch.unchecked ? 'checked' : ''}> Not checked yet only</label>
        <label class="chk small"><input type="checkbox" id="ghid" ${ch.hidden ? 'checked' : ''}> Show “not a character” (${list.hidden_count})</label>
        <div class="gprog" title="Groups you’ve marked as checked"><span class="prog"><i style="width:${pr.total ? Math.round(100 * pr.checked / pr.total) : 0}%"></i></span>${pr.checked.toLocaleString()} / ${pr.total.toLocaleString()} checked</div>
      </div>
      ${ch.checked.size ? `<div class="mergebar"><span>${ch.checked.size} ticked</span><button class="btn primary sm" data-gact="mergechk" ${ch.checked.size < 2 ? 'disabled' : ''}>Merge…</button><button class="link" data-gact="clearchk">Clear</button></div>` : ''}
      <ul class="crows">${list.items.map(groupRow).join('')}${list.total > list.items.length ? `<li class="more">${list.total - list.items.length} more. Narrow the list to see them.</li>` : ''}${!list.items.length ? '<li class="more">No groups match.</li>' : ''}</ul>
    </aside>
    <section class="cd" id="cd">${paneNav()}${paneHTML}</section></div>`;
  if (S.view !== 'chars' || S.mode !== 'table' || seq !== renderChars.seq) return;
  const keep = $('#cd') ? $('#cd').scrollTop : 0;
  const keepL = $('.crows') ? $('.crows').scrollTop : 0;
  const samePane = renderChars._last === `${ch.pane}:${ch.sel}`;
  renderChars._last = `${ch.pane}:${ch.sel}`;
  $('#main').innerHTML = html;
  if (samePane) $('#cd').scrollTop = keep;
  $('.crows').scrollTop = keepL;
  const selRow = $('.crow.sel'); if (selRow && !samePane) selRow.scrollIntoView({ block: 'nearest' });
  if (ch.pane === 'group') { loadBookPreview(ch.sel); loadGroupMatches(ch.sel); }
  loadBadges();
}
/* Fill in the counts on the tool tabs and overview cards. */
async function loadBadges() {
  const set = (id, n) => { const el = $('#' + id); if (el) { el.textContent = n ? n.toLocaleString() : ''; el.hidden = !n; } };
  try {
    const [m, f, q] = await Promise.all([api('GET', '/api/merge-suggestions?limit=1'), api('GET', `/api/fragments?size=${S.ch.fsize}&limit=1`), api('POST', '/api/quote-rule', { scope: 'book' })]);
    set('bdMerge', m.total); set('bdFrag', f.total); set('bdQr', q.n);
    const extra = typeof extraBadges === 'function' ? await extraBadges(set) : '';
    const ov = $('#ovCounts');
    if (ov) ov.innerHTML = ovCountsHTML(m, f, q) + extra;
  } catch { }
}
/* Open a group's page in Entities. */
function openGroup(gid) { openPane('group', gid); }
/* Open an Entities tool from anywhere (switches to the Entities view first). */
function openPane(pane, sel) {
  if (S.view === 'chars') { setPane(pane, sel); return; }
  S.ch.pane = pane;
  if (sel !== undefined) { S.ch.sel = sel; S.ch.offset = 0; S.ch.mchecked.clear(); }
  setView('chars');
}
/* Change tool or group inside Entities (adds a history entry). */
function setPane(pane, sel) {
  saveNav();
  S.ch.pane = pane;
  if (sel !== undefined) S.ch.sel = sel;
  if (pane === 'group') { S.ch.offset = 0; S.ch.mchecked.clear(); }
  renderChars().then(() => { $('#cd').scrollTop = 0; pushNav(); }).catch(fail);
}

/* ------------------------------------------------------------ overview */
/* The three tool cards of the overview with their counts. */
function ovCountsHTML(m, f, q) {
  const card = (pane, n, title, text) => `<button class="tool" data-pane="${pane}"><b>${n.toLocaleString()}</b><span class="tt">${title}</span><span class="td">${text}</span></button>`;
  return card('merge', m.total, 'Merge suggestions', `Pairs of groups that may be the same, with reasons. Review with M, N, S.${m.bundles && m.bundles.length ? ` Includes ${m.bundles.length} set${m.bundles.length === 1 ? '' : 's'} of same-name groups to merge in one go.` : ''}`)
    + card('fragments', f.total, `Fragments (${S.ch.fsize} or fewer mentions)`, 'Small groups one by one, with likely targets. Assign with number keys.')
    + card('quoterule', q.n, 'Quote rule', 'First-person pronouns in quotes that aren’t in their speaker’s group.');
}
/* Entities overview: tool cards, pinned keys and the entity types for the exported .book. */
async function overviewPaneHTML() {
  const bs = await api('GET', '/api/book-settings');
  const keys = Object.entries(S.hotkeys);
  return `<div class="overview"><h2>Entities</h2>
    <p class="muted">Every coreference group BookNLP found, of any entity type. Pick one on the left to rename it, set pronouns, see all its mentions and split off wrong ones. Tick two or more to merge them.</p>
    <div class="tools" id="ovCounts"><span class="muted">Counting…</span></div>
    <h3>Number keys</h3>
    <p class="note" style="margin-top:0">Pin groups to keys 1–9 (on a group’s page or in the Groups sidebar). In the text, select mentions or words and press the key to assign them.</p>
    ${keys.length ? `<div class="keylist">${keys.map(([k, v]) => `<span class="keyrow"><kbd>${k}</kbd>${groupChip(v)}<button class="link" data-unkey="${k}">Clear</button></span>`).join('')}</div>` : '<p class="muted">No keys pinned yet.</p>'}
    <h3>Entity types in the exported .book</h3>
    <div class="booktypes">${bs.available.map(a => `<label class="chk"><input type="checkbox" data-booktype="${esc(a.cat)}" ${bs.types.includes(a.cat) ? 'checked' : ''}> ${esc(a.cat)} <span class="num">${a.groups} group${a.groups === 1 ? '' : 's'}</span></label>`).join('')}</div>
    <p class="note">BookNLP’s .book lists only PER groups. Any type you tick here is included by the same rule: groups with at least two mentions of the ticked types. When you include more than PER, each entry gets a <code>type</code> field. Takes effect on the next export.</p></div>`;
}

/* ------------------------------------------------------------ group page */
/* Words around a mention, the mention highlighted in its group colour. */
function ctxWords(words, start, a, b, h) {
  return (start > 0 ? '… ' : '') + words.map((w, i) => { const o = start + i; return o >= a && o <= b ? `<b style="--h:${h}">${esc(w)}</b>` : esc(w); }).join(' ') + ' …';
}
/* A group's page: name, pronouns, checked, names and references, and all its mentions with tick boxes. */
async function groupDetailHTML(cid) {
  const d = await api('GET', `/api/group/${cid}?offset=${S.ch.offset}&limit=150`);
  S.ch.detail = d;
  const mc = S.ch.mchecked;
  const vrows = d.variants.slice(0, 120).map(v => `<tr><td class="v">${esc(v.text)}</td><td class="t">${v.prop}</td><td class="num">${v.n}</td>
    <td class="a"><button class="link" data-gact="goto" data-ord="${v.first}">Show</button><button class="link" data-gact="movevar" data-text="${esc(v.text)}" data-prop="${v.prop}">Move to…</button></td></tr>`).join('');
  const pages = d.total > 150 ? `<div class="rpager"><button class="btn sm" data-gact="mpage" data-off="${d.offset - 150}" ${d.offset === 0 ? 'disabled' : ''}>Previous</button><span class="num">${d.offset + 1}–${Math.min(d.total, d.offset + 150)} of ${d.total}</span><button class="btn sm" data-gact="mpage" data-off="${d.offset + 150}" ${d.offset + 150 >= d.total ? 'disabled' : ''}>Next</button></div>` : '';
  const chain = d.mentions.map(m => `<li class="mrow" data-muid="${m.uid}" data-ord="${m.s}" data-e="${m.e}">
      <input type="checkbox" data-mchk="${m.uid}" ${mc.has(m.uid) ? 'checked' : ''} aria-label="Tick this mention">
      <span class="num">S ${m.sent} · t${m.s}</span><span class="mp">${m.prop}${m.cat !== d.cat ? ' · ' + esc(m.cat) : ''}</span>
      <span class="mctx">${ctxWords(m.context, m.context_start, m.s, m.e, hue(d.id))}</span></li>`).join('');
  const scopeBtns = ['sentence', 'paragraph', 'quote', 'conversation'].map(sc => `<button class="btn sm" data-gscope="${sc}" ${mc.size ? '' : 'disabled'}>${sc[0].toUpperCase() + sc.slice(1)}</button>`).join('');
  return `<div class="crumb"><button class="link" data-pane="overview">Entities</button> › ${esc(d.name)}</div>
    <div class="dhead"><span class="sw big" style="--h:${hue(d.id)}"></span>
      <input class="nameinput" id="gname" data-gid="${d.id}" value="${esc(d.name)}" placeholder="${esc(d.auto_name)}" aria-label="Group name">
      <button class="btn ${d.checked ? 'okbtn' : ''}" data-gact="check" title="Mark this group as checked (c)">${d.checked ? '✓ Checked' : 'Mark as checked'}</button>${flagButton('groups', d.id)}</div>
    <div class="sub">Group ${d.id} · ${Object.keys(d.cats).length > 1 ? Object.entries(d.cats).sort((a, b) => b[1] - a[1]).map(([t, n]) => `${esc(t)} ${n}`).join(', ') : esc(d.cat)}${d.named ? ` · BookNLP’s name: ${esc(d.auto_name)}` : ''}</div>
    <div class="facts">
      <label>Pronouns <input list="dl-pronouns" id="gpron" data-gid="${d.id}" value="${esc(d.pronoun || '')}" placeholder="not set"></label>
      <span>${d.total} mentions</span><span>${d.quotes} quotes</span>
      <label class="chk"><input type="checkbox" id="ghidden" data-gid="${d.id}" ${d.hidden ? 'checked' : ''}> Not a character</label>
      ${keySelect(d.id, 'gkey')}
    </div>
    <div class="row" style="margin:10px 0 4px">
      <button class="btn" data-gact="mergeinto">Merge into…</button>
      <button class="btn" data-gact="paint" title="Click mentions in the text to move them into this group">Paint in text</button>
      <button class="btn" data-gact="first">Step through in text</button>
      ${d.quotes ? `<button class="btn" data-gact="quotesof">Review its quotes</button>` : ''}
      <button class="btn" data-gact="findgroup">List in Find</button>
    </div>
    <div id="gmatches"></div>
    <h3>In the exported .book</h3>
    <div id="bookprev" class="bookprev note">Working it out…</div>
    <h3>Names and references <span class="num">${d.variants.length}</span></h3>
    <p class="note" style="margin-top:0">Move a name to another group, or to a new one, to split this group. Quotes attributed through those mentions move with them.</p>
    <div class="tablewrap"><table class="vt">${vrows}</table></div>
    <h3>All mentions in reading order <span class="num">${d.total}</span></h3>
    <div class="mbar">${mc.size ? `<span>${mc.size} ticked</span><button class="btn primary sm" data-gact="movechk">Move to…</button><button class="link" data-gact="clearm">Clear</button>`
      : '<span class="note">Tick mentions to move them together. Click a mention to edit it in the side panel.</span>'}
      <span class="scopes"><span class="note">Also tick this group’s mentions in the same</span>${scopeBtns}</span></div>
    ${pages}<ol class="chain">${chain}</ol>${pages}`;
}
/* Fill 'Possibly the same as' on a group's page. */
async function loadGroupMatches(cid) {
  let r;
  try { r = await api('GET', `/api/merge-suggestions?group=${cid}&limit=6`); } catch { return; }
  const el = $('#gmatches'); if (!el || S.ch.sel !== cid) return;
  if (!r.items.length) { el.innerHTML = ''; return; }
  el.innerHTML = `<h3>Possibly the same as</h3><ul class="gmlist">${r.items.map(p => {
    const other = p.a.id === cid ? p.b : p.a;
    return `<li>${groupChip(other)}<span class="gmr">${p.reasons.map(x => `<span class="${x.w >= 0 ? 'pos' : 'neg'}">${esc(x.text)}</span>`).join(' · ')}</span>
      <button class="btn sm" data-gm-merge="${other.id}">Merge</button><button class="link" data-gm-no="${p.key}">Not the same</button></li>`;
  }).join('')}</ul>`;
}
/* Fill 'In the exported .book' on a group's page. */
async function loadBookPreview(cid) {
  let p;
  try { p = await api('GET', `/api/book/${cid}`); } catch (e) { const el = $('#bookprev'); if (el) el.textContent = `Couldn’t work this out: ${e.message}`; return; }
  const el = $('#bookprev'); if (!el || S.ch.sel !== cid) return;
  if (!p.in_book) {
    const tl = p.types.join(', ');
    el.innerHTML = p.cat && !p.types.includes(p.cat)
      ? `Not included: this is a ${esc(p.cat)} group, and .book is set to include ${esc(tl)}. <button class="link" data-pane="overview">Change the types</button>`
      : `Not included: .book lists groups with at least two mentions of ${esc(tl)}, and this one has ${p.included_mentions}.`;
    return;
  }
  const words = (k, label) => p[k].length ? `<div class="bp"><span class="bpk">${label} <span class="num">${p.n[k]}</span></span><span>${p[k].map(x => `${esc(x.w)}${x.n > 1 ? ` <span class="num">${x.n}</span>` : ''}`).join(', ')}${p.n[k] > p[k].reduce((a, x) => a + x.n, 0) ? ' …' : ''}</span></div>` : '';
  const g = p.g ? `${esc(p.g.argmax)} <span class="num">(${Math.round(p.g.max * 100)}%)</span>` : '<span class="muted">none</span>';
  el.classList.remove('note');
  el.innerHTML = `<div class="bp"><span class="bpk">Listed as</span><span>“${esc(p.name || '')}”${p.type ? ` (${esc(p.type)})` : ''}, ${p.count} mention${p.count === 1 ? '' : 's'} of ${esc(p.types.join('/'))}, pronouns ${g}</span></div>
    ${words('agent', 'Does')}${words('patient', 'Is done to')}${words('poss', 'Has')}${words('mod', 'Is')}
    <p class="note" style="margin:6px 0 0">From ${esc(p.used)}. This updates as you correct heads, dependencies and groups.</p>`;
}

/* ------------------------------------------------------------ merge suggestions */
/* One side (group with context) of a merge suggestion. */
function sideHTML(g, ctx, role) {
  return `<div class="mside ${role}"><div class="msn"><span class="sw" style="--h:${hue(g.id)}"></span><b>${esc(g.name)}</b> <span class="num">#${g.id} · ${esc(g.cat)} · ${g.count} mention${g.count === 1 ? '' : 's'}${g.quotes ? ` · ${g.quotes} quotes` : ''}</span></div>
    ${ctx ? `<div class="mctx">${ctxWords(ctx.words, ctx.start, ctx.s, ctx.e, hue(g.id))}</div>` : ''}
    <div class="row"><button class="link" data-gsel="${g.id}">Open</button>${ctx ? `<button class="link" data-gact="goto" data-ord="${ctx.s}">Show in text</button>` : ''}</div></div>`;
}
/* Merge suggestions: same-name bundles and one pair at a time with its reasons. */
async function mergePaneHTML() {
  const r = await api('GET', '/api/merge-suggestions?limit=500');
  const ch = S.ch;
  S.ch.mqItems = r.items;
  if (ch.mq >= r.items.length) ch.mq = Math.max(0, r.items.length - 1);
  const p = r.items[ch.mq];
  const bundles = r.bundles.map((b, i) => {
    const open = ch.bopen === i;
    const n = b.drops.filter(x => !ch.bexcl.has(x.id)).length;
    return `<div class="bundle"><div class="bh"><span class="sw" style="--h:${hue(b.keep)}"></span><b>${esc(b.name)}</b><span class="num">#${b.keep} · ${b.count.toLocaleString()} mentions</span>
        <span>← ${b.drops.length.toLocaleString()} group${b.drops.length === 1 ? '' : 's'} with exactly the same name (${b.mentions.toLocaleString()} mentions)</span>
        <span class="grow"></span><button class="btn sm primary" data-bundle="${i}" data-bact="merge">Merge ${n === b.drops.length ? 'all' : n}</button><button class="btn sm" data-bundle="${i}" data-bact="toggle">${open ? 'Hide list' : 'Review list'}</button></div>
      ${open ? `<ul class="blist">${b.drops.map(x => `<li><label class="chk"><input type="checkbox" data-bx="${x.id}" ${ch.bexcl.has(x.id) ? '' : 'checked'}> #${x.id}</label>
        <span class="mctx">${x.ctx ? ctxWords(x.ctx.words, x.ctx.start, x.ctx.s, x.ctx.e, hue(x.id)) : ''}</span>${x.ctx ? `<button class="link" data-gact="goto" data-ord="${x.ctx.s}">Show</button>` : ''}</li>`).join('')}</ul>` : ''}</div>`;
  }).join('');
  const card = p ? `<div class="mcard" id="mcard">
      <div class="mhead"><span>Suggestion ${ch.mq + 1} of ${r.total.toLocaleString()}</span><span class="score" title="Sum of the reasons below">score ${p.score}</span></div>
      <div class="mpair">${sideHTML(p.a, p.a_ctx, 'keep')}<div class="marrow" aria-hidden="true">←</div>${sideHTML(p.b, p.b_ctx, 'drop')}</div>
      <ul class="reasons">${p.reasons.map(x => `<li class="${x.w >= 0 ? 'pos' : 'neg'}"><span class="w">${x.w > 0 ? '+' : ''}${x.w}</span>${esc(x.text)}</li>`).join('')}</ul>
      <div class="row mact"><button class="btn primary" data-mq="merge">Merge into “${esc(trim(p.a.name, 24))}” <kbd>M</kbd></button>
        <button class="btn" data-mq="swap">Keep “${esc(trim(p.b.name, 18))}” instead <kbd>B</kbd></button>
        <button class="btn" data-mq="no">Not the same <kbd>N</kbd></button><button class="btn" data-mq="skip">Skip <kbd>S</kbd></button>
        <button class="link" data-mq="prev" ${ch.mq ? '' : 'disabled'}>Previous <kbd>P</kbd></button></div>
    </div>` : '<p class="muted">No merge suggestions left. New ones appear as you edit.</p>';
  return `<div class="overview wide"><h2>Merge suggestions</h2>
    <p class="note" style="margin-top:0">Groups that may be the same, scored from their names, pronouns and titles. Evidence against counts too: groups named together in a sentence, or speaking to each other, are probably different people. “Not the same” is remembered.</p>
    ${bundles ? `<h3>Same name, merge in one go</h3>${bundles}<h3>One by one</h3>` : ''}${card}</div>`;
}
/* Act on the current merge suggestion (merge, keep the other, not the same, skip, previous). */
async function mergeAct(kind) {
  const ch = S.ch, p = (ch.mqItems || [])[ch.mq]; if (!p) return;
  if (kind === 'skip') { ch.mq++; renderChars().catch(fail); return; }
  if (kind === 'prev') { ch.mq = Math.max(0, ch.mq - 1); renderChars().catch(fail); return; }
  if (kind === 'no') { await charsDo(() => api('POST', '/api/group-suggestions/dismiss', { key: p.key })); toast('Remembered as not the same'); return; }
  const [keep, drop] = kind === 'swap' ? [p.b.id, p.a.id] : [p.a.id, p.b.id];
  await charsDo(() => api('POST', '/api/groups/merge', { target: keep, sources: [drop] }));
}

/* ------------------------------------------------------------ fragments */
/* Fragments: small groups one at a time with their mentions and candidate groups to assign them to. */
async function fragPaneHTML() {
  const ch = S.ch;
  const r = await api('GET', `/api/fragments?size=${ch.fsize}&cat=${encodeURIComponent(ch.fcat)}&offset=${ch.fskip}&limit=1`);
  if (ch.fskip && ch.fskip >= r.total) { ch.fskip = Math.max(0, r.total - 1); return fragPaneHTML(); }
  const f = r.items[0];
  ch.frag = f;
  const cats = Object.keys((ch.list && ch.list.cats) || {});
  if (ch.fcat && !cats.includes(ch.fcat)) cats.push(ch.fcat);
  const controls = `<div class="row fctl"><label>Groups with <select id="fsize">${[1, 2, 3, 5].map(n => `<option value="${n}" ${ch.fsize === n ? 'selected' : ''}>${n === 1 ? '1 mention' : `${n} or fewer mentions`}</option>`).join('')}</select></label>
    <label>Type <select id="fcat"><option value="">All</option>${cats.map(c => `<option ${ch.fcat === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></label>
    <span class="num">${r.total.toLocaleString()} left</span></div>`;
  if (!f) return `<div class="overview wide"><h2>Fragments</h2>${controls}<p class="muted">No fragments left with these settings. Groups you keep are marked as checked and don’t come back.</p></div>`;
  return `<div class="overview wide"><h2>Fragments</h2>
    <p class="note" style="margin-top:0">Small groups one at a time, in reading order. Assign each to a likely group with its number key, or keep, hide or delete it.</p>
    ${controls}
    <div class="mcard fcard" id="fcard">
      <div class="mhead"><span>Fragment ${ch.fskip + 1} of ${r.total.toLocaleString()}</span><span class="num">${esc(f.cat)} · group #${f.id}</span></div>
      <div class="msn"><span class="sw" style="--h:${hue(f.id)}"></span><b>${esc(f.name)}</b> <span class="num">${f.count} mention${f.count === 1 ? '' : 's'}</span></div>
      <ul class="fments">${f.mentions.map(m => `<li><span class="num">S ${m.sent}</span><span class="mctx">${ctxWords(m.ctx.words, m.ctx.start, m.ctx.s, m.ctx.e, hue(f.id))}</span><button class="link" data-gact="goto" data-ord="${m.s}">Show</button></li>`).join('')}</ul>
      <h3>Assign to</h3>
      <ol class="fcands">${f.candidates.map((c, i) => `<li><button class="btn fc" data-fc="${c.id}"><kbd>${i + 1}</kbd><span class="sw" style="--h:${hue(c.id)}"></span>${esc(c.name)} <span class="num">#${c.id} · ${esc(c.cat)} · ${c.count}</span></button><span class="note" title="${esc((c.reasons || []).map(r => `${r.w > 0 ? '+' : ''}${r.w} ${r.text}`).join('\n'))}">${c.score != null ? `<b>${c.score.toFixed(2)}</b> · ` : ''}${esc(c.reason || '')}</span></li>`).join('') || '<li class="muted">No groups nearby</li>'}</ol>
      <div class="row mact"><button class="btn" data-fa="other">Other group… <kbd>G</kbd></button>
        <button class="btn" data-fa="keep" title="Leave it as its own group and mark it as checked">Keep as is <kbd>K</kbd></button>
        <button class="btn" data-fa="hide">Not a character <kbd>H</kbd></button>
        <button class="btn danger" data-fa="delete">Delete mention${f.count === 1 ? '' : 's'} <kbd>D</kbd></button>
        <button class="btn" data-fa="skip">Skip <kbd>S</kbd></button><button class="link" data-fa="prev" ${ch.fskip ? '' : 'disabled'}>Previous <kbd>P</kbd></button></div>
    </div></div>`;
}
// The fragment joins the chosen group; whichever of the two is bigger keeps its number and settings.
function mergeKeepBigger(target, frag) {
  const c = S.clusterById.get(target);
  return (c ? c.count : 0) >= frag.count ? { target, sources: [frag.id] } : { target: frag.id, sources: [target] };
}
/* Act on the current fragment (assign, other group, keep, hide, delete, skip, previous). */
async function fragAct(kind, arg) {
  const ch = S.ch, f = ch.frag; if (!f) return;
  if (kind === 'skip') { ch.fskip++; renderChars().catch(fail); return; }
  if (kind === 'prev') { ch.fskip = Math.max(0, ch.fskip - 1); renderChars().catch(fail); return; }
  if (kind === 'assign') { noteRecent(arg); await charsDo(() => api('POST', '/api/groups/merge', mergeKeepBigger(arg, f))); return; }
  if (kind === 'keep') { await charsDo(() => api('PATCH', `/api/group/${f.id}`, { checked: true })); return; }
  if (kind === 'hide') { await charsDo(() => api('PATCH', `/api/group/${f.id}`, { hidden: true })); return; }
  if (kind === 'delete') { await charsDo(() => api('POST', '/api/mentions/delete', { uids: f.uids })); return; }
  if (kind === 'other') {
    const btn = $('[data-fa="other"]'); const m0 = f.mentions[0];
    groupPicker(btn.getBoundingClientRect(), { title: `Assign “${trim(f.name, 30)}” to`, allowNew: false, exclude: [f.id], near: { a: m0.s, b: m0.e },
      onPick: t => charsDo(() => api('POST', '/api/groups/merge', mergeKeepBigger(t, f))) });
  }
}

/* ------------------------------------------------------------ quote rule (whole book) */
/* Quote rule for the whole book: every first-person pronoun in a quote that is in another group, to untick and apply. */
async function quoteRulePaneHTML() {
  const r = await api('POST', '/api/quote-rule', { scope: 'book' });
  S.ch.qr = r;
  const ex = S.ch.qrExcl;
  const n = r.items.filter(i => !ex.has(i.uid)).length;
  return `<div class="overview wide"><h2>Quote rule</h2>
    <p class="note" style="margin-top:0">Inside a quote, <i>I, me, my, mine</i> and <i>myself</i> refer to the speaker. These ${r.n.toLocaleString()} are in another group than their quote’s speaker. Untick any you want to leave, then apply. It undoes in one step.${r.no_speaker ? ` ${r.no_speaker} quote${r.no_speaker === 1 ? '' : 's'} with first-person pronouns have no speaker yet; set those in the Quotes tab.` : ''}</p>
    ${r.n ? `<div class="row" style="margin:8px 0"><button class="btn primary" data-qr="apply">Move ${n === r.n ? 'all ' + r.n.toLocaleString() : n.toLocaleString()}</button>
      <button class="link" data-qr="all">Tick all</button><button class="link" data-qr="none">Untick all</button>${r.n > r.items.length ? `<span class="note">Showing the first ${r.items.length}; applying covers all ticked and the ones not shown.</span>` : ''}</div>
      <ul class="qrlist">${r.items.map(i => `<li><input type="checkbox" data-qrx="${i.uid}" ${ex.has(i.uid) ? '' : 'checked'} aria-label="Include">
        <span class="mctx">${ctxWords(i.context, i.context_start, i.s, i.e, hue(i.to))}</span>
        <span class="qrmv"><span class="sw" style="--h:${hue(i.from)}"></span>${esc(trim(i.from_name, 18))} → <span class="sw" style="--h:${hue(i.to)}"></span>${esc(trim(i.to_name, 18))}</span>
        <button class="link" data-gact="goto" data-ord="${i.s}">Show</button></li>`).join('')}</ul>` : '<p class="muted">Nothing to change: every first-person pronoun in a quote is in its speaker’s group.</p>'}</div>`;
}

/* ------------------------------------------------------------ compare (side-by-side entity matching) */
/* Pronoun classes (core/tools/rule.py PRON_CLASS) as words, for the profile display. */
const PRON_WORDS = { m: 'he', f: 'she', n: 'it', p: 'they', 1: 'I', '1p': 'we', 2: 'you' };
/* A character profile summary (core/profiles.py `summary`, from `/api/group/{id}/profile`) as short readable lines —
   the same layout as the library's "Full profile", so a group looks the same wherever its profile is shown. */
function profileHTML(s) {
  const join = xs => esc((xs || []).slice(0, 12).join(', '));
  const line = (label, xs) => `<div><span class="pl">${label}</span> ${xs && xs.length ? join(xs) : '<span class="muted">none</span>'}</div>`;
  return `<div class="lprof">${[['Titles', s.titles], ['Described as', s.nouns], ['Relations', s.relations], ['Appears with', s.cooccur],
    ['Places', s.places], ['Does', s.actions], ['Done to them', s.undergoes], ['Has', s.has], ['Described by', s.described],
    ['Pronouns', Object.entries(s.pronouns || {}).map(([k, v]) => `${PRON_WORDS[k] || k} ${Math.round(100 * v)}%`)],
    ['Speech', s.quotes ? [`${s.quotes} quotes, ${s.words.toLocaleString()} words`, ...s.speech_words] : []]]
    .map(([label, xs]) => line(label, xs)).join('')}</div>`;
}
/* One side of the Compare pane: the chosen group's header and full profile, or a prompt to pick one from the list. */
function compareCardHTML(gid, label, profile) {
  if (gid == null) return `<div class="cmpcard"><div class="cmphead"><span class="cmpletter">${label}</span><span class="muted">Click a group on the left</span></div></div>`;
  const c = S.clusterById.get(gid);
  return `<div class="cmpcard"><div class="cmphead"><span class="cmpletter">${label}</span><span class="sw" style="--h:${hue(gid)}"></span>
      <b>${esc(c ? c.name : '#' + gid)}</b><span class="num">#${gid}${c ? ` · ${typeLabel(c)} · ${c.count} mention${c.count === 1 ? '' : 's'}${c.quotes ? ` · ${c.quotes}q` : ''}` : ''}</span>
      <button class="link" data-gact="cmpPick" data-slot="${label}">Change…</button></div>
    ${profile ? profileHTML(profile) : '<p class="muted">No profile yet: it has no mentions.</p>'}</div>`;
}
/* The Compare pane: two chosen groups' profiles side by side (their "Full profile", as in the collection's list),
   with a menu to merge them once both are picked. Pick A and B by clicking groups in the list on the left. */
async function comparePaneHTML() {
  const ch = S.ch;
  const [pa, pb] = await Promise.all([
    ch.cmpA != null ? api('GET', `/api/group/${ch.cmpA}/profile`) : null,
    ch.cmpB != null ? api('GET', `/api/group/${ch.cmpB}/profile`) : null]);
  const merge = ch.cmpA != null && ch.cmpB != null ? `<div class="row cmpmerge">
    <button class="btn primary" data-gact="cmpMerge" data-into="b">Merge A into B</button>
    <button class="btn primary" data-gact="cmpMerge" data-into="a">Merge B into A</button>
    <button class="btn" data-gact="cmpMerge" data-into="other">Merge into other…</button>
    <button class="link" data-gact="cmpSwap">Swap</button><button class="link" data-gact="cmpClear">Clear both</button></div>` : '';
  return `<div class="overview wide"><h2>Compare</h2>
    <p class="note" style="margin-top:0">Click a group on the left to fill A, then another for B, to see their profiles in this book side by side. Click a card’s “Change…” to replace just that one.</p>
    ${merge}
    <div class="cmpgrid">${compareCardHTML(ch.cmpA, 'A', pa)}${compareCardHTML(ch.cmpB, 'B', pb)}</div></div>`;
}

/* ------------------------------------------------------------ events */
/* Every click in the Entities view (tools, lists, group page actions). */
async function onCharsClick(e) {
  if (e.target.matches('input, select')) return;
  const ch = S.ch;
  if (EXTRA_PANES[ch.pane] && EXTRA_PANES[ch.pane].click && await EXTRA_PANES[ch.pane].click(e)) return;
  const pn = e.target.closest('[data-pane]');
  if (pn) { setPane(pn.dataset.pane); return; }
  const unkey = e.target.closest('[data-unkey]');
  if (unkey) { try { await setHotkey(unkey.dataset.unkey, null); renderChars().catch(fail); } catch (err) { fail(err); } return; }
  const pick = e.target.closest('[data-pick]');
  if (pick) { setPane('group', +pick.dataset.pick); return; }
  const mq = e.target.closest('[data-mq]'); if (mq) { mergeAct(mq.dataset.mq); return; }
  const fc = e.target.closest('[data-fc]'); if (fc) { fragAct('assign', +fc.dataset.fc); return; }
  const fa = e.target.closest('[data-fa]'); if (fa) { fragAct(fa.dataset.fa); return; }
  const gm = e.target.closest('[data-gm-merge]');
  if (gm) { const src = ch.sel, t = +gm.dataset.gmMerge; const keepThis = (S.clusterById.get(src)?.count || 0) >= (S.clusterById.get(t)?.count || 0);
    charsDo(() => api('POST', '/api/groups/merge', keepThis ? { target: src, sources: [t] } : { target: t, sources: [src] })).then(() => { if (!keepThis) setPane('group', t); }); return; }
  const gno = e.target.closest('[data-gm-no]'); if (gno) { charsDo(() => api('POST', '/api/group-suggestions/dismiss', { key: gno.dataset.gmNo })); return; }
  const bd = e.target.closest('[data-bundle]');
  if (bd) {
    const b = (await api('GET', '/api/merge-suggestions?limit=1')).bundles[+bd.dataset.bundle];
    if (!b) { renderChars().catch(fail); return; }
    if (bd.dataset.bact === 'toggle') { ch.bopen = ch.bopen === +bd.dataset.bundle ? null : +bd.dataset.bundle; renderChars().catch(fail); return; }
    const ids = b.drops.map(x => x.id).filter(id => !ch.bexcl.has(id));
    if (!ids.length) { toast('Nothing ticked'); return; }
    if (ids.length > 25 && !confirm(`Merge ${ids.length.toLocaleString()} groups into “${b.name}” (#${b.keep})? You can undo this in one step.`)) return;
    ch.bopen = null; ch.bexcl.clear();
    charsDo(() => api('POST', '/api/groups/merge', { target: b.keep, sources: ids }));
    return;
  }
  const qr = e.target.closest('[data-qr]');
  if (qr) {
    const r = ch.qr; if (!r) return;
    if (qr.dataset.qr === 'all') { ch.qrExcl.clear(); renderChars().catch(fail); return; }
    if (qr.dataset.qr === 'none') { r.items.forEach(i => ch.qrExcl.add(i.uid)); renderChars().catch(fail); return; }
    const body = { scope: 'book', apply: true, exclude: [...ch.qrExcl] };
    ch.qrExcl.clear();
    charsDo(() => api('POST', '/api/quote-rule', body));
    return;
  }
  const a = e.target.closest('[data-gact]');
  const rect = a ? a.getBoundingClientRect() : null;
  if (a) {
    const act = a.dataset.gact;
    if (act === 'clearchk') { ch.checked.clear(); renderChars().catch(fail); }
    else if (act === 'clearm') { ch.mchecked.clear(); renderChars().catch(fail); }
    else if (act === 'mpage') { ch.offset = Math.max(0, +a.dataset.off); ch.mchecked.clear(); await renderChars(); $('#cd').scrollTop = 0; }
    else if (act === 'goto') goTo(+a.dataset.ord).catch(fail);
    else if (act === 'findgroup') openFind({ layer: 'entities', field: 'coref', op: 'eq', value: String(ch.sel) });
    else if (act === 'quotesof') { S.qv.speaker = String(ch.sel); S.qv.offset = 0; setView('quotes'); }
    else if (act === 'check') charsDo(() => api('PATCH', `/api/group/${ch.sel}`, { checked: !ch.detail.checked }));
    else if (act === 'paint') startPaint(ch.sel);
    else if (act === 'first') stepGroup(ch.sel, -1, 'next');
    else if (act === 'mergechk') {
      const ids = [...ch.checked];
      pop.innerHTML = `<div class="gp"><div class="gpt">Keep which group? The others merge into it.</div>${ids.map(id => { const c = S.clusterById.get(id); return `<button class="cchip" data-keep="${id}"><span class="sw" style="--h:${hue(id)}"></span>${esc(c ? c.name : '#' + id)}<span class="num">${c ? c.count : ''}</span></button>`; }).join('')}</div>`;
      pop.hidden = false; pop.style.left = rect.left + 'px'; pop.style.top = rect.bottom + 6 + 'px';
      pop.onclick = ev => { const k = ev.target.closest('[data-keep]'); if (!k) return; closePop(); const t = +k.dataset.keep; ch.checked.clear(); charsDo(() => api('POST', '/api/groups/merge', { target: t, sources: ids.filter(i => i !== t) })).then(() => setPane('group', t)); };
      pop.onkeydown = ev => { if (ev.key === 'Escape') closePop(); };
    } else if (act === 'mergeinto') {
      const m0 = ch.detail.mentions[0];
      groupPicker(rect, { title: `Merge “${ch.detail.name}” into`, allowNew: false, exclude: [ch.sel], near: m0 ? { a: m0.s, b: m0.e } : null,
        onPick: t => { const src = ch.sel; charsDo(() => api('POST', '/api/groups/merge', { target: t, sources: [src] })).then(() => setPane('group', t)); } });
    } else if (act === 'movevar') {
      groupPicker(rect, { title: `Move every “${a.dataset.text}” (${a.dataset.prop}) to`, exclude: [ch.sel], near: { a: +a.closest('tr').querySelector('[data-ord]').dataset.ord, b: +a.closest('tr').querySelector('[data-ord]').dataset.ord },
        onPick: (t, name) => charsDo(() => api('POST', `/api/group/${ch.sel}/variant`, { text: a.dataset.text, prop: a.dataset.prop, target: t, name })) });
    } else if (act === 'movechk') {
      const uids = [...ch.mchecked];
      const first = ch.detail.mentions.find(m => ch.mchecked.has(m.uid));
      groupPicker(rect, { title: `Move ${uids.length} mention${uids.length > 1 ? 's' : ''} to`, exclude: [ch.sel], near: first ? { a: first.s, b: first.e } : null,
        onPick: (t, name) => { ch.mchecked.clear(); charsDo(() => api('POST', '/api/mentions/regroup', { uids, target: t, name })); } });
    } else if (act === 'cmpPick') { ch.cmpPick = a.dataset.slot === 'A' ? 'a' : 'b'; toast(`Click a group on the left for ${a.dataset.slot}`);
    } else if (act === 'cmpSwap') { [ch.cmpA, ch.cmpB] = [ch.cmpB, ch.cmpA]; renderChars().catch(fail);
    } else if (act === 'cmpClear') { ch.cmpA = ch.cmpB = null; ch.cmpPick = null; renderChars().catch(fail);
    } else if (act === 'cmpMerge') {
      const A = ch.cmpA, B = ch.cmpB, into = a.dataset.into;
      if (into === 'a' || into === 'b') {
        const target = into === 'a' ? A : B, src = into === 'a' ? B : A;
        charsDo(async () => { const r = await api('POST', '/api/groups/merge', { target, sources: [src] }); ch.cmpA = target; ch.cmpB = null; return r; });
      } else {
        groupPicker(rect, { title: 'Merge A and B into', allowNew: true, exclude: [A, B],
          onPick: (target, name) => charsDo(async () => {
            if (target != null) { const r = await api('POST', '/api/groups/merge', { target, sources: [A, B] }); ch.cmpA = target; ch.cmpB = null; return r; }
            const sel = await api('POST', '/api/mentions/scope', { anchors: [], scope: 'book', groups: [A, B] });
            const r = await api('POST', '/api/mentions/regroup', { uids: sel.uids, target: '', name });
            ch.cmpA = r.target; ch.cmpB = null; return r;
          }) });
      }
    }
    return;
  }
  const sc = e.target.closest('[data-gscope]');
  if (sc) {
    try {
      const r = await api('POST', '/api/mentions/scope', { anchors: [...ch.mchecked], scope: sc.dataset.gscope, groups: [ch.sel] });
      const before = ch.mchecked.size;
      r.uids.forEach(u => ch.mchecked.add(u));
      toast(`${(ch.mchecked.size - before).toLocaleString()} more ticked (${ch.mchecked.size.toLocaleString()} in all)`);
      renderChars().catch(fail);
    } catch (err) { fail(err); }
    return;
  }
  const gs = e.target.closest('[data-gsel]');
  const row = e.target.closest('.crow');
  if (gs || row) {
    const gid = gs ? +gs.dataset.gsel : +row.dataset.gid;
    if (ch.pane === 'compare' && row && !gs) {
      if (ch.cmpPick === 'a') { ch.cmpA = gid; ch.cmpPick = null; }
      else if (ch.cmpPick === 'b') { if (gid !== ch.cmpA) ch.cmpB = gid; ch.cmpPick = null; }
      else if (ch.cmpA == null) ch.cmpA = gid;
      else if (gid !== ch.cmpA) ch.cmpB = gid;  // B empty, or both filled: a plain click sets/replaces B
      renderChars().catch(fail);
      return;
    }
    setPane('group', gid); return;
  }
  const m = e.target.closest('.mrow');
  if (m) {
    try { const d = await selectSpan('entities', +m.dataset.muid); S.prevSpan = { layer: 'entities', uid: +m.dataset.muid, text: d.text }; renderPanel(); document.querySelectorAll('.mrow.on').forEach(x => x.classList.remove('on')); m.classList.add('on'); }
    catch (err) { fail(err); }
  }
}
/* Delay for the group search box, so the list isn't redrawn on every key. */
let charsInputTimer = null;
$('#main').addEventListener('input', e => {
  const t = e.target;
  if (t.id === 'gq') { S.ch.q = t.value; clearTimeout(charsInputTimer); charsInputTimer = setTimeout(() => renderChars().then(() => { const i = $('#gq'); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }).catch(fail), 250); }
});
$('#main').addEventListener('change', e => {
  if (S.view !== 'chars') return;
  const t = e.target, ch = S.ch;
  if (t.id === 'gcat') { ch.cat = t.value; renderChars().catch(fail); }
  else if (t.id === 'gsort') { ch.sort = t.value; renderChars().catch(fail); }
  else if (t.id === 'ghid') { ch.hidden = t.checked; renderChars().catch(fail); }
  else if (t.id === 'gunchk') { ch.unchecked = t.checked; renderChars().catch(fail); }
  else if (t.id === 'fsize') { ch.fsize = +t.value; ch.fskip = 0; renderChars().catch(fail); }
  else if (t.id === 'fcat') { ch.fcat = t.value; ch.fskip = 0; renderChars().catch(fail); }
  else if (t.dataset.gchk != null) { const id = +t.dataset.gchk; t.checked ? ch.checked.add(id) : ch.checked.delete(id); renderChars().catch(fail); }
  else if (t.dataset.mchk != null) { const id = +t.dataset.mchk; t.checked ? ch.mchecked.add(id) : ch.mchecked.delete(id); renderChars().catch(fail); }
  else if (t.dataset.bx != null) { const id = +t.dataset.bx; t.checked ? ch.bexcl.delete(id) : ch.bexcl.add(id); renderChars().catch(fail); }
  else if (t.dataset.qrx != null) { const id = +t.dataset.qrx; t.checked ? ch.qrExcl.delete(id) : ch.qrExcl.add(id); renderChars().catch(fail); }
  else if (t.dataset.booktype != null) {
    const types = [...document.querySelectorAll('[data-booktype]:checked')].map(x => x.dataset.booktype);
    api('PUT', '/api/book-settings', { types }).then(r => toast(`.book will include ${r.types.join(', ')}`)).catch(err => { fail(err); renderChars().catch(fail); });
  }
  else if (t.id === 'gname') charsDo(() => api('PATCH', `/api/group/${t.dataset.gid}`, { name: t.value }));
  else if (t.id === 'gpron') charsDo(() => api('PATCH', `/api/group/${t.dataset.gid}`, { pronoun: t.value }));
  else if (t.id === 'ghidden') charsDo(() => api('PATCH', `/api/group/${t.dataset.gid}`, { hidden: t.checked }));
});

/* keys in the Entities tab: queues and list navigation (run before the text-view keys) */
window.addEventListener('keydown', e => {
  if (S.view !== 'chars' || S.mode !== 'table' || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.target.matches && e.target.matches('input, select, textarea')) return;
  if (!pop.hidden || !$('#drawer').hidden) return;
  const k = e.key.toLowerCase(), ch = S.ch;
  const stop = () => { e.preventDefault(); e.stopImmediatePropagation(); };
  if (EXTRA_PANES[ch.pane] && EXTRA_PANES[ch.pane].key) { if (EXTRA_PANES[ch.pane].key(k, e)) stop(); return; }
  if (ch.pane === 'merge' && ch.mqItems && ch.mqItems.length) {
    const map = { m: 'merge', b: 'swap', n: 'no', s: 'skip', p: 'prev' };
    if (map[k]) { stop(); mergeAct(map[k]); }
    return;
  }
  if (ch.pane === 'fragments' && ch.frag) {
    if (/^[1-9]$/.test(k)) { const c = ch.frag.candidates[+k - 1]; if (c) { stop(); fragAct('assign', c.id); } return; }
    const map = { g: 'other', k: 'keep', h: 'hide', d: 'delete', s: 'skip', p: 'prev' };
    if (map[k]) { stop(); fragAct(map[k]); }
    return;
  }
  if (ch.pane === 'group' && ch.detail) {
    if (k === 'c') { stop(); charsDo(() => api('PATCH', `/api/group/${ch.sel}`, { checked: !ch.detail.checked })); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      const items = (ch.list && ch.list.items) || [];
      const i = items.findIndex(c => c.id === ch.sel) + (e.key === 'ArrowDown' ? 1 : -1);
      if (items[i]) { stop(); setPane('group', items[i].id); }
    }
  }
}, true);
