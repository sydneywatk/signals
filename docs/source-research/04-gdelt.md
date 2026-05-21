# 04 — GDELT Doc 2.0 API

Source spike for the GTM signal pipeline. Goal: validate whether the GDELT Doc
2.0 API can deliver (a) news coverage volume timelines on policy topics like
"drug pricing" and "PBM reform", (b) state-level filtering for US articles, and
(c) clean article lists with URLs, titles, sources.

Probes were run on **2026-05-20** against
`https://api.gdeltproject.org/api/v2/doc/doc`. All response samples and probe
URLs in this doc are from those live calls. Raw payloads are in
`/Users/sydneywatkins/source-research/.probes/`.

---

## TL;DR

- **Doc 2.0 is free, keyless, and returns useful volume + article data for
  policy-topic monitoring.** Phrase searches like `"drug pricing"` return
  highly relevant US article streams and per-day volume timelines. Latest
  `seendate` we observed (`20260520T221500Z`) was ~30 minutes before the probe
  ran, consistent with GDELT's 15-min update cadence.
- **Doc 2.0 does NOT do state-level filtering.** There is no `sourcelocation:`
  operator (we tested it — it silently fails as a keyword). The only
  geo-filter on the Doc API is `sourcecountry:` (publisher country) and the
  text-proximity `near:` operator, which is NOT a geo filter. For real
  state-level filtering you must drop to the **GKG BigQuery dataset**, where
  `V2Locations` carries ADM1 codes like `USTX`.
- **Rate limits are aggressive and undocumented.** First-call `429`s are
  common. The throttle message says "one request every 5 seconds" but in
  practice you need a real `User-Agent` header AND ~10-15s spacing under
  bursty conditions. The API can also return `{}` (HTTP 200, 2 bytes) instead
  of an error — silent-empty is the biggest footgun.
- **Fit for the pipeline:** GOOD for national/topic-level volume + article
  surfacing. INSUFFICIENT for state-level rollups — plan on dual sourcing
  (Doc API for fresh national, GKG BigQuery for state-tagged historical).

---

## Auth

**None.** It's a public unauthenticated REST endpoint. No API key, no OAuth, no
tokens. You only need:

1. A working `User-Agent` header (see Rate Limits — without one, GDELT
   short-circuits with a 429-style throttle string even on the first call).
2. URL encoding of the query string.

No env vars, no secrets to manage. This is a major operational win.

---

## Rate Limits

GDELT does not publish a formal rate limit. The throttle response we got says
literally:

```
Please limit requests to one every 5 seconds or contact
kalev.leetaru5@gmail.com for larger queries.
```

That message comes back with **HTTP 200** (yes, 200, not 429 — though we also
saw 429 with the same body), 102 bytes, plain text. Empirical findings from
this probe session:

- **First request with the default curl User-Agent was 429.** Adding a real
  browser-style `User-Agent` (we used a Chrome string) made the same URL
  return 200 with valid JSON. This matches the lore in
  https://github.com/alex9smith/gdelt-doc-api/issues/22.
- Even with a good UA, back-to-back requests at 7-8s spacing **still
  triggered 429 about 30% of the time** during this session. The probe runner
  had to retry up to 4 times on some calls with 12-18s sleeps.
- The GDELT blog post
  https://blog.gdeltproject.org/behind-the-scenes-api-quotas-the-impact-of-a-fraction-of-a-qps/
  is candid that the limit is enforced "at three decimal places of QPS" and
  changes by event traffic. There is no fixed QPS to design against.
- Their own recommendation for high-volume use is to drop to the **Web NGrams
  3.0 downloadable dataset** or the **GKG on BigQuery**, not to hammer the
  Doc API.

**Operational rules of thumb from this spike:**

| Behavior | Status code | Body | Treat as |
|----------|------------|------|----------|
| Throttle string | 200 or 429 | "Please limit requests..." (102 B) | Retry after 15s |
| Valid response | 200 | JSON object with the expected mode key | Success |
| Silent empty | 200 | `{}` exactly (2 B) | **Retry once, then trust as legitimate zero-result** |
| Query error | 200 | "One or more of your keywords were too short..." or "Your query contained an invalid location search..." | Bad query — surface to caller, do NOT retry |

