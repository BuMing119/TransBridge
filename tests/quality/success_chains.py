"""Success-chain / cross-entrypoint parity support assets for Release S02.

This module backs ``test_success_chains.py``. It models the requirement that
every P0 capability has at least one *real* success chain (a genuine fixture or
a controlled integration chain that exercises the real serialization/composition
root — never a mock-only substitute), and that the same use case produces the
same normalized result across entrypoints (GUI / Agent / controlled HTTP / FOMOD).

Design intent
-------------
- ``SuccessChain`` is a value object describing one chain: a runner thunk that
  produces a fresh result on demand, zero or more alternate ``entrypoints`` for
  cross-entrypoint parity, a ``normalizer`` and an optional ``semantic_assert``.
- ``FIXTURE_CHECKSUMS`` is the anti-drift registry: every real fixture's sha256 is
  recorded here and verified (``preflight``) before a chain is run.
- ``summarize`` turns a raw result (ParseResult / OperationResult / FomodReport /
  ChangeSet / MutationResult / already-summarized dict) into a comparable,
  path-free and time-free dictionary.
- ``run_chain_deterministic`` repeats a chain and asserts the normalized output is
  stable; ``assert_entrypoint_parity`` asserts all entrypoints agree.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any

from transbridge.application.contracts import OperationResult
from transbridge.application.fomod import PipelineResult
from transbridge.application.io import ChangeSet, MutationResult, ParseResult

# Repository root: tests/quality/ -> repository root.
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class FixtureIntegrityError(RuntimeError):
    """Raised when a registered fixture's real bytes drift from the recorded hash."""


# sha256 registry of every real fixture referenced by S02 success chains.
FIXTURE_CHECKSUMS: dict[str, str] = {
    "tests/contracts/io/fixtures/eet-small.xml": (
        "95d6266b9fb3b6ec2b16281dc7d3b79f3d050c2ec3b6185e4b8fe173968950bc"
    ),
    "tests/contracts/io/fixtures/xt-small.xml": (
        "ca024ffffd7f2dea3348a42c04e454aa2324b7d3a64dd5454882aab9ed3780dd"
    ),
    "tests/contracts/io/fixtures/paratranz_dual_id.json": (
        "410ec78fd7a22219469f9c77f26d8daa0dee559416b47de6796db301024dad08"
    ),
    "tests/contracts/io/fixtures/strings/integrity.strings": (
        "47a0b1fec1a4bdfad84b7ffd67cf472e436f0e0c97a7a1531852d7845f26a678"
    ),
    "tests/contracts/io/fixtures/strings/integrity.dlstrings": (
        "5fce460b0451040e47aedd11a5cb5e3c309cd3b188969dfdc0bdabacc1dc7cc6"
    ),
    "tests/contracts/io/fixtures/strings/integrity.ilstrings": (
        "631d767133fb75d4a1280f9df224a39647c4e9b3de5460947b36a6eed789778f"
    ),
    "tests/parser/data/sample.esp": (
        "1701df1f8ccf08279751b84fe064df921d9304dcb4644883b9bb396c9f1f4f9f"
    ),
}


