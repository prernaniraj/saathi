import fs from 'node:fs';
import path from 'node:path';
import { seed } from './seed.js';

// JSON-file store. Pass no file for an in-memory store (used by tests).
export function createStore(file = null) {
  let db = null;
  if (file && fs.existsSync(file)) {
    try { db = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { /* fall back to seed */ }
  }
  db ??= seed();
  const store = {
    get db() { return db; },
    save() {
      if (!file) return;
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, JSON.stringify(db, null, 2));
    },
    reset() { db = seed(); store.save(); },
    nextId(prefix) { return prefix + db.seq++; }
  };
  store.save();
  return store;
}
