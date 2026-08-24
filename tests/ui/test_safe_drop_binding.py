from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from transbridge.ui.drop_binding import SafeDropBinding
from transbridge.ui.drop_router import DropResolutionStatus, DropRouter
from transbridge.ui.shell.action_catalog import IntentId

_APP = QApplication.instance() or QApplication([])


def test_drop_binding_never_emits_intent_before_explicit_confirmation(tmp_path: Path) -> None:
    source = tmp_path / "Demo.esp"
    source.write_bytes(b"TES4" + b"\0" * 12)
    target = QWidget()
    binding = SafeDropBinding(target, router=DropRouter())
    resolutions = []
    confirmed = []
    binding.resolution_ready.connect(resolutions.append)
    binding.intent_confirmed.connect(lambda intent, payload: confirmed.append((intent, dict(payload))))

    resolution = binding.inspect_paths((str(source),))

    assert resolution.status is DropResolutionStatus.CANDIDATE
    assert resolutions == [resolution]
    assert confirmed == []

    assert binding.confirm(resolution)
    assert confirmed == [
        (
            IntentId.SOURCE_PARSE,
            {
                "path": str(source.resolve()),
                "drop_kind": "plugin",
                "format_id": "plugin.sse",
            },
        )
    ]
    binding.close()


def test_dismiss_and_close_do_not_dispatch_and_close_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "Demo.esp"
    source.write_bytes(b"TES4" + b"\0" * 12)
    target = QWidget()
    binding = SafeDropBinding(target, router=DropRouter())
    confirmed = []
    dismissed = []
    binding.intent_confirmed.connect(lambda *args: confirmed.append(args))
    binding.dismissed.connect(lambda: dismissed.append(True))
    resolution = binding.inspect_paths((str(source),))

    cancelled = binding.dismiss()
    binding.close()
    binding.close()

    assert cancelled.status is DropResolutionStatus.CANCELLED
    assert dismissed == [True]
    assert confirmed == []
    assert not binding.confirm(resolution)
