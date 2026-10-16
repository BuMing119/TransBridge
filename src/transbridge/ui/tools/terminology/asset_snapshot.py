"""Collect immutable workbench assets outside the GUI event loop."""

from dataclasses import dataclass

from transbridge.application.terminology.browse_queries import TermBrowseQuery


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
        None
        if browsing is None
        else call(
            lambda: browsing.describe(
                TermBrowseQuery(context.project_id, context.variant_id),
                **({} if cancellation is None else {"cancellation": cancellation}),
            )
        ),
    )
