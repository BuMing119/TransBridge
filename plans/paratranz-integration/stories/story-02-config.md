# Story 02: 配置管理

**所属方案**: `plans/paratranz-integration/plan.md`
**状态**: ✔️ 已实现

## 概述

INI 文件配置管理，同时服务 ParaTranz API 和 AI 翻译 LLM。两个配置类共享同一 INI 文件不同 section。

## 关键设计

- **ParatranzConfig**: 管理 `[api]` section（token, base_url, timeout, user_id）
- **LLMConfig**: 管理 `[llm]` section（provider, api_key, model, max_concurrent 等 20+ 字段）
- **共享 INI**: `data/paratranz_config.ini`，两个类互不干扰
- **get_data_dir()**: 自适应开发/打包环境（`{项目根}/data/` vs `%APPDATA%/TransBridge/data/`）
- **create_or_load()**: 工厂方法，文件不存在时创建默认配置

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/paratranz/config_manager.py` | ParatranzConfig + LLMConfig |
