'use strict';
/* Coreference tools in the text views: selecting mentions by scope, the quote rule, number keys,
   paint mode, the groups sidebar and stepping through a group's mentions. */

S.paint = null;
S.hl = null;
S.gside = localStorage.getItem('bne.gside') === '1';
S.gsq = '';
/* Scopes for selecting mentions around a mention, with their button labels. */
const SCOPE_LABELS = [['sentence', 'Sentence'], ['paragraph', 'Paragraph'], ['quote', 'Quote'], ['conversation', 'Conversation'], ['book', 'Whole book']];

/* A group's name for messages ('#id' if unknown). */
function groupName(gid) { const c = S.clusterById.get(gid); return c ? c.name : `#${gid}`; }
/* The mention on the page with exactly these bounds, if any. */
function mentionAt(a, b) { return S.win && S.win.entities.find(x => x.s === a && x.e === b); }

/* ------------------------------------------------------------ side panel additions (called from app.js) */
/* Side panel additions for a selected entity (group tools, scope selection) or quote (quote rule). */
function spanExtras(layer, d) {
  if (layer === 'entities') {
    const g = d.coref;
    return `<h3>Group</h3>
      <div class="row cxg"><span class="sw" style="--h:${hue(g)}"></span><b>${esc(trim(d.name || groupName(g), 30))}</b><span class="num">#${g}</span>
        <span class="grow"></span><button class="btn sm" data-cx="step" data-dir="prev" data-g="${g}" data-s="${d.s}" title="Previous mention of this group">‹</button>
        <span class="num" id="cxIdx"></span><button class="btn sm" data-cx="step" data-dir="next" data-g="${g}" data-s="${d.s}" title="Next mention of this group">›</button></div>
      <div class="row" style="margin-top:6px"><button class="btn sm" data-cx="paint" data-g="${g}" title="Click mentions in the text to move them into this group">Paint with this group</button>
        ${keySelect(g, 'pkey')}<button class="link" data-cx="open" data-g="${g}">Open in Entities</button></div>
      <h3>Select mentions in the same</h3>
      <div class="row scopebtns">${SCOPE_LABELS.map(([k, l]) => `<button class="btn sm" data-cx="scope" data-scope="${k}">${l}</button>`).join('')}</div>
      <label class="chk" style="margin-top:6px"><input type="checkbox" id="cxAll"> Every group, not only “${esc(trim(d.name || groupName(g), 20))}”</label>
      <p class="note">Then move, retype or delete them together, or narrow them down to certain words such as <i>he</i> or <i>him</i>.</p>`;
  }
  if (layer === 'quotes') {
    return `<h3>Quote rule</h3><div id="qrule" data-quote="${d.uid}" class="note">Checking first-person pronouns…</div>`;
  }
  return '';
}
/* Side panel block for a selected passage: select its mentions (all or one group's), narrator for the passage, keys. */
function rangeExtras(a, b) {
  const counts = new Map();
  for (const x of (S.win ? S.win.entities : [])) if (x.s >= a && x.e <= b) counts.set(x.coref, (counts.get(x.coref) || 0) + 1);
  const groups = [...counts.entries()].sort((x, y) => y[1] - x[1]);
  const keys = Object.entries(S.hotkeys);
  return `<h3>Mentions in this passage</h3>
    ${groups.length ? `<div class="row"><button class="btn sm" data-cx="rsel" data-g="all">Select all ${[...counts.values()].reduce((n, v) => n + v, 0)}</button><span class="note" style="margin:0">or only</span></div>
      <div class="row" style="margin-top:6px">${groups.slice(0, 12).map(([g, n]) => `<button class="cchip" data-cx="rsel" data-g="${g}"><span class="sw" style="--h:${hue(g)}"></span>${esc(trim(groupName(g), 20))}<span class="num">${n}</span></button>`).join('')}</div>`
      : '<p class="note" style="margin-top:0">No entity mentions in this passage.</p>'}
    <div id="rqr" data-a="${a}" data-b="${b}" class="note"></div>
    <h3>Narrator</h3>
    <div class="row"><button class="btn sm" data-cx="narr" data-a="${a}" data-b="${b}" title="For a letter, a diary or a story told by someone else">Narrator for this passage…</button></div>
    ${keys.length ? `<p class="note">Press a number key to add these words as a mention: ${keys.map(([k, v]) => `<kbd>${k}</kbd> ${esc(trim(v.name, 16))}`).join(', ')}.</p>` : ''}`;
}
/* After the side panel is drawn: fill in the step counter, and the quote-rule lines for a quote or passage. */
async function afterPanel() {
  const idx = $('#cxIdx');
  if (idx) {
    const btn = $('[data-cx="step"]');
    try { const r = await api('GET', `/api/group/${btn.dataset.g}/step?ord=${+btn.dataset.s - 1}&dir=next`); if ($('#cxIdx') === idx) idx.textContent = `${r.index} of ${r.total}`; } catch { }
  }
  const qr = $('#qrule');
  if (qr) {
    const quote = +qr.dataset.quote;
    try {
      const [one, conv] = await Promise.all([api('POST', '/api/quote-rule', { scope: 'quote', quote }),
        api('POST', '/api/quote-rule', { scope: 'conversation', quote }).catch(() => null)]);
      if ($('#qrule') !== qr) return;
      const line = (r, scope, what) => r && r.n
        ? `<div class="row"><span>${r.n} first-person pronoun${r.n === 1 ? '' : 's'} in ${what} ${r.n === 1 ? 'isn’t' : 'aren’t'} in the speaker’s group.</span><button class="btn sm" data-cx="qr" data-scope="${scope}" data-quote="${quote}">Move to speaker</button></div>` : '';
      const html = line(one, 'quote', 'this quote') + (conv && conv.n > (one ? one.n : 0) ? line(conv, 'conversation', 'this conversation') : '');
      qr.classList.toggle('note', !html);
      qr.innerHTML = html || (one && one.no_speaker ? 'Set a speaker first; then <i>I, me, my</i> in the quote can follow it.' : 'Every <i>I, me, my</i> in this quote is in the speaker’s group.');
    } catch (e) { qr.textContent = ''; }
  }
  const rq = $('#rqr');
  if (rq) {
    try {
      const r = await api('POST', '/api/quote-rule', { scope: 'passage', a: +rq.dataset.a, b: +rq.dataset.b });
      if ($('#rqr') !== rq || !r.n) return;
      rq.classList.remove('note');
      rq.innerHTML = `<div class="row" style="margin-top:8px"><span>${r.n} first-person pronoun${r.n === 1 ? '' : 's'} in quotes here ${r.n === 1 ? 'isn’t' : 'aren’t'} in the speaker’s group.</span><button class="btn sm" data-cx="qr" data-scope="passage" data-a="${rq.dataset.a}" data-b="${rq.dataset.b}">Move to speakers</button></div>`;
    } catch { }
  }
}

