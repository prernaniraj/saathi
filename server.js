import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createStore } from './src/store.js';
import { handle } from './src/api.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, 'public');
const port = process.env.PORT ?? 3000;
const store = createStore(path.join(here, 'data', 'db.json'));
const types = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml' };
const send = (res, status, body, type) => { res.writeHead(status, { 'Content-Type': type }); res.end(body); };

http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname.startsWith('/api/')) {
      let raw = '';
      for await (const chunk of req) {
        raw += chunk;
        if (raw.length > 1e5) throw Object.assign(new Error('Request too large.'), { status: 413 });
      }
      const { status, data } = handle(store, req.method, url.pathname, raw ? JSON.parse(raw) : {});
      return send(res, status, JSON.stringify(data), 'application/json');
    }
    const file = path.join(root, url.pathname === '/' ? 'index.html' : url.pathname);
    if (!file.startsWith(root)) return send(res, 403, 'Forbidden', 'text/plain');
    send(res, 200, await fs.readFile(file), types[path.extname(file)] ?? 'application/octet-stream');
  } catch (e) {
    const status = e.code === 'ENOENT' || e.code === 'EISDIR' ? 404 : e instanceof SyntaxError ? 400 : e.status ?? 500;
    if (status === 500) console.error(e);
    send(res, status, JSON.stringify({ error: status === 500 ? 'Server error.' : status === 404 ? 'Not found.' : e.message }), 'application/json');
  }
}).listen(port, () => console.log(`Saathi running at http://localhost:${port}`));
