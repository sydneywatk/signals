# Fixtures

Layout: `<source>/<scenario>.json`.

Each fixture is one captured response from a real external API. Fixtures are
committed to the repo and serve double duty as test data.

To seed or refresh, run `python scripts/capture_fixtures.py` with live
credentials in `.env`. See `scripts/capture_fixtures.py` for the scenario
registry.

Required seed coverage per `docs/SPEC.md` §6.6:

- **openstates/** — ≥1 `recent_bills` and ≥1 `bill_text`
- **lda/** — ≥1 `recent_registrations`
- **edgar/** — ≥1 `submissions.json`, ≥1 10-K Item 1A text, ≥1 8-K with item codes
- **anthropic/** — ≥1 embedding response and ≥1 extraction per prompt template
