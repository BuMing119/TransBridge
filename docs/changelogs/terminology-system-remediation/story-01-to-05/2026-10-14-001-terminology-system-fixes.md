# 术语构建、方案、同步与检索修复

- 日期：2026-10-14
- Epic：terminology-system-remediation
- Story：S01～S05
- 计划：[plan.md](../../../../plans/terminology-system-remediation/plan.md)
- 范围：修复当前审查确认的九类缺陷；本地完成，未提交、推送或访问真实远端写接口。
- 归属：用户既有 `pyproject.toml`、`uv.lock` 的 0.2.1.5 版本变动未修改，不计入本记录。

## 行为结果

非空候选现在可以进入草稿；重建保留人工新增、已确认译名、抑制、作用域和无证据决定，同时向已有草稿加入新候选。已审查证据未新增时保持人工确认，新增矛盾证据仍要求复核。Build 和草稿同事务保存，避免失败留下半次构建。

发布同时验证构建时输入和提交时状态：来源字节、注册、适配器、项目、版本或配置变化后必须重新构建；正常草稿编辑不影响输入证明。切换工程、Variant 或关闭工程会关闭旧术语窗口和方案管理器，旧动作及晚到的来源结果不能进入新工程。预览和写出复用完整投影，缺失映射时一致保留整条项目译文，明确条目 override 优先生效。

ParaTranz 同内容认领持久化关联与共同基线，不发送远端写请求；首次全失败后的有效重试可以恢复，真实目标、版本、远端内容变化仍拒绝。关闭检索后不再直填、注入、查询向量或更新/抽取术语，包括部分响应及截断递归。有效术语按不可变快照建立规范原词索引，避免每个候选重复扫描全库。

## 逐文件变更

以下路径均相对于仓库根目录。

### S01：构建、草稿与发布

- 新增 `src/transbridge/application/terminology/build_drafts.py`：`decisions_from_build` 使用 keyword-only 身份参数和既有 baseline 规则；`BuildDraftCoordinator` 合并证据、候选及人工决定，并受 draft revision/base 保护。
- 新增 `src/transbridge/application/terminology/build_freshness.py`：持久化源/配置输入摘要，排除可合法变化的草稿及有效版本指针；旧证明缺失或当前输入变化时提供明确重建提示。
- 修改 `src/transbridge/application/terminology/build.py`：构建结果和内容摘要携带输入证明。
- 修改 `src/transbridge/application/terminology/models.py`：`BuildResult.input_digest` 为可选字段，旧序列化数据按默认值读取。
- 修改 `src/transbridge/application/terminology/identity.py`：build-key schema 升为 v2，重建不与旧不可变构建记录碰撞。
- 修改 `src/transbridge/application/terminology/conflicts.py`：提取可复用的 `reconcile_evidence`，保持原证据协调语义。
- 修改 `src/transbridge/application/terminology/publish.py`：`VersionPublisher` 验证持久化构建输入证明。
- 新增 `src/transbridge/bootstrap/terminology_build.py`：从 workloads 提取 `BuildRunner`，优先使用当前草稿基线；构建回交前验证捕获的 draft/base 并协调保存。
- 新增 `src/transbridge/bootstrap/terminology_draft_access.py`：共享生产生命周期版本线读取，供手工草稿和构建事务使用。
- 修改 `src/transbridge/bootstrap/terminology_workloads.py`：删除旧建稿实现和已提取职责；发布/回滚命令在提交前校验构建输入。
- 修改 `src/transbridge/bootstrap/terminology_storage.py`：expected state 使用与构建相同的输入证明，保留提交时源字节与 revision 检查。
- 修改 `src/transbridge/bootstrap/terminology.py`：向提取后的 runner 注入生命周期依赖。
- 新增 `src/transbridge/persistence/terminology/builds.py`：独立保存不可变构建及子记录，供现有事务复用。
- 修改 `src/transbridge/persistence/terminology/repository.py`：构建存储委托给 `BuildStore`；草稿事务可携带同次构建。
- 修改 `src/transbridge/persistence/terminology/draft_transactions.py`：在同一 `BEGIN IMMEDIATE` 中验证版本线并写 build/draft，失败共同回滚。
- 修改 `src/transbridge/persistence/terminology/publish.py`：SQLite 发布事务再次验证构建输入证明。
- 新增 `tests/application/terminology/test_build_drafts.py`：候选成稿、人工/抑制/无证据保留、新候选及并发草稿保护。
- 新增 `tests/application/terminology/test_build_freshness.py`：合法编辑、源/配置变更、旧 payload 可读但拒绝新发布。
- 新增 `tests/integration/bootstrap/test_terminology_build_review.py`：真实 TaskRuntime、SQLite 重开、人工编辑发布、已有草稿更新、两阶段来源变化及 build/draft 共同回滚。
- 修改 `tests/application/terminology/story08_support.py`：发布夹具提供对应构建证明。
- 修改 `tests/integration/bootstrap/test_terminology_task_wiring.py`：对“无证明先重建”的命令行为更新断言。

