from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.application.terminology_sync.test_service import _Contexts, _plan, _Planning
from transbridge.application.contracts import RequestContext
from transbridge.application.tasks import JobState, OwnerRef, TaskRuntime
from transbridge.application.tasks.backends import CallbackThreadBackend
from transbridge.application.terminology_sync.execution_models import (
    TerminologyBackupExecutionResult,
    TerminologySyncRetryToken,
)
from transbridge.application.terminology_sync.models import TerminologySyncMode
from transbridge.application.terminology_sync.service import TerminologySyncApplicationService
from transbridge.application.terminology_sync.task_adapter import (
    TerminologySyncTaskDraft,
    TerminologySyncTaskEntrypoint,
)
from transbridge.application.terminology_sync.use_case import (
    AuthorizedTerminologySyncPlan,
    TerminologySyncPlanStaleError,
)


def runtime_service(executor, plan=None):
    plan = plan or _plan()
    runtime = TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: str(uuid4())),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
        backend=CallbackThreadBackend(dispatch=lambda _run, work: work()),
    )
    owner = OwnerRef("owner-1", "gui", "project-1", "variant-1")
    context = RequestContext("owner-1", project_id="project-1", variant_id="variant-1")
    entrypoint = TerminologySyncTaskEntrypoint(runtime, executor)
    service = TerminologySyncApplicationService(
        contexts=_Contexts(),
        planning=_Planning(plan),
        tasks=entrypoint,
        runtime=runtime,
    )
    service.create_plan(context, TerminologySyncMode.BACKUP)
    return runtime, owner, context, entrypoint, service, plan


@pytest.mark.parametrize("operation", ["submit", "reconcile"])
def test_failure_before_item_results_keeps_original_reason_and_owner_guard(operation):
    error = TerminologySyncPlanStaleError("Remote snapshot changed; generate a fresh plan")

    def fail(_request):
        raise error

    runtime, owner, _, entrypoint, service, plan = runtime_service(SimpleNamespace(execute=fail, reconcile=fail))
    authorized = AuthorizedTerminologySyncPlan(plan, owner.owner_id, "TEST")
    try:
        if operation == "submit":
            ref = service.submit(authorized, owner)
            read_failure = service.failure
        else:
            token = TerminologySyncRetryToken(
                plan.line_id, plan.target_identity, plan.plan_hash, owner.owner_id, (), ("i",)
            )
            ref = entrypoint.reconcile(TerminologySyncTaskDraft(authorized, token), owner)
            read_failure = entrypoint.failure
        assert runtime.get(ref, owner).state is JobState.FAILED
        assert entrypoint.result(ref, owner) is None
        failure = read_failure(ref, owner)
        assert failure.code == error.code
        assert failure.message == str(error)
        with pytest.raises(PermissionError):
            read_failure(ref, OwnerRef("stranger", "gui", "project-1", "variant-1"))
    finally:
        runtime.close()


def test_result_item_names_use_authorized_plan_without_new_remote_read():
    def execute(request):
        return TerminologyBackupExecutionResult(request.run_id, request.authorized_plan.plan.plan_hash, ())

    runtime, owner, _, _, service, plan = runtime_service(SimpleNamespace(execute=execute))
    try:
        ref = service.submit(AuthorizedTerminologySyncPlan(plan, owner.owner_id, "TEST"), owner)
        assert runtime.get(ref, owner).state is JobState.COMPLETED
        assert service.failure(ref, owner) is None
        assert service.result_items(ref, owner) == plan.items
        with pytest.raises(PermissionError):
            service.result_items(ref, OwnerRef("stranger", "gui", "project-1", "variant-1"))
    finally:
        runtime.close()
