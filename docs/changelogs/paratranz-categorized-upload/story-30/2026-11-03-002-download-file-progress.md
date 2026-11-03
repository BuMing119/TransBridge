# S30：下载按文件实时显示进度

日期：2026-11-03（Asia/Shanghai）。Epic：paratranz-categorized-upload；Story：S30。
关联：[计划](../../../../plans/paratranz-categorized-upload/plan.md)、[五路并发记录](2026-10-31-001-bounded-file-concurrency.md)。

## 行为

修复第一个插件含多个分类文件时长期显示 0%：按所选插件匹配到的云端文件计算总数，每个成功下载的文件更新“已下载 X / Y 个文件”和文件路径，进度条使用相同计数。并发中后续文件先完成时，在原 worker 最多每 100ms 收集完成通知，再复用窗口原有定时刷新；不会等待第一个文件结束才显示进展。

失败请求不增加成功计数，不在结束时强制补成 100%；空文件计为一次成功下载。404 刷新后按当前文件清单重算总数及有效完成数，重复读取相同 ID 不重复计数。该计数表示文件下载，不表示译文已合入或保存；文件布局变化时分母及百分比可能调整。完成后提示正在整理下载结果，原合入、失败隔离及保存规则不变。

## 本次可归属增量

起始工作区已包含五路并发和其他未提交修改；这些既有实现不归入本记录。

- 新增 `src/transbridge/paratranz/download_progress.py`：文件 ID 去重、范围内总数与路径提示、清单刷新及串行读取进度适配。
- 修改 `src/transbridge/paratranz/batch_download_snapshot.py` 的 `fetch`：以文件级进度替换插件级进度，接入既有串行/并发读取器。
- 修改起始时已存在且未跟踪的 `src/transbridge/paratranz/parallel_file_reads.py`：只新增完成通知队列、原 worker 的有界等待/通知收集、可选完成回调；保留既有五路前瞻、顺序验证和资源关闭。
- 新增 `tests/paratranz/test_download_progress.py`：五个回归覆盖插件内实时推进、未选文件排除、失败不报完成、空范围、404 清单变化和缓存去重、首文件阻塞时其他文件计数及回调线程归属。
- 更新 `docs/changelogs/INDEX.md`：增加本记录链接。

## 验证

初次默认 uv 缓存因沙箱目录拒绝访问而无法启动检查；改用已有缓存目录后执行以下命令（PowerShell 先设置 `$env:UV_CACHE_DIR = 'D:\MyCode\TransBridge\.uv-cache-agent'`）：

```text
uv run pytest tests/paratranz/test_download_progress.py tests/paratranz/test_batch_download_snapshot.py tests/paratranz/test_parallel_file_downloads.py tests/paratranz/test_dialogue_order_file_retry.py tests/ui/operations/test_batch_download_dialog.py -q
85 passed in 1.73s

uv run ruff check src tests
All checks passed!

uv run ruff format --check src tests
2351 files already formatted

git diff --check
退出码 0；仅已有 CRLF 转换提示。
```

未运行全库 pytest、真实云端请求和打包：本次为局部进度修复，使用受控传输与现有 Qt 窗口测试验证。未操作用户工程数据、未重启正在运行的应用；从修改后的源码重新启动后生效，打包版需要重新构建。无数据迁移要求。未创建任务临时目录，未提交 Git。

遗留项：无已知本次阻塞问题。
