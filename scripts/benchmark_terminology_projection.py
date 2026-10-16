"""Measure prepared and complete terminology projection on fixed synthetic snapshots.

Defaults: 1k/10k mappings, 1000 entries, five repetitions. Each size constructs
one immutable profile/base and one entry tuple outside timing. Each repetition
prepares a fresh projector, then reuses that exact instance for the warm batch.
No database, Qt, network, or LLM is involved. Cold means fresh projection indexes,
not a fresh interpreter, regex cache, or operating-system cache.

An optional trusted source archive loads ONLY its projection.py and
entry_projection.py under an isolated namespace. Both implementations share the
current models/effective dependencies and identical fixture objects. This is an
isolated S06 module comparison, not a full historical application benchmark.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from types import ModuleType
from zipfile import ZipFile

from transbridge.application.terminology import effective as effective_module
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_profiles import entry_projection, models, projection
from transbridge.converter.translation_entry import TranslationEntry

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PREFIX = "src/transbridge/application/terminology_profiles/"
MODULE_NAMES = ("projection", "entry_projection")
PROJECT, VARIANT, PLUGIN = "projection-benchmark", "synthetic-variant", "Synthetic.esm"


@dataclass(frozen=True)
class Fixture:
    profile: models.PublishedTerminologyProfile
    base: EffectiveTerminologySnapshot
    entries: tuple[TranslationEntry, ...]
    calls: tuple[dict, ...]
    expected: tuple[tuple[str, str], ...]


def make_fixture(mapping_count: int, entry_count: int) -> Fixture:
    mappings = tuple(
        models.ProfileTermMapping(
            f"Term {index:06d}" if index % 2 == 0 else f"Record common Unique{index:06d}",
            f"NewName{index:06d}",
            f"OldName{index:06d}",
            scope_kind="plugin" if index % 5 == 0 else "project",
            plugin_id=PLUGIN if index % 5 == 0 else None,
        )
        for index in range(mapping_count)
    )
    content = models.TerminologyProfileContent(mappings)
    profile = models.PublishedTerminologyProfile(
        "fixed-profile", PROJECT, 1, "Synthetic names", content.content_digest, content, "2026-10-15T00:00:00Z"
    )
    decisions = tuple(
        TermDecision(
            f"term-{index:06d}",
            PROJECT,
            VARIANT,
            item.original,
            models.normalize_term(item.original),
            item.base_translation,
            TermScope.plugin(PLUGIN) if item.plugin_id else TermScope.project(),
            DecisionStatus.ADOPTED,
        )
        for index, item in enumerate(mappings)
    )
    base = EffectiveTerminologySnapshot(
        PROJECT, VARIANT, EffectiveSnapshotStatus.READY, "synthetic-base", "b" * 64, decisions
    )
    entries, calls, expected = [], [], []
    for index in range(entry_count):
        # Spread references across the full mapping range, including its tail.
        selected = (index * (mapping_count - 1)) // max(entry_count - 1, 1)
        mapping = mappings[selected]
        entry = TranslationEntry(
            str(index),
            f"entry-{index:06d}",
            f"Visit {mapping.original} then leave.",
            f"Visit {mapping.base_translation} then leave.",
            1,
            None,
            metadata=(("terminology_plugin_id", PLUGIN),) if mapping.plugin_id else (),
        )
        entries.append(entry)
        calls.append({
            "entry_key": entry.identity.serialize(),
            "original": entry.original,
            "common_translation": entry.translation,
            "plugin_id": mapping.plugin_id,
        })
        expected.append((f"Visit {mapping.translation} then leave.", mapping.term_key))
    return Fixture(profile, base, tuple(entries), tuple(calls), tuple(expected))


def _check(results, fixture):
    assert len(results) == len(fixture.expected)
    for result, (translation, term_key), entry, call in zip(
        results, fixture.expected, fixture.entries, fixture.calls, strict=True
    ):
        assert result.translation == translation
        assert result.changed_term_keys == (term_key,)
        assert not result.diagnostics and not result.used_override
        assert result.common_translation == entry.translation == call["common_translation"]


def _stats(values):
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def measure(implementation, fixture, repeats, *, complete):
    samples = []
    for iteration in range(repeats):
        gc.collect()
        # Legacy indexes used a module-global LRU. Reset only between cold
        # repetitions; never clear or reconstruct them inside the warm batch.
        cache = getattr(implementation.projection, "_profile_indexes", None)
        clear = getattr(cache, "cache_clear", None)
        if clear is not None:
            clear()
        started = time.perf_counter()
        if complete:
            projector = implementation.entry_projection.ProfileEntryProjector(fixture.profile, fixture.base)
            prepared = time.perf_counter()
            first = projector.project(fixture.entries[0])
        else:
            projector = implementation.projection.TerminologyProfileProjector()
            prepare = getattr(projector, "prepare", None)
            if prepare is None:
                prepared = None  # Legacy preparation happens lazily in project().
                first = projector.project(content=fixture.profile.content, **fixture.calls[0])
            else:
                projector = prepare(fixture.profile.content)
                prepared = time.perf_counter()
                first = projector.project(**fixture.calls[0])
        first_done = time.perf_counter()
        first_expected = fixture.expected[0]
        assert first.translation == first_expected[0] and first.changed_term_keys == (first_expected[1],)
        assert not first.diagnostics and not first.used_override
        batch_started = time.perf_counter()
        if complete:
            results = tuple(projector.project(entry) for entry in fixture.entries)
        elif prepare is None:
            results = tuple(projector.project(content=fixture.profile.content, **call) for call in fixture.calls)
        else:
            results = tuple(projector.project(**call) for call in fixture.calls)
        batch_done = time.perf_counter()
        _check(results, fixture)  # Correctness and unchanged inputs are outside timing.
        samples.append({
            "iteration": iteration + 1,
            "constructor_or_prepare_ms": None if prepared is None else (prepared - started) * 1000,
            "first_projection_including_prepare_ms": (first_done - started) * 1000,
            "warm_batch_ms": (batch_done - batch_started) * 1000,
        })
    metrics = {}
    for key in samples[0]:
        if key != "iteration" and samples[0][key] is not None:
            metrics[key] = _stats([item[key] for item in samples])
    path = "ProfileEntryProjector.project"
    if not complete:
        path = (
            "PreparedProfileProjector.project"
            if hasattr(implementation.projection.TerminologyProfileProjector, "prepare")
            else "TerminologyProfileProjector.project"
        )
    return {
        "implementation": implementation.label,
        "scenario": "complete_entry" if complete else "term_projection",
        "path": path,
        "mapping_count": len(fixture.profile.content.mappings),
        "entry_count": len(fixture.entries),
        "content_digest": fixture.profile.content_digest,
        "samples": samples,
        "metrics_ms": metrics,
    }


@dataclass(frozen=True)
class Implementation:
    label: str
    projection: ModuleType
    entry_projection: ModuleType
    sources: dict[str, str]


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def load_baseline(archive: Path, directory: Path) -> Implementation:
    """Extract only known module members into a new directory, never archive paths."""
    with ZipFile(archive) as bundle:
        sources = {name: bundle.read(f"{SOURCE_PREFIX}{name}.py") for name in MODULE_NAMES}
    directory.mkdir(parents=True, exist_ok=False)
    package_name = "_terminology_projection_benchmark_before"
    package = ModuleType(package_name)
    package.__path__ = [str(directory)]
    sys.modules[package_name] = package
    sys.modules[f"{package_name}.models"] = models
    loaded = {}
    for name, source in sources.items():
        path = directory / f"{name}.py"
        path.write_bytes(source)
        spec = importlib.util.spec_from_file_location(f"{package_name}.{name}", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load isolated baseline module: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return Implementation(
        "archived_s06_modules",
        loaded["projection"],
        loaded["entry_projection"],
        {name: _digest(source) for name, source in sources.items()},
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="new JSON report path; existing files are rejected")
    parser.add_argument("--sizes", nargs="+", type=int, default=[1000, 10000])
    parser.add_argument("--entries", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--baseline-archive", type=Path, help="optional trusted before-worktree.zip")
    parser.add_argument(
        "--baseline-directory", type=Path, help="new isolated extraction directory, required with archive"
    )
    args = parser.parse_args()
    if args.repeats < 5 or args.entries < 1 or any(size < 1 for size in args.sizes):
        parser.error("at least five repetitions and positive sizes/entries are required")
    if args.output.exists():
        parser.error("output must be a new path")
    if bool(args.baseline_archive) != bool(args.baseline_directory):
        parser.error("baseline archive and directory must be provided together")
    if args.baseline_directory is not None and args.baseline_directory.exists():
        parser.error("baseline directory must be a new path")
    implementations = [
        Implementation(
            "current",
            projection,
            entry_projection,
            {name: _digest((ROOT / SOURCE_PREFIX / f"{name}.py").read_bytes()) for name in MODULE_NAMES},
        )
    ]
    if args.baseline_archive:
        implementations.insert(0, load_baseline(args.baseline_archive, args.baseline_directory))
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True)
    report = {
        "scenario": "transbridge.synthetic-terminology-projection.v1",
        "status": "running",
        "python": sys.version,
        "os": platform.platform(),
        "git_head": revision.stdout.strip(),
        "command": sys.argv,
        "synthetic": True,
        "fixture": "shared prefixes, rare ASCII tokens, 20% plugin-scoped mappings; fixed snapshots",
        "timing": "fresh projector per repeat; first projection includes preparation; warm batch reuses that projector",
        "constructor_timing": "excludes legacy lazy index work; compare first projection including prepare instead",
        "comparison": "optimized-only"
        if len(implementations) == 1
        else "isolated archived S06 modules; current dependencies shared",
        "baseline_archive": None if args.baseline_archive is None else str(args.baseline_archive.resolve()),
        "source_sha256": {item.label: item.sources for item in implementations},
        "shared_dependency_sha256": {
            module.__name__: _digest(Path(module.__file__).read_bytes()) for module in (models, effective_module)
        },
        "results": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    for size in args.sizes:
        fixture = make_fixture(size, args.entries)
        for implementation in implementations:
            for complete in (False, True):
                result = measure(implementation, fixture, args.repeats, complete=complete)
                report["results"].append(result)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(
                    json.dumps({key: value for key, value in result.items() if key != "samples"}, ensure_ascii=False),
                    flush=True,
                )
    report["status"] = "complete"
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
