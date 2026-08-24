from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.ui.foundation.tabler_icons import tabler_pixmap


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    "icon_id",
    (
        "layout-dashboard",
        "language",
        "plus",
        "list-details",
        "circle-dashed",
        "clock-hour-3",
        "circle-check",
        "settings",
        "help-circle",
        "info-circle",
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
