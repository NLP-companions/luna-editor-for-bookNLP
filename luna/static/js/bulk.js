'use strict';
/* ------------------------------------------------------------ bulk changes from Find
   The bar above the Find results acts on all results, or only the ticked ones, in one undoable step:
     Change a field   set POS, lemma, type, group, speaker … on every result          POST /api/bulk
     Create           make an entity, supersense or quote over every result           POST /api/bulk/create
     Delete           delete every result (entities, supersenses, quotes)              POST /api/bulk/delete
   Each request sends the search again (S.find: layer, field, op, value, where, without) plus `uids` when only the
   ticked results are meant, so the server acts on exactly what the results list shows.
   The bar's choices live in S.find.bulk and survive re-drawing the bar; they reset when the search changes. */

const BULK_FIELDS = { tokens: [['pos', 'POS'], ['tag', 'Fine POS'], ['dep', 'Dependency'], ['lemma', 'Lemma'], ['event', 'Event']],
  entities: [['cat', 'Type'], ['prop', 'Mention type'], ['coref', 'Group']], supersenses: [['cat', 'Category']], quotes: [['char_id', 'Speaker']] };
/* What Create can make from results. */
const BULK_TARGETS = [['entities', 'an entity'], ['supersenses', 'a supersense'], ['quotes', 'a quote']];
/* How new entities are grouped: one group for all, per distinct text, per result, or an existing group. */
const GROUP_MODES = [['one', 'One new group for all'], ['text', 'One new group per distinct text'], ['each', 'A new group for each'], ['existing', 'An existing group…']];
/* Layers whose results can be deleted. */
const SPAN_LAYERS = ['entities', 'supersenses', 'quotes'];
/* '1 result' / '3 results'. */
const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;

/* the bar's current choices, filled in on first use */
function bulkState() {
  const f = S.find, fields = BULK_FIELDS[f.layer] || [];
  return f.bulk || (f.bulk = { act: 'set', field: fields.length ? fields[0][0] : '', value: '', scope: 'all',
    target: 'entities', cat: '', prop: 'PROP', gmode: 'one', coref: '', char: '' });
}
/* copy what is typed in the bar into the state (before the bar is drawn again) */
function readBulk() {
  const bar = $('.bulk'); if (!bar) return bulkState();
  const b = bulkState(), get = id => { const el = $(id, bar); return el ? el.value : undefined; };
  for (const [key, id] of [['act', '#bkAct'], ['field', '#bkField'], ['value', '#bkValue'], ['scope', '#bkScope'], ['target', '#bkTarget'],
    ['cat', '#bkCat'], ['prop', '#bkProp'], ['gmode', '#bkGmode'], ['coref', '#bkCoref'], ['char', '#bkChar']]) {
    const v = get(id); if (v !== undefined) b[key] = v;
  }
  return b;
}
/* Redraw the bulk bar, keeping what was typed in it. */
function redrawBulk() { readBulk(); const bar = $('.bulk'); if (bar) bar.outerHTML = bulkBarHTML(); }

