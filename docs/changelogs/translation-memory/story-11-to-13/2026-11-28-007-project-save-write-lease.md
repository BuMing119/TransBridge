# 编辑后立即保存时等待同工程的后台写入

2026-11-28 · Epic `translation-memory` · [S11 工程保存与词典入库](../../../../plans/translation-memory/plan.md)

编辑译文会触发后台术语定位缓存失效。缓存线程清理同一工程的 SQLite 数据时，自动保存或手动保存可能收到 `ACTIVE_SAVE_FAILED`。手动保存缺少成功回执时，本次译文也无法进入个人词典。

## 实现

- `src/transbridge/persistence/project_store/connection.py` 的 `project_mutation_lease` 按规范化数据库路径对本进程写入使用可重入锁。缓存事务释放后，保存线程再取得原有操作系统锁并继续写入。不同工程使用独立的锁，最后一个使用者退出后可回收锁对象。其他进程尝试写入同一工程时仍会收到 `PROJECT_WRITER_BUSY`。已有工程和词典均可直接使用。
- 新增 `tests/persistence/project_store/test_concurrent_writes.py`，通过真实工程验证同工程线程等待、另一工程并行保存、嵌套持锁以及独立进程在最外层锁释放后写入成功。

## 验证

- 快速保存专项用例 `tests/ui/test_project_save_dictionary.py::test_explicit_save_updates_current_source_dictionary` 在修复前使用 12 个独立临时工程运行，2 次失败，分别发生在自动保存和手动保存。修复后的同一用例 12 次全部通过。单独控制缓存线程持锁的执行顺序，旧版发生拒写，修复后工程保存成功，词典可读到最新译文。
- `uv run --offline --no-sync --no-cache pytest tests/persistence/project_store/test_concurrent_writes.py tests/persistence/test_long_database_paths.py tests/ui/test_project_save_dictionary.py -q` 通过 13 项。新并发用例在修复前的源码快照中按预期失败。
- 扩展回归覆盖工程存储、词典、AI 初译与最终 QA，共 287 项通过。全仓 `ruff check src tests` 和 `ruff format --check src tests` 均通过。

本次修复遗留项为零。
