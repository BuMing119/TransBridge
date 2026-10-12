from __future__ import annotations

from dataclasses import replace

import pytest

from tests.contracts.tasks.test_task_center_actions import _history_record
from transbridge.application.assistant_requests.models import (
    AssistantExecutionRef,
    EffectIntent,
    EffectStatus,
    ExecutionDispatch,
    RequestError,
    RequestItem,
    RequestStatus,
    UserRequest,
)
from transbridge.application.contracts import RequestContext
from transbridge.application.tasks import OwnerRef, TaskCenterAction, TaskNavigationIntent
from transbridge.bootstrap.composition import build_runtime


class _Preparation:
    def resolve(self, _record, _actor, _action):
        return None

    def prepare_retry(self, record, _actor):
        return TaskNavigationIntent("feature.prepare", (("run_id", record.run_id),))


@pytest.mark.parametrize("association", ["effect", "dispatch"])
def test_real_runtime_preparation_rejects_cancelled_assistant_request_ownership(tmp_path, association):
    runtime = build_runtime({"persistence_v2_root": tmp_path / "v2", "ui_config_path": tmp_path / "ui.json"})
    try:
        sessions = runtime.use_cases.resolve("gui_session_commands")
        assert sessions.create_and_activate("Owned request", RequestContext("owner")).is_success
        lifecycle = runtime.use_cases.resolve("session_lifecycle")
        session_id = lifecycle.active.aggregate.ref.identity.value
        context = RequestContext("owner", session_id=session_id)
        owner = OwnerRef("owner", "gui", session_id=session_id)
        record = _history_record(owner)
        runtime.use_cases.resolve("task_history").append(record)
        runtime.use_cases.resolve("task_history_navigators").register(record.job_type, _Preparation())
        actions = runtime.use_cases.resolve("task_center_actions")
        item = actions.list_history(owner, retry_context=None)[0]
        assert item.available_actions.retry and item.retry_requires_preparation
        prepared = actions.execute(item, TaskCenterAction.RETRY, owner, retry_context=None)
        assert prepared.job_ref is None and prepared.navigation.target == "feature.prepare"
        assert not runtime.tasks.list(owner)

        execution = AssistantExecutionRef("request", 1, ("item",), "attempt", "dispatch", "turn", session_id)
        request = UserRequest(
            "request",
            session_id,
            "Owned task",
            (RequestItem("item", "Run operation"),),
            status=RequestStatus.CANCELLED,
        )
        if association == "effect":
            request = replace(
                request,
                effects=(
                    EffectIntent(
                        "effect",
                        "digest",
                        execution,
                        0,
                        status=EffectStatus.CANCELLED,
                        job_id=record.job_id,
                        run_id=record.run_id,
                    ),
                ),
            )
        else:
            request = replace(
                request,
                dispatches=(
                    ExecutionDispatch(
                        "dispatch",
                        execution,
                        record.job_id,
                        record.run_id,
                        status="cancelled",
                    ),
                ),
            )
        sessions.assistant_requests.transact(
            context,
            lambda state: state.setdefault("requests", []).append(request.to_dict()),
        )

        # The facade must recheck durable request ownership after listing the
        # action, even though preparation itself does not start a new task.
        with pytest.raises(RequestError, match="REQUEST_RECOVERY_REQUIRED"):
            actions.execute(item, TaskCenterAction.RETRY, owner, retry_context=None)
        assert not runtime.tasks.list(owner)
    finally:
        runtime.close()