/* ------------------------------------------------------------ several mentions selected (⌘-click or by scope) */
/* ⌘-click: add or remove an entity label from the multi-selection. */
function toggleMention(layer, uid) {
  if (layer !== 'entities') { toast('Only entity labels can be selected together'); return; }
  if (!S.msel.size && S.sel && S.sel.kind === 'span' && S.sel.layer === 'entities' && S.sel.uid !== uid) S.msel.add(S.sel.uid);
  const keep = new Set(S.msel);
  if (keep.has(uid)) keep.delete(uid); else keep.add(uid);
  S.sel = keep.size ? { kind: 'msel' } : null;
  S.prevSpan = null;
  S.msel = keep;
  paintSelection(); renderPanel();
}
/* Select these mentions together (shows the multi-selection panel). */
function setMsel(uids, msg) {
  if (!uids.length) { toast('No mentions found there'); return; }
  S.msel = new Set(uids);
  S.sel = { kind: 'msel' };
  S.prevSpan = null;
  paintSelection(); renderPanel();
  if (msg) toast(msg);
}
/* Side panel for several selected mentions: move, words selected, extend by scope, retype, delete. */
async function mselPanel() {
  const uids = [...S.msel];
  const d = await api('POST', '/api/mentions/detail', { uids });
  if (d.missing) S.msel = new Set(d.items.map(x => x.uid));  // some were deleted meanwhile
  S.mselItems = d.items;
  const groups = new Map(), forms = new Map();
  for (const m of d.items) { groups.set(m.coref, (groups.get(m.coref) || 0) + 1); const f = m.text.toLowerCase(); forms.set(f, (forms.get(f) || 0) + 1); }
  const onPage = d.items.filter(m => S.win && m.s >= S.win.start && m.e <= S.win.end).length;
  const shown = d.items.slice(0, 200);
  return `<h2>${d.items.length.toLocaleString()} mention${d.items.length === 1 ? '' : 's'} selected</h2>
    <div class="sub">From ${groups.size} group${groups.size === 1 ? '' : 's'}${onPage < d.items.length ? ` · ${(d.items.length - onPage).toLocaleString()} on other pages` : ''}</div>
    <p class="note" style="margin-top:0">⌘-click labels to add or remove them. The selection stays when you change page.</p>
    <h3>Move to a group</h3>
    <div class="row"><button class="btn primary" data-ms="move">Move to…</button>${Object.keys(S.hotkeys).length ? '<span class="note" style="margin:0">or press a number key</span>' : ''}</div>
    <h3>Words selected</h3>
    <div class="row msforms">${[...forms.entries()].sort((a, b) => b[1] - a[1]).map(([f, n]) => `<span class="fchip"><button class="link" data-msonly="${esc(f)}" title="Keep only “${esc(f)}”">${esc(f)}</button><span class="num">${n}</span><button class="link x" data-msform="${esc(f)}" aria-label="Remove “${esc(f)}” from the selection" title="Remove from the selection">✕</button></span>`).join('')}</div>
    <p class="note">Click a word to keep only it, or ✕ to drop it.</p>
    <h3>Add this group’s mentions in the same</h3>
    <div class="row scopebtns">${SCOPE_LABELS.slice(0, 4).map(([k, l]) => `<button class="btn sm" data-msext="${k}">${l}</button>`).join('')}</div>
    <h3>Change type</h3>
    <div class="row"><input id="msCat" list="dl-entities-cat" placeholder="e.g. VAR" style="width:90px" aria-label="Entity type">
      <button class="btn sm" data-ms="cat">Set type</button>
      <select id="msProp" aria-label="Mention type"><option value="">Mention type…</option><option>PROP</option><option>NOM</option><option>PRON</option></select>
      <button class="btn sm" data-ms="prop">Set</button></div>
    <h3>Selected</h3>
    <ul class="mslist">${shown.map(m => `<li data-ms-go="${m.s}"><span class="sw" style="--h:${hue(m.coref)}"></span>
      <span class="mt">${esc(trim(m.text, 40))}</span><span class="num">${esc(m.cat)} ${esc(m.prop)} · ${esc(trim(m.name || '#' + m.coref, 22))} · t${m.s}</span>
      <button class="link" data-ms-drop="${m.uid}" aria-label="Remove from selection">✕</button></li>`).join('')}
      ${d.items.length > shown.length ? `<li class="muted">and ${(d.items.length - shown.length).toLocaleString()} more</li>` : ''}</ul>
    <div class="row" style="margin-top:14px"><button class="btn" data-ms="clear">Clear selection</button><button class="btn danger" data-ms="delete">Delete these mentions</button></div>`;
}
/* A position near the selection, for the picker's 'nearby groups'. */
function mselNear() {
  const items = (S.mselItems || []).filter(m => S.win && m.s >= S.win.start && m.e <= S.win.end);
  const m = items[0] || (S.mselItems || [])[0];
  return m ? { a: m.s, b: m.e } : null;
}
/* Move mentions to another group (or a new one) in one step and redraw. */
async function moveMentions(uids, target, name) {
  const r = await api('POST', '/api/mentions/regroup', { uids, target, name });
  if (!r.moved) { toast(`Already in ${groupName(target)}`); return; }
  S.msel.clear(); S.sel = null;
  updateHistoryButtons(r.history); toast(r.history.undo);
  await refresh(true);
}

