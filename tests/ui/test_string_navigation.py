from transbridge.ui.paratranz.string_navigation import filtered_indices, sync_candidates

STRINGS = [
    {"id": 1, "original": "Hello", "stage": 0, "user": {"id": 7}},
    {"id": 2, "original": "Hello", "stage": 1, "user": {"id": 8}},
    {"id": 3, "original": "Other", "stage": 2, "user": {"id": 7}},
]


def test_navigation_filter_preserves_source_indices() -> None:
    assert filtered_indices(
        STRINGS,
        selected_stages={0, 2},
        modifier_id=1,
        current_user_id=7,
    ) == [0, 2]


def test_sync_candidates_excludes_current_and_higher_stages() -> None:
    assert sync_candidates(STRINGS, STRINGS[1], new_stage=2) == [STRINGS[0]]
