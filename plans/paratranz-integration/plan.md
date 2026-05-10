# ParaTranz 平台集成

> **状态**: ✔️ 已实现
> **模块**: `src/transbridge/paratranz/`

## 概述

与 ParaTranz 翻译平台 API 对接，实现项目管理、文件上传/下载、翻译条目 CRUD、术语管理、导出工件等完整工作流。

## Story 清单

| Story | 标题 | 状态 |
|-------|------|------|
| Story-01 | API 客户端基础（HTTP 封装、认证） | ✔️ |
| Story-02 | 配置管理（INI 持久化、LLM 配置共享） | ✔️ |
| Story-03 | 文件上传工作流（分类/普通模式、冲突检测、多模式） | ✔️ |
| Story-04 | 文件下载工作流（单文件/批量、分割文件合并） | ✔️ |
| Story-05 | 翻译条目 CRUD API | ✔️ |
| Story-06 | 术语管理 API | ✔️ |
| Story-07 | 导出工件工作流（触发导出 + 轮询 + 下载） | ✔️ |
| Story-08 | 项目管理（项目列表、新建、成员、历史、贡献统计） | ✔️ |

## 关键文件

- `src/transbridge/paratranz/config_manager.py` — ParatranzConfig + LLMConfig
- `src/transbridge/paratranz/paratranz_client.py` — API 客户端基类
- `src/transbridge/paratranz/api/paratranz_files_api.py` — 文件 API
- `src/transbridge/paratranz/api/paratranz_strings_api.py` — 词条 API
- `src/transbridge/paratranz/api/paratranz_terms_api.py` — 术语 API
- `src/transbridge/paratranz/api/paratranz_export_api.py` — 导出 API
- `src/transbridge/paratranz/workflow/uploader.py` — 上传工作流
- `src/transbridge/paratranz/workflow/downloader.py` — 下载工作流
- `src/transbridge/paratranz/workflow/artifact.py` — 导出工作流
