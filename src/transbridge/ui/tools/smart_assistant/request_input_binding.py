"""Admission callbacks and unsent text belong to the originating chat view."""

from copy import deepcopy
from dataclasses import dataclass
import logging
from uuid import uuid4

from PyQt6.QtWidgets import QPushButton

from transbridge.application.assistant_requests.attachments import display_user_content

from .message_bubble import MessageBubble
from .submission_scope import scope_from_input

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FailedInput:
    text: str
    error: str
    context: object
    command_id: str
    selection: dict
    attachments: object = None
    skill_context: object = None


class RequestInputBinding:
    def __init__(self, binding):
        self.binding = binding
        self.generation = 0
        self.failures = {}
        self._presented = set()
        self._pending = set()
        self.clearing = False

    def invalidate(self):
        self.generation += 1
        self.clearing = False
        self._presented.clear()

    def submit(self, text, *, attachments=None, skill_context=None, _recovery=None):
        binding, facade = self.binding, self.binding.facade
        if self.clearing:
            facade.add_system_message("正在清空会话，完成后可继续发送。")
            return False
        if binding._closed or not text.strip():
            return False
        context = facade._session_runtime.request_context()
        if _recovery is not None and _recovery.context != context:
            facade.add_system_message("请先切回这条输入原来的会话和项目，再重试提交。")
            return False
        if not context.session_id:
            facade.add_system_message("请先创建或选择一个会话。")
            return False
        binding.context = context
        binding._user_stopped = False
        binding.interrupt()
        selection = deepcopy(_recovery.selection) if _recovery else binding._selection()
        generation = self.generation
        command_id = _recovery.command_id if _recovery else uuid4().hex
        if command_id in self._pending:
            return False
        self._pending.add(command_id)
        material = deepcopy(_recovery.attachments if _recovery else attachments)
        skill = deepcopy(_recovery.skill_context if _recovery else skill_context)
        binding._accepting += 1

        def accept():
            binding.service.scheduler.activate(context.session_id, binding.view_id)
            kwargs = {"selection": selection, "attachments": material, "command_id": command_id}
            if skill is not None:
                kwargs["skill_context"] = skill
            return binding.service.accept_input(context, text.strip(), **kwargs)

        future = binding._queue.submit(accept)

        def display():
            binding._accepting -= 1
            self._pending.discard(command_id)
            failures = self.failures.setdefault(context.session_id, [])
            failures[:] = [value for value in failures if value.command_id != command_id]
            current = not binding._closed and binding.context == context and self.generation == generation
            try:
                accepted = future.result()
            except Exception as exc:
                logger.warning("Input admission failed for session %s", context.session_id, exc_info=True)
                if not binding._closed:
                    failure = FailedInput(text, str(exc), context, command_id, selection, material, skill)
                    failures.append(failure)
                    if current:
                        self.present_failures()
                return
            if not current:
                return
            already_restored = any(
                message.get("message_id") == accepted["message_id"] for message in facade._conversation.get_transcript()
            )
            if not already_restored:
                facade._conversation.add_user(accepted.get("content", text.strip()), message_id=accepted["message_id"])
                bubble = MessageBubble(
                    display_user_content(accepted.get("content", text.strip())), "user", theme=facade._theme
                )
                bubble.setProperty("message_id", accepted["message_id"])
                bubble.set_scope(scope_from_input(accepted))
                facade._message_list.add_bubble(bubble)
            binding.wake()

        def ready(_):
            try:
                binding.delivered.emit(display)
            except RuntimeError:
                logger.info("Input admission finished after the chat view was destroyed")

        future.add_done_callback(ready)
        return True

    def present_failures(self):
        binding, facade = self.binding, self.binding.facade
        session_id = binding.context.session_id
        for failure in self.failures.get(session_id, ()):
            if id(failure) in self._presented:
                continue
            self._presented.add(id(failure))
            facade.add_system_message(f"输入未能接纳：{failure.error}。原文仍可恢复到输入框。")
            retry = QPushButton("重试提交")
            retry.setToolTip("使用原来的材料、技能和提交范围重试，不重复接纳同一条输入。")
            retry_generation = self.generation

            def resubmit(_checked=False, value=failure, epoch=retry_generation, action=retry):
                if self.generation != epoch or binding._closed:
                    return
                if self.submit(value.text, _recovery=value):
                    action.setEnabled(False)

            retry.clicked.connect(resubmit)
            facade._message_list.add_widget(retry)
            button = QPushButton("恢复未发送内容")
            button.setToolTip(failure.text)
            generation = self.generation

            def restore(_checked=False, value=failure, owner=session_id, epoch=generation, action=button):
                if binding._closed or binding.context.session_id != owner or self.generation != epoch:
                    return
                current = facade._input.toPlainText()
                facade.set_input(current + ("\n" if current else "") + value.text)
                values = self.failures.get(owner, [])
                if value in values:
                    values.remove(value)
                action.setEnabled(False)

            button.clicked.connect(restore)
            facade._message_list.add_widget(button)
        # Leave records until explicitly restored, so leaving and returning keeps recovery available.
