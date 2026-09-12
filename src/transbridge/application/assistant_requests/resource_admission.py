"""Shared resource exclusion for writes across requests, sessions and views."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock

from .models import RequestError


class ResourceAdmission:
    """Application-scoped lock table; keys describe actual resources, never session IDs."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._owners: dict[str, str] = {}

    def acquire(self, effect_id: str, resource_keys: tuple[str, ...]) -> bool:
        if not effect_id or not resource_keys or any(not key.strip() for key in resource_keys):
            raise RequestError("REQUEST_PROTOCOL_INVALID", "write requires explicit resources or a coarse fallback key")
        with self._lock:
            if any(key in self._owners and self._owners[key] != effect_id for key in resource_keys):
                return False
            for key in resource_keys:
                self._owners[key] = effect_id
            return True

    def release(self, effect_id: str) -> None:
        with self._lock:
            self._owners = {key: owner for key, owner in self._owners.items() if owner != effect_id}

    @contextmanager
    def hold(self, effect_id: str, resource_keys: tuple[str, ...]) -> Iterator[None]:
        if not self.acquire(effect_id, resource_keys):
            raise RequestError("RESOURCE_BUSY", "another admitted operation owns the target resource")
        try:
            yield
        finally:
            self.release(effect_id)
