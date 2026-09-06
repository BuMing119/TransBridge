from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import shutil

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, TranslationIoUseCase
from transbridge.application.io.operation_write import HydratedWriteDraft, HydratedWritePreflightService
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    ProfileTermMapping,
    TerminologyProfileContent,
    TerminologyProfileService,
    TerminologyProfileWriteProjectionSource,
)

FIXTURE = Path("tests/contracts/io/fixtures/eet-small.xml")


def test_write_preflight_freezes_profile_projection_without_mutating_common_entry(tmp_path: Path) -> None:
    source_path = tmp_path / "source.xml"
    shutil.copyfile(FIXTURE, source_path)
    parsed = TranslationIoUseCase().parse(
        ParseRequest(
            SourceDescriptor(str(source_path), source_path.name, source_path.stat().st_size),
            RequestContext("gui"),
            FormatId.XML_EET,
        )
    )
    common = replace(parsed.entries[0], original="Visit Whiterun", translation="前往雪漫", stage=1)
    ids = iter(("profile-a", "profile-b"))
    service = TerminologyProfileService(
        InMemoryTerminologyProfileRepository(),
        now=lambda: datetime(2026, 9, 6, tzinfo=UTC),
        new_id=lambda: next(ids),
    )

    def publish(name: str, target: str) -> str:
        profile = service.create("project-1", name)
        draft = service.save_draft(
            profile.profile_id,
            TerminologyProfileContent(mappings=(ProfileTermMapping("Whiterun", target, "雪漫"),)),
            expected_revision=0,
        )
        service.publish(profile.profile_id, expected_draft_revision=draft.draft_revision)
        return profile.profile_id

    profile_a = publish("A", "白漫")
    profile_b = publish("B", "雪漫城")
    base_snapshot = EffectiveTerminologySnapshot(
        "project-1",
        "variant-1",
        EffectiveSnapshotStatus.READY,
        version_id="base-1",
        content_digest="1" * 64,
        decisions=(
            TermDecision(
                "term-whiterun",
                "project-1",
                "variant-1",
                "Whiterun",
                "whiterun",
                "雪漫",
                TermScope.project(),
                DecisionStatus.ADOPTED,
            ),
        ),
    )
    projection = TerminologyProfileWriteProjectionSource(
        lambda _project_id: service,
        base_snapshot_for=lambda _project_id, _variant_id: base_snapshot,
    )
    preflights = HydratedWritePreflightService(entry_projection=projection)
    request = HydratedWriteDraft(
        parsed.source_snapshot,
        FormatId.XML_EET,
        (common.snapshot(),),
        str(tmp_path / "translated.xml"),
        1,
        RequestContext("gui", project_id="project-1", variant_id="variant-1"),
    )

    service.select("project-1", "variant-1", profile_a)
    frozen_a = preflights.preflight(request)
    service.select("project-1", "variant-1", profile_b)
    frozen_b = preflights.preflight(request)

    assert frozen_a.projected_entries[0].translation == "前往白漫"
    assert frozen_b.projected_entries[0].translation == "前往雪漫城"
    assert frozen_a.projection_identity != frozen_b.projection_identity
    assert frozen_a.request_digest != frozen_b.request_digest
    assert common.translation == "前往雪漫"
