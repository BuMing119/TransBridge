from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QIcon, QPalette
from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from transbridge.ui.foundation.tabler_icons import tabler_icon, tabler_pixmap


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    "icon_id",
    (
        "layout-dashboard",
        "language",
        "plus",
        "minus",
        "list-details",
        "circle-dashed",
        "clock-hour-3",
        "circle-check",
        "settings",
        "help-circle",
        "info-circle",
        "home",
        "folder",
        "package",
        "arrow-left",
        "chevron-right",
        "alert-triangle",
    ),
)
def test_bundled_tabler_icon_renders_transparent_vector_pixmap(icon_id: str) -> None:
    pixmap = tabler_pixmap(icon_id, 24, QColor("#2563EB"), dpr=1.0)

    assert not pixmap.isNull()
    assert pixmap.size().width() == pixmap.size().height() == 24
    assert pixmap.toImage().pixelColor(0, 0).alpha() == 0


def test_unknown_tabler_icon_is_rejected() -> None:
    with pytest.raises(KeyError, match="unknown Tabler icon"):
        tabler_pixmap("not-an-icon", 24, QColor("#2563EB"))


@pytest.mark.parametrize("use_override", [False, True])
def test_icon_palette_override_preserves_default_widget_palette_behavior(use_override: bool) -> None:
    widget = QWidget()
    widget_palette = QPalette(widget.palette())
    widget_palette.setColor(QPalette.ColorRole.Text, QColor("#a02030"))
    widget_palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#718096"))
    widget.setPalette(widget_palette)
    override = QPalette(widget_palette)
    override.setColor(QPalette.ColorRole.Text, QColor("#128644"))
    override.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#98aabb"))
    expected = override if use_override else widget_palette

    icon = tabler_icon(widget, "folder", 24, **({"palette": override} if use_override else {}))

    for mode, group in (
        (QIcon.Mode.Normal, QPalette.ColorGroup.Active),
        (QIcon.Mode.Disabled, QPalette.ColorGroup.Disabled),
    ):
        image = icon.pixmap(24, 24, mode, QIcon.State.Off).toImage()
        colour = expected.color(group, QPalette.ColorRole.Text).name()
        matching = sum(
            image.pixelColor(x, y).alpha() > 240 and image.pixelColor(x, y).name() == colour
            for x in range(image.width())
            for y in range(image.height())
        )
        assert matching > 10
    assert widget.palette() == widget_palette
