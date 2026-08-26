# 术语格式最大化兼容

> **状态**：已完成（2026-08-26，相关 QA 通过）
> **对应 ADR**：[ADR-027](../../docs/adr/027-canonical-terminology-format-adapters.md)

## 目标

让动态术语库、ParaTranz、本地 JSON、CSV 和 Excel 通过同一规范模型互操作：已知字段完整映射、未知字段尽量保留、旧文件继续可读，并让 CSV 成为正式的可配置术语来源。

## 非目标

- 不改变术语来源覆盖顺序的语义、匹配算法、语义召回或逐条术语绑定。
- 不自动把本地文件上传到 ParaTranz，不执行远端写入。
- 不承诺 ParaTranz 不接受的只读字段可以回写到其 API。
- 不依赖本机安装 Excel 或 LibreOffice；`.xlsx` 与旧式 `.xls` 均由内置 Python 适配器读取。

## 当前事实与约束

- `term_database.py` 为 760 行，已经超过仓库强制拆分阈值，格式解析必须提取到独立模块。
- 当前 `TermEntry` 不含 ParaTranz 的 `pos`、`note`、服务端标识或未知元数据。
- 本地 JSON 只保留原文、译文和变体，CSV 没有运行时入口，Excel 只读两列。
- `config_view.py` 和 `llm.py` 有用户未提交改动，实施必须做小范围合并并保留现有工作。

## Story 1：规范模型与无损格式适配器

### 验收标准

- 旧动态 JSON 和简单 JSON 无需迁移即可读取。
- ParaTranz 的 `term`、`translation`、`pos`、`note`、`variants`、`caseSensitive` 以及只读/未知字段进入规范模型。
- JSON 数组、键值对象、`terms`/`results` 包装结构均可导入。
- CSV、`.xlsx` 和 `.xls` 可按规范或 ParaTranz 风格表头读取；两列 Excel 继续支持配置列回退。
- 规范 JSON/CSV/Excel 与 ParaTranz payload 序列化有测试覆盖；复杂字段往返不丢失。

### 文件落点

- `src/transbridge/ai_translator/term_formats.py`（新增）
- `src/transbridge/ai_translator/term_database.py`（提取模型与加载委托，保留重导出）
- `tests/ai_translator/test_term_formats.py`（新增）

## Story 2：CSV 来源接入所有运行入口

### 验收标准

- `csv` 可出现在 `term_priority` 中，并拥有持久化的 `local_csv_path`。
- AI 翻译配置界面可选择 CSV，加载/保存配置及自动保存均覆盖该字段。
- `TermDatabaseManager` 按既有优先级加载 CSV、生成来源缓存并在失败时回退。
- 批量翻译空术语检查和智能助手配置查询/修改均识别 CSV。
- 没有 CSV 配置的旧 INI 行为不变。

### 文件落点

- `src/transbridge/config/llm.py`
- `src/transbridge/ai_translator/term_database.py`
- `src/transbridge/ui/tools/ai_translator/config_view.py`
- `src/transbridge/ui/tools/ai_translator/view_controls.py`
- `src/transbridge/ui/tools/ai_translator/batch_runtime.py`
- `src/transbridge/smart_assistant/tools/tool_translator.py`
- 相关配置、UI 与工具测试

## Story 3：动态库与 ParaTranz 边界兼容

### 验收标准

- 动态库和来源/合并缓存使用兼容解析，新增字段不会导致整库清空。
- ParaTranz 分页加载保留所有响应字段；可写 payload 只含 API 可写字段。
- 单条损坏记录不会拖垮其他有效记录；文件级损坏仍触发现有缓存回退并产生加载日志。
- 原有匹配、逐条作用域、向量索引和精确直填测试保持通过。

### 文件落点

- `src/transbridge/ai_translator/term_database.py`
- `src/transbridge/paratranz/api/paratranz_terms_api.py`（仅在需要规范 payload 边界时修改）
- `tests/ai_translator/test_term_database.py`

## 实施顺序与验证

1. 完成 Story 1 并运行格式适配器与术语数据库聚焦测试。
2. 完成 Story 2，补配置、UI 和智能助手工具回归。
3. 完成 Story 3，运行 AI 术语、翻译规划和依赖降级测试。
4. 运行相关测试、`uv run ruff check src tests`、`uv run ruff format --check src tests` 与 `git diff --check`。

## 风险与回退

- 新动态库字段可能让旧版本 TransBridge 的严格加载失败；不做启动时强制重写，只在用户正常保存动态库时升级格式。
- 表头别名过宽可能误识别普通表格；只有同时识别原文和译文列时才启用表头模式，否则使用显式列配置或报错。
- 未知元数据只允许 JSON 可序列化值；无法序列化的外部对象转为字符串，避免保存失败。

## 明确假设

- “完全兼容”指字段级最大化互操作，不指所有文件采用同一字面格式。
- ParaTranz 服务端只读字段应保留用于本地往返和审计，但不提交到创建/更新接口。

## 完成记录

- Story 1：已完成。规范模型、JSON/CSV/Excel/ParaTranz 适配器及流式表格读取已落地；`.xlsx` 与旧式 `.xls` 均受支持。
- Story 2：已完成。CSV 已接入配置、优先级、UI、批量预检和智能助手工具。
- Story 3：已完成。动态库与缓存兼容旧结构，ParaTranz 分页字段完整归一化，损坏来源可回退缓存。
- QA：73 项相关测试通过；本次核心文件 Ruff 与格式检查通过；`git diff --check` 通过。
