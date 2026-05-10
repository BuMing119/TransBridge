# Story 14: 批量操作 UI

**所属方案**: `plans/ui-workbench/plan.md`
**状态**: ✔️ 已实现

## 概述

批量操作的通用 UI 组件：插件选择对话框、确认对话框、结果对话框。供 UploadCard/DownloadCard/WriteCard 复用。

## 关键设计

- **_SlotSelectDialog**: 列出所有已加载插件，全选/全不选 toggle，返回选中的 slot keys
- **_BatchConfirmDialog**: 滚动确认对话框（最大高度 400px），展示所有待处理项 + 确认/取消
- **_BatchResultDialog**: 滚动结果对话框（最大高度 400px），逐项显示成功/失败
- **复用**: 三卡片（UploadCard/DownloadCard/WriteCard）统一使用此组件

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/workbench/cards/upload_card.py` | _SlotSelectDialog, _BatchUploadModeDialog, _BatchConfirmDialog, _BatchResultDialog, _ConflictResolveDialog |
| `src/transbridge/ui/workbench/cards/download_card.py` | 复用上述对话框 |
| `src/transbridge/ui/workbench/cards/write_card.py` | _SlotSelectDialog, _WriteTargetDialog |