/* the bar */
function bulkBarHTML() {
  const f = S.find;
  if (!f.res || !f.res.total) return '';
  const b = bulkState(), fields = BULK_FIELDS[f.layer] || [], nt = f.checked.size;
  const sel = (id, label, list, cur) => `<select id="${id}" aria-label="${label}">${list.map(([v, l]) => `<option value="${v}" ${cur === v ? 'selected' : ''}>${l}</option>`).join('')}</select>`;
  const acts = [['set', 'Change a field'], ['create', 'Create from the results']].concat(SPAN_LAYERS.includes(f.layer) ? [['delete', 'Delete the results']] : []);
  if (!acts.some(([v]) => v === b.act)) b.act = 'set';
  let what = '';
  if (b.act === 'set') {
    const key = `${f.layer}.${b.field}`;
    const list = S.info.tagsets[key] ? `dl-${key.replace('.', '-')}` : (b.field === 'coref' || b.field === 'char_id') ? 'dl-clusters' : '';
    what = `<span>Set</span>${sel('bkField', 'Field to change', fields, b.field)}<span>to</span>
      <input id="bkValue" ${list ? `list="${list}"` : ''} value="${esc(b.value || '')}" placeholder="${b.field === 'char_id' ? 'speaker; empty = no one' : 'new value'}" aria-label="New value">`;
  } else if (b.act === 'create') {
    what = `<span>Make</span>${sel('bkTarget', 'What to create', BULK_TARGETS, b.target)}<span>of each result:</span>`;
    if (b.target === 'entities') {
      what += `<input id="bkCat" list="dl-entities-cat" value="${esc(b.cat)}" placeholder="type, e.g. VAR" aria-label="Entity type" style="width:110px">
        <input id="bkProp" list="dl-entities-prop" value="${esc(b.prop)}" placeholder="mention type" aria-label="Mention type" style="width:100px">
        ${sel('bkGmode', 'Coreference group', GROUP_MODES, b.gmode)}
        ${b.gmode === 'existing' ? `<input id="bkCoref" list="dl-clusters" value="${esc(b.coref)}" placeholder="group name or number" aria-label="Group">` : ''}`;
    } else if (b.target === 'supersenses') {
      what += `<input id="bkCat" list="dl-supersenses-cat" value="${esc(b.cat)}" placeholder="e.g. noun.person" aria-label="Supersense category">`;
    } else {
      what += `<input id="bkChar" list="dl-clusters" value="${esc(b.char)}" placeholder="speaker (optional)" aria-label="Speaker">`;
    }
  } else what = '<span class="muted">Deletes the selected results from the book.</span>';
  const total = f.res.total.toLocaleString();
  const scope = `<span>on</span><select id="bkScope" aria-label="Which results"><option value="all" ${b.scope !== 'ticked' ? 'selected' : ''}>all ${total} results</option>
    <option value="ticked" ${b.scope === 'ticked' ? 'selected' : ''} ${nt ? '' : 'disabled'}>${nt} ticked result${nt === 1 ? '' : 's'}</option></select>`;
  const go = { set: 'Apply', create: 'Create', delete: 'Delete' }[b.act];
  const saveOk = b.act === 'set' && f.layer !== 'quotes' && b.field !== 'coref' && ['eq', 'contains', 'regex'].includes(f.op) && f.field !== 'coref' && !f.where && !f.without;
  return `<div class="bulk">${sel('bkAct', 'What to do with the results', acts, b.act)}${what}${scope}
    <button class="btn ${b.act === 'delete' ? 'danger' : 'primary'} sm" data-bulkgo>${go}</button>
    ${saveOk ? '<button class="link" data-bulksave title="Save this search and change as a rule, offered again in the other books of the collection">Save as rule…</button>' : ''}
    ${b.act === 'create' && b.target === 'entities' ? '<p class="note bulkhint">Results that already have an entity with the same start and end are skipped. Pair with “Not yet an entity” above to be sure.</p>' : ''}</div>`;
}

/* a choice in the bar changed: the kind of action, the field or target, or the scope */
function onBulkChange(t) {
  const before = { ...bulkState() };
  const b = readBulk();
  if (t.id === 'bkField' && b.field !== before.field) b.value = '';
  if (t.id === 'bkAct' || t.id === 'bkField' || t.id === 'bkTarget' || t.id === 'bkGmode') redrawBulk();
}
/* a click inside the bar: Apply / Create / Delete, or Save as rule */
function onBulkClick(e) {
  if (e.target.closest('[data-bulkgo]')) runBulk().catch(fail);
  else if (e.target.closest('[data-bulksave]')) saveBulkRule(e.target.closest('[data-bulksave]')).catch(fail);
}

/* the search to repeat on the server, and which results (null = all) */
function bulkRequest() {
  const f = S.find, b = readBulk();
  return { n: b.scope === 'ticked' ? f.checked.size : f.res.total,
    body: { layer: f.layer, sfield: f.field, op: f.op, svalue: f.value, where: f.where || '', without: f.without || '',
      uids: b.scope === 'ticked' ? [...f.checked] : null } };
}

