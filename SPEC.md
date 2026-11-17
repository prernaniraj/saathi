# Saathi specification

Proof of concept for a care-leaver-to-caregiver pipeline. Young adults leaving child care institutions are trained and certified as eldercare companions, matched to families who pay from abroad, and log visit updates the family can read.

Version 0.4. Backend: Python 3.11+ and FastAPI. Front end: plain HTML and ES modules in `public/`.

## 1. Change rule

Nothing changes without a requirement ID. A change is done only when all of these hold.

1. **Spec.** The requirement is in section 4 with an ID, priority and status.
2. **Code.** The code is marked `@implements("REQ-…")`, or is a state change wrapped in `@mutation(action, "REQ-…")`.
3. **Test.** At least one test is marked `@covers("REQ-…")`.
4. **Audit.** Every state change writes one audit event. `@mutation` does this, so a change cannot skip it.
5. **Observability.** Every state change emits a structured log line and a metric. `@mutation` does this too.
6. **Matrix.** `python scripts/trace_matrix.py` exits 0. It fails on an Implemented requirement with no code or no test, and on any ID that code or tests cite but this file does not define.

Chain of evidence for one change: **request ID in a log line → audit event (same request ID, actor, action, before, after, spec refs) → requirement in this file → test that covers it.**

## 2. Actors

| Actor | Does |
| --- | --- |
| Coordinator | Runs the pipeline: adds care-leavers, tracks training, certifies, matches |
| Care-leaver | Trains, then visits a parent and logs the visit |
| Family | Submits a request, reads visit updates |

Login is email and password. Passwords are hashed with PBKDF2-HMAC-SHA256 and a per-user salt; login returns an opaque bearer token (7-day expiry, only its SHA-256 hash is stored). The coordinator has full access; a caregiver sees only their own record, matched requests and own visits and may log visits only for their own matched requests; a family sees only its own request, its visits and its matched caregiver's public info, and may submit a request, which becomes linked to them. The audit actor is the authenticated user's email. The `X-Actor` header is ignored whenever a valid token is present; with no token the request is rejected with 401, except when `SAATHI_DEV_NO_AUTH=1` (local development only), where `X-Actor` is accepted as before. `/healthz`, `/readyz` and `/metrics` stay unauthenticated.

## 3. Domain

- **Stages:** Applied → Training → Certified → Matched.
- **Training modules (5):** safety and hygiene; companionship and conversation; dementia basics; medication reminders and emergencies; reporting and ethics.
- **Verification checks:** identity, background, references; each Pending, Verified or Failed.
- **Request status:** Open → Matched.
- **Visit mood:** Good, Okay, Needs attention.

## 4. Requirements

Status is Implemented, Partial or Planned. Priority: P0 must have before real use, P1 next, P2 later. IDs are stable; never reuse one.

### 4.1 Pipeline, requests, visits

| ID | Requirement | Priority | Status |
| --- | --- | --- | --- |
| REQ-PIPE-01 | A care-leaver can be added with a name, city and date of birth and starts in Applied | P0 | Implemented |
| REQ-PIPE-02 | Training can start only from Applied | P0 | Implemented |
| REQ-PIPE-03 | Module progress can change only during training, and only for the five known modules | P0 | Implemented |
| REQ-PIPE-04 | Certification needs stage Training and all five modules complete | P0 | Implemented |
| REQ-FAM-01 | A family can submit a request with parent, city, 1 to 7 visits a week and a need; it starts Open | P0 | Implemented |
| REQ-MATCH-01 | A match needs an Open request and a Certified caregiver; both move to Matched together | P0 | Implemented |
| REQ-VIS-01 | A visit can be logged only for a Matched request, with a valid mood and a non-empty note | P0 | Implemented |

### 4.2 Eligibility and safety

| ID | Requirement | Priority | Status |
| --- | --- | --- | --- |
| REQ-AFT-02 | Only adults are enrolled: date of birth is required, anyone under 18 or with a future date is rejected, and the date of birth is stored but not returned by the state API | P0 | Implemented |
| REQ-SAFE-01 | A caregiver can be matched only when identity document, background check and references are all Verified; each check keeps a status (Pending, Verified, Failed) and the date it was set | P0 | Implemented |

