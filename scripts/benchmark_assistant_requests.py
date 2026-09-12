"""Offline P95 benchmark for long request sessions backed by real V2 storage.

Run: uv run python scripts/benchmark_assistant_requests.py --samples 20
The temporary repository is owned and removed by TemporaryDirectory. No model
API or user project is accessed. Heartbeat measures the current synchronous save
operation when called on a Qt event-loop thread, not an idealized worker path.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
import platform
from tempfile import TemporaryDirectory
from time import perf_counter
from uuid import uuid4

from transbridge.application.assistant_requests.models import RequestItem, UserRequest
from transbridge.application.contracts import RequestContext
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.smart_assistant.request_context_assembler import RequestContextAssembler


def summary(samples: list[float]) -> dict:
    ordered = sorted(samples)
    return {
        "samples": len(samples),
        "min_ms": round(ordered[0], 3),
        "median_ms": round(ordered[len(ordered) // 2], 3),
        "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
        "max_ms": round(ordered[-1], 3),
    }


def measure(callback, samples: int) -> dict:
    timings = []
    for index in range(samples):
        start = perf_counter()
        callback(index)
        timings.append((perf_counter() - start) * 1000)
    return summary(timings)


def dataset(count: int, requests: int) -> list[dict]:
    records = [
        {
            "message_id": f"message-{index}",
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"Message {index}: preserve variable names and explain translation progress. " * 2,
            "request_id": f"request-{min(requests - 1, index * requests // count)}",
        }
        for index in range(count)
    ]
    records[-3].update(
        role="assistant",
        content="",
        tool_calls=[
            {"id": "long-result-call", "name": "translate", "arguments": {}},
        ],
    )
    records[-2].update(
        role="tool",
        tool_call_id="long-result-call",
        name="translate",
        content=json.dumps({"result": "原始结果数据 " * 10000}, ensure_ascii=False),
    )
    records[-1].update(role="assistant", content="Translation completed; inspect the full result by reference.")
    return records


def heartbeat(callback) -> dict:
    from PyQt6.QtCore import QCoreApplication, QTimer

    app = QCoreApplication.instance() or QCoreApplication([])
    ticks = [perf_counter()]
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(perf_counter()))
    failure = []
    duration = []

    def run_save():
        start = perf_counter()
        try:
            callback()
        except Exception as error:
            failure.append(error)
        finally:
            duration.append((perf_counter() - start) * 1000)
            QTimer.singleShot(50, app.quit)

    timer.start()
    QTimer.singleShot(50, run_save)
    app.exec()
    timer.stop()
    if failure:
        raise failure[0]
    return {
        "timer_interval_ms": 10,
        "save_duration_ms": round(duration[0], 3),
        "maximum_tick_gap_ms": round(max((right - left) * 1000 for left, right in zip(ticks, ticks[1:])), 3),
        "candidate_gap_limit_ms": 200,
    }


def run(samples: int, message_count: int, request_count: int, *, profile_only: bool = False) -> dict:
    if samples < 20 or message_count < 4 or request_count < 1:
        raise ValueError("use at least 20 samples, 4 messages and 1 request")
    with TemporaryDirectory(prefix="transbridge-request-benchmark-") as root:
        services = build_persistence_v2_services(root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now")
        try:
            assert services.gui_session_commands.create_and_activate(
                "Benchmark", RequestContext("benchmark")
            ).is_success
            ref = services.session_lifecycle.active.aggregate.ref
            context = RequestContext("benchmark", session_id=ref.identity.value)
            service = services.gui_session_commands.assistant_requests
            records = dataset(message_count, request_count)
            requests = tuple(
                UserRequest(
                    f"request-{index}",
                    context.session_id,
                    f"Goal {index}",
                    (RequestItem("item", "answer"),),
                    scope=(("owner_id", "benchmark"), ("session_id", context.session_id)),
                    constraints=("Preserve variable names",),
                )
                for index in range(request_count)
            )
            service.transact(
                context, lambda state: state.update(requests=[r.to_dict() for r in requests]), history=records
            )
            print(json.dumps({"seeded_messages": message_count, "seeded_requests": request_count}), flush=True)
            if profile_only:
                import cProfile
                import pstats

                profiler = cProfile.Profile()
                profiler.runcall(service.accept_input, context, "profile input", selection={}, command_id="profile")
                pstats.Stats(profiler).strip_dirs().sort_stats("cumulative").print_stats(30)
                latest = list(service.lifecycle.read_session(ref, context).backend_messages())
                profiler = cProfile.Profile()
                profiler.runcall(service.save_history, context, latest)
                pstats.Stats(profiler).strip_dirs().sort_stats("cumulative").print_stats(30)
                return {"profiled": ["input_admission", "history_save"]}
            state = {
                "request_id": requests[-1].request_id,
                "goal": requests[-1].goal,
                "constraints": requests[-1].constraints,
                "other_requests": [{"id": r.request_id, "goal": r.goal, "status": r.status} for r in requests[:-1]],
            }
            assembler = RequestContextAssembler()
            assembler.assemble(records, request_state=state)  # warm up imports/caches
            result = {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "messages": message_count,
                "requests": request_count,
            }
            result["context_assembly"] = measure(lambda _: assembler.assemble(records, request_state=state), samples)
            print(json.dumps({"context_assembly": result["context_assembly"]}), flush=True)
            result["input_admission"] = measure(
                lambda i: service.accept_input(context, f"New question {i}", selection={}, command_id=f"ingress-{i}"),
                samples,
            )
            print(json.dumps({"input_admission": result["input_admission"]}), flush=True)
            latest = list(service.lifecycle.read_session(ref, context).backend_messages())
            result["history_save"] = measure(lambda _: service.save_history(context, latest), samples)
            print(json.dumps({"history_save": result["history_save"]}), flush=True)

            def save_on_qt():
                saved = services.gui_session_commands.save_conversation(ref, latest, latest, replace(context))
                if not saved.is_success:
                    raise RuntimeError(str(saved.diagnostics))

            result["qt_synchronous_save_heartbeat"] = heartbeat(save_on_qt)
            return result
        finally:
            services.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--messages", type=int, default=10000)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--profile-only", action="store_true")
    options = parser.parse_args()
    print(
        json.dumps(
            run(options.samples, options.messages, options.requests, profile_only=options.profile_only),
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
