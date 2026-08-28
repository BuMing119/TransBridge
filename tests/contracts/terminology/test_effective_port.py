from __future__ import annotations

from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologyPort,
    EffectiveTerminologySnapshot,
    SnapshotEffectiveTerminologyPort,
    TerminologyLookupContext,
)


class _NoVersionSnapshots:
    def snapshot(self, local_project_id, local_variant_id, version_id=None):
        assert (local_project_id, local_variant_id, version_id) == ("local-project", "local-variant", None)
        return EffectiveTerminologySnapshot(
            local_project_id,
            local_variant_id,
            EffectiveSnapshotStatus.NO_PROJECT_VERSION,
        )


def test_effective_port_is_structural_read_only_and_uses_local_string_identities():
    port = SnapshotEffectiveTerminologyPort(_NoVersionSnapshots())
    assert isinstance(port, EffectiveTerminologyPort)
    context = TerminologyLookupContext("local-project", "local-variant")

    result = port.resolve("Sword", context)

    assert result.snapshot.status is EffectiveSnapshotStatus.NO_PROJECT_VERSION
    assert result.decision is None
    assert not result.blocks_legacy_fallback
