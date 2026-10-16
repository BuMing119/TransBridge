"""Session-local composer materials, asynchronous parsing and submission snapshots."""

from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QFileDialog

from transbridge.application.assistant_requests.attachments import MAX_ATTACHMENT_CHARACTERS, MAX_ATTACHMENTS
from transbridge.smart_assistant.file_parser import FileParser

from .attachment_dialog import AttachmentDialog

logger = logging.getLogger(__name__)


class UploadBinding(QObject):
    """Drafts survive switching within this window, while sent snapshots survive restarts."""

    delivered = pyqtSignal(object)

    def __init__(self, *, parent, documents, notify, max_bytes, busy_changed=lambda busy: None):
        super().__init__(parent)
        self._parent = parent
        self.documents = documents
        self._notify = notify
        self._max_bytes = max_bytes
        self._busy_changed = busy_changed
        self._sessions = {}
        self._session_id = None
        self._used = False
        self._generation = 0
        self._pending = {}
        self._label = None
        self._dialog = None
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="assistant-attachment")
        self.delivered.connect(self._receive_delivery, Qt.ConnectionType.QueuedConnection)

    @pyqtSlot(object)
    def _receive_delivery(self, callback):
        if not self._closed:
            callback()

    @property
    def busy(self):
        return bool(self._pending)

    def bind_label(self, label):
        self._label = label
        self._refresh()

    def activate(self, session_id):
        if session_id == self._session_id or self._closed:
            return
        self._sessions[self._session_id] = (dict(self.documents), self._used)
        self._invalidate()
        self._session_id = session_id
        documents, self._used = self._sessions.get(session_id, ({}, False))
        self.documents.clear()
        self.documents.update(documents)
        if self._dialog is not None:
            self._dialog.close()
        self._refresh()

    def open_manager(self, label=None):
        if self._closed:
            return
        if label is not None:
            self.bind_label(label)
        if not self.documents and not self.busy:
            self.select_files()
            return
        if self._dialog is None:
            self._dialog = AttachmentDialog(self, self._parent)
        self._dialog.refresh()
        self._dialog.show()
        self._dialog.raise_()

    def select_files(self, label=None):
        if self._closed:
            return
        if label is not None:
            self.bind_label(label)
        paths, _ = QFileDialog.getOpenFileNames(
            self._parent,
            "选择参考文件",
            "",
            "文档 (*.xlsx *.csv *.md *.txt *.json *.pdf *.docx *.zip);;全部 (*.*)",
        )
        self.add_paths(paths)

    def add_paths(self, paths):
        if self._closed:
            return
        for raw in paths:
            if len(self.documents) + len(self._pending) >= MAX_ATTACHMENTS:
                self._notify(f"最多添加 {MAX_ATTACHMENTS} 个参考文件，请先移除不需要的附件。")
                break
            path = Path(raw)
            generation, session_id = self._generation, self._session_id
            future = self._executor.submit(self._parse, path)
            self._pending[future] = path.name
            future.add_done_callback(lambda result, p=path, g=generation, s=session_id: self._deliver(result, p, g, s))
        self._refresh()

    def _parse(self, path):
        parser = FileParser.get_parser(path)
        if parser is None:
            raise ValueError("不支持的文件格式")
        size = path.stat().st_size
        if size > self._max_bytes:
            raise ValueError(f"文件过大 ({size / 1048576:.1f} MB)，上限 {self._max_bytes / 1048576:.0f} MB")
        document = parser.parse(path)
        if not document.raw_text.strip():
            raise ValueError("未提取到可读取的文本；扫描图片需先转换为文字")
        if len(document.raw_text) > MAX_ATTACHMENT_CHARACTERS:
            raise ValueError(f"提取文本超过 {MAX_ATTACHMENT_CHARACTERS:,} 字，未添加且未截断，请拆分文件")
        return document

    def _deliver(self, future, path, generation, session_id):
        try:
            self.delivered.emit(lambda: self._received(future, path, generation, session_id))
        except RuntimeError:
            logger.debug("Attachment parsing finished after its view was destroyed")

    def _received(self, future, path, generation, session_id):
        if self._closed or generation != self._generation or session_id != self._session_id:
            return
        self._pending.pop(future, None)
        try:
            document = future.result()
            count = sum(len(value.raw_text) for value in self.documents.values()) + len(document.raw_text)
            if count > MAX_ATTACHMENT_CHARACTERS:
                raise ValueError(f"附件文本合计超过 {MAX_ATTACHMENT_CHARACTERS:,} 字，未添加此文件，请拆分发送")
            name, index = path.name, 2
            while name in self.documents:
                name = f"{path.stem} ({index}){path.suffix}"
                index += 1
            self.documents[name] = document
            self._used = True
        except Exception as error:
            logger.warning("Attachment parsing failed for %s", path.name, exc_info=True)
            message = str(error).replace(str(path), path.name)
            self._notify(f"无法添加参考文件 {path.name}：{message}")
        self._refresh()

    def remove(self, name):
        if name in self.documents:
            del self.documents[name]
            self._used = True
            self._refresh()

    def clear(self):
        self._invalidate()
        self._used = self._used or bool(self.documents)
        self.documents.clear()
        self._refresh()

    def snapshot(self):
        if self.busy:
            raise ValueError("参考文件仍在提取文本，请等待完成后发送，或清空附件后重试。")
        if not self._used:
            return None
        return tuple(
            {"name": name, "format": document.format, "content": document.raw_text}
            for name, document in self.documents.items()
        )

    def _refresh(self):
        if self._closed:
            return
        names = "、".join(self.documents)
        summary = f"本次附件 {len(self.documents)} 个：{names}" if names else ""
        if self._pending:
            summary = (summary + " · " if summary else "") + f"正在提取 {len(self._pending)} 个文件…"
        if self._label is not None:
            self._label.set_full_text(summary)
            self._label.setVisible(bool(summary))
            self._label.setToolTip(summary + "\n点击“附件”预览完整提取文本、移除或继续添加。")
            self._label.setAccessibleDescription(summary)
        self._busy_changed(self.busy)
        if self._dialog is not None:
            self._dialog.refresh()

    def _invalidate(self):
        self._generation += 1
        for future in self._pending:
            future.cancel()
        self._pending.clear()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._invalidate()
        self.documents.clear()
        self._sessions.clear()
        if self._dialog is not None:
            self._dialog.close()
        self._executor.shutdown(wait=False, cancel_futures=True)
