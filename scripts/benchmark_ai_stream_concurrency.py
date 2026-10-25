"""Offline streaming benchmark using real batches, logs and durable candidates.

Only the remote provider is synthetic. Compare --source snapshots with the same
arguments in alternating fresh processes. This does not run final project commit,
semantic embeddings, model quality checks or the full main window. Scratch data
is isolated and removed; --output explicitly retains the numerical evidence.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import os
from pathlib import Path
import platform
import re
import statistics
import sys
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch


def _percentile(values, fraction):
    return sorted(values)[min(len(values) - 1, int((len(values) - 1) * fraction))] if values else 0


def _translation(key):
    return f"记录{key}：公会守卫请你沿河前往城镇，找到失踪的信使，将密封信件交给城门旁的商人。"


class _Provider:
    def __init__(self, concurrency, chunk_size, interval):
        self.barrier = threading.Barrier(concurrency, timeout=60)
        self.concurrency, self.chunk_size, self.interval = concurrency, chunk_size, interval
        self.lock = threading.Lock()
        self.calls = self.active = self.peak = 0
        self.records = []

    def chat_stream(self, messages, _max_tokens, callback):
        payload = re.search(r"<translation_entries>\s*(.*?)\s*</translation_entries>", messages[-1]["content"], re.S)
        if payload is None:
            raise ValueError("benchmark expected the production translation prompt")
        output = json.dumps(
            {"results": [{"entry_id": key, "translation": _translation(key)} for key in json.loads(payload.group(1))]},
            ensure_ascii=False,
        )
        chunks = [output[i : i + self.chunk_size] for i in range(0, len(output), self.chunk_size)]
        with self.lock:
            self.calls += 1
            call = self.calls
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if call <= self.concurrency:
                self.barrier.wait()
            started = time.perf_counter()
            callback_cpu = 0.0
            lags = []
            for index, chunk in enumerate(chunks):
                due = started + (index + 1) * self.interval
                delay = due - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                lags.append(max(0, time.perf_counter() - due))
                cpu = time.thread_time()
                callback(chunk)
                callback_cpu += time.thread_time() - cpu
            with self.lock:
                self.records.append({
                    "chars": len(output),
                    "chunks": len(chunks),
                    "elapsed": time.perf_counter() - started,
                    "ideal": len(chunks) * self.interval,
                    "cpu": callback_cpu,
                    "lag_p95": _percentile(lags, 0.95),
                    "lag_max": max(lags),
                })
            return output
        finally:
            with self.lock:
                self.active -= 1

    def cancel(self):
        self.barrier.abort()


class _CountedFile:
    def __init__(self, delegate, counts):
        self._delegate, self._counts = delegate, counts

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def write(self, text):
        self._counts["writes"] += 1
        started = time.perf_counter()
        try:
            return self._delegate.write(text)
        finally:
            self._counts["io_s"] += time.perf_counter() - started

    def flush(self):
        self._counts["flushes"] += 1
        started = time.perf_counter()
        try:
            return self._delegate.flush()
        finally:
            self._counts["io_s"] += time.perf_counter() - started


def _runtime(source):
    sys.path.insert(0, str(source.resolve()))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    modules = {
        "batch": "transbridge.ai_translator.batch_planner",
        "execution": "transbridge.ai_translator.batch_execution",
        "translator": "transbridge.ai_translator.translator",
        "budget": "transbridge.application.translation.ai_request_budget",
        "checkpoint": "transbridge.application.translation.candidate_checkpoint",
        "candidate": "transbridge.application.translation.legacy_bridge",
        "config": "transbridge.config.llm",
        "entry": "transbridge.converter.translation_entry",
        "collection": "transbridge.converter.translation_entry_collection",
        "limited": "transbridge.infra.limited_llm_client",
        "feedback": "transbridge.ui.tools.ai_translator.task_feedback",
        "store": "transbridge.ui.tools.ai_translator.workflow_log_store",
        "logging": "transbridge.ui.tools.ai_translator.workflow_logging_client",
    }
    loaded = {name: importlib.import_module(module) for name, module in modules.items()}
    for module in loaded.values():
        if not Path(module.__file__).resolve().is_relative_to(source.resolve()):
            raise RuntimeError(f"Import escaped --source: {module.__file__}")
    loaded["core"] = importlib.import_module("PyQt6.QtCore")
    loaded["widgets"] = importlib.import_module("PyQt6.QtWidgets")
    try:
        loaded["process"] = importlib.import_module("psutil").Process()
    except ImportError:
        loaded["process"] = None
    return SimpleNamespace(**loaded)


def _measure(args, runtime, app, folder):
    terms_path = folder / "terms.json"
    terms_path.write_text(
        json.dumps({f"Guild{i}": f"公会{i}" for i in range(args.terms)}, ensure_ascii=False), encoding="utf-8"
    )
    raw = json.dumps([
        {
            "key": f"e{i:06d}",
            "original": f"Guild{i % max(args.terms, 1)} guard asks you to follow the river and deliver "
            f"this sealed letter to the merchant beside the gate. Record {i}.",
        }
        for i in range(args.requests * args.rows)
    ])
    started = time.perf_counter()
    entries = [
        runtime.entry.TranslationEntry(item["key"], item["key"], item["original"], "", 0, "BOOK:DESC")
        for item in json.loads(raw)
    ]
    collection = runtime.collection.TranslationEntryCollection(entries)
    import_s = time.perf_counter() - started
    cfg = runtime.config.LLMConfig(
        max_concurrent=args.concurrency,
        enable_semantic_match=False,
        term_priority=["dynamic", "json"],
        local_json_path=str(terms_path),
        enable_post_process=False,
        max_output_tokens=0,
    )
    provider = _Provider(args.concurrency, args.chunk, args.interval)
    budget = runtime.budget.AiRequestBudget(args.concurrency)
    checkpoint = runtime.checkpoint.FilesystemTranslationCheckpointPort(folder / "candidates")
    store = runtime.store.WorkflowLogStore(None, workflow="offline", log_dir=folder / "logs")
    counts = {"opens": 0, "writes": 0, "flushes": 0, "io_s": 0.0}
    original_open = Path.open

    def counted_open(path, *a, **kw):
        handle = original_open(path, *a, **kw)
        if path.parent == folder / "logs":
            counts["opens"] += 1
            return _CountedFile(handle, counts)
        return handle

    def data_dir(stem):
        path = folder / "data" / stem
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    with patch.object(runtime.config.LLMConfig, "get_ai_translator_dir", staticmethod(data_dir)):
        translator = runtime.translator.AutoTranslator(
            runtime.translator.TranslatorConfig(cfg, str(folder / "benchmark.esp")),
            llm_client=provider,
            candidate_checkpoint=checkpoint,
            request_budget=budget,
        )
        translator._term_mgr.load_all()
        translator._llm = runtime.logging.WorkflowLoggingLLMClient(
            runtime.limited.LimitedLLMClient(provider, budget), store
        )
        translator._candidate_session = runtime.candidate.LegacyTranslationCandidateSession(
            run_id="offline",
            owner_id="offline",
            spec_fingerprint="a" * 64,
            input_fingerprint="b" * 64,
            checkpoint=checkpoint,
            provider="fake",
            model="offline",
        )
        batches = [runtime.batch.Batch(entries[i : i + args.rows], "长文本") for i in range(0, len(entries), args.rows)]
        result, lock = runtime.translator.TranslationResult(), threading.Lock()
        logs = runtime.widgets.QPlainTextEdit()
        logs.document().setMaximumBlockCount(3000)
        logs.show()
        feedback = runtime.feedback.TaskFeedback(
            progress=lambda *a: None,
            project_progress=lambda *a: None,
            log_batch=lambda lines: logs.appendPlainText("\n".join(str(line) for line in lines)),
        )
        feedback.enable_polling()
        poll = runtime.core.QTimer()
        poll.setInterval(50)
        poll.timeout.connect(feedback.poll)
        poll.start()
        ticks, memory, threads, handles = [], [], [], []
        timings, checkpoint_times, close_times = [], [], []
        append_batch = checkpoint.append_batch

        def timed_append(*a, **kw):
            start = time.perf_counter()
            try:
                return append_batch(*a, **kw)
            finally:
                checkpoint_times.append(time.perf_counter() - start)

        checkpoint.append_batch = timed_append
        process = runtime.process
        heartbeat = runtime.core.QTimer()
        heartbeat.setInterval(10)

        def tick():
            ticks.append(time.perf_counter())
            if process:
                memory.append(process.memory_info().rss)
                threads.append(process.num_threads())
                handles.append(process.num_handles() if hasattr(process, "num_handles") else process.num_fds())

        heartbeat.timeout.connect(tick)
        heartbeat.start()

        def operation(batch):
            index = int(batch.entries[0].key[1:]) // args.rows
            timing = {}
            translator._run_batch(
                batch,
                collection,
                result,
                lock,
                log_callback=lambda line: feedback.log("source", line),
                stream_callback=lambda chunk: store.write_chunk(f"batch_{index:03d}", chunk),
                progress_emit=lambda: feedback.progress("source", "translate", result.success_count, len(entries), ""),
                update_terms=False,
                _timing_out=timing,
            )
            timings.append(timing)

        started, cpu_start = time.perf_counter(), time.process_time()
        ticks.append(started)
        try:
            with (
                patch.object(Path, "open", counted_open),
                runtime.execution.BatchExecutor(args.concurrency + budget.max_preparing) as executor,
                ThreadPoolExecutor(max_workers=1) as runner,
            ):

                def run():
                    executor.run([[batch] for batch in batches], operation, lane_key="source")
                    close_started = time.perf_counter()
                    store.close()
                    close_times.append(time.perf_counter() - close_started)

                future = runner.submit(run)
                while not future.done():
                    app.processEvents()
                    time.sleep(0.001)
                future.result()
            elapsed, cpu_s = time.perf_counter() - started, time.process_time() - cpu_start
            ticks.append(time.perf_counter())
        finally:
            heartbeat.stop()
            poll.stop()
            store.close()
            feedback.poll(all_pending=True)
            logs.close()
            logs.deleteLater()
            app.processEvents()
        restored = runtime.checkpoint.FilesystemTranslationCheckpointPort(folder / "candidates").load("offline")
        assert result.failed_count == 0, result.failed_entries[:3]
        assert result.success_count == len(entries)
        assert len(restored.candidates) == len({c.entry_key for c in restored.candidates}) == len(entries)
        assert all(c.text == _translation(c.entry_key.local_key) for c in restored.candidates)
        assert provider.peak == args.concurrency
        assert store.is_available, store.last_error
        gaps = [(b - a) * 1000 for a, b in zip(ticks, ticks[1:])]
        return {
            "elapsed_s": elapsed,
            "cpu_s": cpu_s,
            "json_import_ms": import_s * 1000,
            "peak_requests": provider.peak,
            "peak_threads": max(threads, default=None),
            "peak_process_handles": max(handles, default=None),
            "rss_mb": max(memory) / 1048576 if memory else None,
            "heartbeat_p95_ms": _percentile(gaps, 0.95),
            "heartbeat_max_ms": max(gaps),
            "stream_median_s": statistics.median(r["elapsed"] for r in provider.records),
            "ideal_stream_median_s": statistics.median(r["ideal"] for r in provider.records),
            "callback_cpu_s": sum(r["cpu"] for r in provider.records),
            "stream_lag_max_ms": max(r["lag_max"] for r in provider.records) * 1000,
            "chunks": sum(r["chunks"] for r in provider.records),
            "logs": counts,
            "phase_sum_s": {key: sum(t.get(key, 0) for t in timings) for key in ("t_terms", "t_llm", "t_parse")},
            "candidate_append_sum_s": sum(checkpoint_times),
            "log_close_s": sum(close_times),
            "accepted": result.success_count,
            "recovered": len(restored.candidates),
            "failed": result.failed_count,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "src")
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--rows", type=int, default=20)
    parser.add_argument("--terms", type=int, default=1000)
    parser.add_argument("--chunk", type=int, default=16)
    parser.add_argument("--interval", type=float, default=0.05)
    args = parser.parse_args()
    if (
        min(args.samples, args.concurrency, args.requests, args.rows, args.chunk) < 1
        or args.requests < args.concurrency
    ):
        parser.error("positive sizes and requests >= concurrency are required")
    if args.interval < 0 or args.terms < 0:
        parser.error("interval and terms must be nonnegative")
    if args.scratch:
        args.scratch.mkdir(parents=True, exist_ok=True)
    runtime = _runtime(args.source)
    app = runtime.widgets.QApplication.instance() or runtime.widgets.QApplication([])
    samples = []
    for _ in range(args.samples):
        with TemporaryDirectory(prefix="ai-stream-", dir=args.scratch) as scratch:
            sample = _measure(args, runtime, app, Path(scratch))
        samples.append(sample)
        print(json.dumps(sample), flush=True)
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_logical": os.cpu_count(),
        "arguments": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
        "samples": samples,
        "elapsed_summary": {
            "median": statistics.median(row["elapsed_s"] for row in samples),
            "min": min(row["elapsed_s"] for row in samples),
            "max": max(row["elapsed_s"] for row in samples),
        },
    }
    if args.output:
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["elapsed_summary"]), flush=True)


if __name__ == "__main__":
    main()
