import { $, esc, getState, shell, plural } from './common.js';
shell();
const s = await getState();
const count = (stage) => s.caregivers.filter((c) => c.stage === stage).length;
const latest = (r) => s.visits.find((v) => v.requestId === r.id);
const flagged = s.requests.filter((r) => latest(r)?.mood === 'Needs attention');
const open = s.requests.filter((r) => r.status === 'Open');
$('#app').innerHTML = `
<ol class="path" aria-label="Pipeline counts">${s.meta.stages.map((st) => `<li><b>${count(st)}</b><span>${st}</span></li>`).join('')}</ol>
<div class="cols">
 <div class="card"><h3>Needs attention</h3>${flagged.length ? flagged.map((r) => `<p class="flag">${esc(r.parent)}: ${esc(latest(r).note)}</p>`).join('') + '<p><a href="/visits.html">Open the visit log</a></p>' : '<p class="mute">No flagged visits.</p>'}</div>
 <div class="card"><h3>Waiting for a match</h3><p>${plural(open.length, 'open request')} and ${plural(count('Certified'), 'certified caregiver')}.</p><p><a href="/requests.html">Match caregivers</a></p></div>
 <div class="card"><h3>In training</h3><p>${plural(count('Training'), 'care-leaver')} working through five modules.</p><p><a href="/caregivers.html">Open the pipeline</a></p></div>
</div>`;
