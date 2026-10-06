'use strict';
/* Entities tools: checks for impossible links, the narrator rule and carrying corrections over from the book's collection.
   Registered in EXTRA_PANES (entities.js). */

Object.assign(S.ch, { ckKinds: new Set(['mismatch', 'mixed', 'reflexive', 'objsubj', 'appos']), ckSkip: 0, nrExcl: new Set(), carryOff: new Set(), carryAssign: {} });
S.carry = null;
/* Names of the check kinds. */
const CHECK_LABELS = { mismatch: 'Pronoun doesn’t fit', mixed: 'He and she in one group', reflexive: 'Reflexive ≠ subject', objsubj: 'Object = subject', appos: 'Apposition split' };
/* One-line explanation of each check kind, shown under the card. */
const CHECK_HELP = {
  mismatch: 'A pronoun whose gender or number doesn’t fit its group: <i>she</i> in a he/him group, <i>they</i> for one person, <i>it</i> in a PER group, <i>he</i> in a place.',
  mixed: 'A group referred to as both <i>he</i> and <i>she</i>, several times each: probably two people merged.',
  reflexive: '<i>himself, herself, myself…</i> refer back to the subject of their verb, so they belong in its group.',
  objsubj: 'In <i>Holmes pushed him aside</i>, <i>him</i> can’t be Holmes (that would be <i>himself</i>).',
  appos: 'Two descriptions set side by side for the same thing (<i>Lestrade, the Scotland Yard man</i>) are normally in one group.',
};

/* Words around a link with the mentions concerned highlighted in their group colours. */
function ctx2(words, start, spans) {
  // spans: [{s, e, h}] highlighted in their group colour
  return (start > 0 ? '… ' : '') + words.map((w, i) => {
    const o = start + i, sp = spans.find(x => o >= x.s && o <= x.e);
    return sp ? `<b style="--h:${sp.h}"${sp.cur ? ' class="cur"' : ''}>${esc(w)}</b>` : esc(w);
  }).join(' ') + ' …';
}
/* The numbered list of candidate groups to move a mention to. */
function candList(cands, verb = 'Move to') {
  return `<h3>${verb}</h3><ol class="fcands">${cands.map((c, i) => `<li><button class="btn fc" data-ckc="${c.id}"><kbd>${i + 1}</kbd><span class="sw" style="--h:${hue(c.id)}"></span>${esc(c.name)} <span class="num">#${c.id} · ${esc(c.cat)} · ${c.count}</span></button><span class="note${c.fits ? ' fitok' : ''}">${esc(c.reason || '')}</span></li>`).join('') || '<li class="muted">No other groups nearby</li>'}</ol>`;
}

