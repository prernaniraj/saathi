# Saathi

Proof of concept for a care-leaver-to-caregiver pipeline. Young adults leaving orphanages are trained as eldercare companions, certified, matched to families abroad who pay for visits, and log visit updates the family can read.

No dependencies. Needs Node 20 or newer.

```
npm start        # http://localhost:3000  (PORT=4000 npm start to change it)
npm test         # API rule tests
npm run dev      # restart on file changes
```

Data lives in `data/db.json` (created on first run, ignored by git). Delete it to restore the sample data, or `POST /api/reset`.

## Pages

| Page | Purpose |
| --- | --- |
| `/` | Dashboard: stage counts, flagged visits, waiting matches |
| `/caregivers.html` | Add care-leavers, run training modules, certify |
| `/requests.html` | Family requests, match a certified caregiver (same city first) |
| `/visits.html` | Log a visit and see all updates |
| `/family.html?r=r1` | What one family sees: their caregiver and visit updates |

## Rules enforced by the API (`src/api.js`)

- Training starts only from Applied; modules change only during training.
- Certify needs all five modules.
- Match needs a certified caregiver and an open request.
- Visits need a matched request, a valid mood and a note.

## Layout

```
server.js        static files + JSON API, no framework
src/domain.js    stages, modules, moods
src/api.js       routes and rules (pure function, easy to test)
src/store.js     JSON-file store (in-memory when no file given)
src/seed.js      fictional sample data
public/          multi-page front end (plain HTML + ES modules)
test/            node:test suite
```

## Not built yet

Logins and roles, notifications to families, caregiver pay and background checks, a real database. The store and API are separated so a database can replace `src/store.js`.