### 4.3 Observability

| ID | Requirement | Priority | Status |
| --- | --- | --- | --- |
| REQ-OBS-01 | Every request has a request ID (taken from `X-Request-ID` or generated) and a trace ID (taken from W3C `traceparent` or generated), both returned in response headers | P0 | Implemented |
| REQ-OBS-02 | Logs are one JSON object per line and always carry request ID and trace ID; each state change logs action, entity, audit sequence, actor and spec refs | P0 | Implemented |
| REQ-OBS-03 | `/metrics` exposes Prometheus counters for HTTP requests, committed changes, rejected changes and validation errors, and a request-duration histogram | P0 | Implemented |
| REQ-OBS-04 | `/healthz` reports liveness; `/readyz` returns 503 if the audit chain does not verify | P0 | Implemented |
| REQ-OBS-05 | When `OTEL_EXPORTER_OTLP_ENDPOINT` is set and the `otel` extra is installed, traces are exported over OTLP and the OpenTelemetry trace ID is used as the trace ID | P1 | Partial |

REQ-OBS-05 is written but has not been run end to end.

### 4.4 Traceability

| ID | Requirement | Priority | Status |
| --- | --- | --- | --- |
| REQ-TRC-01 | Every committed state change appends one audit event with sequence, time, request ID, trace ID, actor, action, entity, before, after and spec refs | P0 | Implemented |
| REQ-TRC-02 | Audit events are hash-chained, so editing or removing one is detectable; `/api/audit/verify` reports the first broken event | P0 | Implemented |
| REQ-TRC-03 | The audit log can be queried by entity and action, newest first | P0 | Implemented |
| REQ-TRC-04 | `scripts/trace_matrix.py` produces `docs/TRACEABILITY.md` and fails on gaps | P0 | Implemented |
| REQ-TRC-05 | A rejected change is logged and counted but not written to the audit log, because nothing changed | P1 | Implemented |
| REQ-TRC-06 | A corrupt data file stops startup instead of being replaced, and state plus audit chain survive a restart | P0 | Implemented |

### 4.5 Researched backlog

Each item names the research behind it (section 7). Not built.

| ID | Requirement | Priority | Status |
| --- | --- | --- | --- |
| REQ-SAFE-02 | Incident report and escalation workflow, with owner, severity and resolution; a Needs attention visit opens one | P0 | Planned |
| REQ-RBAC-01 | Login and roles (coordinator, caregiver, family); a family sees only its own parent’s updates; the audit actor comes from the login | P0 | Implemented |
| REQ-PRIV-01 | Consent and notice for every person whose data is held, a way to withdraw, and retention and erasure rules | P0 | Planned |
| REQ-PRIV-02 | Breach register with a clock: notify affected people without delay and report to the Board within 72 hours | P0 | Planned |
| REQ-STORE-01 | Move from a JSON file to SQLite or Postgres, with the audit append in the same transaction as the change | P0 | Implemented |
| REQ-AFT-01 | Record each care-leaver’s sending institution, aftercare eligibility window (18 to 21, extendable to 23), and linked child-protection contacts | P1 | Planned |
| REQ-AFT-03 | Track identity documents, housing and wellbeing check-ins for each care-leaver | P1 | Planned |
| REQ-EVV-01 | Visit check-in and check-out with timestamps, and location only with the caregiver’s consent | P1 | Planned |
| REQ-CRED-01 | Credential and background-check expiry with alerts | P1 | Planned |
| REQ-PLAN-01 | Care plan per parent and a task checklist per visit, visible to the caregiver before arrival | P1 | Planned |
| REQ-NOTIF-01 | Notify the coordinator and family on Needs attention visits, late arrivals and missed visits | P1 | Planned |
| REQ-MATCH-02 | Match on language, availability and visit frequency, not only city | P2 | Planned |
| REQ-REP-01 | Reports: visit completion, punctuality, flagged visits, retention and placement rate | P2 | Planned |

