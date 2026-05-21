"""Signal detector package.

Public API:
- Signal: dataclass that every detector emits.
- detect_signal_a, detect_signal_c, detect_signal_d3: per-signal detectors.

Each detector receives the pre-fetched data it needs (so the pipeline avoids
duplicate source calls) and returns a list of Signal objects ready for scoring.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SignalType = Literal["A", "C", "D3"]


@dataclass
class Signal:
    signal_type: SignalType
    company_cik: str
    company_name: str
    title: str
    why_now: str
    evidence: dict[str, Any] = field(default_factory=dict)
    score_inputs: dict[str, float] = field(default_factory=dict)

    def key(self) -> str:
        """Stable id for dedup / logging."""
        anchor = (
            self.evidence.get("cluster_id")
            or self.evidence.get("filing", {}).get("accession")
            or self.evidence.get("model_bill_id")
            or self.title
        )
        return f"{self.signal_type}:{self.company_cik}:{anchor}"


from signals.detectors.signal_a import detect_signal_a  # noqa: E402
from signals.detectors.signal_c import detect_signal_c  # noqa: E402
from signals.detectors.signal_d import detect_signal_d3  # noqa: E402

__all__ = ["Signal", "detect_signal_a", "detect_signal_c", "detect_signal_d3"]
