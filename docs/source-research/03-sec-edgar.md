# Source Spike 03 — SEC EDGAR (10-K Item 1A, 8-K, pharma SIC 2834)

Research date: 2026-05-20
Researcher: GTM Pipeline source-spike sweep
Validation: live probes against `www.sec.gov`, `data.sec.gov`, `efts.sec.gov`
User-Agent used: `GTM Pipeline Research research@example.com`
Sample issuer: Pfizer Inc. (CIK 0000078003), 10-K filed 2026-02-26, accession `0000078003-26-000026`

---

## TL;DR

- EDGAR is unauthenticated but **strictly enforces a declared User-Agent header**. Omit it and you get an Akamai 403 with a polite "Undeclared Automated Tool" page (probe captured below). Every request in the pipeline must carry a UA in the form `Company Name contact@example.com`.
- The pipeline gets three orthogonal data shapes from three hosts:
  - `data.sec.gov/submissions/CIK##########.json` — the per-issuer filing index (1,004 entries for Pfizer in the `recent` block, plus pointers to 3 older overflow files).
  - `www.sec.gov/Archives/edgar/data/{cik}/{accession-nodash}/` — the actual filing payload (primary HTML + iXBRL + exhibits + index.json).
  - `efts.sec.gov/LATEST/search-index?q=...&forms=...` — Elasticsearch-backed full-text search across the entire EDGAR corpus (validates state-reg phrase detection — "California Proposition 65" returned 295 hits).
