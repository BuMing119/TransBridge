"""Worker-owned preview reads fenced by the original confirmation generation."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import logging
from weakref import ref

from PyQt6 import sip

from transbridge.smart_assistant.operation_preview import build_operation_previews

logger = logging.getLogger(__name__)


class ConfirmationPreviewBinding:
    def __init__(self, view, context_provider):
        self.view = view
        self.context_provider = context_provider
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="confirmation-preview")

    def load(self, card, steps):
        if self.context_provider is None:
            return
        generation, weak_card = self.view._generation, ref(card)
        card._preview.set_loading()
        card._exec_btn.setEnabled(False)
        try:
            context = self.context_provider()
        except Exception as exc:
            logger.warning("Unable to capture operation preview context", exc_info=True)
            card._preview.set_unavailable(str(exc))
            card._exec_btn.setEnabled(True)
            return
        future = self._worker.submit(build_operation_previews, deepcopy(steps), context)

        def received():
            current = weak_card()
            if (
                self.view._closed
                or self.view._generation != generation
                or current is None
                or sip.isdeleted(current)
                or not current._pending
            ):
                return
            try:
                current.set_change_previews(future.result())
            except Exception as exc:
                logger.warning("Unable to project operation preview", exc_info=True)
                current._preview.set_unavailable(str(exc))
            current._exec_btn.setEnabled(True)

        future.add_done_callback(lambda _: self.view.dispatch(received))

    def close(self):
        self._worker.shutdown(wait=False, cancel_futures=True)
