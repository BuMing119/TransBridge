"""Synthetic SQLite terminology benchmarks; never open or overwrite user fixtures.

Cold means fresh application adapters, not a flushed OS disk cache. Fixture
creation and repository opening are outside query/save timers. The legacy
sequence is retained separately for comparison with the 2026-10-15 audit.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import UTC, datetime
import gc
import json
from pathlib import Path
import platform
import sqlite3
import statistics
import sys
from threading import Event, Thread
import time
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.application.terminology.story08_support import build  # noqa: E402
from tests.performance.measure import current_rss_bytes  # noqa: E402
from transbridge.application.contracts import RequestContext  # noqa: E402
from transbridge.application.terminology import decisions as decision_module, drafts as draft_module  # noqa: E402
from transbridge.application.terminology.browse_queries import (  # noqa: E402
    TermBrowseQuery,
    TermFilter,
    TerminologyBrowseQueries,
)
from transbridge.application.terminology.drafts import new_draft  # noqa: E402
from transbridge.application.terminology.models import (  # noqa: E402
    BuildCompleteness,
    CanonicalDiff,
    DecisionStatus,
    TermDecision,
    TerminologyVersion,
    TerminologyVersionRef,
)
from transbridge.application.terminology.ports import PageRequest  # noqa: E402
from transbridge.application.terminology.publish import terminology_version_content_digest  # noqa: E402
from transbridge.application.terminology_profiles.browse_queries import (  # noqa: E402
    ProfiledTerminologyBrowseSnapshotPort,
)
from transbridge.application.terminology_profiles.models import (  # noqa: E402
    ProfileTermMapping,
    TerminologyProfileContent,
)
from transbridge.application.terminology_profiles.service import TerminologyProfileService  # noqa: E402
from transbridge.bootstrap.adapters import SystemClock, UuidGenerator  # noqa: E402
from transbridge.bootstrap.terminology_storage import ProductionState  # noqa: E402
from transbridge.bootstrap.terminology_workloads import ProductionTerminologyCommands  # noqa: E402
from transbridge.persistence.terminology import drafts as draft_storage, repository as repository_module  # noqa: E402
from transbridge.persistence.terminology.effective import SqliteEffectiveTerminologySnapshotPort  # noqa: E402
from transbridge.persistence.terminology.paths import TerminologyPaths  # noqa: E402
from transbridge.persistence.terminology.repository import SqliteTerminologyRepository  # noqa: E402
from transbridge.ui.tools.terminology.asset_snapshot import read_assets  # noqa: E402

PROJECT, VARIANT = "project-1", "variant-1"
FIXTURE_KIND = "transbridge.synthetic-terminology-storage.v1"
MANIFEST = "synthetic-fixture.json"


def create_fixture(path: Path, count: int) -> Path:
    """Create and close one fresh, audit-compatible fixture; reject existing roots."""
    if count < 1:
        raise ValueError("fixture count must be positive")
    terms = tuple(
        TermDecision(
            f"term-{i:06}",
            PROJECT,
            VARIANT,
            f"Term {i:06}",
            f"term {i:06}",
            f"术语 {i:06}",
            status=DecisionStatus.ADOPTED,
        )
        for i in range(count)
    )
    return _create_fixture(Path(path), terms, profile=True)


def _create_fixture(path, terms, *, profile):
    path.mkdir(parents=True, exist_ok=False)
    repository = SqliteTerminologyRepository.open(str(path), PROJECT)
    try:
        source = build()
        repository.put_build(source)
        version = TerminologyVersion(
            TerminologyVersionRef("version-1", PROJECT, VARIANT, "pending"),
            None,
            source.ref,
            1,
            1,
            BuildCompleteness.FULL,
            "2026-10-15T00:00:00Z",
            terms,
            CanonicalDiff(None, "version-1", "empty", ()),
        )
        version = replace(version, ref=replace(version.ref, content_digest=terminology_version_content_digest(version)))
        repository.publish_version(version, expected_effective_version_id=None)
        repository.create_draft(
            new_draft(
                draft_id="draft-1",
                project_id=PROJECT,
                variant_id=VARIANT,
                base_version_id="version-1",
                base_content_digest=version.ref.content_digest,
                decisions=terms,
            )
        )
        if profile:
            profiles = TerminologyProfileService(repository.localization_profiles)
            mappings = tuple(ProfileTermMapping(term.original, "方案 " + term.translation) for term in terms)
            item = profiles.create_with_content(PROJECT, "Benchmark", TerminologyProfileContent(mappings))
            profiles.publish(item.profile_id, expected_draft_revision=0)
            profiles.select(PROJECT, VARIANT, item.profile_id)
        (path / MANIFEST).write_text(
            json.dumps({"kind": FIXTURE_KIND, "count": len(terms), "profile": profile}), encoding="utf-8"
        )
    finally:
        repository.close()
    return path


def _require_fixture(path):
    marker = Path(path) / MANIFEST
    if not marker.is_file():
        raise ValueError(f"not a generated synthetic fixture: {path}")
    manifest = json.loads(marker.read_text(encoding="utf-8"))
    if manifest.get("kind") != FIXTURE_KIND:
        raise ValueError(f"unrecognized synthetic fixture: {path}")
    if not TerminologyPaths(Path(path)).database(PROJECT).is_file():
        raise ValueError(f"synthetic fixture database is missing: {path}")
    return manifest


def open_browse(path: Path):
    """Return (repository, profiles, base, browsing); caller must close repository."""
    _require_fixture(path)
    repository = SqliteTerminologyRepository.open(str(path), PROJECT)
    profiles = TerminologyProfileService(repository.localization_profiles)
    base = SqliteEffectiveTerminologySnapshotPort(repository)
    browsing = TerminologyBrowseQueries(repository, ProfiledTerminologyBrowseSnapshotPort(base, profiles))
    return repository, profiles, base, browsing


def commands_for(repository):
    """Create the real save command boundary without external services or Qt."""
    lifecycle = SimpleNamespace(
        active=SimpleNamespace(
            project=SimpleNamespace(envelope=SimpleNamespace(identity=PROJECT, revision=1)),
            variant=SimpleNamespace(ref=SimpleNamespace(identity=SimpleNamespace(value=VARIANT)), revision=1),
        )
    )
    repositories = SimpleNamespace(for_project=lambda _project: repository)
    commands = ProductionTerminologyCommands(
        None, repositories, None, ProductionState(), UuidGenerator(), SystemClock(), None, lifecycle
    )
    context = RequestContext("perf", project_id=PROJECT, variant_id=VARIANT, metadata=(("manual_actor_id", "perf"),))
    return commands, context


def _copy_fixture(source: Path, target: Path) -> None:
    manifest = _require_fixture(source)
    target.mkdir(parents=True, exist_ok=False)
    destination = TerminologyPaths(target).database(PROJECT)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_db = TerminologyPaths(source).database(PROJECT)
    with sqlite3.connect(source_db.resolve().as_uri() + "?mode=ro", uri=True) as reader:
        with sqlite3.connect(destination) as writer:
            reader.backup(writer)
    (target / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")


def _timed(samples, name, action, check):
    started = time.perf_counter()
    value = action()
    elapsed = (time.perf_counter() - started) * 1000
    check(value)
    samples[name] = elapsed
    return value


def _check_page(page, count, *, start=0, search=False, effective=False):
    indexes = [count - 1] if search else list(range(start, min(start + 100, count)))
    assert page.total == (1 if search else count)
    assert [item.term_id for item in page.items] == [f"term-{i:06}" for i in indexes]
    prefix = "方案 " if effective else ""
    assert [item.translation for item in page.items] == [f"{prefix}术语 {i:06}" for i in indexes]
    assert bool(page.next_cursor) == (not search and start + len(indexes) < count)


def _check_draft(value, count):
    assert value.ref.project_id == PROJECT and value.ref.variant_id == VARIANT
    assert len(value.decisions) == count and value.actions == ()


def _check_base(value, count):
    assert value.status.value == "ready" and value.version_id == "version-1"
    assert len(value.decisions) == count and value.decisions[-1].translation == f"术语 {count - 1:06}"


def _check_description(value, count):
    assert value.profile_name == "Benchmark" and value.profile_revision == 1
    assert len(value.mapping_rows) == count and len(value.effective_snapshot.decisions) == count


def _page_sample(samples, name, read, count, *, cursor=None, search=False, effective=False):
    filters = TermFilter(search=f"Term {count - 1:06}" if search else "")
    query, request = TermBrowseQuery(PROJECT, VARIANT, filters), PageRequest(limit=100, cursor=cursor)
    return _timed(
        samples,
        name,
        lambda: read(query, request),
        lambda page: _check_page(page, count, start=100 if cursor else 0, search=search, effective=effective),
    )


@contextmanager
def _rss_sample(enabled):
    metrics = {"status": "disabled"}
    if not enabled:
        yield metrics
        return
    try:
        baseline = current_rss_bytes()
    except RuntimeError as exc:
        metrics.update(status="unavailable", diagnostic=str(exc))
        yield metrics
        return
    peak = [baseline]
    errors = []
    stop = Event()

    def sample():
        while not stop.wait(0.01):
            try:
                peak[0] = max(peak[0], current_rss_bytes())
            except RuntimeError as exc:
                errors.append(str(exc))
                break

    worker = Thread(target=sample, name="terminology-benchmark-rss", daemon=True)
    worker.start()
    try:
        yield metrics
    finally:
        stop.set()
        worker.join()
        metrics.update(
            status="unavailable" if errors else "sampled",
            diagnostic=errors,
            rss_baseline_mb=baseline / 2**20,
            rss_peak_delta_mb=(peak[0] - baseline) / 2**20,
            scope="legacy sequence only; 10ms process RSS sampling",
        )


def measure_legacy(path: Path, count: int, *, rss=False):
    repository, _, base, browsing = open_browse(path)
    query = TermBrowseQuery(PROJECT, VARIANT)
    effective, draft = browsing.list_effective_terms, browsing.list_browsable_draft_terms
    timings = {}
    try:
        gc.collect()
        with _rss_sample(rss) as memory:
            for name, action, check in (
                ("draft_read", lambda: repository.active_draft(PROJECT, VARIANT), _check_draft),
                ("base_snapshot", lambda: base.snapshot(PROJECT, VARIANT), _check_base),
                ("describe", lambda: browsing.describe(query), _check_description),
            ):
                _timed(timings, name, action, lambda value: check(value, count))
            first = _page_sample(timings, "effective_first", effective, count, effective=True)
            if first.next_cursor is not None:
                _page_sample(timings, "effective_next", effective, count, cursor=first.next_cursor, effective=True)
            _page_sample(timings, "effective_search", effective, count, search=True, effective=True)
            _page_sample(timings, "draft_first", draft, count)
            _page_sample(timings, "draft_search", draft, count, search=True)
        return {"ms": timings, "rss": memory}
    finally:
        repository.close()


def measure_browse(path: Path, count: int, kind: str):
    if kind not in {"effective", "draft"}:
        raise ValueError("browse kind must be effective or draft")
    repository, _, _, browsing = open_browse(path)
    read = browsing.list_effective_terms if kind == "effective" else browsing.list_browsable_draft_terms
    timings = {}
    effective = kind == "effective"
    try:
        gc.collect()
        cold = _page_sample(timings, "cold_first_total", read, count, effective=effective)
        first = _page_sample(timings, "warm_first", read, count, effective=effective)
        assert cold == first
        if first.next_cursor is not None:
            _page_sample(timings, "warm_next", read, count, cursor=first.next_cursor, effective=effective)
        _page_sample(timings, "warm_search", read, count, search=True, effective=effective)
        return {"ms": timings}
    finally:
        repository.close()


def measure_edits(source: Path, target: Path, count: int, repeats: int):
    if repeats < 1:
        raise ValueError("edit repetitions must be positive")
    _copy_fixture(source, target)
    repository, _, _, _ = open_browse(target)
    commands, context = commands_for(repository)
    try:
        draft = repository.active_draft(PROJECT, VARIANT)
        _check_draft(draft, count)
        samples = []
        for iteration in range(repeats):
            translation = f"编辑后的译名 {iteration}"
            gc.collect()
            started = time.perf_counter()
            result = commands.apply_decision(
                "change_translation", context, term_id=draft.decisions[-1].term_id, translation=translation
            )
            samples.append(time.perf_counter() - started)
            assert result.ref.revision == draft.ref.revision + iteration + 1
            assert result.decisions[-1].translation == translation
            assert len(result.actions) == iteration + 1
            print(
                json.dumps({"event": "edit", "count": count, "repeat": iteration + 1, "seconds": samples[-1]}),
                flush=True,
            )
        assert repository.active_draft(PROJECT, VARIANT) == result
        history = repository.list_manual_actions(result.ref, PageRequest(limit=1000))
        assert history.total == repeats and {action.action_id for action in history.items} <= {
            action.action_id for action in result.actions
        }
        return {
            "count": count,
            "seconds": samples,
            **{f"{key}_seconds": value for key, value in _statistics(samples).items()},
        }
    finally:
        repository.close()


def measure_read_counts(path: Path):
    terms = (TermDecision("term-0", PROJECT, VARIANT, "Dragon", "dragon", "龙", status=DecisionStatus.ADOPTED),)
    _create_fixture(path, terms, profile=False)
    repository, _, _, browsing = open_browse(path)
    commands, context = commands_for(repository)
    counts, reads = Counter(), Counter()
    loads, dumps = repository_module.loads, draft_storage.dumps

    def decode(payload, expected_type):
        counts[f"decode:{expected_type.__name__}"] += 1
        counts[f"json_chars:{expected_type.__name__}"] += len(payload)
        return loads(payload, expected_type)

    def encode(value):
        counts[f"encode:{type(value).__name__}"] += 1
        return dumps(value)

    def counted_digest(original):
        def digest(value, **kwargs):
            counts[f"hash:{kwargs['namespace']}"] += 1
            return original(value, **kwargs)

        return digest

    def trace(statement):
        normalized = " ".join(statement.split())
        if normalized.upper().startswith("SELECT "):
            reads[normalized.split(" WHERE ")[0]] += 1

    results = {}
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(repository_module, "loads", decode))
            stack.enter_context(patch.object(draft_storage, "dumps", encode))
            for module in (decision_module, draft_module):
                stack.enter_context(patch.object(module, "canonical_digest", counted_digest(module.canonical_digest)))
            repository._connection.set_trace_callback(trace)
            actions = {
                "change_one_translation": lambda: commands.apply_decision(
                    "change_translation", context, term_id="term-0", translation="巨龙"
                ),
                "post_edit_read_assets": lambda: read_assets(commands, context, browsing=browsing),
                "effective_first_page": lambda: browsing.list_effective_terms(TermBrowseQuery(PROJECT, VARIANT)),
                "review_conflicts_first_page": lambda: repository.list_review_conflicts(build().ref),
            }
            for name, action in actions.items():
                counts.clear()
                reads.clear()
                result = action()
                results[name] = {"operations": dict(counts), "selects": dict(reads)}
                if name == "change_one_translation":
                    assert result.ref.revision == 1 and result.decisions[0].translation == "巨龙"
                elif name == "post_edit_read_assets":
                    assert result.draft.ref.revision == 1 and result.version_ref.version_id == "version-1"
                elif name == "effective_first_page":
                    assert result.total == 1 and result.items[0].translation == "龙"
                else:
                    assert result.total == 0 and result.items == ()
        return results
    finally:
        repository._connection.set_trace_callback(None)
        repository.close()


def _statistics(values):
    return {"median": statistics.median(values), "minimum": min(values), "maximum": max(values)}


def _summary(rows):
    grouped = {}
    for row in rows:
        for phase in ("legacy", "effective", "draft"):
            for operation, value in row[phase]["ms"].items():
                grouped.setdefault(f"{row['count']}/{phase}/{operation}", []).append(value)
    return {key: {"unit": "ms", "samples": values, **_statistics(values)} for key, values in grouped.items()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True, help="fresh synthetic fixture root; must not exist")
    parser.add_argument("--output", type=Path, required=True, help="new JSON evidence file; must not exist")
    parser.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000, 50000])
    parser.add_argument("--edit-sizes", type=int, nargs="*", default=[1000, 10000])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--rss", action="store_true", help="sample RSS across the unchanged legacy sequence")
    args = parser.parse_args(argv)
    if args.repeats < 5 or any(value < 1 for value in (*args.sizes, *args.edit_sizes)):
        parser.error("repeats must be at least five and sizes must be positive")
    if sys.flags.optimize:
        parser.error("run without -O: result assertions are required")
    for path in (args.workspace, args.output):
        if path.exists() or path.is_symlink():
            parser.error(f"refusing an existing path: {path}")
    workspace, output = args.workspace.resolve(), args.output.resolve()
    if workspace == output or workspace.is_relative_to(output):
        parser.error("output cannot be the workspace or its parent")
    workspace.mkdir(parents=True, exist_ok=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "completed": False,
        "python": sys.version,
        "platform": platform.platform(),
        "started_at": datetime.now(UTC).isoformat(),
        "arguments": vars(args) | {"workspace": str(workspace), "output": str(output)},
        "fixture_kind": FIXTURE_KIND,
        "samples": [],
        "edits": [],
        "semantics": "Synthetic terms/profile; small build, empty diff. Cold adapters, OS cache uncontrolled. "
        "Setup/open/checks excluded. Edit samples are seconds; browse samples are milliseconds.",
    }
    with output.open("x", encoding="utf-8") as stream:
        try:
            fixtures = {
                count: create_fixture(workspace / f"data-{count}", count)
                for count in sorted(set(args.sizes) | set(args.edit_sizes))
            }
            for count in sorted(set(args.sizes)):
                for repeat in range(1, args.repeats + 1):
                    row = {
                        "count": count,
                        "repeat": repeat,
                        "legacy": measure_legacy(fixtures[count], count, rss=args.rss),
                        "effective": measure_browse(fixtures[count], count, "effective"),
                        "draft": measure_browse(fixtures[count], count, "draft"),
                    }
                    report["samples"].append(row)
                    print(json.dumps({"event": "browse", **row}), flush=True)
            for count in sorted(set(args.edit_sizes)):
                report["edits"].append(
                    measure_edits(fixtures[count], workspace / f"edits-{count}", count, args.repeats)
                )
            report["read_counts"] = measure_read_counts(workspace / "read-counts")
            report["summary"] = _summary(report["samples"])
            report["completed"] = True
        finally:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(f"wrote {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
