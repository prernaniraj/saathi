import { $, esc, api, getState, run, say, shell } from './common.js';
shell();
let S;
function render() {
  const matched = S.requests.filter((r) => r.status === 'Matched');
  const name = (id) => S.caregivers.find((c) => c.id === id)?.name ?? '';
  $('#form').innerHTML = matched.length ? `<form id="f"><label>Family<select name="requestId">${matched.map((r) => `<option value="${r.id}">${esc(r.parent)} with ${esc(name(r.caregiverId))}</option>`).join('')}</select></label>
    <label>How did it go?<select name="mood">${S.meta.moods.map((m) => `<option>${m}</option>`).join('')}</select></label>
    <textarea name="note" placeholder="What did you do together? Anything the family should know?" required></textarea><button class="btn">Save visit</button></form>`
    : '<p class="mute">No matched families yet. Match a certified caregiver on the Family requests page first.</p>';
  $('#app').innerHTML = S.visits.map((v) => {
    const r = S.requests.find((r) => r.id === v.requestId);
    return `<div class="card"><strong>${esc(r.parent)}</strong> <span class="mute">${v.date}, with ${esc(name(v.caregiverId))}</span> <span class="badge ${v.mood === 'Needs attention' ? 'flag' : ''}">${v.mood}</span><p>${esc(v.note)}</p></div>`;
  }).join('') || '<p class="mute">No visits logged yet.</p>';
}
const refresh = async () => { S = await getState(); render(); };
$('#form').addEventListener('submit', (e) => {
  e.preventDefault();
  run(async () => { await api('/api/visits', 'POST', Object.fromEntries(new FormData(e.target))); await refresh(); }, 'Visit saved and shared with the family.');
});
refresh().catch((e) => say(e.message, true));
