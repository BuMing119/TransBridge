# Story 06：AI 运行术语快照固定与 legacy 回声过滤

- **所属 Plan**：[项目术语库与 ParaTranz 备份/双向同步计划](../plan.md)
- **状态**：已确认
- **追溯**：FR5.17.2、FR5.17.3；FR5.2、FR5.13.4、FR5.16 S10
- **前置依赖**：FR5.16 `EffectiveTerminologyPort`；精确回声过滤依赖 [Story 02](story-02-sync-line-baseline-persistence.md)
- **下游调用方**：GUI AI translator、Smart Assistant translator/postprocess、S08 跨批次一致性验证

## 目标

在 AI 任务被接纳并创建不可变运行档案时，一次性固定 Project/Variant 的实际术语 snapshot和可验证 identity。翻译、润色、混合、自定义工作流的全部阶段/批次只使用该 frozen binding；运行中发布/恢复/同步或切换 Variant不能改变它。同时在存在项目已发布版本时，按同步 baseline过滤 ParaTranz legacy回声，但保留独立远端 fallback。

## 原始验收标准

- 用户确认 AI 预检并创建运行档案时，一次性读取 `EffectiveTerminologySnapshot`，将 Project/Variant/version ID/content digest/status 写入不可变 run spec，并把同一已验证 snapshot binding 传给全部批次/阶段。
- 翻译、润色、混合和自定义任务运行中发布/恢复其他术语版本、同步 ParaTranz 或修改长期配置，均不改变该任务的术语 snapshot identity 和匹配结果；后续新任务读取新版本。
- 运行中不能读取已固定 version 或摘要不匹配时安全失败；已持有的不可变内存 snapshot 可继续使用，但不得切到 current/legacy。
- 存在已发布项目版本时，S02 baseline 证明为该版本回声的 ParaTranz legacy term 在来源合并前过滤；独立远端项仅在未被项目版本覆盖/抑制且用户配置允许时 fallback。
- 没有已发布版本时保持 FR5.2 的 JSON/CSV/Excel/ParaTranz/dynamic 优先级和现有 AI 行为；本 Story 不触发网络同步。

## 当前调用链与缺口

- `AiRunSpec` 当前只保存 input/config/profile/capability digest，不含术语 snapshot。
- `RunController.begin()` 先构造 spec并提交 TaskRuntime；`start_translation_run()`、`start_mixed_run()` 等随后调用 `resolve_project_terminology(ctx)`，因此术语解析发生在运行档案之后。
- `_MixedWorker` 在没有注入 binding时会在不同阶段再次调用 `resolve_project_terminology()`，可能跨版本。
- `ProjectTerminologyBinding` 只保存 adapter + 无 version ID 的 `TerminologyLookupContext`；adapter读取 current snapshot。
- Smart Assistant 的 `tool_translator.py` 和 `_postprocess_tool_runtime.py` 各自在执行前解析 binding，但没有共享 run snapshot ref。
- `TermDatabaseManager` 超过 896 行；回声过滤必须通过注入 policy/窄 helper实现，不能把 sync repository查询塞入主类。

## 运行档案与 frozen binding

计划新增 application 层结构：

- `TerminologyRunSnapshotRef`：local Project/Variant、`EffectiveSnapshotStatus`、可选 version ID/content digest、snapshot identity、captured_at。
- `FrozenTerminologyRunSnapshot`：ref + 排序后的不可变 `TermDecision` tuple。只有运行内存/受控 checkpoint持有内容，不要求把全部术语塞入 TaskRuntime metadata。
- `TerminologyRunSnapshotFactory.freeze(project_id, variant_id)`：读取一次 effective snapshot，校验 Project/Variant、version/digest和内容摘要，返回 frozen值。
- `FrozenEffectiveTerminologyPort`：只从 frozen snapshot执行 `snapshot/resolve`，忽略 current pointer；请求其他 version时拒绝。
- `FrozenProjectTerminologyBinding`：frozen adapter、带显式 `version_id` 的 lookup context、run ref和 legacy filter policy。

`NO_PROJECT_VERSION` 是明确、可序列化的运行状态；`UNAVAILABLE/CORRUPT` 在已存在项目上下文时使 preflight失败，不静默降级。没有 Project/Variant 的 legacy流程保持现状。

## 事件顺序

```text
AI preflight success
  → freeze exact effective snapshot / explicit NO_PROJECT_VERSION
  → build AiRunSpec(terminology_snapshot_ref)
  → TaskRuntime submit/start
  → workers receive same FrozenProjectTerminologyBinding
  → terminal success/failure/cancel/stop
```

任务恢复时按 ref读取 exact version并校验 digest；如果 checkpoint已保存可信 frozen snapshot可恢复该内容，否则安全失败，绝不改读 current。

## legacy 回声过滤规则

- 仅在 run ref 为 READY 且 source为 ParaTranz时应用 baseline filter。
- 使用 S02 confirmed item link的 remote ID + local term ID + local version/digest证明回声；仅同词/同译文不足以排除。
- 当前已发布 snapshot 覆盖的 sync echo在 legacy merge前排除，避免参与优先级、冲突和 prompt。
- 项目 version中的 suppressed decision继续由 `ProjectTerminologyAdapter` 阻止同作用域 fallback；即使远端旧副本未删除也不能复活。
- remote ID不在 baseline、line/target/version不匹配或 link outcome unknown的术语不是已证明回声，仍按用户 FR5.2 source配置作为未覆盖 fallback。
- legacy filter使用 run创建时捕获的 baseline revision/snapshot，不在批次间重新查询。

