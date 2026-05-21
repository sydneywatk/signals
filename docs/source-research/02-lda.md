# Source spike: Federal Lobbying Disclosure Act (LDA) API

Research date: 2026-05-20. All probes executed against `https://lda.senate.gov/api/v1/`
without authentication. Real curl responses transcribed inline.

The authoritative spec is published as YAML at
`GET https://lda.senate.gov/api/openapi/v1/` (`Content-Type:
application/vnd.oai.openapi`, ~11,800 lines / 20 KB raw). This document cross-checks
that spec against live behavior and flags discrepancies.

---

## TL;DR

- **Base URL:** `https://lda.senate.gov/api/v1/` — Django REST Framework JSON API,
  gzip by default. **Successor URL is `https://lda.gov/api/v1/` and the Senate URL
  sunsets 2026-06-30** (per `Sunset` / `Deprecation` / `Link: rel="successor-version"`
  headers on every response). Migration is mandatory.
- **Auth:** `Authorization: Token <key>` (DRF style). Anonymous works for every read
  endpoint but is heavily throttled. Registration is **free** at
  `https://lda.senate.gov/api/register/` (email + name + password, 10+ chars).
  Tokens can be fetched programmatically via `POST /api/auth/login/` with
  username/password.
- **Rate limits (official):** Anonymous **15/minute (900/hour)**, registered
  **120/minute (7,200/hour)**, per RFC 9745. Anonymous limit hit empirically at
  request ~22 in ~5 seconds. Responses use 429 with `Retry-After: <sec>` header and
  body `{"detail":"Request was throttled. Expected available in N seconds."}`.
  **Constants and document-print URLs do NOT count against quota.**
- **Pagination is page+offset.** `count`, `next`, `previous` envelope. **`page_size`
  is officially and silently capped at 25** — you can request 500 and get 25 back
  with the wrong number echoed in `next`. This makes full-year backfills (~108K
  filings) ~4,340 requests = ~36 min at the authenticated rate.
- **Filings + Contributions require at least one query param** beyond `page` to
  paginate past page 1 (performance guard). Use `filing_year` as the cheap one.
- **50 distinct `filing_type` codes:** `RR`=LD-1 registration, `Q1..Q4`=LD-2
  quarterly reports, `MM/YY`=legacy semiannual, plus amendments (`?A`), terminations
  (`?T`), termination-amendments (`?@`), and no-activity variants (`?Y`).
- **Issue-area filtering works two ways:** 79 controlled `general_issue_code`
  values (HCR=Health, TAX=Taxation, PHA=Pharmacy, DEF=Defense, …) AND server-side
  search on the free-text `specific_issues` field via
  `filing_specific_lobbying_issues=<text>`. The text search **supports phrase
  quoting, explicit `OR`, and `-` NOT operators** per the spec — verified live.
- **Near-real-time freshness.** Filings appear within minutes of acceptance.
  Watermark via `filing_dt_posted_after=YYYY-MM-DD&ordering=dt_posted`.
- **No bulk CSV/ZIP export.** The OPR explicitly directs anyone wanting "all reports"
  to the REST API. Backfill = paginate.
- **Fit for GTM pipeline:** Strong. Data model is exactly what you want (registrant
  + ID, client + ID, lobbyist roster, income/expenses, issue codes, free-text issue
  descriptions, posted timestamp). The looming 2026-06-30 cutover and the
  25-result page cap are the only real frictions.

---

## 1. Authentication

### Scheme
Standard Django REST Framework token authentication.

```
Authorization: Token z944b09199c62bcf9418ad846dd0e4bbdfc6ee4b
```

Probed with a bogus token:

```
$ curl -H "Authorization: Token thisisnotrealtoken" \
       https://lda.senate.gov/api/v1/filings/?page_size=1
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Token
Content-Type: application/json

{"detail":"Invalid token."}
```

`WWW-Authenticate: Token` confirms the scheme name.

### Registration is free, no tiers, no payment
1. POST to `https://lda.senate.gov/api/register/` (or via the web form there) with
   `email`, `first_name`, `last_name`, `username`, and a 10+ char mixed-case password.
2. Email verification.
3. Fetch the token via either the web form at `/api/auth/login/` **or** an API call:

   ```http
   POST https://lda.senate.gov/api/auth/login/
   Content-Type: application/json

   {"username": "...", "password": "..."}
   ```
   ```json
   {"key": "z944b09199c62bcf9418ad846dd0e4bbdfc6ee4b"}
   ```

4. There are **no paid tiers** — anonymous and registered are the only options.
5. **For post-2026-06-30:** re-register at `https://lda.gov/` — tokens almost certainly
   do not migrate across hosts.

### Anonymous vs registered (from the spec)
- Anonymous limit is rate-limited **by originating IP**.
- Registered limit is **per user**, shared across multiple keys held by the same user.
- See §2 for numbers.

---

## 2. Rate limits

### Official numbers (from the OpenAPI description)

| Auth tier        | Per minute | Per hour |
| ---------------- | ---------- | -------- |
| Anonymous        | 15         | 900      |
| API Key (registered) | 120    | 7,200    |

These were raised from 1,000/hour to 7,200/hour in 2021 and converted from per-hour
to per-minute in 2023 ("to reduce burstable requests").

### Empirical 429 behavior

Bursting unauthenticated GETs to `/api/v1/filings/?page=N&page_size=1`:

```
req 1..22 -> 200
req 23    -> 429
```

```
HTTP/1.1 429 Too Many Requests
Retry-After: 10
Content-Type: application/json

{"detail":"Request was throttled. Expected available in 4 seconds."}
```

Note: `Retry-After: 10` and the body's "4 seconds" disagree by design. The header is
a fixed-window hint; the body is the live wait. **Trust the body.** Use a regex on
`detail` to extract the integer, fall back to the header.

### Endpoints that do NOT count against quota

Per the spec, the following do **not** consume rate-limit budget:

- Original filing documents:
  - `https://lda.senate.gov/filings/public/filing/{filing_uuid}/print/`
  - `https://lda.senate.gov/filings/public/contribution/{filing_uuid}/print/`
- All `constants/*` endpoints (`/api/v1/constants/filing/...`, `/constants/general/...`,
  `/constants/lobbyist/...`).

This means you can liberally cache constants and fetch full filing PDFs/HTMLs without
worrying about the 120/min cap. Plan your enrichment around this.

### Missing headers

No `X-RateLimit-*`, `X-Throttle-*`, or similar. You only know you're approaching the
limit when 429 arrives. Build a request budget client-side.

### Always-present response headers

```
Content-Type: application/json
Content-Encoding: gzip                    (with Accept-Encoding allowed)
Allow: GET, HEAD, OPTIONS                 (GET only on list endpoints)
deprecation: @1768003199                  (RFC 9745, epoch -> 2026-01-09 UTC, already past)
sunset: Tue, 30 Jun 2026 23:59:59 GMT     (RFC 8594)
Link: <https://lda.gov/api/v1/>; rel="successor-version",
      <https://lda.senate.gov/api/redoc/v1/>; rel="help"
x-frame-options: DENY
x-content-type-options: nosniff
referrer-policy: same-origin
strict-transport-security: max-age=31536000
```

