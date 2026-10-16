from __future__ import annotations

import sqlite3

import pytest

from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    ProfileState,
    TerminologyProfileConflictError,
    TerminologyProfileService,
)
from transbridge.persistence.terminology.profiles import SqliteTerminologyProfileRepository
from transbridge.persistence.terminology.schema import initialize_schema


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_archive_impact_is_checked_atomically_and_restore_never_rebinds(backend):
    connection = sqlite3.connect(":memory:", isolation_level=None)
    initialize_schema(connection)
    repository = (
        SqliteTerminologyProfileRepository(connection)
        if backend == "sqlite"
        else InMemoryTerminologyProfileRepository()
    )
    service = TerminologyProfileService(repository)
    try:
        profile = service.create("project", "Names")
        service.publish(profile.profile_id, expected_draft_revision=0)
        service.select("project", "variant-b", profile.profile_id)
        service.select("project", "variant-a", profile.profile_id)
        reviewed = service.list_profile_selections(profile.profile_id)
        assert [item.variant_id for item in reviewed] == ["variant-a", "variant-b"]
        service.select("project", "variant-c", profile.profile_id)
        with pytest.raises(TerminologyProfileConflictError, match="重新查看归档影响"):
            service.archive(profile.profile_id, expected_selections=reviewed)
        assert repository.get_profile(profile.profile_id).state is ProfileState.ACTIVE
        assert service.selected_revision("project", "variant-a") is not None
        service.archive(profile.profile_id, expected_selections=service.list_profile_selections(profile.profile_id))
        assert service.list_profile_selections(profile.profile_id) == ()
        service.restore(profile.profile_id)
        assert service.list_profile_selections(profile.profile_id) == ()
        assert service.published_revision(profile.profile_id, 1) is not None
        assert repository.get_profile(profile.profile_id).state is ProfileState.ACTIVE
    finally:
        connection.close()
