# Architecture

Stub — filled in during build task 12.

## Diagram

```mermaid
flowchart LR
    Cron[GitHub Actions cron 6h] --> Main[main.py]
    Trigger[POST /trigger on Modal] --> Main
    Main --> Sources
    subgraph Sources
        OS[OpenStates]
        LDA[LDA]
        EDGAR[SEC EDGAR]
        ALEC[ALEC corpus]
    end
    Sources --> Enrich
    subgraph Enrich
        EMB[Claude embeddings]
        EXT[Claude extraction]
        ICP[ICP matcher]
    end
    Enrich --> Detect
    subgraph Detect
        SA[Signal A]
        SC[Signal C]
        SD[Signal D3]
    end
    Detect --> Score
    Score --> Filter{score ≥ 70?}
    Filter -- yes --> Slack[Slack alert / stdout]
    Filter -- 50-70 --> Watchlist[watchlist.jsonl]
    Filter -- <50 --> Drop[drop]
```

## Design rationale

- Fixtures-first dev mode keeps iteration fast and cheap. Live mode only proves the wiring.
- Three signals chosen for non-obviousness and load-bearing enrichment, not breadth.
- Precision > recall by design — rep trust is the binding constraint.
- No DB in v1; JSON on disk + in-memory state are sufficient.

## Source-by-source notes

See `source-research/`.
