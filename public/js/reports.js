import { $, esc, api, say, shell } from './common.js';
shell();
const pct = (x) => (x === null || x === undefined ? '—' : `${Math.round(x * 100)}%`);
async function load() {
  const s = await api('/api/reports/summary');
  $('#app').innerHTML = `
  <div class="card"><strong>Visit completion</strong> <span class="mute">as of ${esc(s.asOf)}</span>
    <p>${s.visits.logged} visit(s) logged vs ${s.visits.expected} expected — ${pct(s.visits.completionRate)} complete.</p></div>
  <div class="card"><strong>Punctuality</strong>
    <p>${s.punctuality.withCheckInAndCheckOut} of ${s.punctuality.total} visit(s) have both a check-in and a check-out (${pct(s.punctuality.checkInOutRate)}). ${s.punctuality.late} of ${s.punctuality.checkedIn} checked-in visit(s) were late (${pct(s.punctuality.lateRate)}).</p></div>
  <div class="card"><strong>Flagged visits</strong>
    <p>${s.flaggedVisits} visit(s) flagged "Needs attention".</p></div>
  <div class="card"><strong>Retention</strong>
    <p>${s.retention.currentlyMatched} of ${s.retention.everMatched} caregiver(s) ever matched are still matched (${pct(s.retention.rate)}).</p></div>
  <div class="card"><strong>Placement rate</strong>
    <p>${s.placement.matchedRequests} of ${s.placement.totalRequests} request(s) matched (${pct(s.placement.rate)}).</p></div>`;
}
load().catch((e) => say(e.message, true));
