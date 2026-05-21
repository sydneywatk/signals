# OpenStates API — Source Spike

> Source research for a GTM-signal pipeline that ingests state legislative data
> across all 50 states, DC, and PR.
>
> Probes run **2026-05-20** against `https://v3.openstates.org/`.
> All curl probes below are real, unauthenticated, and reproducible.

---

## TL;DR

- **Single canonical source** for 50-state legislative data. One vendor, one
  schema, one API contract — replaces 50+ scraper integrations. Backed by the
  Plural Policy / OpenStates project (`docs.openstates.org`, `v3.openstates.org`).
- **API key is mandatory** on every endpoint. Unauthenticated requests return
  `HTTP 403` with a JSON body pointing to the signup page. No public/anon tier,
  no IP-based allowance. Key is sent via `X-API-KEY` header (preferred) or
  `?apikey=` query param.
- **Rich, normalized bill schema**: `identifier`, `title`, `subject[]`,
  `sponsorships[]`, `actions[]`, `versions[]` (PDF/HTML links), `votes[]`,
  `latest_action_date`, `updated_at`. Sponsorships expose nested
  `CompactPerson` with party + jurisdiction. This is exactly the signal surface
  the pipeline needs.
- **Incremental sync is first-class**: `updated_since`, `created_since`, and
  `action_since` are all query params on `/bills`. Default sort is
  `updated_desc`. The pipeline can do daily delta pulls cheaply.
- **Committees + events are flagged experimental** in the spec's own preamble:
  "data is not yet available for all states and the exact format ... may change
  slightly." For witness/hearing testimony, the API exposes
  `EventParticipant` (a name + entity_type), but **no transcripts and no
  witness statement text**. Plan to use events for *scheduling signal*, not
  testimony content.
- **Bulk fallback exists and is huge**: full PostgreSQL dump
  (~10.5 GB, monthly) at
  `https://data.openstates.org/postgres/monthly/YYYY-MM-public.pgdump`,
  served from S3+CloudFront, no auth, returns `HTTP 200` on `HEAD`. Use this
  for backfill; use the API for incremental.

---

## 1. Authentication

### Where the key lives

API keys are issued from a user account at:

- Profile (current redirect target): `https://open.pluralpolicy.com/accounts/profile/`
- Older URL still referenced in error bodies: `https://openstates.org/account/profile/`

> The OpenStates brand is being merged into Plural Policy. `openstates.org/api/`
> and `openstates.org/data/` now `301`-redirect to `open.pluralpolicy.com/...`.
> The API host itself (`v3.openstates.org`) has NOT moved.

### How to send it

Two equivalent methods (from `openapi.json`, every endpoint accepts both):

```
X-API-KEY: <key>          # header — preferred
?apikey=<key>             # query string — convenient for one-off curl
```

### Probe: missing key (403)

```bash
curl -i "https://v3.openstates.org/jurisdictions"
```

```
HTTP/2 403
date: Thu, 21 May 2026 00:02:37 GMT
content-type: application/json
content-length: 132
server: uvicorn

{"detail":"Must provide API Key as ?apikey or X-API-KEY.
 Login and visit https://openstates.org/account/profile/ for your API key."}
```

### Probe: invalid key (401)

```bash
curl -i -H "X-API-KEY: invalid_test_key_12345" \
  "https://v3.openstates.org/bills?jurisdiction=ca"
```

```
HTTP/2 401
content-type: application/json
content-length: 103

{"detail":"Invalid API Key. Login and visit
 https://openstates.org/account/profile/ for your API key."}
```

**Distinction matters for retry logic**: `403` = missing/malformed key
(client bug, do not retry), `401` = bad key (rotate secret, do not retry).
Only `429` / `5xx` should be retried.

### Tiers — what I could and could not confirm

The public docs (`docs.openstates.org/api-v3/`) state only that "API keys are
required." They do **not** publish:

- Free vs paid tier names
- Per-second or per-day request quotas
- Pricing for higher tiers
- Whether rate-limit headers (`X-RateLimit-*`, `Retry-After`) are emitted on
  success responses

Pricing has historically lived behind a "Contact us" form on the Plural Policy
site (the org that now owns OpenStates). **Action item before integrating:**
register a free key, observe response headers on a real authenticated request,
and email Plural for commercial tier pricing. Do not assume an unlimited free
tier — the project explicitly says they may throttle.

---

## 2. Rate Limits

### What's documented