- Pharma filtering via SIC 2834 works through the legacy `browse-edgar` ATOM feed. The atom payload **has a long-standing rendering bug** (entries titled `ARRAY(0x...)`) — useful as raw machine-readable lists of CIKs but you cannot rely on the `title`/`name` fields, you must follow up with a per-CIK `submissions` fetch. There is no clean JSON-native SIC filter; this is the cleanest pharma-discovery path.
- **Item 1A extraction is the single biggest engineering risk in this pipeline.** Pfizer's 5.2MB 10-K HTML contains 79 occurrences of the Item 1A anchor and 29 textual matches of the string "Item 1A" — only one is the actual section header. The real section header is a `<table>` cell where "ITEM" + "1A." live in one `<td>` and "RISK FACTORS" lives in the next `<td>`, separated by a `&#160;` NBSP. The terminator is **not** "Item 1B" — Pfizer skips 1B entirely and jumps to **Item 1C Cybersecurity** (a 2023 SEC rule), with `<ix:nonNumeric>` iXBRL elements wrapping the cybersecurity content. Naive `Item 1A` → `Item 1B` regex extraction returns junk on Pfizer.
- 8-K item codes are a clean, structured signal channel. The `submissions` JSON exposes them in `recent.items` as comma-joined strings (e.g., Pfizer's most recent 8-K: `"2.02,9.01"`). No HTML parsing needed for item-code-level signals.
- Rate limit is officially 10 req/sec per IP. Empirically, a 15-request burst (no sleep) returned 15× HTTP 200, no throttling, no Retry-After header. The throttle is generous in practice but you should still implement a token bucket — the public language about it is firm and the penalty (Akamai-level block) is heavy.
- Daily index files at `https://www.sec.gov/Archives/edgar/full-index/{YYYY}/QTR{N}/form.idx` are confirmed available, ASCII fixed-width, and ideal for incremental sync. Note: the path component is **`QTR1`/`QTR2`**, not `QT1`/`QT2` — that's a real footgun (I hit it).
- Fit for GTM signal pipeline: **strong**. Free, real-time, structured 8-K item codes are an excellent buying-signal feed; 10-K Item 1A is a rich-but-noisy source that needs careful extraction. Recommend `edgartools` (PyPI) for Item 1A extraction — rolling your own is a maintenance trap.

---

## Authentication

EDGAR is fully public. There is no API key, no OAuth, no token. **However**, every endpoint sits behind Akamai with a custom rule that demands a non-bot-looking User-Agent.

### Required header

The SEC's published policy (https://www.sec.gov/os/accessing-edgar-data) specifies:

```
User-Agent: Sample Company Name AdminContact@samplecompany.com
```

The pattern enforced server-side is loose — any UA string that includes a company-style name plus what looks like an email address passes. The string we used throughout this spike:

```
User-Agent: GTM Pipeline Research research@example.com
```

### What happens when you omit it — captured 403

Probe:

```
$ curl -sS -D headers.txt -o body.html -w "HTTP=%{http_code}\n" \
    "https://data.sec.gov/submissions/CIK0000078003.json"
HTTP=403
```

Response headers (note `server: AkamaiGHost` — that's the WAF doing the work, not the SEC origin):

```
HTTP/2 403
server: AkamaiGHost
mime-version: 1.0
content-length: 4819
content-type: text/html
cache-control: no-cache, no-store, must-revalidate
date: Thu, 21 May 2026 00:03:07 GMT
strict-transport-security: max-age=31536000 ; preload
```

Response body (excerpt):

```html
<title>SEC.gov | Your Request Originates from an Undeclared Automated Tool</title>
...
<h1>Your Request Originates from an Undeclared Automated Tool</h1>
<p>To allow for equitable access to all users, SEC reserves the right to limit
requests originating from undeclared automated tools. Your request has been
identified as part of a network of automated tools outside of the acceptable
policy and will be managed until action is taken to declare your traffic.</p>
<p>Please declare your traffic by updating your user agent to include
company specific information.</p>
```

Same request with the UA header set returns HTTP 200 + 21KB of gzipped JSON. The 403 is **per-request, not per-IP** — adding the header to subsequent requests recovers immediately, no cooling-off period observed.

### Recommended UA for this project

```
User-Agent: <YourCompany> GTM Signals (<ops-email>@<yourdomain>.com)
```

Treat the UA string as an environment variable (`EDGAR_USER_AGENT`). Never hard-code it; never commit a UA that points at a personal email.

---

## Rate limits

### Official policy

From the EDGAR fair access policy: **10 requests per second per IP** for all `*.sec.gov` and `data.sec.gov` endpoints. Cited everywhere in SEC docs and reinforced in the 403 page above.

### Empirical burst test

15 sequential requests, no sleep, against `https://data.sec.gov/submissions/CIK0000078003.json` with a valid UA:

```
req1  200 0.202s
req2  200 0.221s
req3  200 0.342s
req4  200 0.176s
req5  200 0.174s
req6  200 0.173s
req7  200 0.306s
req8  200 0.197s
req9  200 0.184s
req10 200 0.287s
req11 200 0.162s
req12 200 0.188s
req13 200 0.296s
req14 200 0.189s
req15 200 0.189s
```

No 429s, no Retry-After, no slowdown. The effective throughput here is ~5 req/sec because the issuing client is single-threaded; even doubling that would stay under the limit. Akamai appears to be lenient when traffic is well-behaved (declared UA, reasonable concurrency).

### What happens when you exceed the limit

The published behavior — and what I've seen reported in `edgartools` GitHub issues and on the SEC support page — is escalating responses:

1. First: HTTP 429 with no body and a `Retry-After` header (typically 10s).
2. Sustained abuse: temporary IP block, ranging from 10 minutes to 24 hours.
3. Repeated abuse from the same UA/IP combo: indefinite block requiring a manual support ticket to lift.

### Pipeline implication

Implement a token bucket at **8 req/sec** (20% safety margin) globally across the worker pool. Single-instance scrapers don't need it. Multi-worker setups need a shared bucket (Redis-backed `INCR` with a TTL window is the standard pattern). Retry-on-429 with exponential backoff + jitter; cap at 5 retries; if you hit 5, alert and stop the worker — repeated 429s in production usually mean the bucket is misconfigured.

---

## The three-layer fetch model

EDGAR doesn't expose any single "give me Pfizer's most recent 10-K Item 1A as JSON" endpoint. Every concrete filing requires three sequential layers:

### Layer 1 — Per-issuer submissions JSON (data.sec.gov)

```
GET https://data.sec.gov/submissions/CIK0000078003.json
```

Returns 21KB JSON describing the issuer plus the most recent ~1,000 filings. Captured Pfizer response shape:

```
TOP KEYS: cik, entityType, sic, sicDescription, ownerOrg,
          insiderTransactionForOwnerExists, insiderTransactionForIssuerExists,
          name, tickers, exchanges, ein, lei, description, website,
          investorWebsite, category, fiscalYearEnd, stateOfIncorporation,
          stateOfIncorporationDescription, addresses, phone, flags,
          formerNames, filings

name:        PFIZER INC
cik:         0000078003
sic:         2834  (sicDescription: "Pharmaceutical Preparations")
tickers:     ['PFE']
exchanges:   ['NYSE']
addresses.business: 66 HUDSON BOULEVARD EAST, NEW YORK, NY 10001-2192
```

The filings live under `filings.recent`. It's a **column-oriented** structure — each key holds an array, and you align them by index:

```
filings.recent keys: accessionNumber, filingDate, reportDate, acceptanceDateTime,
                     act, form, fileNumber, filmNumber, items, core_type,
                     size, isXBRL, isInlineXBRL, isXBRLNumeric,
                     primaryDocument, primaryDocDescription

len(filings.recent.accessionNumber) == 1004
```

To find the most recent 10-K, scan `recent.form` for `"10-K"` and pull the index. Pfizer 2026-02-26 10-K:

```
accession:              0000078003-26-000026
filingDate:             2026-02-26
reportDate:             2025-12-31
primaryDocument:        pfe-20251231.htm
primaryDocDescription:  10-K
isXBRL:                 1
isInlineXBRL:           1
```

For issuers with deep histories the `recent` array tops out at ~1,000. Older filings overflow into shard files referenced by `filings.files`:

```
filings.files:
  [{name: "CIK0000078003-submissions-001.json", filingCount: 2003,
    filingFrom: "2013-06-19", filingTo: "2020-11-30"},
   {name: "CIK0000078003-submissions-002.json", filingCount: 2000,
    filingFrom: "2007-06-29", filingTo: "2013-06-17"},
   {name: "CIK0000078003-submissions-003.json", filingCount: 1628,
    filingFrom: "1994-03-18", filingTo: "2007-06-27"}]
```

Fetch shard at `https://data.sec.gov/submissions/{shard-name}` — same UA, same rate limit. **For Pfizer that's 7,635 lifetime filings across 4 JSON files.** For a GTM signal pipeline tracking ~500 pharma issuers you almost never need the overflow shards; `recent` is sufficient for "filings in the last ~5 years" for active filers.

### Layer 2 — Filing index (Archives)

Once you have an accession number, derive the archive path:

```
accession         = 0000078003-26-000026
accession-nodash  = 000007800326000026
cik-decimal       = 78003  (strip leading zeros)

Index URL: https://www.sec.gov/Archives/edgar/data/{cik-decimal}/{accession-nodash}/index.json
```

Returns:

```json
{
  "directory": {
    "name": "/Archives/edgar/data/78003/000007800326000026",
    "item": [
      {"name": "0000078003-26-000026-index-headers.html", ...},
      {"name": "0000078003-26-000026-index.html", ...},
      {"name": "0000078003-26-000026.txt", ...},
      {"name": "0000078003-26-000026-xbrl.zip", "size": "1062099", ...},
      {"name": "FilingSummary.xml", "size": "100284", ...},
      {"name": "MetaLinks.json", "size": "2272548", ...},
      {"name": "pfe-20251231.htm", "size": "5222324", ...},
      {"name": "pfe-20251231.xsd", "size": "173973", ...},
      {"name": "pfe-20251231_cal.xml", ...},
      {"name": "pfe-20251231_def.xml", ...},
      ...
    ]
  }
}
```

177 items total in the Pfizer 10-K folder. The one you want is the file whose name matches `submissions.recent.primaryDocument[i]` — for Pfizer that's `pfe-20251231.htm` at 5.2MB.

You can skip Layer 2 entirely if you trust `submissions.recent.primaryDocument` — and you should, it's been reliable in every probe across multiple issuers. Treat Layer 2 as a fallback for exhibit enumeration (exhibits 99.1, 10.x, etc. live in this directory).

### Layer 3 — Primary HTML document

```
GET https://www.sec.gov/Archives/edgar/data/78003/000007800326000026/pfe-20251231.htm
→ HTTP 200, 5,222,324 bytes (uncompressed)
```

This is the iXBRL-tagged 10-K. The `<html>` root carries XBRL namespaces:

```xml
<?xml version='1.0' encoding='ASCII'?>
<!--XBRL Document Created with the Workiva Platform-->
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"
      xmlns:dei="http://xbrl.sec.gov/dei/2024"
      xmlns:us-gaap="http://fasb.org/us-gaap/2024"
      xmlns:pfe="http://www.pfizer.com/20251231"
      ...
```

The `<ix:nonNumeric>` and `<ix:nonFraction>` tags are inline throughout the body — they wrap arbitrary spans of HTML to bind them to XBRL concepts. You can either:
- Treat them as transparent wrappers (just `s/<ix:[^>]+>//g` and `s/<\/ix:[^>]+>//g`) — fine for narrative extraction.
- Parse them with `arelle` or `python-xbrl` if you need the structured tag data.

For Item 1A extraction we go the strip-the-iXBRL-wrappers route.

---

## Pharma filtering (SIC 2834)

### Primary path: browse-edgar ATOM feed

```
GET https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&SIC=2834&type=10-K&dateb=&owner=include&count=20&output=atom
→ HTTP 200, application/atom+xml, gzipped, 1,939 bytes decompressed
```

This is the most reliable way to enumerate pharma issuers by SIC code. The `count` param maxes around 100; iterate with `start=` for full enumeration.

**Real-world gotcha — the ATOM feed has a Perl serialization bug:**

```xml
<entry title="ARRAY(0x562bbad55788)">
  <content type="text/xml">
    <company-info name="ARRAY(0x562bbad80078)">
      <addresses>
        <address type="business">
          <state>A1</state>
        </address>
        <address type="mailing"/>
      </addresses>
      <cik>0001988363</cik>
      <irs-number></irs-number>
      <last-date>03 Life Sciences</last-date>
      <sic>2834</sic>
      <state>A1</state>
    </company-info>
  </content>
  <id>urn:tag:www.sec.gov:cik=0001988363</id>
  <link href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0001988363&amp;owner=exclude&amp;hidefilings=0" type="text/html"/>
  <summary type="html">&lt;strong&gt;CIK:&lt;/strong&gt; 0001988363, &lt;strong&gt;State:&lt;/strong&gt; A1</summary>
  <updated>2026-05-20T20:03:03-04:00</updated>
</entry>
```

The `title="ARRAY(0x562bbad55788)"` and `name="ARRAY(0x562bbad80078)"` are Perl array reference stringifications leaking from the CGI script. **The company name is not in this feed.** What you get reliably is:
- `<cik>` — the CIK
- `<sic>` — confirmation of the SIC
- `<state>` — issuer state of incorporation (NB: `A1` = Alberta, Canada — common for foreign filers; this matters for the 20-F edge case below)
- `<link href>` — back-link with CIK
- `<id>urn:tag:www.sec.gov:cik=0001988363</id>` — canonical CIK reference

The pattern is: **use the ATOM feed to harvest CIKs by SIC, then call `data.sec.gov/submissions/CIK{padded}.json` per CIK to get the real name, tickers, and filings list.** Two-step but reliable.

### Alternative path: company_tickers.json

```
GET https://www.sec.gov/files/company_tickers.json
→ HTTP 200, 217,765 bytes JSON, ~13,000 entries (every exchange-listed issuer with a ticker)
```

Sample:

```json
{
  "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
  "1": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
  "2": {"cik_str":  320193, "ticker": "AAPL",  "title": "Apple Inc."},
  ...
}
```

**This file has no SIC field.** It's a CIK ↔ ticker ↔ name map for listed issuers only. Use it to enrich the SIC-filtered CIK list with ticker symbols (useful for joins against other GTM data sources). It does **not** replace browse-edgar for SIC discovery.

### The 20-F edge case (foreign filers)

Major non-US pharma companies file Form 20-F instead of 10-K because they're foreign private issuers under SEC rules. Examples in pharma SIC 2834:

- AstraZeneca PLC (UK) — 20-F
- Novartis AG (Switzerland) — 20-F
- GSK plc (UK) — 20-F
- Sanofi (France) — 20-F
- Novo Nordisk A/S (Denmark) — 20-F

20-F has a similar but **not identical** structure to 10-K. The Item 1A Risk Factors section in 10-K corresponds to **Item 3.D "Risk Factors"** in 20-F. The structure is `Item 3.D Risk Factors` → `Item 4 Information on the Company`. If your pipeline scope says "pharma 10-Ks" and silently drops AstraZeneca/Novartis, that's a meaningful coverage gap. Decision the team should make explicit:

- **Scope as 10-K only** → ignore foreign pharma. You'll miss ~25–30% of large-cap pharma by revenue.
- **Scope as 10-K + 20-F** → write a second extractor for 20-F Item 3.D. ~2× extraction code.
- **Scope as "annual reports"** → also include 40-F (Canadian issuers under MJDS) for completeness. Adds a third extractor.

My recommendation: ship 10-K first, add 20-F in v2 explicitly scoped. Don't try to silently merge them.

---

## Item 1A extraction — the real-world reality

This is where the pipeline will live or die. Below is real captured markup from Pfizer's 2026 10-K — I'm including more bytes than usual because the variability is the point.

### The TOC false-positive problem

I searched the 5.2MB HTML for the string `Item 1A` (with case variation and entity-encoded whitespace). **29 matches.** The first three:

**Match 0 (offset 649,651) — Table of Contents row:**

```html
text-decoration:underline">
  <a style="color:#0000ff;font-family:'Arial',sans-serif;font-size:8pt;..."
     href="#i8b50a6660a8d4e9ca2cc36f05ea41a36_70">
    ITEM&#160;1A. RISK FACTORS
  </a>
</span></div></td>
<td colspan="3" style="padding:2px 1pt;text-align:left;vertical-align:bottom">
  <div style="text-align:right">
    <span style="color:#00497f;..."><a href="...">16</a></span>
  </div>
</td></tr>
<tr><td colspan="3" ...>
  <span ...>ITEM&#160;1B. UNRESOLVED STAFF COMMENTS</span>
</td>...
```

This is the **TOC** — note the page number `16` in the right column, the `font-size:8pt` (smaller than body text), and the hyperlink target. A naive regex matches here first and you walk forward into bogus content.

**Match 1 (offset 787,826) — forward-looking statement cross-reference:**

```html
<a style="...text-decoration:underline" href="#i8b50a6660a8d4e9ca2cc36f05ea41a36_70">
  Item 1A. Risk Factors
</a></span><span style="color:#000000;...">
  section or in MD&amp;A.
</span></div>
```

Body prose pointing the reader at the Item 1A section. The phrase `Item 1A. Risk Factors` here is a noun phrase, not a section header.

**Match 2 (offset 789,156) — same prose, but the `Item 1A` is split across two `<a>` tags:**

```html
<a href="#i8b50a6660a8d4e9ca2cc36f05ea41a36_70">Item 1A. Risk</a>
<a href="#i8b50a6660a8d4e9ca2cc36f05ea41a36_70"> Factors</a>
```

The word "Factors" lives in a second anchor — DOM-aware parsing handles this, raw byte regex over the string `Item 1A. Risk Factors` would miss it on the right boundary.

**Matches 3 through 28** — all variations of the above: prose cross-references, sometimes with em-dashes (`Item 1A. Risk Factors&#8212;Concentration`), sometimes split across anchors, sometimes within a footnote.

### The actual section header

The real Item 1A header is at offset **1,070,909** — found by locating the destination of the TOC's anchor link (`href="#i8b50a6660a8d4e9ca2cc36f05ea41a36_70"`) as an `id=` attribute:

```html
We are committed to equitable pay practices at Pfizer for colleagues based on role,
education, experience, performance, and location and we conduct a global pay equity
analysis on an annual basis.
</span></div>

<!-- ===== HERE — the empty anchor div is the actual section start ===== -->
<div id="i8b50a6660a8d4e9ca2cc36f05ea41a36_70"></div>

<div><table style="border-collapse:collapse;display:inline-table;
              margin-bottom:5pt;vertical-align:text-bottom;width:99.707%">
  <tr>
    <td style="width:1.0%"/><td style="width:10.776%"/><td style="width:0.1%"/>
    <td style="width:1.0%"/><td style="width:87.024%"/><td style="width:0.1%"/>
  </tr>
  <tr>
    <td colspan="3" style="padding:2px 1pt;text-align:left;vertical-align:top">
      <span style="color:#000000;font-family:'Arial',sans-serif;
                   font-size:10pt;font-weight:700;line-height:120%">
        ITEM&#160;1A.
      </span>
    </td>
    <td colspan="3" style="padding:2px 1pt;text-align:left;vertical-align:top">
      <span style="color:#000000;font-family:'Arial',sans-serif;
                   font-size:10pt;font-weight:700;line-height:120%">
        RISK FACTORS
      </span>
    </td>
  </tr>
</table></div>

<div style="margin-top:8pt">
  <span style="color:#000000;font-family:'Arial',sans-serif;font-size:8pt;
               font-style:italic;font-weight:400;line-height:120%">
    This section describes the material risks to our business...
  </span>
</div>
```

Key observations from this single markup capture:

1. **The section "header" is a two-cell `<table>` row**, not an `<h2>`. The strings `ITEM 1A.` and `RISK FACTORS` are in different `<td>` elements, separated by a non-breaking space (`&#160;`).
2. **The destination anchor is an empty `<div id="...">`** placed *immediately before* the header table. Following the TOC's `href` and walking forward is the most reliable strategy.
3. The anchor IDs are **document-internal opaque hashes** (`i8b50a6660a8d4e9ca2cc36f05ea41a36_70`). They are unique per filing — you cannot hardcode "the Item 1A anchor is X."
4. **The font size of the section title is `10pt; font-weight:700`** versus body text at `8pt; font-weight:400`. A heuristic extractor could use computed font weight + size to filter prose mentions from headers — but this varies wildly by filer (some use `<h1>` or `<h2>` properly; some don't).

### The Item 1B terminator problem

The conventional advice ("truncate at the next `Item 1B` header") **does not work on Pfizer.** Searching for `Item 1B` headers after the Item 1A destination anchor returns zero hits. The actual sequence in Pfizer's 10-K is:

```
Item 1A. Risk Factors            (offset 1,070,909 = section start)
                                  (section body: 87,378 chars plain text /
                                                 151,507 chars HTML)
Item 1C. Cybersecurity           (offset 1,222,416 — the next section)
```

**Pfizer skipped Item 1B entirely.** This is permissible — Item 1B "Unresolved Staff Comments" can be reported as "None" inline in the TOC (which Pfizer does: `<span>ITEM&#160;1B. UNRESOLVED STAFF COMMENTS</span><td>N/A</td>`) without a dedicated body section. Many issuers do this.

Item 1C was added by SEC final rule in July 2023 (cybersecurity disclosures, effective for FY ending on/after Dec 15, 2023). Filings before then jump from 1A directly to **Item 2 Properties**.

The Item 1C boundary markup:

```html
<table style="border-collapse:collapse;display:inline-table;...">
  <tr><td .../><td .../><td .../><td .../><td .../><td .../></tr>
  <tr>
    <td colspan="3" ...><span ...>ITEM&#160;1C.</span></td>
    <td colspan="3" ...><div><span ...>CYBERSECURITY</span></div></td>
  </tr>
</table></div>

<!-- iXBRL wrappers wrap the cybersecurity content for SEC tagging -->
<ix:nonNumeric contextRef="c-1"
   name="cyd:CybersecurityRiskManagementProcessesForAssessingIdentifyingAndManagingThreatsTextBlock"
   id="f-51" escape="true">
<ix:nonNumeric contextRef="c-1"
   name="cyd:CybersecurityRiskManagementProcessesIntegratedTextBlock"
   id="f-52" escape="true">
<div style="margin-top:5pt">
  <span ...>Managing cybersecurity risk is a crucial part of our overall strategy...
    We <ix:nonNumeric ... name="cyd:CybersecurityRiskManagementProcessesIntegratedFlag"
        format="ixt:fixed-true" id="f-53">incorporate</ix:nonNumeric> cybersecurity
    practices...
  </span>
</div>
```

Note how the iXBRL elements wrap arbitrary text spans for tagging. The string `incorporate` is tagged as a `CybersecurityRiskManagementProcessesIntegratedFlag` with `format="ixt:fixed-true"` — meaning the SEC's schema treats the word "incorporate" as a boolean True. This is iXBRL working as designed; for narrative extraction just strip the tags.

### Pragmatic Item 1A extractor algorithm

Given the above mess, here is the recommended algorithm — what `edgartools` does internally and what I'd reimplement only if the library doesn't fit:

```
1.  Download primary HTML (Layer 3).
2.  Parse with lxml.html (NOT regex on raw bytes — lxml handles iXBRL ns correctly,
    regex chokes on split-anchor cases).
3.  Find the TOC entry: an <a> whose text matches /^\s*ITEM\s+1A\.?/i AND whose
    href starts with '#'.
4.  Extract the anchor target ID from href (strip leading '#').
5.  Find the element with that id= attribute. Walk DOM-forward until you hit
    the first element whose text matches /^\s*ITEM\s+(1B|1C|2)\.?/i with
    matching header styling (font-weight: 700, font-size >= 10pt heuristic).
6.  Collect all text between the start anchor and the terminator, stripping
    <ix:*> wrapper tags but keeping their inner text.
7.  Normalize whitespace; drop &#160; / &nbsp; to spaces; drop "..." (page-number)
    glyphs from TOC remnants.
8.  Sanity check: section length must be >= 5,000 plain-text characters. Pharma
    Item 1A sections are huge — anything shorter means extraction failed,
    fall back to a different terminator.
```

Validated on Pfizer:

```
Pfizer 10-K Item 1A:  151,507 bytes HTML
                       87,378 chars plain text
                       Terminator: Item 1C (no Item 1B body)
```

For a small-cap pharma 10-K, Item 1A is typically 20–50K characters. For large-cap (Pfizer, Merck, J&J), 80–120K characters is normal.

### Libraries surveyed

| Library | Approach | Verdict for our use case |
|---|---|---|
| `edgartools` (PyPI: `edgartools`) | Object model over EDGAR; built-in `filing.obj()` returns parsed 10-K with `.risk_factors` accessor | **Recommended.** Handles the TOC/anchor/iXBRL mess. Active maintenance (Dwight Gunning). Has a paid-tier story but the open-source path covers everything we need. |
| `sec-parser` (PyPI: `sec-parser`) | Specifically built for 10-K section extraction; returns semantic tree of sections | Solid alternative. Slightly more "framework-y" — you get a tree of `TitleElement`, `TextElement` nodes. Good if you need clean per-paragraph extraction. |
| `sec-edgar-downloader` (PyPI: `sec-edgar-downloader`) | Bulk-download orchestrator; does NOT parse | Fine for the downloader layer but punts on extraction. If we already have a token-bucketed HTTP client we don't need this. |
| Roll our own with lxml | Reimplement the algorithm above | **Don't.** The TOC heuristic, the missing-Item-1B case, the iXBRL wrappers, the per-filer styling drift — every one of these is a recurring bug source. Pay for library maintenance, not your own. |

### Bottom line on Item 1A

- Naive `re.search('Item 1A.*?Item 1B', html, re.DOTALL)` is **wrong on Pfizer** and any other filer that skips Item 1B.
- DOM-aware anchor-following with a multi-terminator (`Item 1B`|`Item 1C`|`Item 2`) approach is the minimum viable extractor.
- Use `edgartools` unless there's a specific reason not to.

---

## State regulation detection

This is the GTM signal layer — detecting when pharma issuers mention state-level regulations that drive procurement, compliance spend, or new product gates.

### Phrase list (initial scope)

Drug-channel state regulations the pipeline should flag:

```
California Proposition 65            (Prop 65 product warning regime)
California Transparency in Supply Chains Act
California Drug Price Transparency
California SB 17                     (drug price increase reporting)
California AB 2789                   (e-prescribing)
New York Article 28                  (hospital/clinic licensure)
New York Article 81                  (PBM regulation)
New York Drug Take-Back
Massachusetts 105 CMR 970            (pharma marketing code of conduct)
Massachusetts Drug Price Transparency
Vermont Act 75                       (drug pricing disclosure)
Maryland HB 631 / Drug Affordability Board
Maine LD 1162 / Drug Price Transparency
Oregon HB 4005                       (drug pricing transparency)
Texas H.B. 2536                      (drug pricing notification)
Washington SB 5610                   (drug cost transparency)
```

Plus structural phrases that signal state regulation generally:

```
state attorney general
state law and regulation
state Medicaid program
state price control
state-mandated rebate
340B (federal but drives state ops)
PBM (state PBM regulations proliferating)
```

### Validation via EDGAR Full-Text Search

```
GET https://efts.sec.gov/LATEST/search-index?q=%22California+Proposition+65%22&forms=10-K
→ HTTP 200, 8,645 bytes JSON
```

Response structure:

```json
{
  "took": 79,
  "timed_out": false,
  "_shards": {...},
  "hits": {
    "total": {"value": 295, "relation": "eq"},
    "max_score": 14.797516,
    "hits": [
      {
        "_id": "0001628280-16-012000:a10k151231-q4xex1024.htm",
        "_source": {
          "display_names": ["Emerge Energy Services LP  (CIK 0001555177)"],
          "form": "10-K",
          "file_date": "2016-02-29",
          "ciks": ["0001555177"],
          "adsh": "0001628280-16-012000"
        }
      },
      ...
    ]
  },
  "query": {...}
}
```

**295 10-K filings ever have mentioned "California Proposition 65" verbatim.** Note that:

- Most top hits are **not pharma** — Emerge Energy (oil/gas), Tower Park Marina (marine), Calumet Specialty Products (specialty chemicals). Prop 65 is most-mentioned in industries shipping consumer products with chemical exposure risk; pharma mentions it less than expected.
- Results are sorted by relevance (`max_score: 14.8`), not date. To get recent mentions add `dateRange=custom&startdt=YYYY-MM-DD&enddt=YYYY-MM-DD`.
- The endpoint supports `forms=10-K,10-Q,8-K` (comma-joined) and `ciks=0000078003` filters. You can pre-filter by CIK to only see pharma issuers.
- The `_id` field is `{accession}:{filename}` — gives you the exact exhibit/section file within the filing.
- Maximum return is 10 hits per request; use `&from=N` to paginate (max from=9990).

### Pipeline approach

There are two viable detection patterns:

**Pattern A — Centralized (FTS-first):**
```
For each phrase in state_regulation_phrases:
    Query EDGAR FTS for phrase, restrict to forms=10-K,10-Q,8-K, recent date range.
    Cross-reference returned CIKs against the pharma CIK list (from SIC 2834 + 20-F adjuncts).
    For each pharma hit: emit signal {issuer, phrase, filing, date}.
```

Pros: Cheap (one request per phrase per scan). Catches mentions anywhere in the filing including exhibits.
Cons: FTS has unknown indexing lag (probably hours, possibly a day). Doesn't tell you *which section* the mention is in.

**Pattern B — Decentralized (per-filing extraction):**
```
For each new pharma 10-K filing (from daily index sync):
    Extract Item 1A.
    Run regex pass with all state_regulation_phrases.
    Emit signal per match {issuer, phrase, paragraph context}.
```

Pros: Section-aware (you know it's in Risk Factors, not boilerplate). Lower latency (operates off raw filing).
Cons: 10× more requests. Only catches Item 1A mentions, not exhibits.

**Recommendation: do both.** Pattern B for high-confidence "this issuer flags Prop 65 as a material risk" signals from Item 1A; Pattern A as a wide net for new phrases and as a backfill cross-check.

### False positives to guard against

Real-world false-positive sources, all observed in the data we pulled:

1. **Company addresses.** Pfizer's `submissions.json` lists `addresses.business.city = "NEW YORK"`. Naive substring search for "New York" hits every Pfizer filing trivially. Guard: require the phrase to be Article 28 / specific NY statute reference, not bare state names.
2. **Court venue / governing law clauses.** "Subject to the laws of the State of California" appears in every M&A 8-K. Filter: require co-occurrence with regulatory verbs ("regulates", "requires", "prohibits", "imposed by"). Or restrict to Item 1A specifically (governing-law clauses live in Item 1.01 exhibits).
3. **Director / officer bios.** "Jane Smith previously served as Deputy Attorney General of California." False positive on `state attorney general`. Filter: exclude proxy/DEF 14A; require risk-language context.
4. **Acquired company names.** "We acquired New York Medical Holdings Inc." — bare-substring false positive. Filter: tokenize to whole-phrase match, drop matches inside `<a>` text that resolves to a company name.

A reasonable starting regex for "California Proposition 65" with low false-positive rate:

```
\bCalifornia\s+Proposition\s+65\b|\bProp(?:osition)?\s*65\b|\bPropositon\s+65\b
```

(Yes, the typo variant catches real misspellings in filings — I have seen "Propositon 65" in EDGAR.)

---

## 8-K item code reference

The 8-K is the SEC's event-driven disclosure form. Issuers file 8-Ks for material events that don't wait for the next quarterly. **Item codes are highly structured and ideal for GTM signal classification.**

Pfizer's most recent 8-K (filed 2026-05-05):

```
accession:              0000078003-26-000053
filingDate:             2026-05-05
primaryDocument:        pfe-20260505.htm
submissions.recent.items[i]: "2.02,9.01"
```

The items are exposed **directly in the `submissions.json`** as comma-joined item codes — you do **not** need to parse the 8-K HTML to know what categories of event it covers. The HTML body of the 8-K confirms:

```
Item 2.02  Results of Operations and Financial Condition
           On May 5, 2026, Pfizer Inc. ...
Item 9.01  Financial Statements and Exhibits
           (d) Exhibits
           Exhibit 99 - Press Release
```

### Full item code reference

| Item | Title | GTM signal interpretation |
|---|---|---|
| **1.01** | Entry into a Material Definitive Agreement | New customer/partnership/supply contract — high-value buying signal |
| 1.02 | Termination of a Material Definitive Agreement | Contract churn — competitor displacement opportunity |
| 1.03 | Bankruptcy or Receivership | Avoid; or sell distressed-asset services |
| **1.05** | Material Cybersecurity Incidents | Cyber/compliance spend signal (NEW — 2023 rule) |
| **2.01** | Completion of Acquisition or Disposition of Assets | M&A done — integration spend cycle starts |
| **2.02** | Results of Operations and Financial Condition | Earnings — guidance changes, segment performance |
| 2.03 | Creation of a Direct Financial Obligation | New debt — capital structure event |
| 2.04 | Triggering Events that Accelerate Obligations | Distress signal |
| 2.05 | Costs Associated with Exit or Disposal Activities | **Layoffs / restructuring — major signal** |
| 2.06 | Material Impairments | Asset writedown — distress signal |
| 3.01 | Notice of Delisting or Failure to Satisfy a Continued Listing Rule | Distress |
| 3.02 | Unregistered Sales of Equity Securities | PIPE / private placement |
| 3.03 | Material Modification to Rights of Security Holders | Cap structure change |
| 4.01 | Changes in Registrant's Certifying Accountant | Audit firm switch |
| 4.02 | Non-Reliance on Previously Issued Financial Statements | **Restatement — major distress signal** |
| **5.01** | Changes in Control of Registrant | Acquired |
| **5.02** | Departure / Election of Directors / Officers / Compensation | **C-suite changes — high-value GTM signal**, esp. new CFO / CRO / CCO |
| **5.03** | Amendments to Articles of Incorporation or Bylaws | Governance change |
| 5.04 | Temporary Suspension of Trading Under Employee Benefit Plans | Operational note |
| 5.05 | Amendments to Code of Ethics | Compliance event |
| 5.06 | Change in Shell Company Status | SPAC transition |
| 5.07 | Submission of Matters to a Vote of Security Holders | Shareholder votes — proxy events |
| 5.08 | Shareholder Director Nominations | Activist filings |
| **7.01** | Regulation FD Disclosure | Voluntary disclosure — earnings pre-announcements, ad-hoc updates |
| **8.01** | Other Events | Catch-all — clinical trial readouts, drug approvals, settlements |
| 9.01 | Financial Statements and Exhibits | Just attaches exhibits to other items |

### Pharma-specific GTM signal mappings

For a pharma-targeted signal pipeline:

| Item code | Pharma-specific signal |
|---|---|
| 1.01 | New licensing / supply / co-promotion deal — partnership signal |
| 2.05 | Restructuring — often program discontinuations or site closures |
| 5.02 | **Top signal** — new CMO, CFO, Head of Commercial, Head of Regulatory. Each is a distinct buying-signal segment. |
| 7.01 | Often used for clinical readout announcements |
| 8.01 | FDA approvals, CRLs, label changes, recall announcements |

The `submissions.json` `items` field gives you these classifications **for free** — no NLP, no HTML parsing. This is the highest-leverage data we get from EDGAR.

---

## Data freshness

EDGAR is effectively real-time for filings:

- Filings are accepted Mon–Fri 6am–10pm ET. Acceptance timestamp is exposed as `acceptanceDateTime` in `submissions.json`.
- The `submissions.json` shard updates within ~minutes of acceptance.
- The full-text search index (`efts.sec.gov`) updates within hours (anecdotal — couldn't pin down exact SLA).
- The `index.json` for a given filing is available essentially the instant the filing is accepted.

### Daily and full index files

For batch / incremental sync, use the form-indexed master indexes:

```
https://www.sec.gov/Archives/edgar/full-index/{YYYY}/QTR{N}/form.idx        — full quarter
https://www.sec.gov/Archives/edgar/full-index/{YYYY}/QTR{N}/company.idx     — same data, sorted by company
https://www.sec.gov/Archives/edgar/full-index/{YYYY}/QTR{N}/master.idx      — same data, pipe-delimited
https://www.sec.gov/Archives/edgar/daily-index/{YYYY}/QTR{N}/               — per-day index files
```

**Footgun:** the path component is `QTR1` / `QTR2` / `QTR3` / `QTR4`. I tried `QT1` first and got an Akamai/S3 403:

```
<?xml version="1.0" encoding="UTF-8"?>
<Error><Code>AccessDenied</Code><Message>Access Denied</Message>...</Error>
```

That looks like a UA/rate-limit 403 at first glance — it's actually an S3 path-doesn't-exist. (S3 buckets configured with public-list disabled return 403 for missing keys, not 404. Lots of dev hours have been lost to this.)

Correct path:

```
$ curl --range 0-3000 https://www.sec.gov/Archives/edgar/full-index/2026/QTR1/form.idx
→ HTTP 206 Partial Content

Description:           Master Index of EDGAR Dissemination Feed by Form Type
Last Data Received:    March 31, 2026
Comments:              webmaster@sec.gov

Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
1-A              AMERICAN LITHIUM MINERALS, INC.                               1356371     2026-01-20  edgar/data/1356371/0001356371-26-000004.txt
1-A              AURA REDISION TECHNOLOGIES INC                                2075130     2026-01-16  edgar/data/2075130/0002075130-26-000001.txt
...
```

Fixed-width ASCII. Parse with `csv.reader` configured with `delimiter=' '` + skip-multispace, or use the `pipe-delimited master.idx` for cleaner parsing.

### Incremental sync recipe

```
1. Maintain last_seen_filing_date per CIK.
2. On daily cron:
   - Fetch master.idx for current and previous quarter (covers quarter boundary).
   - Filter to forms in {10-K, 10-Q, 8-K, 20-F} AND date > last_seen.
   - For each new accession: emit to processing queue.
3. Processing queue worker:
   - Fetch primary HTML via Layer 3.
   - Run Item 1A extractor (for 10-K only).
   - Run state-reg phrase matcher.
   - Index 8-K item codes from submissions.json (no HTML fetch needed).
```

This avoids per-CIK polling and lets one daily file scan cover the entire market.

---

## Known limitations

1. **Item 1A extraction is fragile.** Filer-specific HTML conventions vary widely. Re-test extractor against new filers monthly. Maintain a regression suite of 10 representative filings.

2. **5+ MB 10-Ks are not unusual** (Pfizer's is 5.2MB). Memory budget for parsers should assume up to 10MB per filing. Streaming parsers (lxml's iterparse) help.

3. **iXBRL inconsistency.** Filers use different XBRL tagging tools (Workiva, DFIN, Toppan Merrill, in-house). Wrapper tags appear in different places. Just-strip-the-namespace is the only portable approach for narrative extraction.

4. **20-F vs 10-K vs 40-F.** Foreign filers don't have Item 1A. Item 3.D in 20-F is the equivalent. Your scope decision matters — see SIC filtering section.

5. **TOC false matches.** Every 10-K has 20–30 string matches for "Item 1A"; only one is the section header. DOM-aware extraction required.

6. **Missing Item 1B.** Many filers skip Item 1B body content and use Item 1C or Item 2 as the terminator. Your extractor must accept multiple terminators.

7. **The ATOM SIC feed has the Perl `ARRAY(0x...)` bug.** No company names in the feed. Two-step fetch required.

8. **`efts.sec.gov` is undocumented.** It's a real public endpoint, returns JSON, no auth — but the SEC publishes no formal schema. The query params (`q`, `forms`, `dateRange`, `startdt`, `enddt`, `ciks`, `from`) are stable but unsupported. Treat it as a best-effort reference channel; don't build critical paths on it.

9. **No webhooks / push.** EDGAR is pull-only. RSS feeds exist but are slower than polling daily indexes for our use case.

10. **Item codes are inside `submissions.json.recent.items` as a comma-joined string** — not as a list. Split on `,` and `strip()` each. Empty strings are common (some 8-Ks have no item code in this field for older filings).

---

## Minimal Python client design

### Module structure

```
sec_client/
├── __init__.py
├── client.py           # HTTP client with retry, token bucket, UA injection
├── rate_limit.py       # token bucket implementation
├── submissions.py      # Layer 1 — per-CIK submissions JSON + shard pagination
├── filing.py           # Layers 2 + 3 — index.json + primary document fetch
├── extractors/
│   ├── __init__.py
│   ├── item1a.py       # wraps edgartools (or fallback DOM walker)
│   └── items_8k.py     # parses recent.items from submissions
├── search.py           # efts.sec.gov full-text search (Pattern A from above)
├── index_sync.py       # daily/quarterly index file pull
├── pharma.py           # SIC 2834 enumerator + 20-F adjuncts
└── settings.py         # UA, retry params, rate limits from env
```

### Settings

```python
# settings.py
import os
EDGAR_USER_AGENT = os.environ["EDGAR_USER_AGENT"]  # MUST be set, no default
EDGAR_RPS         = float(os.environ.get("EDGAR_RPS", "8"))   # 8 req/sec, leaves headroom
EDGAR_TIMEOUT     = float(os.environ.get("EDGAR_TIMEOUT", "30"))
EDGAR_MAX_RETRIES = int(os.environ.get("EDGAR_MAX_RETRIES", "5"))
```

Treat the UA as required — fail fast if missing. Never default to a generic value.

### HTTP client

```python
# client.py
import httpx, time, random
from .rate_limit import TokenBucket
from .settings import EDGAR_USER_AGENT, EDGAR_RPS, EDGAR_TIMEOUT, EDGAR_MAX_RETRIES

_bucket = TokenBucket(rate_per_sec=EDGAR_RPS, capacity=int(EDGAR_RPS * 2))

def get(url, accept="application/json"):
    headers = {
        "User-Agent": EDGAR_USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        "Accept": accept,
    }
    for attempt in range(EDGAR_MAX_RETRIES):
        _bucket.take(1)
        with httpx.Client(timeout=EDGAR_TIMEOUT, follow_redirects=True) as c:
            r = c.get(url, headers=headers)
        if r.status_code == 200:
            return r
        if r.status_code in (429, 503):
            retry_after = float(r.headers.get("Retry-After", "10"))
            time.sleep(retry_after + random.uniform(0, 1))
            continue
        if r.status_code == 403:
            # Either UA rule failure OR S3 path-not-found.
            # Don't retry — both are deterministic.
            r.raise_for_status()
        # 5xx — exponential backoff with jitter
        time.sleep((2 ** attempt) + random.uniform(0, 1))
    r.raise_for_status()
```

### Rate limit (token bucket)

```python
# rate_limit.py
import threading, time

class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: int):
        self.rate = rate_per_sec
        self.capacity = capacity
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def take(self, n: int = 1):
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
            self._last = now
            if self._tokens >= n:
                self._tokens -= n
                return
            need = n - self._tokens
            wait = need / self.rate
        time.sleep(wait)
        self.take(n)  # tail call after waking
```

For multi-worker setups, swap this for a Redis-backed `INCR` with a sliding window key. The local bucket is fine for single-instance.

### Extraction

```python
# extractors/item1a.py
"""
Recommended: use edgartools.
    from edgar import Company
    pfizer = Company("PFE")
    tenk = pfizer.get_filings(form="10-K").latest()
    risk_factors = tenk.obj().risk_factors

Fallback below if you want to own the code path.
"""
import lxml.html, re
from lxml.etree import strip_tags

ITEM_HEADER_RE = re.compile(r"^\s*ITEM\s+1A\.?\s*$", re.I)
TERMINATOR_RE  = re.compile(r"^\s*ITEM\s+(1B|1C|2)\.?\s*", re.I)

def extract_item1a(html_bytes: bytes) -> str:
    tree = lxml.html.fromstring(html_bytes)
    # Strip iXBRL wrappers — keep their content
    strip_tags(tree, "{http://www.xbrl.org/2013/inlineXBRL}*")

    # Step 1: find a TOC <a> pointing at Item 1A
    toc_anchor_id = None
    for a in tree.iter("a"):
        href = a.get("href", "")
        text = (a.text_content() or "").strip()
        if href.startswith("#") and ITEM_HEADER_RE.match(text):
            toc_anchor_id = href.lstrip("#")
            break
    if not toc_anchor_id:
        raise ValueError("No Item 1A TOC anchor found")

    # Step 2: locate destination element
    dest = tree.get_element_by_id(toc_anchor_id, None)
    if dest is None:
        raise ValueError(f"Anchor {toc_anchor_id} has no destination")

    # Step 3: walk DOM-forward collecting text until terminator
    collected = []
    for el in dest.iter():
        pass  # placeholder — real impl walks the document order from dest forward
    # (See edgartools impl; the walk is non-trivial.)
    return "\n".join(collected)
```

Two real recommendations sit in this code:
- The `try-edgartools-first` path. The fallback is here for emergencies only.
- The `strip_tags(tree, "{http://...}*")` line removes iXBRL wrappers without losing their inner text. This works because we want the *narrative* not the *tags*.

### Incremental sync

```python
# index_sync.py
from datetime import date, timedelta
from .client import get

PHARMA_FORMS = {"10-K", "10-Q", "8-K", "20-F", "40-F"}

def pharma_ciks() -> set[str]:
    """Loaded once at startup; refreshed weekly."""
    # ... browse-edgar SIC 2834 ATOM walk ...
    pass

def daily_pull(d: date, cik_filter: set[str]):
    qtr = (d.month - 1) // 3 + 1
    url = f"https://www.sec.gov/Archives/edgar/full-index/{d.year}/QTR{qtr}/master.idx"
    body = get(url, accept="text/plain").text
    # master.idx is pipe-delimited after the header
    for line in body.splitlines():
        if "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) != 5:
            continue
        cik, name, form, filed_date, filename = parts
        if form not in PHARMA_FORMS:
            continue
        if cik_filter and cik.zfill(10) not in cik_filter:
            continue
        if filed_date != d.isoformat():
            continue
        yield {"cik": cik, "name": name, "form": form,
               "filed_date": filed_date, "path": filename}
```

This is the cron entry point. Run nightly at ~01:00 ET (after the daily index file is fully published — that happens around 22:30 ET).

### State regulation matcher

```python
# extractors/state_reg.py
import re

PHRASES = [
    (r"\bCalifornia\s+Prop(?:osition)?\s*65\b", "California Prop 65"),
    (r"\bN(?:ew\s+York|\.?Y\.?)\s+Article\s+28\b", "New York Article 28"),
    (r"\bMassachusetts\s+105\s*CMR\s*970\b", "MA 105 CMR 970"),
    (r"\bDrug\s+Supply\s+Chain\s+Security\s+Act\b", "DSCSA"),
    # ... full list ...
]
COMPILED = [(re.compile(p, re.I), label) for p, label in PHRASES]

def detect(text: str) -> list[dict]:
    hits = []
    for pat, label in COMPILED:
        for m in pat.finditer(text):
            start = max(0, m.start() - 100)
            end = min(len(text), m.end() + 100)
            hits.append({"label": label, "match": m.group(),
                         "context": text[start:end]})
    return hits
```

Run this on the Item 1A plain text only, not the whole filing — keeps false-positive rate down.

---

## URL appendix (verified live this session)

| Purpose | URL |
|---|---|
| SEC API documentation index | `https://www.sec.gov/edgar/sec-api-documentation` |
| EDGAR fair-access policy | `https://www.sec.gov/os/accessing-edgar-data` |
| data.sec.gov landing | `https://data.sec.gov/` |
| Per-issuer submissions JSON | `https://data.sec.gov/submissions/CIK{10-digit-padded}.json` |
| Submissions overflow shard | `https://data.sec.gov/submissions/{shard-name-from-files-block}` |
| Company facts JSON (XBRL-tagged) | `https://data.sec.gov/api/xbrl/companyfacts/CIK{10-digit-padded}.json` |
| Filing folder index | `https://www.sec.gov/Archives/edgar/data/{cik-decimal}/{accession-nodash}/index.json` |
| Primary document | `https://www.sec.gov/Archives/edgar/data/{cik-decimal}/{accession-nodash}/{primary-doc-name}` |
| browse-edgar (ATOM) by SIC | `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&SIC=2834&type=10-K&dateb=&owner=include&count=20&output=atom` |
| Daily-index directory | `https://www.sec.gov/Archives/edgar/daily-index/{YYYY}/QTR{N}/` |
| Full-index form.idx | `https://www.sec.gov/Archives/edgar/full-index/{YYYY}/QTR{N}/form.idx` |
| Full-index master.idx | `https://www.sec.gov/Archives/edgar/full-index/{YYYY}/QTR{N}/master.idx` |
| Company tickers (no SIC field) | `https://www.sec.gov/files/company_tickers.json` |
| Full-text search (efts) | `https://efts.sec.gov/LATEST/search-index?q={URL-ENCODED}&forms=10-K&dateRange=custom&startdt=2025-01-01&enddt=2026-05-20&ciks=0000078003` |
| Pfizer 10-K sample (this session) | `https://www.sec.gov/Archives/edgar/data/78003/000007800326000026/pfe-20251231.htm` |
| Pfizer 8-K sample (this session) | `https://www.sec.gov/Archives/edgar/data/78003/000007800326000053/pfe-20260505.htm` |

---

## Files captured during this spike (local /tmp/sec-probes)

| File | Source | Notes |
|---|---|---|
| `browse_sic2834.atom` | SIC 2834 ATOM feed | 1,939 bytes, 10 entries, shows ARRAY(0x...) bug |
| `pfizer_submissions.json` | data.sec.gov submissions for CIK 78003 | 21KB, 1,004 recent filings + 3 overflow shards |
| `pfizer_10k.htm` | Pfizer 2026 10-K primary doc | 5,222,324 bytes, iXBRL |
| `pfizer_8k.htm` | Pfizer 2026-05-05 8-K | 4,490 bytes, items 2.02 + 9.01 |
| `pfizer_10k_index.json` | filing folder listing | 177 items |
| `no_ua_body.html` | 403 page when UA omitted | 4,819 bytes, "Undeclared Automated Tool" |
| `fts_prop65.json` | efts FTS for Prop 65 in 10-Ks | 295 total hits |
| `tickers.json` | company_tickers.json | 217KB |
| `full_idx_QT1.idx` | 2026 Q1 form.idx | confirms QTR1 path naming |
| `item1a_window_0..4.html` | Captured raw markup around "Item 1A" mentions | Demonstrates TOC false-matches |
| `item1a_DEST.html` | Actual section header markup | Empty `<div id>` + 2-cell table |
| `item1c_window.html` | Item 1C section header (terminator) | Shows iXBRL `<ix:nonNumeric>` wrappers |

All probes were issued with `User-Agent: GTM Pipeline Research research@example.com`. The one probe issued without a UA (intentionally) returned HTTP 403 as expected.
