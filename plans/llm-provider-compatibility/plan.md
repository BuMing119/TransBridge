# LLM 服务配置、推理与缓存兼容修复

- 日期：2026-11-12
- 状态：S01～S03 本地实现、相关 QA 和增量记录完成；用户已授权 bm-pilot 端到端修复与多 Agent 编码。
- 关联： [ADR-029](../../docs/adr/029-request-scoped-llm-reasoning-control.md)、[ADR-031](../../docs/adr/031-native-llm-function-calling.md)、[ADR-041](../../docs/adr/041-assistant-context-compaction.md)。
- 来源：本次代码审查确认的 Anthropic 输出上限、Base URL、历史缓存、缓存门槛、推理控制及 OpenAI 隐式缓存策略问题。

## 目标与边界

旧配置的输出上限 0 可被正确解释，Anthropic 自定义地址真正生效；请求级推理意图只映射到已验证控制；新模型不因过时缓存门槛或白名单失去缓存机会。保持原生结构化输出、取消、限流、流式预览、助手历史布局和校对业务调用次数。

不自动迁移到另一供应商，不安装依赖，不更改锁文件，不提交或发布，不修改工作区原有的 ParaTranz 下载及 AI 结果后台审阅工作。真实 API 测试需可用的明确测试配置；本地假服务结果不作为实际缓存命中率或翻译质量证明。

## 已核实事实与约束

- Anthropic 客户端拒绝 `max_tokens <= 0`，配置/UI 却允许默认 0；助手单独使用 4096 兜底。
- Anthropic 工厂没有传入 `base_url`，SDK 会读取环境变量；旧配置可能残留 OpenAI 默认地址。
- 助手 Anthropic 缓存有模型前缀白名单；翻译缓存未知模型返回 1024，三个 5.5 模型的官方最低长度实际为 512。
- Anthropic 推理目前无条件声明省略 thinking 即直接回答，而新模型有默认/强制推理；现有能力缓存与请求作用域可复用。
- 官方 OpenAI 显式缓存最低 1024；旧模型隐式缓存受请求设置影响，不应以本地 2048 判断服务端是否缓存。
- `llm_client.py` 已超过 500 行：本次仅作局部接线，预算、地址和新增协议逻辑提取到独立、可测试模块；不扩充综合 Manager。

## 阶段进度

- [x] 分析与调用链确认：前序审查、75 项基线回归及官方文档核验。
- [x] 规划与编码边界：本计划；三个编码 Agent 的文件所有权已分开。
- [x] 实现：S01～S03。
- [x] QA：聚焦、跨模块回归、Ruff、最终 diff 审查。
- [x] 记录：仅归档本任务差异，保留已有增量索引条目。

## S01：输出预算与服务地址一致

验收：

- 0 显示为自动，正数不被静默重写，负数/bool 非法；OpenAI 0 保持省略输出上限。
- Anthropic 翻译自动预算使用 8192，助手保持 4096；普通/流式/tools 共用解析规则。默认值不是足够完成任意批次的保证，截断继续进入现有失败/拆批流程。
- 助手上下文预留与发送值一致；输出预算为新建小模块中的纯函数，不复制到每个业务入口。
- 保留 OpenAI 已绑定助手原有的 4096 发送/预留；未绑定自动请求仍可传 0，但保守预留至少 4096，不能把未知输出上限当作 0。
- 显式地址优先于环境变量，再回退官方地址；取消重建保留实际地址。工厂、连接测试和能力键使用一致解析结果。
- 旧 Anthropic 配置残留 OpenAI 官方默认地址时明确要求修正；设置页供应商切换只调整默认值，不覆盖自定义地址。

落点：`infra/llm_client.py`、新增 `infra/llm_request_config.py`、设置/翻译输出控件、助手预算与相关测试。tools 适配由 S03 Agent 接线；reasoning 能力键由 S02 Agent 接线。

验证：自动/显式/非法预算矩阵，流式和工具调用；8192 范围的结构化翻译响应；显式/环境/默认端点优先级，旧地址拒绝，取消重建，UI 切换和上下文预留。

