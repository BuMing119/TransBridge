"""Bounded ParaTranz project catalog cache with configuration isolation."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
import time

from transbridge.application.ports.paratranz import CancellationPort, ParaTranzProject
from transbridge.application.projects import normalize_paratranz_endpoint

from .service import ParaTranzService


@dataclass(frozen=True, slots=True)
class ParaTranzCatalogKey:
    endpoint: str
    account_user_id: int | None
    config_revision: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "endpoint", normalize_paratranz_endpoint(self.endpoint))
        if self.account_user_id is not None and (isinstance(self.account_user_id, bool) or self.account_user_id <= 0):
            raise ValueError("account_user_id must be a positive integer")
        if isinstance(self.config_revision, bool) or self.config_revision < 0:
            raise ValueError("config_revision must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ParaTranzCatalogSnapshot:
    key: ParaTranzCatalogKey
    projects: tuple[ParaTranzProject, ...]
    loaded_at: float


class ParaTranzProjectCatalog:
    """Qt-free directory query used by selectors and management views."""

    def __init__(self, *, ttl_seconds: float = 30.0, clock=time.monotonic) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must not be negative")
        self._ttl = ttl_seconds
        self._clock = clock
        self._cache: dict[ParaTranzCatalogKey, ParaTranzCatalogSnapshot] = {}
        self._lock = RLock()

    def list_my_projects(
        self,
        service: ParaTranzService,
        key: ParaTranzCatalogKey,
        *,
        cancellation: CancellationPort | None = None,
        refresh: bool = False,
    ) -> ParaTranzCatalogSnapshot:
        now = self._clock()
        with self._lock:
            cached = self._cache.get(key)
            if not refresh and cached is not None and now - cached.loaded_at <= self._ttl:
                return cached
        projects = service.list_projects(uid="my", cancellation=cancellation)
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        snapshot = ParaTranzCatalogSnapshot(key, projects, self._clock())
        with self._lock:
            self._cache = {key: snapshot}
        return snapshot

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


__all__ = [
    "ParaTranzCatalogKey",
    "ParaTranzCatalogSnapshot",
    "ParaTranzProjectCatalog",
]
