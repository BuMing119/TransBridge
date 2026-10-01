from __future__ import annotations

from dataclasses import replace

import pytest

from transbridge.application.terminology_sync.identity import sync_line_id
from transbridge.application.terminology_sync.models import (
    TerminologySyncItemLink,
    TerminologySyncOutcome,
    TerminologySyncOwnership,
    TerminologySyncTarget,
)


def test_target_is_normalized_and_unverified_target_is_explicit() -> None:
    target = TerminologySyncTarget("HTTPS://EXAMPLE.COM/api/", None, 9)

    assert target.endpoint == "https://example.com"
    assert not target.verified
    assert "terminology-sync.identity.v1.target" in target.target_id


def test_line_id_requires_its_complete_canonical_identity() -> None:
    target = TerminologySyncTarget("https://example.com", 3, 9)
    line_id = sync_line_id(
        project_id="project-1",
        variant_id="variant-1",
        target_identity=target.target_id,
        profile_revision=0,
    )

    from transbridge.application.terminology_sync.models import TerminologySyncLine

    line = TerminologySyncLine(line_id, "project-1", "variant-1", target, 0, "2026-08-30T00:00:00Z")
    with pytest.raises(ValueError, match="canonical"):
        replace(line, variant_id="variant-2")


def test_item_link_requires_complete_local_version_evidence_and_remote_observation() -> None:
    with pytest.raises(ValueError, match="complete or absent"):
        TerminologySyncItemLink(
            "line-1",
            "item-1",
            0,
            "term-1",
            None,
            None,
            None,
            None,
            None,
            None,
            "project",
            TerminologySyncOwnership.MANAGED,
        )
    with pytest.raises(ValueError, match="revision or observed digest"):
        TerminologySyncItemLink(
            "line-1",
            "item-1",
            0,
            None,
            None,
            None,
            5,
            None,
            None,
            None,
            "project",
            TerminologySyncOwnership.REMOTE_INDEPENDENT,
        )


def test_unknown_is_a_first_class_outcome() -> None:
    link = TerminologySyncItemLink(
        "line-1",
        "item-1",
        0,
        "term-1",
        "version-1",
        "local-digest",
        5,
        None,
        "observed-digest",
        None,
        "project",
        TerminologySyncOwnership.MANAGED,
        last_outcome=TerminologySyncOutcome.UNKNOWN,
    )

    assert link.last_outcome is TerminologySyncOutcome.UNKNOWN
