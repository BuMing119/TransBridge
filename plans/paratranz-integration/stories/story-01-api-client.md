# Story 01: API 客户端基础

**所属方案**: `plans/paratranz-integration/plan.md`
**状态**: ✔️ 已实现

## 概述

封装 ParaTranz REST API 的 HTTP 客户端基类，处理认证、超时、错误响应。

## 关键设计

- **ParatranzClient**: 基类，提供 `_request(method, path, **kwargs)` 统一 HTTP 方法
- **认证**: Bearer Token 通过 header `Authorization: Bearer {token}` 传递
- **超时配置**: `timeout=30`，从 INI 配置文件读取
- **base_url**: 默认 `https://paratranz.cn/api`，可在配置中覆盖

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/paratranz/paratranz_client.py` | ParatranzClient 基类 |
