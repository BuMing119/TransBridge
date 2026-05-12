# 智能助手知识缺口（临时分析）

> 2026-05-11 发现，待后续排期处理。

AI 助手有工具但不知道怎么正确使用。5 个缺口：

## 1. 异步任务无完成通知

`start_translation` / `start_polish` 后台跑，返回 task_id 后 AI 以为完事了。
`TaskManager` 无回调，线程完成后没人通知 LLM。

**涉及**: `task_manager.py`, `tool_translator.py:19`, `chat_widget.py:390/314`

**候选**:
- A. TaskManager 完成信号 → chat_widget 自动注入 observation → 触发 LLM
- B. 工具阻塞等待完成
- C. TaskManager 加 on_complete 回调
- D. prompt 引导 LLM 主动轮询（不可靠）

## 2. 前置条件未知

AI 不知道调 `start_translation` 前要检查什么：

| 需要检查 | 有无工具 | AI知道吗 |
|---------|---------|---------|
| API Key 已配 | get_translation_config ✅ | ❌ |
| 术语库有内容 | 无工具 | ❌ |
| 术语来源配置 | 无工具 | ❌ |
| 集合已加载 | get_statistics ✅ | ❌ |
| 作用域已设 | get_scope_preview ✅ | ❌ |
| 后处理开关状态 | 无工具（get_translation_config 不返回后处理段） | ❌ |

## 3. 工序流程未知

AI 实际：用户说"翻译" → 直接 start_translation → "搞定"
正确流程：确认配置 → 检查术语 → 设作用域 → 预览 → 翻译 → 轮询 → 检查结果 → 后处理 → 写回

缺失工具：`list_term_sources`, `check_term_coverage`, `get/set_post_process_config`, `get/set_term_config`

## 4. 配置知识割裂

```
paratranz_config.ini
├─ [paratranz]    ← AI 看不到
├─ [llm]          ← get_translation_config 可读
├─ [post_process] ← AI 看不到
├─ [term]         ← AI 看不到
└─ [llm_profiles] ← get_translation_config 可读
```

AI 只知道 LLM 配置，不知道后处理/术语/ParaTranz 的状态。

## 5. 错误诊断与恢复

工具失败后 AI 只知道错误消息，不知道原因分类和修复策略。

## 修复优先级

| 优 | 缺口 | 改动 |
|----|------|------|
| P0 | 1. 异步通知 | TaskManager + chat_widget |
| P0 | 6. Prompt 无领域知识 | prompts.py + context_builder.py 大改 |
| P0 | 2. 前置条件 | 新工具 + ContextBuilder |
| P1 | 3. 工序知识 | prompts.py |
| P1 | 4. 配置知识 | 新配置查询工具 + ContextBuilder |
| P2 | 5. 错误恢复 | 工具错误码标准化 + prompts |