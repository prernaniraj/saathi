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
- **Incident:** severity Low, Medium, High or Critical; status Open, In progress or Resolved; owner is a coordinator email; a Needs attention visit opens one.
- **Consent:** per person (a care-leaver, or a parent identified by their family request id); purposes are care matching, visit updates to family and training records; status Granted or Withdrawn; a notice version is recorded.
- **Breach:** status Open, Notified, Reported or Closed; the Board must be told within 72 hours of discovery.

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
| REQ-OBS-05 | When `OTEL_EXPORTER_OTLP_ENDPOINT` is set and the `otel` extra is installed, traces are exported over OTLP and the OpenTelemetry trace ID is used as the trace ID | P1 | Implemented |

REQ-OBS-05 was verified end to end against a dummy OTLP/HTTP receiver (`tests/test_otel.py`): a POST to `/v1/traces` arrives carrying spans for the requests made, and the trace ID in the log lines is the OpenTelemetry trace ID.

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
| REQ-SAFE-02 | Incident report and escalation workflow, with owner, severity and resolution; a Needs attention visit opens one | P0 | Implemented |
| REQ-RBAC-01 | Login and roles (coordinator, caregiver, family); a family sees only its own parent’s updates; the audit actor comes from the login | P0 | Implemented |
| REQ-PRIV-01 | Consent and notice for every person whose data is held, a way to withdraw, and retention and erasure rules | P0 | Implemented |
| REQ-PRIV-02 | Breach register with a clock: notify affected people without delay and report to the Board within 72 hours | P0 | Implemented |
| REQ-STORE-01 | Move from a JSON file to SQLite or Postgres, with the audit append in the same transaction as the change | P0 | Implemented |
| REQ-AFT-01 | Record each care-leaver’s sending institution, aftercare eligibility window (18 to 21, extendable to 23), and linked child-protection contacts | P1 | Implemented |
| REQ-AFT-03 | Track identity documents, housing and wellbeing check-ins for each care-leaver | P1 | Implemented |
| REQ-EVV-01 | Visit check-in and check-out with timestamps, and location only with the caregiver’s consent | P1 | Implemented |
| REQ-CRED-01 | Credential and background-check expiry with alerts | P1 | Implemented |
| REQ-PLAN-01 | Care plan per parent and a task checklist per visit, visible to the caregiver before arrival | P1 | Implemented |
| REQ-NOTIF-01 | Notify the coordinator and family on Needs attention visits, late arrivals and missed visits | P1 | Implemented |
| REQ-MATCH-02 | Match on language, availability and visit frequency, not only city | P2 | Implemented |
| REQ-REP-01 | Reports: visit completion, punctuality, flagged visits, retention and placement rate | P2 | Implemented |

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
| `POST /api/caregivers/{id}/aftercare` (institution, aftercareUntil, extended, contacts, locationConsent; coordinator only) | REQ-AFT-01 |
| `POST /api/caregivers/{id}/documents` (type, reference, status; coordinator only) | REQ-AFT-03 |
| `POST /api/caregivers/{id}/housing` (status, details; coordinator only) | REQ-AFT-03 |
| `POST /api/caregivers/{id}/checkins` (note, date; coordinator, or the caregiver themself) | REQ-AFT-03 |
| `POST /api/caregivers/{id}/credentials` (type, issuedDate, expiryDate; coordinator only) | REQ-CRED-01 |
| `GET /api/credentials/expiring?days=30` (coordinator only) | REQ-CRED-01 |
| `POST /api/caregivers/{id}/profile` (languages, availability, capacityVisitsPerWeek; coordinator only) | REQ-MATCH-02 |
| `POST /api/caregivers/{id}/start` | REQ-PIPE-02 |
| `POST /api/caregivers/{id}/modules/{index}` | REQ-PIPE-03 |
| `POST /api/caregivers/{id}/certify` | REQ-PIPE-04 |
| `POST /api/caregivers/{id}/verification/{item}` (status) | REQ-SAFE-01 |
| `POST /api/requests` (optional `language`) | REQ-FAM-01, REQ-MATCH-02 |
| `POST /api/requests/{id}/match` | REQ-MATCH-01 |
| `GET /api/requests/{id}/suggestions` (coordinator only) | REQ-MATCH-02 |
| `GET /api/requests/{id}/care-plan` (coordinator, matched caregiver, or the family) | REQ-PLAN-01 |
| `POST /api/requests/{id}/care-plan` (goals; coordinator only) | REQ-PLAN-01 |
| `POST /api/requests/{id}/care-plan/tasks` (title; coordinator only) | REQ-PLAN-01 |
| `POST /api/requests/{id}/care-plan/tasks/{taskId}/toggle` (coordinator or matched caregiver) | REQ-PLAN-01 |
| `POST /api/visits` (optional `plannedAt`) | REQ-VIS-01, REQ-EVV-01 |
| `POST /api/visits/{id}/checkin` (timestamp, location; caregiver who owns the visit, or coordinator) | REQ-EVV-01 |
| `POST /api/visits/{id}/checkout` (timestamp, location; caregiver who owns the visit, or coordinator) | REQ-EVV-01 |
| `GET /api/notifications` (role-scoped) | REQ-NOTIF-01 |
| `POST /api/notifications/{id}/read` | REQ-NOTIF-01 |
| `POST /api/notifications/check-missed` (coordinator only; runs the missed-visit check) | REQ-NOTIF-01 |
| `GET /api/reports/summary` (coordinator only) | REQ-REP-01 |
| `GET /api/incidents` (role-scoped) | REQ-SAFE-02 |
| `POST /api/incidents` (coordinator only) | REQ-SAFE-02 |
| `POST /api/incidents/{id}/assign` {owner} (coordinator only) | REQ-SAFE-02 |
| `POST /api/incidents/{id}/resolve` {resolution} (coordinator only) | REQ-SAFE-02 |
| `GET /api/consents` (role-scoped) | REQ-PRIV-01 |
| `POST /api/consents` (coordinator only) | REQ-PRIV-01 |
| `POST /api/consents/{id}/withdraw` (coordinator only) | REQ-PRIV-01 |
| `POST /api/caregivers/{id}/erase` (coordinator only) | REQ-PRIV-01 |
| `GET /api/breaches` (coordinator only) | REQ-PRIV-02 |
| `POST /api/breaches` (coordinator only) | REQ-PRIV-02 |
| `POST /api/breaches/{id}/notify` (coordinator only) | REQ-PRIV-02 |
| `POST /api/breaches/{id}/report` (coordinator only) | REQ-PRIV-02 |
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

