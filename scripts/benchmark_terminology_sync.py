"""Offline UI callback timings and deterministic refresh/cancellation probes.

All synthetic fixtures are created before timing. Hidden offscreen widgets mean
callback time excludes later compositor/paint work. Never contacts ParaTranz.
"""

# Qt platform selection and checkout imports require the bootstrap below first.
# ruff: noqa: E402

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication

from transbridge.application.contracts import RequestContext
from transbridge.application.tasks import JobSpec, JobState, OwnerRef, TaskRuntime
from transbridge.application.terminology.drafts import DraftLineState
from transbridge.application.terminology.identity import canonical_digest
from transbridge.application.terminology.models import DecisionStatus, TermDecision
from transbridge.application.terminology.query_control import QueryCancellation
from transbridge.application.terminology_sync.draft_import_models import (
    DraftImportChoice,
    DraftImportEffect,
    DraftImportMutation,
    DraftImportProposal,
    DraftImportSelection,
)
from transbridge.application.terminology_sync.execution_models import (
    TerminologyBackupExecutionResult,
    TerminologySyncItemOutcome,
    TerminologySyncItemStatus,
)
from transbridge.application.terminology_sync.inbound import InboundReviewDecision, InboundReviewStatus
from transbridge.application.terminology_sync.plan_models import (
    TerminologyContentSummary,
    TerminologySyncAction,
    TerminologySyncPlanItem,
    TerminologySyncReason,
)
from transbridge.ui.tools.terminology.async_actions import AsyncActionRunner
from transbridge.ui.tools.terminology.inbound_review import InboundReviewEdit
from transbridge.ui.tools.terminology.paged_dispatch import PageQueryDispatch
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.sync_presenter import TerminologySyncPresenter, TerminologySyncViewState
from transbridge.ui.tools.terminology.sync_projection import OutcomeProjection, ProposalProjection
from transbridge.ui.tools.terminology.sync_view import TerminologySyncPanel
from transbridge.ui.tools.terminology.window import TerminologyWindow

APP = QApplication.instance() or QApplication([])


def measure(call, runs, *, prepare=None):
    values = []
    for _ in range(runs):
        if prepare is not None:
            prepare()
        APP.processEvents()
        cpu = time.process_time()
        start = time.perf_counter()
        call()
        values.append({"wall_ms": (time.perf_counter() - start) * 1000, "cpu_ms": (time.process_time() - cpu) * 1000})
    walls = [item["wall_ms"] for item in values]
    return {"median_ms": statistics.median(walls), "min_ms": min(walls), "max_ms": max(walls), "samples": values}


def fixture(size):
    items, inbound, mutations, choices = [], [], [], []
    for i in range(size):
        identity = f"item-{i:06}"
        original = f"Synthetic enchanted sword {i}"
        before = TerminologyContentSummary(original, original.casefold(), f"旧译名 {i}", "project")
        after = TerminologyContentSummary(original, original.casefold(), f"新译名 {i}", "project")
        items.append(
            TerminologySyncPlanItem(
                identity,
                TerminologySyncAction.UPDATE_REMOTE,
                TerminologySyncReason.LOCAL_CHANGED,
                remote_id=i + 1,
                local=after,
                remote=before,
            )
        )
        # Renderer input is immutable summaries; this lightweight inbound envelope
        # avoids spending setup time hashing an unrelated durable change-set.
        inbound.append(
            SimpleNamespace(item_id=identity, local=before, remote=after, kind="remote_update", reason="remote_changed")
        )
        before_decision = TermDecision(
            f"term-{i}",
            "project",
            "variant",
            original,
            original.casefold(),
            before.translation,
            status=DecisionStatus.ADOPTED,
        )
        after_decision = TermDecision(
            f"term-{i}",
            "project",
            "variant",
            original,
            original.casefold(),
            after.translation,
            status=DecisionStatus.ADOPTED,
        )
        mutations.append(
            DraftImportMutation(
                identity, DraftImportEffect.UPDATE, InboundReviewStatus.ACCEPTED, before_decision, after_decision, None
            )
        )
        choices.append(DraftImportChoice(identity, InboundReviewDecision.ACCEPT))
    selection = DraftImportSelection(
        "changes", "digest", 0, DraftLineState("project", "variant", 1, "version", "effective"), tuple(choices)
    )
    payload = dict(
        selection=selection,
        source_review_revision=0,
        initial_decision_digest="initial",
        mutations=tuple(mutations),
        decisions=(),
        diagnostics=(),
    )
    proposal = DraftImportProposal(
        **payload,
        counts=(("update", size),),
        proposal_digest=canonical_digest(payload, namespace="terminology-sync.draft-import-proposal.v1"),
    )
    change_set = SimpleNamespace(change_set_id="changes", items=tuple(inbound), created_at=datetime.now(UTC))
    outcomes = tuple(
        TerminologySyncItemOutcome(
            item.item_id, item.action, TerminologySyncItemStatus.SUCCEEDED, "OK", "已完成", remote_id=i + 1
        )
        for i, item in enumerate(items)
    )
    result = TerminologyBackupExecutionResult("run", "hash", outcomes)
    return tuple(items), change_set, proposal, result


