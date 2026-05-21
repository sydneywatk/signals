# signals

GTM signal pipeline for State Affairs. Detects buying moments inside the US
mid-market pharmaceutical segment (SIC 2834) and routes prioritized leads to the
sales team via Slack.

This README is a stub. See [docs/SPEC.md](docs/SPEC.md) for the full build spec
and [docs/source-research/](docs/source-research/) for source-by-source research.

## Status

Under active build. Tracking against [docs/SPEC.md](docs/SPEC.md) section 13.

## Quick start

```bash
git clone https://github.com/sydneywatk/signals.git
cd signals
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # fill in real keys for live mode (optional)
python -m signals.main  # runs in fixture mode by default
```

## Three signals

- **Signal A** — Coordinated multistate legislative waves
- **Signal C** — SEC 8-K material event matching active state bills
- **Signal D3** — Model bill propagation velocity

Detail in [docs/SPEC.md](docs/SPEC.md) §3.
