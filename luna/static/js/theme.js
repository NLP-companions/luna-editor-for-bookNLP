'use strict';
/* Applies a saved light/dark override before the page paints, so there's no flash of the wrong theme.
   Loaded first (in <head>, before the stylesheet) on every page. Setting is 'bne.theme' in localStorage:
   'light' or 'dark' forces that theme; anything else (including unset) follows the OS (see app.css). */
try {
  const v = localStorage.getItem('bne.theme');
  if (v === 'light' || v === 'dark') document.documentElement.dataset.theme = v;
} catch { }