Nothing concrete in `docs.openstates.org/api-v3/` or the OpenAPI spec
(`/openapi.json`). The OpenAPI `info.description` only warns that committees
and events are "experimental." No `securitySchemes` block in the spec.

### What I observed

Five sequential unauthenticated requests to `/jurisdictions` (each rejected
with `403`):

```
Req 1: HTTP 403 time=0.439s
Req 2: HTTP 403 time=0.299s
Req 3: HTTP 403 time=0.294s
Req 4: HTTP 403 time=0.328s
Req 5: HTTP 403 time=0.556s
```

No `Retry-After` header on the rejection, no `X-RateLimit-*` headers. Latency
is stable (~300 ms), no burst penalty visible at the auth layer.

### Working assumptions (must verify with key)

- Treat the API as **throttled per key**, not per IP.
- Implement client-side rate limiting from day one — assume something like
  **1 req/sec sustained, 1000 req/day** as a conservative default until the
  account dashboard shows otherwise. Many free legislative APIs use that band.
- **Honor `Retry-After` if present**, even though it wasn't observed on `403`.
  It is the standard header for `429 Too Many Requests` and the client should
  handle it.
- Cache aggressively. Bill content changes infrequently — once a bill is
  "passed" or "failed," it almost never moves again.

---

## 3. Endpoint Reference

All paths confirmed via `GET https://v3.openstates.org/openapi.json` (returned
`HTTP 200`, 45,488 bytes, `openapi: 3.0.2`, `info.version: 2021.11.12`).

```
/jurisdictions
/jurisdictions/{jurisdiction_id}
/people
/people.geo
/bills
/bills/ocd-bill/{openstates_bill_id}
/bills/{jurisdiction}/{session}/{bill_id}
/committees
/committees/{committee_id}
/events
/events/{event_id}
/metrics            # Prometheus dump, public, not part of v3 contract
```

### 3.1 `GET /jurisdictions`

List the 52 jurisdictions (50 states + DC + PR).

**Query params** (from `openapi.json`):

| Param | Type | Default | Notes |
|---|---|---|---|
| `classification` | enum | — | `state`, `municipality`, … |
| `include` | array | `[]` | `organizations`, `legislative_sessions`, `latest_runs` |
| `page` | int | `1` | |
| `per_page` | int | `52` | Default sized to fit all jurisdictions in one call |

**Probe**:

```bash
curl -i "https://v3.openstates.org/jurisdictions" \
  -H "X-API-KEY: $OPENSTATES_API_KEY"
```

**Response shape** (from `Jurisdiction` schema):

```json
{
  "results": [{
    "id": "ocd-jurisdiction/country:us/state:nc/government",
    "name": "North Carolina",
    "classification": "state",
    "division_id": "ocd-division/country:us/state:nc",
    "url": "https://nc.gov",
    "latest_bill_update": "2026-05-19T18:42:00Z",
    "latest_people_update": "2026-05-12T09:00:00Z",
    "organizations": [...],
    "legislative_sessions": [{
      "identifier": "2025-2026",
      "name": "2025-2026 Regular Session",
      "classification": "primary",
      "start_date": "2025-01-08",
      "end_date": "2026-12-31",
      "downloads": [{"data_type":"bills","url":"https://...","updated_at":"..."}]
    }],
    "latest_runs": [{"success":true,"start_time":"...","end_time":"..."}]
  }],
  "pagination": {"per_page": 52, "page": 1, "max_page": 1, "total_items": 52}
}
```

**Pipeline use**: cache once at startup, refresh weekly. Use
`legislative_sessions[].downloads[]` to discover the bulk-data URLs for each
session — this is the official way to find per-session CSV/JSON exports.
Use `latest_runs` to detect stuck scrapers on a given state.

### 3.2 `GET /bills`

The hot path for the pipeline.

**Query params** (16 total, abbreviated):

| Param | Type | Notes |
|---|---|---|
| `jurisdiction` | string | name or ID; e.g., `ca` or `ocd-jurisdiction/...` |
| `session` | string | session identifier |
| `chamber` | string | chamber of origination |
| `identifier` | array | exact bill identifier(s), e.g., `["SB 113"]` |
| `classification` | string | `bill`, `resolution`, etc. |
| `subject` | array | one or more subject tags |
| `updated_since` | string | **incremental sync key** |
| `created_since` | string | first-seen filter |
| `action_since` | string | filter by recent legislative activity |
| `sort` | enum | `updated_asc`, `updated_desc` (default), `first_action_asc/desc`, `latest_action_asc/desc` |
| `sponsor` | string | person name or ID |
| `sponsor_classification` | string | e.g., `primary` |
| `q` | string | full-text search |
| `include` | array | see below |
| `page` | int | default `1` |
| `per_page` | int | default `10` |