### S02：窗口上下文与一致投影

- 新增 `src/transbridge/application/terminology_profiles/entry_projection.py`：`ProfileEntryProjector` 统一基础快照、缺失映射、override 和插件作用域规则，使用有效术语索引。
- 修改 `src/transbridge/application/terminology_profiles/projection.py`：提取 `SourceTermIndex` 复用原词候选分桶，保持词边界、Unicode、标点子串识别语义。
- 修改 `src/transbridge/application/terminology_profiles/write_projection.py`：导出使用共享完整投影，删除重复规则。
- 新增 `src/transbridge/ui/tools/terminology/context_binding.py`：绑定固定 Project/Variant，身份变化立即禁用并关闭，关闭时断开共享信号。
- 修改 `src/transbridge/ui/tools/terminology/window.py`：使用上下文绑定器，关闭时结束方案交互，发布完成刷新预览。
- 修改 `src/transbridge/ui/tools/terminology/schemes_controller.py`：关闭时断开状态监听并释放来源导入控制器。
- 修改 `src/transbridge/ui/tools/terminology_profiles/source_import_controller.py`：关闭后拒绝启动、晚到结果和对话框确认后的写入。
- 修改 `src/transbridge/ui/workbench/step2.py`：预览使用完整共享投影，基础快照改变时重新生成显示结果。
- 修改 `src/transbridge/ui/workbench/terminology_profile_controller.py`：注入基础快照；工程/版本变化关闭旧管理器。
- 新增 `tests/application/terminology_profiles/test_entry_projection.py`：候选索引与原全量识别的行为对照。
- 新增 `tests/ui/tools/terminology/test_context_isolation.py`：工程/Variant/关闭、旧操作和晚到结果隔离。
- 新增 `tests/ui/workbench/test_terminology_projection_consistency.py`：基础状态、缺失映射、override、插件优先级及预览导出一致。
- 修改 `tests/ui/workbench/test_terminology_profile_switching.py`：注入基础快照并验证基础版本变化会刷新。

### S03：同步认领与失败重试

- 修改 `src/transbridge/application/terminology_sync/executor.py`：ADOPT_LINK 作为本地确认成功持久化 ID、revision、observed/common digest；重试统一调用输入校验。
- 新增 `src/transbridge/application/terminology_sync/execution_retry.py`：提取 token 构建和校验；零成功重试只允许本次执行产生的 baseline revision 变化，继续保护确认项和待处理远端项。
- 修改 `tests/application/terminology_sync/test_backup_executor.py`：认领后规划 UPDATE_REMOTE、全失败重试、已成功项不重复写及真实状态变化拦截。

### S04：检索开关与匹配索引

- 修改 `src/transbridge/application/terminology/effective.py`：抽取共享作用域解析，增加 `EffectiveTerminologyIndex`，索引解析与原解析保持同一语义。
- 修改 `src/transbridge/ai_translator/project_terminology_adapter.py`：按完整快照身份缓存索引，锁内替换；每次新鲜合并 legacy 数据，插件上下文单独解析。
- 修改 `src/transbridge/ai_translator/term_database.py`：公共读取、精确匹配、向量操作统一遵守 retrieval_enabled，关闭时清除已载入的匹配状态。
- 修改 `src/transbridge/ai_translator/translator.py`：普通、部分响应和递归批次遵守检索开关，阻止动态术语写入及对话术语抽取。
- 新增 `tests/ai_translator/test_effective_term_index.py`：作用域/抑制/别名一致性、快照替换、legacy 更新、并发与确定性复杂度回归。
- 新增 `tests/ai_translator/test_retrieval_disabled.py`：真实 manager 和翻译流程覆盖完整/部分/截断响应，确认无直填、注入、动态库和抽取副作用。
- 修改 `tests/integration/terminology/test_published_translation_consumption.py`：补齐绕过初始化的 translator 夹具配置，继续验证发布到真实匹配消费者链路。

### S05：计划与记录

- 新增 `plans/terminology-system-remediation/plan.md`：九类缺陷的边界、验收与完成证据。
- 修改 `plans/INDEX.md`、`docs/changelogs/INDEX.md`：只增加本计划和记录的索引项。
- 新增本文件：记录实际改动、验证和兼容性。

## 验证证据

使用现有 uv 环境和 lockfile，未同步或升级依赖。常规 `uv` 默认缓存及系统临时目录在沙箱中不可写，因此命令统一使用任务专属缓存；涉及本机 HTTP/文件链接的联合测试经授权在本机运行，`tempfile.tempdir` 定向到任务目录，不调用真实服务。