Bake all four shapes into the client retry logic. The silent-empty is the one
that will burn you most.

---

## Modes

Tested in this spike. Mode goes in `&mode=` and changes both the response
shape and the data layer GDELT runs against. From the official docs at
https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/ plus our probes:

| Mode | Purpose | Probe | Notes |
|------|---------|-------|-------|
| `artlist` | Article list (URL, title, seendate, domain, sourcecountry, language) | p01, p06, p09, p17 | Default `maxrecords=75`, max **250**. Default sort `hybridrel`. |
| `artgallery` | Same as artlist but with image-forward layout (HTML focus) | not probed | HTML/JSON only; same underlying data as artlist |
| `timelinevol` | Volume intensity over time (percent of all GDELT coverage matching the query) | p02 | Normalized 0-100 scale; daily resolution at `timespan=1m` |
| `timelinevolraw` | Raw article counts per bucket + corpus-size norm | p03 | Returns both `value` (count) and `norm` (corpus size that bucket) — ratio gives volume intensity |
| `timelinevolinfo` | Like timelinevol but includes a top article per time bucket | p20 | Hourly resolution at `timespan=7d`. Big payload (~26 KB for 7d). |
| `timelinetone` | Average tone over time | not probed | Same shape as timelinevol but `value` is tone (-10..+10) |
| `timelinelang` | Volume broken out by language | not probed | One series per language |
| `timelinesourcecountry` | Volume broken out by source country | not probed | One series per country |
| `tonechart` | Histogram of articles bucketed by tone (-10..+10) with top articles per bucket | p04 | Big payload (~18 KB). Useful for "is coverage skewing negative?" |
| `imagecollage` / `imagecollageinfo` | Image search results | not probed | Requires `imagetag:` / `imageocrmeta:` operators |
| `wordcloudImageTags`, `wordcloudImageWebTags` | Word cloud over image tags | not probed | HTML-oriented |

For this pipeline the relevant set is **`artlist` + `timelinevol` +
`timelinevolraw` + `timelinevolinfo` + `tonechart`**.

---

## Output formats

`&format=` parameter. Tested:

- `json` — the workhorse. UTF-8, no BOM, single top-level object whose key
  matches the mode (`{"articles":[...]}`, `{"timeline":[...]}`,
  `{"tonechart":[...]}`).
- `csv` (p21) — `URL,MobileURL,Date,Title` columns for `artlist`. **Has a
  UTF-8 BOM** (`\xEF\xBB\xBF`) on the first line. Date format is
  `YYYY-MM-DD HH:MM:SS` (not the JSON-shape `YYYYMMDDTHHMMSSZ`).
- `html` — default if you omit format; ignored for the pipeline.
- `jsonp`, `rss`, `rssarchive`, `jsonfeed` — exist; not relevant here.

Recommendation: stick with `format=json` everywhere. CSV is fine for
ArticleList only.

---

## Operator cheat sheet (with verdicts)

The Doc 2.0 query language is more like Elastic-search-style search-string
than URL parameters. Operators go **inside the `query=` value**, space-
separated, AND-combined. Example:
`query="drug pricing" sourcecountry:US theme:HEALTH_DRUGS_AND_MEDICATIONS`.

