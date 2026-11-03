"""Offline transfer pacing and download candidate materialization comparison.

The before path repeats the unchanged result_changes conversion for preview and
publication. The after path reuses that immutable plan's candidates in a fresh
ProjectDownloadSession. It does not measure the complete download/save pipeline.
No credentials, user files, real network, or application configuration are used.
"""

from __future__ import annotations

import argparse
from collections import Counter
import gc
import hashlib
import json
import logging
from pathlib import Path
import platform
import statistics
from threading import Lock
from time import perf_counter, sleep
from unittest.mock import patch

import requests

from transbridge.application.io.identity import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.sync.batch_download import BatchDownloadSource, plan_batch_download
from transbridge.application.sync.download_merge import result_changes
from transbridge.application.sync.models import LocalEntrySnapshot, RemoteEntrySnapshot
from transbridge.application.sync.project_download import ProjectDownloadSession
from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.paratranz.file_publication import FilePublicationGateway, RequestBudget


def synthetic_plan(sources, entries):
    if not 1 <= sources <= entries <= 100_000:
        raise ValueError("require 1 <= sources <= entries <= 100,000")
    # Represent the observed skew: one large plugin and many small plugins.
    largest = min(65_000, entries - sources + 1) if sources > 1 else entries
    sizes = [largest]
    if sources > 1:
        quotient, remainder = divmod(entries - largest, sources - 1)
        sizes.extend(quotient + (index < remainder) for index in range(sources - 1))
    fixtures = []
    for source, count in enumerate(sizes):
        namespace = SourceNamespace(f"synthetic:transfer:{source}")
        local, remote = [], []
        for index in range(count):
            key = EntryKey(namespace, str(index))
            original = f"Synthetic original {source}:{index}"
            local.append(LocalEntrySnapshot(key, EntryRevision(1), original, "", "NPC_:FULL", 0))
            remote.append(RemoteEntrySnapshot(key, "remote", original, f"合成译文 {source}:{index}", "NPC_:FULL", 1))
        fixtures.append(BatchDownloadSource(str(source), f"Synthetic{source}.esp", tuple(local), tuple(remote)))
    return plan_batch_download(fixtures)


def candidate_round(plan, optimized):
    session = ProjectDownloadSession.__new__(ProjectDownloadSession)
    session.plan = plan
    session._candidate_plan = None
    session._candidates = ()
    read = session._result_changes if optimized else lambda: result_changes(plan)
    started = perf_counter()
    preview = read()
    published = read()
    elapsed = (perf_counter() - started) * 1000
    if preview != published:
        raise RuntimeError("preview and publication candidates differ")
    digest = hashlib.sha256()
    for change in published:
        digest.update(repr(change).encode())
    return {"milliseconds": elapsed, "count": len(published), "sha256": digest.hexdigest()}


class SimulatedClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def wait(self, seconds):
        self.now += seconds


class ControlledSession(requests.Session):
    def __init__(self, clock, latency):
        super().__init__()
        self.clock, self.latency = clock, latency
        self.calls = 0

    def send(self, request, **kwargs):
        self.calls += 1
        self.clock.wait(self.latency)
        response = requests.Response()
        response.status_code = 200
        response.url, response.request = request.url, request
        response._content = b"[]"
        return response


def pacing_round(requests_count, latency, interval):
    clock = SimulatedClock()
    transport = ControlledSession(clock, latency)
    config = ParatranzConfig(
        token="synthetic-benchmark-token",
        base_url="https://paratranz.example/api",
        credential_store=UnavailableCredentialStore(),
        environment={},
    )
    gateway = FilePublicationGateway(config, session=transport, budget=RequestBudget(interval))
    gateway.cancellation.wait = clock.wait
    with patch("transbridge.paratranz.file_publication.monotonic", clock.monotonic):
        for _ in range(requests_count):
            gateway.inventory(42)
    gateway.close()
    return {"simulated_seconds": clock.now, "requests": transport.calls, "interval_seconds": interval}


def summarize(values):
    return {"median": statistics.median(values), "range": [min(values), max(values)]}


class DelayedUploads:
    """Local response latency with independent sessions, no sockets or real API."""

    def __init__(self, latency):
        self.latency, self.lock = latency, Lock()
        self.active = self.peak = self.calls = 0

    def session(self):
        backend = self

        class Session(requests.Session):
            def send(self, request, **kwargs):
                with backend.lock:
                    backend.active += 1
                    backend.peak = max(backend.peak, backend.active)
                    backend.calls += 1
                    file_id = backend.calls
                try:
                    sleep(0.01 if request.method == "GET" else backend.latency)
                    response = requests.Response()
                    response.status_code = 200
                    response.url, response.request = request.url, request
                    response._content = json.dumps(
                        [] if request.method == "GET" else {"file": {"id": file_id}}
                    ).encode()
                    return response
                finally:
                    with backend.lock:
                        backend.active -= 1

        return Session()


