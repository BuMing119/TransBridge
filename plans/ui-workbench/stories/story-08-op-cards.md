# Story 08: OpCard 操作卡片基类 + UploadCard/DownloadCard/WriteCard

**所属方案**: `plans/ui-workbench/plan.md`
**状态**: ✔️ 已实现

## 概述

操作卡片组件体系。OpCard 提供统一基类（主按钮 + 可选批量按钮），子类实现具体操作逻辑。

## 关键设计

- **OpCard**: 基类，主按钮 + 批量按钮 + 结果信号
- **UploadCard**: 分类/普通上传模式 + 批量上传（_BatchUploadModeDialog 模式选择 + _ConflictResolveDialog 冲突解决）
- **DownloadCard**: 单文件/批量下载 + 分割文件自动检测与合并（_find_split_files）
- **WriteCard**: ESP/EET/XT 三种写回目标（_WriteTargetDialog radio 选择）+ 纯本地化模式（QFileDialog.getExistingDirectory）

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/workbench/cards/base.py` | OpCard |
| `src/transbridge/ui/workbench/cards/upload_card.py` | UploadCard + 相关对话框 |
| `src/transbridge/ui/workbench/cards/download_card.py` | DownloadCard |
| `src/transbridge/ui/workbench/cards/write_card.py` | WriteCard + _WriteTargetDialog |