$('#panel').addEventListener('click', async e => {
  const t = e.target;
  // selected mentions
  const drop = t.closest('[data-ms-drop]');
  if (drop) { e.stopPropagation(); toggleMention('entities', +drop.dataset.msDrop); return; }
  const fr = t.closest('[data-msform]'), fo = t.closest('[data-msonly]');
  if (fr || fo) {
    const f = (fr || fo).dataset[fr ? 'msform' : 'msonly'];
    const keep = (S.mselItems || []).filter(m => fr ? m.text.toLowerCase() !== f : m.text.toLowerCase() === f).map(m => m.uid);
    if (!keep.length) { S.sel = null; S.msel.clear(); paintSelection(); renderPanel(); return; }
    setMsel(keep); return;
  }
  const ext = t.closest('[data-msext]');
  if (ext) {
    try {
      const groups = [...new Set((S.mselItems || []).map(m => m.coref))];
      const r = await api('POST', '/api/mentions/scope', { anchors: [...S.msel], scope: ext.dataset.msext, groups });
      const before = S.msel.size;
      setMsel([...new Set([...S.msel, ...r.uids])], `${(new Set([...S.msel, ...r.uids]).size - before).toLocaleString()} more selected`);
    } catch (err) { fail(err); }
    return;
  }
  const go = t.closest('[data-ms-go]');
  const a = t.closest('[data-ms]');
  if (!a && go) { const ord = +go.dataset.msGo; if (!S.win || ord < S.win.start || ord > S.win.end) { await loadWindow({ tok: ord }); } const el = document.querySelector(`#main [data-ord="${ord}"]`); if (el) el.scrollIntoView({ block: 'center' }); return; }
  if (a) {
    const uids = [...S.msel];
    const done = async r => { S.msel.clear(); S.sel = null; if (r) updateHistoryButtons(r.history || r); await refresh(true); if (r) toast((r.history || r).undo); };
    try {
      if (a.dataset.ms === 'clear') { S.sel = null; paintSelection(); renderPanel(); }
      else if (a.dataset.ms === 'move') {
        groupPicker(a.getBoundingClientRect(), { title: `Move ${uids.length} mention${uids.length === 1 ? '' : 's'} to`, near: mselNear(),
          onPick: async (tg, name) => { try { await moveMentions(uids, tg, name); } catch (err) { fail(err); } } });
      } else if (a.dataset.ms === 'cat') {
        const v = $('#msCat').value.trim(); if (!v) { toast('Type the new type first, e.g. VAR'); return; }
        if (!inTagset('entities.cat', v)) toast(`“${v}” isn’t one of the entity types. Saved and flagged.`);
        await done(await api('POST', '/api/mentions/edit', { uids, cat: v }));
      } else if (a.dataset.ms === 'prop') {
        const v = $('#msProp').value; if (!v) { toast('Choose PROP, NOM or PRON first'); return; }
        await done(await api('POST', '/api/mentions/edit', { uids, prop: v }));
      } else if (a.dataset.ms === 'delete') {
        if (uids.length > 3 && !confirm(`Delete ${uids.length} mentions? You can undo this in one step.`)) return;
        await done(await api('POST', '/api/mentions/delete', { uids }));
      }
    } catch (err) { fail(err); }
    return;
  }
  // entity, quote and passage tools
  const cx = t.closest('[data-cx]'); if (!cx) return;
  try {
    const kind = cx.dataset.cx;
    if (kind === 'step') stepGroup(+cx.dataset.g, +cx.dataset.s, cx.dataset.dir);
    else if (kind === 'paint') startPaint(+cx.dataset.g);
    else if (kind === 'open') openGroup(+cx.dataset.g);
    else if (kind === 'scope') {
      if (!S.sel || S.sel.kind !== 'span' || S.sel.layer !== 'entities') return;
      const all = $('#cxAll') && $('#cxAll').checked;
      const r = await api('POST', '/api/mentions/scope', { anchors: [S.sel.uid], scope: cx.dataset.scope, groups: all ? 'all' : 'same' });
      setMsel(r.uids, `${r.n.toLocaleString()} mention${r.n === 1 ? '' : 's'} selected in the ${cx.dataset.scope === 'book' ? 'whole book' : cx.dataset.scope}`);
    } else if (kind === 'rsel') {
      const r = S.sel && S.sel.kind === 'range' ? S.sel : null; if (!r) return;
      const res = await api('POST', '/api/mentions/scope', { anchors: [], scope: 'passage', a: r.a, b: r.b, groups: cx.dataset.g === 'all' ? 'all' : [+cx.dataset.g] });
      setMsel(res.uids, `${res.n} mention${res.n === 1 ? '' : 's'} selected`);
    } else if (kind === 'narr') {
      const a = +cx.dataset.a, b = +cx.dataset.b;
      groupPicker(cx.getBoundingClientRect(), { title: 'In this passage, I, me and my refer to', allowNew: false, allowNone: true, near: { a, b }, okLabel: 'Set',
        placeholder: 'Group name or #number; empty = no narrator', onPick: async t => {
          try { await api('POST', '/api/narrator/passage', { a, b, group: t }); toast(t == null ? 'Passage marked as having no narrator' : `Narrator of this passage: ${groupName(t)}`); } catch (err) { fail(err); }
        } });
    } else if (kind === 'qr') {
      const body = { scope: cx.dataset.scope, apply: true };
      if (cx.dataset.quote) body.quote = +cx.dataset.quote;
      if (cx.dataset.a) { body.a = +cx.dataset.a; body.b = +cx.dataset.b; }
      const r = await api('POST', '/api/quote-rule', body);
      updateHistoryButtons(r.history); toast(r.history.undo);
      await refresh(true);
    }
  } catch (err) { fail(err); }
});

