from __future__ import annotations

import pytest

from transbridge.ui.foundation.accessibility import (
    AccessibleStateCue,
    ContrastPair,
    ContrastRole,
    RgbColor,
    UnavailableAccessibilityHintsSource,
    configure_accessible_widget,
    contrast_ratio,
    parse_color,
    scaled_pixels,
    update_accessible_state,
    validate_contrast_pairs,
    validate_state_cue,
)
from transbridge.ui.foundation.model import RgbaColor


class _Widget:
    def __init__(self) -> None:
        self.name = ""
        self.description = ""
        self.focus_policy = None

    def setAccessibleName(self, value: str) -> None:  # noqa: N802 - test double for Qt API
        self.name = value

    def setAccessibleDescription(self, value: str) -> None:  # noqa: N802 - test double for Qt API
        self.description = value

    def setFocusPolicy(self, value: object) -> None:  # noqa: N802 - test double for Qt API
        self.focus_policy = value


def test_color_parsing_and_wcag_ratio_are_unrounded() -> None:
    assert parse_color("#fff") == RgbColor(255, 255, 255)
    assert parse_color((0, 0, 0)) == RgbColor(0, 0, 0)
    assert parse_color(RgbaColor(255, 255, 255, 255)) == RgbColor(255, 255, 255)
    assert contrast_ratio("#000", "#fff") == pytest.approx(21.0)
    assert contrast_ratio("#777", "#fff") < 4.5


def test_contrast_registry_checks_role_thresholds_duplicates_and_exemptions() -> None:
    result = validate_contrast_pairs([
        ContrastPair("body", "#777", "#fff", ContrastRole.TEXT),
        ContrastPair("focus", "#767676", "#fff", ContrastRole.FOCUS_INDICATOR),
        ContrastPair("focus", "#000", "#fff", ContrastRole.UI_COMPONENT),
        ContrastPair("disabled", "#aaa", "#fff", exempt=True),
    ])

    assert not result.valid
    assert {issue.code for issue in result.issues} == {
        "contrast_ratio_too_low",
        "contrast_pair_duplicate",
        "contrast_exemption_reason_missing",
    }
    low = next(issue for issue in result.issues if issue.code == "contrast_ratio_too_low")
    assert low.actual_ratio is not None and low.actual_ratio < 4.5
    assert low.required_ratio == 4.5


def test_state_cue_rejects_color_only_and_accepts_text_or_described_icon() -> None:
    assert validate_state_cue(AccessibleStateCue("failed")) == ("accessible_state_color_only",)
    assert validate_state_cue(AccessibleStateCue("failed", visible_text="失败")) == ()
    assert (
        validate_state_cue(AccessibleStateCue("failed", icon_id="status.failed", accessible_description="任务失败"))
        == ()
    )


def test_widget_helper_sets_metadata_and_updates_dynamic_state_without_pyqt_dependency() -> None:
    widget = _Widget()

    configure_accessible_widget(
        widget,
        name="任务状态",
        description="AI 翻译",
        state_text="正在运行",
        focus_policy="strong",
    )
    update_accessible_state(widget, "已完成", description="AI 翻译")

    assert widget.name == "任务状态"
    assert widget.description == "AI 翻译；已完成"
    assert widget.focus_policy == "strong"


def test_qt_65_default_hints_are_explicitly_unavailable() -> None:
    snapshot = UnavailableAccessibilityHintsSource().snapshot()

    assert not snapshot.available
    assert snapshot.reduce_motion is None
    assert snapshot.increase_contrast is None


@pytest.mark.parametrize(("scale", "expected"), [(1.0, 24), (1.5, 36), (2.0, 48)])
def test_dpi_probe_uses_stable_ceil_rounding(scale: float, expected: int) -> None:
    assert scaled_pixels(24, scale) == expected
