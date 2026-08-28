from tests.application.terminology.story08_support import decision
from transbridge.application.terminology.diff import CanonicalDiffEngine
from transbridge.application.terminology.models import TerminologyVersionRef
from transbridge.application.terminology.narrative import ChangeNarrativeProjector


def test_narrative_is_deterministic_and_keeps_internal_identifiers_out_of_user_messages() -> None:
    changed = decision()
    diff = CanonicalDiffEngine().compare(None, target_version_id="v1", decisions=(changed,))
    ref = TerminologyVersionRef("v1", "project-1", "variant-1", "version-content")
    projector = ChangeNarrativeProjector()

    first = projector.project(version_ref=ref, diff=diff, decisions=(changed,), conflicts=(), manual_actions=())
    second = projector.project(version_ref=ref, diff=diff, decisions=(changed,), conflicts=(), manual_actions=())

    assert first == second
    assert first.changes == diff.changes
    rendered_messages = repr(first.user_messages)
    assert changed.term_id not in rendered_messages
    assert diff.changes[0].change_id not in rendered_messages
    assert "游戏文本" not in rendered_messages