/* ------------------------------------------------------------ stepping through a group's mentions */
/* Jump to the next or previous mention of a group anywhere in the book and highlight the group. */
async function stepGroup(gid, ref, dir) {
  try {
    const r = await api('GET', `/api/group/${gid}/step?ord=${ref}&dir=${dir}`);
    S.hl = gid;
    await goTo(r.s, async () => { await selectSpan('entities', r.uid); });
    toast(`${groupName(gid)}: mention ${r.index} of ${r.total}`);
  } catch (e) { fail(e); }
}
/* The position to step from: the selected span, else the edge of the page. */
function stepRef(gid, dir) {
  if (S.sel && S.sel.kind === 'span' && S.sel.s != null) return S.sel.s;
  if (!S.win) return dir === 'next' ? -1 : 1e9;
  return dir === 'next' ? S.win.start - 1 : S.win.start;
}

/* ------------------------------------------------------------ paint mode */
/* Enter paint mode: clicked labels move into the group, dragged words become new mentions. */
function startPaint(gid) {
  stopPasses();
  const c = S.clusterById.get(gid);
  S.paint = { id: gid, name: c ? c.name : `#${gid}`, cat: c ? c.cat : 'PER' };
  S.hl = gid;
  if (S.view !== 'table' && S.view !== 'annot') setView(S.textView || 'annot');
  else renderMain();
  showPaintBanner();
}
/* Show or hide the 'Painting with <group>' banner. */
function showPaintBanner() {
  const b = $('#banner');
  if (!S.paint) { if (b.dataset.kind === 'paint') { b.hidden = true; b.dataset.kind = ''; } return; }
  b.dataset.kind = 'paint';
  b.innerHTML = `<span><span class="sw" style="--h:${hue(S.paint.id)}"></span> Painting with <b>${esc(S.paint.name)}</b>: click entity labels to move them into it, or ${S.view === 'annot' ? 'drag across words' : 'select words'} to add a new mention. Esc to stop.</span><button class="btn sm" id="paintStop">Stop painting</button>`;
  b.hidden = false;
  $('#paintStop').onclick = stopPaint;
}
/* Leave paint mode. */
function stopPaint() {
  if (!S.paint) return;
  S.paint = null;
  showPaintBanner();
  if (S.view === 'annot' || S.view === 'table') paintSelection();
}
/* In paint mode: move the clicked mention into the painting group. */
async function paintMention(uid) {
  const m = S.win.entities.find(x => x.uid === uid);
  if (m && m.coref === S.paint.id) { toast(`Already in ${S.paint.name}`); return; }
  try {
    const r = await api('POST', '/api/mentions/regroup', { uids: [uid], target: S.paint.id });
    updateHistoryButtons(r.history);
    toast(`“${m ? m.text : 'Mention'}” → ${S.paint.name}`);
    await refresh(true);
  } catch (e) { fail(e); }
}
/* Add a mention (or move the one already there) for these words in a group. */
async function createMentionIn(gid, a, b) {
  const existing = mentionAt(a, b);
  if (existing) {
    if (existing.coref === gid) { toast(`Already in ${groupName(gid)}`); return; }
    const r = await api('POST', '/api/mentions/regroup', { uids: [existing.uid], target: gid });
    updateHistoryButtons(r.history); toast(`“${existing.text}” → ${groupName(gid)}`);
  } else {
    const c = S.clusterById.get(gid);
    const r = await api('POST', '/api/span/entities', { s: a, e: b, cat: (c && c.cat) || 'PER', prop: guessProp(a, b), coref: gid });
    updateHistoryButtons(r.history); toast(r.history.undo);
  }
  S.sel = null;
  await refresh(true);
}
// called from app.js when words are dragged over in the annotation view
function onRangeSelected(a, b) {
  if (!S.paint) return false;
  createMentionIn(S.paint.id, a, b).catch(fail);
  return true;
}
// clicks in paint mode go to the painter first
$('#main').addEventListener('click', e => {
  if (!S.paint || S.mode !== 'table' || (S.view !== 'annot' && S.view !== 'table')) return;
  const bar = e.target.closest('.sbar[data-layer="entities"], .chip[data-layer="entities"], .cont[data-layer="entities"]');
  if (!bar || e.metaKey || e.ctrlKey) return;
  e.preventDefault(); e.stopImmediatePropagation();
  paintMention(+bar.dataset.uid);
}, true);

