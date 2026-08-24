# AI 翻译/后处理 Prompt 缓存与契约修复 — QA 报告

**日期**: 2026-08-19
**对应方案**: `plans/ai-translation/plan.md` (Story 15) · `plans/ai-post-process/plan.md` (Story 14)
**审查范围**: 翻译提示词分层与 Provider Prompt Cache + 后处理提示词契约修复与阶段级缓存

## 审查方式

- 运行受影响模块完整测试套件 + 独立协议/契约运行时 sanity 检查
- 逐项对照两个 Story 的验收标准

## 测试覆盖

| 测试项 | 状态 | 备注 |
|--------|------|------|
| `tests/ai_translator/test_prompt_builder.py` (30 用例) | ✅ | 三消息结构、A/B 标记、模式映射、术语顺序/空值、key 稳定性、旧配置迁移 |
| `tests/infra/test_llm_client_prompt_cache.py` (11 用例) | ✅ | 官方显式断点/自动前缀/非官方清洁、聊天与流式一致、单次降级、Anthropic blocks |
| `tests/ai_translator/post_processor/` 提示词契约测试 (52+ 用例) | ✅ | 八变体 SYSTEM(FINAL)->USER、QualityGate 逐条术语、Refiner 只修复、Polisher 稳定渲染、Arbiter 单条润色字段 |
| 受影响模块集成 `pytest tests/ai_translator/post_processor tests/ai_translator/test_prompt_builder.py tests/infra` | ✅ | **98 passed** |
| 完整 `tests/ai_translator` | ✅ | **83 passed**（9 error 均为沙箱 tmp_path PermissionError，预存环境限制，非本次回归） |
| 协议/契约 sanity 脚本 | ✅ | official URL 门控、key 稳定性、能力判定、A/B 与 FINAL 校验、四个 TOML 无联合伪 JSON、官方显式请求清洁 |

## 审查结论

- **方案一致性**: ✅ Story 15 满足「通用 SYSTEM(A) -> 模式 SYSTEM(B) -> 动态 USER」、动态术语仅迁移位置、精确直填不动、非官方端点只收清洁消息；Story 14 满足「单稳定 System + 单 FINAL 断点、八变体独立 key、Refiner 只修复、Polisher 稳定渲染、Arbiter 单条可见润色字段、JSON 示例合法」
- **代码质量**: ✅ 共享缓存协议集中在 `prompt_cache.py` + `prompt_contract.py`，Consumer 不感知 Provider 私有字段；去除 `safe_substitute` 泄漏路径
- **安全性**: ✅ 内部元数据不泄漏到 SDK；非官方兼容端点不接收 OpenAI/Anthropic 私有缓存字段；日志不输出系统提示词/术语/原文
- **非回归**: ✅ 现有 `AutoTranslator._run_batch` 精确直填代码零改动；术语匹配/顺序不变；结果数据类/解析器/阈值不变；`tests/ai_translator` 83 passed

## 发现的问题

- [ ] (Minor) `AutoTranslator._run_batch` 流式调试头仍把 `messages[1]` 标为 "USER"，现为模式 SYSTEM(B)；仅调试标注错位，不影响真实请求（受保护区域未改）
- [ ] (Minor) `llm_client.py` 存在 HEAD 已有的 3 条 ruff lint（I001/E501/UP037），非本次引入，任务范围未处理

## 签名

**QA 通过**（无 Blocker/Critical/Major；2 项 Minor 已知限制，可延后处理）