The `Link: rel="successor-version"` is RFC 8288. A robust client can auto-follow this
during the migration window.

---

## 3. Endpoint catalog

`GET https://lda.senate.gov/api/v1/` returns the root index:

```json
{
  "filings":                                  ".../api/v1/filings/",
  "contributions":                            ".../api/v1/contributions/",
  "registrants":                              ".../api/v1/registrants/",
  "clients":                                  ".../api/v1/clients/",
  "lobbyists":                                ".../api/v1/lobbyists/",
  "constants/filing/filingtypes":             ".../constants/filing/filingtypes/",
  "constants/filing/lobbyingactivityissues":  ".../constants/filing/lobbyingactivityissues/",
  "constants/filing/governmententities":      ".../constants/filing/governmententities/",
  "constants/general/countries":              ".../constants/general/countries/",
  "constants/general/states":                 ".../constants/general/states/",
  "constants/lobbyist/prefixes":              ".../constants/lobbyist/prefixes/",
  "constants/lobbyist/suffixes":              ".../constants/lobbyist/suffixes/",
  "constants/contribution/itemtypes":         ".../constants/contribution/itemtypes/"
}
```

| Resource        | Total rows (probed)              | Primary key                | Detail URL                              |
| --------------- | -------------------------------- | -------------------------- | --------------------------------------- |
| filings         | **1,947,495** total / 108,547 in 2025 | `filing_uuid` (UUID v4) | `/filings/{filing_uuid}/`               |
| contributions   | 39,890 in 2025                   | `filing_uuid` (UUID v4)    | `/contributions/{filing_uuid}/`         |
| registrants     | 17,312                           | integer `id`               | `/registrants/{id}/`                    |
| clients         | 134,930                          | integer `id` + `client_id` | `/clients/{id}/`                        |
| lobbyists       | 87,919                           | integer `id`               | `/lobbyists/{id}/`                      |
| constants/*     | small enums                      | `value` (string) or `id`   | (list-only, no detail)                  |

### 3.1 `/filings/` — LD-1 + LD-2 (the main GTM signal)

#### Confirmed-working query params

| Param                                  | Type        | Notes                                                  |
| -------------------------------------- | ----------- | ------------------------------------------------------ |
| `filing_year`                          | int         | Single year. Repeated param keeps the LAST.            |
| `filing_type`                          | enum string | One of 50 codes (see §6.0).                            |
| `filing_period`                        | enum string | `first_quarter`, `second_quarter`, `third_quarter`, `fourth_quarter`, `mid_year`, `year_end` |
| `filing_specific_lobbying_issues`      | string      | **Full-text search** — supports quotes, OR, NOT.       |
| `filing_general_issue_area_code`       | enum string | One of 79 codes (see §6.1).                            |
| `filing_dt_posted_after`               | YYYY-MM-DD  | Date precision. **Use for incremental sync.**          |
| `filing_dt_posted_before`              | YYYY-MM-DD  | Date precision. For bounded backfills.                 |
| `registrant_name`                      | string      | Substring match, case-insensitive.                     |
| `client_name`                          | string      | Substring match, case-insensitive.                     |
| `registrant_id`                        | int         | Exact match.                                           |
| `client_id`                            | int         | Exact match.                                           |
| `lobbyist_conviction_disclosure`       | string      | Full-text search (same operators).                     |
| `lobbyist_covered_position`            | string      | Full-text search (same operators).                     |
| `ordering`                             | string      | `dt_posted`, `-dt_posted`, multiple via comma. `id` was removed in 2023 for perf. |
| `page`                                 | int         | 1-indexed.                                             |
| `page_size`                            | int         | **Hard-capped at 25** despite no error (see §4).       |

**Param silently ignored if unknown.** I verified `lobbyist_name`, `lobbyist_first_name`,
`lobbyist_last_name` on `/filings/` — all returned the full corpus count (1,947,495)
with no warning. **Always sanity-check `count` against a no-filter baseline.**

#### LD-1 registrations (`filing_type=RR`)

```
$ curl --compressed "https://lda.senate.gov/api/v1/filings/?filing_year=2025&filing_type=RR&page_size=2"
```

Response (one result, truncated):

```json
{
  "count": 6964,
  "next": "https://lda.senate.gov/api/v1/filings/?filing_type=RR&filing_year=2025&page=2&page_size=2",
  "previous": null,
  "results": [{
    "url": "https://lda.senate.gov/api/v1/filings/4f33e46d-4018-4899-8926-c03bb9977ae2/",
    "filing_uuid": "4f33e46d-4018-4899-8926-c03bb9977ae2",
    "filing_type": "RR",
    "filing_type_display": "Registration",
    "filing_year": 2025,
    "filing_period": "first_quarter",
    "filing_period_display": "1st Quarter (Jan 1 - Mar 31)",
    "filing_document_url": "https://lda.senate.gov/filings/public/filing/4f33e46d-.../print/",
    "filing_document_content_type": "text/html",
    "income": null,
    "expenses": null,
    "expenses_method": null,
    "posted_by_name": "John Roberti",
    "dt_posted": "2025-01-01T12:03:08-05:00",
    "termination_date": null,
    "registrant": {
      "id": 401109030, "house_registrant_id": 56732,
      "name": "COVENANT GOVERNMENT AFFAIRS, LLC",
      "address_1": "38 S Blue Angel Pkwy", "city": "Pensacola", "state": "FL", "zip": "32506",
      "country_display": "United States of America",
      "contact_name": "RACHAEL AUGHTMAN", "contact_telephone": "+1 615-601-4694",
      "dt_updated": "2026-03-02T14:20:13.351139-05:00"
    },
    "client": {
      "id": 63333, "client_id": 63333,
      "name": "FUSE INTEGRATION, INC.",
      "general_description": "Manufacturing communications systems",
      "state": "CA", "ppb_state": "CA",
      "effective_date": "2025-01-01"
    },
    "lobbying_activities": [
      {"general_issue_code":"BUD","general_issue_code_display":"Budget/Appropriations",
       "description":"FY26 defense appropriations and authorizations",
       "lobbyists":[{"lobbyist":{"id":148053,"first_name":"JOHN","last_name":"ROBERTI"},
                     "covered_position":null,"new":true}],
       "government_entities":[]},
      {"general_issue_code":"DEF","..." :"..."}
    ],
    "conviction_disclosures": [],
    "foreign_entities": [],
    "affiliated_organizations": []
  }]
}
```

6,964 new registrations in 2025. The `lobbying_activities[].lobbyists[].new`
field is a Tier-1 GTM hook: `true` means first-time lobbyist for this
registrant.

#### LD-2 quarterly reports (`filing_type=Q1`)

```
$ curl --compressed "https://lda.senate.gov/api/v1/filings/?filing_year=2025&filing_type=Q1&page_size=1"
```

```json
{
  "count": 19905,
  "results": [{
    "filing_uuid": "1b1e0584-7e23-4f4d-a41d-141aa86190b8",
    "filing_type": "Q1",
    "filing_type_display": "1st Quarter - Report",
    "filing_year": 2025,
    "expenses": "100000.00",
    "expenses_method": "A",
    "posted_by_name": "Ryan Shay",
    "dt_posted": "2025-01-14T14:52:17-05:00",
    "registrant": {"name":"LEGO SYSTEMS, INC.", "id":401107919, "house_registrant_id":56234},
    "client":     {"name":"LEGO SYSTEMS, INC.", "id":57269, "client_self_select":true},
    "lobbying_activities": [
      {"general_issue_code":"ENV","general_issue_code_display":"Environment/Superfund",
       "description":"Monitor issues related to sustainability and climate change.",
       "foreign_entity_issues":"LEGO A/S, the parent company of LEGO Systems, Inc., have aligned interests.",
       "lobbyists":[
         {"lobbyist":{"id":146721,"first_name":"RYAN","last_name":"SHAY"},
          "covered_position":"Leg. Dir., Rep. Susie Lee (D-NV); Sr. Leg. Asst & Leg. Asst, Rep. Andre Carson (D-IN); Leg. Aide & Leg. Corrs., Sen. Maria Cantwell (D-WA); Staff Asst, Senate Committee on Energy & Natural Resources",
          "new":false},
         {"lobbyist":{"id":144098,"first_name":"ANA","middle_name":"CAROLINA","last_name":"GIUGA"},
          "covered_position":null,"new":false}],
       "government_entities":[{"id":2,"name":"HOUSE OF REPRESENTATIVES"},
                              {"id":1,"name":"SENATE"}]},
      {"general_issue_code":"CSP","..." :"..."},
      {"general_issue_code":"EDU","..." :"..."},
      {"general_issue_code":"SCI","description":"Monitor issues related to children's online safety and privacy","..." :"..."},
      {"general_issue_code":"TRD","..." :"..."}
    ]
  }]
}
```

19,905 Q1 2025 reports. LEGO's Q1 alone has 5 activity rows covering 5 different
issue codes — typical for a self-filer.

#### Full-text search hit (issue area + phrase)

```
$ curl --compressed "https://lda.senate.gov/api/v1/filings/?filing_year=2025&filing_specific_lobbying_issues=insulin&page_size=1"
```

```json
{
  "count": 104,
  "results": [{
    "filing_uuid": "92b65e33-4aa9-441c-be76-12467ae25778",
    "filing_type": "Q1",
    "registrant": {"name":"T1INTERNATIONAL USA", "description":"Patient support & advocacy"},
    "client":     {"name":"T1INTERNATIONAL USA"},
    "lobbying_activities":[{
      "general_issue_code": "HCR",
      "description": "Issues related to the following bills: S. 1040, regarding product hopping; S. 1041, regarding patent evergreening; S. 1095, the Stop STALLING Act; S. 1096, the Preserve Access to Affordable Generics and Biosimilars Act; and a to-be-introduced bill on insulin copays in line with The INSULIN Act of 2023 (S. 1269) and Affordable Insulin Now Act of 2023 (S. 954)",
      "foreign_entity_issues": "",
      "lobbyists":[
        {"lobbyist":{"id":119495,"first_name":"SHAINA","last_name":"KASPER"}},
        {"lobbyist":{"id":100460,"first_name":"ALLISON","last_name":"HARDT"}}
      ],
      "government_entities":[{"id":2,"name":"HOUSE OF REPRESENTATIVES"},
                             {"id":1,"name":"SENATE"}]
    }]
  }]
}
```

104 filings mention "insulin" in 2025. See §6.2 for operator verification.

#### Detail endpoint
```
GET /api/v1/filings/{filing_uuid}/
```
Returns the **exact same shape** as a list result. No additional fields. The list
endpoint is fully expanded; there is no "summary vs detail" distinction. This means
you almost never need to hit detail (saves rate limit).

### 3.2 `/contributions/` — LD-203 contributions

```
$ curl --compressed "https://lda.senate.gov/api/v1/contributions/?filing_year=2025&page_size=1"
```

```json
{
  "count": 39890,
  "results": [{
    "filing_uuid": "082b5ec0-e293-4bbe-9ee0-62ce64171941",
    "filing_type": "MM",
    "filing_period": "mid_year",
    "dt_posted": "2025-01-09T12:23:17-05:00",
    "filer_type": "lobbyist",
    "filer_type_display": "Lobbyist",
    "registrant": {"id":401105458,"name":"FDD ACTION"},
    "lobbyist":   {"id":146703,"first_name":"CONNOR","middle_name":"RUSSELL","last_name":"PFEIFFER"},
    "no_contributions": true,
    "pacs": [],
    "contribution_items": []
  }]
}
```

LD-203 is filed semi-annually. `filer_type` is `lobbyist` (per-individual) or
`registrant` (firm-level) — the same period+entity will appear **twice**, once per
filer type. **Filter `no_contributions: false`** unless you want nil filings.

### 3.3 `/registrants/`, `/clients/`, `/lobbyists/`

Reference catalogs. Use when you want lookup by id or want to slice without dragging
the heavy `lobbying_activities` array.

```
$ curl --compressed "https://lda.senate.gov/api/v1/registrants/?registrant_name=akin&page_size=1"
{"count":1,"results":[{"id":682,"house_registrant_id":31784,
 "name":"AKIN GUMP STRAUSS HAUER & FELD","description":"Law firm",
 "city":"WASHINGTON","state":"DC","zip":"20006"}]}
```

```
$ curl --compressed "https://lda.senate.gov/api/v1/clients/?client_name=pfizer&page_size=1"
{"count":87,"results":[{"id":52095,"client_id":52095,"name":"PFIZER, INC.",
 "general_description":"Pharmaceutical Company","state":"NY","ppb_state":"DC",
 "effective_date":"2022-06-02",
 "registrant":{"id":401105775,"name":"RUTLEDGE POLICY GROUP, LLC", "...":"..."}}]}
```

Pfizer has **87 client records** — same real-world company, multiple `client.id`s,
one per registrant relationship. Entity resolution is on you.

```
$ curl --compressed "https://lda.senate.gov/api/v1/lobbyists/?lobbyist_name=schumer&page_size=1"
{"count":2,"results":[{"id":34928,"first_name":"LAUREN","last_name":"SCHUMER", "...":"..."}]}
```

Two lobbyists named Schumer (neither is the Senator). **NB:** the matching param is
`lobbyist_name` on `/lobbyists/`. The same word as `lobbyist_last_name` is **silently
ignored** (returns full corpus 87,919).

### 3.4 `/constants/*` — vocabulary tables

Enumerations. Free to call (don't count against quota). Cache on startup; they
change a few times per year.

| Endpoint                                      | Rows  | Used for                                  |
| --------------------------------------------- | ----- | ----------------------------------------- |
| `constants/filing/filingtypes/`               | 50    | `filing_type` enum (§6.0)                 |
| `constants/filing/lobbyingactivityissues/`    | 79    | `general_issue_code` enum (§6.1)          |
| `constants/filing/governmententities/`        | ~250  | `government_entities[].id` lookup         |
| `constants/general/countries/`                | ~250  | `country` two-letter codes                |
| `constants/general/states/`                   | ~60   | US states + DC + territories              |
| `constants/lobbyist/prefixes/`                | ~10   | "MR.", "MS.", "DR." …                     |
| `constants/lobbyist/suffixes/`                | ~10   | "JR.", "III" …                            |
| `constants/contribution/itemtypes/`           | ~10   | LD-203 contribution_items[].type          |

---

## 4. Pagination model

Standard **page + page_size offset pagination** (DRF `PageNumberPagination`).
Not cursor-based.

```json
{
  "count": 19905,
  "next": "https://lda.senate.gov/api/v1/filings/?filing_type=Q1&filing_year=2025&page=2&page_size=1",
  "previous": null,
  "results": [...]
}
```

- `count` is the exact total.
- `next` / `previous` are absolute URLs you can pass straight to `requests.get()` —
  they echo all your query params back.
- Page 2 example:

  ```
  $ curl --compressed "https://lda.senate.gov/api/v1/filings/?filing_year=2025&filing_type=RR&page=2&page_size=1"
  {"count":6964,
   "next":"https://lda.senate.gov/api/v1/filings/?filing_type=RR&filing_year=2025&page=3&page_size=1",
   "previous":"https://lda.senate.gov/api/v1/filings/?filing_type=RR&filing_year=2025&page_size=1",
   "results": [...]}
  ```
  Page 1 is the bare URL (no `page=` in `previous`).

### Two critical pagination caveats

1. **`page_size` is silently capped at 25.** The spec says "up to 25"; the server
   does not error if you ask for more — it just returns 25.

   ```
   $ curl --compressed "https://lda.senate.gov/api/v1/filings/?filing_year=2025&page_size=500"
   count returned: 108547
   actual results in page: 25                    <-- capped
   next: "...?filing_year=2025&page=2&page_size=500"   <-- but echoes 500
   ```

   **Implication:** full-year 2025 backfill = `ceil(108547/25)` = **4,342 requests**.
   At the 120/min authenticated rate, that's ~36 minutes pure-paginate, more in
   practice with retry slack. Plan storage and resumability accordingly.

2. **Filings and Contributions require at least one query param to paginate beyond
   page 1.** From the spec (verbatim):

   > The `Filings` and `Contribution Reports` endpoints require at least one
   > queryset parameter to be passed in order to paginate results beyond the first
   > page. This is for performance reasons.

   Recommendation from the spec itself: paginate by `filing_year`. Confirmed
   live — `?filing_year=2025&page=2` works fine.

### No total-pages field
Compute as `ceil(count / 25)`. Never rely on iterating until `next == null` for
worst-case planning, but do use it as the loop termination condition.

### Ordering
- Default order varies by endpoint and is unspecified.
- Set explicitly with `ordering=dt_posted` (ascending) or `-dt_posted` (descending),
  multiple keys via comma: `ordering=dt_posted,registrant__name`.
- Several orderings were **removed in 2023** for performance:
  - Filing: `id`
  - Contribution Report: `id`
  - Client: `registrant_name`
  - Lobbyist: everything except `id`
- For incremental sync, **always use `ordering=dt_posted`** (ascending). If the
  pipeline dies mid-stream you can resume without skipping unseen rows.

---

## 5. Pipeline-relevant fields (the full GTM payload)

Every list result is a fully expanded record. Key fields:

### Top-level filing fields

| Field                        | Type             | Notes                                                                                  |
| ---------------------------- | ---------------- | -------------------------------------------------------------------------------------- |
| `filing_uuid`                | UUID v4          | **Primary key.**                                                                       |
| `url`                        | string           | API self-link.                                                                         |
| `filing_type`                | string enum      | 50 codes (§6.0).                                                                       |
| `filing_type_display`        | string           | Human label.                                                                           |
| `filing_year`                | int              | YYYY.                                                                                  |
| `filing_period`              | string enum      | `first_quarter` / `second_quarter` / `third_quarter` / `fourth_quarter` / `mid_year` / `year_end` |
| `filing_period_display`      | string           | e.g. `"1st Quarter (Jan 1 - Mar 31)"`.                                                 |
| `filing_document_url`        | URL              | Link to the original PDF or HTML (does **not** count against rate limit).              |
| `filing_document_content_type`| string          | `text/html` (new e-filings) or `application/pdf` (legacy).                             |
| `income`                     | decimal string\| | LD-2 only. e.g. `"20000.00"`. Often null when registrant reports expenses instead.     |
| `expenses`                   | decimal string\| | LD-2 only. e.g. `"100000.00"`. Often null on LD-1.                                     |
| `expenses_method`            | string           | `A` = LDA method (rounded to $10K), `B`/`C` = IRC methods. `A` dominates.              |
| `posted_by_name`             | string           | Free-text name of the human who filed.                                                 |
| `dt_posted`                  | ISO 8601 + offset| e.g. `2025-01-14T14:52:17-05:00`. **Sync watermark.**                                  |
| `termination_date`           | date \| null     | Set on `?T` and `?@` filing types.                                                     |
| `registrant`                 | object           | Embedded firm record.                                                                  |
| `client`                     | object           | Embedded client record.                                                                |
| `lobbying_activities`        | array            | Per-activity rows. The main signal carrier.                                            |
| `conviction_disclosures`     | array            | LD-1 only. Usually empty.                                                              |
| `foreign_entities`           | array            | LD-1 only. Foreign parents/owners. Empty for ~99% of filings.                          |
| `affiliated_organizations`   | array            | LD-1 only. Related orgs.                                                               |

### `registrant` (the lobbying firm)

`id`, `url`, `house_registrant_id` (int|null — joins to the House clerk's separate
ID system), `name`, `description`, `address_1..4`, `city`, `state`, `state_display`,
`zip`, `country`, `country_display`, `ppb_country` (principal place of business
country), `contact_name`, `contact_telephone` (E.164), `dt_updated` (registrant
profile last-modified — different from filing's `dt_posted`).

### `client` (the customer paying for lobbying)

`id`, `url`, `client_id`, `name`, `general_description`, `client_government_entity`
(bool|null — true when the client is a government entity), `client_self_select`
(bool — true when registrant is lobbying for itself, e.g. LEGO), `state`, `country`,
`ppb_state`, `ppb_country`, `effective_date` (registrant↔client relationship start).

### `lobbying_activities[]` — the activity rows

Per row:
- `general_issue_code` (string, 3 chars, one of 79 — §6.1).
- `general_issue_code_display` (human label).
- `description` — **the free-text "specific issues" field**. This is what
  `filing_specific_lobbying_issues=` searches.
- `foreign_entity_issues` (string|null — narrative about foreign interests).
- `lobbyists[]` — `[{lobbyist:{id, prefix, first_name, middle_name, last_name,
  suffix}, covered_position, new}]`.
- `government_entities[]` — `[{id, name}]` (Senate, House, EPA, USTR, …).

### What is NOT in the response

- No PAC/contribution detail — that's `/contributions/`.
- No per-lobbyist dollar split or hours.
- No client NAICS code or canonical industry classification — only the free-text
  `general_description`.
- No parent/child relationship between original filings and amendments — must be
  reconstructed client-side.

---

## 6. Issue area filtering

### 6.0 The 50 `filing_type` codes

From `GET /api/v1/constants/filing/filingtypes/`. Pattern:

```
RR  Registration                          (LD-1, new client/registrant)
RA  Registration - Amendment              (LD-1 fix)

Q1  1st Quarter - Report                  (LD-2, the main quarterly)
Q1Y 1st Quarter - Report (No Activity)    (nil filing)
1T  1st Quarter - Termination
1TY 1st Quarter - Termination (No Activity)
1A  1st Quarter - Amendment
1AY 1st Quarter - Amendment (No Activity)
1@  1st Quarter - Termination Amendment
1@Y 1st Quarter - Termination Amendment (No Activity)

Q2/Q3/Q4 and 2T/3T/4T, 2A/3A/4A, 2@/3@/4@ — same pattern, other quarters

MM  Mid-Year Report                       (legacy semiannual, pre-2008)
MT/MA/M@ (mid-year termination/amendment variants)

YY  Year-End Report                       (legacy semiannual, pre-2008)
YT/YA/Y@ (year-end termination/amendment variants)
```

**Suffix `Y` universally means "(No Activity)"** — a nil filing. For GTM purposes,
filter these out (`filing_type` not ending in `Y`); they carry empty
`lobbying_activities[]`.

### 6.1 The 79 controlled `general_issue_code` values

From `GET /api/v1/constants/filing/lobbyingactivityissues/`. Pipeline-relevant ones
in **bold**:

| Code   | Name                                        | Code | Name                                          |
| ------ | ------------------------------------------- | ---- | --------------------------------------------- |
| ACC    | Accounting                                  | IMM  | Immigration                                   |
| ADV    | Advertising                                 | IND  | Indian/Native American Affairs                |
| AER    | Aerospace                                   | INS  | Insurance                                     |
| AGR    | Agriculture                                 | INT  | Intelligence                                  |
| ALC    | Alcohol and Drug Abuse                      | LBR  | Labor Issues/Antitrust/Workplace              |
| ANI    | Animals                                     | LAW  | Law Enforcement/Crime/Criminal Justice        |
| APP    | Apparel/Clothing Industry/Textiles          | MAN  | Manufacturing                                 |
| ART    | Arts/Entertainment                          | MAR  | Marine/Maritime/Boating/Fisheries             |
| AUT    | Automotive Industry                         | MIA  | Media (information/publishing)                |
| AVI    | Aviation/Airlines/Airports                  | MED  | Medical/Disease Research/Clinical Labs        |
| BAN    | Banking                                     | **MMM** | **Medicare/Medicaid**                       |
| BNK    | Bankruptcy                                  | MON  | Minting/Money/Gold Standard                   |
| BEV    | Beverage Industry                           | NAT  | Natural Resources                             |
| BUD    | Budget/Appropriations                       | **PHA** | **Pharmacy**                                |
| CIV    | Civil Rights/Civil Liberties                | POS  | Postal                                        |
| CHM    | Chemicals/Chemical Industry                 | RRR  | Railroads                                     |
| CAW    | Clean Air and Water (quality)               | RES  | Real Estate/Land Use/Conservation             |
| CDT    | Commodities (big ticket)                    | REL  | Religion                                      |
| COM    | Communications/Broadcasting/Radio/TV        | RET  | Retirement                                    |
| CPI    | Computer Industry                           | ROD  | Roads/Highway                                 |
| CON    | Constitution                                | SCI  | Science/Technology                            |
| CSP    | Consumer Issues/Safety/Products             | SMB  | Small Business                                |
| CPT    | Copyright/Patent/Trademark                  | SPO  | Sports/Athletics                              |
| DEF    | Defense                                     | TAR  | Tariff (miscellaneous tariff bills)           |
| DIS    | Disaster Planning/Emergencies               | **TAX** | **Taxation/Internal Revenue Code**          |
| DOC    | District of Columbia                        | TEC  | Telecommunications                            |
| ECN    | Economics/Economic Development              | TOB  | Tobacco                                       |
| EDU    | Education                                   | TOR  | Torts                                         |
| ENG    | Energy/Nuclear                              | TRD  | Trade (domestic/foreign)                      |
| ENV    | Environment/Superfund                       | TRA  | Transportation                                |
| FAM    | Family issues/Abortion/Adoption             | TOU  | Travel/Tourism                                |
| FIN    | Financial Institutions/Investments/Securities | TRU | Trucking/Shipping                            |
| FIR    | Firearms/Guns/Ammunition                    | URB  | Urban Development/Municipalities              |
| FOO    | Food Industry (safety, labeling, etc.)      | UNM  | Unemployment                                  |
| FOR    | Foreign Relations                           | UTI  | Utilities                                     |
| FUE    | Fuel/Gas/Oil                                | VET  | Veterans                                      |
| GAM    | Gaming/Gambling/Casino                      | WAS  | Waste (hazardous/solid/interstate/nuclear)    |
| GOV    | Government Issues                           | WEL  | Welfare                                       |
| **HCR**| **Health Issues**                           | HOM  | Homeland Security                             |
| HOU    | Housing                                     |      |                                               |

**Pipeline-relevant codes for the user's domains:**
- **Health / pharma:** `HCR` (Health Issues), `PHA` (Pharmacy), `MMM`
  (Medicare/Medicaid), `MED` (Medical/Disease Research/Clinical Labs), `INS`
  (Insurance — health plans file here, but so does property/casualty).
- **Taxation:** `TAX` (the only code). `BUD` overlaps for spending side; serious
  tax-policy filings often tag both.
- **Trade / tariffs:** `TRD`, `TAR`.

#### Filter syntax — one code at a time
```
?filing_general_issue_area_code=HCR    -> 108,547 filings in 2025
?filing_general_issue_area_code=PHA    -> works (returns Pharmacy)
```
**Repeating the param is not supported** — only one value per query. Union
client-side if you need multi-code OR.

#### How issue codes map onto a filing
Issue codes live on each `lobbying_activities[]` row, not on the filing as a whole.
A single quarterly report can list 10+ activities across different codes (LEGO's
Q1 spanned ENV, CSP, EDU, SCI, TRD). The `filing_general_issue_area_code` filter
returns filings where **at least one activity** matches. A "health" hit may be 1 of
10 activities, the other 9 unrelated.

### 6.2 Free-text `specific_issues` search

```
?filing_specific_lobbying_issues=<query>
```

Searches the per-activity `description` field. **Supports advanced operators** —
per the spec verbatim:

> - **Unquoted Text** - Text not inside quote marks will not be treated as a phrase.
>   Text separated by a space is treated as an OR operator between them. *Estate Tax*
>   will match *estate* OR *tax* even if they do not appear next to each other.
> - **"Quoted Text"** - Text inside double quote marks will be treated as a phrase.
> - **OR** - The word *OR* will be treated as a true OR operator.
> - **-** - The dash will be treated as a NOT EQUALS operator. *"Estate Tax"
>   -"Payroll Taxes"* will match *estate tax* but not *payroll taxes*.

Verified live (2026-05-20):

| Query                              | URL-encoded value                       | `count` |
| ---------------------------------- | --------------------------------------- | ------- |
| `insulin`                          | `insulin`                               | 104     |
| `insulin diabetes`                 | `insulin+diabetes`                      | **40**  |
| `"insulin copays"`                 | `%22insulin+copays%22`                  | 1       |
| `insulin -Medicare`                | `insulin+-Medicare`                     | 75      |

**Observation worth noting:** The spec describes unquoted-space as OR, but empirically
`insulin diabetes` returned **40** filings (less than `insulin` alone at 104). If the
spec were strictly applied, OR-of-two-terms should be ≥104. Either the server is
treating space as AND in practice, or the matcher is more nuanced than the docs
suggest (e.g., proximity / phrase prefix). **For predictable behavior, always use
explicit operators:**

```
filing_specific_lobbying_issues="insulin" OR "diabetes"      # explicit OR
filing_specific_lobbying_issues="insulin"                    # exact phrase
filing_specific_lobbying_issues="insulin" -"medicare"        # NOT
```

The same operators apply to two other fields per the spec:
- `lobbyist_conviction_disclosure`
- `lobbyist_covered_position`

### 6.3 Combining filters

Filters AND across each other. Layered queries are the right primitive:

```
?filing_year=2026
 &filing_general_issue_area_code=HCR
 &filing_specific_lobbying_issues="GLP-1"
 &filing_dt_posted_after=2026-04-01
 &ordering=dt_posted
```

This is the canonical "new healthcare filings mentioning GLP-1 since April"
GTM query.

### 6.4 Government entity filtering
Each activity carries `government_entities[]`. The `/filings/` endpoint does **not**
expose a top-level `government_entity` filter (verified — silently ignored). To slice
by, say, "filings targeting FDA," fetch by issue code + year, then filter
`government_entities[].id` client-side. The ID lookup comes from
`/constants/filing/governmententities/`.

---

## 7. Data freshness

- `dt_posted` is when the filing was accepted into the public record (timezone is
  Eastern, with DST — values end in `-05:00` (EST) or `-04:00` (EDT)).
- **Most recent observed during this probe:** `2026-05-20T16:18:44-04:00` — a Q3
  termination filed ~15 minutes before our probe at 17:00 local. **No batch lag.**
- The system streams continuously throughout business hours.
- **Filing-deadline burstiness:** huge spikes on the 20th of January/April/July/October
  (LD-2 quarterly deadlines) and on the 30/31 of July/January (LD-203 deadlines).
  Expect 1,000+ filings per hour on deadline days.
- **Amendments** (`?A`, `?@`) are independent rows with their own `filing_uuid`.
  The original filing's `dt_posted` is unchanged when an amendment lands.

#### Incremental sync probe

```
$ curl --compressed "https://lda.senate.gov/api/v1/filings/?filing_dt_posted_after=2026-05-15&page_size=1"
{"count":491,"results":[{"dt_posted":"2026-05-15T01:46:02-04:00","..." :"..."}]}
```

491 filings between 2026-05-15 00:00 and probe time (2026-05-20 17:03) — ~80-100/day
in an off-deadline week. Quarterly weeks will be 10-100x that.

---

## 8. Known limitations & quirks

1. **2026-06-30 sunset.** Both `deprecation` (already past) and `sunset` headers point
   to `lda.gov`. Build all URLs from a configurable base. Re-register for a token on
   the new host before the cutover.
2. **Default-on gzip.** API returns `Content-Encoding: gzip` even when you don't send
   `Accept-Encoding`. Curl shows mojibake unless you pass `--compressed`. Python
   `requests`/`httpx` handle this transparently.
3. **`page_size` silently capped at 25.** Don't trust the echoed value in `next` —
   always verify `len(results) <= 25`. This is the single biggest gotcha.
4. **Filings/Contributions reject page>1 without a query filter** (for performance).
   Always paginate with at least `filing_year=`.
5. **Unknown filter params are silently ignored** — endpoint returns the unfiltered
   corpus instead of an error. Sanity-check `count` against an unfiltered baseline
   when developing.
6. **Repeated same-name param keeps the LAST value.** `?filing_year=2024&filing_year=2025`
   = 2025 only (`count: 108547` matches `?filing_year=2025` alone).
7. **`dt_posted: 1905-06-24` on some legacy records.** Filing #1 in an unfiltered scan
   has `filing_year: 1999` but `dt_posted: 1905-06-24T00:00:00-05:00`. Always filter
   by `filing_year` before sorting chronologically.
8. **Mixed date formats internally:**
   - `dt_posted`: ISO 8601 with offset, no microseconds — `2025-01-14T14:52:17-05:00`.
   - `dt_updated`: ISO 8601 with microseconds and offset — `2026-03-02T14:20:13.351139-05:00`.
   - `effective_date`, `termination_date`: plain `YYYY-MM-DD`, no time.
   - `filing_dt_posted_after` filter accepts only `YYYY-MM-DD` (date precision).
9. **Free-text `description` quality is uneven.** Examples range from rich (bill
   numbers like "S. 1040, regarding product hopping") to useless ("Monitor issues
   related to sustainability"). Single-token and explicit-phrase searches work
   reliably; unquoted multi-token has surprising behavior (see §6.2).
10. **`covered_position` is free text** — semicolon-separated narrative like
    `"Leg. Dir., Rep. Susie Lee (D-NV); Sr. Leg. Asst, Rep. Andre Carson (D-IN); ..."`.
    Parsing is a Tier-2 enrichment.
11. **Pre-2021-02-14 filings have filing-level government entities, not activity-level.**
    Per the spec: "Filings posted before 2/14/2021 do not have government entities
    broken down by each individual lobbying activity area." Older filings list one
    aggregate set on the filing as a whole.
12. **`client_id` duplication.** The integer `client.id` is the new system row ID;
    `client.client_id` is the legacy ID. For new records they're equal
    (e.g. 63333 == 63333). For legacy records they diverge (e.g.
    `id: 202998, client_id: 12`). Use `id` for joins inside the API; `client_id`
    only for legacy joins.
13. **Pfizer has 87 client rows.** Same real-world company across multiple registrant
    relationships and historical renames. **Entity resolution is on you** — there is
    no canonical "Company" entity. Naive normalized-name + state matching is a
    starting point but will collide on common names ("ABC Holdings").
14. **`registrant.house_registrant_id` is sometimes null** even for active firms.
    The House clerk's separate registration process means the Senate API only
    carries the link when it knows it.
15. **Amendments are independent rows.** No parent link from amendment to original.
    To compute the "current state" of a filing, group by
    `(registrant_id, client_id, filing_year, filing_period)` and pick the latest
    `dt_posted`. `filing_type` codes (`?T`/`?A`/`?@`) tell you what kind of update
    each row is.
16. **Encoding quirks in name fields.** Trailing spaces, double spaces, all-caps,
    odd punctuation (`"925 L ST   #1404"`). Normalize before deduping.
17. **`filer_type` doubles LD-203 rows.** Same registrant + same period appears
    twice: once `filer_type: "registrant"` (firm-level), once per
    `filer_type: "lobbyist"`. Don't double-count.
18. **`no_contributions: true` records dominate LD-203.** Nil filings — filter out
    for GTM unless you specifically want activity-presence signals.
19. **No webhooks, no subscriptions.** Polling only. Watermark on
    `filing_dt_posted_after`.
20. **`ordering=id` is gone** on Filings and Contribution Reports (removed 2023 for
    perf). Use `dt_posted` for stable enumeration.

---

## 9. Bulk data option

**There isn't one** on the LDA Senate endpoint. The public-facing system page
(`https://lda.senate.gov/system/public/`) explicitly says "If you want all reports,
use the REST API." There are no CSV/ZIP archive links.

What you can do:
- **Paginate the API.** At `page_size=25` (the actual cap) and the authenticated
  120/min rate, you can pull ~3,000 filings/min = ~180K/hour. A full 2025 corpus
  (~108K filings) takes ~36 minutes pure-paginate.
- **Third-party mirrors** (not officially endorsed):
  - **OpenSecrets** (`opensecrets.org/bulk-data`) republishes processed CSV
    extracts on a quarterly cadence, with their own normalization layer. Faster
    for historical backfill but lags real-time and applies their entity resolution
    (which may help or hurt depending on your needs).
  - **ProPublica** historically mirrored similar data; verify currency.

For incremental real-time monitoring, the API is the right primitive. For full
historical backfill where you don't care about ProPublica/OpenSecrets normalization,
the API is also viable — it's just an evening of throttled paginate-and-store.

---

## 10. Minimal Python client design

### File structure

```
lda_client/
├── __init__.py
├── client.py        # LDAClient — auth, retry, pagination
├── search.py        # Helpers for the advanced text-search operator syntax
├── models.py        # dataclasses for Filing, Activity, Registrant, Client, Lobbyist
├── sync.py          # Incremental sync orchestrator (dt_posted watermark)
├── constants.py     # Cached enum mirrors; refreshed monthly
└── config.py        # Env loading
```

### `config.py`

```python
import os
from dataclasses import dataclass

@dataclass(frozen=True)
class LDAConfig:
    base_url: str = os.getenv("LDA_BASE_URL", "https://lda.senate.gov/api/v1")
    token: str | None = os.getenv("LDA_API_TOKEN")
    user_agent: str = os.getenv("LDA_UA", "gtm-signal-pipeline/0.1 (ops@example.com)")
    timeout_seconds: float = float(os.getenv("LDA_TIMEOUT", "30"))
    max_retries: int = int(os.getenv("LDA_MAX_RETRIES", "5"))
    # page_size is officially capped at 25 — don't bother asking for more.
    page_size: int = int(os.getenv("LDA_PAGE_SIZE", "25"))
    # Soft client-side budget: stay under 100/min to leave headroom under 120/min cap.
    requests_per_minute: int = int(os.getenv("LDA_RPM", "100"))
```

After 2026-06-30: `LDA_BASE_URL=https://lda.gov/api/v1`.

### `client.py` — request layer

```python
import re
import time
import logging
import httpx
from .config import LDAConfig

log = logging.getLogger(__name__)
_WAIT_RE = re.compile(r"(\d+)\s+seconds?")


class LDAClient:
    def __init__(self, cfg: LDAConfig):
        self.cfg = cfg
        headers = {"User-Agent": cfg.user_agent, "Accept": "application/json"}
        if cfg.token:
            headers["Authorization"] = f"Token {cfg.token}"
        # httpx auto-decompresses gzip (LDA always returns gzip).
        self.http = httpx.Client(
            base_url=cfg.base_url,
            headers=headers,
            timeout=cfg.timeout_seconds,
            follow_redirects=False,
        )
        # Simple in-process token bucket: budget per minute.
        self._bucket = []  # timestamps of recent requests

    def _throttle_self(self):
        now = time.monotonic()
        # Drop entries older than 60s.
        self._bucket = [t for t in self._bucket if now - t < 60]
        if len(self._bucket) >= self.cfg.requests_per_minute:
            wait = 60 - (now - self._bucket[0]) + 0.1
            log.debug("Self-throttle sleeping %.2fs", wait)
            time.sleep(wait)
        self._bucket.append(time.monotonic())

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET with 429-aware retry. Returns parsed JSON."""
        for attempt in range(self.cfg.max_retries):
            self._throttle_self()
            resp = self.http.get(path, params=params)
            if resp.status_code == 429:
                wait = self._extract_wait(resp)
                log.warning("LDA throttled, sleeping %ss (attempt %d)", wait, attempt + 1)
                time.sleep(wait)
                continue
            if 500 <= resp.status_code < 600:
                wait = min(60, 2 ** attempt)
                log.warning("LDA %d, sleeping %ss", resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"LDA failed after {self.cfg.max_retries} retries: {path} {params}")

    @staticmethod
    def _extract_wait(resp) -> float:
        # Body says "Expected available in N seconds." — prefer that.
        try:
            m = _WAIT_RE.search(resp.json().get("detail", ""))
            if m:
                return float(m.group(1)) + 0.5
        except Exception:
            pass
        return float(resp.headers.get("Retry-After", "10"))

    def paginate(self, path: str, params: dict):
        """Yield each result dict across all pages. Page-offset model."""
        params = dict(params)
        params.setdefault("page_size", self.cfg.page_size)
        # First page.
        body = self.get(path, params)
        for item in body["results"]:
            yield item
        # Subsequent pages: follow `next` URL verbatim (it echoes query params).
        url = body.get("next")
        while url:
            body = self.get(url)
            for item in body["results"]:
                yield item
            url = body.get("next")
```

Key design choices:
- **Self-throttle on the client side** to stay under 120/min — avoids the 429 spiral.
- **Trust the JSON body's wait time** over the `Retry-After` header (they disagree).
- **Follow `next` verbatim** for pagination — it's an absolute URL with all params
  baked in, so don't try to re-derive them.
- **No `page_size > 25`** — it would only add false confidence; cap is silent.

### `sync.py` — incremental sync via `dt_posted` watermark

```python
from datetime import datetime, timedelta


class FilingSync:
    """Pulls new filings since the last watermark. Idempotent on filing_uuid."""

    def __init__(self, client: LDAClient, store):
        self.client = client
        self.store = store  # your DB: load_watermark() / save_watermark() / upsert(filing)

    def run(self, extra_filters: dict | None = None):
        # Date-precision watermark, with a 1-day overlap to absorb late arrivals
        # and quietly-edited amendments. Upsert by filing_uuid makes overlap free.
        wm = self.store.load_watermark()  # "YYYY-MM-DD"
        cursor = (datetime.fromisoformat(wm) - timedelta(days=1)).date().isoformat()

        params = {
            "filing_dt_posted_after": cursor,
            "ordering": "dt_posted",  # ASC — resumable if we crash
        }
        if extra_filters:
            params.update(extra_filters)

        max_seen = wm
        for filing in self.client.paginate("/filings/", params):
            self.store.upsert(filing)  # key = filing_uuid
            ts = filing["dt_posted"]
            if ts > max_seen:
                max_seen = ts
        # Persist as date — filter is date-precision.
        self.store.save_watermark(max_seen[:10])
```

Why a 1-day overlap: `filing_dt_posted_after` is date-precision (no support for
intra-day timestamps). Backing up 1 day costs ~80 extra rows in slow weeks (~5K in
deadline weeks) and is free with `UPSERT ON CONFLICT (filing_uuid) DO UPDATE`.

#### Pagination model summary

- **Page-offset, not cursor.** Iterate by following `body["next"]` until `None`.
- **Sort with `ordering=dt_posted` (ascending)** when watermarking — never
  `-dt_posted` for incremental, because crashes would leave the newest unseen rows
  skipped on resume.
- **Page size is locked at 25 regardless of what you ask for** — plan request
  budgets accordingly.

### Issue-area-targeted streams

```python
# Healthcare GTM signal: every new health-related filing since yesterday
for f in sync.run(extra_filters={"filing_general_issue_area_code": "HCR"}):
    pass

# Tax policy with an explicit phrase
params = {
    "filing_year": 2026,
    "filing_general_issue_area_code": "TAX",
    "filing_specific_lobbying_issues": '"carried interest"',  # quoted phrase
}
for f in client.paginate("/filings/", params):
    enrich_and_alert(f)

# Cross-issue with NOT
params = {
    "filing_year": 2026,
    "filing_specific_lobbying_issues": '"insulin" -"Medicare"',
}
```

### Suggested storage schema

```sql
CREATE TABLE filings (
  filing_uuid           UUID        PRIMARY KEY,
  filing_type           TEXT        NOT NULL,
  filing_year           INT         NOT NULL,
  filing_period         TEXT        NOT NULL,
  dt_posted             TIMESTAMPTZ NOT NULL,
  termination_date      DATE,
  registrant_id         INT         NOT NULL,
  client_id             INT         NOT NULL,
  income_cents          BIGINT,
  expenses_cents        BIGINT,
  expenses_method       TEXT,
  filing_document_url   TEXT,
  raw                   JSONB       NOT NULL  -- full API response for re-derivation
);

CREATE INDEX ix_filings_dt_posted ON filings (dt_posted);
CREATE INDEX ix_filings_year_type ON filings (filing_year, filing_type);

CREATE TABLE lobbying_activities (
  filing_uuid          UUID NOT NULL REFERENCES filings(filing_uuid),
  idx                  INT  NOT NULL,
  general_issue_code   TEXT NOT NULL,
  description          TEXT,
  foreign_entity_issues TEXT,
  government_entity_ids INT[],
  PRIMARY KEY (filing_uuid, idx)
);
CREATE INDEX ix_activities_issue ON lobbying_activities (general_issue_code);
CREATE INDEX ix_activities_descr_trgm ON lobbying_activities USING GIN (description gin_trgm_ops);

CREATE TABLE activity_lobbyists (
  filing_uuid       UUID NOT NULL,
  activity_idx      INT  NOT NULL,
  lobbyist_id       INT  NOT NULL,
  covered_position  TEXT,
  is_new            BOOL,
  PRIMARY KEY (filing_uuid, activity_idx, lobbyist_id)
);

CREATE TABLE registrants (id INT PRIMARY KEY, name TEXT, house_registrant_id INT, raw JSONB);
CREATE TABLE clients     (id INT PRIMARY KEY, client_id INT, name TEXT, raw JSONB);
CREATE TABLE lobbyists   (id INT PRIMARY KEY, first_name TEXT, last_name TEXT, raw JSONB);
```

Keeping `raw JSONB` is cheap (rows are 2-5 KB each — full 2025 corpus is ~500 MB
uncompressed, ~120 MB on disk with Postgres compression) and means schema migrations
don't require a re-pull.

---

## Appendix A — Always-present response headers

```
Content-Type: application/json
Allow: GET, HEAD, OPTIONS    (GET only on list endpoints)
Content-Encoding: gzip
deprecation: @1768003199
sunset: Tue, 30 Jun 2026 23:59:59 GMT
Link: <https://lda.gov/api/v1/>; rel="successor-version",
      <https://lda.senate.gov/api/redoc/v1/>; rel="help"
x-frame-options: DENY
x-content-type-options: nosniff
referrer-policy: same-origin
cross-origin-opener-policy: same-origin
strict-transport-security: max-age=31536000
```

## Appendix B — Quick reference: "how do I get X?"

| Want                                                                  | Query                                                                                  |
| --------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| All new LD-1 registrations in 2025                                    | `/filings/?filing_year=2025&filing_type=RR`                                            |
| All Q1 2025 quarterlies                                               | `/filings/?filing_year=2025&filing_type=Q1`                                            |
| Every health-tagged filing in 2025                                    | `/filings/?filing_year=2025&filing_general_issue_area_code=HCR`                        |
| Filings mentioning a phrase                                           | `/filings/?filing_specific_lobbying_issues=%22insulin+copays%22`                       |
| Filings about insulin but NOT Medicare                                | `/filings/?filing_specific_lobbying_issues=%22insulin%22+-%22medicare%22`              |
| New filings since yesterday (sync)                                    | `/filings/?filing_dt_posted_after=2026-05-19&ordering=dt_posted`                       |
| All filings for a known client                                        | `/filings/?client_id=52095`                                                            |
| All Pfizer-named clients (entity-resolution problem)                  | `/clients/?client_name=pfizer`                                                         |
| A specific lobbyist by name                                           | `/lobbyists/?lobbyist_name=schumer`                                                    |
| A registrant by name                                                  | `/registrants/?registrant_name=akin`                                                   |
| Enum values for filter params                                         | `/constants/filing/filingtypes/` and `/constants/filing/lobbyingactivityissues/`       |
| LD-203 contributions for 2025                                         | `/contributions/?filing_year=2025`                                                     |
| Original PDF/HTML of a filing (free, no rate-limit cost)              | `https://lda.senate.gov/filings/public/filing/{filing_uuid}/print/`                    |

## Appendix C — Changes log from the official spec

For completeness, the version history embedded in the OpenAPI description:

| Date       | Description                                                                                    |
| ---------- | ---------------------------------------------------------------------------------------------- |
| 2025-07-17 | Added deprecation/sunset headers for lda.senate.gov. Sunset 2026-06-30.                        |
| 2024-05-09 | Clarified date format (`YYYY-MM-DD`) and constants information.                                |
| 2024-01-18 | Added limitations/caveats section.                                                             |
| 2023-08-08 | Changed throttling from per-hour to per-minute. New limits: anon 15/min, registered 120/min.   |
| 2023-04-05 | Added requirement: Filings/Contributions need ≥1 query param to paginate past page 1.          |
| 2023-04-05 | Removed orderings for perf. Filing: `id`; ContribReport: `id`; Client: `registrant_name`; Lobbyist: all but `id`. |
| 2022-12-14 | Added registrant address fields to filing endpoint.                                            |
| 2022-03-28 | Fixed LD-2 income/expenses display issue for 2/15-3/28/2022 filings.                           |
| 2022-01-24 | Added advanced text searching on `filing_specific_lobbying_issues`, `lobbyist_conviction_disclosure`, `lobbyist_covered_position`. |
| 2022-01-13 | Fixed LD-203 contribution_items mismatch with original document.                               |
| 2021-07-30 | Added Lobbyist endpoint.                                                                       |
| 2021-03-10 | Decreased pagination to 25/page. Increased throttle to 1,000/hr anon, 20,000/hr registered.    |