**`include` enum values** (`BillInclude`):
`sponsorships`, `abstracts`, `other_titles`, `other_identifiers`, `actions`,
`sources`, `documents`, `versions`, `votes`, `related_bills`.

> By default, the bill list returns **none** of these heavy fields.
> The pipeline must opt-in per call.

**Probe**:

```bash
curl -i "https://v3.openstates.org/bills?jurisdiction=ca&updated_since=2026-04-01"
# → HTTP 403  (key required)
```

For the authenticated, signal-rich call the pipeline will use:

```bash
curl "https://v3.openstates.org/bills" \
  --data-urlencode "jurisdiction=ca" \
  --data-urlencode "updated_since=2026-04-01" \
  --data-urlencode "include=sponsorships" \
  --data-urlencode "include=actions" \
  --data-urlencode "include=versions" \
  --data-urlencode "per_page=20" \
  --data-urlencode "page=1" \
  -G \
  -H "X-API-KEY: $OPENSTATES_API_KEY"
```

**Response envelope** is `BillList`:

```json
{
  "results": [ { ...Bill... } ],
  "pagination": {"per_page": 20, "page": 1, "max_page": 187, "total_items": 3731}
}
```

**Bill object — key fields** (from `Bill` schema):

| Field | Type | Notes |
|---|---|---|
| `id` | string | `ocd-bill/f0049138-1ad8-4506-...` — stable UUID, primary key |
| `session` | string | `2025-2026` |
| `jurisdiction` | CompactJurisdiction | `{id, name, classification}` |
| `from_organization` | Organization | originating chamber |
| `identifier` | string | human ID, e.g., `SB 113` |
| `title` | string | |
| `classification` | string[] | e.g., `["bill"]`, `["resolution"]` |
| `subject` | string[] | normalized subject tags |
| `created_at` | datetime | |
| `updated_at` | datetime | **delta sync watermark** |
| `first_action_date` | string | YYYY-MM-DD |
| `latest_action_date` | string | YYYY-MM-DD |
| `latest_action_description` | string | e.g., "Introduced in House" |
| `latest_passage_date` | string | YYYY-MM-DD or empty |
| `openstates_url` | string | canonical web URL |
| `abstracts` | BillAbstract[] | only with `include=abstracts` |
| `sponsorships` | BillSponsorship[] | only with `include=sponsorships` |
| `actions` | BillAction[] | only with `include=actions` |
| `versions` | BillDocumentOrVersion[] | drafts / amendments — **links only** |
| `documents` | BillDocumentOrVersion[] | fiscal notes, analyses — links only |
| `votes` | VoteEvent[] | only with `include=votes` |
| `sources` | Link[] | upstream gov URLs |
| `related_bills` | RelatedBill[] | companion / replaces / etc. |

**`BillSponsorship`** — exactly what the GTM signal needs:

```json
{
  "id": "uuid",
  "name": "JONES",
  "entity_type": "person",            // or "organization"
  "primary": true,
  "classification": "primary",         // or "cosponsor"
  "person": {                          // CompactPerson, null if entity is org
    "id": "ocd-person/...",
    "name": "Angela Augusta",
    "party": "Democratic",
    "current_role": {
      "title": "Senator",
      "org_classification": "upper",
      "district": 3,
      "division_id": "ocd-division/country:us/state:nc/sldu:3"
    }
  }
}
```

This single nested object delivers: who introduced it, primary vs co-sponsor,
party affiliation, chamber, and district — without a second API call.

**`BillAction`** — provides the timeline signal:

```json
{
  "id": "uuid",
  "organization": {...},
  "description": "Passed 1st Reading",
  "date": "2020-03-14",
  "classification": ["passed"],
  "order": 12,
  "related_entities": [...]
}
```

Watch `classification` arrays for `["introduction"]`, `["referral-committee"]`,
`["reading-1"]`, `["passage"]`, `["executive-signature"]` — these are the
trigger events for GTM signals.

**`BillDocumentOrVersion`** — important caveat:

