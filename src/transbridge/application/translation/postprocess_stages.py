"""Candidate-only post-process stage adapters.

Each stage consumes and returns :class:`PostProcessCandidate` values with an
explicit phase name and typed diagnostics.  The legacy GUI checker classes are
wrapped through a minimal read-only entry view (explicit input fields), while
refine/polish/arbitrate are driven through a small transport-neutral LLM port
so controlled HTTP servers can prove the chain without a GUI.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any, Protocol
from urllib import error, request

from transbridge.ai_translator.structured_schemas import POSTPROCESS_VALUES_OUTPUT_SCHEMA
from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, ErrorCategory
from transbridge.application.io import EntryKey
from transbridge.application.translation.token_batching import StableContentBatcher
from transbridge.infra.llm_structured_outputs import (
    LlmStructuredOutputError,
    attach_structured_output_directive,
    ensure_openai_structured_output_completion,
    openai_response_format,
    validate_structured_output,
)
from transbridge.infra.token_counting import TiktokenContentTokenCounter

from .postprocess import PostProcessCandidate, PostProcessStageOutcome
from .workload_models import canonical_hash


class PostProcessLlmPhase(StrEnum):
    REFINE = "refine"
    POLISH = "polish"
    ARBITRATE = "arbitrate"


@dataclass(frozen=True, slots=True)
class PostProcessLlmRequest:
    phase: PostProcessLlmPhase
    run_id: str
    candidates: tuple[PostProcessCandidate, ...]
    target_locale: str = "zh_CN"
    game_profile: str = "skyrim_se"
    base_url: str = "http://127.0.0.1"
    model: str = "fixture-model"


@dataclass(frozen=True, slots=True)
class PostProcessLlmResponse:
    """Per-key result value; arbitrate responses carry the final verdict."""

    values: tuple[tuple[EntryKey, str], ...]
    response_sha256: str

    def __post_init__(self) -> None:
        keys = tuple(key for key, _ in self.values)
        if len(keys) != len(set(keys)):
            raise ValueError("post-process LLM response contains duplicate EntryKeys")
        if any(not isinstance(value, str) for _, value in self.values):
            raise ValueError("post-process LLM response values must be strings")
        if not _is_sha256(self.response_sha256):
            raise ValueError("post-process LLM response summary must be a SHA-256 digest")

    def by_key(self) -> dict[EntryKey, str]:
        return dict(self.values)


class PostProcessLlmError(RuntimeError):
    def __init__(self, code: str, safe_message: str, *, response_sha256: str | None = None) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.response_sha256 = response_sha256


class PostProcessLlmPort(Protocol):
    def apply(self, phase: PostProcessLlmPhase, request: PostProcessLlmRequest) -> PostProcessLlmResponse: ...


class OpenAiPostProcessHttpPort:
    """Transport-neutral controlled-LLM adapter for the post-process chain."""

    def __init__(
        self,
        *,
        credential: Any = None,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        if timeout_seconds <= 0 or max_response_bytes < 1024:
            raise ValueError("HTTP post-process timeout and response limit must be positive")
        self._credential = credential or (lambda: None)
        self._timeout = timeout_seconds
        self._max_response_bytes = max_response_bytes

    def apply(self, phase: PostProcessLlmPhase, req: PostProcessLlmRequest) -> PostProcessLlmResponse:
        payload = _payload(phase, req)
        payload["response_format"] = openai_response_format(POSTPROCESS_VALUES_OUTPUT_SCHEMA)
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Idempotency-Key": canonical_hash({
                "phase": phase.value,
                "run_id": req.run_id,
                "keys": [key.to_dict() for key in _keys(req)],
            }),
        }
        credential = self._credential()
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        http_request = request.Request(_endpoint(req), data=body, headers=headers, method="POST")
        try:
            with request.urlopen(http_request, timeout=self._timeout) as response:
                raw = response.read(self._max_response_bytes + 1)
        except error.HTTPError as exc:
            raw = exc.read(self._max_response_bytes + 1)
            raise PostProcessLlmError(
                f"POSTPROCESS_HTTP_{exc.code}",
                "The post-process service rejected the request.",
                response_sha256=hashlib.sha256(raw).hexdigest(),
            ) from None
        except (error.URLError, TimeoutError):
            raise PostProcessLlmError(
                "POSTPROCESS_TRANSPORT_UNAVAILABLE",
                "The post-process service is unavailable.",
            ) from None
        if len(raw) > self._max_response_bytes:
            raise PostProcessLlmError(
                "POSTPROCESS_RESPONSE_TOO_LARGE",
                "The post-process response exceeded the configured size limit.",
                response_sha256=hashlib.sha256(raw).hexdigest(),
            )
        response_sha256 = hashlib.sha256(raw).hexdigest()
        try:
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict) and "choices" in payload:
                choice = payload["choices"][0]
                message = choice["message"]
                ensure_openai_structured_output_completion(
                    finish_reason=choice.get("finish_reason"),
                    refusal=message.get("refusal"),
                )
            content = _response_content(payload)
            validate_structured_output(
                json.dumps(content, ensure_ascii=False, separators=(",", ":")),
                POSTPROCESS_VALUES_OUTPUT_SCHEMA,
            )
            values = _values(content)
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            LlmStructuredOutputError,
        ):
            raise PostProcessLlmError(
                "POSTPROCESS_RESPONSE_MALFORMED",
                "The post-process service returned malformed JSON.",
                response_sha256=response_sha256,
            ) from None
        return PostProcessLlmResponse(values, response_sha256)


class LlmClientPostProcessPort:
    """Run post-process requests through the configured, run-limited LLM client."""

    def __init__(self, client: Any, *, max_output_tokens: int = 4000) -> None:
        self._client = client
        self._max_output_tokens = max_output_tokens

    def apply(self, phase: PostProcessLlmPhase, req: PostProcessLlmRequest) -> PostProcessLlmResponse:
        messages = _payload(phase, req)["messages"]
        messages[-1] = attach_structured_output_directive(messages[-1], POSTPROCESS_VALUES_OUTPUT_SCHEMA)
        try:
            content = self._client.chat(messages, max_tokens=self._max_output_tokens)
        except Exception as exc:
            raise PostProcessLlmError(
                "POSTPROCESS_LLM_CALL_FAILED",
                f"The post-process model call failed: {type(exc).__name__}",
            ) from exc
        encoded = str(content).encode("utf-8")
        response_sha256 = hashlib.sha256(encoded).hexdigest()
        try:
            values = _values(json.loads(content))
        except (TypeError, ValueError, json.JSONDecodeError):
            raise PostProcessLlmError(
                "POSTPROCESS_RESPONSE_MALFORMED",
                "The post-process model returned malformed JSON.",
                response_sha256=response_sha256,
            ) from None
        return PostProcessLlmResponse(values, response_sha256)


def _keys(request: PostProcessLlmRequest) -> tuple[EntryKey, ...]:
    return tuple(candidate.entry_key for candidate in request.candidates)


def _endpoint(request: PostProcessLlmRequest) -> str:
    return request.base_url.rstrip("/") + "/chat/completions"


def _payload(phase: PostProcessLlmPhase, request: PostProcessLlmRequest) -> dict[str, Any]:
    entries = [
        {
            "entry_key": candidate.entry_key.to_dict(),
            "original": candidate.original,
            "current": candidate.text,
            "stage": candidate.stage,
        }
        for candidate in request.candidates
    ]
    verb = {"refine": "refine", "polish": "polish", "arbitrate": "arbitrate"}[phase.value]
    instruction = (
        'Return JSON only as {"results":[{"entry_key":{"namespace":"...","local_key":"..."},'
        '"value":"..."}]}. Preserve every entry_key exactly and return each entry once.'
    )
    return {
        "model": request.model,
        "messages": [
            {
                "role": "system",
                "content": f"You perform {verb} toward {request.target_locale}. {instruction}",
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"phase": phase.value, "run_id": request.run_id, "entries": entries},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ],
    }


def _response_content(payload: Any) -> Any:
    if isinstance(payload, dict) and "results" in payload:
        return payload
    if not isinstance(payload, dict):
        raise TypeError("response root must be an object")
    choices = payload["choices"]
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise TypeError("response choices are invalid")
    message = choices[0]["message"]
    if not isinstance(message, dict):
        raise TypeError("response message is invalid")
    content = message["content"]
    if isinstance(content, str):
        return json.loads(content)
    return content


def _values(payload: Any) -> tuple[tuple[EntryKey, str], ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise TypeError("results must be an array")
    result = []
    for item in payload["results"]:
        if not isinstance(item, dict) or not isinstance(item.get("entry_key"), dict):
            raise TypeError("result item is invalid")
        value = item.get("value")
        if not isinstance(value, str):
            raise ValueError("result value must be a string")
        result.append((EntryKey.from_dict(item["entry_key"]), value))
    return tuple(result)


class CheckerStage:
    """Candidate DTO adapter around one legacy BaseChecker."""

    def __init__(
        self,
        phase: str,
        checker: Any,
        *,
        model: str = "",
        max_tokens_per_batch: int | None = None,
    ) -> None:
        self.phase = phase
        self._checker = checker
        self._batcher = (
            StableContentBatcher(TiktokenContentTokenCounter(model), max_tokens_per_batch, max_items=1)
            if max_tokens_per_batch is not None
            else None
        )

    def __call__(self, candidates: tuple[PostProcessCandidate, ...]) -> PostProcessStageOutcome:
        diagnostics: list[Diagnostic] = []
        oversized_keys: set[EntryKey] = set()
        if self._batcher is not None:
            plan = self._batcher.plan(
                candidates,
                key=lambda candidate: candidate.entry_key,
                content=lambda candidate: (candidate.original, candidate.text, candidate.context),
            )
            oversized_keys = {item.entry_key for item in plan.oversized}
            diagnostics.extend(
                Diagnostic(
                    "POSTPROCESS_CONTENT_TOKEN_LIMIT",
                    item.message,
                    category=ErrorCategory.INPUT,
                    severity=DiagnosticSeverity.ERROR,
                    details=(("entry_key", item.entry_key.to_dict()),),
                )
                for item in plan.oversized
            )
        for candidate in candidates:
            if candidate.entry_key in oversized_keys:
                continue
            view = _EntryView(candidate)
            try:
                found = self._checker.check(view)
            except Exception as exc:  # legacy checkers must never escape
                diagnostics.append(
                    Diagnostic(
                        "POSTPROCESS_CHECK_FAILED",
                        "A post-process checker failed.",
                        category=ErrorCategory.INTERNAL,
                        severity=DiagnosticSeverity.ERROR,
                        details=(("error_type", type(exc).__name__),),
                    )
                )
                continue
            for issue in found:
                diagnostics.append(
                    Diagnostic(
                        issue.issue_type.upper() or "POSTPROCESS_ISSUE",
                        issue.message,
                        category=ErrorCategory.INPUT,
                        severity=(
                            DiagnosticSeverity.ERROR if issue.severity == "error" else DiagnosticSeverity.WARNING
                        ),
                        details=(("entry_key", candidate.entry_key.to_dict()),),
                    )
                )
        return PostProcessStageOutcome(self.phase, candidates, tuple(diagnostics))


class LlmPostProcessStage:
    """Candidate-only refine/polish/arbitrate adapter over a PostProcessLlmPort."""

    def __init__(
        self,
        phase: PostProcessLlmPhase,
        port: PostProcessLlmPort,
        *,
        target_locale: str = "zh_CN",
        game_profile: str = "skyrim_se",
        base_url: str = "http://127.0.0.1",
        model: str = "fixture-model",
        max_tokens_per_batch: int | None = None,
        max_items: int | None = None,
    ) -> None:
        self.phase = phase.value
        self._phase = phase
        self._port = port
        self._target_locale = target_locale
        self._game_profile = game_profile
        self._base_url = base_url
        self._model = model
        self._batcher = (
            StableContentBatcher(TiktokenContentTokenCounter(model), max_tokens_per_batch, max_items=max_items)
            if max_tokens_per_batch is not None
            else None
        )

    def __call__(self, candidates: tuple[PostProcessCandidate, ...]) -> PostProcessStageOutcome:
        if not candidates:
            return PostProcessStageOutcome(self.phase, candidates)
        if self._batcher is None:
            return self._apply_batch(candidates)
        plan = self._batcher.plan(
            candidates,
            key=lambda candidate: candidate.entry_key,
            content=lambda candidate: (candidate.original, candidate.text, candidate.context),
        )
        diagnostics = [
            Diagnostic(
                "POSTPROCESS_CONTENT_TOKEN_LIMIT",
                item.message,
                category=ErrorCategory.INPUT,
                severity=DiagnosticSeverity.ERROR,
                details=(("entry_key", item.entry_key.to_dict()),),
            )
            for item in plan.oversized
        ]
        updated: list[PostProcessCandidate] = []
        for batch in plan.batches:
            outcome = self._apply_batch(batch.items)
            updated.extend(outcome.candidates)
            diagnostics.extend(outcome.diagnostics)
        return PostProcessStageOutcome(self.phase, tuple(updated), tuple(diagnostics))

    def _apply_batch(self, candidates: tuple[PostProcessCandidate, ...]) -> PostProcessStageOutcome:
        request = PostProcessLlmRequest(
            self._phase,
            candidates[0].run_id,
            candidates,
            target_locale=self._target_locale,
            game_profile=self._game_profile,
            base_url=self._base_url,
            model=self._model,
        )
        try:
            response = self._port.apply(self._phase, request)
        except PostProcessLlmError as exc:
            diagnostic = Diagnostic(
                exc.code,
                exc.safe_message,
                category=ErrorCategory.EXTERNAL,
                severity=DiagnosticSeverity.ERROR,
            )
            return PostProcessStageOutcome(self.phase, candidates, (diagnostic,))
        by_key = response.by_key()
        updated: list[PostProcessCandidate] = []
        diagnostics: list[Diagnostic] = []
        for candidate in candidates:
            value = by_key.get(candidate.entry_key)
            if value is None:
                diagnostics.append(
                    Diagnostic(
                        "POSTPROCESS_RESPONSE_MISSING_KEY",
                        "The post-process service omitted a requested entry.",
                        category=ErrorCategory.EXTERNAL,
                        details=(("entry_key", candidate.entry_key.to_dict()),),
                    )
                )
                continue
            if not value:
                diagnostics.append(
                    Diagnostic(
                        "POSTPROCESS_EMPTY_VALUE",
                        "The post-process service returned an empty value.",
                        category=ErrorCategory.EXTERNAL,
                        details=(("entry_key", candidate.entry_key.to_dict()),),
                    )
                )
                continue
            updated.append(self._apply_value(candidate, value, diagnostics))
        return PostProcessStageOutcome(self.phase, tuple(updated), tuple(diagnostics))

    def _apply_value(
        self,
        candidate: PostProcessCandidate,
        value: str,
        diagnostics: list[Diagnostic],
    ) -> PostProcessCandidate:
        if self._phase is PostProcessLlmPhase.ARBITRATE:
            if value in ("pass", "reject", "pending"):
                if value != "pass":
                    diagnostics.append(
                        Diagnostic(
                            f"POSTPROCESS_{value.upper()}",
                            "An entry did not pass arbitration.",
                            category=ErrorCategory.INPUT,
                            severity=DiagnosticSeverity.WARNING,
                            details=(("entry_key", candidate.entry_key.to_dict()),),
                        )
                    )
                return candidate.with_accepted(value == "pass")
            diagnostics.append(
                Diagnostic(
                    "POSTPROCESS_VERDICT_INVALID",
                    "The post-process service returned an invalid verdict.",
                    category=ErrorCategory.EXTERNAL,
                    details=(("entry_key", candidate.entry_key.to_dict()),),
                )
            )
            return candidate
        return candidate.with_text(value, self.phase)


def build_http_postprocess_stages(
    port: PostProcessLlmPort,
    *,
    target_locale: str = "zh_CN",
    game_profile: str = "skyrim_se",
    base_url: str = "http://127.0.0.1",
    model: str = "fixture-model",
) -> tuple[LlmPostProcessStage, LlmPostProcessStage, LlmPostProcessStage]:
    """Refine -> polish -> arbitrate chain over one controlled LLM port."""
    return (
        LlmPostProcessStage(
            PostProcessLlmPhase.REFINE,
            port,
            target_locale=target_locale,
            game_profile=game_profile,
            base_url=base_url,
            model=model,
        ),
        LlmPostProcessStage(
            PostProcessLlmPhase.POLISH,
            port,
            target_locale=target_locale,
            game_profile=game_profile,
            base_url=base_url,
            model=model,
        ),
        LlmPostProcessStage(
            PostProcessLlmPhase.ARBITRATE,
            port,
            target_locale=target_locale,
            game_profile=game_profile,
            base_url=base_url,
            model=model,
        ),
    )


class _EntryView:
    """Minimal read-only view providing the explicit fields legacy checkers read."""

    def __init__(self, candidate: PostProcessCandidate) -> None:
        self.entry_id = candidate.entry_key.local_key
        self.key = candidate.entry_key
        self.original = candidate.original
        self.translation = candidate.text
        self.context = candidate.context
        self.stage = candidate.stage


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