### Design decisions (P1/P2 wave: REQ-AFT-01, REQ-AFT-03, REQ-EVV-01, REQ-CRED-01, REQ-PLAN-01, REQ-NOTIF-01, REQ-MATCH-02, REQ-REP-01)

- **New modules.** `app/aftercare.py` (AFT-01, AFT-03), `app/evv.py` (EVV-01), `app/credentials.py` (CRED-01), `app/careplans.py` (PLAN-01), `app/notifications.py` (NOTIF-01), `app/matching.py` (MATCH-02), `app/reports.py` (REP-01). New store key `db["notifications"]`; care plans live on the request dict as `carePlan`; credentials, documents, housing and check-ins live on the caregiver dict. Seed data covers all of it (languages/availability/capacity on every seed caregiver, a care plan for r1, credential expiry dates, one wellbeing check-in for c6).
- **Aftercare window (AFT-01).** The default `aftercareUntil` is the 21st birthday, computed from the date of birth; `extended: true` records an extension to the 23rd birthday (seed c5 is the extended example). The 23-year cap is NOT enforced in code: the date and the flag are stored as the coordinator records them, because the extension decision sits with the aftercare programme, not this system. `locationConsent` is set through the same aftercare endpoint because it is a consent the coordinator records with the aftercare record.
- **Privacy of aftercare records (AFT-03).** Document reference numbers, housing, wellbeing check-ins and child-protection contacts are visible only to the coordinator and to the caregiver themself. The family-scoped `get_state` strips all four from the matched caregiver's record. Credentials (type, issued/expiry dates, status — no reference numbers) stay visible to the family.
- **EVV (EVV-01).** Check-in/check-out store `checkinAt`/`checkoutAt` and, when consented, `checkinLocation`/`checkoutLocation`. A location sent without consent returns 400 and stores nothing — the check is made before any state change. `plannedAt` is optional on visit creation and defaults to the moment the visit was logged, so a visit logged without a planned time is never late. Late means a check-in more than 30 minutes after `plannedAt`.
- **Credentials (CRED-01).** Credential status is computed at creation from the expiry date ("Valid"/"Expired"). The expiring list (`expiring_credentials`, `GET /api/credentials/expiring?days=30`) includes already-expired credentials (negative `daysLeft`), sorted soonest first, so an expired background check never drops silently out of the alert list. An expired "Background check" credential does NOT change the caregiver's verification status: it is surfaced as a `verificationStale` flag in `get_state` and in the expiring list, and re-verification stays a deliberate coordinator action.
- **Care plans (PLAN-01).** The plan is `{goals, tasks: [{id, title, done}]}` on the request, so it is visible wherever the request is visible: the matched caregiver and the family see it in `get_state` and through `GET /api/requests/{id}/care-plan`. Adding the first task creates an empty plan (goals blank) so the checklist exists before the goals are written. Task toggle is allowed for the coordinator and the matched caregiver.
- **Notifications (NOTIF-01).** In-app only, plus structured log lines: there is no email/SMS provider, and choosing a real delivery channel is an open decision. Audience is `{role: "coordinator"}`, `{role: "family", requestId}` or `{role: "caregiver", caregiverId}`. `notify_for_visit` (Needs attention) and `check_missed_visits` are standalone `@mutation` helpers that the coordinator wires in after `log_visit` / on a schedule during the merge; `log_visit` and `match` were deliberately not modified. A late check-in creates its late notification inside the check-in mutation, so the check-in's own audit event covers it. Missed visits: for each Matched request the expected interval is 7 / visitsPerWeek days since the last logged visit; a request with no visit logged yet has no baseline and is skipped. A second missed-visit notification for the same request is not created while an unread one exists. `POST /api/notifications/check-missed` (coordinator) runs the check on demand, since there is no scheduler yet.
- **Matching (MATCH-02).** Suggestions rank Certified + fully-verified caregivers only. Score out of 100: city 30, language 25, availability 25, capacity 20. Availability is scored on the number of days free a week against the visits a week the request needs — the request does not name days, so day-of-week overlap cannot be scored yet. A request with no language preference gives every candidate the language points. A caregiver with no capacity recorded scores 0 on capacity and the breakdown says so, rather than assuming they can take the visits. `POST /api/requests/{id}/match` is unchanged.
- **Reports (REP-01).** `GET /api/reports/summary` is read-only (coordinator). Definitions: visit completion = visits logged vs expected, where expected per Matched request is visitsPerWeek × full weeks since `matchedAt` (set when the match is made; a request matched less than a week ago expects 0, so the rate is null until there is something to compare against). Punctuality = share of visits with both check-in and check-out, and share of checked-in visits that were late per the EVV 30-minute rule. Flagged visits = count of "Needs attention". Retention = of caregivers who have ever been matched, the share still in the Matched stage; "ever matched" is caregivers currently Matched, plus any caregiver linked to a Matched request, plus any caregiver named in a `request.matched` audit event. Placement rate = Matched requests / all requests.
- **The `@mutation` wrapper consumes `ctx`.** New mutation functions are written without a `ctx` parameter (like the existing ones in `app/service.py`): callers pass `(store, ctx, ...)` and the wrapper keeps `ctx` for the audit event and log line. New modules import `@mutation` from `app.service`; `app/service.py` imports the new modules only lazily inside `get_state`, to avoid a circular import.

