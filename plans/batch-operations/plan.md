# 批量操作

> **状态**: ✔️ 已实现
> **跨模块**: `ui/workbench/`, `ui/tools/`, `paratranz/workflow/`

## 概述

为各类操作提供批量处理能力，包括批量 ESP 解析、批量上传/下载/写回、批量 AI 翻译，统一使用多 CollectionSlot 架构。

## Story 清单

| Story | 标题 | 状态 |
|-------|------|------|
| Story-01 | 批量 ESP 解析（多文件选择 + 独立 slot 管理） | ✔️ |
| Story-02 | 批量上传（插件选择对话框 + 模式选择 + 滚动确认/结果） | ✔️ |
| Story-03 | 批量下载（分割文件自动检测 + 合并） | ✔️ |
| Story-04 | 批量写回（多插件选择 + EET/XT 路径预填） | ✔️ |
| Story-05 | 批量 AI 翻译（插件排序 + 配置 + 进度 + 日志查看） | ✔️ |
| Story-06 | 多集合管理（AppContext._slots + 信号广播） | ✔️ |
| Story-07 | Strings "全部" 批量导入（应用到所有已加载集合） | ✔️ |

## 关键文件

- `src/transbridge/ui/context.py` — AppContext._slots 多集合管理
- `src/transbridge/ui/workbench/cards/upload_card.py` — _SlotSelectDialog, _BatchUploadModeDialog, _BatchConfirmDialog, _BatchResultDialog
- `src/transbridge/ui/workbench/cards/download_card.py` — 批量下载 + 分割文件合并
- `src/transbridge/ui/workbench/cards/write_card.py` — _SlotSelectDialog, _WriteTargetDialog
- `src/transbridge/ui/tools/ai_translator/_batch_translation_dialog.py` — 批量翻译对话框
- `src/transbridge/ui/tools/ai_translator/_batch_translation_worker.py` — PluginTranslationResult, BatchTranslationSummary
