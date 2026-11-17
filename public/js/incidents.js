import { $, esc, api, getState, run, say, shell } from './common.js';
shell();
const user = JSON.parse(sessionStorage.getItem('saathi_user') || 'null');
const isCoordinator = user?.role === 'coordinator';
let S;
const requestLabel = (id) => {
  const r = S.requests.find((r) => r.id === id);
  return r ? `${esc(r.parent)} — ${esc(r.family)}` : esc(id);
};
const caregiverName = (id) => {
  const c = S.caregivers.find((c) => c.id === id);
  return c ? esc(c.name) : '';
};
function card(i) {
  const open = i.status !== 'Resolved';
  const actions = isCoordinator && open ? `<div class="row">
      <label>Owner (coordinator email)<input name="owner" value="${esc(i.owner || '')}" placeholder="coordinator@saathi.local"></label>
      <button class="btn" data-a="assign" data-id="${i.id}">Assign</button>
    </div>
    <div class="row">
      <label>Resolution<input name="resolution" placeholder="How was it resolved?"></label>
      <button class="btn" data-a="resolve" data-id="${i.id}">Resolve</button>
    </div>` : '';
  return `<div class="card"><strong>${esc(i.severity)} incident</strong> <span class="badge">${esc(i.status)}</span>
    <p>${esc(i.description)}</p>
    <p class="mute">${requestLabel(i.requestId)}${i.caregiverId ? ` · caregiver ${caregiverName(i.caregiverId)}` : ''} · opened ${esc(i.createdAt.replace('T', ' ').slice(0, 19))} UTC</p>
    <p class="mute">Owner: ${i.owner ? esc(i.owner) : 'not assigned yet'}</p>
    ${i.resolution ? `<p>Resolution: ${esc(i.resolution)} <span class="mute">(${esc((i.resolvedAt || '').replace('T', ' ').slice(0, 19))} UTC)</span></p>` : ''}
    ${actions}</div>`;
}
async function refresh() {
  S = await getState();
  const list = await api('/api/incidents');
  $('#app').innerHTML = list.map(card).join('') || '<p class="mute">No incidents. A “Needs attention” visit opens one automatically.</p>';
  if (isCoordinator) {
    $('#f').hidden = false;
    $('#req').innerHTML = S.requests.map((r) => `<option value="${esc(r.id)}">${esc(r.parent)} — ${esc(r.family)}</option>`).join('');
  }
}
$('#app').addEventListener('click', (e) => {
  const b = e.target.closest('[data-a]'); if (!b) return;
  const box = b.closest('.card');
  run(async () => {
    if (b.dataset.a === 'assign') await api(`/api/incidents/${b.dataset.id}/assign`, 'POST', { owner: box.querySelector('[name=owner]').value });
    else await api(`/api/incidents/${b.dataset.id}/resolve`, 'POST', { resolution: box.querySelector('[name=resolution]').value });
    await refresh();
  });
});
$('#f').addEventListener('submit', (e) => {
  e.preventDefault();
  run(async () => { await api('/api/incidents', 'POST', Object.fromEntries(new FormData(e.target))); e.target.reset(); await refresh(); }, 'Incident reported.');
});
refresh().catch((e) => say(e.message, true));
