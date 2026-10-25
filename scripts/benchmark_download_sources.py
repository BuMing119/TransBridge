"""Compare real synthetic-plugin source listing and cold/warm download captures.

No network, user project, or application configuration is read. The baseline
module is loaded from an explicit saved source file; both implementations use
the same in-memory project and synthetic plugin files. "Cold" means an empty
parse cache, not an empty operating-system disk cache.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import gc
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import platform
import statistics
import sys
from time import perf_counter
from uuid import uuid4


def _baseline(path):
    name = "transbridge.application.projects._benchmark_download_baseline"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.ProjectCategorizedUploadService


def _fixture(workspace, plugins, entries):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tests.plugin_fixtures import write_plugin
    from transbridge.application.contracts import RequestContext
    from transbridge.application.io import FormatId
    from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
    from transbridge.application.projects.source_registry import migrate_legacy_source_registry
    from transbridge.bootstrap.persistence import build_persistence_v2_services
    from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
    from transbridge.persistence.v2.variant import VariantSnapshot

    workspace.mkdir(parents=True, exist_ok=False)
    source_dir = workspace / "plugins"
    source_dir.mkdir()
    requests = []
    for index in range(plugins):
        path = write_plugin(
            source_dir / f"Synthetic{index:03d}.esp",
            [(0x800 + row, f"Entry{row:05d}", f"Synthetic original {index}:{row}") for row in range(entries)],
        )
        requests.append(
            ProjectSourceRequest(
                str(path), FormatId.PLUGIN_SSE, options=(("source_namespace", f"source:benchmark:{index}"),)
            )
        )
    services = build_persistence_v2_services(
        workspace / "data", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-10-25T00:00:00+00:00"
    )
    context = RequestContext("source-performance", run_id="fixture")
    result = services.gui_project_commands.create_project(
        ProjectProvisioningRequest("Synthetic source performance", source=requests[0]),
        context,
    )
    if not result.is_success:
        raise RuntimeError(str(result.diagnostics))
    # The measurement is source listing/capture, not multi-source provisioning.
    # Assemble the already-parsed fixture through real immutable contracts and
    # the actual lifecycle publication boundary, avoiding unrelated setup cost.
    prepared, preparer = [], TranslationIoProjectSourcePreparer()
    for index, request in enumerate(requests):
        prepared.append(preparer.prepare_source(request, context, role="primary", common_options=()))
        if (index + 1) % 20 == 0:
            print(f"Fixture prepared {index + 1}/{plugins} plugins", flush=True)
    active = services.project_lifecycle.active
    registry = migrate_legacy_source_registry(
        active.project_ref.identity.value, tuple(item.to_dict() for item in prepared)
    )
    baselines = tuple(item.baseline for item in prepared)
    snapshot = VariantSnapshot(
        active.formal_variant_ref,
        tuple(item.fingerprint for item in baselines),
        tuple(entry for item in baselines for entry in item.entries),
        active.variant.revision + 1,
    )
    project = replace(
        active.project,
        envelope=replace(
            active.project.envelope,
            revision=active.project.envelope.revision + 1,
            data={**active.project.envelope.data, **registry.to_project_data()},
        ),
    )
    updated = services.project_lifecycle.commit_active_content(
        project,
        snapshot,
        context,
        expected_project_revision=active.project.envelope.revision,
        expected_variant_revision=active.variant.revision,
    )
    if not updated.is_success:
        raise RuntimeError(str(updated.diagnostics))
    services.baselines.register(active.project_ref, active.formal_variant_ref, baselines)
    return services, context, tuple(source_dir.glob("*.esp"))


class CountingPreparer:
    def __init__(self):
        from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer

        self.delegate = TranslationIoProjectSourcePreparer()
        self.calls = 0

    def prepare_source(self, *args, **kwargs):
        self.calls += 1
        return self.delegate.prepare_source(*args, **kwargs)


def _measure(service, preparer, context, *, catalog=False, source_ids=None):
    previous = preparer.calls
    started = perf_counter()
    result = (
        service.catalog(context)
        if catalog
        else service.snapshot(context, endpoint="https://paratranz.cn", remote_project_id=42, source_ids=source_ids)
    )
    elapsed = (perf_counter() - started) * 1000
    if not result.is_success:
        raise RuntimeError(str(result.diagnostics))
    count = (
        sum(item.count for item in result.value) if catalog else sum(len(item.entries) for item in result.value.sources)
    )
    return {"milliseconds": elapsed, "prepare_calls": preparer.calls - previous, "entries": count}


def _round(factory, services, context, *, optimized, selected):
    def fresh():
        preparer = CountingPreparer()
        return factory(services.project_lifecycle, services.baselines, preparer), preparer

    service, preparer = fresh()
    results = {"list": _measure(service, preparer, context, catalog=optimized)}
    del service
    gc.collect()
    service, preparer = fresh()
    results["cold_all"] = _measure(service, preparer, context)
    results["warm_all"] = _measure(service, preparer, context)
    cache = getattr(service, "_hydrations", None)
    results["retained_parse_cache_estimated_bytes"] = getattr(cache, "_estimated_bytes", 0)
    del service
    gc.collect()
    service, preparer = fresh()
    results["cold_selected"] = _measure(service, preparer, context, source_ids=(selected,))
    results["warm_selected"] = _measure(service, preparer, context, source_ids=(selected,))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plugins", type=int, default=80)
    parser.add_argument("--entries-per-plugin", type=int, default=1200)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 5 or args.plugins < 1 or args.entries_per_plugin < 1:
        parser.error("use at least five repetitions and positive source/entry counts")
    logging.disable(logging.INFO)
    from transbridge.application.projects.categorized_upload import ProjectCategorizedUploadService

    baseline = _baseline(args.baseline.resolve(strict=True))
    services, context, paths = _fixture(args.workspace.resolve(), args.plugins, args.entries_per_plugin)
    input_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    raw = {"before": [], "after": []}
    try:
        selected = services.gui_project_commands.file_sources_catalog(context).value[0].source_id
        for index in range(args.repeats):
            modes = ("before", "after") if index % 2 == 0 else ("after", "before")
            for mode in modes:
                row = _round(
                    ProjectCategorizedUploadService if mode == "after" else baseline,
                    services,
                    context,
                    optimized=mode == "after",
                    selected=selected,
                )
                raw[mode].append(row)
                print(json.dumps({"round": index + 1, "mode": mode, **row}), flush=True)
        if not all(
            hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in input_hashes.items()
        ):
            raise RuntimeError("synthetic benchmark inputs changed")
    finally:
        services.close()
    summary = {}
    for mode, samples in raw.items():
        summary[mode] = {}
        for scenario in ("list", "cold_all", "warm_all", "cold_selected", "warm_selected"):
            values = [sample[scenario]["milliseconds"] for sample in samples]
            summary[mode][scenario] = {
                "median_ms": statistics.median(values),
                "range_ms": (min(values), max(values)),
                "prepare_calls": [sample[scenario]["prepare_calls"] for sample in samples],
            }
    result = {
        "python": platform.python_version(),
        "plugins": args.plugins,
        "entries": args.plugins * args.entries_per_plugin,
        "repeats": args.repeats,
        "baseline_module": str(args.baseline.resolve()),
        "shared_runtime": "Both source-service versions use the current VariantSnapshot namespace-set optimization.",
        "cold_definition": "Empty source hydration cache; operating-system filesystem cache is not cleared.",
        "input_hashes": input_hashes,
        "raw": raw,
        "summary": summary,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
