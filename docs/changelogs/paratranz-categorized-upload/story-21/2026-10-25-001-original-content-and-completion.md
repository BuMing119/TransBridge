# 同源原文译文自动处理与明确完成态

- 日期：2026-10-25
- Epic：paratranz-categorized-upload；Story：S21
- 需求：FR22.3、FR22.3.2、FR22.3.3；ADR-047
- [计划](../../../../plans/paratranz-categorized-upload/plan.md)

## 问题与交付

截图显示 93,538 条已更新并保存，另有 144 条待确认，但主按钮仍为可点击的“下载并保存”，
容易误认为需要再次点击。已保存部分此前已经落盘，不是等待第二次提交。
用户要求能按原文与译文确定的由程序处理，不把内部 key/元数据限制转交给用户。

当前工程同步现在按同插件精确原文/译文内容自动匹配，支持旧 key 变化、相同译文的元数据差异及内容复用。
真正不同译文先以准确 key/上下文、本地现译收窄；仍不唯一才复核。
没有一致原文或云端空译文保留本地并说明，不要求无意义确认。完成后主按钮显示“已保存”并禁用，
完整无变化显示“已完成”，真实未完成来源/读取/保存才显示“重试未完成项”。

未取得截图 144 条实际逐项候选，不能声称它们均属于同一种原因或会全部消失。
只读核查了最新两份本地应用日志的诊断计数，未找到该批量路径的逐项计划事件；没有访问真实云端。
以下复现、规则及验证均明确以代码和受控数据为证据。

## 真实变更

- `src/transbridge/application/sync/planner.py`：新增默认 False 的 `content_download`；
  `batch_download.py` 仅为当前工程显式启用，独立严格下载/上传/双向同步合同保留。
- `candidate_matching.py`：新增同命名空间精确原文索引及预分组内容证据。
  非空唯一译文自动采用；同译文不因上下文/状态不同产生伪冲突，空译文不干扰有效译文。
  多译文按准确 key+上下文、准确 key、本地现译等确定证据收窄，仍不唯一保留真正复核。
  无一致原文/仅空译文分别以 `no_matching_original`/`remote_untranslated` 保留本地。
  不跨插件、不归一化原文；空原文只使用 same-key，限定身份/正文不一致仍保护。
- 同译文状态一致采用真实状态；状态不同且本地现译一致时保留本地状态，否则保守 TRANSLATED=1。
  状态不是有序置信度，不取数字最大，不制造 reviewed/locked。
- `download_planner.py`：内容自动路径省去不必要的 review 构造及云端 ID 归属要求；
  真正复核/新编辑/显式选择仍绑定完整所见内容，不随机采用相互不同的非空译文。
- `local_candidate.py`：索引并物化实际被选中的无 ID 内容，允许一份同源译文用于多个本地 key，
  验证原文/译文/状态及来源不被替换，保留本地原文、上下文、顺序；不只修改 planner 的计数或标签。
- 重复原文组排序、译文集合、key/context 证据及状态共识只预计算一次，普通匹配和物化查共享索引，
  避免每个本地 key 重扫/重哈希整个同原文组。
- `src/transbridge/ui/operations/batch_download_view.py`、`download_run.py`：增加默认 False 的 `retryable`，
  区分已完成、真实失败来源及尚未提交计划；已保存部分仍保留失败项重试，不二次下载成功内容。
- `batch_download_session.py`：汇总实际未完成来源状态，零差异完整来源可明确完成。
  原有自动保存、还原点、版本/取消保护及部分保存后的复核保护保持。
  每源详情列出自动原文匹配、同译文合并、无匹配原文/无译文及真正复核原因；完成回执保留这些详情。
  未变更计数只算本地已有记录，不把额外云端行计为本地未变更。
  批量路径接入既有结构化计划诊断，失败保留异常日志但不阻断内容计划和保存，不写翻译正文或凭据。
- `batch_download_dialog.py`：显式完成/重试状态；已保存或已完成按钮禁用，普通 slot 重复调用同样无效。
  真失败仍可原地重试；改变来源/策略复位，接受复核先清完成态再自动运行；迟到取消不翻转既成回执。
- `sync_review_projection.py`、`paratranz/sync_diagnostic_reasons.py`：新原因提供准确中文解释，
  不把 metadata、原文缺失或无译文包装为“你必须选择”的普通冲突。
- 新增 `tests/contracts/paratranz/test_content_download_matching.py`：30 项新内容合同，覆盖 key 漂移、
  多 key 复用、metadata/状态差异、现译证据、空译文、无匹配、来源隔离、限定身份、新编辑、
  严格/上传/双向默认合同、内容失效物化及重复原文复杂度。
