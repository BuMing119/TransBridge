"""The actual maintenance entry point defaults to a harmless preview."""

import json
from pathlib import Path
import runpy
from uuid import uuid4

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.bootstrap.persistence import build_persistence_v2_services


def test_cli_preview_then_explicit_offline_cleanup_in_owned_temporary_root(tmp_path, capsys):
    root = tmp_path / "repository"
    services = build_persistence_v2_services(root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now")
    try:
        assert services.gui_session_commands.create_and_activate("Test", RequestContext("owner")).is_success
        session_id = services.session_lifecycle.active.aggregate.ref.identity.value
        reference = services.gui_session_commands.assistant_requests.transcript_store.write_artifact(
            session_id, b"orphan"
        )
    finally:
        services.close()
    main = runpy.run_path(str(Path(__file__).parents[2] / "scripts" / "maintain_assistant_storage.py"))["main"]
    target = root / reference.path
    assert main(["--root", str(root)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is True
    assert report["candidates"]
    assert target.is_file()
    with pytest.raises(SystemExit) as error:
        main(["--root", str(root), "--apply"])
    assert error.value.code == 2
    assert target.is_file()
    capsys.readouterr()
    assert main(["--root", str(root), "--apply", "--offline"]) == 0
    assert not target.exists()
    assert json.loads(capsys.readouterr().out)["removed"]