/* Apply / Create / Delete: ask when it touches more than 25 results, send it, then show the new state */
async function runBulk() {
  const f = S.find, b = readBulk(), { n, body } = bulkRequest();
  if (!n) throw new Error('Tick some results first');
  let what;
  if (b.act === 'set') {
    const value = (b.field === 'coref' || b.field === 'char_id') ? parseCluster(b.value.trim()) : b.value.trim();
    if (!(b.field === 'coref' || b.field === 'char_id') && !value) throw new Error('Type the new value first');
    Object.assign(body, { field: b.field, value });
    what = `Change ${b.field} on ${plural(n, 'result')}`;
  } else if (b.act === 'create') {
    const fields = {};
    if (b.target === 'entities') {
      if (!b.cat.trim()) throw new Error('Type the entity type first (for example VAR or LOC)');
      Object.assign(fields, { cat: b.cat.trim(), prop: b.prop.trim() || 'PROP', group_mode: b.gmode === 'existing' ? 'one' : b.gmode });
      if (b.gmode === 'existing') { fields.coref = parseCluster(b.coref.trim()); if (fields.coref == null) throw new Error('Choose the group'); }
    } else if (b.target === 'supersenses') {
      if (!b.cat.trim()) throw new Error('Type the supersense category first');
      fields.cat = b.cat.trim();
    } else if (b.char.trim()) fields.char_id = parseCluster(b.char.trim());
    Object.assign(body, { target: b.target, fields });
    what = `Create ${b.target} over ${plural(n, 'result')}`;
  } else {
    what = `Delete ${plural(n, 'result')}`;
  }
  if ((n > 25 || b.act === 'delete') && !confirm(`${what}? You can undo this in one step.`)) return;
  const r = b.act === 'set' ? await api('POST', '/api/bulk', body)
    : b.act === 'create' ? await api('POST', '/api/bulk/create', body)
    : await api('POST', '/api/bulk/delete', body);
  updateHistoryButtons(r.history);
  toast(b.act === 'create' && r.skipped ? `${r.history.undo} (${plural(r.skipped, 'result')} skipped: already there)` : r.history.undo);
  f.checked.clear();
  await refresh(true);
}

/* keep this search and change as a rule for the book's collection (or all books) */
async function saveBulkRule(btn) {
  const f = S.find, b = readBulk(), field = b.field, value = b.value.trim();
  if (!value) throw new Error('Type the new value first');
  const c = await api('GET', '/api/carry');
  if (!c.available) throw new Error('Open this book from the library to save rules');
  const rule = { layer: f.layer, sfield: f.field, op: f.op, svalue: f.value, field, value };
  const coll = c.collection;
  pop.innerHTML = `<div class="gp"><div class="gpt">Save as a rule</div>
    <p class="note" style="margin-top:0">Find ${esc(f.layer)} where ${esc(f.field)} ${esc(f.op === 'eq' ? 'is' : f.op === 'contains' ? 'contains' : 'matches')} “${esc(f.value)}”, then set ${esc(field)} to ${esc(value)}. Offered for review in each new book; nothing changes here.</p>
    <div class="row">${coll ? `<button class="btn primary sm" data-rs="${esc(coll.id)}">For ${esc(coll.name)}</button>` : ''}<button class="btn sm" data-rs="*">For all books</button><button class="btn sm" data-rs="">Cancel</button></div>
    ${coll ? '' : '<p class="note">This book isn’t in a collection; put it in one in the library to save rules for a series.</p>'}</div>`;
  pop.hidden = false;
  const r = btn.getBoundingClientRect();
  pop.style.left = Math.max(8, Math.min(innerWidth - 340, r.left)) + 'px'; pop.style.top = r.bottom + 6 + 'px';
  pop.onclick = async e => {
    const b = e.target.closest('[data-rs]'); if (!b) return;
    closePop();
    if (!b.dataset.rs) return;
    try { await api('POST', '/api/carry/rule', { list: b.dataset.rs, rule }); toast(`Rule saved for ${b.dataset.rs === '*' ? 'all books' : coll.name}`); } catch (err) { fail(err); }
  };
  pop.onkeydown = e => { if (e.key === 'Escape') closePop(); };
}