Every Planned item, when built, must follow section 1 and add its own audit actions, metrics and tests.

## 5. API

| Method and path | Requirement |
| --- | --- |
| `POST /api/auth/login` {email, password} → {token, user} | REQ-RBAC-01 |
| `POST /api/auth/logout` | REQ-RBAC-01 |
| `GET /api/auth/me` | REQ-RBAC-01 |
| `POST /api/auth/users` (coordinator only) | REQ-RBAC-01 |
| `GET /api/state` | read-only |
| `POST /api/caregivers` (name, city, dateOfBirth) | REQ-PIPE-01, REQ-AFT-02 |
| `POST /api/caregivers/{id}/start` | REQ-PIPE-02 |
| `POST /api/caregivers/{id}/modules/{index}` | REQ-PIPE-03 |
| `POST /api/caregivers/{id}/certify` | REQ-PIPE-04 |
| `POST /api/caregivers/{id}/verification/{item}` (status) | REQ-SAFE-01 |
| `POST /api/requests` | REQ-FAM-01 |
| `POST /api/requests/{id}/match` | REQ-MATCH-01 |
| `POST /api/visits` | REQ-VIS-01 |
| `GET /api/audit?entity_id=&action=&limit=` | REQ-TRC-03 |
| `GET /api/audit/verify` | REQ-TRC-02 |
| `GET /healthz`, `GET /readyz`, `GET /metrics` | REQ-OBS-03, REQ-OBS-04 |

All `/api/*` routes except `POST /api/auth/login` require a bearer token (`Authorization: Bearer <token>`); without one they return 401. `/healthz`, `/readyz` and `/metrics` need no login. Write routes are coordinator-only except logging a visit (coordinator, or the matched caregiver for their own request) and submitting a request (coordinator or family). `GET /api/state` and `GET /api/audit` are role-scoped; the audit endpoint strips date of birth from before/after payloads for non-coordinator roles.

Errors are always `{"error": "message"}` with status 400 (invalid input), 404 (not found) or 409 (rule conflict).

## 6. Observability and audit design

**Correlation.** Request ID and trace ID are set in middleware, held in context variables, and copied into every log line and audit event.

**Audit event.** `seq, ts, request_id, trace_id, actor, action, entity_type, entity_id, before, after, spec_refs, prev_hash, hash`. The hash is SHA-256 over the event’s other fields in canonical JSON, and `prev_hash` is the previous event’s hash. The audit log is the only history of changes; the current state is stored separately.

**Metrics.** `saathi_http_requests_total{method,route,status}`, `saathi_http_request_seconds{route}`, `saathi_changes_total{action}`, `saathi_changes_rejected_total{action,status}`, `saathi_validation_errors_total{field}`. Route labels use the route template, not the raw path.

**Alerts to set once deployed.** `/readyz` failing; any increase in `saathi_changes_rejected_total` with status 500; a spike in `saathi_validation_errors_total`.

**Limits.** The hash chain proves history was not edited after the fact; it does not stop someone with file access from rewriting the whole chain. Anchor the latest hash somewhere else (a log sink or a separate store) before real use. The SQLite store is single-process. Date of birth is held in the data file and in audit events; the state API never returns it, and the audit endpoint strips it from before/after payloads for non-coordinator roles (the coordinator still sees it).

## 7. Research behind the backlog

