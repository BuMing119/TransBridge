from __future__ import annotations

import pytest

from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError

from .controlled_server import ControlledFault, ControlledParaTranzTermsServer
from .scenario_builder import TerminologySyncScenarioBuilder
from .test_acceptance_backup import controlled_service


def test_real_http_pagination_is_deterministic_and_fully_logged() -> None:
    builder = TerminologySyncScenarioBuilder(seed=5170806)
    records = tuple(
        {
            "id": remote_id,
            "term": f"Remote-{remote_id}",
            "translation": f"远端-{remote_id}",
        }
        for remote_id in range(1, 6)
    )
    with ControlledParaTranzTermsServer(terms=records) as server:
        service = controlled_service(server)
        try:
            snapshot = service.snapshot_terms(builder.remote_project_id, page_size=2)
        finally:
            service.close()

    assert [item.remote_id for item in snapshot.items] == [1, 2, 3, 4, 5]
    assert snapshot.stable
    assert [request.method for request in server.requests] == ["GET", "GET", "GET"]
    assert [dict(request.query)["page"] for request in server.requests] == [("1",), ("2",), ("3",)]


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (401, ExternalServiceCategory.AUTHENTICATION),
        (403, ExternalServiceCategory.AUTHORIZATION),
        (429, ExternalServiceCategory.RATE_LIMITED),
        (503, ExternalServiceCategory.UNAVAILABLE),
    ],
)
def test_real_http_faults_keep_typed_error_and_never_mutate_remote(
    status: int,
    category: ExternalServiceCategory,
) -> None:
    with ControlledParaTranzTermsServer() as server:
        service = controlled_service(server)
        server.queue_fault(ControlledFault("GET", "response", status=status, retry_after=0))
        try:
            with pytest.raises(ExternalServiceError) as exc_info:
                service.snapshot_terms(server.project_id)
        finally:
            service.close()

    assert exc_info.value.category is category
    assert server.write_requests == ()
    assert server.terms == ()