## 依赖有序的实施步骤

1. 新建 `application/translation/terminology_run_snapshot.py` 定义 ref/frozen snapshot/factory/errors；复用 `EffectiveTerminologySnapshot` 验证，不导入 UI或 term database。
2. 实现 `FrozenEffectiveTerminologyPort`，可直接包装 captured decisions；对 version不匹配或 digest重算失败抛稳定 `TerminologyRunSnapshotError`。
3. 扩展 `ProjectTerminologyBinding` 或新增 frozen subtype，使 `translator_kwargs()`/`term_database_kwargs()` 始终携带显式 version context和 legacy filter；保留旧 `resolve_project_terminology()` 只供未迁移兼容调用。
4. 在 `AiRunSpec` 增加 `terminology_snapshot` ref并纳入 config/input fingerprint以外的独立运行档案字段；`runtime_job_spec()` metadata只保存安全 identity/digest/status。
5. 给 `RunController` 注入 snapshot factory。`begin()` 在首次/重建 runtime run ID时复用同一 frozen snapshot，不因第二次 `build_run_spec()`重新读取 current。
6. 将 `TranslationRunRequest` 扩展为持有 frozen binding；`start_translation_run()`、`start_mixed_run()`、polish、batch只从request取 binding，删除 worker内部 current fallback。
7. Smart Assistant在提交 translator/postprocess TaskRuntime前调用相同 factory并把 ref/binding放入 request；将 shared helper放入新模块，`tool_translator.py`只做窄委托。
8. 新建 `ai_translator/legacy_term_policy.py` 的 `LegacyTermFilterPort`/`ProjectTerminologyEchoFilter`；由 S02 snapshot port构造冻结 filter输入。
9. 在 `TermDatabaseManager` source合并点增加一个可选 `legacy_term_filter`委托。filter在 list加载后、冲突/priority/prompt/vector前执行；默认 None完全保持旧行为。
10. 为 checkpoint/retry定义恢复规则：ref可读取 exact version且digest一致则重建 frozen binding；否则运行失败并给出可行动诊断。

## 文件变更清单

- **新增** `src/transbridge/application/translation/terminology_run_snapshot.py`。
- **新增/修改** `src/transbridge/ai_translator/project_terminology_runtime.py`、`legacy_term_policy.py`、`project_terminology_adapter.py`。
- **窄修改** `src/transbridge/ai_translator/term_database.py`：注入/调用 filter，不查询 sync state。
- **修改** `src/transbridge/ui/tools/ai_translator/run_spec.py`、`run_controller.py`、`_mixed_worker.py`、`polish_runtime.py`、`batch_runtime.py` 和相应 worker构造点。
- **新增** Smart Assistant shared run helper，并最小修改 `tool_translator.py`、`_postprocess_tool_runtime.py`。
- **新增/更新测试** `tests/application/translation/test_terminology_run_snapshot.py`、`tests/ai_translator/test_project_terminology_runtime.py`、`test_legacy_term_policy.py`、AI run/controller/worker/Smart Assistant相关测试。

## 边界条件与错误处理

- Project/Variant存在但 effective snapshot为 CORRUPT/UNAVAILABLE：preflight失败；不能用 legacy掩盖损坏的权威版本。
- NO_PROJECT_VERSION：ref明确记录状态，允许 legacy；后续运行中发布首个version也不改变该run。
- frozen snapshot内容digest在capture时不一致：不创建run spec。
- 运行内存已有frozen snapshot时，后台数据库暂时不可读不影响当前批次；需要进程恢复且无法验证exact version时安全失败。
- baseline filter snapshot不可用：项目权威version仍可用，但无法证明remote echo时应避免把疑似回声加入独立legacy权威；返回明确诊断并按确认后的fail-closed策略处理，不猜测。
- filter只处理ParaTranz source，不能排除用户JSON/CSV/Excel中的同词项，除非现有项目scope覆盖/抑制规则本就阻止。
- 打开Project、切换Variant、创建run都不能调用术语写API；freeze只读本地effective和baseline。

## 测试策略与建议命令

- factory/frozen port：READY/NO_VERSION/CORRUPT/UNAVAILABLE、digest mismatch、exact version读取和内存继续。
- 四种mode多批次：run开始后publish/restore/sync/Variant switch，前后snapshot identity和匹配结果一致；新run使用新version。
- worker：移除binding fallback后缺失binding安全失败或明确legacy状态，不重新解析ctx。
- echo filter：confirmed remote ID回声排除、独立remote保留、unknown link不误排、suppression防复活、跨Project/Variant/target隔离。
- compatibility：无Project/no published version时FR5.2 source顺序、动态库和vector identity不变。
- no-network spy：open/switch/start AI均无term write call。
- 建议命令：`uv run pytest tests/application/translation/test_terminology_run_snapshot.py tests/ai_translator/test_project_terminology_runtime.py tests/ai_translator/test_legacy_term_policy.py tests/ui/tools/ai_translator tests/smart_assistant/tools/test_run_postprocess.py -q` 的相关筛选集。

## 风险、回退与未决问题

- 大snapshot复制可能增加运行内存；共享同一frozen tuple/adapter给全部batch，禁止每批deepcopy。性能在S08测量。
- `AiRunSpec`目前位于UI包，长期可迁移到application run archive；本Story只增加application-owned ref，避免继续把业务模型绑定Qt。
- 回退必须停用新run创建或保留已捕获binding，不能让新代码生成的run中途切回current/legacy。
