from __future__ import annotations

import json
from pathlib import Path

import pytest

from .evidence import EVIDENCE_SCHEMA_VERSION, RELEASE_GATE_BLOCKED, TerminologySyncEvidenceManifest


def _manifest() -> TerminologySyncEvidenceManifest:
    return TerminologySyncEvidenceManifest(
        "tests/integration/terminology_sync/test_evidence.py::test_manifest",
        "controlled-1",
        51708,
        "version-1",
        "local-digest",
        "remote-digest",
        0,
        "plan-hash",
        "run-1",
        (("confirmed", 1),),
        (("GET", 2), ("POST", 1)),
        (("remote_fetch", 1.25),),
    )


def test_manifest_is_deterministic_traceable_and_never_claims_release_pass(tmp_path: Path) -> None:
    manifest = _manifest()
    path = manifest.write(tmp_path / "evidence.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert payload["fixture_seed"] == 51708
    assert payload["release_gate"] == RELEASE_GATE_BLOCKED
    assert payload["request_counts"] == {"GET": 2, "POST": 1}
    assert path.read_text(encoding="utf-8") == manifest.to_json() + "\n"


def test_manifest_rejects_formal_pass_claim_and_secret_shaped_count_key() -> None:
    with pytest.raises(ValueError, match="formal release gate"):
        replace = _manifest().__class__(
            "node",
            "scenario",
            1,
            "version",
            "local",
            "remote",
            None,
            "plan",
            "run",
            (),
            (),
            release_gate="passed",
        )
        del replace

    manifest = _manifest().__class__(
        "node",
        "scenario",
        1,
        "version",
        "local",
        "remote",
        None,
        "plan",
        "run",
        (),
        (("token", 1),),
    )
    with pytest.raises(ValueError, match="secret field"):
        manifest.to_dict()