/* ------------------------------------------------------------ number keys, Esc */
window.addEventListener('keydown', e => {
  if (e.target.matches && e.target.matches('input, select, textarea')) return;
  if (typeof editing !== 'undefined' && editing) return;
  if (e.key === 'Escape') {
    if (S.paint) { e.preventDefault(); e.stopImmediatePropagation(); stopPaint(); return; }
    if (S.hl != null && (!S.sel || S.sel.kind !== 'span')) { S.hl = null; if (S.view === 'annot') { drawOverlay(); if (S.gside) renderGside(); } }
    return;
  }
  if (e.metaKey || e.ctrlKey || e.altKey || !/^[1-9]$/.test(e.key)) return;
  if (PASSES.some(p => S[p.key])) return;  // a pass uses the number keys for its own candidates
  if (S.mode !== 'table' || (S.view !== 'annot' && S.view !== 'table') || !pop.hidden) return;
  const pin = S.hotkeys[e.key]; if (!pin) return;
  const sel = S.sel; if (!sel) return;
  let act = null;
  if (sel.kind === 'msel') act = () => moveMentions([...S.msel], pin.id, null);
  else if (sel.kind === 'span' && sel.layer === 'entities') act = () => moveMentions([sel.uid], pin.id, null);
  else if (sel.kind === 'range') act = () => createMentionIn(pin.id, sel.a, sel.b);
  else if (sel.kind === 'token' && S.view === 'annot') act = () => createMentionIn(pin.id, sel.ord, sel.ord);
  if (!act) return;
  e.preventDefault(); e.stopImmediatePropagation();
  noteRecent(pin.id);
  act().catch(fail);
}, true);

