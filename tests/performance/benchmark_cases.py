"""Versioned benchmark-case registry and default thresholds for release S03.

Every budget in the performance / stability contract is represented here as a
``BenchmarkCase`` with a stable ``case_id``, an immutable corpus fingerprint, a
hardware tier, sampling parameters (warmup / repetitions) and the versioned
thresholds that the gates assert against.

Hard rules enforced by this module:

* ``THRESHOLDS_V1`` is the single source of truth for budget numbers used by
  the gates. Relaxing a threshold requires requirement confirmation and a
  changelog entry; it must never be edited merely to make a failing gate pass.
* The development machine (``HardwareTier.DEV_64BIT``) only validates the
  measurement chain and the absence of boundary violations on small samples.
  The authoritative Windows hardware evidence (``HardwareTier.WINDOWS_S05``)
  is produced by release S05.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
from pathlib import Path

# Root of the performance test package (repo-relative filesystem paths resolved
# from the repository root at runtime; no hard-coded absolute paths).
PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]


# ---------------------------------------------------------------------------
# Hardware tiers
# ---------------------------------------------------------------------------
class HardwareTier(StrEnum):
    DEV_64BIT = "dev-64bit"  # typical developer machine (this test host)
    CI_LINUX = "ci-linux"  # CI container budget
    WINDOWS_S05 = "windows-s05"  # authoritative final hardware (release S05)


# Software version affected by these budgets (package version guardrail).
PACKAGE_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Versioned thresholds (single source of truth).
#
# Keys mirror the contract exactly:
#   small-esp.parse.p95          <= 3.0s       (small ESP)
#   medium-esp.parse.p95         <= 30.0s      (medium ESP)
#   medium-esp.parse.rss         <= 1 GiB      (RSS ceiling)
#   ui.heartbeat.p95             <= 200 ms
#   ui.progress.p95              <= 500 ms     (boundary probe, not fake GUI pass)
#   cancel.p95                   <= 1.0s       (side-effect stop latency)
#   concurrent.workers.max        = 3
#   checkpoint-100k.save.p95     <= 100 ms
#   checkpoint-100k.load.p95     <= 100 ms
#   checkpoint-100k.recover      = 100%        (committed-id set must match)
#   session-500.rss.growth       <= 15%
#   archive.budget                versioned below
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Thresholds:
    """Versioned budget set (V1). All positive numbers, seconds or ratio."""

    version: int = 1
    small_esp_parse_p95_s: float = 3.0
    medium_parse_p95_s: float = 30.0
    medium_parse_rss_bytes: int = 1 * 1024 * 1024 * 1024  # 1 GiB
    ui_heartbeat_p95_ms: float = 200.0
    ui_progress_p95_ms: float = 500.0
    cancel_p95_s: float = 1.0
    max_concurrent_workers: int = 3
    checkpoint_100k_save_p95_ms: float = 100.0
    checkpoint_100k_load_p95_ms: float = 100.0
    session_500_rss_growth_ratio: float = 0.15  # <= 15% growth
    # Archive policy budget (see fileops/archive_policy).
    archive_max_files: int = 1000
    archive_max_bytes: int = 512 * 1024 * 1024


# Canonical registry referenced by the gates. Versioned with the thresholds.
THRESHOLDS_V1 = Thresholds()


def corpus_fingerprint(data: bytes) -> str:
    """Stable SHA-256 fingerprint of a canonical benchmark corpus.

    Constant across machines so a corpus drift (an accidental fixture edit)
    surfaces as a fingerprint change instead of a silent measurement change.
    """
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """A fixed, versioned performance or stability workload definition.

    Attributes
    ----------
    case_id:
        Stable identifier referenced by the gates (e.g. ``small-esp``).
    kind:
        Workload family (parse / checkpoint / cancel / session / archive / ui).
    tier:
        Hardware tier the case is calibrated for.
    warmup:
        Number of ignored warm-up iterations before sampling starts.
    repetitions:
        Number of samples collected for percentile computation.
    threshold_p95:
        Default P95 ceiling in seconds (float), when the case is time-based.
    threshold_p50:
        Optional P50 ceiling in seconds (float), when a median gate is needed.
    resource_budget_bytes:
        Optional ceiling on a resource count (e.g. RSS bytes).
    note:
        Boundary / calibration explanation surfaced in reports.
    """

    case_id: str
    kind: str
    tier: HardwareTier
    warmup: int = 1
    repetitions: int = 5
    threshold_p95: float | None = None
    threshold_p50: float | None = None
    resource_budget_bytes: int | None = None
    corpus_sha256: str | None = None
    corpus_bytes: int | None = None
    note: str = ""

    def assert_valid(self) -> None:
        if not self.case_id or not self.kind.strip():
            raise ValueError("benchmark case requires case_id and kind")
        if self.warmup < 0 or self.repetitions < 1:
            raise ValueError("benchmark case requires warmup >= 0 and repetitions >= 1")
        for label, value in (
            ("threshold_p95", self.threshold_p95),
            ("threshold_p50", self.threshold_p50),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"{label} must be positive when provided")
        if self.resource_budget_bytes is not None and self.resource_budget_bytes <= 0:
            raise ValueError("resource budget must be positive when provided")


def _tiny_eet_xml(entries: int) -> bytes:
    """Deterministic medium EET/XML corpus of ``entries`` ESP records."""
    lines: list[str] = ['<?xml version="1.0" encoding="utf-8"?>', "<DocumentElement>"]
    for i in range(entries):
        lines.extend([
            "  <ESP>",
            "    <GRUP>INFO</GRUP>",
            f"    <EDID>Topic{i}</EDID>",
            "    <CHAMP>NAM1</CHAMP>",
            f"    <ORIGINAL>Hello traveler number {i}.</ORIGINAL>",
            "    <TRADUIT></TRADUIT>",
            f"    <INDEX>{i}</INDEX>",
            "    <STATUS>0</STATUS>",
            "  </ESP>",
        ])
    lines.append("</DocumentElement>")
    return ("\n".join(lines) + "\n").encode("utf-8")


def medium_eet_corpus(entries: int = 10000) -> bytes:
    """Build the canonical small/medium EET corpus bytes (deterministic)."""
    return _tiny_eet_xml(entries)


_SMALL_ESP_REL = "tests/parser/data/sample.esp"


def small_esp_bytes() -> bytes:
    """Raw bytes of the real small ESP fixture used by the small-ESP gate."""
    path = REPO_ROOT / _SMALL_ESP_REL
    if not path.exists():
        raise FileNotFoundError(f"small ESP corpus not found: {path}")
    return path.read_bytes()


def _build_registry() -> tuple[BenchmarkCase, ...]:
    threshold = THRESHOLDS_V1
    small_esp = small_esp_bytes()
    medium = medium_eet_corpus(entries=10000)
    return (
        BenchmarkCase(
            case_id="small-esp",
            kind="parse",
            tier=HardwareTier.DEV_64BIT,
            warmup=1,
            repetitions=5,
            threshold_p95=threshold.small_esp_parse_p95_s,
            corpus_sha256=corpus_fingerprint(small_esp),
            corpus_bytes=len(small_esp),
            note="real 3.95MB plugin fixture; P95<=3s on dev, hard budget set by S05",
        ),
        BenchmarkCase(
            case_id="medium-parse",
            kind="parse",
            tier=HardwareTier.DEV_64BIT,
            warmup=1,
            repetitions=3,
            threshold_p95=threshold.medium_parse_p95_s,
            resource_budget_bytes=threshold.medium_parse_rss_bytes,
            corpus_sha256=corpus_fingerprint(medium),
            corpus_bytes=len(medium),
            note="constructed ~2MB EET (10k records); chain+boundary only, hard budget by S05",
        ),
        BenchmarkCase(
            case_id="ui-heartbeat",
            kind="ui",
            tier=HardwareTier.DEV_64BIT,
            warmup=2,
            repetitions=20,
            threshold_p95=threshold.ui_heartbeat_p95_ms / 1000.0,
            note="event-loop heartbeat boundary probe; GUI automation limited, see report",
        ),
        BenchmarkCase(
            case_id="ui-progress",
            kind="ui",
            tier=HardwareTier.DEV_64BIT,
            warmup=2,
            repetitions=20,
            threshold_p95=threshold.ui_progress_p95_ms / 1000.0,
            note="progress-update delivery probe; boundary only, not a fake GUI pass",
        ),
        BenchmarkCase(
            case_id="cancel-100",
            kind="cancel",
            tier=HardwareTier.DEV_64BIT,
            warmup=0,
            repetitions=1,
            threshold_p95=threshold.cancel_p95_s,
            note="100 fake LLM calls, concurrency<=3, cancel-to-side-effect-stop latency P95<=1s",
        ),
        BenchmarkCase(
            case_id="checkpoint-100k",
            kind="checkpoint",
            tier=HardwareTier.DEV_64BIT,
            warmup=1,
            repetitions=3,
            threshold_p95=threshold.checkpoint_100k_save_p95_ms / 1000.0,
            note=(
                "budget entity = 100k-entry task checkpoint save/load P95<=100ms; "
                "the fast gate uses a calibrated 10000-entry sample to validate the chain "
                "and the 100ms boundary; hard 100k evidence by S05"
            ),
        ),
        BenchmarkCase(
            case_id="session-500",
            kind="session",
            tier=HardwareTier.DEV_64BIT,
            warmup=1,
            repetitions=1,
            threshold_p50=None,
            note="500-round session aggregate RSS growth <=15% (psutil, tracemalloc fallback)",
        ),
        BenchmarkCase(
            case_id="archive-budget",
            kind="archive",
            tier=HardwareTier.DEV_64BIT,
            warmup=0,
            repetitions=1,
            note="archive policy budget boundary probe (archive_max_files/max_bytes)",
        ),
    )


# Stable registry exported to the gates and consumed by measure / reports.
BENCHMARK_CASES: dict[str, BenchmarkCase] = {case.case_id: case for case in _build_registry()}


def get_case(case_id: str) -> BenchmarkCase:
    """Return a validated benchmark case by id."""
    case = BENCHMARK_CASES.get(case_id)
    if case is None:
        raise KeyError(f"unknown benchmark case: {case_id}")
    case.assert_valid()
    return case
