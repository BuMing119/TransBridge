"""Small OpenAI-compatible HTTP adapter for TranslationLlmPort."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from typing import Any
from urllib import error, request

from transbridge.application.io.identity import EntryKey
from transbridge.application.ports.paratranz import CancellationPort

from .workload_models import (
    TranslationBatchRequest,
    TranslationBatchResponse,
    TranslationServiceError,
)


class OpenAiTranslationHttpPort:
    """Credential-bound adapter; response bodies only leave as SHA-256 summaries."""

    def __init__(
        self,
        *,
        credential: Callable[[], str | None] | None = None,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        if timeout_seconds <= 0 or max_response_bytes < 1024:
            raise ValueError("HTTP translation timeout and response limit must be positive")
        self._credential = credential or (lambda: None)
        self._timeout = timeout_seconds
        self._max_response_bytes = max_response_bytes

    def translate(
        self,
        batch: TranslationBatchRequest,
        *,
        cancellation: CancellationPort | None = None,
    ) -> TranslationBatchResponse:
        if cancellation is not None and cancellation.is_cancelled:
            raise TranslationServiceError("TRANSLATION_CANCELLED", "The translation request was cancelled.")
        body = json.dumps(
            _request_payload(batch),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Idempotency-Key": batch.batch_id,
        }
        credential = self._credential()
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        endpoint = batch.run_spec.base_url.rstrip("/") + "/chat/completions"
        http_request = request.Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(http_request, timeout=self._timeout) as response:
                raw = response.read(self._max_response_bytes + 1)
        except error.HTTPError as exc:
            raw = exc.read(self._max_response_bytes + 1)
            response_hash = hashlib.sha256(raw).hexdigest()
            retry_after = _retry_after(exc.headers.get("Retry-After"))
            raise TranslationServiceError(
                f"TRANSLATION_HTTP_{exc.code}",
                "The translation service rejected the batch.",
                retryable=exc.code == 429 or 500 <= exc.code <= 599,
                retry_after=retry_after,
                response_sha256=response_hash,
            ) from None
        except (error.URLError, TimeoutError):
            raise TranslationServiceError(
                "TRANSLATION_TRANSPORT_UNAVAILABLE",
                "The translation service is unavailable.",
                retryable=True,
            ) from None
        if len(raw) > self._max_response_bytes:
            raise TranslationServiceError(
                "TRANSLATION_RESPONSE_TOO_LARGE",
                "The translation response exceeded the configured size limit.",
                response_sha256=hashlib.sha256(raw).hexdigest(),
            )
        response_hash = hashlib.sha256(raw).hexdigest()
        try:
            payload = json.loads(raw.decode("utf-8"))
            content = _response_content(payload)
            decoded = json.loads(content) if isinstance(content, str) else content
            translations = _translations(decoded)
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise TranslationServiceError(
                "TRANSLATION_RESPONSE_MALFORMED",
                "The translation service returned malformed JSON.",
                response_sha256=response_hash,
            ) from None
        return TranslationBatchResponse(translations, response_hash)


def _request_payload(batch: TranslationBatchRequest) -> dict[str, Any]:
    parameters = {key: json.loads(value) for key, value in batch.run_spec.parameters}
    reserved = {"model", "messages"}.intersection(parameters)
    if reserved:
        names = ", ".join(sorted(reserved))
        raise TranslationServiceError(
            "TRANSLATION_PARAMETERS_INVALID",
            f"Run parameters cannot override reserved request fields: {names}.",
        )
    entries = [
        {
            "entry_key": entry.entry_key.to_dict(),
            "original": entry.original,
            "translation": entry.translation,
            "context": entry.context,
        }
        for entry in batch.entries
    ]
    instruction = (
        'Return JSON only as {"translations":[{"entry_key":'
        '{"namespace":"...","local_key":"..."},"text":"..."}]}. '
        "Preserve every entry_key exactly and return each requested entry once."
    )
    payload = {
        "model": batch.run_spec.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You perform {batch.action.value} from {batch.run_spec.source_locale} "
                    f"to {batch.run_spec.target_locale}. {instruction}"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "batch_id": batch.batch_id,
                        "category": batch.category,
                        "quest_id": batch.quest_id,
                        "entries": entries,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ],
    }
    payload.update(parameters)
    return payload


def _response_content(payload: Any) -> Any:
    if isinstance(payload, dict) and "translations" in payload:
        return payload
    if not isinstance(payload, dict):
        raise TypeError("response root must be an object")
    choices = payload["choices"]
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise TypeError("response choices are invalid")
    message = choices[0]["message"]
    if not isinstance(message, dict):
        raise TypeError("response message is invalid")
    return message["content"]


def _translations(payload: Any) -> tuple[tuple[EntryKey, str], ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("translations"), list):
        raise TypeError("translations must be an array")
    result = []
    for item in payload["translations"]:
        if not isinstance(item, dict) or not isinstance(item.get("entry_key"), dict):
            raise TypeError("translation item is invalid")
        text = item.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("translation text must be non-empty")
        result.append((EntryKey.from_dict(item["entry_key"]), text))
    return tuple(result)


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None
