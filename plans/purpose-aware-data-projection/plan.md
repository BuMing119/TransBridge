# 业务数据保真与凭据用途边界

状态：S01～S05 本地完成并验证。2026-10-07。

需求：FR7.13.8.4、FR22.5；架构：[ADR-046](../../docs/adr/046-purpose-aware-data-projection.md)。用户已批准方案和多 Agent 全流程。

## 目标与范围

查询、编辑、翻译、重试、报告、MCP 与本地日志中的业务文本和身份原样保留；真正凭据在来源与技术诊断边界保护。保留原 key/id，不增加临时词条编号，不更改授权与格式安全，不自动恢复已经丢失的历史文字，不调用真实模型或远端服务。

## 阶段与 Story

- [x] 分析：三路只读调查与合成复现完成，区分权威业务数据、恢复状态、业务证据、技术异常、账户凭据与操作令牌。
- [x] 架构与规划：用户接受方案，冻结共享投射 API 和写入所有权。
- [x] S01 共享分类及诊断出口：application/security 的 SecretValue、DiagnosticValue、project_business_data；保留 config 导入兼容；工作流业务日志保持原样，GUI/MCP 技术日志处理完整异常栈，请求已知凭据保护。
- [x] S02 工具往返与重试：OutputGuard/base/ToolResult/Observation/MCP 清除无差别正文扫描；get/下标一致；重试仅保护显式凭据，业务参数和 schema 保真；技术异常在来源处理，trace 保存业务证据。
- [x] S03 报告与术语作用域：业务 metadata、诊断 EntryKey 保真；插件作用域迁为明确字段并贯通旧/新 checkpoint、history、checker adapters；技术诊断显式处理。
- [x] S04 联合 QA：核心新增反例先跑，再扩展助手、报告、LLM 日志、凭据及恢复链；Ruff check/format、diff 审查；清理自建临时文件。
- [x] S05 增量记录：按真实差异更新本计划、相关索引和 changelog。

## 独立验收与文件落点

S01 由安全/日志工作线负责 `application/security/`、`config/paratranz_credentials.py`、`workflow_logging_client.py`、GUI/MCP 日志与必要的 LLM credential adapters。合成请求、响应、跨 chunk、无效模型响应中的游戏 password/token/sk 文本保持原样；显式凭据及网络头/已知 token 的技术异常不外泄，完整 traceback 被覆盖。

S02 负责 `smart_assistant/`、助手 UI 桥接和对应测试。真实注册工具 query → guard → observation → edit/select 能定位相同 key 的不同原文；set_filters 的非法字段修复后查询原文不变；业务字典键 token/password 不误删；授权令牌往返正常；账户凭据不出现在修复提示。写工具继续不自动重试。

S03 负责 `application/translation/postprocess*`、报告/history 与 AI 术语 adapter。候选与 diagnostic 中身份相等，错误能关联原词条；note/issues 保真；迁移前旧快照及新 checkpoint 的术语作用域一致，候选提交和断点恢复不丢字段。

主会话负责需求/ADR/plan、跨界集成、复核和最终证据。共享接口先冻结后并行实现；子 Agent 不改彼此文件，不提交 Git、不读真实凭据、不修改用户 ESP。

配置指纹验收：只将账户和 embedding 配置的明确凭据字段投射为是否配置；token 预算和工作流内业务字典参与真实指纹计算。旧指纹可能因修复后的预算值计算而不再相等，继续使用既有一致性检查，不迁移或伪造旧指纹。

## 测试与风险

相关现有测试有把业务文本匹配凭据模式当作正确结果的断言，必须改为来源明确的凭据反例，同时新增合法文本保真断言。不得只删除安全测试。

已有大模块只做职责内局部修复；新投射与日志职责独立抽取。旧兼容插件作用域读取仅用于历史快照，待历史格式迁移完成且无旧入口时可移除。没有 update_plan 工具，本文件是当前阶段状态的权威记录。

OutputValidationGuard 保留旧 redactor 构造参数并发出弃用警告，但不再用它扫描业务值；下次允许破坏公开接口的主版本可移除。重试的凭据 schema 标注覆盖当前使用的直接 properties/items/patternProperties/additionalProperties；未来如注册 oneOf/$ref 内的凭据参数，必须先扩展 schema 解析及对应回归。

## 完成证据

三条 Agent 工作线与主会话交叉复核完成。主会话四组离线回归共 2270 passed、4 skipped；最后补充异常对象边界及相关回归 37 passed（有重复覆盖，不累计）。Ruff check、format --check（1550 文件）与 diff --check 通过。专属测试目录和链接已验证清理。完整命令、逐文件变化及兼容性见[增量记录](../../docs/changelogs/purpose-aware-data-projection/story-01-to-05/2026-10-07-001-business-data-fidelity.md)。未调用真实模型、未打包或提交。