| Operator | Form tested | Verdict | Evidence |
|----------|------------|---------|----------|
| Exact phrase | `"drug pricing"` | **WORKED** | p01 returned 5303 B of clean drug-pricing articles |
| Boolean OR | `(clinton OR sanders OR trump)` | DOC-CONFIRMED, not probed | Documented at blog.gdeltproject.org |
| Negation | `-trump` | DOC-CONFIRMED, not probed | Documented |
| `sourcecountry:` | `sourcecountry:US` (no quotes) | **WORKED** | p06 returned valid US-publisher article list |
| `sourcelang:` | `sourcelang:English` | DOC-CONFIRMED, not probed | Standard FIPS/ISO codes |
| `domain:` | `domain:cnn.com` | DOC-CONFIRMED, not probed | Substring match — beware `notactuallycnn.com` |
| `domainis:` | `domainis:cnn.com` | DOC-CONFIRMED, not probed | Exact-match variant |
| `theme:` | `theme:TERROR` | **WORKED** | p18 returned 2949 B of articles |
| `theme:` (specific to our use case) | `theme:HEALTH_DRUGS_AND_MEDICATIONS` | **SILENTLY EMPTY** | p05/p05b/p05c all returned `{}` — that theme name does not match GKG taxonomy, but API does NOT error. |
| `theme:` (gouging) | `theme:ECON_PRICEGOUGE` | **SILENTLY EMPTY** | p19 returned `{}` |
| `near[N]:` | `near20:"drug Texas"` | **WORKED but NOT a geo filter** | p08 returned articles from Louisiana, California, etc. that mention "drug" near "Texas" in *text*. Text proximity only. |
| `repeat[N]:` | `repeat3:"melania" trump` | DOC-CONFIRMED, not probed | Documented; useful for "this article is actually about X" |
| `tone>N` / `tone<N` | `tone>5` | DOC-CONFIRMED, not probed | Numeric only |
| `toneabs>N` | `toneabs>10` | DOC-CONFIRMED, not probed | Absolute value |
| `imagetag:` and friends | n/a | DOC-CONFIRMED, not probed | Vision-AI image features |
| `sourcelocation:` (state name) | `sourcelocation:"Texas"` | **ERRORED — operator does not exist** | p07/p14/p15 all returned: `One or more of your keywords were too short, too long or too common: (sourcelocation:0)`. The parser treats the whole `sourcelocation:"Texas"` as a single keyword. |
| `sourcelocation:` (FIPS state code) | `sourcelocation:USTX` | **ERRORED** | p11 returned `Your query contained an invalid location search (the search keyword/phrase must be enclosed in quotation marks).` Adding quotes (p13) gave the same "too short" error as above — `sourcelocation` is **not a real operator**. |
| `location:` (generic) | `location:"Texas"` | **ERRORED** | p16 returned `One or more of your keywords were too short, too long or too common: (location:0).` Same story. |

### Make-or-break finding

> **There is no working US-state filter operator on the Doc 2.0 API.** Both
> `sourcelocation:` and `location:` look like operators (they have colons,
> they show up in some third-party clients and tutorials) but the Doc 2.0
> parser does not recognize them and falls through to keyword treatment,
> which then rejects them as too-common or too-short tokens. Don't trust
> any tutorial that claims `sourcelocation:` works on Doc 2.0 — it doesn't.
> See the State-level filtering verdict section below.

---

## Response shapes (JSON)

### `mode=artlist`

```json
{
  "articles": [
    {
      "url": "https://www.jdsupra.com/legalnews/healthcare-life-sciences-drug-pricing-6826333/",
      "url_mobile": "",
      "title": "Healthcare & Life Sciences : Drug Pricing Digest - May 2026 # 2 | Latham & Watkins LLP",
      "seendate": "20260520T221500Z",
      "socialimage": "https://jdsupra-static.s3.amazonaws.com/profile-images/og.15361_4613.jpg",
      "domain": "jdsupra.com",
      "language": "English",
      "sourcecountry": "United States"
    }
  ]
}
```

Per-article fields: `url`, `url_mobile`, `title`, `seendate` (UTC,
`YYYYMMDDTHHMMSSZ`), `socialimage`, `domain`, `language` (full name, not
code), `sourcecountry` (full name, not code). **No article ID, no body text,
no author, no published-date-original-vs-seen-date split.**

### `mode=timelinevol`

```json
{
  "query_details": {"title": "\"drug pricing\"", "date_resolution": "day"},
  "timeline": [
    {
      "series": "Volume Intensity",
      "data": [
        {"date": "20260422T000000Z", "value": 0.0214},
        {"date": "20260423T000000Z", "value": 0.0554}
      ]
    }
  ]
}
```