## 8. Open questions

- Which state aftercare programme or institution will be the first referral partner?
- Who verifies identity and background checks in India, and what does it cost per person?
- Does a US-based family need a separate consent for health information about a parent?
- Where should the audit chain’s latest hash be anchored?
- How should a full erasure handle earlier audit events that still hold the erased person's name, given the hash chain cannot be rewritten?
- Which channel should deliver notifications to the coordinator and the family (email, SMS, WhatsApp)? In-app only for now.
- Who runs the missed-visit check on a schedule, and how often? `POST /api/notifications/check-missed` is on demand for now.
- Should a family request name the days of the week it needs, so matching can score day-of-week overlap instead of just the number of free days?
- Should the system enforce the aftercare extension cap (no later than the 23rd birthday), or keep trusting the coordinator's record?

### Design decisions (foundation phase, REQ-STORE-01 and REQ-RBAC-01)

- **SQLite approach.** The store keeps its in-memory dict as the working mirror (same public surface: `store.db`, `store.lock`, `store.audit`, `next_id()`, `save()`), but SQLite is the durable store: one `state(key, value)` table, one row per top-level key, and `save()` writes the whole state — audit events included — in a single transaction, so the audit append lands in the same transaction as the change. Standard library `sqlite3` only; the default data file is `data/saathi.sqlite`. On first boot, a legacy `data/db.json` next to it is imported once and renamed to `db.json.migrated`, never deleted. A corrupt SQLite file raises `RuntimeError` at startup and is never overwritten.
- **Token approach.** Login is email and password; passwords use PBKDF2-HMAC-SHA256 with a per-user salt (stdlib `hashlib`). Tokens are opaque values from `secrets`, stored server-side only as SHA-256 hashes with a 7-day expiry, in the same SQLite store (`users` and `sessions` tables in the dict mirror). Sessions are auth bookkeeping, not domain changes, so login/logout are not audit-logged; creating an account is a domain change and goes through `@mutation`, so it is audited, logged and counted like any other.
- **Seed credential handling.** Exactly one coordinator account is seeded: `coordinator@saathi.local`. Its password is `SAATHI_SEED_PASSWORD` when set, otherwise a randomly generated password printed once to stdout at first boot ("Seed coordinator: coordinator@saathi.local / <password> — change after first login"). No fixed password is hardcoded anywhere.
- **X-Actor fallback rule.** A valid bearer token always wins and `X-Actor` is ignored. With no token the request is rejected with 401, except when `SAATHI_DEV_NO_AUTH=1` (local development only, default off), where `X-Actor` is accepted as the coordinator, as before.
- **`/metrics` stays unauthenticated**, so Prometheus can scrape it without a login; it exposes counters and timings only, no personal data.