/* ------------------------------------------------------------ checks */
/* Checks: links that can't be right, one card at a time with its evidence and what to do. */
async function checksPaneHTML() {
  const ch = S.ch;
  const kinds = [...ch.ckKinds].join(',');
  let r = await api('GET', `/api/checks?kinds=${kinds}&offset=${ch.ckSkip}&limit=1`);
  if (ch.ckSkip && ch.ckSkip >= r.total) { ch.ckSkip = Math.max(0, r.total - 1); r = await api('GET', `/api/checks?kinds=${kinds}&offset=${ch.ckSkip}&limit=1`); }
  const x = r.items[0];
  ch.ck = x;
  const filters = `<div class="ckf">${Object.entries(CHECK_LABELS).map(([k, l]) => `<label class="chk" title="${esc(CHECK_HELP[k].replace(/<[^>]+>/g, ''))}"><input type="checkbox" data-ckkind="${k}" ${ch.ckKinds.has(k) ? 'checked' : ''}> ${l} <span class="num">${r.counts[k] || 0}</span></label>`).join('')}</div>`;
  const head = `<div class="overview wide"><h2>Checks</h2>
    <p class="note" style="margin-top:0">Links that can’t be right, found from pronouns, titles and the dependency parse. Go through them one by one; “It’s right” is remembered.${r.dismissed ? ` ${r.dismissed} marked as right so far.` : ''}</p>${filters}`;
  if (!x) return head + '<p class="muted">Nothing left to check with these filters. New ones appear as you edit.</p></div>';
  const g = { id: x.group, name: x.group_name, count: x.group_count };
  const o = x.other;
  let body = '', acts = '';
  const common = `<button class="btn" data-cka="other">Other group… <kbd>G</kbd></button>
    <button class="btn" data-cka="ok" title="Leave it and don’t show it again">It’s right <kbd>K</kbd></button>
    <button class="btn" data-cka="skip">Skip <kbd>S</kbd></button><button class="link" data-cka="prev" ${ch.ckSkip ? '' : 'disabled'}>Previous <kbd>P</kbd></button>`;
  if (x.kind === 'mixed') {
    body = `<div class="msn"><span class="sw" style="--h:${hue(x.group)}"></span><b>${esc(x.group_name)}</b> <span class="num">#${x.group} · ${x.group_count} mentions · ${Object.entries(x.counts).map(([k, v]) => `${esc(k)} ${v}`).join(', ')}</span></div>
      <ul class="fments">${x.samples.map(m => `<li><span class="mctx">${ctx2(m.ctx.words, m.ctx.start, [{ s: m.s, e: m.e, h: hue(x.group), cur: true }])}</span><button class="link" data-gact="goto" data-ord="${m.s}">Show</button></li>`).join('')}</ul>
      ${candList(x.candidates, `Move its ${x.minority_uids.length} ${esc(x.minority)} mention${x.minority_uids.length === 1 ? '' : 's'} to`)}`;
    acts = `<button class="btn" data-cka="open">Open the group <kbd>O</kbd></button>${common}`;
  } else {
    const spans = [{ s: x.s, e: x.e, h: hue(x.group), cur: true }];
    if (o) spans.push({ s: o.s, e: o.e, h: hue(o.group) });
    body = `<div class="ckctx mctx">${ctx2(x.ctx.words, x.ctx.start, spans)}</div>
      <div class="ckwho"><span><span class="sw" style="--h:${hue(x.group)}"></span>“${esc(x.text)}” is in <b>${esc(x.group_name)}</b> <span class="num">#${x.group}${x.group_pron ? ' · ' + esc(x.group_pron) : ''}</span></span>
      ${o ? `<span><span class="sw" style="--h:${hue(o.group)}"></span>“${esc(o.text)}” is in <b>${esc(o.group_name)}</b> <span class="num">#${o.group}</span></span>` : ''}</div>`;
    if (x.kind === 'reflexive') {
      acts = `<button class="btn primary" data-cka="a">Move “${esc(trim(x.text, 14))}” to ${esc(trim(o.group_name, 18))} <kbd>A</kbd></button>
        <button class="btn" data-cka="u">Move “${esc(trim(o.text, 14))}” to ${esc(trim(x.group_name, 18))} <kbd>U</kbd></button>${common}`;
      body += candList(x.candidates, `Or move “${esc(x.text)}” to`);
    } else if (x.kind === 'appos') {
      acts = `<button class="btn primary" data-cka="a">Move “${esc(trim(x.text, 14))}” to ${esc(trim(o.group_name, 18))} <kbd>A</kbd></button>
        <button class="btn" data-cka="m" title="The bigger group keeps its number">Merge the two groups <kbd>M</kbd></button>
        <button class="btn" data-cka="u">Move “${esc(trim(o.text, 14))}” to ${esc(trim(x.group_name, 18))} <kbd>U</kbd></button>${common}`;
    } else {
      body += candList(x.candidates, `Move “${esc(x.text)}” to`);
      acts = `<button class="btn" data-cka="new">New group <kbd>N</kbd></button>${common}`;
    }
  }
  return head + `<div class="mcard" id="ckcard">
    <div class="mhead"><span>Check ${ch.ckSkip + 1} of ${r.total.toLocaleString()} · ${CHECK_LABELS[x.kind]}</span>${x.sent != null ? `<button class="link" data-gact="goto" data-ord="${x.s}">Show in text</button>` : ''}</div>
    <p class="ckwhy">${esc(x.why)}</p>${body}
    <div class="row mact">${acts}</div>
    <p class="note">${CHECK_HELP[x.kind]}</p></div></div>`;
}
/* Act on the current check (move to a candidate or group, make the same, merge, it's right, skip, previous). */
async function checkAct(kind, arg) {
  const ch = S.ch, x = ch.ck; if (!x) return;
  const move = (uids, target, name) => charsDo(() => api('POST', '/api/mentions/regroup', { uids, target, name }));
  if (kind === 'skip') { ch.ckSkip++; renderChars().catch(fail); return; }
  if (kind === 'prev') { ch.ckSkip = Math.max(0, ch.ckSkip - 1); renderChars().catch(fail); return; }
  if (kind === 'ok') { await charsDo(() => api('POST', '/api/checks/dismiss', { key: x.key })); toast('Remembered as right'); return; }
  if (kind === 'open') { setPane('group', x.group); return; }
  const uids = x.kind === 'mixed' ? x.minority_uids : [x.uid];
  if (kind === 'cand') { noteRecent(arg); await move(uids, arg); return; }
  if (kind === 'new') { await move(uids, null, null); return; }
  if (kind === 'a' && x.other) { await move([x.uid], x.other.group); return; }
  if (kind === 'u' && x.other) { await move([x.other.uid], x.group); return; }
  if (kind === 'm' && x.other) {
    const keepThis = x.group_count >= x.other.group_count;
    await charsDo(() => api('POST', '/api/groups/merge', keepThis ? { target: x.group, sources: [x.other.group] } : { target: x.other.group, sources: [x.group] }));
    return;
  }
  if (kind === 'other') {
    const btn = $('[data-cka="other"]');
    groupPicker(btn.getBoundingClientRect(), { title: x.kind === 'mixed' ? `Move ${uids.length} ${x.minority} mention${uids.length === 1 ? '' : 's'} to` : `Move “${x.text}” to`,
      exclude: [x.group], near: { a: x.s, b: x.e ?? x.s }, onPick: (t, name) => move(uids, t, name) });
  }
}

