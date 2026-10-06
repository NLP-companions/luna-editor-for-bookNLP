'use strict';
/* The passes: go through the pronouns (Pronoun pass) or the quotes (Quote pass) of the book one at a time, in reading
   order, in the annotation view. This file holds the engine both share (`makePass`) and the Pronoun pass's own part;
   qpassui.js holds the Quote pass's. You choose what to skip and how to advance (settings are saved per book): 'auto'
   (Keep or a candidate also goes on to the next one) or 'manual' (they stay put; you go on yourself with Next, so you can
   keep working on the same one first).
   Keys while a pass runs: 1–6 move to a candidate, Enter keeps it, → next (also the manual advance), ← back, G another
   group (speaker), Esc stops. Both passes share one bar (#banner); starting one stops the other and paint mode. */

S.pp = null;
/* Every pass that exists, so the rest of the page can stop them, refresh them or ask which one owns the bar. */
const PASSES = [];
/* Stop every running pass (starting paint mode or the entity filter does this). */
function stopPasses() { for (const p of PASSES) if (S[p.key]) p.stop(); }
/* Refresh the running passes' current item after an edit made elsewhere (side panel, table, hotkey), so the bar doesn't go stale. */
async function syncPassItems() { for (const p of PASSES) if (S[p.key] && S[p.key].cur) await p.sync(); }
/* Is a pass running in a text view (annotation or table)? */
function anyPassActive() { return PASSES.some(p => p.active()); }

/* Build one pass from a description of what differs between the kinds:
     key        'pp' or 'qp': where its state lives (S[key]), and the prefix of its data-attributes in the bar
     kind       what #banner.dataset.kind says while its bar is showing
     name       'Pronoun pass' / 'Quote pass';  noun  'pronouns' / 'quotes' (for the "no more …" line)
     reasons    {skip reason: short text} for the "Skipped: …" line;  skips  [[key, label, hint]] for the settings popup
     presets    the title of the Edit and Check preset buttons;  settingsTitle  the tooltip of the bar's Settings button
     layer      the layer whose span is selected in the text ('entities' / 'quotes')
     focus(x)   the group to highlight in the text for an item
     question(x)  the bar's sentence about the item;  chip(c)  the title of a candidate's button
     moveTo(x, target, name)  make the item belong to `target` and confirm it (API calls, history, toast)
     other(x, rect)  open the picker for "another group";  extraSettings(st)  the settings popup's extra rows (or '')
     fetch(q) / saveSettings(body) / confirm(uids)  the three API calls of the pass (literal URLs, so tests/test_static.py can check them) */
