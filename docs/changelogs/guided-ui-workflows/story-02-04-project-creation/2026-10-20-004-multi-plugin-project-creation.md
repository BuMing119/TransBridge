# 创建工程时多选插件并导入同一工程

- **日期**：2026-10-20
- **Epic / Story**：`guided-ui-workflows` / S02、S04
- **需求**：[FR26.3、FR28.1](../../../requirements.md)
- **计划**：[plan](../../../../plans/guided-ui-workflows/plan.md)、[S02](../../../../plans/guided-ui-workflows/stories/story-02-authoritative-project-provisioning.md)、[S04](../../../../plans/guided-ui-workflows/stories/story-04-start-center-guided-project.md)
- **前序记录**：[003 统一建项与当前工程加插件入口](2026-08-30-003-统一建项与当前工程加插件入口.md)
- **验证证据**：[专项 QA](../../../test-reports/guided-multi-plugin-project-qa-2026-10-20.md)
- **类型**：功能扩展、回归验证与 BM 文档补齐

## 原因与用户可见结果

此前工作台允许为当前工程批量添加插件，但开始页的“选择插件”使用单选窗口，建项请求与提交后的界面数据也只携带一个原始来源。用户明确要求创建时一次选择多个插件，全部导入同一个工程。

现在可同时选择 ESP、ESM、ESL，建项页按首个插件建议工程名，展示数量和可滚动的文件列表。一次“确定”在后台准备全部来源并原子提交；成功后所有插件进入同一工程及默认版本。任何来源失败均保留草稿，解析诊断指明文件，持久化失败不会留下部分工程。

## 实现与测试变更

- **修改 `src/transbridge/application/projects/provisioning.py`**：服务委托独立的来源准备模块，保存全部原始来源 hydration，并在成功提交后按 owner 一次性交付。原公开导入路径保留。
- **新增 `src/transbridge/application/projects/provisioning_models.py`**：从原模块提取不可变契约和校验；请求与 hydration 均保留 `source`，增加 `additional_sources` 和统一 `sources` 访问。额外原始来源进入请求 fingerprint。
- **新增 `src/transbridge/application/projects/provisioning_sources.py`**：集中准备所有原始插件和已有译文，拒绝规范化重复路径及重复原始身份；按 namespace 将对应插件译文合并至各自基线，按规范化路径建立正确来源关系。来源解析异常保留原错误类别、代码和 cause，并带失败路径。该拆分避免继续扩张原超长 provisioning 模块。
- **修改 `src/transbridge/ui/coordinators/guided_project_coordinator.py`**：新增 `begin_sources()`、`additional_source_paths` 和 `source_paths`，保留 `begin(path)`；新草稿递增 revision，防止编辑后重选仍显示旧来源。完整来源集合进入同一建项请求。
- **修改 `src/transbridge/ui/shell/start_center_controller.py`**：文件窗口改为 `getOpenFileNames()`，多个路径交给同一 coordinator；成功后逐来源加载 hydration，复用 `slot_from_hydration()` 保留身份、字符串 ID 和源元数据。
- **修改 `src/transbridge/ui/shell/start_center.py`**：多源草稿显示插件数量、有界滚动文件列表、完整路径提示和无障碍文本；单源与空工程沿用既有展示。该文件原有的输入焦点修复属于其他工作线，不计入本次功能归属。
- **修改 `src/transbridge/ui/shell/start_center_landing.py`**：主入口说明明确可多选并导入同一个工程。
- **修改 `tests/application/projects/test_provisioning.py`**：补充多原始来源一次提交、一次 hydration 消费、重复路径/身份拒绝、失败保留原工程、额外来源 fingerprint 覆盖；调整 fixture 使多个 primary 拥有独立身份。
- **新增 `tests/persistence/v2/test_project_provisioning_multiple_sources.py`**：用生成的真实 ESP/ESM/ESL fixture 验证同工程保存重开、保持原文与未译状态、只解析一次、第二插件损坏无副作用、三处事务故障回滚及两个迁移对不串联。
- **修改 `tests/ui/test_start_center_guided_project.py`**：原单来源 hydration 测试适配集合返回及标准格式/字符串 ID 字段，保留原先“不重复解析”的断言。
- **新增 `tests/ui/test_start_center_multi_plugin.py`**：覆盖多选三种插件、一次请求/提交、全部 collection 加载、取消/重选、失败保留参数、独立 EntryKey 与字符串 ID，以及 50 个文件名的滚动列表布局。

