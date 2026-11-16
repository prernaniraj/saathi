import { STAGES, NOTES, MODULES, MOODS } from './domain.js';

class HttpError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
const bad = (m) => new HttpError(400, m);
const notFound = (m) => new HttpError(404, m);
const conflict = (m) => new HttpError(409, m);
const res = (data, status = 200) => ({ status, data });

const text = (v, name) => {
  if (typeof v !== 'string' || !v.trim()) throw bad(`${name} is required.`);
  return v.trim().slice(0, 200);
};

// Pure request handler: (store, method, path, body) -> { status, data }. Throws HttpError.
export function handle(store, method, pathname, body = {}) {
  const db = store.db;
  const find = (list, id, label) => {
    const item = db[list].find((x) => x.id === id);
    if (!item) throw notFound(`${label} not found.`);
    return item;
  };
  let m;

  if (method === 'GET' && pathname === '/api/state') {
    return res({ meta: { stages: STAGES, notes: NOTES, modules: MODULES, moods: MOODS }, ...db });
  }

  if (method === 'POST' && pathname === '/api/caregivers') {
    const c = { id: store.nextId('c'), name: text(body.name, 'Name'), city: text(body.city, 'City'), stage: 'Applied', modules: MODULES.map(() => 0) };
    db.caregivers.push(c); store.save();
    return res(c, 201);
  }

  if (method === 'POST' && (m = pathname.match(/^\/api\/caregivers\/(\w+)\/(start|certify)$/))) {
    const c = find('caregivers', m[1], 'Care-leaver');
    if (m[2] === 'start') {
      if (c.stage !== 'Applied') throw conflict('Training can only start from the Applied stage.');
      c.stage = 'Training';
    } else {
      if (c.stage !== 'Training') throw conflict('Only care-leavers in training can be certified.');
      if (!c.modules.every(Boolean)) throw conflict('Complete all five modules before certifying.');
      c.stage = 'Certified';
    }
    store.save();
    return res(c);
  }

  if (method === 'POST' && (m = pathname.match(/^\/api\/caregivers\/(\w+)\/modules\/(\d+)$/))) {
    const c = find('caregivers', m[1], 'Care-leaver');
    const i = Number(m[2]);
    if (i >= MODULES.length) throw bad('Unknown module.');
    if (c.stage !== 'Training') throw conflict('Modules can only be updated during training.');
    c.modules[i] = c.modules[i] ? 0 : 1; store.save();
    return res(c);
  }

  if (method === 'POST' && pathname === '/api/requests') {
    const visitsPerWeek = Number(body.visitsPerWeek);
    if (!Number.isInteger(visitsPerWeek) || visitsPerWeek < 1 || visitsPerWeek > 7) throw bad('Visits per week must be 1 to 7.');
    const r = { id: store.nextId('r'), family: text(body.family, 'Family name'), parent: text(body.parent, 'Parent’s name'), city: text(body.city, 'City'), visitsPerWeek, need: text(body.need, 'Need'), status: 'Open', caregiverId: null };
    db.requests.push(r); store.save();
    return res(r, 201);
  }

  if (method === 'POST' && (m = pathname.match(/^\/api\/requests\/(\w+)\/match$/))) {
    const r = find('requests', m[1], 'Request');
    const c = find('caregivers', body.caregiverId, 'Caregiver');
    if (r.status !== 'Open') throw conflict('This request already has a caregiver.');
    if (c.stage !== 'Certified') throw conflict('Only certified caregivers can be matched.');
    r.status = 'Matched'; r.caregiverId = c.id; c.stage = 'Matched'; store.save();
    return res(r);
  }

  if (method === 'POST' && pathname === '/api/visits') {
    const r = find('requests', body.requestId, 'Request');
    if (r.status !== 'Matched') throw conflict('Visits can only be logged for matched requests.');
    if (!MOODS.includes(body.mood)) throw bad('Choose how the visit went.');
    const v = { id: store.nextId('v'), requestId: r.id, caregiverId: r.caregiverId, date: new Date().toISOString().slice(0, 10), mood: body.mood, note: text(body.note, 'Note') };
    db.visits.unshift(v); store.save();
    return res(v, 201);
  }

  if (method === 'POST' && pathname === '/api/reset') { store.reset(); return res({ ok: true }); }

  throw notFound('Not found.');
}
