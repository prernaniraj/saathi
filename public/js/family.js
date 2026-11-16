import { $, esc, getState, say, shell } from './common.js';
shell();
const S = await getState().catch((e) => { say(e.message, true); });
if (S) {
  const matched = S.requests.filter((r) => r.status === 'Matched');
  const sel = $('#who');
  sel.innerHTML = matched.map((r) => `<option value="${r.id}">${esc(r.family)} (${esc(r.parent)})</option>`).join('');
  const pick = new URLSearchParams(location.search).get('r');
  if (matched.some((r) => r.id === pick)) sel.value = pick;
  const render = () => {
    const r = matched.find((r) => r.id === sel.value);
    if (!r) { $('#app').innerHTML = '<p class="mute">No matched families yet.</p>'; return; }
    const c = S.caregivers.find((c) => c.id === r.caregiverId);
    const visits = S.visits.filter((v) => v.requestId === r.id);
    $('#app').innerHTML = `<div class="card"><p><strong>${esc(c.name)}</strong> visits ${esc(r.parent)} in ${esc(r.city)}, ${r.visitsPerWeek} times a week.</p></div>` +
      (visits.map((v) => `<div class="card"><span class="mute">${v.date}</span> <span class="badge ${v.mood === 'Needs attention' ? 'flag' : ''}">${v.mood}</span><p>${esc(v.note)}</p></div>`).join('') || '<p class="mute">No visits yet.</p>');
  };
  sel.addEventListener('change', () => { history.replaceState(null, '', '?r=' + sel.value); render(); });
  render();
}
