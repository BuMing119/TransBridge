"""Run one multi-source local publication under task cancellation arbitration."""

from __future__ import annotations

from dataclasses import replace
from threading import Event

from transbridge.application.io.publish import TaskRuntimeCommitGuard
from transbridge.application.tasks import JobCapabilities, JobSpec, OwnerRef, TaskCancelled, TransitionError


def run_download_task(runtime, context, fingerprint, operation, cancel):
    """Wait on a worker thread, retaining the actual operation error and receipt."""
    actor = OwnerRef(
        context.owner_id,
        "gui.operation-plan",
        context.project_id,
        context.variant_id,
        context.session_id,
        context.permissions,
    )
    ref = runtime.submit(
        JobSpec(
            "operation.paratranz.download",
            f"batch:{context.project_id}:{context.variant_id}",
            fingerprint,
            display_name="从 ParaTranz 批量下载",
            capabilities=JobCapabilities(supports_cancel=True),
        ),
        actor,
    ).ref
    done, started, outcome = Event(), Event(), {}

    def execute(cancellation):
        started.set()
        try:
            cancellation.raise_if_cancelled()
            task_context = replace(context, run_id=ref.run_id)

            def commit(mutation):
                if cancel.is_set():
                    raise TaskCancelled("下载已取消，本地译文未修改。")
                cancellation.raise_if_cancelled()
                guard = TaskRuntimeCommitGuard(runtime, runtime.commit_permit(ref, actor))

                def finish_mutation():
                    mutation()
                    # This is the final local publication: completion wins the
                    # same arbitration lock before any later cancel can run.
                    runtime.complete(ref, actor)

                decision = guard.commit(ref.run_id, finish_mutation)
                if not decision.accepted:
                    raise TaskCancelled("下载已取消，本地译文未修改。")

            outcome["receipt"] = operation(task_context, commit, cancellation)
        except Exception as exc:
            outcome["error"] = exc
            raise
        finally:
            done.set()

    runtime.schedule(ref, actor, execute)
    cancellation_requested = False
    while not done.wait(0.05):
        snapshot = runtime.get(ref, actor)
        if snapshot.is_terminal and not started.is_set():
            if snapshot.state.value == "cancelled":
                raise TaskCancelled("下载已取消，本地译文未修改。")
            raise RuntimeError("下载任务未执行完成，本地译文未修改，可在此重试。")
        if cancel.is_set() and not cancellation_requested:
            snapshot = runtime.get(ref, actor)
            if not snapshot.is_terminal:
                try:
                    runtime.cancel(ref, actor)
                except TransitionError:
                    if not runtime.get(ref, actor).is_terminal:
                        raise
            cancellation_requested = True
    if "error" in outcome:
        raise outcome["error"]
    return outcome["receipt"]
