from dataclasses import replace

import pytest

from transbridge.application.io import EntryKey, SourceNamespace
from transbridge.application.translation.postprocess_checkpoint import PostProcessCheckpoint, PostProcessCheckpointEntry


def test_checkpoint_preserves_same_key_different_original_candidates() -> None:
    entries = tuple(
        PostProcessCheckpointEntry(
            EntryKey(SourceNamespace("source:test"), "key", original).to_dict(),
            1,
            "polish",
            "译文",
            "a" * 64,
            True,
            original=original,
        )
        for original in ("first", "second")
    )
    checkpoint = PostProcessCheckpoint("run", "owner", "b" * 64, entries=entries)
    assert PostProcessCheckpoint.from_dict(checkpoint.to_dict()) == checkpoint
    with pytest.raises(ValueError, match="unique EntryKeys"):
        replace(checkpoint, entries=(entries[0], entries[0]))
