import { $, say } from './common.js';

// Already logged in: go straight to the dashboard.
if (sessionStorage.getItem('saathi_token')) location.href = '/';

$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target));
  try {
    const r = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || 'Log in failed.');
    sessionStorage.setItem('saathi_token', d.token);
    sessionStorage.setItem('saathi_user', JSON.stringify(d.user));
    location.href = '/';
  } catch (err) { say(err.message, true); }
});
