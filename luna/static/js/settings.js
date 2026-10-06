'use strict';
/* The Settings page: everything that applies to every book, in one place — where BookNLP output is read from and
   where exports are written, how much context the annotation view shows, and which evidence each kind of suggestion
   uses. Reachable from the library page's sidebar and, once a book is open, from its own Settings tab.
   Shared helpers ($, esc, api, toast, fail, modal/closeModal, browse) come from pageui.js, loaded first. */

/* The library overview from the server: sources, scan problems, exports_dir(_custom), the open book's name. */
let L = null;
/* {settings, defaults, spec} for every kind of suggestion, as /api/library/suggestions returns it. */
let SUG = null;
/* Downloaded transformer models, for the collection-matching tab's speech choice. */
let MODELS = null;
/* {settings, defaults} from /api/library/display-settings. */
let DISPLAY = null;
/* Which suggestion-settings tab is open. */
let sugTab = 'collection';
/* The Appearance toggle's state: 'light'/'dark' force a theme, 'auto' follows the OS (see theme.js, app.css).
   Browser-only, like the other bne.* localStorage keys (§7 of DOCUMENTATION.md); not synced anywhere. */
let THEME = (() => { try { const v = localStorage.getItem('bne.theme'); return v === 'light' || v === 'dark' ? v : 'auto'; } catch { return 'auto'; } })();

