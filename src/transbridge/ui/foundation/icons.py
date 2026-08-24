"""GUI-thread-only, cost-aware icon and pixmap cache."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from threading import get_ident

from PyQt6.QtCore import QSize, QThread
from PyQt6.QtGui import QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication

from .qt_palette import qcolor
from .theme_service import ThemeSnapshot

DEFAULT_ICON_CACHE_BYTES = 8 * 1024 * 1024
IconRenderer = Callable[[str, QSize, float, str, ThemeSnapshot], QPixmap | None]


@dataclass(frozen=True, slots=True)
class IconCacheStats:
    entries: int
    cost_bytes: int
    max_cost_bytes: int
    hits: int
    misses: int
    evictions: int
    uncached: int


@dataclass(frozen=True, slots=True)
class _IconKey:
    fingerprint: str
    icon_id: str
    width: int
    height: int
    dpr: float
    state: str


class IconProvider:
    """Render icons lazily and retain pixmaps under an actual-byte LRU budget."""

    def __init__(
        self,
        renderer: IconRenderer | None = None,
        *,
        max_cost_bytes: int = DEFAULT_ICON_CACHE_BYTES,
        max_item_cost_bytes: int | None = None,
    ) -> None:
        if max_cost_bytes < 1:
            raise ValueError("max_cost_bytes must be positive")
        item_budget = max_cost_bytes if max_item_cost_bytes is None else max_item_cost_bytes
        if item_budget < 1:
            raise ValueError("max_item_cost_bytes must be positive")
        self._renderer = renderer
        self._max_cost_bytes = max_cost_bytes
        self._max_item_cost_bytes = min(item_budget, max_cost_bytes)
        self._owner_thread_id = get_ident()
        self._cache: OrderedDict[_IconKey, tuple[QPixmap, int]] = OrderedDict()
        self._cost_bytes = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._uncached = 0
        self._diagnostics: set[str] = set()
        self._require_gui_thread()

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(sorted(self._diagnostics))

    @property
    def stats(self) -> IconCacheStats:
        return IconCacheStats(
            entries=len(self._cache),
            cost_bytes=self._cost_bytes,
            max_cost_bytes=self._max_cost_bytes,
            hits=self._hits,
            misses=self._misses,
            evictions=self._evictions,
            uncached=self._uncached,
        )

    def icon(
        self,
        icon_id: str,
        size: int | QSize,
        dpr: float,
        state: str,
        snapshot: ThemeSnapshot,
    ) -> QIcon:
        return QIcon(self.pixmap(icon_id, size, dpr, state, snapshot))

    def pixmap(
        self,
        icon_id: str,
        size: int | QSize,
        dpr: float,
        state: str,
        snapshot: ThemeSnapshot,
    ) -> QPixmap:
        self._require_gui_thread()
        logical_size = _logical_size(size)
        rounded_dpr = round(float(dpr), 2)
        if rounded_dpr <= 0:
            raise ValueError("dpr must be positive")
        if not isinstance(icon_id, str) or not icon_id.strip():
            raise ValueError("icon_id must not be empty")
        key = _IconKey(
            snapshot.fingerprint,
            icon_id,
            logical_size.width(),
            logical_size.height(),
            rounded_dpr,
            str(state),
        )
        cached = self._cache.get(key)
        if cached is not None:
            self._hits += 1
            self._cache.move_to_end(key)
            return QPixmap(cached[0])

        self._misses += 1
        pixel_size = QSize(
            max(1, round(logical_size.width() * rounded_dpr)),
            max(1, round(logical_size.height() * rounded_dpr)),
        )
        pixmap = self._render(icon_id, pixel_size, rounded_dpr, str(state), snapshot)
        if pixmap.devicePixelRatio() != rounded_dpr:
            pixmap.setDevicePixelRatio(rounded_dpr)
        cost = _pixmap_cost(pixmap)
        if cost > self._max_item_cost_bytes or cost > self._max_cost_bytes:
            self._uncached += 1
            return pixmap
        while self._cache and self._cost_bytes + cost > self._max_cost_bytes:
            _old_key, (_old_pixmap, old_cost) = self._cache.popitem(last=False)
            self._cost_bytes -= old_cost
            self._evictions += 1
        self._cache[key] = (QPixmap(pixmap), cost)
        self._cost_bytes += cost
        return pixmap

    def clear(self) -> None:
        self._require_gui_thread()
        self._cache.clear()
        self._cost_bytes = 0

    def _render(
        self,
        icon_id: str,
        pixel_size: QSize,
        dpr: float,
        state: str,
        snapshot: ThemeSnapshot,
    ) -> QPixmap:
        pixmap = None if self._renderer is None else self._renderer(icon_id, pixel_size, dpr, state, snapshot)
        if pixmap is not None and not isinstance(pixmap, QPixmap):
            raise TypeError("icon renderer must return QPixmap or None")
        if pixmap is not None and not pixmap.isNull():
            return pixmap
        self._diagnostics.add(f"theme_icon_unknown:{icon_id}")
        return _missing_pixmap(pixel_size, snapshot)

    def _require_gui_thread(self) -> None:
        application = QApplication.instance()
        if application is None:
            raise RuntimeError("theme_application_missing")
        if get_ident() != self._owner_thread_id or QThread.currentThread() != application.thread():
            raise RuntimeError("theme_wrong_thread")


def _logical_size(size: int | QSize) -> QSize:
    if isinstance(size, int):
        result = QSize(size, size)
    elif isinstance(size, QSize):
        result = QSize(size)
    else:
        raise TypeError("icon size must be int or QSize")
    if result.width() < 1 or result.height() < 1:
        raise ValueError("icon size must be positive")
    return result


def _pixmap_cost(pixmap: QPixmap) -> int:
    return max(1, pixmap.width() * pixmap.height() * max(1, pixmap.depth()) // 8)


def _missing_pixmap(size: QSize, snapshot: ThemeSnapshot) -> QPixmap:
    pixmap = QPixmap(size)
    pixmap.fill(qcolor(snapshot.tokens.semantic.surface_alt))
    painter = QPainter(pixmap)
    try:
        pen = QPen(qcolor(snapshot.tokens.semantic.text_secondary))
        pen.setWidth(max(1, min(size.width(), size.height()) // 12))
        painter.setPen(pen)
        inset = max(1, min(size.width(), size.height()) // 5)
        painter.drawLine(inset, inset, size.width() - inset, size.height() - inset)
        painter.drawLine(size.width() - inset, inset, inset, size.height() - inset)
    finally:
        painter.end()
    return pixmap


__all__ = [
    "DEFAULT_ICON_CACHE_BYTES",
    "IconCacheStats",
    "IconProvider",
    "IconRenderer",
]