/* ------------------------------------------------------------ groups sidebar (annotation view) */
/* One row of the groups sidebar. */
function gsRow(gid, onPage) {
  const c = S.clusterById.get(gid); if (!c) return '';
  const k = keyOf(gid);
  return `<div class="gr${S.hl === gid ? ' hl' : ''}${S.paint && S.paint.id === gid ? ' painting' : ''}" data-gs="${gid}" title="${esc(c.name)} · group ${gid} · ${c.count} mentions. Click to highlight, double-click to open.">
    <span class="sw" style="--h:${hue(gid)}"></span><span class="gn">${k ? `<kbd>${k}</kbd>` : ''}${esc(c.name)}</span>
    <span class="num">${onPage != null ? `${onPage}/` : ''}${c.count}</span>
    <span class="gra"><button class="gb" data-gs-step="prev" aria-label="Previous mention">‹</button><button class="gb" data-gs-step="next" aria-label="Next mention">›</button><button class="gb" data-gs-paint aria-label="Paint with this group" title="Paint with this group">✎</button></span></div>`;
}
/* The groups sidebar of the annotation view: pinned, on this page, matching or biggest groups. */
function renderGside() {
  const el = $('#gside'); if (!el || !S.win) return;
  const onPage = new Map();
  for (const x of S.win.entities) onPage.set(x.coref, (onPage.get(x.coref) || 0) + 1);
  const pageRows = [...onPage.entries()].filter(([g]) => S.clusterById.has(g)).sort((a, b) => b[1] - a[1]);
  const q = S.gsq.trim().toLowerCase();
  const all = q ? S.clusters.filter(c => c.name.toLowerCase().includes(q) || String(c.id) === q).slice(0, 60)
    : S.clusters.filter(c => !c.hidden).slice(0, 30);
  const keys = Object.entries(S.hotkeys);
  const focused = document.activeElement && document.activeElement.id === 'gsq';
  el.innerHTML = `<div class="gsh"><input type="search" id="gsq" placeholder="Find a group" value="${esc(S.gsq)}" aria-label="Find a group"></div>
    ${keys.length ? `<div class="gsl">Number keys</div>${keys.map(([, v]) => gsRow(v.id, onPage.get(v.id) || 0)).join('')}` : ''}
    <div class="gsl">On this page</div>${pageRows.map(([g, n]) => gsRow(g, n)).join('') || '<p class="note">No entity mentions on this page.</p>'}
    <div class="gsl">${q ? 'Matching groups' : 'Biggest groups'}</div>${all.map(c => gsRow(c.id, null)).join('') || '<p class="note">No groups match.</p>'}
    <p class="note gshint">Click a group to highlight its mentions; ‹ › step through them in the book; ✎ paints with it. Pin number keys on a group’s page or in the side panel.</p>`;
  if (focused) { const i = $('#gsq'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }
}
$('#main').addEventListener('click', e => {
  if (e.target.id === 'gsideBtn') {
    e.stopImmediatePropagation();
    S.gside = !S.gside; localStorage.setItem('bne.gside', S.gside ? '1' : '0');
    renderAnnot(); return;
  }
  const side = e.target.closest('#gside'); if (!side) return;
  e.stopImmediatePropagation();
  const row = e.target.closest('[data-gs]'); if (!row) return;
  const gid = +row.dataset.gs;
  const st = e.target.closest('[data-gs-step]');
  if (st) { stepGroup(gid, stepRef(gid, st.dataset.gsStep), st.dataset.gsStep); return; }
  if (e.target.closest('[data-gs-paint]')) { if (S.paint && S.paint.id === gid) stopPaint(); else startPaint(gid); renderGside(); return; }
  S.hl = S.hl === gid ? null : gid;
  drawOverlay(); renderGside();
}, true);
$('#main').addEventListener('dblclick', e => {
  const row = e.target.closest('#gside [data-gs]'); if (!row) return;
  e.stopImmediatePropagation();
  openGroup(+row.dataset.gs);
}, true);
$('#main').addEventListener('input', e => {
  if (e.target.id !== 'gsq') return;
  S.gsq = e.target.value;
  clearTimeout(renderGside._t); renderGside._t = setTimeout(renderGside, 150);
});
$('#main').addEventListener('mousedown', e => { if (e.target.closest('#gside')) e.stopImmediatePropagation(); }, true);

/* ------------------------------------------------------------ entity filter (annotation view limited to one entity) */
S.ef = { on: false, gid: null, q: '', offset: 0, total: 0, listHTML: '' };  // one group's mentions/quotes in context, 20 excerpts a page

/* One page of excerpts (mentions and quotes, with context) for the chosen entity, combined into one synthetic window
   so the normal annotation rendering (renderAnnot) draws it unchanged, gaps between excerpts and all. */
async function loadEntityWindow() {
  const ef = S.ef;
  const empty = () => ({ first: 0, last: -1, n_sentences: S.info.n_sentences, start: 0, end: -1,
    sentences: [], tokens: [], entities: [], supersenses: [], quotes: [], pending: [] });
  if (ef.gid == null) { S.win = empty(); return; }
  let r;
  try { r = await api('GET', `/api/entity-context/${ef.gid}?offset=${ef.offset * 20}&limit=20`); }
  catch (err) { fail(err); ef.gid = null; S.win = empty(); return; }
  ef.total = r.total;
  const w = empty();
  for (const it of r.items) for (const k of ['sentences', 'tokens', 'entities', 'supersenses', 'quotes', 'pending']) w[k].push(...it[k]);
  S.win = w;
  S.pendingSet = new Set(w.pending);
}
/* The entity picker for this mode: the same group list the Entities tab shows, searchable. */
async function efListHTML() {
  const ef = S.ef;
  const r = await api('GET', `/api/groups?${new URLSearchParams({ q: ef.q, hidden: '0' })}`);
  const rows = r.items.map(c => `<li class="crow${c.id === ef.gid ? ' sel' : ''}" data-efgid="${c.id}">
    <span></span><span class="sw" style="--h:${hue(c.id)}"></span><span class="cn">${esc(c.name)}</span>
    <span class="ct">${typeLabel(c)}</span><span class="num">${c.count}${c.quotes ? ` · ${c.quotes}q` : ''}</span></li>`).join('');
  return `<div class="gsh"><input type="search" id="efq" placeholder="Find a group" value="${esc(ef.q)}" aria-label="Find a group"></div>
    <ul class="crows">${rows || '<li class="more">No groups match.</li>'}</ul>`;
}
/* Redraw entity-filter mode: the picker list, then (once a group is chosen) its excerpts. */
async function renderEntityAnnot() {
  S.ef.listHTML = await efListHTML();
  await loadEntityWindow();
  renderAnnot();
}
$('#main').addEventListener('click', e => {
  if (e.target.id === 'efBtn') {
    e.stopImmediatePropagation();
    stopPasses();
    S.ef.on = !S.ef.on;
    if (S.ef.on) { S.ef.gid = null; S.ef.offset = 0; }
    renderEntityAnnot().catch(fail);
    return;
  }
  if (e.target.id === 'efChange') { e.stopImmediatePropagation(); S.ef.gid = null; S.ef.offset = 0; renderEntityAnnot().catch(fail); return; }
  const efp = e.target.closest('[data-efpage]');
  if (efp) { e.stopImmediatePropagation(); S.ef.offset += efp.dataset.efpage === 'next' ? 1 : -1; renderEntityAnnot().catch(fail); return; }
  const side = e.target.closest('#efside'); if (!side) return;
  e.stopImmediatePropagation();
  const row = e.target.closest('[data-efgid]'); if (!row) return;
  S.ef.gid = +row.dataset.efgid; S.ef.offset = 0;
  renderEntityAnnot().catch(fail);
}, true);
$('#main').addEventListener('input', e => {
  if (e.target.id !== 'efq') return;
  S.ef.q = e.target.value;
  clearTimeout(renderEntityAnnot._t); renderEntityAnnot._t = setTimeout(() => renderEntityAnnot().catch(fail), 150);
});
$('#main').addEventListener('mousedown', e => { if (e.target.closest('#efside')) e.stopImmediatePropagation(); }, true);

/* Quotes view: the whole-book quote rule lives in the Entities tab */
$('#main').addEventListener('click', e => {
  const b = e.target.closest('[data-goqr]'); if (!b) return;
  e.stopImmediatePropagation();
  openPane('quoterule');
}, true);

/* ------------------------------------------------------------ start */
init().then(async () => {
  await loadHotkeys();
  await startNav();
  showPaintBanner();
  if (typeof afterStart === 'function') await afterStart();
}).catch(fail);
