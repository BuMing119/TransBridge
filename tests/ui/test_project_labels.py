"""Label reads preserve source isolation and reuse unchanged memberships."""

from transbridge.application.projections import ProjectionSnapshot
from transbridge.ui.project_labels import project_entry_labels


def states(*items):
    return ProjectionSnapshot(
        "test",
        1,
        0,
        {
            "entries": [
                {"entry_key": {"namespace": source, "local_key": key}, "labels": labels}
                for source, key, labels in items
            ]
        },
    ).values["entries"]


def test_unchanged_memberships_reused_with_source_isolation():
    raw = states(("a", "same", ["one"]), ("b", "same", ["two"]))
    labels, exact = project_entry_labels(raw, {})
    assert labels == {"same": {"two"}}
    assert exact == {("a", "same"): {"one"}, ("b", "same"): {"two"}}
    _, again = project_entry_labels(raw, exact)
    assert again is exact
    changed = states(("a", "same", ["one"]), ("b", "new", []))
    labels, updated = project_entry_labels(changed, exact)
    assert updated[("a", "same")] is exact[("a", "same")]
    assert ("b", "same") not in updated
    assert labels == {"same": {"one"}, "new": set()}


def test_duplicate_labels_cannot_preserve_removed_membership():
    _, previous = project_entry_labels(states(("a", "key", ["one", "two"])), {})
    _, updated = project_entry_labels(states(("a", "key", ["one", "one"])), previous)
    assert updated[("a", "key")] == {"one"}