### Design decisions (safety and privacy, REQ-SAFE-02, REQ-PRIV-01, REQ-PRIV-02)

- **Incidents (`app/incidents.py`).** `open_for_visit(store, ctx, visit)` opens an incident only for a visit whose mood is "Needs attention" (any other mood is a 409, so the helper can be called unconditionally after logging a visit); one visit can open at most one incident (a second call is a 409). An automatically opened incident has severity Medium and no owner — a coordinator assigns one. Assigning requires the owner to be an existing coordinator account and moves Open to In progress; resolving needs a non-empty resolution and stamps `resolvedAt`. The helper is wired into the visit flow in `app/main.py` (`_escalate_visit`, called after `log_visit` commits), so a visit can never fail because of incident handling: a failure in the escalation is logged with the request ID and the visit still succeeds. Reads are role-scoped like the state API: a caregiver sees incidents about their own visits, a family sees incidents about its own request.
- **Consent (`app/privacy.py`).** One active (Granted) consent per person; granting again is a 409 until the old one is withdrawn, and withdrawing then re-granting creates a new record, so the history is kept. A parent is identified by their family request id, since parents have no separate id. Purposes are a closed list: care matching, visit updates to family, training records. The notice version recorded is "1". Withdrawal is coordinator-only in this POC: the person concerned asks the coordinator to withdraw it — a self-service withdrawal is a later step, not a silent default. The pipeline is NOT blocked on missing consent in this POC: a care-leaver with no consent can still be trained, certified and matched. Instead it is surfaced — `GET /api/state` carries `consentStatus` (Granted, Withdrawn or None) on every caregiver, and the Pipeline page flags a withdrawn or missing consent and lets the coordinator record or withdraw it.
- **Retention rule.** Erase a person's data on request, without delay. In addition, once a year (each January) the coordinator reviews records with no activity for 12 months and erases them, unless there is a live match or an open incident or breach involving that person. That review is a manual process in this POC; no automatic deletion runs.
- **Erasure.** Name and city become "[erased]", date of birth is removed, and `erased`/`erasedAt` are set; id, stage, modules and verification are kept so requests, visits and the audit chain still refer to the record, and any granted consent for the person is withdrawn as part of the same change. The erasure audit event's before/after payloads are built from the redacted copy only, so the erased personal data never enters the audit log through that event. Known limit: earlier audit events (for example the original `caregiver.created`) still hold the name, because the hash chain cannot be rewritten without breaking verification. A full erasure that also rewrites history is an open question, not something this POC does silently.
- **Seed consent is assumed demo data.** The seed grants consent for the six seed care-leavers and the three seed parents so the consent flag shows as granted in the sample data. That is not real consent and must be replaced by recorded consent before any real person's data is used.
- **Breach register (`app/breach.py`).** `boardDueAt = discoveredAt + 72h`; the read view computes `overdue` (now past the due time while `boardReportedAt` is empty) and `hoursRemaining` at read time, so the countdown is always current. The order is fixed: notify the affected people first, then report to the Board — reporting an Open breach is a 409. `affectedPersonIds` are validated against care-leaver and request ids. Status Closed is reserved for a future close step; there is no close endpoint yet.
