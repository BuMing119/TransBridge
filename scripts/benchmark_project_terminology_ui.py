"""Capture digest-bound FR5.16 Qt and TaskRuntime supplemental evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PyQt6.QtCore import QThreadPool, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from transbridge.application.contracts import JobRef, RequestContext  # noqa: E402
from transbridge.application.tasks import CancellationToken, JobState, OwnerRef, TaskRuntime  # noqa: E402
from transbridge.application.terminology.runtime import (  # noqa: E402
    DEFAULT_TERMINOLOGY_HEARTBEAT_SECONDS,
    ProgressHeartbeat,
    TerminologyRunLease,
)
from transbridge.application.terminology.workloads import (  # noqa: E402
    BuildWorkloadRequest,
    TerminologyExpectedState,
    TerminologyPhase,
    TerminologyProgress,
    terminology_job_spec,
)
from transbridge.bootstrap.adapters import SystemClock, UuidGenerator  # noqa: E402
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices  # noqa: E402
from transbridge.ui.tools.terminology.task_adapter import TerminologyTaskAdapter  # noqa: E402
from transbridge.ui.tools.terminology.window import TerminologyWindow  # noqa: E402

SCHEMA_VERSION = 1
REPETITIONS = 5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    if args.output.exists() and not args.overwrite:
        parser.error(f"supplemental evidence already exists: {args.output}; pass --overwrite to replace it")

    app = QApplication.instance() or QApplication([])
    visible, main_thread = _measure_qt_feedback(app)
    heartbeat = tuple(_measure_progress_heartbeat() for _ in range(REPETITIONS))
    cancel_visible = tuple(_measure_cancel_visible() for _ in range(REPETITIONS))
    metrics = {
        "visible-feedback": _metric(max(visible), "maximum-of-five-raw", "real Qt terminology window", visible),
        "progress-heartbeat": _metric(
            max(heartbeat),
            "maximum-of-five-raw",
            "real ProgressHeartbeat delivery through TaskRuntime",
            heartbeat,
        ),
        "main-thread-block": _metric(
            max(main_thread),
            "maximum-of-five-raw",
            "10 ms QTimer gaps while terminology command preparation runs in QThreadPool",
            main_thread,
        ),
        "cancel-visible-feedback": _metric(
            max(cancel_visible),
            "maximum-of-five-raw",
            "real TerminologyTaskAdapter CANCELLING projection",
            cancel_visible,
        ),
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "profile": "fr516-ui-supplemental-v1",
        "repetitions": REPETITIONS,
        "supplemental_metrics": metrics,
        "additional_checks": [],
    }
    digest = _digest(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({**payload, "artifact_digest": digest}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {args.output}")
    print(f"artifact digest: {digest}")
    for name, value in metrics.items():
        print(f"{name}: {value['value']:.6f}s")
    return 0


def _measure_qt_feedback(app: QApplication) -> tuple[tuple[float, ...], tuple[float, ...]]:
    presenter = TerminologyPresenter(
        TerminologyUiServices(),
        RequestContext("benchmark", project_id="project", variant_id="variant"),
    )
    window = TerminologyWindow(presenter)
    visible_samples: list[float] = []
    block_samples: list[float] = []
    for iteration in range(REPETITIONS):
        ticks = [time.perf_counter()]
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
        timer.start()

        def command() -> JobRef:
            time.sleep(0.05)
            return JobRef(f"ui-job-{iteration}", "benchmark", f"ui-run-{iteration}")

        started = time.perf_counter()
        window._run_command(command, "任务已开始")
        if window.build_view.message.text() != "正在准备并提交任务…":
            raise RuntimeError("terminology window did not expose immediate preparation feedback")
        visible_samples.append(time.perf_counter() - started)
        deadline = time.monotonic() + 2.0
        while f"ui-run-{iteration}" not in window._task_refs and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.001)
        app.processEvents()
        ticks.append(time.perf_counter())
        timer.stop()
        if f"ui-run-{iteration}" not in window._task_refs:
            raise RuntimeError("terminology Qt command preparation did not finish")
        block_samples.append(max(right - left for left, right in zip(ticks, ticks[1:])))
    window.close()
    QThreadPool.globalInstance().waitForDone(2_000)
    return tuple(visible_samples), tuple(block_samples)


def _measure_progress_heartbeat() -> float:
    runtime = TaskRuntime(id_generator=UuidGenerator(), clock=SystemClock())
    owner = OwnerRef("benchmark", "gui", project_id="project", variant_id="variant")
    request = _request()
    specification = terminology_job_spec(request)
    ref = runtime.submit(specification, owner).ref
    runtime.start(ref, owner)
    lease = TerminologyRunLease(
        runtime,
        ref,
        owner,
        input_fingerprint=specification.input_fingerprint,
        cancellation=CancellationToken(),
    )
    heartbeat = ProgressHeartbeat(
        runtime,
        lease,
        interval_seconds=DEFAULT_TERMINOLOGY_HEARTBEAT_SECONDS,
    )
    heartbeat.update(TerminologyProgress(phase=TerminologyPhase.EXTRACT))
    started = time.perf_counter()
    deadline = time.monotonic() + 2.5
    while not heartbeat.pulse():
        if time.monotonic() >= deadline:
            runtime.close()
            raise RuntimeError("production terminology heartbeat was not delivered")
        time.sleep(0.001)
    elapsed = time.perf_counter() - started
    runtime.cancel(ref, owner)
    runtime.finish_cancelled(ref, owner)
    runtime.close()
    return elapsed


def _measure_cancel_visible() -> float:
    runtime = TaskRuntime(id_generator=UuidGenerator(), clock=SystemClock())
    owner = OwnerRef("benchmark", "gui", project_id="project", variant_id="variant")
    changes = []
    adapter = TerminologyTaskAdapter(runtime, owner, changes.append)
    adapter.start()
    request = _request()
    ref = runtime.submit(terminology_job_spec(request), owner).ref
    runtime.start(ref, owner)
    started = time.perf_counter()
    if not adapter.cancel(ref):
        raise RuntimeError("terminology cancellation request was rejected")
    elapsed = time.perf_counter() - started
    if not changes or changes[-1].state is not JobState.CANCELLING:
        raise RuntimeError("terminology cancelling state was not projected visibly")
    runtime.finish_cancelled(ref, owner)
    adapter.close()
    runtime.close()
    return elapsed


def _request() -> BuildWorkloadRequest:
    return BuildWorkloadRequest(
        project_id="project",
        variant_id="variant",
        expected=TerminologyExpectedState(0, 0, "source-graph", "source-fingerprints"),
        build_key="supplemental-ui-benchmark",
    )


def _metric(value: float, statistic: str, evidence: str, samples: tuple[float, ...]) -> dict[str, object]:
    return {
        "value": value,
        "statistic": statistic,
        "evidence": evidence,
        "raw_samples": list(samples),
        "median": statistics.median(samples),
        "unit": "seconds",
    }


def _digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
