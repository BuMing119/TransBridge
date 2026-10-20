"""Conversation navigation, persistence and read-only previews for the assistant panel."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import logging

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QInputDialog

from transbridge.persistence.v2 import SessionId, SessionRef

from .session_directory import SessionDirectory

logger = logging.getLogger(__name__)


class PanelSessions(QObject):
    delivered = pyqtSignal(object)

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.values = {}
        self.preview = False
        self.loading = False
        self.generation = 0
        self._accept_projection = False
        self._drafts = {}
        self._restore_generation = None
        self._directory_error = None
        self._has_displayed_session = False
        self.directory = SessionDirectory(self)
        self.directory.updated.connect(self._install_directory)
        self.directory.failed.connect(self._directory_failed)
        self._reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="session-preview")
        self.delivered.connect(lambda callback: callback())

    def restore(self):
        panel = self.panel
        if panel._session_commands is not None:
            if panel._current_session_id() is None:
                self._restore_generation = self.generation
                self.refresh()
            return
        manager = panel._session_mgr
        if manager.count() == 0:
            panel._active_session_id = manager.create_session(project_name=panel._get_project_name())
        else:
            panel._active_session_id = manager.get_last_active()
            data = manager.get_session(panel._active_session_id) if panel._active_session_id else None
            if data and data.get("messages"):
                panel._chat.load_history(data["messages"])
        self.refresh()

    def create(self, name):
        panel = self.panel
        if panel._session_commands is None:
            self.switch(panel._session_mgr.create_session(name=name, project_name=panel._get_project_name()))
            return
        if not self.persist():
            return
        context = panel._workspace.current_context()
        if context is None:
            logger.error("V2 Session creation requires RuntimeContext")
            return
        self._remember_draft()
        self.generation += 1
        self.loading = False
        self._accept_projection = True
        try:
            result = panel._session_commands.create_and_activate(name, context)
        finally:
            self._accept_projection = False
        panel._show_session_result(result, "创建会话失败")

    def switch(self, session_id):
        panel = self.panel
        if panel._current_session_id() == session_id and not self.loading:
            return
        if panel._session_commands is None:
            if panel._active_session_id:
                panel._chat.save_current_session(panel._active_session_id)
            panel._active_session_id = session_id
            data = panel._session_mgr.get_session(session_id)
            if data:
                panel._chat.load_session(data)
            self.refresh()
            return
        if not self.persist():
            return
        self._remember_draft()
        self.generation += 1
        generation = self.generation
        self.loading = True
        panel._workspace.refresh_banner()
        future = self._reader.submit(
            panel._session_commands.read_conversation, SessionRef(SessionId(session_id)), panel._runtime_context
        )

        def display():
            if panel._disposed or generation != self.generation:
                return
            self.loading = False
            try:
                result = future.result()
            except Exception:
                logger.exception("Could not read conversation preview %s", session_id)
                panel._chat.add_system_message("读取对话失败，请检查本地会话文件后重试。")
                panel._workspace.refresh_banner()
                return
            if panel._show_session_result(result, "读取会话失败"):
                # Directory metadata is display-only. Scope comes from this
                # owned record and activation performs its own fresh validation.
                if panel._workspace.matches(result.value):
                    self._activate(result.value)
                else:
                    self._display(result.value, preview=True)
            else:
                panel._workspace.refresh_banner()

        def ready(_):
            if not panel._disposed:
                try:
                    self.delivered.emit(display)
                except RuntimeError:
                    logger.debug("Conversation preview finished after its panel was destroyed")

        future.add_done_callback(ready)

    def activate_view(self):
        if not self.panel._workspace.matches() or not self.values.get("session_id"):
            return False
        if not self.preview:
            self.panel._workspace.resume_execution()
            return True
        self._remember_draft()
        return self._activate(self.values)

    def _activate(self, values):
        panel = self.panel
        self._accept_projection = True
        try:
            result = panel._session_commands.switch(
                SessionRef(SessionId(values["session_id"])), panel._workspace.bound_context(values)
            )
        finally:
            self._accept_projection = False
        return panel._show_session_result(result, "切换会话失败")

    def _remember_draft(self):
        editor = getattr(self.panel._chat, "_input", None)
        if editor is not None and self.panel._active_session_id:
            self._drafts[self.panel._active_session_id] = editor.toPlainText()

    def _display(self, values, *, preview, refresh=False):
        panel = self.panel
        first_display = not self._has_displayed_session
        self._has_displayed_session = True
        self._remember_draft()
        self.values = dict(values)
        self.preview = preview
        panel._active_session_id = str(values["session_id"])
        message_list = getattr(panel._chat, "_message_list", None)
        navigation = getattr(message_list, "navigation", None)
        # Initial discovery can finish after the user starts typing. Bind that
        # unowned draft without replacing its document or resetting search.
        keep_inputs = first_display or refresh
        updating = navigation.replacing_records() if keep_inputs and navigation is not None else nullcontext()
        with updating:
            panel._chat.load_session({**values, "_preview": preview})
        if not preview:
            panel._workspace.resume_execution()
        if not keep_inputs and hasattr(panel._chat, "set_input"):
            panel._chat.set_input(self._drafts.get(panel._active_session_id, ""))
        self.refresh()

    def projection(self, snapshot):
        panel = self.panel
        if not hasattr(panel, "_session_list"):
            return
        if snapshot is None:
            if not self.preview or self._accept_projection:
                self.values = {}
                panel._active_session_id = None
                panel._chat.load_session({"messages": []})
            self.refresh()
            return
        values = snapshot.to_dict()["values"]
        session_id = str(values.get("session_id", ""))
        if not self._accept_projection:
            if self.preview or self.loading:
                return
            if panel._active_session_id != session_id:
                self.generation += 1
        same_session = panel._active_session_id == session_id and not self.preview
        if same_session:
            self.values = dict(values)
            self.refresh()
            if panel._saving_session_id == session_id:
                return
            if getattr(panel._chat, "session_ready", True):
                backend, controller = panel._chat.recovery_snapshot()
                if backend == values.get("backend_history", values.get("messages", [])) and (
                    controller.to_dict() == values.get("controller")
                ):
                    return
        self._display(
            values,
            preview=not panel._workspace.matches(values),
            refresh=same_session and not self._accept_projection,
        )

    def persist(self):
        panel = self.panel
        session_id = panel._current_session_id()
        if self.preview or self.loading or session_id is None or panel._runtime_context is None:
            return True
        if not getattr(panel._chat, "session_ready", True):
            return True
        backend, controller = panel._chat.recovery_snapshot()
        values = self.values
        visible = list(backend)
        if getattr(panel._session_commands, "assistant_requests", None) is None:
            previous = values.get("backend_history", [])
            if backend[: len(previous)] == previous:
                visible = [*values.get("messages", []), *backend[len(previous) :]]
        panel._saving_session_id = session_id
        try:
            result = panel._session_commands.save_conversation(
                SessionRef(SessionId(session_id)),
                visible,
                list(backend),
                panel._workspace.bound_context(),
                backend_summary=values.get("backend_summary") if backend else None,
                controller=controller,
                jobs=panel._chat.lifecycle_jobs(),
            )
        finally:
            panel._saving_session_id = None
        saved = panel._show_session_result(result, "保存会话失败")
        if saved:
            self.refresh()
        return saved

    def delete(self, session_id):
        panel = self.panel
        if panel._session_commands is None:
            panel._session_mgr.delete_session(session_id)
            if panel._active_session_id == session_id:
                panel._active_session_id = None
                last = panel._session_mgr.get_last_active()
                self.switch(last) if last else self.create("")
            self.refresh()
            return
        selected = panel._current_session_id() == session_id
        if selected and not self.persist():
            return
        result = panel._session_commands.delete(SessionRef(SessionId(session_id)), panel._runtime_context)
        if not panel._show_session_result(result, "删除会话失败"):
            self.refresh()
            return
        self._drafts.pop(session_id, None)
        if selected:
            panel._active_session_id = None
            self.values = {}
            self.preview = False
            self.restore()
        self.refresh()

    def rename(self, session_id, current_name):
        panel = self.panel
        name, ok = QInputDialog.getText(panel, "重命名会话", "新名称:", text=current_name)
        if not ok or not name.strip():
            return
        if panel._session_commands is None:
            panel._session_mgr.rename_session(session_id, name.strip())
        else:
            if not self.persist():
                return
            result = panel._session_commands.rename(
                SessionRef(SessionId(session_id)), name.strip(), panel._runtime_context
            )
            if not panel._show_session_result(result, "重命名会话失败"):
                return
        self.refresh()

    def refresh(self):
        panel = self.panel
        if panel._disposed:
            return
        source = panel._session_commands or panel._session_mgr
        panel._session_list.set_workbench(*panel._workspace.identity())
        panel._session_list.set_active(panel._active_session_id)
        self.directory.refresh(source, panel._navigation.get("session_project_labels"))
        panel._workspace.refresh_banner()

    def directory_labels(self, values):
        return self.directory.labels(values, lookup_available="session_project_labels" in self.panel._navigation)

    def _install_directory(self, sessions):
        panel = self.panel
        if panel._disposed:
            return
        self._directory_error = None
        panel._session_list.set_workbench(*panel._workspace.identity())
        panel._session_list.set_sessions(sessions)
        current = panel._active_session_id
        panel._session_list.set_active(current)
        panel._header.set_session_name(
            next((row.get("name", "新对话") for row in sessions if row["session_id"] == current), "新对话")
        )
        panel._workspace.refresh_banner()
        restoring = self._restore_generation
        if restoring is None or restoring != self.generation or current is not None:
            self._restore_generation = None
            return
        if panel._workspace.workbench_issue():
            return
        matching = next((row for row in sessions if row.get("available", True) and panel._workspace.matches(row)), None)
        if matching is not None:
            self._restore_generation = None
            self.switch(matching["session_id"])
        elif any(not row.get("available", True) for row in sessions):
            self._directory_failed(RuntimeError("有会话记录无法读取，尚未自动新建对话。请检查会话文件后重试。"))
        else:
            self._restore_generation = None
            self.create("")

    def _directory_failed(self, error):
        if self.panel._disposed:
            return
        logger.error(
            "Could not refresh the assistant session directory", exc_info=(type(error), error, error.__traceback__)
        )
        message = f"读取会话列表失败，已保留当前列表。请检查本地会话或工程文件后重试。\n{error}"
        if message != self._directory_error:
            self.panel._chat.add_system_message(message)
        self._directory_error = message
        self.panel._workspace.refresh_banner()

    def close(self):
        self.generation += 1
        self.directory.close()
        self._reader.shutdown(wait=False, cancel_futures=True)