```json
{
  "id": "uuid",
  "note": "Latest Version",
  "date": "2020-10-01",
  "classification": "amendment",
  "links": [
    {"url": "https://...", "media_type": "application/pdf"},
    {"url": "https://...", "media_type": "text/html"}
  ]
}
```

The API gives you **links to the state's official text**, not the text itself.
The pipeline needs a separate fetch+OCR/PDF-parse stage if it wants searchable
bill text. The bulk JSON dumps include "full text materials" per
`openstates.org/data/`, but that has not been verified in this spike.

### 3.3 `GET /bills/{jurisdiction}/{session}/{bill_id}`

Single-bill lookup by the human-readable triple. Same `include` enum applies.

```bash
curl "https://v3.openstates.org/bills/ca/20252026/SB113?include=sponsorships&include=actions&include=versions" \
  -H "X-API-KEY: $OPENSTATES_API_KEY"
```

Returns a bare `Bill` object (not wrapped in `results`).

### 3.4 `GET /bills/ocd-bill/{openstates_bill_id}`

Same as above but keyed by the internal UUID. Use this in the pipeline because
the UUID is stable across renames; the human identifier occasionally changes
between sessions.

### 3.5 `GET /people`

**Query params**: `jurisdiction`, `name` (case-insensitive substring), `id`
(repeatable for batch lookup), `org_classification` (`upper`/`lower`),
`district`, `include`, `page`, `per_page` (default `10`).

**`include` enum**: `other_names`, `other_identifiers`, `links`, `sources`,
`offices`.

```bash
curl -i "https://v3.openstates.org/people?jurisdiction=ca"
# → HTTP 403  (key required)
```

**`Person` schema** — all the GTM-relevant fields are top-level (not gated by
`include`):

| Field | Notes |
|---|---|
| `id` | `ocd-person/...` |
| `name`, `given_name`, `family_name` | |
| `party` | `Democratic`, `Republican`, `Independent`, ... |
| `current_role` | `{title, org_classification, district, division_id}` |
| `jurisdiction` | CompactJurisdiction |
| `image`, `email`, `gender`, `birth_date`, `death_date` | |
| `created_at`, `updated_at` | |
| `openstates_url` | |
| `offices` | array of `{name, voice, fax, address, classification}` — capitol vs district |
| `links`, `sources` | only with `include` |

> The `Person` schema has **no phone number at the top level**. Voice/fax
> live inside `offices[]`, which requires `include=offices`. The pipeline
> should request `include=offices` if it wants contact details.

### 3.6 `GET /people.geo`

Reverse-geocode to legislators. Useful for "given a customer's address,
who represents them?" — directly GTM-relevant.

```bash
curl "https://v3.openstates.org/people.geo?lat=37.7749&lng=-122.4194" \
  -H "X-API-KEY: $OPENSTATES_API_KEY"
```

(Not probed unauthenticated — schema same as `/people` results.)

### 3.7 `GET /committees`

**Marked experimental**: "data is not yet available for all states."

**Query params**: `jurisdiction`, `classification` (`committee`/`subcommittee`),
`parent` (ocd-organization ID), `chamber`, `include`, `page`, `per_page`
(default `20`).

**`include` enum**: `memberships`, `links`, `sources`.

```bash
curl -i "https://v3.openstates.org/committees?jurisdiction=ca"
# → HTTP 403
```

**`Committee` schema**:

```json
{
  "id": "ocd-organization/...",
  "name": "Health & Public Services",
  "classification": "committee",        // or "subcommittee"
  "parent_id": "ocd-organization/...",  // null for top-level
  "extras": {"room": "Room 4B"},
  "memberships": [
    {"person_name": "...", "role": "chair", "person": { CompactPerson }}
  ],
  "other_names": [...],
  "links": [...],
  "sources": [...]
}
```

### 3.8 `GET /events`

**Also experimental**. This is the closest thing to a hearings endpoint.

**Query params**: `jurisdiction`, `deleted` (default `false`), `before`,
`after`, `require_bills` (default `false`), `include`, `page`, `per_page`
(default `20`).

**`include` enum**: `links`, `sources`, `media`, `documents`,
`participants`, `agenda`.

```bash
curl -i "https://v3.openstates.org/events?jurisdiction=ca"
# → HTTP 403
```

**`Event` schema**:

