"""The update handoff respects the actual asynchronous save/close lifecycle."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from transbridge.application.update_service import ReleaseInfo
from transbridge.ui.shell.update_controller import UpdateController
from transbridge.ui.worker_registry import get_api_worker_registry

from . import test_worker_shutdown_registry

host = test_worker_shutdown_registry.host


@pytest.mark.parametrize("saved", [True, False])
def test_update_cannot_launch_until_save_and_close_succeed(host, tmp_path: Path, monkeypatch, saved):
    controller = UpdateController(host)
    controller._checked_once = True
    installer = tmp_path / "Setup.exe"
    installer.write_bytes(b"installer")
    controller._release = ReleaseInfo("0.2.1.10", "", "", "", "a" * 64, 9)
    controller._downloaded_installer = installer
    host.close_cancelled = Mock()
    host.close_cancelled.emit.side_effect = controller.cancel_installation
    host.save_ok = saved
    launched = []
    monkeypatch.setattr(
        "transbridge.ui.shell.update_controller.launch_after_exit", lambda *args, **kwargs: launched.append(args)
    )
    try:
        controller._install_downloaded()
        assert not launched
        test_worker_shutdown_registry._wait(
            lambda: not get_api_worker_registry().busy and (host.close_ready or not host.close_pending)
        )
        assert not launched  # Accepting the window close still precedes process exit.
        if saved:
            assert host.order == ["save", "disposed", "closed"]
            assert controller._pending_installer == installer
            controller._launch_pending_installer()
            assert launched == [(installer,)]
        else:
            assert host.isVisible() and not host.close_ready
            assert controller._pending_installer is None
            assert not controller._installation_scheduled
            controller._launch_pending_installer()
            assert not launched
    finally:
        controller.cancel_installation()