function makePass(spec) {
  const key = spec.key;
  const attr = (name) => `data-${key}${name}`;      // e.g. data-ppc, data-pps-preset
  const set = (name) => `${key}s${name}`;           // the dataset name of data-pps-preset: ppsPreset
  const me = {
    key, kind: spec.kind,

    /* Is the pass running in a text view? */
    active() { return !!S[key] && S.mode === 'table' && (S.view === 'annot' || S.view === 'table'); },

    /* Start from a position (default: the selected word or the top of the page). */
    async start(from) {
      if (S.paint) stopPaint();
      for (const p of PASSES) if (p !== me && S[p.key]) p.stop();
      if (S.ef.on) S.ef.on = false;
      closePop();
      S[key] = { trail: [], cur: null, r: null };
      if (S.view !== 'annot') setView('annot');
      let ref = from;
      if (ref == null) {
        const sel = S.sel;
        ref = sel && sel.kind === 'span' && sel.s != null ? sel.s - 1 : sel && sel.kind === 'token' ? sel.ord - 1 : S.win ? S.win.start - 1 : -1;
      }
      try { await me.go(`ord=${ref}&dir=next`, false); } catch (e) { fail(e); }
    },

    /* End the pass and remove its bar. */
    stop() {
      if (!S[key]) return;
      S[key] = null;
      const b = $('#banner');
      if (b.dataset.kind === spec.kind) { b.hidden = true; b.dataset.kind = ''; }
      S.hl = null;
      closePop();
      if (S.view === 'annot') renderAnnot();
    },

    /* Fetch an item (next, previous or a given one: `q` is the query), jump to it and show the bar. */
    async go(q, pushTrail = true) {
      const r = await spec.fetch(q);
      if (!S[key]) return;
      if (pushTrail && S[key].cur) S[key].trail.push(S[key].cur.uid);
      S[key].r = r; S[key].cur = r.item;
      if (r.item) {
        S.hl = spec.focus(r.item);
        await goTo(r.item.s, async () => { await selectSpan(spec.layer, r.item.uid); });
      }
      me.bar();
    },

    /* Refresh the current item's group and candidates from the server, without moving on: used after a manual edit made
       elsewhere while the pass is showing it, so the bar doesn't go stale. */
    async sync() {
      if (!S[key] || !S[key].cur) return;
      const uid = S[key].cur.uid;
      try {
        const r = await spec.fetch(`uid=${uid}`);
        if (!S[key] || !S[key].cur || S[key].cur.uid !== uid) return;
        S[key].r = r; S[key].cur = r.item;
        S.hl = spec.focus(r.item);
        me.bar();
        syncPassBanner();
      } catch (e) { /* the item is gone (deleted elsewhere); leave the bar showing what it last had */ }
    },

    /* 'Skipped: 12 rules, 4 confirmed …' for the bar. */
    skipped(r) {
      const parts = Object.entries(r.skipped || {}).filter(([, n]) => n).map(([k, n]) => `${n.toLocaleString()} ${spec.reasons[k] || k}`);
      return parts.length ? `Skipped: ${parts.join(', ')}` : 'Nothing skipped';
    },

    /* Draw the bar: the item, its group, candidate groups, and the buttons. */
    bar() {
      const b = $('#banner'), p = S[key]; if (!p) return;
      const r = p.r, x = p.cur;
      b.dataset.kind = spec.kind;
      b.hidden = false;
      const preset = r.settings.preset === 'edit' ? 'Edit pass' : r.settings.preset === 'check' ? 'Check pass' : 'Your settings';
      if (!x) {
        b.innerHTML = `<div class="ppbar"><span class="pph"><b>${spec.name}</b> <span class="num">${preset}</span></span>
          <span>No more ${spec.noun} to look at after this point. ${esc(me.skipped(r))} of ${r.total.toLocaleString()}.</span>
          <span class="ppa"><button class="btn sm" ${attr('')}="restart">Start from the beginning</button><button class="btn sm" ${attr('')}="settings">Settings</button><button class="btn sm" ${attr('')}="stop">Stop</button></span></div>`;
        return;
      }
      const manual = r.settings.advance === 'manual';
      const cands = x.candidates.map((c, i) => `<button class="cchip${spec.nofit && spec.nofit(c) ? ' nofit' : ''}" ${attr('c')}="${c.id}" title="${esc(spec.chip(c))}"><kbd>${i + 1}</kbd><span class="sw" style="--h:${hue(c.id)}"></span>${esc(trim(c.name, 18))}</button>`).join('');
      b.innerHTML = `<div class="ppbar">
        <span class="pph"><b>${spec.name}</b> <span class="num">${x.index ? `${x.index.toLocaleString()} of ${r.shown.toLocaleString()}` : ''} · ${preset}</span></span>
        <span class="ppq">${spec.question(x)}</span>
        <span class="ppc"><button class="btn sm primary" ${attr('')}="ok" title="${manual ? 'It’s right: keep it' : 'It’s right: keep it and go on'}">Keep <kbd>↵</kbd></button><span class="muted">or move to</span>${cands}<button class="btn sm" ${attr('')}="other">Other… <kbd>G</kbd></button></span>
        <span class="ppa"><button class="btn sm" ${attr('')}="prev" ${p.trail.length ? '' : 'disabled'} title="Back to the previous one">‹ <kbd>←</kbd></button><button class="btn sm" ${attr('')}="next" title="${manual ? 'Go on to the next one' : 'Go on without confirming'}">${manual ? 'Next' : 'Skip'} <kbd>→</kbd></button>
          <button class="btn sm" ${attr('')}="settings" title="${esc(spec.settingsTitle)}">Settings</button><button class="btn sm" ${attr('')}="stop">Stop <kbd>Esc</kbd></button></span>
        <span class="note ppn">${esc(me.skipped(r))} · number keys choose from this bar while the pass runs</span></div>`;
    },

    /* After Keep or moving to a candidate: go on to the next one (advance: 'auto') or just refresh this one in place so you can
       keep working on it ('manual': you go on yourself with Next). */
    async on(x) {
      if (S[key].r.settings.advance === 'manual') await me.go(`uid=${x.uid}`, false);
      else await me.go(`ord=${x.s}&dir=next`);
    },

    /* Move the current item to a group (what that means is the kind's own: `moveTo`), refresh, and go on (or not). */
    async move(target, name) {
      const x = S[key] && S[key].cur; if (!x) return;
      try {
        await spec.moveTo(x, target, name);
        await refresh(true);
        await me.on(x);
      } catch (e) { fail(e); }
    },

    /* Act on a bar button (keep, skip, back, other group, settings, restart, stop). */
    async act(k, el) {
      const p = S[key]; if (!p) return;
      const x = p.cur;
      try {
        if (k === 'stop') { me.stop(); return; }
        if (k === 'restart') { p.trail = []; await me.go('ord=-1&dir=next', false); return; }
        if (k === 'settings') { me.settings(el); return; }
        if (!x) return;
        if (k === 'ok') { await spec.confirm([x.uid]); await me.on(x); return; }
        if (k === 'next') { await me.go(`ord=${x.s}&dir=next`); return; }
        if (k === 'prev') { const uid = p.trail.pop(); if (uid == null) return; await me.go(`uid=${uid}`, false); return; }
        if (k === 'other') spec.other(x, (el || $(`[${attr('')}="other"]`)).getBoundingClientRect());
      } catch (e) { fail(e); }
    },

    /* The settings popup: presets, the kind's extra rows (what to show), what to skip, how to advance. */
    settings(anchor) {
      const st = S[key].r.settings;
      pop.innerHTML = `<div class="gp pps"><div class="gpt">${spec.name} settings</div>
        <div class="row"><button class="btn sm${st.preset === 'edit' ? ' primary' : ''}" ${attr('s-preset')}="edit" title="${esc(spec.presets.edit)}">Edit pass</button>
          <button class="btn sm${st.preset === 'check' ? ' primary' : ''}" ${attr('s-preset')}="check" title="${esc(spec.presets.check)}">Check pass</button></div>
        ${spec.extraSettings(st)}
        <div class="gpl" style="margin-top:8px">Skip</div>${spec.skips.map(([k, l, h]) => `<label class="chk ppsk"><input type="checkbox" ${attr('s-skip')}="${k}" ${st.skip.includes(k) ? 'checked' : ''}> ${l}${h ? `<span class="note">${h}</span>` : ''}</label>`).join('')}
        <div class="gpl" style="margin-top:8px">How to advance</div>
        <div class="row"><button class="btn sm${st.advance === 'auto' ? ' primary' : ''}" ${attr('s-advance')}="auto" title="Keep or a candidate also goes on to the next one">Auto</button>
          <button class="btn sm${st.advance === 'manual' ? ' primary' : ''}" ${attr('s-advance')}="manual" title="Keep or a candidate stays put; go on yourself with Next">Manual</button></div>
        <div class="row" style="margin-top:8px"><button class="btn sm" ${attr('s')}="close">Done</button></div></div>`;
      pop.hidden = false;
      const r = anchor.getBoundingClientRect();
      pop.style.left = Math.max(8, Math.min(innerWidth - pop.offsetWidth - 8, r.right - pop.offsetWidth)) + 'px';
      pop.style.top = r.bottom + 6 + 'px';
      const apply = async body => {
        try {
          const s = await spec.saveSettings(body);
          S[key].r.settings = s;
          const x = S[key].cur;
          await me.go(`ord=${x ? x.s - 1 : -1}&dir=next`, false);
          me.settings($(`[${attr('')}="settings"]`) || anchor);
        } catch (e) { fail(e); }
      };
      pop.onclick = e => {
        const pr = e.target.closest(`[${attr('s-preset')}]`); if (pr) { apply({ preset: pr.dataset[set('Preset')] }); return; }
        const av = e.target.closest(`[${attr('s-advance')}]`); if (av) { apply({ advance: av.dataset[set('Advance')] }); return; }
        if (e.target.closest(`[${attr('s')}="close"]`)) closePop();
      };
      pop.onchange = () => {
        const body = { skip: [...pop.querySelectorAll(`[${attr('s-skip')}]:checked`)].map(i => i.dataset[set('Skip')]) };
        if (pop.querySelector(`[${attr('s-inc')}]`)) body.include = [...pop.querySelectorAll(`[${attr('s-inc')}]:checked`)].map(i => i.dataset[set('Inc')]);
        apply(body);
      };
      pop.onkeydown = e => { if (e.key === 'Escape') { closePop(); e.stopPropagation(); } };
    },
  };

  $('#banner').addEventListener('click', e => {
    if (!S[key]) return;
    const c = e.target.closest(`[${attr('c')}]`); if (c) { me.move(+c.dataset[`${key}c`], null); return; }
    const a = e.target.closest(`[${attr('')}]`); if (a) me.act(a.dataset[key], a);
  });
  // start buttons: the annotation bar and the Entities overview card
  document.addEventListener('click', e => {
    const b = e.target.closest(`#${key}Btn, [${attr('start')}]`); if (!b) return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (S[key] && b.id === `${key}Btn`) me.stop(); else me.start(b.dataset[`${key}start`] ? -1 : null);
  }, true);
  // keys while the pass runs (before the number keys for pinned groups)
  window.addEventListener('keydown', e => {
    if (!me.active() || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target.matches && e.target.matches('input, select, textarea')) return;
    if (typeof editing !== 'undefined' && editing) return;
    if (!pop.hidden) return;
    const x = S[key].cur;
    const stop = () => { e.preventDefault(); e.stopImmediatePropagation(); };
    if (e.key === 'Escape') { stop(); me.stop(); return; }
    if (/^[1-9]$/.test(e.key)) { stop(); const c = x && x.candidates[+e.key - 1]; if (c) me.move(c.id, null); return; }
    if (e.key === 'Enter') { stop(); me.act('ok'); return; }
    if (e.key === 'ArrowRight') { stop(); me.act('next'); return; }
    if (e.key === 'ArrowLeft') { stop(); me.act('prev'); return; }
    if (e.key.toLowerCase() === 'g') { stop(); me.act('other'); }
  }, true);
  PASSES.push(me);
  return me;
}