`value` is a normalized intensity, roughly: (matching articles in bucket) /
(all GDELT articles in bucket) expressed as a percent (0.0214 = 0.0214% of
all coverage that day was about "drug pricing"). Bucket size auto-scales
with `timespan` — daily for ~1 month, hourly for ~7 days, 15-min for ~1 day.

### `mode=timelinevolraw`

```json
{
  "query_details": {"title": "\"drug pricing\"", "date_resolution": "day"},
  "timeline": [
    {
      "series": "Article Count",
      "data": [
        {"date": "20260422T000000Z", "value": 41, "norm": 191394},
        {"date": "20260423T000000Z", "value": 119, "norm": 214966}
      ]
    }
  ]
}
```

`value` = raw article count, `norm` = corpus size that bucket. **This is the
mode you want for any quantitative downstream model** — `timelinevol`'s
normalized number is a lossy transformation of this. You can always compute
intensity = value/norm yourself.

### `mode=timelinevolinfo`

Same shape as `timelinevol` but each `data` entry also has a `toparts` array
with the most representative article URL + title for that bucket. Useful for
"why did volume spike?" annotation. Hourly resolution at `timespan=7d`.

### `mode=tonechart`

```json
{
  "tonechart": [
    {
      "bin": -8,
      "count": 1,
      "toparts": [
        {"url": "https://www.express.co.uk/news/world/2207007/...",
         "title": "Trump certainly concerned about Ebola outbreak..."}
      ]
    },
    {"bin": -7, "count": 0, "toparts": []}
  ]
}
```

17 bins from -8 to +8. Each bin has a count and up to ~10 exemplar articles.
Useful for sentiment skew but not for time-series tone (use `timelinetone`
for that).

### Error / throttle bodies (HTTP 200)

- Throttle: `Please limit requests to one every 5 seconds or contact kalev.leetaru5@gmail.com for larger queries.`
- Empty result: `{}`
- Bad operator / keyword: `One or more of your keywords were too short, too long or too common: (<token>:0)`
- Bad location search: `Your query contained an invalid location search (the search keyword/phrase must be enclosed in quotation marks).`

All return HTTP 200 with a `Content-Type: application/json` header even when
the body is plain text. The client MUST detect by parsing.

---

## State-level filtering — verdict section

**This is the make-or-break for the pipeline.** Direct answers first:

### Does `sourcelocation:` work for US states?

**No.** It is not a real operator on Doc 2.0. Probes p07, p11, p13, p14, p15
all proved this. The parser silently degrades it to a keyword and then
rejects it.

Reproduced error response for `sourcelocation:"Texas"`:

```
One or more of your keywords were too short, too long or too common: (sourcelocation:0)
```

Any guide or wrapper library that claims `sourcelocation:` works on the Doc
API is either talking about a different GDELT product (GEO 2.0 maps API or
GKG GeoJSON) or is wrong.

### Does `near:` with state geocodes work as a geo filter?

**No.** `near20:"drug Texas"` (p08) returned articles mentioning the literal
word "Texas" in proximity to "drug" — including articles from Louisiana,
California, and elsewhere. It is a **text proximity** operator, not a geo
filter. State names mentioned in headlines or bylines are matched as
strings, not as places.

### What about `sourcecountry:`?

`sourcecountry:US` works (p06). But it's publisher-country, not
article-subject-location. An article in *The Times of India* about a Texas
court ruling on drug pricing is `sourcecountry:IN` even though the subject
is in Texas. For US-publisher news this is fine; for "stories *about* Texas
PBM reform regardless of who wrote them" it isn't enough.

### Source location (publisher) vs article location (subject)

This is the core taxonomy issue with GDELT for state-level GTM:

| Concept | Doc 2.0 API | GKG (BigQuery) |
|---------|------------|----------------|
| Publisher country | `sourcecountry:` (works) | `SourceCommonName` + crosswalk |
| Publisher state | not exposed | derivable but messy |
| Article-subject country | not directly filterable; comes through theme:WB_xxx country themes | `V2Locations` parsed for country codes |
| Article-subject **state** | **not filterable** | **`V2Locations` with `LocationType=2` (USSTATE) and ADM1 code `US<XX>`** |

### Recommendation: drop to GKG BigQuery for state filtering

Since real state-level filtering doesn't exist on Doc 2.0, the pipeline
needs to query the **GDELT GKG 2.0 BigQuery dataset** for any state-tagged
work.

Table: `gdelt-bq:gdeltv2.gkg`. Key column: `V2Locations`, which is
semicolon-delimited, with each location encoded as 9 pound-delimited
sub-fields. The relevant ones:

- field 1: `LocationType` — 1=COUNTRY, 2=USSTATE, 3=USCITY, 4=WORLDCITY,
  5=WORLDSTATE
- field 2: full-text location name (e.g., "Austin, Texas, United States")
- field 3: FIPS country code (e.g., `US`)
- field 4: ADM1 code — for US states this is the **FIPS 2-letter state code
  prefixed with country code**: `USTX`, `USCA`, `USNY`, etc.
- field 6/7: lat/long
- field 8: FeatureID (numeric for cities, textual ADM1 for states)
- field 9: offset

