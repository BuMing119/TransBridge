# 强制上传前确认空译文覆盖风险

日期：2026-11-06。Epic：paratranz-categorized-upload / Story：S12 文件 API 上传的安全确认补修。

## 原因与交付行为

此前选择“仅强制导入译文”后点击上传会直接启动后台任务，未明确提醒空译文也会覆盖云端已有译文。用户要求普通/分类及单独上传均在执行前确认。本次在共用窗口拦截强制上传和强制补传，覆盖单插件与批量范围。

弹窗说明人工译文会被覆盖、空译文将清空云端原有译文。确认按钮为“仍要强制上传”，默认及 Escape 按钮为“取消”；直接回车、Esc、关闭弹窗均不启动 worker。每次执行重新确认，取消不改动已有补传状态。非强制上传保持原流程。

## 本次文件变更

- 修改 `src/transbridge/ui/operations/categorized_upload_dialog.py`：新增 `_confirm_force_upload`；`_request_upload` 在更新补传状态和启动 worker 前确认，`_request_retry` 按 `_retry_mode` 保存的原执行策略确认，避免当前下拉框改变补传警告。弹窗结束后释放其 Qt 对象。
- 新增 `tests/ui/operations/test_force_upload_confirmation.py`：28 项真实 Qt 弹窗交互回归，覆盖普通/分类、单插件/批量、明确确认/取消/回车/Esc/关闭、重复执行重新确认、非强制模式无弹窗、补传沿用原策略以及取消新上传后保留旧补传。
- 修改 `docs/dev/paratranz-categorized-upload.md`：在原操作说明中更新强制确认、空译文覆盖与补传确认规则。
- 修改 `docs/changelogs/INDEX.md`：增加本记录索引。

共享窗口仍小于 500 行且主类方法数小于 30，本次属于既有上传交互职责；未新增 API、数据格式、依赖或迁移。不改变服务端 force 的语义，也不自动过滤用户确认后上传的空译文。

## 验证

使用现有 uv 环境，设置 `UV_CACHE_DIR=D:/MyCode/TransBridge/.tmp-force-upload-confirmation/uv-cache`，`--no-sync` 保持依赖不变。首次默认 uv 缓存路径因沙箱权限失败，改用任务目录后成功。首轮 60 项通过；后续新增取消新上传保留旧补传用例，最终扩大集合如下：

```powershell
uv run --no-sync pytest tests/ui/operations/test_force_upload_confirmation.py tests/ui/operations/test_categorized_upload_dialog.py tests/ui/operations/test_categorized_upload_retry.py tests/ui/operations/test_upload_mode_routing.py tests/ui/operations/test_file_upload_production.py tests/ui/operations/test_categorized_upload_bridge.py tests/paratranz/test_file_publication.py -q --basetemp .tmp-force-upload-confirmation/pytest
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
git diff --check
```

最终 **153 passed**，3 条既有 SWIG 弃用警告。Ruff 全部通过，2382 个文件格式通过，diff 空白检查通过。新增测试首轮导入排序与格式问题已修复后复验。

使用 offscreen Qt 实例渲染实际确认弹窗，显式加载本机微软雅黑字体后检查截图：完整警告与确认问题换行可见，两个中文按钮可见且默认选中取消。自动化交互使用真实 QMessageBox 与延迟 worker，不发送网络请求。临时缓存、测试目录与截图在收尾清理。

未运行全库 pytest、打包安装或本次代码的真实 ParaTranz 写入；未修改真实翻译数据。前序备份恢复操作不归入本次代码增量。启动时已有 `.transbridge/` 未跟踪内容，保持原样。

## 遗留项

无。本地代码与相关验证完成，未提交或推送。
