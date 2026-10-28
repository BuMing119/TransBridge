"""Explicit targets reuse the installed project authority and result lifecycle."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from transbridge.ui.operations.download_context import DownloadContext


def test_explicit_target_preserves_current_commands_and_does_not_rebind_project():
    target = object()
    commands = object()
    context = SimpleNamespace(
        uses_authoritative_projection=True,
        project_commands=commands,
        active_project_id="project",
        active_variant_id="variant",
        resolve_paratranz_target=Mock(return_value=target),
    )
    override = DownloadContext(context, {"paratranz_project_id": "42", "paratranz_project_name": "Remote"})
    assert override.project_commands is commands
    assert override.active_project_id == "project"
    assert override.resolve_paratranz_target() is target
    context.resolve_paratranz_target.assert_called_once_with(explicit_project_id=42, explicit_project_name="Remote")
    assert context.active_project_id == "project"


@pytest.mark.parametrize("project_id", ["invalid", 0, -1, True])
def test_explicit_target_rejects_invalid_project_id(project_id):
    context = SimpleNamespace(uses_authoritative_projection=True, project_commands=object())
    with pytest.raises(ValueError, match="正整数"):
        DownloadContext(context, {"paratranz_project_id": project_id}).resolve_paratranz_target()


def test_transfer_override_requires_installed_project_commands():
    with pytest.raises(ValueError, match="本地工程"):
        DownloadContext(SimpleNamespace(uses_authoritative_projection=False))