def concurrent_upload_round(count, latency, concurrency):
    from transbridge.application.categorized_upload.models import CategorizedFile
    from transbridge.application.file_upload import execute_file_upload
    from transbridge.converter.translation_entry import TranslationEntry

    backend = DelayedUploads(latency)
    config = ParatranzConfig(
        token="synthetic-benchmark-token",
        base_url="https://paratranz.example/api",
        credential_store=UnavailableCredentialStore(),
        environment={},
    )
    gateway = FilePublicationGateway(
        config,
        session=backend.session(),
        session_factory=backend.session,
        budget=RequestBudget(0.5, max_concurrent=concurrency),
    )
    files = tuple(
        CategorizedFile(
            "synthetic",
            str(index),
            f"Synthetic/file-{index}.json",
            (TranslationEntry(str(index), str(index), "Synthetic original", "合成译文", 1, "NPC_:FULL"),),
        )
        for index in range(count)
    )
    try:
        started = perf_counter()
        result = execute_file_upload(7, files, gateway)
        elapsed = perf_counter() - started
        if result.succeeded != count or result.failed or result.retry_files:
            raise RuntimeError("synthetic file publication did not complete")
    finally:
        gateway.close()
    return {"seconds": elapsed, "peak_http": backend.peak, "http_calls": backend.calls, "succeeded": result.succeeded}


def concurrency_demo(args):
    raw = {"serial": [], "up_to_five": []}
    for index in range(args.repeats):
        for mode in tuple(raw) if index % 2 == 0 else tuple(reversed(raw)):
            row = concurrent_upload_round(args.files, args.latency, 1 if mode == "serial" else 5)
            raw[mode].append(row)
            print(json.dumps({"round": index + 1, "mode": mode, **row}), flush=True)
    result = {
        "python": platform.python_version(),
        "files": args.files,
        "latency_seconds": args.latency,
        "interval_seconds": 0.5,
        "repeats": args.repeats,
        "raw": raw,
        "seconds": {mode: summarize([row["seconds"] for row in rows]) for mode, rows in raw.items()},
        "scope": "Actual uploader/session coordination with local delayed responses; no sockets or real ParaTranz.",
        "limitation": "Shorter HTTP than 0.5s may not improve throughput; not an online speedup prediction.",
    }
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=int, default=80)
    parser.add_argument("--entries", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--concurrency-demo", action="store_true")
    parser.add_argument("--files", type=int, default=6)
    parser.add_argument("--latency", type=float, default=0.8)
    args = parser.parse_args()
    if args.repeats < 5:
        parser.error("use at least five repetitions")
    logging.disable(logging.CRITICAL)
    if args.concurrency_demo:
        if args.files < 2 or args.latency <= 0:
            parser.error("concurrency demo requires at least two files and positive latency")
        concurrency_demo(args)
        return
    started = perf_counter()
    plan = synthetic_plan(args.sources, args.entries)
    plan_ms = (perf_counter() - started) * 1000
    raw = {"before": [], "after": []} if not args.baseline_only else {"before": []}
    for index in range(args.repeats):
        for mode in tuple(raw) if index % 2 == 0 else tuple(reversed(raw)):
            gc.collect()
            row = candidate_round(plan, mode == "after")
            raw[mode].append(row)
            print(json.dumps({"round": index + 1, "mode": mode, **row}), flush=True)
    if len({sample["sha256"] for samples in raw.values() for sample in samples}) != 1:
        raise RuntimeError("candidate content changed between implementations or repetitions")
    pacing = {
        mode: {str(count): pacing_round(count, 0.15, interval) for count in (82, 4971)}
        for mode, interval in (("before", 1.0), ("after", 0.5))
    }
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sources": args.sources,
        "entries": args.entries,
        "repeats": args.repeats,
        "plan_milliseconds": plan_ms,
        "plan_actions": dict(Counter(item.action.value for source in plan.plans for item in source.items)),
        "candidate_milliseconds": {
            mode: summarize([row["milliseconds"] for row in rows]) for mode, rows in raw.items()
        },
        "raw": raw,
        "pacing": pacing,
        "scope": "Candidate conversion twice versus same-plan reuse; excludes matching, merge/save and real network.",
        "pacing_scope": "Deterministic clock, 150ms successful HTTP, serial requests; not a live speedup prediction.",
    }
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
