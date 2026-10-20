"""Install a newly created project's complete workbench as one GUI transition."""

from __future__ import annotations

from dataclasses import dataclass

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.io import EntryKey
from transbridge.ui.project_view_binding import prepare_project_view
from transbridge.ui.source_hydration import prepare_project_slots


@dataclass(frozen=True, slots=True)
class CreatedProjectView:
    result: object
    snapshot: object = None
    slots: tuple = ()
    state: object = None


class ProjectCreationOperation:
    """Keep old slots visible until the worker prepares the complete replacement."""

    def __init__(self, host) -> None:
        self._host = host

    def start(self, operation, message, on_result, on_error, *, has_sources: bool, preview_token: str) -> bool:
        host = self._host
        context = host.context
        token = None
        committed = False

        def begin():
            nonlocal token
            token = context.begin_project_open()

        def release():
            nonlocal token
            if token is not None:
                context.finish_project_open(token)
                token = None

        def run():
            nonlocal committed
            if context.authoritative_projection_diverged():
                host.project_commands.discard_create(preview_token, host.runtime_context)
                return CreatedProjectView(
                    OperationResult.failed(
                        DomainError(
                            ErrorCategory.CONFLICT,
                            "PROJECTION_AUTHORITY_DIVERGED",
                            "当前工作台内容尚未保存，无法切换到新工程。请先重试保存，或备份当前内容后重新打开工程。",
                        )
                    )
                )
            result = operation()
            if not result.is_success or result.value is None:
                return CreatedProjectView(result)
            committed = True
            projection = host.app_runtime.use_cases.resolve("project_projection")
            snapshot = projection.snapshot()
            if snapshot is None or snapshot.values.get("project_id") != result.value["project_id"]:
                raise RuntimeError("新建工程已发生变化，请重新打开工程。")
            sources = ()
            if has_sources:
                hydration = host.project_commands.consume_create_hydration(
                    str(result.value["project_id"]), host.runtime_context
                )
                if not hydration.is_success or hydration.value is None:
                    diagnostic = hydration.diagnostics[0]
                    raise DomainError(ErrorCategory.PREREQUISITE, diagnostic.code, diagnostic.message)
                sources = hydration.value.sources
            slots = prepare_project_slots(sources, snapshot)
            required = {EntryKey.from_dict(entry["entry_key"]) for entry in snapshot.values.get("entries", ())}
            visible = {entry.identity for _, slot in slots for entry in slot.collection}
            if visible != required:
                raise RuntimeError("工程来源未能完整加载，已保留正式工程记录。")
            return CreatedProjectView(result, snapshot, slots, prepare_project_view(snapshot))

        def fail(message):
            if committed:
                context.project_view_failed = True
            release()
            if committed:
                on_result(
                    OperationResult.failed(
                        DomainError(
                            ErrorCategory.PREREQUISITE,
                            "PROJECT_VIEW_INSTALL_FAILED",
                            f"工程已创建，但工作台未能完整加载：{message} 请重新打开工程。",
                        )
                    )
                )
            else:
                on_error(message)

        def received(bundle):
            try:
                if bundle.result.is_success and bundle.result.value is not None:
                    if not context.install_project_open(token, bundle.snapshot, bundle.slots, state=bundle.state):
                        raise RuntimeError("工程内容在创建期间已发生变化。")
                release()
                if committed and context.project_view_failed:
                    raise RuntimeError("工程内容在工作台加载期间已发生变化。")
            except Exception as exc:
                fail(str(exc))
                return
            on_result(bundle.result)

        try:
            accepted = host.start_foreground_task(
                run,
                message=message,
                on_result=received,
                on_error=fail,
                before_start=begin,
            )
        except Exception as exc:
            fail(str(exc))
            return True
        if not accepted:
            release()
        return accepted
