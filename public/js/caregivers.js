import { $, esc, api, getState, run, say, shell } from './common.js';
shell();
let S;
const NEXT = { Pending: 'Verified', Verified: 'Failed', Failed: 'Pending' };
const cleared = (c) => S.meta.verification.every((v) => c.verification?.[v.id]?.status === 'Verified');
const checks = (c) => `<p class="mute">Checks (tap to change)</p><div class="chips">${S.meta.verification.map((v) => {
  const st = c.verification?.[v.id]?.status ?? 'Pending';
  return `<button class="chip ${st === 'Failed' ? 'bad' : ''}" aria-pressed="${st === 'Verified'}" data-a="verify" data-id="${c.id}" data-item="${v.id}" data-status="${NEXT[st]}">${esc(v.label)}: ${st}</button>`;
}).join('')}</div>`;
const detail = (c) => {
  const done = c.modules.filter(Boolean).length;
  if (c.stage === 'Applied') return `<button class="btn" data-a="start" data-id="${c.id}">Start training</button>${checks(c)}`;
  if (c.stage === 'Training') return `<p class="mute">${done} of ${S.meta.modules.length} modules complete</p>
    <div class="chips">${S.meta.modules.map((m, i) => `<button class="chip" aria-pressed="${!!c.modules[i]}" data-a="mod" data-id="${c.id}" data-i="${i}">${m}</button>`).join('')}</div>
    <button class="btn" data-a="certify" data-id="${c.id}" ${done < S.meta.modules.length ? 'disabled' : ''}>Certify</button>${checks(c)}`;
  if (c.stage === 'Certified') return `<p class="mute">${cleared(c) ? 'Certified and cleared. Ready to match on the Family requests page.' : 'Certified. Finish the checks before matching.'}</p>${checks(c)}`;
  const r = S.requests.find((r) => r.caregiverId === c.id);
  return r ? `<p>Visits ${esc(r.parent)} for the ${esc(r.family)}.</p>` : '';
};
function render() {
  $('#app').innerHTML = `<div class="cols">${S.meta.stages.map((st, i) => `<div><h3>${st}</h3><p class="mute">${S.meta.notes[i]}</p>${
    S.caregivers.filter((c) => c.stage === st).map((c) => `<div class="card"><strong>${esc(c.name)}</strong> <span class="mute">${esc(c.city)}</span>${detail(c)}</div>`).join('') || '<p class="mute">No one here yet.</p>'}</div>`).join('')}</div>`;
}
const refresh = async () => { S = await getState(); render(); };
$('#app').addEventListener('click', (e) => {
  const b = e.target.closest('[data-a]'); if (!b) return;
  const { a, id, i, item, status } = b.dataset;
  run(async () => {
    if (a === 'verify') await api(`/api/caregivers/${id}/verification/${item}`, 'POST', { status });
    else await api(`/api/caregivers/${id}/${a === 'mod' ? 'modules/' + i : a}`, 'POST');
    await refresh();
  });
});
$('#f').addEventListener('submit', (e) => {
  e.preventDefault();
  run(async () => { await api('/api/caregivers', 'POST', Object.fromEntries(new FormData(e.target))); e.target.reset(); await refresh(); }, 'Care-leaver added.');
});
refresh().catch((e) => say(e.message, true));
