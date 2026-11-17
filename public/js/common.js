export const $ = (s) => document.querySelector(s);
export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

export async function api(url, method = 'GET', body) {
  const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || 'Something went wrong. Try again.');
  return d;
}
export const getState = () => api('/api/state');

export function say(text, isError = false) {
  const el = $('#live');
  el.textContent = text;
  el.className = isError ? 'flag' : 'mute';
}
// Runs an async action, shows a success message, or the error message if it fails.
export async function run(fn, okMessage) {
  try { await fn(); if (okMessage) say(okMessage); } catch (e) { say(e.message, true); }
}

const PAGES = [['', 'Dashboard'], ['caregivers.html', 'Pipeline'], ['requests.html', 'Family requests'], ['visits.html', 'Visit log'], ['family.html', 'Family view'], ['audit.html', 'Audit']];
export function shell() {
  const here = location.pathname.split('/').pop();
  $('#shell').innerHTML = `<header><h1>Saathi</h1><p>Train young adults leaving orphanages as eldercare companions, then match them to families paying from abroad.</p></header>
  <nav>${PAGES.map(([p, l]) => `<a href="/${p}" ${p === here ? 'aria-current="page"' : ''}>${l}</a>`).join('')}</nav>`;
}
