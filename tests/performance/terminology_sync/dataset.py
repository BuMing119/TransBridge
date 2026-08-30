"""Fixed workload descriptions without invented release thresholds."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True, slots=True)
class TerminologySyncPerformanceProfile:
    name: str
    seed: int
    local_terms: int
    remote_terms: int
    page_size: int
    baseline_ratio: float
    independent_ratio: float
    conflict_ratio: float
    delete_ratio: float
    lossy_ratio: float
    ui_page_size: int

    def __post_init__(self) -> None:
        if not self.name.strip() or self.seed < 0:
            raise ValueError("performance profile requires a name and non-negative seed")
        if min(self.local_terms, self.remote_terms, self.page_size, self.ui_page_size) < 1:
            raise ValueError("performance profile counts and page sizes must be positive")
        ratios = (
            self.baseline_ratio,
            self.independent_ratio,
            self.conflict_ratio,
            self.delete_ratio,
            self.lossy_ratio,
        )
        if any(value < 0 or value > 1 for value in ratios):
            raise ValueError("performance profile ratios must be between zero and one")

    @property
    def dataset_digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


DIAGNOSTIC_PROFILES = (
    TerminologySyncPerformanceProfile("regular", 517_080_001, 10_000, 11_000, 500, 0.8, 0.1, 0.02, 0.02, 0.01, 100),
    TerminologySyncPerformanceProfile("stress", 517_080_002, 50_000, 55_000, 500, 0.8, 0.1, 0.02, 0.02, 0.01, 100),
)


__all__ = ["DIAGNOSTIC_PROFILES", "TerminologySyncPerformanceProfile"]