def render_suite(sizes, runs):
    output = []
    for size in sizes:
        items, changes, proposal, result = fixture(size)
        presenter = TerminologySyncPresenter(SimpleNamespace(), RequestContext("owner"), OwnerRef("owner", "gui"))
        panel = TerminologySyncPanel(presenter)
        state = TerminologySyncViewState(result=result, result_items=items, job_state=JobState.COMPLETED)
        try:
            measured = {"terms": size}
            measured["worker_result_projection"] = measure(lambda: OutcomeProjection(result, items), runs)
            measured["worker_proposal_projection"] = measure(lambda: ProposalProjection(proposal), runs)
            measured["result_first_render"] = measure(
                lambda: panel.render_sync(state), runs, prepare=lambda: panel.result_details.set_rows(())
            )
            measured["result_unchanged_render"] = measure(lambda: panel.render_sync(state), runs)
            measured["result_page_50_rows"] = measure(
                lambda: (panel.result_details._move(1), panel.result_details._move(-1)), runs
            )
            presenter._state = TerminologySyncViewState(
                inbound_sets=(changes,),
                selected_inbound_id="changes",
                inbound_items=changes.items[:50],
                inbound_total=size,
                inbound_proposal=proposal,
            )
            review = panel.inbound_review
            review.render(presenter.state)
            measured["inbound_next_then_previous_50_rows"] = measure(lambda: (review.page(50), review.page(-50)), runs)
            measured["preview_table_rows_retained"] = len(review.preview_details._page)
            measured["preview_total_rows_available"] = review.preview_details._projection.total
            measured["visible_inbound_rows"] = review.table.rowCount()
            measured["visible_preview_rows"] = review.preview_details.table.rowCount()
            review._drafts.remember(
                "changes",
                tuple(
                    InboundReviewEdit(item, InboundReviewDecision.ACCEPT, item.remote.translation)
                    for item in changes.items
                ),
            )
            measured["prepare_all_accepted_choices"] = measure(lambda: review._drafts.choices("changes"), runs)
            measured["ui_capture_all_accepted_edits"] = measure(lambda: review._drafts.snapshot("changes"), runs)
            review._drafts.remember(
                "changes",
                tuple(
                    InboundReviewEdit(item, InboundReviewDecision.EDIT, item.remote.translation)
                    for item in changes.items
                ),
            )
            measured["prepare_all_edited_choices"] = measure(lambda: review._drafts.choices("changes"), runs)
            measured["ui_capture_all_edited_edits"] = measure(lambda: review._drafts.snapshot("changes"), runs)
            output.append(measured)
            print(json.dumps(measured, ensure_ascii=False), flush=True)
        finally:
            panel.dispose()
            panel.deleteLater()
            APP.processEvents()
    return output


