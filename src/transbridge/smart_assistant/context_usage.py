"""Keep billing delivery independent of whether a business response is still admitted."""


def connect_usage(worker, collector, bridge):
    capture = getattr(collector, "capture_usage_callback", None)
    if capture is not None:
        # The collector owns diagnostics, not the destroyed view or the old turn.
        worker.on_usage = capture(notify=bridge._dispatch.emit)
    else:
        worker.on_token_usage = lambda model, i, o: bridge._dispatch.emit(lambda: collector.on_llm_tokens(model, i, o))
