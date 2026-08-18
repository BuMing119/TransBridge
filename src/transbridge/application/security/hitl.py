"""One-use, request-bound human confirmation authority."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import hmac
import secrets
import threading
import time


@dataclass(frozen=True, slots=True)
class ConfirmationToken:
    token_id: str
    owner_id: str
    request_hash: str
    expires_at: float
    signature: str


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    code: str
    reason: str


class ConfirmationAuthority:
    """Issues opaque one-use confirmation tokens and consumes them atomically."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        secret: bytes | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._secret = secret or secrets.token_bytes(32)
        self._issued: dict[str, ConfirmationToken] = {}
        self._lock = threading.Lock()

    def _signature(
        self, token_id: str, owner_id: str, request_hash: str, expires_at: float
    ) -> str:
        payload = f"{token_id}\0{owner_id}\0{request_hash}\0{expires_at!r}".encode()
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def issue(self, *, owner_id: str, request_hash: str) -> ConfirmationToken:
        token_id = secrets.token_urlsafe(24)
        expires_at = self._clock() + self.ttl_seconds
        token = ConfirmationToken(
            token_id=token_id,
            owner_id=owner_id,
            request_hash=request_hash,
            expires_at=expires_at,
            signature=self._signature(token_id, owner_id, request_hash, expires_at),
        )
        with self._lock:
            self._issued[token_id] = token
        return token

    def consume(
        self,
        token: ConfirmationToken | None,
        *,
        owner_id: str,
        request_hash: str,
    ) -> AuthorizationDecision:
        if token is None:
            return AuthorizationDecision(False, "CONFIRMATION_REQUIRED", "缺少操作确认")
        expected = self._signature(
            token.token_id, token.owner_id, token.request_hash, token.expires_at
        )
        if not hmac.compare_digest(expected, token.signature):
            return AuthorizationDecision(False, "CONFIRMATION_INVALID", "确认凭据无效")
        with self._lock:
            issued = self._issued.pop(token.token_id, None)
        if issued is None:
            return AuthorizationDecision(False, "CONFIRMATION_REPLAYED", "确认凭据已使用或不存在")
        if self._clock() > token.expires_at:
            return AuthorizationDecision(False, "CONFIRMATION_EXPIRED", "操作确认已超时")
        if token.owner_id != owner_id:
            return AuthorizationDecision(False, "CONFIRMATION_OWNER_CHANGED", "确认所有者已变化")
        if token.request_hash != request_hash:
            return AuthorizationDecision(False, "CONFIRMATION_REQUEST_CHANGED", "操作请求已变化")
        return AuthorizationDecision(True, "CONFIRMED", "操作已确认")