def task_history_counts(size, *, execute=False):
    runtime = TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: str(uuid4())), clock=SimpleNamespace(now=lambda: datetime.now(UTC))
    )
    owner = OwnerRef("owner", "gui", "project", "variant")
    for _ in range(size):
        ref = runtime.submit(JobSpec("terminology.report.render", "input", "hash"), owner).ref
        runtime.start(ref, owner)
        runtime.complete(ref, owner)
    released = threading.Event()
    started, read_steps, delivered = [], [], []

    class Commands:
        def latest_build_ref(self, *args):
            started.append(args)
            assert released.wait(3)

        def __getattr__(self, name):
            def read(*args):
                read_steps.append(name)

            return read

    window = TerminologyWindow(
        TerminologyPresenter(
            TerminologyUiServices(runtime=runtime, commands=Commands()),
            RequestContext("owner", project_id="project", variant_id="variant"),
        )
    )
    calls = []
    pool = QThreadPool()
    pool.setMaxThreadCount(4)
    window._asset_refresh._runner._pool = pool
    window._asset_refresh._ready = delivered.append
    try:
        original_run = window._asset_refresh._runner.run_read

        def record(command, on_success, **kwargs):
            calls.append("assets")
            if execute:
                # Keep real AsyncActionRunner dispatch/cancellation and real
                # read_assets; omit downstream table refresh to isolate reads.
                return original_run(command, on_success, **kwargs)
            return QueryCancellation()

        window._asset_refresh._runner.run_read = record
        deadline = time.monotonic() + 2
        while (not calls or len(window._finished_runs) != size) and time.monotonic() < deadline:
            APP.processEvents()
        assert calls and len(window._finished_runs) == size
        released.set()
        assert pool.waitForDone(3000)
        APP.processEvents()
        if execute:
            deadline = time.monotonic() + 2
            while not delivered and time.monotonic() < deadline:
                APP.processEvents()
                assert pool.waitForDone(1000)
            assert delivered
        return {
            "completed_history_tasks": size,
            "scheduled_actions": dict(Counter(calls)),
            "finished_runs": len(window._finished_runs),
            "execute": execute,
            "pool_max_threads": 4,
            "assets_reads_actually_started": len(started),
            "read_steps_after_cancel_or_release": dict(Counter(read_steps)),
            "delivered_asset_snapshots": len(delivered),
        }
    finally:
        released.set()
        pool.waitForDone(3000)
        window.close()
        window.deleteLater()
        APP.processEvents()
        runtime.close()


def cancel_probe(kind):
    pool = QThreadPool()
    pool.setMaxThreadCount(1)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    delivered = []

    def command(token):
        entered.set()
        if not release.wait(3):
            raise TimeoutError("probe release missing")
        token.raise_if_cancelled()
        finished.set()  # Must stay false for the new explicitly read-only APIs.
        return "result"

    if kind == "page":
        runner = PageQueryDispatch(pool)
        runner.signals.succeeded.connect(lambda *args: delivered.append(args))
        runner.start_read(1, command)
    else:
        runner = AsyncActionRunner(on_error=delivered.append, thread_pool=pool)
        runner.run_read(command, delivered.append, key="assets")
    assert entered.wait(3)
    if kind == "page":
        runner.cancel()
    else:
        runner.run_read(lambda token: "replacement", delivered.append, key="assets")
    release.set()
    assert pool.waitForDone(3000)
    APP.processEvents()
    runner.close()
    return {
        "path": kind,
        "started_before_cancel": entered.is_set(),
        "continued_after_cancel": finished.is_set(),
        "stale_result_delivered": bool(delivered) if kind == "page" else "result" in delivered,
        "replacement_result_delivered": "replacement" in delivered,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("render", "counts"), required=True)
    parser.add_argument("--sizes", nargs="+", type=int, default=[1000, 10000, 50000])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.runs < 5:
        parser.error("--runs must be at least 5")
    if any(size < 1 for size in args.sizes):
        parser.error("--sizes must contain positive integers")
    if args.output.exists():
        raise FileExistsError(args.output)
    metadata = {
        "python": sys.version,
        "platform": platform.platform(),
        "suite": args.suite,
        "qt_platform": APP.platformName(),
        "runs": args.runs,
        "command": subprocess.list2cmdline([sys.executable, *sys.argv]),
        "scope": "S07 callback comparison; same fixtures; excludes deferred paint; projection and choice conversion costs reported separately as worker work",
        "baseline": "Compare with the original audit render/counts JSON; existing outputs are never overwritten",
    }
    if args.suite == "render":
        data = render_suite(args.sizes, args.runs)
    else:
        data = {
            "history": [
                {"trial": trial, **task_history_counts(n, execute=execute)}
                for trial in range(1, args.runs + 1)
                for n in (1, 20, 100)
                for execute in (False, True)
            ],
            "cancellation": [cancel_probe(kind) for kind in ("page", "assets")],
        }
        print(json.dumps(data, ensure_ascii=False), flush=True)
    args.output.write_text(
        json.dumps({"metadata": metadata, "data": data}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
