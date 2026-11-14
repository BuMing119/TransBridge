from dataclasses import replace

import pytest

from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    TerminologyProfileContent,
    TerminologyProfileImportPolicy,
    TerminologyProfileService,
    TerminologySourceEntry,
    TerminologySourceSnapshot,
)
from transbridge.application.terminology_profiles.existing_import import preview_existing_import
from transbridge.application.terminology_profiles.models import ProfileEntryOverride, ProfileTermMapping


@pytest.mark.parametrize("policy", tuple(TerminologyProfileImportPolicy))
def test_existing_import_preserves_identity_authority_and_other_content(policy):
    service = TerminologyProfileService(InMemoryTerminologyProfileRepository())
    content = TerminologyProfileContent(
        mappings=(ProfileTermMapping("A", "旧译", scheme_owned=True),),
        overrides=(ProfileEntryOverride("entry", "单条特例"),),
    )
    target = service.create_with_content("project", "我的方案", content)
    other = service.create("project", "其他方案")
    base = EffectiveTerminologySnapshot("project", "variant", EffectiveSnapshotStatus.NO_PROJECT_VERSION)
    source = TerminologySourceSnapshot.capture(
        "json",
        "test",
        (
            TerminologySourceEntry("A", "新译"),
            TerminologySourceEntry("B", "新增"),
        ),
    )
    preview = preview_existing_import(service, "project", "variant", base, source, target.profile_id, policy=policy)
    published = service.publish_reviewed(preview.target_publication)
    assert published.profile_id == target.profile_id
    assert published.name == target.name
    assert published.content.overrides == content.overrides
    mappings = {item.original: item for item in published.content.mappings}
    assert mappings["A"] == replace(
        content.mappings[0], translation=("旧译" if policy is TerminologyProfileImportPolicy.KEEP_EXISTING else "新译")
    )
    assert mappings["B"].scheme_owned
    assert service.get_profile(other.profile_id) == other
    with pytest.raises(RuntimeError, match="变化"):
        service.publish_reviewed(preview.target_publication)


def test_existing_import_rejects_foreign_project():
    service = TerminologyProfileService(InMemoryTerminologyProfileRepository())
    target = service.create("other-project", "其他项目方案")
    with pytest.raises(ValueError, match="不属于"):
        preview_existing_import(
            service,
            "project",
            "variant",
            None,
            None,
            target.profile_id,
            policy=TerminologyProfileImportPolicy.KEEP_EXISTING,
        )
