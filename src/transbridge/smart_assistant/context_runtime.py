"""Background context lifecycle connecting request authority, artifacts and semantic summaries."""

from dataclasses import replace

from transbridge.application.assistant_context.admission import publish_context, require_admitted, save_wait
from transbridge.application.assistant_context.models import PreparationWait, validate_successor
from transbridge.application.assistant_context.projection import (
    append_context,
    authorized_records,
    related_history,
    source_digest,
)
from transbridge.application.assistant_requests.models import digest
from transbridge.application.assistant_requests.summary_service import RequestSummaryService
from transbridge.persistence.assistant_context_store import AssistantContextStore
from transbridge.persistence.v2.ids import SessionId, SessionRef


def configuration_digest(config):
    """Opaque stable configuration identity; never persist credentials or per-turn fields."""
    return digest({
        key: getattr(config, key, None)
        for key in (
            "provider",
            "base_url",
            "model",
            "api_key",
            "temperature",
            "assistant_context_window",
            "assistant_auto_compaction",
            "assistant_prompt_cache",
        )
    })


class ContextRuntime:
    def __init__(self, service, context, admission, *, client, config, on_usage=None, still_current=lambda: True):
        self.service, self.context, self.admission = service, context, admission
        self.client, self.config, self.on_usage, self.still_current = client, config, on_usage, still_current
        self.config_digest = configuration_digest(config)
        self.store = AssistantContextStore(service.transcript_store) if service.transcript_store else None
        self.sources = RequestSummaryService(service)

    def prepare(self, prepared):
        from transbridge.application.assistant_context.compaction import compact
        from transbridge.smart_assistant.context_summary import SemanticSummaryGenerator

        if self.store is None:
            raise PreparationWait("CONTEXT_STORE_UNAVAILABLE", "稳定上下文需要已保存会话的附件存储。")
        snapshot = self.service.lifecycle.read_session(SessionRef(SessionId(self.context.session_id)), self.context)
        state = snapshot.assistant_data()
        request = require_admitted(self.service, self.admission, state)
        from transbridge.application.assistant_context.state_projection import decision_context

        prepared = replace(prepared, request_state=decision_context(request, self.admission, state))
        history = self.sources._history(snapshot, state)
        owners = {**state.get("result_owners", {}), **state.get("message_owners", {})}
        old_head = state.get("context_heads", {}).get(request.request_id)
        previous = self.store.read(request.session_id, request.request_id, request.scope, old_head)
        summaries = ()
        if previous is None and request.request_id in state.get("request_summaries", {}):
            from transbridge.application.assistant_context.migration import import_legacy_summary

            summaries = (
                import_legacy_summary(state["request_summaries"][request.request_id], request, history, owners),
            )
        fingerprint = digest({
            "configuration": self.config_digest,
            "tools": [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema, "strict": t.strict}
                for t in prepared.tools
            ],
        })
        base = previous.epoch if previous else None
        pending_entry = state.get("context_pending", {}).get(request.request_id)
        if (
            pending_entry
            and pending_entry.get("request_revision") == request.revision
            and pending_entry.get("expected_head") == old_head
            and pending_entry.get("ownership_digest") == digest(owners)
        ):
            pending = self.store.read(request.session_id, request.request_id, request.scope, pending_entry["head"])
            if pending.epoch.config_digest == fingerprint:
                if base:
                    validate_successor(base, pending.epoch)
                base = pending.epoch
        try:
            history = related_history(history, request, self.service.requests(state), owners, base)
            candidate = append_context(
                history,
                request,
                prepared.request_state,
                config_digest=fingerprint,
                previous=base,
                owners=owners,
                summaries=summaries,
                verified_immutable=True,
            )
            summarizer = SemanticSummaryGenerator(
                self.client, prepared.budget, on_usage=self.on_usage, cancelled=lambda: not self.still_current()
            )
            candidate = compact(
                candidate,
                prepared.budget,
                prepared.tools,
                dict(prepared.request_state),
                summarizer,
                enabled=getattr(self.config, "assistant_auto_compaction", True),
            )
            # A background job may finish while semantic generation is in flight. Rebase only
            # appended evidence and the freshly read authoritative state; never rerun a model in CAS.
            refreshed = self.service.lifecycle.read_session(
                SessionRef(SessionId(self.context.session_id)), self.context
            )
            fresh_state = refreshed.assistant_data()
            request = require_admitted(self.service, self.admission, fresh_state)
            fresh_owners = {**fresh_state.get("result_owners", {}), **fresh_state.get("message_owners", {})}
            fresh_history = related_history(
                self.sources._history(refreshed, fresh_state),
                request,
                self.service.requests(fresh_state),
                fresh_owners,
                candidate,
            )
            candidate = append_context(
                fresh_history,
                request,
                decision_context(request, self.admission, fresh_state),
                config_digest=fingerprint,
                previous=candidate,
                owners=fresh_owners,
                verified_immutable=True,
            )
            if not prepared.budget.measure(candidate.messages, prepared.tools).fits:
                wait = PreparationWait("CONTEXT_SUMMARY_CAPACITY", "新增材料超出容量，已保留本次摘要进度。")
                wait.candidate_epoch = candidate
                raise wait
            if not self.still_current():
                raise PreparationWait("CONTEXT_PREPARATION_INTERRUPTED", "上下文配置或准备资格已变化。")
            staged = self.store.stage(candidate, previous)

            def validate_sources(current, current_state):
                current_request = require_admitted(self.service, self.admission, current_state)
                canonical = self.sources._history(current, current_state)
                current_owners = {**current_state.get("result_owners", {}), **current_state.get("message_owners", {})}
                canonical = related_history(
                    canonical, current_request, self.service.requests(current_state), current_owners, candidate
                )
                permitted = authorized_records(canonical, current_request, current_owners)
                known_sources = dict(candidate.source_digests)
                current_sources = {
                    r["message_id"]: (
                        known_sources[r["message_id"]] if r["message_id"] in known_sources else source_digest(r)
                    )
                    for r in permitted
                    if r["role"] != "system"
                }
                # Tail additions must be consumed by a fresh preparation, not lost from this dispatch.
                if current_sources != dict(candidate.source_digests):
                    raise PreparationWait("CONTEXT_SOURCE_CHANGED", "整理期间历史材料发生变化，请重新准备。")
                if digest(current_request.to_dict()) != digest(request.to_dict()):
                    raise PreparationWait("CONTEXT_STATE_CHANGED", "整理期间任务状态发生变化，请重新准备。")

            publish_context(
                self.service,
                self.context,
                self.admission,
                staged,
                old_head,
                still_current=self.still_current,
                validate_sources=validate_sources,
            )
            prepared.budget.require(candidate.messages, prepared.tools)
            from transbridge.infra.assistant_prompt_cache import decorate_assistant_messages

            namespace = digest({
                "session": request.session_id,
                "request": request.request_id,
                "configuration": fingerprint,
            })
            return decorate_assistant_messages(
                candidate.messages, namespace=namespace, enabled=getattr(self.config, "assistant_prompt_cache", True)
            ), prepared.tools
        except PreparationWait as exc:
            partial = getattr(exc, "candidate_epoch", None)
            pending = self.store.stage(partial, previous) if partial is not None else None
            save_wait(
                self.service, self.context, self.admission, exc, config_digest=self.config_digest, pending=pending
            )
            raise
