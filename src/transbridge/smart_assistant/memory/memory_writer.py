"""MemoryWriterThread — 后台写入线程，批量刷写记忆数据到磁盘。

Extracted from memory_store.py (Story 04: 模块精简收尾)。
"""

from collections import deque
import json
import logging
import os
from pathlib import Path
import threading

from transbridge.smart_assistant.guardrails.output_validator import sanitize_for_storage

logger = logging.getLogger(__name__)


class MemoryWriterThread(threading.Thread):
    """M9: 后台写入线程，批量刷写记忆数据到磁盘。

    职责：
    - 在独立线程中运行，等待 MemoryStore 通知后批量将元数据 (JSON) 和向量索引
      (FAISS) 持久化到磁盘。
    - 通过 dirty flag 避免无数据变更时的无效刷盘，减少 CPU 和 I/O 浪费。

    生命周期：
    - 由 MemoryStore.__init__ 创建并启动，随 MemoryStore.close() → stop() 终止。
    - stop() 调用后会进行最后一次刷盘，然后等待线程结束 (最多 3 秒)。

    线程模型：
    - enqueue(): 由 MemoryStore 主线程 (持有 lock) 调用，设置 dirty flag 并通知
      Condition，立即返回不阻塞。
    - run(): 线程主循环，wait 在 Condition 上 (0.5s 超时)，唤醒后检查 running
      标志和 dirty flag，仅在有脏数据时执行 _flush()。
    - _flush(): 通过回调 (get_metadata_cb / get_vector_store_cb) 获取 MemoryStore
      的当前状态并写入磁盘。元数据先写临时文件再原子 rename；写入失败时清理临时
      文件并保留 dirty flag 以触发重试。
    """

    def __init__(self, storage_dir: Path, metadata_path: Path, index_path: Path, get_metadata_cb, get_vector_store_cb):
        super().__init__(daemon=True)
        self._queue: deque = deque()
        self._cv = threading.Condition()
        self._storage_dir = storage_dir
        self._metadata_path = metadata_path
        self._index_path = index_path
        self._get_metadata = get_metadata_cb
        self._get_vector_store = get_vector_store_cb
        self._running = True
        # M17: dirty generation counter — 仅在有数据变更时才执行刷盘。
        # 使用计数器而非布尔值，消除 _flush() 与 enqueue() 之间的 TOCTOU 竞态：
        # enqueue() 递增计数器，_flush() 在锁内捕获当前值并归零，IO 完成后
        # 不写回，并发 enqueue() 的递增不会被覆盖。
        self._dirty_gen: int = 0

    def enqueue(self) -> None:
        """通知 writer 有数据待刷盘。递增 dirty 计数器保证下次唤醒执行 flush。"""
        with self._cv:
            self._dirty_gen += 1
            self._cv.notify()

    def run(self) -> None:
        while self._running:
            with self._cv:
                # M53: 空闲时使用更长超时 (5s)，有脏数据时用短超时 (0.5s) 以批量聚合。
                # C11 已引入 _dirty_gen 计数器避免无效刷盘，此处进一步减少空闲唤醒频率。
                timeout = 5.0 if self._dirty_gen == 0 else 0.5
                self._cv.wait(timeout=timeout)
            if not self._running:
                break
            try:
                self._flush()
            except Exception as exc:
                logger.warning("MemoryWriter 刷盘失败: %s", exc)

    def _flush(self) -> None:
        """批量将元数据和向量索引写入磁盘。

        M17: 在 CV 锁内检查 dirty_gen 计数器，无变更时跳过以节省 I/O。
        捕获当前代次后立即归零 — 并发 enqueue() 的递增不会被覆盖，
        从而消除 TOCTOU 竞态。
        M36: 写入失败时清理临时文件 (tmp_meta) 避免残留，
        并将捕获的代次加回计数器以触发重试。
        """
        with self._cv:
            if self._dirty_gen == 0:
                return
            gen = self._dirty_gen
            self._dirty_gen = 0

        try:
            metadata = self._get_metadata()
            data = {}
            for mid, entry in metadata.items():
                data[mid] = entry.to_dict()
            # C5: 持久化前脱敏 — 移除 API 密钥/Token/文件路径等敏感信息
            data = sanitize_for_storage(data)
            tmp_meta = str(self._metadata_path) + ".tmp"
            try:
                with open(tmp_meta, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_meta, str(self._metadata_path))
            except Exception:
                # M36: 清理写入失败后残留的临时文件
                try:
                    os.unlink(tmp_meta)
                except OSError:
                    pass
                raise

            vector_store = self._get_vector_store()
            if vector_store is not None:
                # M36: 向量索引也使用临时文件 + 原子 rename，避免崩溃时残留损坏文件
                tmp_index = str(self._index_path) + ".tmp"
                try:
                    vector_store.save(tmp_index)
                    os.replace(tmp_index, str(self._index_path))
                except Exception:
                    try:
                        os.unlink(tmp_index)
                    except OSError:
                        pass
                    raise
        except Exception:
            # 写入失败：将捕获的代次加回计数器，确保重试
            with self._cv:
                self._dirty_gen += gen
            raise

    def stop(self) -> None:
        self._running = False
        with self._cv:
            self._cv.notify()
        self._flush()  # 最终刷盘
        self.join(timeout=3)
