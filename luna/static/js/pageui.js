'use strict';
/* Small helpers shared by the editor's separate pages (library.html, settings.html): talking to the server, toasts,
   the generic modal dialog, and the folder/file browser. The annotation page (index.html) has its own copy of the
   first few in app.js, since it doesn't load this file (it never needed a second page's worth of shared code until
   settings.html came along). Both library.html and settings.html load this before their own script. */

/* Short for querySelector. */
const $ = (s, r = document) => r.querySelector(s);
/* HTML-escape a value for markup. */
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
/* Call the server (JSON in and out); throws the server's message as an Error. */
async function api(method, url, body) {
  const r = await fetch(url, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  let j = null;
  try { j = await r.json(); } catch { }
  if (!r.ok) throw new Error((j && j.detail) || `${r.status} ${r.statusText}`);
  return j;
}
/* Show a short message. */
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('on');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('on'), 3500);
}
/* Show an error as a toast. */
const fail = e => toast(e.message || String(e));

/* ------------------------------------------------------------ modal */
/* Show a dialog with the given HTML (the page must have a hidden #modal > .mbox). */
function modal(html) {
  const m = $('#modal');
  $('.mbox', m).innerHTML = html;
  m.hidden = false;
  const f = m.querySelector('[autofocus]') || m.querySelector('input, button');
  if (f) f.focus();
}
/* Close the dialog. */
function closeModal() { const m = $('#modal'); m.hidden = true; const b = $('.mbox', m); b.innerHTML = ''; b.className = 'mbox'; b.onchange = null; m.onclick = null; }
document.addEventListener('DOMContentLoaded', () => {
  const m = $('#modal');
  if (!m) return;
  m.addEventListener('mousedown', e => { if (e.target.id === 'modal') closeModal(); });
});
document.addEventListener('keydown', e => { if (e.key === 'Escape' && $('#modal') && !$('#modal').hidden) closeModal(); });

/* folder and file browser: the page can't see paths on the Mac, so the server lists them */
function browse({ title, want, start, onPick, onCancel }) {
  let cur = null;
  const draw = async path => {
    let d;
    try { d = await api('GET', `/api/fs?${new URLSearchParams({ path: path || '', want })}`); }
    catch (e) { fail(e); return; }
    cur = d;
    const note = want === 'dir'
      ? (d.books_here ? `${d.books_here} book${d.books_here === 1 ? '' : 's'} directly in this folder` : 'Books in the folders directly inside this one are found too')
      : 'Click the .txt file you gave BookNLP';
    modal(`<header><h2 id="mtitle">${esc(title)}</h2></header>
      <div class="mbody">
        <div class="fpath"><button class="btn sm" data-f="up" ${d.parent ? '' : 'disabled'} title="Up one folder" aria-label="Up one folder">↑</button>
          <input id="fpath" value="${esc(d.path)}" aria-label="Folder path" spellcheck="false">
          <button class="btn sm" data-f="go">Go</button><button class="btn sm" data-f="home">Home</button></div>
        <ul class="flist">${d.dirs.map(n => `<li tabindex="0" data-dir="${esc(n)}"><span class="ic">▸</span>${esc(n)}</li>`).join('')}
          ${d.files.map(n => `<li tabindex="0" class="file" data-file="${esc(n)}"><span class="ic">≡</span>${esc(n)}</li>`).join('')}
          ${!d.dirs.length && !d.files.length ? '<li class="muted" style="cursor:default">Nothing here</li>' : ''}</ul>
      </div>
      <footer><span class="grow">${note}</span><button class="btn" data-f="cancel">Cancel</button>
        ${want === 'dir' ? '<button class="btn primary" data-f="use">Use this folder</button>' : ''}</footer>`);
    $('#fpath').addEventListener('keydown', e => { if (e.key === 'Enter') draw($('#fpath').value); });
    $('.flist').addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.matches('li[tabindex]')) e.target.click(); });
  };
  $('#modal').onclick = e => {
    const t = e.target.closest('[data-f], [data-dir], [data-file]'); if (!t || !cur) return;
    if (t.dataset.dir != null) draw(cur.path.replace(/\/$/, '') + '/' + t.dataset.dir);
    else if (t.dataset.file != null) onPick(cur.path.replace(/\/$/, '') + '/' + t.dataset.file);
    else if (t.dataset.f === 'up') draw(cur.parent);
    else if (t.dataset.f === 'go') draw($('#fpath').value);
    else if (t.dataset.f === 'home') draw(cur.home);
    else if (t.dataset.f === 'cancel') { if (onCancel) onCancel(); else closeModal(); }
    else if (t.dataset.f === 'use') onPick(cur.path);
  };
  draw(start);
}