/* ------------------------------------------------------------ narrator rule */
/* Narrator rule: the suggested or chosen narrator, per-passage narrators, and the pronouns to move. */
async function narratorPaneHTML() {
  const [info, r] = await Promise.all([api('GET', '/api/narrator'), api('POST', '/api/narrator-rule', {})]);
  S.ch.nr = r; S.ch.nrInfo = info;
  const ex = S.ch.nrExcl;
  const n = r.items.filter(i => !ex.has(i.uid)).length;
  const chip = (id, name) => `<button class="cchip" data-gsel="${id}"><span class="sw" style="--h:${hue(id)}"></span>${esc(trim(name, 26))}<span class="num">#${id}</span></button>`;
  let who;
  if (info.cid != null) {
    who = `<div class="nrwho"><span>Narrator:</span>${chip(info.cid, info.name)}<span class="num">${(info.candidates.find(c => c.id === info.cid) || { first_person: 0 }).first_person} first-person pronouns outside quotes</span>
      <button class="btn sm" data-nr="choose">Change…</button><button class="link" data-nr="clear">Clear</button></div>
      ${info.named ? '' : `<div class="nrwarn"><p>This group has no name. BookNLP often keeps the narrator’s <i>I</i> in a group of its own. If the narrator is a character (Watson, say), merge this group into theirs; the narrator setting follows the merge.</p><button class="btn sm" data-nr="mergeinto">Merge into…</button></div>`}`;
  } else if (info.suggested != null) {
    const c = info.candidates[0];
    who = `<div class="nrwho"><span>Suggested narrator:</span>${chip(c.id, c.name)}<span class="num">${c.first_person} of the ${info.total} first-person pronouns outside quotes</span></div>
      <div class="row"><button class="btn primary" data-nr="use" data-g="${c.id}">Use this group</button><button class="btn" data-nr="choose">Choose another…</button><button class="link" data-nr="third">No narrator (told in the third person)</button></div>`;
  } else {
    who = `<p class="muted">No first-person pronouns outside quotes: the book seems to be told in the third person.</p>`;
  }
  if (info.lost) who = `<p class="nrwarn">The narrator group no longer has any mentions. Choose the narrator again.</p>` + who;
  const others = info.candidates.filter(c => c.id !== info.cid && c.id !== info.suggested);
  const passages = info.passages.length ? `<ul class="nrp">${info.passages.map(p => `<li><span class="num">S ${p.sent}${p.sent_end !== p.sent ? '–' + p.sent_end : ''}</span>
      <span class="mctx">${esc(p.start.join(' '))} …</span><span>${p.cid != null ? `→ <span class="sw" style="--h:${hue(p.cid)}"></span>${esc(trim(p.name, 20))}` : '→ <i>no narrator</i>'}</span>
      <button class="link" data-gact="goto" data-ord="${p.s}">Show</button><button class="link" data-nr="rmp" data-i="${p.i}">Remove</button></li>`).join('')}</ul>` : '';
  return `<div class="overview wide"><h2>Narrator rule</h2>
    <p class="note" style="margin-top:0">Outside quotes, <i>I, me, my, mine</i> and <i>myself</i> refer to the narrator. Confirm who that is, mark passages told by someone else, then move the first-person pronouns that are in another group.</p>
    ${who}
    ${others.length ? `<p class="note">Other groups with first-person pronouns outside quotes: ${others.map(c => `${esc(trim(c.name, 20))} <span class="num">#${c.id} · ${c.first_person}</span>`).join(', ')}.</p>` : ''}
    <h3>Passages with another narrator</h3>
    ${passages}<p class="note" style="margin-top:4px">A letter, a diary or a client’s long story told in the first person: select the passage in the text (drag across it) and choose “Narrator for this passage…” in the side panel. Later passages win where they overlap.</p>
    <h3>Pronouns to move <span class="num">${r.n}</span></h3>
    ${!r.set ? '<p class="muted">Set the narrator first.</p>' : r.n ? `<div class="row" style="margin:8px 0"><button class="btn primary" data-nr="apply">Move ${n === r.n ? 'all ' + r.n.toLocaleString() : n.toLocaleString()}</button>
      <button class="link" data-nr="all">Tick all</button><button class="link" data-nr="none">Untick all</button>${r.n > r.items.length ? `<span class="note">Showing the first ${r.items.length}.</span>` : ''}</div>
      <ul class="qrlist">${r.items.map(i => `<li><input type="checkbox" data-nrx="${i.uid}" ${ex.has(i.uid) ? '' : 'checked'} aria-label="Include">
        <span class="mctx">${ctxWords(i.context, i.context_start, i.s, i.e, hue(i.to))}</span>
        <span class="qrmv"><span class="sw" style="--h:${hue(i.from)}"></span>${esc(trim(i.from_name, 18))} → <span class="sw" style="--h:${hue(i.to)}"></span>${esc(trim(i.to_name, 18))}</span>
        <button class="link" data-gact="goto" data-ord="${i.s}">Show</button></li>`).join('')}</ul>`
      : '<p class="muted">Nothing to move: every first-person pronoun outside quotes is in the narrator’s group.</p>'}
    ${r.no_narrator ? `<p class="note">${r.no_narrator} first-person pronoun${r.no_narrator === 1 ? ' is' : 's are'} in passages marked as having no narrator; they’re left alone.</p>` : ''}</div>`;
}
/* Clicks in the narrator pane (choose, clear, apply the rule, passages). */
async function narratorClick(e) {
  const b = e.target.closest('[data-nr]'); if (!b) return false;
  const k = b.dataset.nr, ch = S.ch;
  const rect = b.getBoundingClientRect();
  const refresh = () => renderChars().catch(fail);
  try {
    if (k === 'use') { await api('PUT', '/api/narrator', { group: +b.dataset.g }); toast('Narrator set'); refresh(); }
    else if (k === 'clear' || k === 'third') { await api('PUT', '/api/narrator', { group: null }); toast(k === 'third' ? 'No narrator' : 'Narrator cleared'); refresh(); }
    else if (k === 'choose') groupPicker(rect, { title: 'The narrator is', allowNew: false, okLabel: 'Set', onPick: async t => { await api('PUT', '/api/narrator', { group: t }); refresh(); } });
    else if (k === 'mergeinto') {
      const src = ch.nrInfo.cid;
      groupPicker(rect, { title: `Merge “${ch.nrInfo.name}” into`, allowNew: false, exclude: [src], okLabel: 'Merge',
        onPick: t => charsDo(() => api('POST', '/api/groups/merge', { target: t, sources: [src] })) });
    }
    else if (k === 'rmp') { await api('DELETE', `/api/narrator/passage/${b.dataset.i}`); refresh(); }
    else if (k === 'all') { ch.nrExcl.clear(); refresh(); }
    else if (k === 'none') { ch.nr.items.forEach(i => ch.nrExcl.add(i.uid)); refresh(); }
    else if (k === 'apply') { const exclude = [...ch.nrExcl]; ch.nrExcl.clear(); await charsDo(() => api('POST', '/api/narrator-rule', { apply: true, exclude })); }
  } catch (err) { fail(err); }
  return true;
}

