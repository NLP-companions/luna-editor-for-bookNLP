'use strict';
/* Quotes view: the "Speaker suggestions" sub-tab, a review queue like the Entities merge suggestions. */

Object.assign(S.qv, { pane: 'list', sq: 0, sqItems: null, bopen: null, bexcl: new Set() });

/* The tabs of the Quotes view: All quotes, Speaker suggestions (with a count), and the quote-rule link. */
function quotesNav() {
  const p = S.qv.pane;
  setTimeout(loadQsBadge, 0);
  return `<nav class="enav" aria-label="Quote tools">
    <button data-qpane="list" aria-pressed="${p === 'list'}">All quotes</button>
    <button data-qpane="sug" aria-pressed="${p === 'sug'}">Speaker suggestions <span class="badge" id="bdQs"></span></button>
    <span class="grow"></span><button class="link" data-goqr title="Move I, me, my inside quotes to each quote’s speaker">Quote rule…</button></nav>`;
}
/* Fill in the number of speaker suggestions on its tab. */
async function loadQsBadge() {
  try {
    const r = await api('GET', '/api/quote-suggestions?limit=1');
    const el = $('#bdQs'); if (el) el.textContent = r.total ? r.total.toLocaleString() : '';
  } catch { }
}
/* Switch between All quotes and Speaker suggestions. */
function setQPane(p) {
  saveNav();
  S.qv.pane = p;
  renderQuotesView().then(() => { $('#main').scrollTop = 0; pushNav(); }).catch(fail);
}

