# Story 05: 翻译条目 CRUD API

**所属方案**: `plans/paratranz-integration/plan.md`
**状态**: ✔️ 已实现

## 概述

ParaTranz 翻译条目的 CRUD 操作 API 封装。支持创建、查询、更新翻译条目，按文件 ID 关联。

## 关键设计

- **ParatranzStringsAPI**: 封装 `/strings` 端点
- **分页查询**: list 接口支持 page/pageSize 参数
- **按文件过滤**: 指定 file_id 查询某文件下的所有条目
- **状态管理**: stage 字段映射到 ParaTranz 的翻译状态

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/paratranz/api/paratranz_strings_api.py` | ParatranzStringsAPI |