- `tests/contracts/paratranz/test_batch_download.py`：原“无关第三条编辑”fixture 与其他目标共用原文 Source，
  新模式下理应可匹配，故仅将该第三条改为真正无关原文；防覆盖断言不变，不用改断言掩盖失败。
- 新增 `tests/ui/operations/test_batch_download_feedback.py`：3 项真实 Session/持久化回归，
  覆盖无原文匹配保留本地且不复核、原文回退/远端余项计数、诊断失败仍能保存。
- `test_download_run.py`：部分成功/待确认情况下失败来源仍可重试，缺文件不伪造保存回执。
- `test_unified_download_service.py`：扩至 33 项，默认 facade→真实 Qt/worker/Service/API/文件网关→磁盘，
  仅替换 HTTP。覆盖原文/译文自动处理、metadata 差异、多 key 无 ID 复用、真正歧义和现译证据、
  成功按钮禁用、禁用点击无副作用、选择变化恢复、失败仅读未完成文件及保存失败复用还原点。
- `test_batch_download_dialog.py`、`test_batch_download_production.py`：真实完成/重试/复核/取消及入口合同更新，
  不只改按钮文字。需求、ADR、开发说明、当前计划及直接索引同步，历史增量不改。

## 先失败后通过的证据

- 新领域合同首跑 19 项中 15 失败/4 通过，验证 key 漂移、元数据伪冲突、内容复用及无解原文复核问题；
  最终领域与执行/回滚等 186 个不同用例通过。
- 真实默认 UI 完成后仍可二次点击、远端 key 变化后原文不能回退均先实跑失败。
  metadata 场景最初只有一个云端上下文不同，旧规则已能唯一收窄，首跑通过；随后使用两个均不同于本地的
  云端上下文、相同原文/译文且 stage=1/5，先确实失败，修后真实保存通过，不虚构修前失败证据。
- 1000 个同原文不同 key 的完整 plan+物化回归：单译文与双译文可按 key 确定两组，
  canonical_hash 各 8,003 次、identity 校验 4,000 次、review 构造 0 次，均满足线性测试边界。
  单次约 0.12 秒仅为本机专项观察，不是五轮性能对比，也不承诺真实项目固定耗时。
- 离线真实 Qt 完成态截图核对：代表性 93,538 条回执显示灰色禁用“已保存”，部分失败显示可用重试。
  该截图为受控模型视图验证，不是重新下载用户的 93,538 条。

## 最终验证

Windows、现有 Python 3.12.12/uv 环境，未设置 FAISS_OPT_LEVEL。
TEMP/TMP、缓存及测试输入均位于有 owner 标记的本任务独占目录。

```powershell
uv --cache-dir .tmp-content-sync-20261025/uv-cache run --no-sync pytest tests/ui/operations tests/ui/test_download_plan_routing.py tests/application/projects tests/contracts/paratranz tests/persistence/v2 tests/paratranz tests/ui/test_main_window_shell.py tests/ui/test_main_window_coordinators.py tests/ui/test_intent_router.py tests/ui/test_workbench_theme_migration.py -q --ignore=tests/contracts/paratranz/test_terms_api_contract.py --ignore=tests/paratranz/test_typed_client.py -k 'not test_reordered_source_projects_into_context_only_remote_plan' --basetemp .tmp-content-sync-20261025/final-integrated
uv --cache-dir .tmp-content-sync-20261025/uv-cache run --no-sync ruff check src tests
uv --cache-dir .tmp-content-sync-20261025/uv-cache run --no-sync ruff format --check src tests
```

**1,701 passed、1 deselected**，130.61 秒，退出码 0，4 条既有弃用 warning。
全库 Ruff check 通过，格式检查 2,193 文件通过。专项 186/144/33 等结果与整合重叠，不重复累加。
沿用 S16 已复现的旧上传排序断言排除；受沙箱本地 HTTP server 限制的 `test_typed_client.py` 未重跑，
不相关 `test_terms_api_contract.py` 未运行。未运行全库 pytest、真实云端操作、安装包构建或正式性能基准。

## 兼容与清理

内容模式默认关闭，由当前工程批量同步启用；独立旧严格调用、上传和双向同步不扩大语义。
新 DTO 字段有默认值，不改 schema、依赖、源文件、本地 key、云端布局和数据迁移。
保留用户其他工作线变更；本轮临时基线、截图、JUnit 和缓存确认所有权后清理。
无已知未解决的本轮业务缺陷，真实 144 条的最终采用/保留数量仍以运行新代码后的实际内容为准。