## 文档与索引补齐

- **修改 `docs/requirements.md`**：在 FR26.3 原条目中明确多选同工程、原子性、文件列表、失败文件及兼容验收；FR28.1 引用同一多选行为，需求历史补录本次变更。既有 FR13/FR19 等其他改动不归入本记录。
- **修改 `plans/guided-ui-workflows/plan.md`**：在现有 S02/S04 更新验收、文件落点、验证和回退边界，关联专项 QA；不新增 Epic、改变历史完成数量或重算历史性能结果。
- **修改 `stories/story-02-authoritative-project-provisioning.md`**：用当前多来源请求、来源准备切片、单次原子提交及全量 hydration 替换过时的单主来源与待实现描述；消除不存在的 `create_project` 接口及已解决的解析时机未决项。
- **修改 `stories/story-04-start-center-guided-project.md`**：对齐多选 draft、单次确认、取消/重选及全部来源加载；两份 Story 保留原文档“草稿”状态，另记实际实现与验证日期。
- **修改 `plans/guided-ui-workflows/ux-contract.md`**：修正建项相关 canonical intent 为 `project.create`，区分当前工程的 `workbench.content.prepare`，明确空工程本地子选择、多选摘要和失败恢复。
- **修改 `plans/guided-ui-workflows/wireframes.md`**：更新多选入口、滚动列表、默认版本、单次“确定”和状态表；不保留未实现的来源重选按钮或两阶段人工确认。
- **修改 `plans/guided-ui-workflows/journey-inventory.md`**：只更新 J01 现行交付目标及证据链接，历史基线和测量数字不变。
- **新增 `docs/test-reports/guided-multi-plugin-project-qa-2026-10-20.md`**：记录本轮命令/退出码、承接的实现阶段证据、环境重试与未运行项。
- **修改 `plans/INDEX.md`、`docs/INDEX.md`、`docs/changelogs/INDEX.md`**：仅补本 Epic 的多插件 QA / 增量链接和相关条目；既有增量文件未改。

## 验证

实现阶段执行：

```powershell
uv run pytest tests/application/projects tests/persistence/v2 -q --basetemp .tmp-multi-source-backend-regression
```

结果：退出码 0，**469 passed**。相关 UI 六个文件分组执行且去重后 **54 项通过**；50 与 35 两次运行存在重叠，不重复累计。精确 UI 命令见专项 QA。

本轮补档定向复核：

```powershell
uv run --offline --no-sync pytest tests/application/projects/test_provisioning.py tests/persistence/v2/test_project_provisioning_multiple_sources.py tests/ui/test_start_center_multi_plugin.py -q --basetemp qa-tmp-multi-plugin-docs-20261020/pytest
```

结果：沙箱首跑的 persistence fixture 遇到 Windows 临时目录 ACL，受审查重试后退出码 0，**27 passed**。本次 11 个源码/测试文件的 Ruff lint 与格式检查均退出 0。

```powershell
uv run --offline --no-sync ruff check --no-cache src tests
uv run --offline --no-sync ruff format --check --no-cache src tests
```

本轮检查时两条命令均退出 1：其他同步 UI 工作线有 3 项 lint 问题、4 个文件格式问题，具体文件见 QA；本任务未修无关代码，也未宣称全仓通过。补档另检查相对文件链接、文档重复/过时合同与 `git diff --check`。

## 兼容性与遗留项

- 不改变 V2 schema、依赖、版本或安装包；单源调用、空工程和旧多来源工程继续兼容，无数据迁移要求。
- 本轮未重新编码；记录的 11 个实现/测试文件来自本聊天上一轮已完成工作。共享工作区的助手、会话、表格、同步 UI 等其他改动不归入本记录。
- 未人工操作原生 Windows 文件选择器，未用用户真实插件测量批量解析/逐来源界面刷新性能；50 个文件名的离屏布局测试不等于大插件性能验证。
- 本轮未重跑全仓 pytest 或制作发行包；定向验证及历史模块回归证据已保留。任务自有临时目录已清理，未清理其他工作线产物。
- 未执行 Git commit、push 或发布。
