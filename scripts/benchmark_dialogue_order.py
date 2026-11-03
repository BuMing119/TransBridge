"""Measure captured-source dialogue extraction and Qt ordering without changing project data.

Run with --artifact-dir data/source_artifacts --artifact-id ID --output TEMP/result.json.
Use --source-root to compare an isolated pre-change src tree with the same environment.
Outputs contain timings and hashes, never entry text. All measurements repeat five times.
"""

# The selected source tree and read-only audit guard must precede application imports.
# ruff: noqa: E402

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import gc
import hashlib
import importlib.util
import inspect
import json
import logging
import os
from pathlib import Path
import statistics
import sys
import time
from types import MappingProxyType

ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source-root", type=Path, default=ROOT / "src")
parser.add_argument("--artifact-dir", type=Path, required=True)
parser.add_argument("--artifact-id", required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
output = args.output.resolve()
if output.exists():
    parser.error("--output must name a new file")
source = args.artifact_dir.resolve(strict=True)
HERE = output.parent
if HERE.is_relative_to(source) or source.is_relative_to(HERE):
    parser.error("Output directory must be separate from the artifact tree")
HERE.mkdir(parents=True, exist_ok=True)
mode = str(args.source_root.resolve(strict=True))
sys.path.insert(0, mode)
sys.dont_write_bytecode = True
os.environ["QT_QPA_PLATFORM"] = "offscreen"
for key in ("TEMP", "TMP", "XDG_CACHE_HOME"):
    os.environ[key] = str(HERE)


def guard(event, args):
    if event.startswith("socket.") and event != "socket.gethostname":
        raise PermissionError("Offline benchmark")
    if event != "open" or not isinstance(args[0], (str, bytes, os.PathLike)):
        return
    path = Path(os.fsdecode(args[0])).resolve()
    if path.suffix.lower() == ".xml" or ".transbridge" in path.parts:
        raise PermissionError("Not a benchmark input")
    access, flags = args[1:]
    writing = isinstance(access, str) and any(c in access for c in "wax+")
    writing |= isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
    if writing and not path.is_relative_to(HERE) and str(path).lower() != os.devnull.lower():
        raise PermissionError(f"Unexpected write: {path}")


sys.addaudithook(guard)
logging.disable(logging.CRITICAL)
from PyQt6.QtWidgets import QApplication

from transbridge.application.io import plugin_dialogue_order as authority
from transbridge.application.io.dialogue_reading_view import ReadingEntry, build_dialogue_reading_view
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.parser.plugin.dialogue_order_evidence import EXTRACTION_VERSION, evidence_payload
from transbridge.persistence.source_artifact_store import ProjectSourceArtifactStore
from transbridge.ui.workbench.table_presenter import RenderSession
from transbridge.ui.workbench.translation_table import TranslationTable
from transbridge.ui.workbench.translation_table_columns import COL_ORIGINAL

app = QApplication.instance() or QApplication([])
results = {"mode": mode, "python": sys.version, "timings": {}}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def record(name, values):
    results["timings"][name] = {
        "median_ms": statistics.median(values) * 1000,
        "min_ms": min(values) * 1000,
        "max_ms": max(values) * 1000,
        "seconds": values,
    }
    print(name, json.dumps(results["timings"][name]), flush=True)


def measure(name, fn, prepare=lambda: None):
    values = []
    for _ in range(5):
        prepare()
        start = time.perf_counter()
        result = fn()
        values.append(time.perf_counter() - start)
    record(name, values)
    return result


def background(name, fn):
    values, gaps = [], []
    with ThreadPoolExecutor(max_workers=1) as executor:
        for i in range(5):
            start = last = time.perf_counter()
            future = executor.submit(fn)
            maximum = 0
            while not future.done():
                app.processEvents()
                time.sleep(0.001)
                now = time.perf_counter()
                maximum = max(maximum, now - last)
                last = now
            result = future.result()
            values.append(time.perf_counter() - start)
            gaps.append(maximum)
            print(name, "run", i + 1, "seconds", values[-1], flush=True)
    record(name, values)
    record(name + "_event_pump_max_gap", gaps)
    return result


def read_record(relative):
    path = (source / relative).resolve()
    assert path.is_relative_to(source)
    return path.read_bytes()


started = time.perf_counter()
prepared = ProjectSourceArtifactStore.read_records(args.artifact_id, read_record)
hydration = prepared.hydration
snapshot = hydration.source_snapshot
# Extraction must use captured bytes even when a newer artifact has saved evidence.
# The metadata path is timed separately below.
binary_snapshot = replace(
    snapshot, metadata=tuple(pair for pair in snapshot.metadata if pair[0] != authority.METADATA_KEY)
)
entries = tuple(
    TranslationEntry(
        id=e.legacy_id,
        key=e.entry_key.local_key,
        original=e.original,
        translation="",
        stage=0,
        context=e.context,
        entry_key=e.entry_key,
        revision=e.revision,
        provenance=e.provenance,
        metadata=e.metadata,
        string_id=e.string_id,
        external_refs=(),
    )
    for e in hydration.entries
)
reading = tuple(ReadingEntry(e.identity, e.context, e.metadata) for e in entries)
results.update(entries=len(entries), source_sha256=snapshot.sha256, source_bytes=len(snapshot.content))
initial_digest = digest([e.to_dict() for e in entries])
print("loaded", mode, len(entries), "seconds", time.perf_counter() - started, flush=True)


def cold():
    authority._cache.clear()
    return authority.dialogue_order_from_snapshot(binary_snapshot)


collections = [item["collections"] for item in gc.get_stats()]
evidence = background("cold_evidence", cold)
results["cold_gc_collections"] = [item["collections"] - before for item, before in zip(gc.get_stats(), collections)]
results["evidence_digest"] = digest(evidence_payload(evidence))
results["records"], results["links"] = len(evidence.records), len(evidence.links)
measure("warm_evidence", lambda: authority.dialogue_order_from_snapshot(binary_snapshot))
with_metadata = replace(
    binary_snapshot,
    metadata=(
        *binary_snapshot.metadata,
        (authority.METADATA_KEY, (EXTRACTION_VERSION, snapshot.sha256, evidence_payload(evidence))),
    ),
)


def cold_metadata():
    if hasattr(authority, "_metadata_cache"):
        authority._metadata_cache.clear()
    return authority.dialogue_order_from_snapshot(with_metadata)


measure("cold_metadata_read", cold_metadata)
measure("warm_metadata_read", lambda: authority.dialogue_order_from_snapshot(with_metadata))


def cold_layout():
    if importlib.util.find_spec("transbridge.application.io.dialogue_reading_cache") is not None:
        from transbridge.application.io import dialogue_reading_cache as cache

        cache._cache.clear()
        cache._items = 0
    return build_dialogue_reading_view(reading, evidence)


view = background("cold_layout", cold_layout)
measure("repeated_layout", lambda: build_dialogue_reading_view(reading, evidence))
results["entry_order_digest"] = digest([key.serialize() for key in view.ordered_keys])
results["topic_order_digest"] = digest(
    sorted((ns.value, quest, order) for (ns, quest), order in view.topic_orders.items())
)
results["topic_groups"] = len(view.topic_orders)
source_keys = tuple(e.identity for e in entries)
ranks = MappingProxyType({key: rank for rank, key in enumerate(view.ordered_keys)})
source_rows = tuple(sorted(range(len(entries)), key=lambda row: ranks.get(source_keys[row], len(ranks) + row)))
kwargs = (
    dict(ranks=ranks, source_keys=source_keys, source_rows=source_rows)
    if "ranks" in inspect.signature(TranslationTable.set_default_entry_order).parameters
    else {}
)
table = TranslationTable(on_progress=lambda *_: None, on_batch=lambda: None)
table.resize(1200, 500)
table.show()
session = RenderSession(1, 1, entries, ("benchmark", snapshot.sha256))
table.start_render(session, {}, {})
app.processEvents()


def reset_source_display():
    table.set_default_entry_order(())
    app.processEvents()


measure(
    "first_rank_install_render",
    lambda: table.set_default_entry_order(view.ordered_keys, **kwargs),
    reset_source_display,
)
measure("repeated_rank_install", lambda: table.set_default_entry_order(view.ordered_keys, render=False, **kwargs))
measure("repeated_default_order", lambda: table._sorting.order(entries, {}))
measure("repeated_table_render", lambda: table.start_render(session, {}, {}))
table._sorting._column = COL_ORIGINAL
measure("original_column_order", lambda: table._sorting.order(entries, {}))
filtered = tuple(e for e in entries if e.context and e.context.startswith("INFO:"))
table._sorting._column = None
measure("filtered_render", lambda: table.start_render(RenderSession(2, 2, filtered, session.content_scope), {}, {}))
assert initial_digest == digest([e.to_dict() for e in entries])
assert source_keys == tuple(e.identity for e in entries)
assert set(view.ordered_keys) == set(source_keys)
results["entry_fields_unchanged"] = True
results["collection_order_unchanged"] = True
table.close_rendering()
table.close()
output.write_text(json.dumps(results, indent=2), encoding="utf-8")
print("FINISHED", mode, flush=True)
