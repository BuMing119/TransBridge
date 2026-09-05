"""Background connection checks shared by settings-center pages."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QLabel, QPushButton

from transbridge.ui.tools.ai_translator.config_presenter import ConnectionTestResult, check_llm_connection
from transbridge.ui.workers import ApiWorker

_ACTIVE_WORKERS: set[ApiWorker] = set()


class SettingsConnectionController(QObject):
    """Run a prepared read-only check and detach safely when the dialog closes."""

    def __init__(
        self,
        button: QPushButton,
        status: QLabel,
        prepare: Callable[[], Callable[[], ConnectionTestResult]],
        *,
        idle_text: str,
    ) -> None:
        super().__init__(button)
        self._button = button
        self._status = status
        self._prepare = prepare
        self._idle_text = idle_text
        self._worker: ApiWorker | None = None
        self._closed = False
        button.clicked.connect(self.start)

    @property
    def is_running(self) -> bool:
        return self._worker is not None

    def start(self) -> bool:
        if self._closed or self._worker is not None:
            return False
        try:
            operation = self._prepare()
        except Exception:
            self._present(ConnectionTestResult("critical", "配置无效", "请检查当前页面的配置。"))
            return False
        worker = ApiWorker(operation, route_http_errors=False)
        self._worker = worker
        _ACTIVE_WORKERS.add(worker)
        worker.result.connect(self._on_result)
        worker.error.connect(self._on_error)
        worker.finished.connect(self._on_finished)
        worker.finished.connect(lambda: _release_worker(worker))
        self._button.setEnabled(False)
        self._button.setText("正在测试…")
        self._status.setText("正在后台检查连接…")
        worker.start()
        return True

    def close(self) -> None:
        self._closed = True
        worker, self._worker = self._worker, None
        if worker is None:
            return
        for signal, slot in (
            (worker.result, self._on_result),
            (worker.error, self._on_error),
            (worker.finished, self._on_finished),
        ):
            try:
                signal.disconnect(slot)
            except TypeError:
                pass

    def _on_result(self, result: object) -> None:
        if isinstance(result, ConnectionTestResult):
            self._present(result)
        else:
            self._on_error("invalid result")

    def _on_error(self, _message: str) -> None:
        self._present(ConnectionTestResult("critical", "连接失败", "连接失败，请检查服务地址和凭据。"))

    def _on_finished(self) -> None:
        self._worker = None
        if not self._closed:
            self._button.setEnabled(True)
            self._button.setText(self._idle_text)

    def _present(self, result: ConnectionTestResult) -> None:
        if self._closed:
            return
        message = result.message if result.level != "critical" else "连接失败，请检查服务地址、模型和凭据。"
        self._status.setText(message)
        self._status.setProperty("tbStatusId", "error" if result.level == "critical" else "success")


def llm_operation(config: object) -> Callable[[], ConnectionTestResult]:
    snapshot = _execution_copy(config)
    return lambda: check_llm_connection(snapshot)


def embedding_operation(config: object) -> Callable[[], ConnectionTestResult]:
    snapshot = _execution_copy(config)
    return lambda: check_embedding_connection(snapshot)


def check_embedding_connection(config: object) -> ConnectionTestResult:
    embedding = getattr(config, "embedding", None)
    mode = str(getattr(embedding, "mode", "disabled") or "disabled").casefold()
    if mode == "disabled":
        return ConnectionTestResult("info", "语义检索已关闭", "当前仅使用精确和字面术语匹配。")
    if mode == "api" and not all(
        str(getattr(embedding, name, "") or "").strip() for name in ("api_key", "model", "base_url")
    ):
        return ConnectionTestResult("critical", "配置不完整", "Embedding API 配置不完整。")
    try:
        from transbridge.infra.embedding_client import create_embedding_client

        client = create_embedding_client(config)
        if not client.available:
            raise RuntimeError("embedding unavailable")
        vectors = client.encode(["TransBridge semantic retrieval check"])
        if getattr(vectors, "shape", (0, 0))[0] != 1:
            raise RuntimeError("invalid embedding result")
        return ConnectionTestResult("info", "语义检索可用", f"编码成功，向量维度 {int(vectors.shape[1])}。")
    except Exception:
        return ConnectionTestResult("critical", "语义检索检查失败", "语义检索服务不可用。")


def paratranz_operation(
    source: object, base_url: str, timeout: int, replacement_token: str
) -> Callable[[], ConnectionTestResult]:
    token = replacement_token or str(getattr(source, "token", "") or "")

    def check() -> ConnectionTestResult:
        if not token:
            return ConnectionTestResult("critical", "未配置 Token", "请先填写 ParaTranz API Token。")
        from transbridge.config.paratranz import ParatranzConfig
        from transbridge.paratranz.api.paratranz_user_api import ParatranzUserAPI

        config = ParatranzConfig(token=token, base_url=base_url, timeout=timeout)
        api = ParatranzUserAPI(token=token, config=config)
        try:
            user = api.get_my_user()
        finally:
            api.close()
        nickname = user.get("nickname") or user.get("username") or "当前账户" if isinstance(user, dict) else "当前账户"
        return ConnectionTestResult("info", "ParaTranz 连接成功", f"验证成功：{nickname}")

    return check


def _execution_copy(config: object) -> object:
    copier = getattr(config, "copy_for_execution", None)
    return copier() if callable(copier) else deepcopy(config)


def _release_worker(worker: ApiWorker) -> None:
    _ACTIVE_WORKERS.discard(worker)
    worker.deleteLater()


__all__ = [
    "SettingsConnectionController",
    "check_embedding_connection",
    "embedding_operation",
    "llm_operation",
    "paratranz_operation",
]
