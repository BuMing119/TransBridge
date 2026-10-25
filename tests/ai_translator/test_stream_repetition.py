import random
import re

import pytest

from transbridge.ai_translator.stream_repetition import StreamRepetitionDetector


def legacy_detect(text, original):
    runs = list(re.finditer(r"(.|.{2}|.{3})\1*", text))
    maximum = max((len(m[0]) for m in runs), default=0)
    if maximum > max(80, original * 2 + 20):
        return True, next(m.start() for m in runs if len(m[0]) == maximum)
    for width, minimum in [(4, 20), (5, 15), (6, 12), (7, 10), (8, 10), (9, 8), (10, 8)]:
        match = re.search(r"(.{" + str(width) + r"})\1{" + str(minimum - 1) + r",}", text)
        if match and len(match[0]) > 200 and len(match[0]) > original * 1.5 + 20:
            return True, match.start()
    return False, -1


@pytest.mark.parametrize("original", [0, 40, 200, 500])
@pytest.mark.parametrize("width", range(1, 11))
def test_all_prefixes_match_thresholds_and_cut_positions(width, original):
    pattern = "abcdefghij"[:width]
    # A short first run must continue to mask later longer runs of that period.
    text = "prefix\n" + pattern * 21 + "\n" + pattern * 110 + "z" * 180
    detector = StreamRepetitionDetector(original)
    for index in range(0, len(text), 7):
        assert detector.feed(text[index : index + 7]) == legacy_detect(text[: index + 7], original)


def test_random_chunk_boundaries_newlines_escapes_and_multiple_runs():
    rng = random.Random(872)
    for _ in range(80):
        text = "".join(
            "".join(rng.choices('a中\\"\n\r😀b', k=rng.randint(1, 10))) * rng.randint(1, 100) for _ in range(5)
        )
        original = rng.choice([0, 20, 100, 300])
        detector = StreamRepetitionDetector(original)
        offset = 0
        while offset < len(text):
            end = offset + rng.randint(1, 70)
            assert detector.feed(text[offset:end]) == legacy_detect(text[:end], original)
            offset = end
