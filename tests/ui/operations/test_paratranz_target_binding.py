from types import SimpleNamespace

from tests.conftest import make_test_collection
from transbridge.application.sync import ConflictPolicy, DeletionPolicy
from transbridge.ui.operations.plan_view import OperationKind
from transbridge.ui.operations.production_support import sync_request, sync_request_target_is_current


def _context(*, binding=None, browsed_id=999):
    return SimpleNamespace(
        collection=make_test_collection(3),
        paratranz_binding=binding,
        project_revision=7,
        current_project={"id": browsed_id, "name": "Browsed only"},
        config=SimpleNamespace(base_url="https://paratranz.cn", user_id=5),
        current_user={"id": 5},
        active_project_id="local-project",
    )


def test_sync_request_uses_project_binding_and_ignores_browsed_project() -> None:
    request, ready, reason = sync_request(
        _context(
            binding={
                "project_id": 42,
                "project_name": "Bound",
                "endpoint": "https://paratranz.cn",
                "account_user_id": 5,
            }
        ),
        OperationKind.UPLOAD,
        False,
    )

    assert ready and not reason
    assert request.project_id == 42
    assert request.target_project_name == "Bound"
    assert request.target_source == "project_binding"
    assert request.target_revision == "project_binding:7:config-0"
    assert request.conflict_policy is ConflictPolicy.PREFER_LOCAL


def test_sync_request_is_unbound_instead_of_falling_back_to_browsed_project() -> None:
    request, ready, reason = sync_request(_context(binding=None), OperationKind.DOWNLOAD, False)

    assert not ready
    assert request.project_id == 0
    assert request.target_source == "unbound"
    assert "尚未绑定" in reason
    assert request.conflict_policy is ConflictPolicy.PREFER_REMOTE
    assert request.deletion_policy is DeletionPolicy.PRESERVE


def test_explicit_operation_override_has_priority_and_revision_identity() -> None:
    request, ready, reason = sync_request(
        _context(
            binding={
                "project_id": 42,
                "project_name": "Bound",
                "endpoint": "https://paratranz.cn",
                "account_user_id": 5,
            }
        ),
        OperationKind.UPLOAD,
        False,
        {"paratranz_project_id": "88", "paratranz_project_name": "Chosen"},
    )

    assert ready and not reason
    assert request.project_id == 88
    assert request.target_project_name == "Chosen"
    assert request.target_source == "explicit"
    assert request.target_revision == "explicit:-:config-0"


def test_project_revision_change_invalidates_frozen_sync_target() -> None:
    context = _context(
        binding={
            "project_id": 42,
            "project_name": "Bound",
            "endpoint": "https://paratranz.cn",
            "account_user_id": 5,
        }
    )
    request, ready, _reason = sync_request(context, OperationKind.UPLOAD, False)

    assert ready
    context.project_revision = 8
    current, reason = sync_request_target_is_current(request)

    assert not current
    assert "重新检查" in reason


def test_config_revision_change_invalidates_frozen_explicit_target() -> None:
    context = _context(binding=None)
    context.config.config_revision = 3
    request, ready, _reason = sync_request(
        context,
        OperationKind.DOWNLOAD,
        False,
        {"paratranz_project_id": "88"},
    )

    assert ready
    context.config.config_revision = 4
    current, _reason = sync_request_target_is_current(request)

    assert not current


def test_explicit_target_can_request_project_default_persistence() -> None:
    context = _context(binding=None)
    request, ready, reason = sync_request(
        context,
        OperationKind.UPLOAD,
        False,
        {"paratranz_project_id": "88", "set_as_default": "true"},
    )

    assert ready and not reason
    assert request.persist_as_default is True

    context.active_project_id = None
    _request, ready, reason = sync_request(
        context,
        OperationKind.UPLOAD,
        False,
        {"paratranz_project_id": "88", "set_as_default": "true"},
    )
    assert not ready
    assert "不能保存" in reason
