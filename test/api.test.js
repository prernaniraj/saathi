import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createStore } from '../src/store.js';
import { handle } from '../src/api.js';

const call = (store, method, path, body) => handle(store, method, path, body);
const fails = (fn, status) => assert.throws(fn, (e) => e.status === status);

test('certify is blocked until all five modules are done', () => {
  const s = createStore();
  fails(() => call(s, 'POST', '/api/caregivers/c2/certify'), 409);
  for (const i of [2, 3, 4]) call(s, 'POST', `/api/caregivers/c2/modules/${i}`);
  assert.equal(call(s, 'POST', '/api/caregivers/c2/certify').data.stage, 'Certified');
});

test('new care-leaver starts in Applied and needs a name and city', () => {
  const s = createStore();
  assert.equal(call(s, 'POST', '/api/caregivers', { name: ' Divya ', city: 'Pune' }).data.stage, 'Applied');
  fails(() => call(s, 'POST', '/api/caregivers', { name: '', city: 'Pune' }), 400);
});

test('matching needs a certified caregiver and an open request', () => {
  const s = createStore();
  fails(() => call(s, 'POST', '/api/requests/r2/match', { caregiverId: 'c2' }), 409);
  call(s, 'POST', '/api/requests/r2/match', { caregiverId: 'c5' });
  assert.equal(s.db.caregivers.find((c) => c.id === 'c5').stage, 'Matched');
  fails(() => call(s, 'POST', '/api/requests/r2/match', { caregiverId: 'c4' }), 409);
});

test('visits need a matched request, a valid mood and a note', () => {
  const s = createStore();
  fails(() => call(s, 'POST', '/api/visits', { requestId: 'r2', mood: 'Good', note: 'x' }), 409);
  fails(() => call(s, 'POST', '/api/visits', { requestId: 'r1', mood: 'Great', note: 'x' }), 400);
  fails(() => call(s, 'POST', '/api/visits', { requestId: 'r1', mood: 'Good', note: ' ' }), 400);
  const v = call(s, 'POST', '/api/visits', { requestId: 'r1', mood: 'Needs attention', note: 'Missed medication.' }).data;
  assert.equal(v.caregiverId, 'c6');
  assert.equal(s.db.visits[0].id, v.id);
});

test('request validation and unknown routes', () => {
  const s = createStore();
  fails(() => call(s, 'POST', '/api/requests', { family: 'A', parent: 'B', city: 'C', visitsPerWeek: 9, need: 'Companionship' }), 400);
  fails(() => call(s, 'GET', '/api/nope'), 404);
});
