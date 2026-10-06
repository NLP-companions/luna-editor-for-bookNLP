'use strict';
/* The Quote pass: the Pronoun pass's engine (makePass, passui.js) pointed at a quote's speaker instead of a pronoun's group
   — every quote in reading order in the annotation view, one at a time. Picking a speaker (a candidate, a number key or
   "Other…") always applies the quote rule too (moves the quote's own I/me/you… to match), same as the quick pick in the
   speaker chip elsewhere. */

S.qp = null;
/* The quote pass: every quote, with its speaker; moving one gives the quote a speaker (and the pass remembers it). */
const QUOTE_PASS = makePass({
  key: 'qp', kind: 'qpass', name: 'Quote pass', noun: 'quotes', layer: 'quotes',
  reasons: { reviewed: 'reviewed', confirmed: 'confirmed', manual: 'set by hand', checked: 'checked speakers', has_speaker: 'already has a speaker' },
  skips: [['reviewed', 'In sentences marked as reviewed', ''],
    ['confirmed', 'Confirmed earlier', 'kept with Enter or moved in a pass, and still with that speaker'],
    ['manual', 'Speaker already set by hand', ''],
    ['checked', 'Speaker’s group marked as checked', ''],
    ['has_speaker', 'Already has a speaker', 'to focus only on the quotes still missing one']],
  presets: { edit: 'Skip speakers already confirmed or set by hand', check: 'Show every quote except reviewed sentences' },
  settingsTitle: 'What to skip and how to advance',
  focus: x => x.speaker,
  chip: c => `${c.distance} words ${c.side}`,
  question: x => `“<b>${esc(trim(x.text, 60))}</b>” is spoken by ${x.speaker != null
    ? `<span class="sw" style="--h:${hue(x.speaker)}"></span><b>${esc(trim(x.speaker_name, 24))}</b>` : '<b class="muted">no one</b>'}`,
  extraSettings: () => '',
  fetch: q => api('GET', `/api/qpass?${q}`),
  saveSettings: body => api('PUT', '/api/qpass/settings', body),
  confirm: uids => api('POST', '/api/qpass/confirm', { uids }),
  async moveTo(x, target) {
    const r = await api('POST', '/api/quotes/speaker', { uids: [x.uid], char_id: target, quote_rule: true });
    await api('POST', '/api/qpass/confirm', { uids: [x.uid] });
    updateHistoryButtons(r);
    toast(r.moved ? `${r.undo || 'Assigned'} · moved ${r.moved} pronoun${r.moved === 1 ? '' : 's'}` : (r.undo || 'Assigned'));
    if (target != null) noteRecent(target);
  },
  other: (x, rect) => groupPicker(rect, { title: `Who says “${trim(x.text, 30)}”?`, allowNew: false, allowNone: true,
    exclude: x.speaker != null ? [x.speaker] : [], near: { a: x.s, b: x.e }, onPick: t => QUOTE_PASS.move(t) }),
});