def chain_sha256(path: str | Path) -> str:
    """Return the lowercase sha256 of a fixture file."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_fixture_checksums(
    registry: dict[str, str] | None = None,
    root: str | Path = REPOSITORY_ROOT,
) -> None:
    """Verify every registered fixture hash; raise :class:`FixtureIntegrityError` on any drift."""
    for relative, expected in (registry or FIXTURE_CHECKSUMS).items():
        path = Path(root) / relative
        if not path.is_file():
            raise FixtureIntegrityError(f"fixture missing: {relative}")
        actual = chain_sha256(path)
        if actual != expected:
            raise FixtureIntegrityError(
                f"fixture checksum drift: {relative}\n"
                f"  expected {expected}\n  actual   {actual}"
            )


def _diagnostic_codes(diagnostics: Any) -> list[str]:
    return sorted({item.code for item in (diagnostics or ())})


def _basenames(paths: Any) -> list[str]:
    return sorted(str(Path(item).name) for item in (paths or ()))


def _count_dict(counts: Any) -> dict[str, int]:
    if counts is None:
        return {}
    return {
        "succeeded": int(getattr(counts, "succeeded", 0)),
        "failed": int(getattr(counts, "failed", 0)),
        "skipped": int(getattr(counts, "skipped", 0)),
        "cancelled": int(getattr(counts, "cancelled", 0)),
        "total": int(getattr(counts, "total", 0) or 0),
    }


def _stats_dict(stats: Any) -> dict[str, int]:
    if stats is None:
        return {}
    return {
        "parsed": int(getattr(stats, "parsed", 0)),
        "failed": int(getattr(stats, "failed", 0)),
        "skipped": int(getattr(stats, "skipped", 0)),
        "cancelled": int(getattr(stats, "cancelled", 0)),
        "total": int(getattr(stats, "total", 0) or 0),
    }


def _artifact_summary(artifacts: Any) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for artifact in artifacts or ():
        kind = getattr(artifact, "kind", None)
        fingerprint = getattr(artifact, "fingerprint", None)
        attribute = getattr(artifact, "attribute", None)
        try:
            a_locale = attribute("target_locale") if attribute is not None else None
        except Exception:  # pragma: no cover - attribute may be strict
            a_locale = None
        summary.append(
            {
                "kind": kind,
                "fingerprint": fingerprint,
                "target_locale": a_locale,
            }
        )
    return sorted(summary, key=lambda item: (item["kind"] or "", item["fingerprint"] or ""))


def summarize(result: Any) -> dict[str, Any]:
    """Return a comparable, path-free/time-free summary of one result.

    Supported inputs: :class:`ParseResult`, :class:`OperationResult`,
    :class:`FomodReport`, :class:`ChangeSet`, :class:`MutationResult`, and any
    already-summarized mapping (passed through unchanged). Absolute paths are
    reduced to basenames; volatile switches (run timestamps, wall-clock) are not
    emitted, so seeded ``run_id`` values make the summary deterministic.
    """
    if isinstance(result, dict):
        return dict(result)

    if isinstance(result, ParseResult):
        return {
            "kind": "parse",
            "outcome": result.outcome.value if result.outcome else None,
            "format_id": result.format_id.value if getattr(result, "format_id", None) else None,
            "entries": len(result.entries),
            "stats": _stats_dict(result.stats),
            "diagnostics": _diagnostic_codes(result.diagnostics),
            "adapter_id": result.adapter_id,
            "source_sha256": (
                result.source_snapshot.sha256 if result.source_snapshot is not None else None
            ),
        }

    if isinstance(result, PipelineResult):
        return {
            "kind": "fomod",
            "outcome": result.outcome.value if result.outcome else None,
            "run_id": result.run_id,
            "stages": [item.stage.value for item in result.stages],
            "artifacts": _artifact_summary(result.artifacts),
            "diagnostics": _diagnostic_codes(result.diagnostics),
        }

    if isinstance(result, OperationResult):
        value_artifacts: list[str] = []
        accepted: int | None = None
        if isinstance(result.value, (tuple, list)):
            value_artifacts = _basenames(result.value)
        elif result.value is not None:
            accepted = getattr(result.value, "accepted_count", None)
        summary = {
            "kind": "operation",
            "outcome": result.outcome.value if result.outcome else None,
            "counts": _count_dict(result.counts),
            "value_artifacts": value_artifacts,
            "artifact_refs": _basenames(result.artifact_refs),
            "diagnostics": _diagnostic_codes(result.diagnostics),
            "run_id": result.run_id,
        }
        if accepted is not None:
            summary["accepted_count"] = int(accepted)
        return summary

    if isinstance(result, ChangeSet):
        return {
            "kind": "changeset",
            "run_id": result.run_id,
            "change_count": len(result.patches),
        }

    if isinstance(result, MutationResult):
        return {
            "kind": "mutation",
            "status": getattr(result.status, "value", result.status),
            "run_id": result.run_id,
        }

    # Fallback: lossy but still comparable.
    return {"unsummarized": type(result).__name__}


@dataclass(frozen=True)
class SuccessChain:
    """One real success chain value object."""

    chain_id: str
    runner: Callable[[], Any]
    entrypoints: tuple[Callable[[], Any], ...] = ()
    normalizer: Callable[[Any], Any] = summarize
    semantic_assert: Callable[[Any], None] | None = None
    fixture_path: str | None = None
    checksum: str | None = None
    description: str = ""

    def preflight(self) -> None:
        """Verify fixture checksums before running (anti-drift guard)."""
        if self.fixture_path and self.checksum:
            actual = chain_sha256(REPOSITORY_ROOT / self.fixture_path)
            if actual != self.checksum:
                raise FixtureIntegrityError(f"[{self.chain_id}] fixture drifted: {self.fixture_path}")


def run_chain_deterministic(chain: SuccessChain, repetitions: int = 3) -> Any:
    """Run a chain ``repetitions`` times and assert the normalized output is stable."""
    if repetitions < 1:
        raise ValueError("repetitions must be >= 1")
    chain.preflight()
    outputs = [chain.normalizer(chain.runner()) for _ in range(repetitions)]
    first = outputs[0]
    for index, output in enumerate(outputs[1:], start=2):
        if output != first:
            raise AssertionError(
                f"chain {chain.chain_id!r} is not deterministic between run 1 and run {index}:\n"
                f"  run 1 = {first}\n  run {index} = {output}"
            )
    if chain.semantic_assert is not None:
        chain.semantic_assert(chain.normalizer(chain.runner()))
    return first


def assert_entrypoint_parity(chain: SuccessChain) -> Any:
    """Run every entrypoint on the same use case and assert normalized parity."""
    if len(chain.entrypoints) < 2:
        raise ValueError(f"chain {chain.chain_id!r} needs at least 2 entrypoints for parity")
    chain.preflight()
    outputs = [chain.normalizer(entrypoint()) for entrypoint in chain.entrypoints]
    first = outputs[0]
    names = [f"entrypoint-{index}" for index in range(len(outputs))]
    for name, output in zip(names[1:], outputs[1:]):
        if output != first:
            raise AssertionError(
                f"chain {chain.chain_id!r} entrypoints differ: {names[0]}={first} {name}={output}"
            )
    return first


__all__ = [
    "FIXTURE_CHECKSUMS",
    "FixtureIntegrityError",
    "REPOSITORY_ROOT",
    "SuccessChain",
    "assert_entrypoint_parity",
    "chain_sha256",
    "run_chain_deterministic",
    "summarize",
    "verify_fixture_checksums",
]
