import { $, esc, getState, shell, plural } from './common.js';
shell();
const s = await getState();
const count = (stage) => s.caregivers.filter((c) => c.stage === stage).length;
const latest = (r) => s.visits.find((v) => v.requestId === r.id);
const flagged = s.requests.filter((r) => latest(r)?.mood === 'Needs attention');
const open = s.requests.filter((r) => r.status === 'Open');
const ready = s.caregivers.filter((c) => c.stage === 'Certified' && s.meta.verification.every((v) => c.verification?.[v.id]?.status === 'Verified')).length;
const started = s.caregivers.length > 0;

$('#app').innerHTML = `
<section class="hero" aria-labelledby="welcome">
  <h2 id="welcome">${started ? 'Here is what needs you today' : 'Welcome to Saathi'}</h2>
  <p>${started
    ? 'Three steps run the pipeline: train, match, and keep the family informed. Start where the numbers ask for you.'
    : 'Saathi trains young adults leaving care as eldercare companions, matches them to families abroad, and keeps those families informed after every visit. Add a first care-leaver to begin.'}</p>
  <ol class="hero-steps">
    <li class="step"><span class="step-num" aria-hidden="true">1</span>
      <div><strong>Train</strong><p>${plural(count('Training'), 'care-leaver')} in training, five modules each.</p>
      <a href="/caregivers.html">Open the pipeline</a></div></li>
    <li class="step"><span class="step-num" aria-hidden="true">2</span>
      <div><strong>Verify and match</strong><p>${ready} certified and cleared. ${plural(open.length, 'family request')} open.</p>
      <a href="/requests.html">Match a companion</a></div></li>
    <li class="step"><span class="step-num" aria-hidden="true">3</span>
      <div><strong>Log visits</strong><p>${flagged.length ? `${plural(flagged.length, 'visit')} need attention.` : 'Every visit so far is on track.'}</p>
      <a href="/visits.html">Open the visit log</a></div></li>
  </ol>
</section>

<ol class="path" aria-label="Pipeline counts">${s.meta.stages.map((st) => `<li><b>${count(st)}</b><span>${esc(st)}</span></li>`).join('')}</ol>

<div class="cols">
 <div class="card"><h3>Needs attention</h3>${flagged.length
    ? flagged.map((r) => `<p class="flag">${esc(r.parent)}: ${esc(latest(r).note)}</p>`).join('') + '<p><a href="/visits.html">Open the visit log</a></p>'
    : '<p class="empty">Nothing to review. No visit has been flagged.</p>'}</div>
 <div class="card"><h3>Waiting for a match</h3>
   <p class="stat">${open.length}</p>
   <p class="mute">${plural(open.length, 'open request')}. ${ready} certified and cleared, ${count('Certified') - ready} still waiting on checks.</p>
   <p><a href="/requests.html">Match caregivers</a></p></div>
 <div class="card"><h3>In training</h3>
   <p class="stat">${count('Training')}</p>
   <p class="mute">${plural(count('Training'), 'care-leaver')} working through five modules.</p>
   <p><a href="/caregivers.html">Open the pipeline</a></p></div>
</div>`;
