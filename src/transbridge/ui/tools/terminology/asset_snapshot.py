# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Collect immutable workbench assets outside the GUI event loop."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkbenchAssets:
    build_ref: object = None
    build: object = None
    draft: object = None
    version_ref: object = None
    comparison: object = None
    task_result: object = None
    review_counts: object = None
    browse_context: object = None
    version: object = None


def read_assets(commands, context, *, run_id=None, browsing=None, cancellation=None) -> WorkbenchAssets:
    if context.project_id is None or context.variant_id is None:
        return WorkbenchAssets()

    def call(command):
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        value = command()
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        return value

    def read(method):
        return call(lambda: getattr(commands, method, lambda *_args: None)(context.project_id, context.variant_id))

    status_reader = getattr(commands, "workbench_status", None)
    status = None if status_reader is None else call(lambda: status_reader(context))
    if status is not None:
        return WorkbenchAssets(
            build_ref=None if status.build is None else status.build.ref,
            build=status.build,
            draft=status.draft,
            version_ref=status.version_ref,
            comparison=read("latest_comparison"),
            task_result=None if run_id is None else call(lambda: commands.task_result(run_id, context)),
            review_counts=status.review_counts,
        )

    return WorkbenchAssets(
        read("latest_build_ref"),
        read("latest_build_result"),
        call(lambda: getattr(commands, "active_draft", lambda *_args: None)(context)),
        read("latest_version_ref"),
        read("latest_comparison"),
        None
        if run_id is None
        else call(lambda: getattr(commands, "task_result", lambda *_args: None)(run_id, context)),
        call(lambda: getattr(commands, "review_counts", lambda *_args: None)(context)),
        None,  # Full scheme mappings load only when the advanced source browser is opened.
        read("latest_version"),
    )
