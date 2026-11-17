import { $, esc, api, run, say, shell } from './common.js';
shell();
async function load() {
  const notes = await api('/api/notifications');
  $('#app').innerHTML = notes.map((n) => `<div class="card"><strong>${esc(n.kind.replace('_', ' '))}</strong> <span class="mute">${esc(n.createdAt.replace('T', ' ').slice(0, 19))} UTC${n.readAt ? ' · read' : ''}</span>
    <p>${esc(n.message)}</p>
    ${n.readAt ? '' : `<div class="row"><button class="btn" data-read="${esc(n.id)}">Mark read</button></div>`}</div>`).join('') || '<p class="mute">No notifications. A Needs attention visit, a late arrival or a missed visit will show up here.</p>';
  document.querySelectorAll('[data-read]').forEach((b) => b.addEventListener('click', () => run(async () => {
    await api(`/api/notifications/${b.dataset.read}/read`, 'POST');
    await load();
  }, 'Marked read.')));
}
$('#check-missed').addEventListener('click', () => run(async () => {
  const created = await api('/api/notifications/check-missed', 'POST');
  await load();
  say(created.length ? `${created.length} missed-visit notification(s) created.` : 'No missed visits found.');
}, undefined));
load().catch((e) => say(e.message, true));
