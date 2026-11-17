import { $, esc, api, run, say, shell } from './common.js';
shell();
const clock = (b) => {
  if (b.boardReportedAt) return `<span class="mute">Reported to the Board ${esc(b.boardReportedAt.replace('T', ' ').slice(0, 19))} UTC</span>`;
  if (b.overdue) return `<span class="flag">Overdue — Board report was due ${esc(b.boardDueAt.replace('T', ' ').slice(0, 19))} UTC</span>`;
  return `<span>Board report due in ${esc(String(b.hoursRemaining))} hours <span class="mute">(by ${esc(b.boardDueAt.replace('T', ' ').slice(0, 19))} UTC)</span></span>`;
};
const card = (b) => `<div class="card"><strong>${esc(b.title)}</strong> <span class="badge">${esc(b.status)}</span>
  <p>${esc(b.description)}</p>
  <p class="mute">Discovered ${esc(b.discoveredAt.replace('T', ' ').slice(0, 19))} UTC · affected: ${b.affectedPersonIds.length ? b.affectedPersonIds.map(esc).join(', ') : 'none recorded'}</p>
  <p>${clock(b)}</p>
  <p class="mute">Affected people notified: ${b.notifiedAt ? esc(b.notifiedAt.replace('T', ' ').slice(0, 19)) + ' UTC' : 'not yet'}</p>
  <div class="row">
    ${b.status === 'Open' ? '<button class="btn" data-a="notify" data-id="' + esc(b.id) + '">Notify affected people</button>' : ''}
    ${b.status === 'Notified' ? '<button class="btn" data-a="report" data-id="' + esc(b.id) + '">Report to the Board</button>' : ''}
  </div></div>`;
async function refresh() {
  const list = await api('/api/breaches');
  $('#app').innerHTML = list.map(card).join('') || '<p class="mute">No breaches recorded.</p>';
}
$('#app').addEventListener('click', (e) => {
  const b = e.target.closest('[data-a]'); if (!b) return;
  run(async () => { await api(`/api/breaches/${b.dataset.id}/${b.dataset.a}`, 'POST'); await refresh(); });
});
$('#f').addEventListener('submit', (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  d.affectedPersonIds = d.affectedPersonIds.split(',').map((s) => s.trim()).filter(Boolean);
  run(async () => { await api('/api/breaches', 'POST', d); e.target.reset(); await refresh(); }, 'Breach recorded.');
});
refresh().catch((e) => say(e.message, true));
