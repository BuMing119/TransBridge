# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Worker-owned preview reads fenced by the original confirmation generation."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import logging
from weakref import WeakKeyDictionary, ref

from PyQt6 import sip

from transbridge.smart_assistant.entry_edit_review import EDIT_TOOLS
from transbridge.smart_assistant.operation_preview import build_operation_previews

logger = logging.getLogger(__name__)


class ConfirmationPreviewBinding:
    def __init__(self, view, context_provider):
        self.view = view
        self.context_provider = context_provider
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="confirmation-preview")
        self._edit_reviews = WeakKeyDictionary()

    def load(self, card, steps):
        if self.context_provider is None:
            return
        generation, weak_card = self.view._generation, ref(card)
        needs_review = any(step.get("tool") in EDIT_TOOLS for step in steps)
        if needs_review:
            self._edit_reviews[card] = None
        card._preview.set_loading()
        card._exec_btn.setEnabled(False)
        try:
            context = self.context_provider()
        except Exception as exc:
            logger.warning("Unable to capture operation preview context", exc_info=True)
            card._preview.set_unavailable(str(exc))
            card._exec_btn.setEnabled(not needs_review)
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
                previews = future.result()
                current.set_change_previews(previews)
                if needs_review and all(p.edit_review is not None for p in previews if p.tool in EDIT_TOOLS):
                    self._edit_reviews[current] = tuple(
                        (step, preview.edit_review)
                        for step, preview in zip(steps, previews, strict=True)
                        if preview.edit_review is not None
                    )
            except Exception as exc:
                logger.warning("Unable to project operation preview", exc_info=True)
                current._preview.set_unavailable(str(exc))
            current._exec_btn.setEnabled(self.ready(current))

        future.add_done_callback(lambda _: self.view.dispatch(received))

    def ready(self, card):
        return self._edit_reviews.get(card, ()) is not None

    def review_for(self, step, *, required):
        if step.get("tool") not in EDIT_TOOLS or self.context_provider is None:
            return None
        for card in self.view._active_cards:
            for original, review in self._edit_reviews.get(card) or ():
                if original is step:
                    return review
        if required or any(card in self._edit_reviews for card in self.view._active_cards):
            raise ValueError("未找到本步骤已确认的词条修改，请重新查看确认卡。")
        return None

    def close(self):
        self._worker.shutdown(wait=False, cancel_futures=True)
