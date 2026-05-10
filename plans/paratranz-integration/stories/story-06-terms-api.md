# Story 06: 术语管理 API

**所属方案**: `plans/paratranz-integration/plan.md`
**状态**: ✔️ 已实现

## 概述

ParaTranz 术语库的 API 封装，支持术语的同步和查询。

## 关键设计

- **ParatranzTermsAPI**: 封装术语相关端点
- **术语同步**: 从 ParaTranz 拉取术语 → 合并到本地 TermDatabaseManager
- **来源优先级**: paratranz 来源优先级高于 json/excel，低于 manual/auto

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/paratranz/api/paratranz_terms_api.py` | ParatranzTermsAPI |
| `src/transbridge/ai_translator/term_database.py` | 术语合并优先级 |