/* ------------------------------------------------------------ rendering */
/* A speaker as a coloured chip with its group number. */
function sqChip(g, extra = '') {
  if (!g) return '<span class="spchip none">No speaker</span>';
  return `<span class="spchip" style="--h:${hue(g.id)}">${esc(g.name)}</span><span class="num">#${g.id}${extra}</span>`;
}
/* The reasons for and against a candidate speaker, with their weights. */
function sqReasons(rs) {
  return rs.length ? `<ul class="reasons">${rs.map(x => `<li class="${x.w >= 0 ? 'pos' : 'neg'}"><span class="w">${x.w > 0 ? '+' : ''}${x.w}</span>${esc(x.text)}</li>`).join('')}</ul>` : '<p class="note">No evidence either way.</p>';
}
/* A quote in context with BookNLP's speaker mention and the suggested one marked. */
function sqContext(it) {
  const c = it.ctx, cm = it.cur_mention, bm = it.best_mention;
  return (c.start > 0 ? '… ' : '') + c.words.map((w, i) => {
    const o = c.start + i;
    let h = esc(w);
    if (bm && o >= bm.s && o <= bm.e) h = `<b class="spk sug" style="--h:${hue(it.best.id)}" title="Suggested speaker mention">${h}</b>`;
    else if (cm && o >= cm.s && o <= cm.e) h = `<b class="spk" style="--h:${hue(it.cur ? it.cur.id : 0)}" title="BookNLP’s speaker mention">${h}</b>`;
    return o >= c.s && o <= c.e ? `<mark>${h}</mark>` : h;
  }).join(' ') + ' …';
}
/* The speakers offered on a card: the suggestion, then alternatives (up to six). */
function sqChoices(it) {
  const list = [];
  if (it.best) list.push({ ...it.best, reason: it.best_reasons[0] ? it.best_reasons[0].text : '', mention: it.best_mention ? it.best_mention.uid : null });
  for (const a of it.alternatives) list.push(a);
  return list.slice(0, 6);
}
/* Why a quote is suggested for review. */
const SQ_KIND = { other: 'A different speaker is more likely', none: 'No speaker yet', doubt: 'BookNLP’s speaker looks doubtful' };
/* The Speaker suggestions tab: bundles to apply together, and one suggestion at a time. */
async function renderQuoteSug() {
  const r = await api('GET', '/api/quote-suggestions?limit=500');
  if (S.view !== 'quotes' || S.mode !== 'table' || S.qv.pane !== 'sug') return;
  const qv = S.qv;
  qv.sqItems = r.items;
  if (qv.sq >= r.items.length) qv.sq = Math.max(0, r.items.length - 1);
  const it = r.items[qv.sq];
  const bundles = r.bundles.map((b, i) => {
    const open = qv.bopen === i;
    const n = b.items.filter(x => !qv.bexcl.has(x.uid)).length;
    return `<div class="bundle"><div class="bh"><b>${esc(b.title)}</b><span class="num">${b.n.toLocaleString()} quote${b.n === 1 ? '' : 's'}</span><span class="grow"></span>
        <button class="btn sm primary" data-sqb="${i}" data-sqbact="apply">Apply ${n === b.n ? 'all' : n}</button><button class="btn sm" data-sqb="${i}" data-sqbact="toggle">${open ? 'Hide list' : 'Review list'}</button></div>
      ${open ? `<ul class="blist">${b.items.map(x => `<li><label class="chk"><input type="checkbox" data-sqx="${x.uid}" ${qv.bexcl.has(x.uid) ? '' : 'checked'}> S ${esc(String(x.s))}</label>
        <span class="mctx">“${esc(trim(x.text.replace(/^["“”'\s]+|["“”'\s]+$/g, ''), 90))}”</span>
        <span class="qrmv">${x.cur ? `<span class="sw" style="--h:${hue(x.cur.id)}"></span>${esc(trim(x.cur.name, 16))}` : '<span class="muted">none</span>'} → <span class="sw" style="--h:${hue(x.best.id)}"></span>${esc(trim(x.best.name, 16))}</span>
        <button class="link" data-qact="goto" data-ord="${x.s}">Show</button></li>`).join('')}</ul>` : ''}</div>`;
  }).join('');
  let card = '<p class="muted">No speaker suggestions left. New ones appear as you edit.</p>';
  if (it) {
    const choices = sqChoices(it);
    const sameName = it.best && it.cur && it.best.name === it.cur.name && it.best.id !== it.cur.id;
    card = `<div class="mcard" id="sqcard">
      <div class="mhead"><span>Suggestion ${qv.sq + 1} of ${r.total.toLocaleString()} · ${SQ_KIND[it.kind]}</span><span class="num">S ${it.sent} · t${it.s}–${it.e}</span></div>
      <p class="qctx sqctx">${sqContext(it)}</p>
      <div class="sqcols">
        <div class="mside"><div class="sql">Now</div><div class="msn">${sqChip(it.cur)}${it.cur ? `<span class="num">score ${it.cur_score}</span>` : ''}</div>${it.cur ? sqReasons(it.cur_reasons) : ''}</div>
        <div class="mside keep"><div class="sql">Suggested</div>${it.best ? `<div class="msn">${sqChip(it.best)}<span class="num">score ${it.best_score}</span></div>${sqReasons(it.best_reasons)}`
          : '<p class="note">No better speaker found nearby. Choose one below or with G.</p>'}</div>
      </div>
      ${sameName ? `<div class="sqsame"><span>“${esc(it.cur.name)}” #${it.cur.id} and #${it.best.id} are two groups with the same name. Merging them in one step fixes this and every quote like it.</span><button class="btn sm" data-sqa="mergegroups">Merge the two groups</button></div>` : ''}
      <div class="row mact" style="margin-top:12px">
        ${it.best ? `<button class="btn primary" data-sqa="accept">Accept “${esc(trim(it.best.name, 22))}” <kbd>A</kbd></button>` : ''}
        <button class="btn" data-sqa="other">Other speaker… <kbd>G</kbd></button>
        ${it.cur ? `<button class="btn" data-sqa="keep" title="Keep BookNLP’s speaker; this suggestion won’t come back">Keep “${esc(trim(it.cur.name, 18))}” <kbd>K</kbd></button>` : `<button class="btn" data-sqa="keep" title="Leave it without a speaker; this suggestion won’t come back">Leave without speaker <kbd>K</kbd></button>`}
        <button class="btn" data-sqa="skip">Skip <kbd>S</kbd></button>
        <button class="link" data-sqa="prev" ${qv.sq ? '' : 'disabled'}>Previous <kbd>P</kbd></button>
        <button class="link" data-qact="goto" data-ord="${it.s}" data-end="${it.e}">Show in text</button></div>
      <h3>Or choose the speaker</h3>
      <ol class="fcands">${choices.map((c, i) => `<li><button class="btn fc" data-sqpick="${i}"><kbd>${i + 1}</kbd><span class="sw" style="--h:${hue(c.id)}"></span>${esc(c.name)} <span class="num">#${c.id}</span></button><span class="note">${esc(c.reason || '')}</span></li>`).join('')}
        <li><button class="btn fc" data-sqpick="none"><kbd>0</kbd>No speaker</button><span class="note">For narration marked as a quote, or when it can’t be told</span></li></ol>
    </div>`;
  }
  const kinds = r.by_kind || {};
  $('#main').innerHTML = `<div class="qwrap">${quotesNav()}
    <div class="overview wide"><h2>Speaker suggestions</h2>
    <p class="note" style="margin-top:0">Quotes whose speaker is probably different from BookNLP’s, or missing. The evidence: an attribution tag next to the quote (<i>“…,” said Holmes</i>; <i>Holmes said, “…”</i>; <i>“…” he asked</i>), a quote that goes on from the one before it in the same paragraph, turn-taking in a conversation, and how much the quote sounds like each candidate’s other quotes. Against a speaker: being addressed by name in the quote, or BookNLP linking it through a mention inside the quote or far away. The weights are under Suggestion settings in the library.
    ${[[kinds.other, 'with a different speaker'], [kinds.none, 'without a speaker'], [kinds.doubt, 'doubtful']].filter(([n]) => n).map(([n, l]) => `${n} ${l}`).join(', ')}${r.total ? '.' : ''}</p>
    ${bundles ? `<h3>Clear attribution tags, apply in one go</h3>${bundles}<h3>One by one</h3>` : ''}${card}</div></div>`;
}

/* ------------------------------------------------------------ actions */
/* Send the chosen speaker changes in one step and refresh. */
async function sqApply(items) {
  const r = await api('POST', '/api/quote-suggestions/apply', { items });
  updateHistoryButtons(r.history); toast(r.history.undo);
  await refresh(true);
}
/* Act on the current suggestion (accept, choose, other speaker, keep BookNLP's, skip, previous, merge the two groups). */
async function sqAct(kind, arg) {
  const qv = S.qv, it = (qv.sqItems || [])[qv.sq]; if (!it) return;
  try {
    if (kind === 'skip') { qv.sq++; renderQuoteSug().catch(fail); return; }
    if (kind === 'prev') { qv.sq = Math.max(0, qv.sq - 1); renderQuoteSug().catch(fail); return; }
    if (kind === 'accept') { if (!it.best) return; noteRecent(it.best.id); await sqApply([{ quote: it.uid, speaker: it.best.id, mention: it.best_mention ? it.best_mention.uid : null }]); return; }
    if (kind === 'keep') { await api('POST', '/api/quote-suggestions/dismiss', { key: it.key }); toast('Kept; this suggestion won’t come back'); renderQuoteSug().catch(fail); loadQsBadge(); return; }
    if (kind === 'pick') {
      if (arg === 'none') { await sqApply([{ quote: it.uid, speaker: null }]); return; }
      const c = sqChoices(it)[arg]; if (!c) return;
      noteRecent(c.id);
      await sqApply([{ quote: it.uid, speaker: c.id, mention: c.mention || null }]);
      return;
    }
    if (kind === 'other') {
      const btn = $('[data-sqa="other"]');
      groupPicker(btn.getBoundingClientRect(), { title: 'Who says this?', allowNew: false, allowNone: true, near: { a: it.s, b: it.e },
        onPick: t => sqApply([{ quote: it.uid, speaker: t }]).catch(fail) });
      return;
    }
    if (kind === 'mergegroups') {
      const [keep, drop] = (it.cur.count || 0) >= (it.best.count || 0) ? [it.cur.id, it.best.id] : [it.best.id, it.cur.id];
      const r = await api('POST', '/api/groups/merge', { target: keep, sources: [drop] });
      updateHistoryButtons(r); toast(r.undo);
      await refresh(true);
    }
  } catch (e) { fail(e); }
}
// called first from app.js's Quotes click handler; returns true when it handled the click
function onQuoteSugClick(e) {
  const pn = e.target.closest('[data-qpane]');
  if (pn) { setQPane(pn.dataset.qpane); return true; }
  if (S.qv.pane !== 'sug') return false;
  const a = e.target.closest('[data-sqa]'); if (a) { sqAct(a.dataset.sqa); return true; }
  const p = e.target.closest('[data-sqpick]'); if (p) { sqAct('pick', p.dataset.sqpick === 'none' ? 'none' : +p.dataset.sqpick); return true; }
  const b = e.target.closest('[data-sqb]');
  if (b) {
    (async () => {
      const qv = S.qv, r = await api('GET', '/api/quote-suggestions?limit=1');
      const bd = r.bundles[+b.dataset.sqb]; if (!bd) { renderQuoteSug().catch(fail); return; }
      if (b.dataset.sqbact === 'toggle') { qv.bopen = qv.bopen === +b.dataset.sqb ? null : +b.dataset.sqb; renderQuoteSug().catch(fail); return; }
      const items = bd.items.filter(x => !qv.bexcl.has(x.uid)).map(x => ({ quote: x.uid, speaker: x.best.id, mention: x.mention }));
      if (!items.length) { toast('Nothing ticked'); return; }
      if (items.length > 25 && !confirm(`Set the speaker of ${items.length.toLocaleString()} quotes? You can undo this in one step.`)) return;
      qv.bopen = null; qv.bexcl.clear();
      await sqApply(items);
    })().catch(fail);
    return true;
  }
  if (e.target.matches('input')) return true;
  return false;
}
document.addEventListener('change', e => {
  const t = e.target.closest('[data-sqx]'); if (!t) return;
  const id = +t.dataset.sqx; t.checked ? S.qv.bexcl.delete(id) : S.qv.bexcl.add(id);
  renderQuoteSug().catch(fail);
});
window.addEventListener('keydown', e => {
  if (S.view !== 'quotes' || S.qv.pane !== 'sug' || S.mode !== 'table' || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.target.matches && e.target.matches('input, select, textarea')) return;
  if (!pop.hidden || !$('#drawer').hidden || !(S.qv.sqItems || []).length) return;
  const k = e.key.toLowerCase();
  const map = { a: 'accept', k: 'keep', g: 'other', s: 'skip', p: 'prev' };
  let act = null;
  if (map[k]) act = () => sqAct(map[k]);
  else if (k === '0') act = () => sqAct('pick', 'none');
  else if (/^[1-6]$/.test(k)) act = () => sqAct('pick', +k - 1);
  if (!act) return;
  e.preventDefault(); e.stopImmediatePropagation();
  act();
}, true);