/* Set the light/dark override, apply it immediately (theme.js only runs on page load) and remember it. */
function setTheme(v) {
  THEME = v;
  try { if (v === 'auto') localStorage.removeItem('bne.theme'); else localStorage.setItem('bne.theme', v); } catch { }
  if (v === 'auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = v;
  document.querySelectorAll('#sec-appearance [data-theme]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.theme === v)));
}

/* Load everything the page shows and draw it. */
async function load() {
  [L, SUG, MODELS, DISPLAY] = await Promise.all([
    api('GET', '/api/library'),
    api('GET', '/api/library/suggestions'),
    api('GET', '/api/library/models'),
    api('GET', '/api/library/display-settings'),
  ]);
  render();
}

/* The page's sections, in order, for the table of contents: [section id, label]. */
const TOC_SECTIONS = [['sec-folders', 'Folders with BookNLP output'], ['sec-exports', 'Where exports are written'],
  ['sec-display', 'Display'], ['sec-appearance', 'Appearance'], ['sugSection', 'Suggestions']];

/* the whole page: a way back, then folders, exports, display and suggestions, each its own section */
function render() {
  $('#topActions').innerHTML = '<a class="btn sm" href="/library">Library</a>' + (L.open ? `<a class="btn primary" href="/">Back to ${esc(L.open)}</a>` : '');
  $('#settingsMain').innerHTML = `<h1>Settings</h1>
    <p class="lede">Applies to every book: where BookNLP output is read from and exports are written, how much context the annotation view shows, and which evidence each kind of suggestion uses.</p>
    <section class="lsec" id="sec-folders"><h2>Folders with BookNLP output</h2>
      ${L.sources.length ? `<ul class="srcs">${L.sources.map(s => `<li><code title="${esc(s)}">${esc(s)}</code>
        <button class="link" data-act="reveal" data-path="${esc(s)}">Show</button><button class="link" data-act="rmsrc" data-path="${esc(s)}">Remove</button></li>`).join('')}</ul>` : ''}
      <button class="btn" data-act="addsrc">Add a folder…</button>
      <p class="note">Each folder is searched for BookNLP output, in the folder itself and in the folders directly inside it. The editor’s own exports are left out.</p>
      ${L.problems.length ? `<p class="probs">${L.problems.map(esc).join('<br>')}</p>` : ''}
    </section>
    <section class="lsec" id="sec-exports"><h2>Where exports are written</h2>
      <p class="note" style="margin-top:0">Export writes a complete, cleaned copy of a book here; BookNLP's own output folder is never changed.</p>
      <div class="field"><code>${esc(L.exports_dir)}</code>${L.exports_dir_custom ? '' : ' <span class="num">(default)</span>'}</div>
      <div class="row" style="margin-top:6px"><button class="btn" data-act="chexports">Change…</button>${L.exports_dir_custom ? '<button class="btn" data-act="rstexports">Reset to default</button>' : ''}</div>
      <p class="note">Exports already written stay where they are; only new ones go to a folder you change to.</p>
    </section>
    <section class="lsec" id="sec-display"><h2>Display</h2>
      <label class="field">Sentences of context shown above and below a mention, when the annotation view is filtered to one entity
        <input type="number" min="0" max="20" step="1" value="${DISPLAY.settings.context_sentences}" data-disp="context_sentences" style="width:70px;display:block;margin-top:4px" aria-label="Sentences of context"></label>
    </section>
    <section class="lsec" id="sec-appearance"><h2>Appearance</h2>
      <p class="note" style="margin-top:0">This browser only; other browsers or devices keep their own setting.</p>
      <div class="seg" role="group" aria-label="Theme">
        <button data-theme="auto" aria-pressed="${THEME === 'auto'}">Auto</button>
        <button data-theme="light" aria-pressed="${THEME === 'light'}">Light</button>
        <button data-theme="dark" aria-pressed="${THEME === 'dark'}">Dark</button>
      </div>
    </section>
    <section class="lsec" id="sugSection"><h2>Suggestions</h2>${suggestionsHTML()}</section>`;
  renderToc();
}

/* The left-hand table of contents: one link per section, kept in step with which one is scrolled into view. */
function renderToc() {
  $('#settingsToc').innerHTML = TOC_SECTIONS.map(([id, label]) => `<a href="#${id}" data-toc="${id}">${esc(label)}</a>`).join('');
  trackToc();
}

/* Highlight the table-of-contents link for whichever section is nearest the top of .lib as it scrolls. */
function trackToc() {
  const lib = $('#settingsMain');
  const mark = () => {
    const top = lib.getBoundingClientRect().top;
    let cur = TOC_SECTIONS[0][0];
    for (const [id] of TOC_SECTIONS) {
      const el = document.getElementById(id);
      if (el && el.getBoundingClientRect().top - top <= 80) cur = id;
    }
    document.querySelectorAll('#settingsToc a').forEach(a => a.classList.toggle('on', a.dataset.toc === cur));
  };
  mark();
  lib.removeEventListener('scroll', trackToc._h);
  trackToc._h = mark;
  lib.addEventListener('scroll', trackToc._h);
}

/* What each collection-matching setting means, shown beside it: [label, explanation]. The other kinds of suggestion
   describe their evidence themselves (core/suggest.py, sent as `spec`). */
const MATCH_HELP = {
  weights: {
    name: ['Names', 'Same name as written, without titles, or sharing a name word. Says who someone is.'],
    title: ['Titles', 'Mr., Dr., Inspector… in common. Says who someone is.'],
    cooccur: ['Appears with', 'The same characters in the same paragraphs.'],
    relations: ['Relations', 'The same relations to the same characters (wife of Holmes).'],
    speech: ['Speech style', 'Function-word rates of their quotes. Weak between characters of one author.'],
    nouns: ['Described as', 'The same descriptions (the doctor, the landlady).'],
    places: ['Places', 'The same places in the same paragraphs.'],
    actions: ['Actions', 'The same verbs, possessions and descriptions.'],
    embedding: ['Speech (transformer)', 'Only when switched on below, for books where you computed them (Collection tool).'],
  },
  penalties: {
    gender: ['Pronouns differ', 'he vs she.'],
    title_gender: ['Titles differ', 'Mr. vs Mrs.'],
    first_name: ['First names differ', 'Sherlock vs Mycroft.'],
  },
  thresholds: {
    match: ['Match', 'A score at least this high, with name evidence, is ticked for you. A name alone gives 0.70.'],
    margin: ['Lead', 'How far the best character must be ahead of the next to be ticked.'],
    possible: ['Possible', 'Named groups scoring at least this are offered as a choice.'],
    possible_unnamed: ['Possible, no name', 'The same for groups without a proper name.'],
    min_evidence: ['Minimum evidence', 'Name and title weights are divided by at least this, so a name alone isn’t full proof.'],
    min_words: ['Words for speech', 'Speech style is compared only for speakers with this many quoted words.'],
    speech_features: ['Speech features', 'How many of the most frequent function words speech style compares.'],
  },
};
/* The tabs of the suggestion settings: collection matching first, then the kinds described by core/suggest.py. */
const SUGGEST_TABS = [['collection', 'Collection matching']];

/* One setting as a row: a tick box (when it can be switched off), its label, a number, and its explanation with the
   default when it differs. `path` says where the number goes: "weights", "penalties" or "thresholds". */
function settingRow({ key, label, help, value, def, path, off, sign }) {
  const step = ['min_words', 'speech_features'].includes(key) ? 1 : 0.05;
  const tick = off === undefined ? '<span></span>' : `<input type="checkbox" data-on="${esc(key)}" ${off ? '' : 'checked'} aria-label="Use ${esc(label)}">`;
  return `<label class="mset${off ? ' off' : ''}">${tick}<span class="ml">${esc(label)}${sign < 0 ? ' <span class="num">against</span>' : ''}</span>
    <input type="number" min="0" step="${step}" value="${value}" data-path="${path}" data-key="${esc(key)}" aria-label="${esc(label)}">
    <span class="note">${esc(help)}${value !== def ? ` <span class="num">default ${def}</span>` : ''}</span></label>`;
}

/* Suggestion settings: tabs (collection matching, then every kind from core/suggest.py), each with a tick box and a
   weight per kind of evidence, and its thresholds. Saved as you change it; "Reset this tab" puts it back. */
function suggestionsHTML() {
  const r = SUG, m = MODELS, tab = sugTab;
  const tabs = [...SUGGEST_TABS, ...Object.entries(r.spec).map(([k, v]) => [k, v.label])];
  const st = r.settings[tab], def = r.defaults[tab], off = new Set(st.off || []);
  let body;
  if (tab === 'collection') {
    const rows = (group, withTick) => Object.keys(MATCH_HELP[group]).map(k => settingRow({ key: k, label: MATCH_HELP[group][k][0], help: MATCH_HELP[group][k][1],
      value: group === 'thresholds' ? st[k] : st[group][k], def: group === 'thresholds' ? def[k] : def[group][k], path: group,
      off: withTick ? off.has(k) : undefined, sign: group === 'penalties' ? -1 : 1 })).join('');
    const models = m.models.some(x => x.id === st.embedding.model) ? m.models : [{ id: st.embedding.model, size: null, missing: true }, ...m.models];
    body = `<p class="note" style="margin-top:0">How a book's groups are matched to the characters of its collection (the Collection tool in Entities, and recording a checked group). The score is the weighted mean of the name and title evidence present (divided by at least the minimum evidence), plus the weighted supporting evidence, minus penalties.</p>
      <h3>Evidence</h3><p class="note" style="margin-top:0">Names and titles say who someone is; the rest only supports it, because every book has its own cast, places and topics.</p><div class="msets">${rows('weights', true)}</div>
      <h3>Against</h3><div class="msets">${rows('penalties', true)}</div>
      <h3>Thresholds</h3><div class="msets">${rows('thresholds', false)}</div>
      <h3>Speech (transformer)</h3><p class="note" style="margin-top:0">Each speaker's quotes as one vector from a downloaded model. Computed per book with “Compute speech embeddings” in the Collection tool; the stylometric speech style is kept either way.</p>
      <div class="msets"><label class="chk"><input type="checkbox" data-emb="use" ${st.embedding.use ? 'checked' : ''}> Use transformer speech in matching</label>
        <label class="mset"><span></span><span class="ml">Model</span><select data-emb="model" style="grid-column:span 2;max-width:420px">${models.map(x => `<option value="${esc(x.id)}" ${x.id === st.embedding.model ? 'selected' : ''}>${esc(x.id)}${x.id === m.default ? ' (BookNLP’s, default)' : ''}${x.missing ? ' (not downloaded)' : x.size ? ` · ${x.size}` : ''}</option>`).join('')}</select></label></div>`;
  } else {
    const sp = r.spec[tab];
    const ev = profile => sp.evidence.filter(e => !!e.profile === profile).map(e => settingRow({ key: e.key, label: e.label, help: e.help,
      value: st.weights[e.key], def: def.weights[e.key], path: 'weights', off: off.has(e.key), sign: e.sign })).join('');
    body = `<p class="note" style="margin-top:0">${esc(sp.note)}</p>
      <h3>Rules</h3><div class="msets">${ev(false)}</div>
      <h3>Character profiles</h3><p class="note" style="margin-top:0">A similarity from 0 to 100%, times the weight.</p><div class="msets">${ev(true)}</div>
      ${(sp.thresholds || []).length ? `<h3>Thresholds</h3><div class="msets">${sp.thresholds.map(x => settingRow({ key: x.key, label: x.label, help: x.help,
        value: st.thresholds[x.key], def: def.thresholds[x.key], path: 'thresholds' })).join('')}</div>` : ''}`;
  }
  return `<nav class="ltabs">${tabs.map(([k, l]) => `<button data-stab="${k}" aria-pressed="${k === tab}">${esc(l)}</button>`).join('')}</nav>
    <p class="note" style="margin-top:0">Which evidence each kind of suggestion uses and how much it counts, for every book. Untick to leave a kind of evidence out. Saved as you change it.</p>
    ${body}
    <div class="row" style="margin-top:6px"><button class="btn" data-act="sugreset">Reset this tab to defaults</button></div>`;
}

/* Redraw just the suggestions section, after a tab switch or a reset (both need fresh settings from the server). */
async function refreshSuggestions() {
  [SUG, MODELS] = await Promise.all([api('GET', '/api/library/suggestions'), api('GET', '/api/library/models')]);
  $('#sugSection').innerHTML = `<h2>Suggestions</h2>${suggestionsHTML()}`;
}

/* Save one change to the open suggestion tab and keep SUG in step with what the server now has. */
async function saveSuggestion(values) {
  try { SUG = await api('PATCH', '/api/library/suggestions', { type: sugTab, values }); toast('Saved'); }
  catch (err) { fail(err); }
}

/* ------------------------------------------------------------ actions */
$('#settingsMain').addEventListener('click', async e => {
  const stab = e.target.closest('[data-stab]');
  if (stab) { sugTab = stab.dataset.stab; await refreshSuggestions(); return; }
  const th = e.target.closest('#sec-appearance [data-theme]');
  if (th) { setTheme(th.dataset.theme); return; }
  const a = e.target.closest('[data-act]'); if (!a) return;
  const act = a.dataset.act;
  try {
    if (act === 'addsrc') browse({ title: 'Add a folder with BookNLP output', want: 'dir', start: L.sources[L.sources.length - 1],
      onPick: async p => { try { L = await api('POST', '/api/library/source', { path: p }); closeModal(); render(); toast('Folder added'); } catch (err) { fail(err); } } });
    else if (act === 'rmsrc') { L = await api('DELETE', '/api/library/source', { path: a.dataset.path }); render(); toast('Folder removed from the library. Its files and working copies are kept.'); }
    else if (act === 'reveal') await api('POST', '/api/library/reveal', { path: a.dataset.path });
    else if (act === 'chexports') browse({ title: 'Where exports are written', want: 'dir', start: L.exports_dir_custom ? L.exports_dir : null,
      onPick: async p => { try { L = await api('PATCH', '/api/library/exports-dir', { path: p }); closeModal(); render(); toast('Saved'); } catch (err) { fail(err); } } });
    else if (act === 'rstexports') { L = await api('PATCH', '/api/library/exports-dir', { path: '' }); render(); toast('Back to the default folder'); }
    else if (act === 'sugreset') {
      if (!confirm('Put every setting of this tab back to its default?')) return;
      await api('PATCH', '/api/library/suggestions', { type: sugTab, reset: true });
      await refreshSuggestions();
      toast('Back to the defaults');
    }
  } catch (err) { fail(err); }
});

$('#settingsMain').addEventListener('change', async e => {
  const t = e.target;
  if (t.dataset.disp) {
    const v = Number(t.value);
    if (!Number.isInteger(v) || v < 0 || v > 20) { toast('Use a whole number from 0 to 20'); return; }
    try { DISPLAY = await api('PATCH', '/api/library/display-settings', { values: { [t.dataset.disp]: v } }); toast('Saved'); }
    catch (err) { fail(err); }
    return;
  }
  if (!t.closest('#sugSection')) return;
  if (t.dataset.emb) { await saveSuggestion({ embedding: { [t.dataset.emb]: t.dataset.emb === 'use' ? t.checked : t.value } }); return; }
  if (t.dataset.on) {
    const off = new Set((SUG.settings[sugTab].off) || []);
    t.checked ? off.delete(t.dataset.on) : off.add(t.dataset.on);
    t.closest('.mset').classList.toggle('off', !t.checked);
    await saveSuggestion({ off: [...off] });
    return;
  }
  if (!t.dataset.path) return;
  const v = Number(t.value);
  if (!(v >= 0)) { toast('Use a number of 0 or more'); return; }
  await saveSuggestion(sugTab === 'collection' && t.dataset.path === 'thresholds' ? { [t.dataset.key]: v } : { [t.dataset.path]: { [t.dataset.key]: v } });
});

load().catch(fail);
