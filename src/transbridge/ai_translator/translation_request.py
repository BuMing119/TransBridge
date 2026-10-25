"""Prepare translation retrieval separately from final request admission."""

from __future__ import annotations

from transbridge.application.translation.entry_alias import ai_entry_key
from transbridge.infra.prepared_llm_request import PreparedLlmRequest


def translation_request(
    *,
    manager,
    builder,
    entries,
    batch_type,
    config,
    shared_terms,
    terms_lock,
    required_terms,
    effective_required,
    log,
    stream=None,
    prepared=lambda: None,
):
    """Retrieve once, then merge only fresh generated terms before sending.

    Published and plugin-scoped mappings remain authoritative. The finalizer
    does not repeat semantic retrieval while holding a model request slot.
    """

    def prepare():
        with terms_lock:
            current = {} if getattr(manager, "_shared_term_pool", None) is not None else dict(shared_terms)
        return manager.match_terms_scoped(
            entries=entries,
            enable_semantic=getattr(config, "enable_semantic_match", True),
            max_terms=getattr(config, "max_terms_per_batch", 200),
            in_flight_terms=current,
        )

    def finalize(matches):
        terms_by_entry = {
            ai_entry_key(entry): dict(matches.terms_by_entry.get(ai_entry_key(entry), {})) for entry in entries
        }
        pool = getattr(manager, "_shared_term_pool", None)
        snapshots = {}
        with terms_lock:
            current = {} if pool is not None else dict(shared_terms)
        for entry in entries:
            terms = terms_by_entry[ai_entry_key(entry)]
            if getattr(config, "retrieval_enabled", True):
                for term, target in current.items():
                    if term.casefold() in entry.original.casefold():
                        terms.setdefault(term, target)
                if pool is not None:
                    from .shared_term_lookup import shared_terms_for_entry

                    context = manager.lookup_context_for_entry(entry)
                    plugin = None if context is None else context.plugin_id
                    if plugin not in snapshots:
                        snapshots[plugin] = pool.snapshot(plugin)
                    terms.update(shared_terms_for_entry(manager, entry, snapshot=snapshots[plugin]))
            required = (required_terms or {}).get(ai_entry_key(entry), {})
            if required:
                context_resolver = getattr(manager, "lookup_context_for_entry", None)
                context = context_resolver(entry) if callable(context_resolver) else None
                resolved = {}
                for term, previous in required.items():
                    canonical = (
                        manager.resolve_term(term, context) if context is not None else manager.resolve_term(term)
                    )
                    if canonical is None or not canonical.translation.strip():
                        log(f"  参考术语已不可用，略过：{term}")
                        continue
                    if canonical.translation != previous:
                        log(f"  参考术语已刷新：{term}→{previous} 改为 {canonical.term}→{canonical.translation}")
                    resolved[canonical.term] = canonical.translation
                effective_required[ai_entry_key(entry)] = resolved
                terms.update(resolved)
        flat = {term: target for terms in terms_by_entry.values() for term, target in terms.items()}
        messages = builder.build_translation_prompt(entries, flat, batch_type, terms_by_entry=terms_by_entry)
        prepared()
        if stream is not None:
            stream("\n[DEBUG] 匹配到的术语\n" + "\n".join(f"{term} → {target}" for term, target in flat.items()))
            stream("\n[REQUEST TO LLM]\n")
            for message in messages:
                stream(f"--- {message.get('role', '').upper()} ---\n{message.get('content', '')}\n")
            stream("[RESPONSE FROM LLM]\n")
        return messages

    return PreparedLlmRequest(prepare, finalize)
