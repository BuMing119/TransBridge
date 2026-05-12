# 001: 修复 EmbeddingConfig 属性访问与默认模型名

**日期**: 2026-05-11
**类型**: 改
**关联**: Epic: AI 自动翻译 > 回归修复

## 修改文件

### `src/transbridge/ui/tools/ai_translator/ai_translator_window.py` (改)
- **修改内容**: 三处 embedding 配置读写代码从旧平铺属性改用 `EmbeddingConfig` 子对象访问：
  - `_load_config()` (L699-703): `cfg.embedding_provider` → `cfg.embedding.provider`，`cfg.embedding_local_model` → `cfg.embedding.local_model_path`，`cfg.embedding_model` → `cfg.embedding.model`，`cfg.embedding_api_key` → `cfg.embedding.api_key`，`cfg.embedding_base_url` → `cfg.embedding.base_url`
  - `_save_config()` (L765-769): 同上五处赋值修正
  - `_build_llm_config()` (L839-843): 同上五处赋值修正
- **原因**: `agent-upgrade` Epic Story-01 将 `LLMConfig` 的 embedding 字段重构为独立 `EmbeddingConfig` 子对象后，AI 翻译器窗口未同步更新，导致打开窗口时抛出 `AttributeError: 'LLMConfig' object has no attribute 'embedding_provider'`

### `data/paratranz_config.ini` (改)
- **修改内容**: `[llm]` 节 `model` 值从 `gpt-4o` 改为 `deepseek-v4-pro`
- **原因**: 当前使用的 API 端点为 DeepSeek (`https://api.deepseek.com`)，不支持 `gpt-4o` 模型名，导致 Smart AI 助手请求返回 400 错误