## S02：可验证的 Anthropic 请求级推理控制

验收：

- 保留 `INHERIT`、`PREFER_DIRECT`、`PREFER_LOW`；助手维持继承。不能关闭时不声明成功，不擅自把 direct 改成 low。
- 使用官方能力信息或有限协议握手分别证明 direct/low 能力；未知端点接受非法字段不能被认定为支持。
- 省略 thinking 不再被无条件视作关闭；能力探测版本提升使旧缓存失效。
- 正式普通/流式请求应用有效补丁，`output_config.effort` 与结构化 `format` 合并；缓存兼容回退保留二者。
- 参数明确拒绝只触发既有一次有界降级；认证、限流、网络等异常不得误报为能力不支持；不重放已输出流。

落点：`infra/llm_reasoning.py`、`llm_reasoning_protocols.py`、必要的独立 Anthropic 协议模块与测试。`llm_client.py` 由 S01 Agent 统一接线。同步修订 ADR-029 和原推理 plan 的过时语义。

验证：无关闭/仅 low/仅 direct/未知能力，静默忽略哨兵，真实错误分类，旧能力失效，schema+effort+缓存降级，流式已开始不重试。

## S03：缓存策略与实际命中分离

验收：

- 官方 Anthropic 助手缓存依据协议启用，不受易过期模型前缀白名单限制；自定义端点仍保守。用户开关、历史 layout 和明确参数拒绝降级保持。
- 已核验 5.5 模型门槛 512，加 25% 估算余量后为 640；准确匹配型号/版本后缀，不用任意子串猜测。未知门槛可委托已支持的官方服务判断，不虚构 1024。
- OpenAI 显式缓存保留 1024；隐式路径不因本地 2048 门槛丢弃稳定 key，仍剥离内部元数据。
- 最小日志区分请求策略与原因；实际命中依供应商 usage，不把缺少自定义字段描述成服务端已关闭缓存。

落点：`infra/prompt_cache.py`、`assistant_prompt_cache.py`、`anthropic_tool_calling.py` 与现有 cache/tests。同步当前缓存设计文档，历史增量不改写。

验证：三个 5.5 型号及合法后缀、伪前缀、未知型号、已知短前缀、未知端点、隐式短前缀稳定 key、用户禁用、流式失败不重放、重试保留完整材料。

## 依赖、兼容与验证

S01、S02、S03 可并行开发；端点/预算 helper 是 S02/S03 的薄接线依赖。主会话最终联合验证并审查文件责任和异常路径。业务逻辑不得依赖 Qt。

旧正数设置保持，0 不需改写配置。既有无效 Anthropic/OpenAI 地址组合暴露为可操作错误；用户自定义地址不被猜测。推理能力缓存通过版本自然失效，不删除用户目录。

已使用仓库现有 uv 环境，增加 `--offline --no-sync --no-cache` 避免共享缓存访问限制，未安装依赖或修改锁文件。翻译/应用层 1097、助手 1174、基础设施/配置 362、UI 67 项回归通过，S01 新增边界另在 183 项聚焦组通过（范围重叠，不重复累计）。全库 Ruff check/format（2547 文件）、diff 检查和新增文档链接/文件清单检查通过。

准确命令、逐文件差异和验证限制见 [S01～S03 增量](../../docs/changelogs/llm-provider-compatibility/story-01-to-03/2026-11-12-001-provider-request-compatibility.md)。未运行真实 API 或安装包测试；不能据此宣称实际缓存命中率和翻译质量提升。任务没有未完成实现项或新增任务临时目录，既有用户改动保留。

## 官方依据（2026-11-12 核验）

- [Anthropic 缓存](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Anthropic thinking 支持](https://platform.claude.com/docs/en/build-with-claude/thinking-troubleshooting)
- [Anthropic effort](https://platform.claude.com/docs/en/build-with-claude/effort)
- [OpenAI 缓存](https://developers.openai.com/api/docs/guides/prompt-caching)