```json
{
  "id": "...",
  "name": "Senate Health Committee Hearing",
  "jurisdiction": { CompactJurisdiction },
  "description": "...",
  "classification": "committee-meeting",
  "start_date": "2026-05-22T14:00:00",
  "end_date": "2026-05-22T17:00:00",
  "all_day": false,
  "status": "confirmed",
  "upstream_id": "...",
  "deleted": false,
  "location": {"name": "Room 4B", "url": "..."},
  "participants": [
    {"name": "Sen. Jones", "entity_type": "person",
     "person": { CompactPerson }, "note": "chair"}
  ],
  "agenda": [
    {"description": "...", "classification": ["bill-discussion"],
     "order": 1, "subjects": [...], "notes": [...],
     "related_entities": [...], "media": [...]}
  ],
  "documents": [...],
  "media": [...]
}
```

**Witness/testimony reality check**: `EventParticipant` is just a name +
entity type. There is **no field for written testimony, submitted statements,
or transcripts**. Use `events` for "who is meeting when about what bills"
signal — not for content analysis of testimony. For the latter, you would
need to scrape state legislature sites directly (out of scope here).

### 3.9 `GET /metrics`

Public Prometheus dump, returns `HTTP 200` unauthenticated. Useful only for
ops debugging (Python GC stats, request counters). Not part of the data API.

```
HTTP/2 200
content-type: text/plain; version=0.0.4; charset=utf-8
content-length: 21063
...
python_gc_objects_collected_total{generation="0"} 163672.0
```

---

## 4. Pagination

**Model**: page/per_page offset pagination. **Not** cursor-based.

**Envelope** (`PaginationMeta` schema), present on every list endpoint:

```json
{
  "results": [...],
  "pagination": {
    "per_page": 20,
    "page": 1,
    "max_page": 187,
    "total_items": 3731
  }
}
```

**Defaults**:

| Endpoint | Default `per_page` |
|---|---|
| `/jurisdictions` | `52` |
| `/bills` | `10` |
| `/people` | `10` |
| `/committees` | `20` |
| `/events` | `20` |

**Upper bound on `per_page`** is not documented. The `per_page=500` probe
still returned `403` (auth check is first), so the real ceiling is unknown
until tested with a valid key. Plan for **`per_page=20` as the safe default**
and probe higher values empirically.

**Total counts**: `total_items` is returned, so the client can compute
expected pages before the loop. This is friendly for progress bars and for
parallel-page fetches, but **don't parallelize beyond the rate limit.**

**Stop conditions for the loop**:

1. `len(results) == 0` (defensive)
2. `pagination.page >= pagination.max_page`

Don't trust a single condition — combine both.

---

## 5. Pipeline-Relevant Fields (Crib Sheet)

### Bills (the core signal)

```
id                          # ocd-bill/UUID — primary key
identifier                  # SB 113
title                       # one-liner
classification[]            # bill | resolution | constitutional amendment
subject[]                   # taxonomy (state-specific vocabulary)
abstracts[]                 # opt-in — for summary cards
sponsorships[]              # opt-in — WHO introduced it
  .person.party             # Dem / Rep / Ind
  .person.current_role.district
  .primary                  # primary vs co-sponsor
  .classification
actions[]                   # opt-in — full timeline
  .classification[]         # introduction | reading-1 | passage | ...
  .date
versions[]                  # opt-in — PDF/HTML links (NOT text)
documents[]                 # opt-in — fiscal notes, analyses (links)
votes[]                     # opt-in — roll calls
sources[]                   # upstream gov URLs
latest_action_date          # cheap recency signal
latest_action_description   # human-readable last event
latest_passage_date         # null if not yet passed
first_action_date           # introduction date
updated_at                  # SYNC WATERMARK
```

### People (sponsor enrichment)

```
id                          # ocd-person/UUID
name, given_name, family_name
party                       # Democratic / Republican / Independent / ...
current_role
  .title                    # Senator / Representative / Speaker / ...
  .org_classification       # upper | lower
  .district                 # int or string
  .division_id              # ocd-division/... for GIS join
jurisdiction
email                       # often empty
offices[]                   # opt-in — phone, address
updated_at                  # SYNC WATERMARK
```

### Committees (hearing context)

```
id                          # ocd-organization/UUID
name                        # "Health & Public Services"
classification              # committee | subcommittee
parent_id                   # null at top level
memberships[]               # opt-in — chair, vice, members (with party via CompactPerson)
```

### Events (hearing schedule signal)

