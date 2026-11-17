import { $, esc, api, getState, run, say, shell } from './common.js';
shell();
let S;
const cleared = (c) => S.meta.verification.every((v) => c.verification?.[v.id]?.status === 'Verified');
function render() {
  const certified = S.caregivers.filter((c) => c.stage === 'Certified');
  const cert = certified.filter(cleared);
  const waiting = certified.length - cert.length;
  $('#app').innerHTML = S.requests.map((r) => {
    let act;
    if (r.status === 'Matched') { const c = S.caregivers.find((c) => c.id === r.caregiverId); act = `<p>Matched with <strong>${esc(c.name)}</strong> from ${esc(c.city)}.</p>`; }
    else if (!cert.length) act = `<p class="mute">${waiting ? waiting + ' certified caregiver' + (waiting === 1 ? ' still needs' : 's still need') + ' identity, background and reference checks. Finish them on the Pipeline page.' : 'No certified caregivers yet. Finish training on the Pipeline page first.'}</p>`;
    else {
      const sorted = [...cert].sort((a, b) => (b.city === r.city) - (a.city === r.city));
      act = `<div class="row"><label>Caregiver<select id="sel-${r.id}">${sorted.map((c) => `<option value="${c.id}">${esc(c.name)}, ${esc(c.city)}${c.city === r.city ? ' (same city)' : ''}</option>`).join('')}</select></label>
        <button class="btn" data-id="${r.id}">Match caregiver</button></div>`;
    }
    return `<div class="card"><strong>${esc(r.parent)}</strong>, ${esc(r.city)} <span class="badge">${r.status}</span>
      <p class="mute">${esc(r.family)}. ${esc(r.need)}, ${r.visitsPerWeek} visits a week.</p>${act}</div>`;
  }).join('') || '<p class="mute">No requests yet. Submit one above.</p>';
}
const refresh = async () => { S = await getState(); render(); };
$('#app').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-id]'); if (!b) return;
  run(async () => { await api(`/api/requests/${b.dataset.id}/match`, 'POST', { caregiverId: $('#sel-' + b.dataset.id).value }); await refresh(); }, 'Caregiver matched.');
});
$('#f').addEventListener('submit', (e) => {
  e.preventDefault();
  run(async () => { await api('/api/requests', 'POST', Object.fromEntries(new FormData(e.target))); e.target.reset(); await refresh(); }, 'Request submitted.');
});
refresh().catch((e) => say(e.message, true));