/* ------------------------------------------------------------ the Pronoun pass */
/* Kinds of pronoun the pass can show, with labels. */
const PP_INCLUDE = [['he_she', 'he / she'], ['they', 'they'], ['it', 'it'], ['first', 'I / we'], ['you', 'you']];
/* The pronoun pass: every pronoun, with the group it is in; moving one regroups the mention (and the pass remembers it). */
const PRONOUN_PASS = makePass({
  key: 'pp', kind: 'pass', name: 'Pronoun pass', noun: 'pronouns', layer: 'entities',
  reasons: { rules: 'rules', one: 'one candidate', reviewed: 'reviewed', confirmed: 'confirmed', manual: 'moved by hand', checked: 'checked groups' },
  skips: [['rules', 'Settled by the quote or narrator rule', 'I, me, my inside a quote with a speaker, or in narration once the narrator is set'],
    ['one', 'Only one matching candidate nearby', 'he or she, when their group is the only one with those pronouns in the last few sentences'],
    ['reviewed', 'In sentences marked as reviewed', ''],
    ['confirmed', 'Confirmed earlier', 'kept with Enter or moved in a pass, and still in that group'],
    ['manual', 'Already moved by hand', 'any mention whose group you changed yourself'],
    ['checked', 'In groups marked as checked', '']],
  presets: { edit: 'Skip what the rules and the context already settle', check: 'Show everything except reviewed sentences' },
  settingsTitle: 'What to show, what to skip and how to advance',
  focus: x => x.group,
  nofit: c => !c.fits,
  chip: c => c.reason || '',
  question: x => `“<b>${esc(x.text)}</b>” is in <span class="sw" style="--h:${hue(x.group)}"></span><b>${esc(trim(x.group_name, 24))}</b>${x.group_pron ? ` <span class="num">${esc(x.group_pron)}</span>` : ''}${x.fits ? '' : ' <span class="ppwarn">doesn’t fit</span>'}${x.in_quote ? ` <span class="num">· in a quote by ${esc(x.speaker || 'no one')}</span>` : ''}`,
  extraSettings: st => `<div class="gpl" style="margin-top:8px">Show</div><div class="row">${PP_INCLUDE.map(([k, l]) => `<label class="chk"><input type="checkbox" data-pps-inc="${k}" ${st.include.includes(k) ? 'checked' : ''}> ${l}</label>`).join('')}</div>`,
  fetch: q => api('GET', `/api/ppass?${q}`),
  saveSettings: body => api('PUT', '/api/ppass/settings', body),
  confirm: uids => api('POST', '/api/ppass/confirm', { uids }),
  async moveTo(x, target, name) {
    const r = await api('POST', '/api/mentions/regroup', { uids: [x.uid], target, name });
    await api('POST', '/api/ppass/confirm', { uids: [x.uid] });
    updateHistoryButtons(r.history); toast(r.history.undo);
    noteRecent(r.target);
  },
  other: (x, rect) => groupPicker(rect, { title: `Move “${x.text}” to`, exclude: [x.group], near: { a: x.s, b: x.e }, onPick: (t, name) => PRONOUN_PASS.move(t, name) }),
});