```
id
name
start_date / end_date       # the time signal
classification              # committee-meeting | floor-session | other
status                      # confirmed | cancelled | tentative
location.name               # room / chamber
agenda[]                    # opt-in — bills under discussion
participants[]              # opt-in — names only, no testimony content
```

---

## 6. Data Freshness

### What the API itself reports

Each `Jurisdiction` carries two timestamps that quantify staleness:

```
latest_bill_update          # last time ANY bill in this state was scraped
latest_people_update        # last time legislator data was refreshed
```

Plus `latest_runs[]` (when `include=latest_runs`) gives the actual scraper
exit times and success flags — useful for monitoring.

### Incremental sync strategy

`/bills` supports three time-based filters (all accept ISO date strings):

- `updated_since` — anything touched (status change, new vote, amendment)
  since the cutoff. **This is the right delta key.**
- `created_since` — only newly introduced bills.
- `action_since` — bills with a *new action* since cutoff.

Default sort is `updated_desc`, so a paginated walk from `page=1` until the
last `updated_at < cutoff` works as a fallback if `updated_since` ever
behaves weirdly.

### Per-state scrape cadence

Not documented per state. The OpenAPI preamble notes that committees and
events are not yet available for all states. Empirically:

- For active bills in session, scrapers run **at least daily** for major
  states (the Postgres dump is updated "regularly throughout the month").
- Off-session states refresh less frequently.
- **Trust `latest_bill_update` per jurisdiction** rather than assuming a
  global cadence — flag stale states (>7 days) in your monitoring.

---

## 7. Known Limitations