/* ------------------------------------------------------------ collection (carry-over) */
/* Why a group matches a character (core/matching.py): the score, then on opening each kind of evidence with its similarity,
   weight and reason, and the contradictions that count against it. `who` names the group when several are shown. */
function evidenceHTML(ev, who = '') {
  if (!ev) return '';
  const pct = x => Math.round(100 * x) + '%';
  const top = ev.parts.slice(0, 3).map(p => `${esc(p.label.toLowerCase())} ${pct(p.sim)}`).join(', ');
  return `<details class="evd"><summary>Evidence${who ? ` for ${esc(trim(who, 24))}` : ''}: <b>${ev.score.toFixed(2)}</b>${top ? ` · ${top}` : ''}${ev.penalties.length ? ` · <span class="neg">${ev.penalties.length} against</span>` : ''}</summary>
    <ul>${ev.parts.map(p => `<li><b>${esc(p.label)}</b> ${pct(p.sim)} <span class="num">weight ${p.w}</span>${p.why ? ` · ${esc(p.why)}` : ''}</li>`).join('')}
      ${ev.penalties.map(p => `<li class="neg"><b>${esc(p.label)}</b> −${p.w}${p.why ? ` · ${esc(p.why)}` : ''}</li>`).join('')}</ul></details>`;
}
/* Collection: what this book's collection would change (characters, retyped names, rules) to review and apply. */
async function carryPaneHTML() {
  const r = await api('GET', '/api/carry/review');
  S.ch.cv = r;
  if (!r.available) return `<div class="overview wide"><h2>Collection</h2><p class="muted">This working copy wasn’t opened from the library, so it isn’t linked to a collection. Open it from the library to carry characters and fixes over between books.</p></div>`;
  const off = S.ch.carryOff;
  const tick = (key, label, extra = '') => `<label class="chk"><input type="checkbox" data-cvk="${esc(key)}" ${off.has(key) ? '' : 'checked'}> <span>${label}${extra}</span></label>`;
  const coll = r.collection;
  const intro = coll
    ? `<p class="note" style="margin-top:0">Characters, retyped names and saved Find rules from other books in <b>${esc(coll.name)}</b> and from the list for all books. Groups are matched to characters by evidence (names, titles, who they appear with, relations, places, actions, speech), not by name alone; the weights are under Suggestion settings in the library. Untick anything that doesn’t apply here, then apply. It undoes in one step.</p>`
    : `<p class="note" style="margin-top:0">This book isn’t in a collection yet, so only the list for all books applies. <a href="/library">Put it in a collection in the library</a> to share characters with the other books of a series.</p>`;
  const chars = r.characters.map(c => {
    const a = c.actions, k = c.entry;
    const rows = [];
    if (a.merge) rows.push(tick(`c:${k}:merge`, `Merge ${a.merge.map(g => `<span class="sw" style="--h:${hue(g.id)}"></span>${esc(trim(g.name, 20))} <span class="num">#${g.id} · ${g.count}${g.score != null ? ` · score ${g.score.toFixed(2)}` : ''}</span>`).join(', ')} into it`)
      + a.merge.filter(g => g.evidence).map(g => evidenceHTML(g.evidence, g.name)).join(''));
    if (a.name) rows.push(tick(`c:${k}:name`, `Rename “${esc(a.name.from)}” to “${esc(a.name.to)}”`));
    if (a.pronoun) rows.push(tick(`c:${k}:pronoun`, `Pronouns ${a.pronoun.from ? esc(a.pronoun.from) + ' → ' : ''}${esc(a.pronoun.to)}`));
    if (a.type) rows.push(tick(`c:${k}:type`, `Set ${a.type.n} mention${a.type.n === 1 ? '' : 's'} to ${esc(a.type.to)}`));
    if (a.link) rows.push(tick(`c:${k}:link`, `Link to the collection’s character`, ' <span class="note">(so the export can match it across books)</span>'));
    return `<div class="cvc"><div class="cvh"><b>${esc(c.name)}</b><span class="num">${esc(c.list)}${c.books.length ? ' · from ' + esc(c.books.join(', ')) : ''}</span>
        <span>→</span><button class="cchip" data-gsel="${c.target}"><span class="sw" style="--h:${hue(c.target)}"></span>${esc(trim(c.target_name, 24))}<span class="num">#${c.target} · ${c.target_count}</span></button>
        ${c.ctx ? `<button class="link" data-gact="goto" data-ord="${c.ctx.s}">Show</button>` : ''}</div>${evidenceHTML(c.evidence)}<div class="cvr">${rows.join('')}</div></div>`;
  }).join('');
  const amb = r.ambiguous.map(g => `<div class="cvc"><div class="cvh"><button class="cchip" data-gsel="${g.group}"><span class="sw" style="--h:${hue(g.group)}"></span>${esc(trim(g.name, 24))}<span class="num">#${g.group} · ${g.count}</span></button>
      <span class="note">called ${Object.keys(g.names).map(esc).join(', ')}: could be</span></div>
      <div class="cvr">${g.options.map(o => `<div class="cvo"><label class="chk"><input type="radio" name="cva${g.group}" data-cva="${g.group}" value="${esc(o.entry)}" ${S.ch.carryAssign[g.group] === o.entry ? 'checked' : ''}> ${esc(o.name)} <span class="num">${esc(o.list)} · score ${o.score.toFixed(2)}</span></label>${evidenceHTML(o)}</div>`).join('')}
        <label class="chk"><input type="radio" name="cva${g.group}" data-cva="${g.group}" value="" ${S.ch.carryAssign[g.group] ? '' : 'checked'}> Neither</label></div></div>`).join('');
  const rts = r.retypes.map(x => `<li>${tick(`r:${x.entry}`, `<b>${esc(x.text)}</b> → ${esc(x.cat)} <span class="num">${x.n} mention${x.n === 1 ? '' : 's'}, now ${esc(x.from.join('/'))} · ${esc(x.list)}</span>`)}
      ${x.ctx ? `<span class="mctx">${ctxWords(x.ctx.words, x.ctx.start, x.ctx.s, x.ctx.e, 200)}</span>` : ''}</li>`).join('');
  const rules = r.rules.map(x => `<li>${x.error ? `<span class="err">${esc(x.label)}: ${esc(x.error)}</span>` : tick(`f:${x.entry}`, `${esc(x.label)} <span class="num">${x.n} change${x.n === 1 ? '' : 's'} · ${esc(x.list)}</span>`)}
      ${(x.sample || []).map(c => `<span class="mctx">${ctxWords(c.words, c.start, c.s, c.e, 200)}</span>`).join('')}</li>`).join('');
  const nothing = !chars && !amb && !rts && !rules;
  return `<div class="overview wide"><h2>Collection${coll ? ': ' + esc(coll.name) : ''}</h2>${intro}
    ${nothing ? `<p class="muted">Nothing to carry over${r.entries ? ': everything in the lists already matches this book' : ' yet. The lists fill as you mark groups as checked, retype names and save Find rules in the books of this collection'}.</p>` : ''}
    ${chars ? `<h3>Characters <span class="num">${r.characters.length}</span></h3>${chars}` : ''}
    ${amb ? `<h3>Which character is this?</h3><p class="note" style="margin-top:0">These groups could be one of these characters, but the evidence isn’t clear enough to tick for you: only a name in common, two characters close together, or no name at all. Best first; open “Evidence” to see why.</p>${amb}` : ''}
    ${rts ? `<h3>Retyped names <span class="num">${r.retypes.length}</span></h3><ul class="cvl">${rts}</ul>` : ''}
    ${rules ? `<h3>Saved Find rules <span class="num">${r.rules.length}</span></h3><ul class="cvl">${rules}</ul>` : ''}
    ${nothing ? '' : '<div class="row" style="margin:14px 0"><button class="btn primary" data-cv="apply">Apply ticked</button><button class="link" data-cv="all">Tick all</button><button class="link" data-cv="none">Untick all</button></div>'}
    ${r.already ? `<p class="note">${r.already} character${r.already === 1 ? '' : 's'} from the lists already match${r.already === 1 ? 'es' : ''} this book.</p>` : ''}
    <p class="note">The lists fill themselves: a group you mark as checked is added as a character (with its names, pronouns, type and a profile of how it appears in this book), and a name whose every mention you retype is added as a retyped name. Edit the lists in the library.</p>
    ${r.linked ? `<p class="note"><button class="link" data-cv="profiles">Update profiles from this book</button> after later corrections (${r.linked} linked group${r.linked === 1 ? '' : 's'}).</p>` : ''}
    ${embedNoteHTML(r.embeddings)}</div>`;
}
/* The Collection pane's line about transformer speech embeddings: what this book has, whether matching uses them, and the
   button to compute them with the chosen model. */
function embedNoteHTML(em) {
  if (!em) return '';
  const done = em.book_model ? `This book has them for ${em.speakers} speaker${em.speakers === 1 ? '' : 's'}${em.book_model !== em.model ? ` (made with ${esc(em.book_model)})` : ''}. ` : '';
  return `<p class="note"><button class="link" data-cv="embed">Compute speech embeddings</button> with ${esc(em.model)}: each speaker's quotes as one vector,
    an extra, optional kind of speech evidence (a minute or two for a long book). ${done}${em.use ? 'Matching uses them.' : 'Matching doesn’t use them yet: switch them on under Suggestion settings → Collection matching in the library.'}</p>`;
}
/* Keys in the Collection pane. */
function carryKeys(r) {
  const keys = [];
  for (const c of r.characters) for (const a of Object.keys(c.actions)) keys.push(`c:${c.entry}:${a}`);
  for (const x of r.retypes) keys.push(`r:${x.entry}`);
  for (const x of r.rules) if (!x.error) keys.push(`f:${x.entry}`);
  return keys;
}
/* Clicks in the Collection pane (tick or untick, choose ambiguous matches, apply). */
async function carryClick(e) {
  const b = e.target.closest('[data-cv]'); if (!b) return false;
  const r = S.ch.cv, off = S.ch.carryOff;
  // the two collection tools: run, report, and redraw (also after a failure, to reset the button)
  const tool = async (call, msg) => {
    try { const x = await call(); updateHistoryButtons(x.history); toast(msg(x)); } catch (err) { fail(err); }
    refresh(true).catch(fail);
  };
  const profilesMsg = x => `updated ${x.updated} character profile${x.updated === 1 ? '' : 's'}${x.linked ? `, linked ${x.linked} checked group${x.linked === 1 ? '' : 's'}` : ''}`;
  if (b.dataset.cv === 'embed') {
    b.disabled = true; b.textContent = 'Computing speech embeddings…';
    await tool(() => api('POST', '/api/carry/embeddings'), x => `Embedded ${x.speakers} speakers (${x.quotes.toLocaleString()} quotes); ${profilesMsg(x)}`);
    return true;
  }
  if (b.dataset.cv === 'profiles') { await tool(() => api('POST', '/api/carry/profiles'), x => `From this book: ${profilesMsg(x)}`); return true; }
  if (b.dataset.cv === 'all') { off.clear(); renderChars().catch(fail); return true; }
  if (b.dataset.cv === 'none') { carryKeys(r).forEach(k => off.add(k)); renderChars().catch(fail); return true; }
  const characters = r.characters.map(c => ({ entry: c.entry, do: Object.keys(c.actions).filter(a => !off.has(`c:${c.entry}:${a}`)) })).filter(c => c.do.length);
  const assign = Object.entries(S.ch.carryAssign).filter(([, v]) => v).map(([g, entry]) => ({ group: +g, entry }));
  const retypes = r.retypes.filter(x => !off.has(`r:${x.entry}`)).map(x => x.entry);
  const rules = r.rules.filter(x => !x.error && !off.has(`f:${x.entry}`)).map(x => x.entry);
  if (!characters.length && !assign.length && !retypes.length && !rules.length) { toast('Nothing ticked'); return true; }
  S.ch.carryOff.clear(); S.ch.carryAssign = {};
  await charsDo(() => api('POST', '/api/carry/apply', { characters, assign, retypes, rules }));
  return true;
}

/* ------------------------------------------------------------ registration */
Object.assign(EXTRA_PANES, {
  checks: { label: 'Checks', badge: 'bdCk', before: true, html: checksPaneHTML,
    click: async e => {
      const c = e.target.closest('[data-ckc]'); if (c) { checkAct('cand', +c.dataset.ckc); return true; }
      const a = e.target.closest('[data-cka]'); if (a) { checkAct(a.dataset.cka); return true; }
      return false;
    },
    key: k => {
      const x = S.ch.ck; if (!x) return false;
      if (/^[1-6]$/.test(k)) { const c = x.candidates && x.candidates[+k - 1]; if (c) { checkAct('cand', c.id); return true; } return false; }
      const map = { g: 'other', k: 'ok', s: 'skip', p: 'prev' };
      if (x.kind === 'mixed') map.o = 'open';
      if (x.kind === 'reflexive' || x.kind === 'appos') { map.a = 'a'; map.u = 'u'; }
      if (x.kind === 'appos') map.m = 'm';
      if (x.kind === 'mismatch' || x.kind === 'objsubj') map.n = 'new';
      if (map[k]) { checkAct(map[k]); return true; }
      return false;
    } },
  narrator: { label: 'Narrator rule', badge: 'bdNr', html: narratorPaneHTML, click: narratorClick },
  carry: { label: 'Collection', badge: 'bdCv', html: carryPaneHTML, click: carryClick },
});
$('#main').addEventListener('change', e => {
  if (S.view !== 'chars') return;
  const t = e.target, ch = S.ch;
  if (t.dataset.ckkind) { t.checked ? ch.ckKinds.add(t.dataset.ckkind) : ch.ckKinds.delete(t.dataset.ckkind); ch.ckSkip = 0; renderChars().catch(fail); }
  else if (t.dataset.nrx != null) { const id = +t.dataset.nrx; t.checked ? ch.nrExcl.delete(id) : ch.nrExcl.add(id); renderChars().catch(fail); }
  else if (t.dataset.cvk != null) { t.checked ? ch.carryOff.delete(t.dataset.cvk) : ch.carryOff.add(t.dataset.cvk); }
  else if (t.dataset.cva != null) { ch.carryAssign[t.dataset.cva] = t.value || null; }
});

/* Badges and overview cards (called from loadBadges in entities.js). What the collection would change is the costly one
   to work out (every group against every character), so it is asked for only where it is shown (the overview and the
   Collection pane); elsewhere the tab keeps the count it had. */
async function extraBadges(set) {
  const showCarry = S.ch.pane === 'overview' || S.ch.pane === 'carry';
  const [c, n, v, p, q] = await Promise.all([api('GET', '/api/checks?limit=0'), api('POST', '/api/narrator-rule', {}),
    showCarry ? api('GET', '/api/carry/review').catch(() => ({ available: false })) : null,
    api('GET', '/api/ppass?ord=-1').catch(() => null), api('GET', '/api/qpass?ord=-1').catch(() => null)]);
  if (v) S.ch.carryCount = v.available ? v.characters.length + v.ambiguous.length + v.retypes.length + v.rules.length : 0;
  set('bdCk', c.total); set('bdNr', n.n); set('bdCv', S.ch.carryCount || 0);
  const card = (pane, num, title, text, attr = `data-pane="${pane}"`) => `<button class="tool" ${attr}><b>${num.toLocaleString()}</b><span class="tt">${title}</span><span class="td">${text}</span></button>`;
  const passLabel = s => s.preset === 'check' ? 'check pass' : s.preset === 'edit' ? 'edit pass' : 'your settings';
  return card('checks', c.total, 'Checks', 'Links that can’t be right: pronouns that don’t fit, reflexives, objects, appositions.')
    + card('narrator', n.n, 'Narrator rule', n.set ? 'First-person pronouns outside quotes that aren’t in the narrator’s group.' : 'Confirm who tells the story, then move their <i>I, me, my</i> in one go.')
    + (p ? card('', p.shown, 'Pronoun pass', `Every pronoun in reading order, in the annotation view, skipping what you choose (${passLabel(p.settings)}).`, 'data-ppstart="1"') : '')
    + (q ? card('', q.shown, 'Quote pass', `Every quote in reading order, in the annotation view, reviewing or setting its speaker (${passLabel(q.settings)}).`, 'data-qpstart="1"') : '')
    + (v && v.available ? card('carry', S.ch.carryCount, v.collection ? `Collection: ${esc(v.collection.name)}` : 'Collection', v.collection ? 'Characters, retyped names and rules from the other books in this collection.' : 'Put this book in a collection in the library to share characters between books.') : '');
}

/* when a book is opened for the first time, offer what its collection has */
async function afterStart() {
  try {
    S.carry = await api('GET', '/api/carry');
    if (!S.carry.available || S.carry.offered || INITIAL_NAV) return;
    const total = S.carry.lists.reduce((n, l) => n + l.counts.characters + l.counts.retypes + l.counts.rules, 0);
    if (!total) return;
    await api('POST', '/api/carry/offered');
    const r = await api('GET', '/api/carry/review');
    if (r.characters.length + r.ambiguous.length + r.retypes.length + r.rules.length) {
      openPane('carry');
      toast(`${S.carry.collection ? S.carry.collection.name : 'All books'}: characters and fixes from other books to review`);
    }
  } catch { }
}
