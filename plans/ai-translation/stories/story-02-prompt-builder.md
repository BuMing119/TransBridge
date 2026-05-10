# Story 02: Prompt 构建器

**所属方案**: `plans/ai-translation/plan.md`
**状态**: ✔️ 已实现

## 概述

TOML 模板 + string.Template 构建翻译 Prompt。支持术语注入、上下文注入、JSON 输出指令，按游戏和语言配置。

## 关键设计

- **PromptBuilder(game_profile, target_lang)**: 加载 data/prompts/games/{profile}.toml + langs/{lang}.toml
- **build_translation_prompt(batch, terms)**: 构建单批次翻译的系统+用户消息
- **$var 语法**: 使用 string.Template.safe_substitute，$ 前缀避开 JSON 花括号冲突
- **parse_translation_response()**: 解析 LLM 返回的 JSON → {entry_id: translation} 映射
- **_extract_partial_json_pairs()**: 截断 JSON 容错提取，处理 max_tokens 耗尽场景
- **parse_hybrid_response()**: (Story-02 扩展) 解析 mode/thought/steps 混合响应

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ai_translator/prompt_builder.py` | PromptBuilder |
| `data/prompts/games/{profile}.toml` | 游戏配置 |
| `data/prompts/langs/{lang}.toml` | 语言配置 |