最终联合验证通过 `uv --cache-dir .tmp-terminology-fixes-20261014/uv run --no-sync python -` 执行以下 `pytest.main` 包装器：

```python
from pathlib import Path
import tempfile
import pytest

scratch = Path('.tmp-terminology-fixes-20261014/final-system-temp').resolve()
scratch.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(scratch)
paths = [
    'tests/application/terminology', 'tests/application/terminology_profiles',
    'tests/application/terminology_sync', 'tests/application/translation',
    'tests/persistence/terminology', 'tests/ai_translator',
    'tests/contracts/terminology', 'tests/contracts/terminology_sync',
    'tests/contracts/translation', 'tests/contracts/io/test_terminology_profile_write.py',
    'tests/contracts/paratranz/test_terms_api_contract.py',
    'tests/integration/terminology', 'tests/integration/terminology_sync',
    'tests/integration/bootstrap', 'tests/integration/translation',
    'tests/ui/workbench', 'tests/ui/tools/terminology',
    'tests/ui/tools/test_ai_project_terminology.py',
    'tests/ui/tools/test_terminology_source_import_controller.py',
    'tests/ui/tools/test_terminology_source_import_dialog.py',
    'tests/ui/tools/test_terminology_profile_manager.py',
    'tests/ui/operations/test_strict_write_routes.py',
    'tests/integration/publish/test_plugin_transfer.py',
    'tests/smart_assistant/tools/test_terminology_sync_tools.py',
]
raise SystemExit(pytest.main([
    *paths, '-m', 'not llm and not slow', '-q',
    '-o', 'addopts=--tb=short --strict-markers',
    '--basetemp=.tmp-terminology-fixes-20261014/final-tests',
]))
```

结果：**1275 passed、1 skipped、1 deselected，41.85 秒**。跳过项要求专用真实 ParaTranz 测试账户；排除项为报告 slow 测试。首次组合运行的 1 项失败由缺失 `_cfg` 的手工构造夹具造成，补齐真实配置后复验通过。

最后补充人工确认保留及检索关闭的部分/截断分支后执行：

```text
uv --cache-dir .tmp-terminology-fixes-20261014/uv run --no-sync python -m pytest tests/ai_translator/test_retrieval_disabled.py tests/integration/bootstrap/test_terminology_build_review.py tests/application/terminology/test_build_drafts.py tests/application/terminology/test_conflicts.py --basetemp=.tmp-terminology-fixes-20261014/final-boundary-tests -q
```

结果：**29 passed**。独立审查另运行构建/发布 27 项聚焦回归，并验证 UNIFIED、PLUGIN_EXCEPTION、IGNORED 冲突决定的重建与 materialize，以及新增第三译名撤销旧解决状态。各批次包含重复测试，不累加宣称全新测试数量。

```text
uv --cache-dir .tmp-terminology-fixes-20261014/uv run --no-sync python -m ruff check src tests
uv --cache-dir .tmp-terminology-fixes-20261014/uv run --no-sync python -m ruff format --check src tests
git -c core.safecrlf=false diff --check
```

均通过；格式检查 **1634 files already formatted**。仅观察到现有 SWIG 和 collection compatibility facade 弃用警告。

性能及独立对照：同一 6000 条术语/5 条文本、关闭语义检索的静态来源微基准，由修复前约 **3.221 秒**降至 **0.214 秒**；包含真实 FrozenEffectiveTerminologyPort 快照验证时为 **0.648 秒**。这是本机微基准，不代表完整翻译或正式发布压力门槛。旧合并算法与新索引 **600 例**完全一致；混合快照/插件上下文 **400 次**并发读取无串线。两种条目类别的部分及截断恢复验证已纳入永久回归。

## 兼容性、职责与遗留

- 不改写发布历史，不修改 SQLite schema，不需要用户数据迁移。旧 BuildResult 可读取用于历史与报告，但无法证明输入状态时必须重新构建后再发布。
- 新协调、输入证明、构建存储、完整投影、上下文守卫和 retry 职责独立提取。既有超大 `term_database.py`、`translator.py`、`step2.py`、`window.py` 仅作局部 guard/委托修改；Step2 从 719 减至 701 行，workloads 和 executor 均缩减。保留原有接口与测试，不在本次修复中扩大重构；若后续增加新责任，应先按独立数据流拆分。
- 未运行全仓库测试、报告 slow 压力测试、真实 LLM 或真实 ParaTranz 账户写入；相关本地 HTTP 合约、Qt、SQLite、导出与翻译集成已验证。
- 本轮九类问题无已知未完成项；不将这些测试结果等同于未执行的正式发布门槛。