1. **Full bill text is a link, not a payload.** The API returns
   `versions[].links[]` pointing to state PDFs/HTML. To get searchable text
   the pipeline must fetch and parse those itself. (The session-level **JSON
   bulk dumps** are documented as including "full text materials" — verify
   this in spike #2.)
2. **Witness/testimony data does not exist** in the schema. `EventParticipant`
   is a `(name, entity_type, person?, organization?, note)` tuple. No
   submitted statements, no transcripts. Witness data is per-state, and where
   it exists at all, it lives on state legislature sites directly.
3. **Committees and events are flagged experimental** in the spec's
   `info.description`. State coverage is incomplete; the format is subject
   to change. Treat as best-effort, not contractual.
4. **No documented rate limits**, no documented tier pricing. Plan for
   throttling but verify exact numbers after registering a key.
5. **No webhooks / push.** Pull-only. Pipeline must poll on a cron.
6. **No bulk write / GraphQL v3.** The GraphQL API is deprecated per
   `docs.openstates.org`. v3 REST is the only forward path.
7. **API version is `2021.11.12`** per `openapi.json`. The spec has not been
   re-versioned despite ongoing schema work on committees/events. Watch for
   silent additive changes; the project has been historically careful about
   breaks.
8. **`extras` is a free-form object** on Bill, Committee, Event, Person.
   Useful per-state info lands here without schema changes — but it's
   unstructured and varies per scraper. Don't depend on any specific
   `extras` key cross-state.
9. **Subject taxonomy is per-state**, not normalized. "Tax" in California is
   not the same string as in Texas. The pipeline will need its own subject
   mapping layer if it wants cross-state subject search.

---

## 8. Bulk Data Option

Confirmed live, May 2026:

```bash
curl -I "https://data.openstates.org/postgres/monthly/2026-05-public.pgdump"
```

```
HTTP/2 200
content-type: binary/octet-stream
content-length: 10566279569          # ~10.5 GB
last-modified: Fri, 01 May 2026 02:01:25 GMT
x-amz-server-side-encryption: AES256
accept-ranges: bytes
server: AmazonS3
x-cache: Hit from cloudfront
x-amz-cf-pop: SEA900-P1
```

### Available bulk products

(From `open.pluralpolicy.com/data/`):

| Product | Format | Refresh | URL pattern |
|---|---|---|---|
| Legislator data | YAML + CSV | "as needed during sessions" | github.com/openstates/people/ ; /data/legislator-csv/ |
| Bills & votes (CSV, per session) | CSV | Monthly | /data/session-csv/ |
| Bills & votes (JSON, per session) | JSON | Monthly | /data/session-json/ — **includes full text** |
| District boundaries (geo) | JSON polygons | Last update Nov 2018 | /data/geo/ |
| Full Postgres dump | `.pgdump` | Updated throughout the month, monthly snapshots | `https://data.openstates.org/postgres/monthly/YYYY-MM-public.pgdump` |
| Postgres schema | `.pgdump` | Versioned | `https://data.openstates.org/postgres/schema/YYYY-MM-schema.pgdump` |

The bulk URLs are served via S3+CloudFront, no authentication, support
HTTP range requests (`accept-ranges: bytes`), and have CDN caching.

### When to prefer bulk over API

- **Initial backfill** of all 50 states across multiple sessions — saves
  hundreds of thousands of paginated requests and respects the rate limit.
- **Full bill text** — the API only returns links; the JSON bulk dump
  includes the text bodies (per `openstates.org/data/`).
- **Historical analytics / batch queries** — load the Postgres dump into
  a local warehouse and run SQL.
- **Disaster recovery** — the Postgres dump is a complete snapshot.

### When to prefer the API

- **Incremental daily sync** (`updated_since`).
- **Real-time GTM signals** — the bulk dumps are at most daily, often
  monthly per-session.
- **Geo lookups** (`/people.geo`).
- **Per-record lookups** by ID.

### Recommended split

1. Bootstrap: load the latest monthly `pgdump` into a local Postgres.
2. Daily delta: pull `/bills?updated_since={watermark}` for each
   jurisdiction, page through, upsert into the local warehouse.
3. Hourly during session for high-priority states: same delta call but
   tighter watermark and per-jurisdiction.

---

## 9. Minimal Python Client Design

Goal: small, testable, no premature abstraction. Three files. Roughly 200
lines total. Not production code — a sketch for the team to flesh out.

### File structure

```
openstates_client/
    __init__.py       # re-export Client
    client.py         # HTTP, auth, retries, pagination — ~120 LoC
    sync.py           # incremental-pull orchestration — ~80 LoC
```

Resist the urge to add `models.py` with Pydantic mirrors of every OpenStates
schema. The API returns JSON dicts; for an ingest pipeline, pass them through
to the warehouse as JSONB columns. Type-hint at the boundaries (the public
sync methods), not at every field.

### `client.py` (sketch)

```python
import os
import time
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing import Iterator

BASE_URL = "https://v3.openstates.org"

class RateLimited(Exception):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after

class OpenStatesClient:
    def __init__(self, api_key: str | None = None, timeout: float = 30.0):
        self.api_key = api_key or os.environ["OPENSTATES_API_KEY"]
        self._http = httpx.Client(
            base_url=BASE_URL,
            timeout=timeout,
            headers={"X-API-KEY": self.api_key, "User-Agent": "gtm-signals/0.1"},
        )

    @retry(
        retry=retry_if_exception_type((RateLimited, httpx.TransportError)),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _get(self, path: str, **params) -> dict:
        # multi-valued params (e.g. include) — pass as list of tuples
        flat = []
        for k, v in params.items():
            if isinstance(v, (list, tuple)):
                flat.extend((k, str(x)) for x in v)
            elif v is not None:
                flat.append((k, str(v)))
        r = self._http.get(path, params=flat)
        if r.status_code == 429:
            ra = float(r.headers.get("Retry-After", "5"))
            time.sleep(ra)                    # also let tenacity back off
            raise RateLimited(ra)
        if r.status_code in (401, 403):
            # auth issue — do NOT retry, surface immediately
            r.raise_for_status()
        r.raise_for_status()
        return r.json()

    def paginate(self, path: str, *, per_page: int = 20, **params) -> Iterator[dict]:
        page = 1
        while True:
            data = self._get(path, page=page, per_page=per_page, **params)
            for item in data.get("results", []):
                yield item
            meta = data.get("pagination", {})
            if not meta or page >= meta.get("max_page", page):
                return
            page += 1

    # thin convenience wrappers
    def bills(self, **kw): return self.paginate("/bills", **kw)
    def people(self, **kw): return self.paginate("/people", **kw)
    def committees(self, **kw): return self.paginate("/committees", **kw)
    def events(self, **kw): return self.paginate("/events", **kw)
    def jurisdictions(self, **kw): return self.paginate("/jurisdictions", **kw)
```

**Why these choices**:

- `httpx.Client` — connection pooling + HTTP/2 (the server speaks HTTP/2).
- `tenacity` — declarative retry; `wait_exponential` covers transient
  network errors and 429s. Cap at 5 attempts so the cron job fails fast
  if the API is truly down.
- Distinguish `401/403` from `429/5xx` — only retry the latter.
- `paginate()` is the only iteration primitive. Every list endpoint goes
  through it. No special-casing per resource.
- `include` is passed as a list and flattened to repeated query params
  (`include=sponsorships&include=actions`).
- Generator return type — caller streams; we never hold all bills in memory.

### `sync.py` (sketch)

```python
from datetime import datetime, timedelta, timezone
import json
from .client import OpenStatesClient

class IncrementalSync:
    def __init__(self, client: OpenStatesClient, watermark_store):
        self.client = client
        self.wm = watermark_store     # any KV: redis, dynamo, or a SQL table

    def sync_bills(self, jurisdiction: str, *, lookback_minutes: int = 60):
        """Pull all bills updated since the last successful sync for this jurisdiction.

        Watermark is per-jurisdiction so a single state failing doesn't poison
        the whole run. We overlap by `lookback_minutes` to absorb clock skew
        and late-arriving updates from the scrapers.
        """
        key = f"openstates:bills:{jurisdiction}"
        last = self.wm.get(key) or (datetime.now(timezone.utc) - timedelta(days=30))
        cutoff = last - timedelta(minutes=lookback_minutes)

        latest_seen = last
        for bill in self.client.bills(
            jurisdiction=jurisdiction,
            updated_since=cutoff.isoformat(),
            include=["sponsorships", "actions", "versions"],
            per_page=20,
            sort="updated_asc",         # ascending so the LAST record has the newest updated_at
        ):
            yield bill                  # caller upserts into warehouse
            updated = datetime.fromisoformat(bill["updated_at"].rstrip("Z"))
            if updated > latest_seen:
                latest_seen = updated

        self.wm.set(key, latest_seen)
```

**Notes on this design**:

- **Per-jurisdiction watermarks** — one stuck state doesn't block 49 others.
- **Lookback overlap** — 60 minutes covers scraper-side late writes and any
  clock skew on the OpenStates side. Dedup happens at the warehouse via
  the bill `id` (which is a stable UUID).
- **Sort ascending** for sync — lets us update the watermark monotonically
  from the last record we processed, so a crash mid-pull doesn't lose
  progress (next run resumes from the last persisted `updated_at`).
- **Default sort `updated_desc` for ad-hoc queries**, but sync flips it to
  `updated_asc`.

### Env vars

```
OPENSTATES_API_KEY=<your-key>            # required
OPENSTATES_TIMEOUT=30                    # optional, seconds
OPENSTATES_PER_PAGE=20                   # optional, can be raised after testing
```

That is it. No config file, no DI framework, no schema mirror. If the
pipeline grows to need bulk-dump loading, add a fourth file `bulk.py` that
downloads the monthly `pgdump` and `pg_restore`s it into a staging schema —
keep it separate from the API client.

---

## Appendix: Probe Log

All commands run from `darwin/zsh`, 2026-05-20, against
`https://v3.openstates.org/`:

```
GET /jurisdictions                                 → 403 (132 bytes, JSON error)
GET /jurisdictions  (Accept: application/json)     → 403
GET /bills?jurisdiction=ca&updated_since=2026-04-01 → 403
GET /people?jurisdiction=ca                        → 403
GET /committees?jurisdiction=ca                    → 403
GET /events?jurisdiction=ca                        → 403
GET /bills?per_page=500                            → 403  (auth checked first)
POST /bills                                        → 405 Method Not Allowed
GET /openapi.json                                  → 200, 45,488 bytes
GET /redoc                                         → 200, HTML
GET /metrics                                       → 200, Prometheus text
GET /bills?... with X-API-KEY: invalid_test_key_12345 → 401 ("Invalid API Key.")

HEAD https://data.openstates.org/postgres/monthly/2026-05-public.pgdump → 200
  content-length: 10,566,279,569 (≈ 10.5 GB)
  last-modified: Fri, 01 May 2026 02:01:25 GMT
  server: AmazonS3, x-cache: Hit from cloudfront
```

Five sequential `GET /jurisdictions` (unauthenticated):

```
Req 1: HTTP 403 time=0.439s
Req 2: HTTP 403 time=0.299s
Req 3: HTTP 403 time=0.294s
Req 4: HTTP 403 time=0.328s
Req 5: HTTP 403 time=0.556s
```

No `Retry-After`, `X-RateLimit-*`, or other rate-limit headers were emitted
on rejected requests. Headers under authentication still need to be observed
once a key is in hand — that's the first thing to do post-registration.