Care-leavers in India
- The Juvenile Justice Act 2015 provides aftercare for people aged 18 to 21, extendable to 23, who have left a child care institution. Rule 25 of the 2016 Model Rules covers education, skill training, employment and housing. This is why REQ-AFT-02 comes first and REQ-AFT-01 records the eligibility window. [IDR](https://idronline.org/article/youth/how-indias-aftercare-programme-can-benefit-care-leavers/), [Lok Sabha reply](https://eparlib.nic.in/bitstream/123456789/2974448/1/AU1350.pdf), [Rule 25(7)](https://future.indiankanoon.org/doc/111348929/)
- Rule 25(7) lists skill training and placement in commercial establishments with corporates as an aftercare service, which supports partnering with state aftercare programmes. Same Rule 25 source.
- Care-leavers report trouble with housing, identity documents, skills, jobs and psychosocial support (basis for REQ-AFT-03). [Int. Journal of Child, Youth and Family Studies](https://journals.uvic.ca/index.php/ijcyfs/article/view/20049/8857)
- One source says Mission Vatsalya promises a ₹4,000 monthly stipend for ages 18 to 21. Confirm before relying on it. [Give.do](https://give.do/givegrants/state-of-careleavers/)

Home care software
- Common features: credential and background-check tracking with expiry alerts, visit check-in and check-out, care plans with task checklists, incident reporting, a family portal with visit summaries, and audit trails. [RaftLabs](https://www.raftlabs.com/industries/senior-care), [AllGeo](https://blog.allgeo.com/?p=5187), [Viv Technologies](https://www.vivtechnologies.com/full-features)
- For a senior-caregiver marketplace: onboarding with credential review and background checks before going live, a visible verification status, and a safeguarding escalation workflow described as mandatory. Compliance varies by state and country. [LowCode Agency](https://www.lowcode.agency/blog/how-to-build-a-senior-caregiver-marketplace)
- Electronic visit verification is legislated in the US for Medicaid-funded care. This product is private-pay, so I treat it as a trust feature, not a mandate. Confirm with a US healthcare lawyer.

India data protection
- The DPDP Rules were notified on 13 November 2025. Consent-manager registration opens 13 November 2026 and the main obligations apply from 13 May 2027. A breach needs notice to affected people without delay and a detailed report to the Data Protection Board within 72 hours. [iPleaders](https://blog.ipleaders.in/dpdp-rules-2025-operational-compliance-guide/), [Mondaq](https://www.mondaq.com/india/data-protection/1844066/dpdp-data-breach-notification-in-india-timelines-requirements-and-key-compliance-considerations)

## 8. Open questions

- Which state aftercare programme or institution will be the first referral partner?
- Who verifies identity and background checks in India, and what does it cost per person?
- Does a US-based family need a separate consent for health information about a parent?
- Where should the audit chain’s latest hash be anchored?

### Design decisions (foundation phase, REQ-STORE-01 and REQ-RBAC-01)

- **SQLite approach.** The store keeps its in-memory dict as the working mirror (same public surface: `store.db`, `store.lock`, `store.audit`, `next_id()`, `save()`), but SQLite is the durable store: one `state(key, value)` table, one row per top-level key, and `save()` writes the whole state — audit events included — in a single transaction, so the audit append lands in the same transaction as the change. Standard library `sqlite3` only; the default data file is `data/saathi.sqlite`. On first boot, a legacy `data/db.json` next to it is imported once and renamed to `db.json.migrated`, never deleted. A corrupt SQLite file raises `RuntimeError` at startup and is never overwritten.
- **Token approach.** Login is email and password; passwords use PBKDF2-HMAC-SHA256 with a per-user salt (stdlib `hashlib`). Tokens are opaque values from `secrets`, stored server-side only as SHA-256 hashes with a 7-day expiry, in the same SQLite store (`users` and `sessions` tables in the dict mirror). Sessions are auth bookkeeping, not domain changes, so login/logout are not audit-logged; creating an account is a domain change and goes through `@mutation`, so it is audited, logged and counted like any other.
- **Seed credential handling.** Exactly one coordinator account is seeded: `coordinator@saathi.local`. Its password is `SAATHI_SEED_PASSWORD` when set, otherwise a randomly generated password printed once to stdout at first boot ("Seed coordinator: coordinator@saathi.local / <password> — change after first login"). No fixed password is hardcoded anywhere.
- **X-Actor fallback rule.** A valid bearer token always wins and `X-Actor` is ignored. With no token the request is rejected with 401, except when `SAATHI_DEV_NO_AUTH=1` (local development only, default off), where `X-Actor` is accepted as the coordinator, as before.
- **`/metrics` stays unauthenticated**, so Prometheus can scrape it without a login; it exposes counters and timings only, no personal data.
