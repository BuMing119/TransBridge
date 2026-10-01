# ParaTranz 远端序号刷新与单文件上传闭环

- 日期：2026-10-01
- Epic：paratranz-context-order
- Story：03 远端仅序号更新；04 单文件上传入口闭环
- 计划：[plan](../../../../plans/paratranz-context-order/plan.md)

## 用户可见变化

重新解析 ESP 后，上传或双向同步可识别同 key、同内容而序号不同的词条，按既有确认流程只更新远端 context。也支持远端尚无前缀的词条。不改 key，不删除重建词条，不发送原文、译文和状态。纯下载不写远端；内容同时变化保留原有冲突策略。

单文件上传现在复用正式 ParaTranz writer，在递归拆分前生成完整集合序号；413 拆分不会重新编号。本地顺序保存在元数据中，八位前缀仅投影到远端上下文。

本记录承接 Story 01～02：原先仅序号差异 SKIP 的限制已由本轮实现解除，旧增量保留为历史记录。

## 逐文件变化

- 修改 `src/transbridge/application/sync/planner.py`：生成 `context_order_changed` 的 UPDATE_REMOTE，保留确认与远端身份要求，限定上传/双向操作及严格结构化上下文。
- 修改 `src/transbridge/application/sync/executor.py`：该分支调用独立 context 更新接口；保留已有快照过期与部分失败重试保护。
- 修改 `src/transbridge/application/ports/paratranz.py`：增加 `update_entry_context` 协议。
- 修改 `src/transbridge/paratranz/service.py`：验证远端 ID，底层请求只传 context，验证返回 ID/key/context 一致性，允许返回服务端最新译文/状态。
- 修改 `src/transbridge/paratranz/workflow/uploader.py`：单文件使用 adapter 输出 wire records 后再拆分，移除直接提交 collection 内部格式的路径。
- 新增 `tests/contracts/paratranz/test_sync_order_refresh.py`：12 项，覆盖确认、无前缀、幂等、下载、自由文本、缺 ID、内容冲突、过期及重试。
- 新增 `tests/paratranz/test_context_update.py`：10 项，覆盖字段隔离、ID 校验、空响应、取消传递及响应身份验证。
- 新增 `tests/paratranz/test_single_upload_order.py`：9 项，覆盖四种上传模式、拆分、身份、缺文件跳过、溢出及本地不变性。
- 更新计划与相关索引，说明新流程与兼容边界；保留无关助手 UI/压缩工作区变更。

## 验证

新增 31 项回归全部通过；独立子 Agent 只读审查未发现新增可行动问题。

主会话联合回归：266 passed、1 skipped、1 failed。

```powershell
uv run pytest tests/paratranz tests/contracts/paratranz tests/contracts/io/test_paratranz_order_export.py tests/contracts/io/test_paratranz_order_readback.py tests/ui/operations -q
uv run ruff check src tests
uv run ruff format --check src tests
git diff --check
```

Ruff 两项通过（1422 文件格式合规），diff 检查通过。唯一失败仍为 `test_assistant_upload_reuses_only_the_target_projects_known_identity[8]`，此前已在 HEAD 上传函数中复现；测试同时期望重复 key 导致零上传和 success=True。本轮未修改该错误语义或测试。

子 Agent 另执行同步 62 项、上传 workflow 32 项、独立 QA 38 项聚焦检查，均通过（集合有重叠，不累加）。任务专属临时目录已清理。

## 边界与遗留

无 schema 迁移。必须先重新解析源文件获取新顺序；不自动监视磁盘 ESP。此次未执行全仓 pytest、真实 ParaTranz 写入或网页排序验证，格式本身不强制网页采用上下文排序。已知既有失败保留。
