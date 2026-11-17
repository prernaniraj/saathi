import { $, esc, api, run, say, shell } from './common.js';
shell();
async function load() {
  const events = await api('/api/audit?limit=100');
  $('#app').innerHTML = events.map((e) => `<div class="card"><strong>${esc(e.action)}</strong> <span class="mute">#${e.seq}, ${esc(e.ts.replace('T', ' ').slice(0, 19))} UTC, by ${esc(e.actor)}</span>
    <p class="mute">${esc(e.entity_type)} ${esc(e.entity_id)}. Requirement ${e.spec_refs.map(esc).join(', ')}. Request ${esc(e.request_id)}</p>
    <details><summary class="mute">Before and after</summary><pre class="mute">${esc(JSON.stringify({ before: e.before, after: e.after }, null, 2))}</pre></details></div>`).join('') || '<p class="mute">No changes recorded yet. Add a care-leaver or start training to create one.</p>';
}
$('#verify').addEventListener('click', () => run(async () => {
  const r = await api('/api/audit/verify');
  if (r.ok) say(`Chain intact: ${r.events} events.`); else say(`Chain broken at event ${r.broken_at}.`, true);
}));
load().catch((e) => say(e.message, true));