Working pattern (from
https://blog.gdeltproject.org/google-bigquery-gkg-2-0-sample-queries/):

```sql
SELECT DocumentIdentifier AS url, V2Themes, V2Locations, DATE
FROM `gdelt-bq.gdeltv2.gkg`
WHERE DATE BETWEEN 20260101000000 AND 20260520000000
  AND V2Themes LIKE '%HEALTH%'
  AND REGEXP_CONTAINS(
        V2Locations,
        r'(^|;)2#[^#]*#US#USTX#'
      );
```

The regex pin to `LocationType=2` and `ADM1Code=USTX` is the canonical way
to filter for "this article mentions Texas as a place." That gives you
**article-subject state filtering** — what Doc 2.0 cannot do.

GKG BigQuery caveats:

- BigQuery cost: GKG is a few hundred GB/year. Use `_PARTITIONTIME` /
  `DATE BETWEEN ...` to keep scans cheap.
- Latency: GKG also updates every 15 min but BigQuery ingest adds ~hour.
- Schema is wide (60+ columns). For this pipeline you typically only need
  `DocumentIdentifier` (URL), `DATE`, `V2Themes`, `V2Locations`,
  `V2Tone`, and maybe `SourceCommonName`.
- ADM1 list: standard FIPS 10-4 — `USTX`, `USCA`, `USNY`, `USFL`, etc. Use
  a crosswalk to two-letter postal codes for the consumer-facing layer.

**Hybrid architecture recommendation:**

- **Doc 2.0 API**: fresh national volume timelines, fresh article surfacing,
  ad-hoc topic exploration. Cheap, free, fast.
- **GKG BigQuery**: state-level rollups, historical analysis, theme
  ground-truthing. Costs BigQuery $.

---

## Data freshness

**Claim:** GDELT 2.0 marketing material says event + GKG update every 15
minutes (https://blog.gdeltproject.org/gdelt-2-0-our-global-world-in-realtime/).

**Verified:** Yes, validated.

- Probe ran at approximately `20260520T2245Z` (the curl wall-clock during
  this session).
- p01 (`mode=artlist`, sorted `datedesc`, phrase `"drug pricing"`) returned
  as its top article `seendate=20260520T221500Z`.
- Gap from `seendate` to probe time: ~30 minutes. Consistent with a 15-min
  ingest cycle plus indexing lag.

Bottom line: Doc 2.0 is fresh enough to power same-day dashboards. Do not
plan on sub-15-min latency.

---

## Known limitations

1. **`maxrecords=250` cap on artlist.** You can't ask for more in a single
   call. For any query that returns more than 250 hits in a window, you
   **must slide the date window** (`startdatetime` / `enddatetime`) and
   stitch. The API does not expose page tokens or offset. See client
   design below.
2. **3-month rolling default.** Without explicit `startdatetime` /
   `enddatetime`, the lookback is 3 months. With them, dates more than ~3
   months back may return empty. The API will not error — it will just
   silently return `{}`.
3. **No stable article IDs.** The `url` is the de facto primary key.
   Articles can shift `seendate` if re-crawled, and the URL itself
   sometimes changes (mobile/AMP variants). De-dupe on a normalized URL,
   not on raw URL.
4. **Empty-vs-error ambiguity.** `{}` is returned for:
    - Legitimately zero matches.
    - Invalid theme names.
    - Date ranges outside the 3-month window.
    - Some malformed but parseable queries.
   These are indistinguishable from the response alone. Always test theme
   names against a known-good query first.
5. **Throttle is on 200 OK.** The throttle string returns under both 200
   and 429 in our session. Don't trust the status code alone — also
   sniff the body.
6. **Translingual caveats.** GDELT translates 65 languages into English
   for indexing. A phrase search `"drug pricing"` returns hits from
   non-English outlets translated as "drug pricing" by GDELT's MT — fine
   for volume metrics, noisy for verbatim-quote work. Apply
   `sourcelang:English` if you need pure-English-source articles.
7. **Two unrelated things called "theme".** GKG themes (the operator
   `theme:`) and event themes (in the Event database) are different
   taxonomies. The Doc API only sees GKG themes.
8. **GKG theme name list is huge and not fully published.** Probe-test
   any theme you plan to use before relying on it. We hit two false-
   positive names this spike alone (`HEALTH_DRUGS_AND_MEDICATIONS` and
   `ECON_PRICEGOUGE`).
9. **User-Agent is mandatory in practice.** Default curl / Python-requests
   UAs get aggressively throttled. Always set a browser-style UA.
10. **No bulk pull endpoint.** For high-volume work GDELT itself says use
    BigQuery or the Web NGrams 3.0 downloadable dataset, not this API.

---

## Minimal Python client design

A single module (`gdelt_doc.py`) is sufficient. No package, no env, no
secrets.

### File layout

```
src/gdelt/
    __init__.py
    doc.py          # public client
    parser.py       # response shape detection + parsing
    rate_limit.py   # retry + throttle handling
    queries.py      # query string builder
```

### Public surface

```python
class GdeltDocClient:
    def __init__(self, user_agent: str = DEFAULT_UA, min_interval_s: float = 6.0):
        ...

    def artlist(self, query: str, *,
                start: datetime | None = None,
                end: datetime | None = None,
                timespan: str | None = None,
                country: str | None = None,
                language: str | None = None,
                themes: list[str] | None = None,
                max_records: int = 250,
                sort: str = "datedesc") -> list[Article]:
        """Returns up to 250 articles. For >250, see artlist_windowed()."""

    def timeline(self, query: str, *,
                 mode: Literal["vol", "volraw", "volinfo", "tone"] = "volraw",
                 start: datetime | None = None,
                 end: datetime | None = None,
                 timespan: str | None = None,
                 country: str | None = None,
                 themes: list[str] | None = None) -> Timeline:
        ...

    def tonechart(self, query: str, *, timespan: str = "1m",
                  country: str | None = None) -> dict[int, ToneBin]:
        ...

    def artlist_windowed(self, query: str, *,
                         start: datetime, end: datetime,
                         window: timedelta = timedelta(days=1),
                         **kw) -> Iterator[Article]:
        """Slides the date window, capped at 250 articles per call.
           For dense topics, narrow `window` further until each window
           returns <250.  Yields deduped articles ordered by seendate."""
```

### Query string builder (queries.py)

```python
def build_query(phrase: str | None = None,
                themes: list[str] | None = None,
                country: str | None = None,
                language: str | None = None,
                exclude: list[str] | None = None,
                near: tuple[int, list[str]] | None = None,
                ) -> str:
    parts: list[str] = []
    if phrase:
        parts.append(f'"{phrase}"')
    for t in themes or []:
        parts.append(f"theme:{t}")
    if country:
        parts.append(f"sourcecountry:{country}")
    if language:
        parts.append(f"sourcelang:{language}")
    for x in exclude or []:
        parts.append(f"-{x}")
    if near:
        n, words = near
        parts.append(f'near{n}:"{" ".join(words)}"')
    return " ".join(parts)
```

**Deliberate omissions:** no `sourcelocation:` argument (it doesn't work).
No `state` argument. The client should not pretend to offer something
Doc 2.0 can't deliver. Add a docstring telling the caller to use the GKG
BigQuery path for state-level filtering.

### Rate-limit & retry (rate_limit.py)

Treat the API as having four response shapes (per the table in the Rate
Limits section). Pseudocode:

```python
THROTTLE_MARKER = b"Please limit requests to one"
ERR_MARKERS = (
    b"One or more of your keywords",
    b"Your query contained an invalid",
)

def classify(status: int, body: bytes) -> ResponseKind:
    if THROTTLE_MARKER in body:
        return ResponseKind.THROTTLED
    if status == 429:
        return ResponseKind.THROTTLED
    if body.strip() == b"{}":
        return ResponseKind.EMPTY
    if any(m in body for m in ERR_MARKERS):
        return ResponseKind.QUERY_ERROR
    if status == 200 and body.startswith(b"{"):
        return ResponseKind.OK
    return ResponseKind.UNKNOWN
```

Retry policy:

- `THROTTLED`: back off 15s, jittered, max 5 attempts.
- `EMPTY`: retry ONCE after 8s — sometimes empty is a transient throttle
  side-effect. If still empty, return `[]`.
- `QUERY_ERROR`: do not retry; raise `GdeltQueryError` with the body.
- `OK`: parse and return.
- `UNKNOWN`: log + raise.

Pace baseline: one request per 6-8 seconds with a token bucket. Don't
trust the docs' "5 seconds" — we saw 429 at 7-8s spacing repeatedly.

**User-Agent**: set a real-looking string by default. Expose it as a
constructor argument but never empty / never `python-requests/x.y.z`.

### Windowed pagination

For dense queries (any major US policy topic over a multi-month window
will exceed 250 articles), slide the date window:

```python
def artlist_windowed(self, query, *, start, end, window=timedelta(days=1)):
    seen = set()
    cursor = start
    while cursor < end:
        window_end = min(cursor + window, end)
        batch = self.artlist(query, start=cursor, end=window_end,
                              max_records=250, sort="datedesc")
        # If we hit the ceiling, auto-bisect the window
        if len(batch) >= 250 and window > timedelta(hours=1):
            yield from self.artlist_windowed(
                query, start=cursor, end=window_end, window=window/2)
        else:
            for art in batch:
                if art.url not in seen:
                    seen.add(art.url)
                    yield art
        cursor = window_end
```

This is the standard pattern for any GDELT Doc API client at scale. The
recursive bisect handles spikes (e.g., the day Trump announces a drug
pricing deal — that day alone may have 250+ matching articles).

### When to drop to BigQuery GKG

The client should NOT try to hide this — make it an explicit branch:

```python
def state_rollup(query: str, state: str, *, start, end):
    """For state-level filtering, route to BigQuery. Doc 2.0 cannot do this."""
    raise NotImplementedError(
        "State-level filtering requires GKG BigQuery. "
        "Use src/gdelt/gkg_bigquery.py:state_rollup() instead."
    )
```

Then in a separate module (`gkg_bigquery.py`):

```python
def state_rollup(client: bigquery.Client, *, themes: list[str],
                 state_adm1: str,   # e.g. "USTX"
                 start: datetime, end: datetime) -> pd.DataFrame:
    sql = """
    SELECT
      DATE,
      DocumentIdentifier AS url,
      SourceCommonName AS source,
      V2Themes, V2Locations, V2Tone
    FROM `gdelt-bq.gdeltv2.gkg`
    WHERE DATE BETWEEN @start AND @end
      AND (""" + " OR ".join(f"V2Themes LIKE '%{t}%'" for t in themes) + """)
      AND REGEXP_CONTAINS(V2Locations, r'(^|;)2#[^#]*#US#""" + state_adm1 + """#')
    """
    job = client.query(sql, job_config=...)
    return job.to_dataframe()
```

Notes for the BigQuery path:
- Bind dates as parameters, not string-format them.
- `V2Themes LIKE` is fast because `V2Themes` is short. For repeated
  queries, materialize a state-tagged view.
- Cost: a 6-month sweep across all themes is ~$1-3 with column pruning.

---

## Probe inventory

For repro / archive. All files in
`/Users/sydneywatkins/source-research/.probes/`.

| Probe | Endpoint | Verdict |
|-------|----------|---------|
| p01 | `mode=artlist` phrase `"drug pricing"` | OK, 5303 B JSON, latest seendate `20260520T221500Z` |
| p02 | `mode=timelinevol&timespan=1m` | OK, daily volume intensity series |
| p03 | `mode=timelinevolraw&timespan=1m` | OK, value + norm per day |
| p04 | `mode=tonechart&timespan=1m` | OK, 17 tone bins with exemplar articles |
| p05/p05b/p05c | `theme:HEALTH_DRUGS_AND_MEDICATIONS` | Silent `{}` — theme name is invalid |
| p06 | `sourcecountry:US` filter | OK |
| p07 | `sourcelocation:"Texas"` | ERRORED — keyword-treatment fallback |
| p08 | `near20:"drug Texas"` | OK content but NOT a geo filter (LA, CA results) |
| p09 | explicit `startdatetime`/`enddatetime` 20260101-20260120 | OK |
| p10 | `"PBM reform"` timelinevol 3m | OK, sparse but valid |
| p11 | `sourcelocation:USTX` (unquoted) | ERRORED — invalid location search |
| p12 | invalid theme `NOT_A_REAL_THEME_XYZ` | Silent `{}` |
| p13 | `sourcelocation:"USTX"` | ERRORED |
| p14 | `sourcelocation:"Texas"` | ERRORED |
| p15 | `sourcelocation:"Texas"` standalone | ERRORED |
| p16 | `location:"Texas"` | ERRORED |
| p17 | artlist for `"PBM reform"` | OK |
| p18 | `theme:TERROR` alone | OK — confirms theme operator works when name is valid |
| p19 | `theme:ECON_PRICEGOUGE` | Silent `{}` — another invalid theme |
| p20 | `mode=timelinevolinfo&timespan=7d` | OK, hourly resolution, ~26 KB |
| p21 | `format=csv` artlist | OK, UTF-8 BOM, `URL,MobileURL,Date,Title` |

---

## References

- https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/ — primary Doc 2.0
  reference, all operators and modes.
- https://blog.gdeltproject.org/doc-2-0-api-now-supports-near-and-repeat-operators/ —
  `near:` and `repeat:` operator deep dive.
- https://blog.gdeltproject.org/gdelt-2-0-our-global-world-in-realtime/ —
  15-min update cadence, 65-language coverage.
- https://blog.gdeltproject.org/google-bigquery-gkg-2-0-sample-queries/ —
  the canonical reference for `V2Locations` parsing and ADM1 filters.
- https://blog.gdeltproject.org/behind-the-scenes-api-quotas-the-impact-of-a-fraction-of-a-qps/ —
  GDELT's own acknowledgment that throttle thresholds are tight and unstable.
- https://blog.gdeltproject.org/ukraine-api-rate-limiting-web-ngrams-3-0/ —
  recommendation to use Web NGrams 3.0 for high-volume needs.
- https://github.com/alex9smith/gdelt-doc-api — third-party Python client;
  good prior art on filter design.
- https://github.com/alex9smith/gdelt-doc-api/issues/22 — User-Agent header
  workaround for rate-limit-on-first-call.
- https://gdelt.github.io/ — interactive Doc API explorer; good for
  validating queries before wiring them into the pipeline.
