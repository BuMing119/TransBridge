"""Versioned ParaTranz transport keys, independent of local/plugin identities."""

import base64
import hashlib

from .identity import EntryKey, SourceNamespace

_PREFIX = "__transbridge_key__:"
_VERSION = _PREFIX + "v1:"


class ParaTranzKeyError(ValueError):
    """A transport key cannot prove its original local identity."""


def encode_key(identity: EntryKey, original: str) -> str:
    """Keep ordinary keys; qualify marked entries using exact UTF-8 originals."""
    key = identity.local_key
    if identity.original is not None:
        if identity.original != original:
            raise ParaTranzKeyError("词条原文与本地身份不一致。")
        return f"{_VERSION}o:{_pack(key)}:{_digest(original)}"
    if key.startswith(_PREFIX):
        return f"{_VERSION}k:{_pack(key)}"
    return key


def decode_key(key: str, original: str, namespace: SourceNamespace) -> EntryKey:
    """Restore keys without a mapping table; reject malformed/changed originals."""
    if not key.startswith(_PREFIX):
        return EntryKey(namespace, key)
    if not key.startswith(_VERSION):
        raise ParaTranzKeyError("不支持的 TransBridge 传输 key 版本。")
    parts = key[len(_VERSION) :].split(":")
    if len(parts) == 3 and parts[0] == "o":
        local_key = _unpack(parts[1])
        if parts[2] != _digest(original):
            raise ParaTranzKeyError("传输 key 的原文哈希不匹配，请检查云端原文是否被修改。")
        return EntryKey(namespace, local_key, original)
    if len(parts) == 2 and parts[0] == "k":
        local_key = _unpack(parts[1])
        if local_key.startswith(_PREFIX):
            return EntryKey(namespace, local_key)
    raise ParaTranzKeyError("TransBridge 传输 key 格式无效。")


def _pack(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _unpack(value: str) -> str:
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True).decode("utf-8")
    except ValueError as exc:
        raise ParaTranzKeyError("传输 key 中的原始 key 编码无效。") from exc
    if not decoded.strip() or _pack(decoded) != value:
        raise ParaTranzKeyError("传输 key 中的原始 key 编码无效。")
    return decoded


def _digest(original: str) -> str:
    return hashlib.sha256(original.encode("utf-8")).hexdigest()
