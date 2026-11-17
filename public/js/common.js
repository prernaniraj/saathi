export const $ = (s) => document.querySelector(s);
export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

export async function api(url, method = 'GET', body) {
  const headers = { 'Content-Type': 'application/json' };
  const token = sessionStorage.getItem('saathi_token');
  if (token) headers.Authorization = `Bearer ${token}`;
  const r = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (r.status === 401) {
    sessionStorage.removeItem('saathi_token');
    sessionStorage.removeItem('saathi_user');
    if (!location.pathname.endsWith('/login.html')) location.href = '/login.html';
    throw new Error('Your login has expired. Log in again.');
  }
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
  const user = JSON.parse(sessionStorage.getItem('saathi_user') || 'null');
  $('#shell').innerHTML = `<header><h1>Saathi</h1><p>Train young adults leaving orphanages as eldercare companions, then match them to families paying from abroad.</p></header>
  <nav>${PAGES.map(([p, l]) => `<a href="/${p}" ${p === here ? 'aria-current="page"' : ''}>${l}</a>`).join('')}</nav>
  <p class="mute">${user ? `Signed in as ${esc(user.email)} (${esc(user.role)}) · <a href="#" id="logout-link">Log out</a>` : '<a href="/login.html">Log in</a>'}</p>`;
  const out = $('#logout-link');
  if (out) out.addEventListener('click', async (e) => {
    e.preventDefault();
    try { await api('/api/auth/logout', 'POST'); } catch { /* still log out locally */ }
    sessionStorage.removeItem('saathi_token');
    sessionStorage.removeItem('saathi_user');
    location.href = '/login.html';
  });
}
