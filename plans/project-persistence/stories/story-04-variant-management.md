# Story 04: 翻译版本管理

**所属方案**: `plans/project-persistence/plan.md`
**技术模块**: `src/transbridge/ui/` (修改), `src/transbridge/persistence/` (修改)
**状态**: 已确认
**创建日期**: 2026-05-08

## 前置依赖

### 上游 Story
- Story-03（同 plan）：项目管理已实现，ProjectBar 已就位

### 引用的架构决策
- ADR-006: Variant 子目录模型、版本数据 current.json 结构、版本切换保存行为配置

## 验收标准

- [ ] 项目下至少有一个默认版本（项目创建时自动创建）
- [ ] 「版本 → 新建版本」创建空白新版本
- [ ] 「版本 → 复制版本」从当前版本继承全部译文+标签创建新版本
- [ ] 版本切换下拉显示所有版本，切换时更新 Step2 表格
- [ ] 版本切换保存行为：根据配置自动保存或弹出提示对话框
- [ ] 版本删除（至少保留一个版本）
- [ ] 标签库随版本切换（每个版本独立的 label_library）

## 数据流

```
新建版本:
  用户输入名称
    → ProjectHandle.add_variant(name)
    → 创建 {variant}/ 目录 + 空 current.json
    → ProjectHandle.save()
    → 更新 ProjectBar 版本下拉

复制版本:
  用户选择源版本 → 输入新版本名
    → VariantStore.load(source/current.json)
    → VariantStore.save(target/current.json)  # 完整复制
    → ProjectHandle.add_variant(name, copied_from=source)
    → 更新 ProjectBar 版本下拉

版本切换:
  用户选择目标版本
    → 检查 dirty 标记
    ├─ dirty + save_behavior="auto" → 自动保存当前版本 → 切换
    ├─ dirty + save_behavior="prompt" → 弹出对话框 → 切换
    └─ 不 dirty → 直接切换
    → VariantStore.save(current_path)
    → VariantStore.load(new_path)
    → VariantStore.apply_to(entries)
    → 重新加载 label_library 到 Step2
    → ctx.active_variant = new_name → emit variant_changed
    → 刷新 Step2 表格
```

## 关键接口

### _variant_dialog.py

```python
class VariantDialog(QDialog):
    """新建/复制版本对话框"""
    
    def __init__(self, mode: str, parent=None):
        """
        mode: "new" | "copy"
        """
    
    def get_result(self) -> dict:
        """返回 {name: str, source: str|None}"""

class VariantCopyDialog(QDialog):
    """复制版本：选择源版本 + 输入新名称"""
    
    def __init__(self, variants: list[str], parent=None): ...
    def get_result(self) -> tuple[str, str]:
        """返回 (source_name, target_name)"""
```

### main_window.py 新增方法

```python
class MainWindow:
    def _on_new_variant(self) -> None:
        """创建空白新版本"""
    
    def _on_copy_variant(self) -> None:
        """从当前版本复制创建新版本"""
    
    def _on_switch_variant(self, name: str) -> None:
        """切换版本——保存/提示 → 加载 → 刷新"""
    
    def _on_delete_variant(self) -> None:
        """删除当前版本（至少保留一个）"""
```

## 实现步骤

### 步骤 1: 新建/复制版本对话框

**涉及文件**: `src/transbridge/ui/workbench/_variant_dialog.py`（新建）

**实现要点**:
- VariantDialog(mode="new"): 仅名称输入
- VariantDialog(mode="copy"): 源版本下拉 + 名称输入
- 验证：名称非空、不与已有版本重名

### 步骤 2: 版本 CRUD 逻辑

**涉及文件**: `src/transbridge/ui/main_window.py`（修改）

**实现要点**:
- `_on_new_variant()`: 创建空 VariantStore → save → add_variant → 切换
- `_on_copy_variant()`: 加载源 VariantStore → save 到新路径 → add_variant → 切换
- `_on_delete_variant()`: 确认对话框 → 检查 variants 数量 ≥ 2 → 删除目录 → remove_variant → 切换到剩余版本

**边界条件**:
- 仅剩一个版本时删除按钮禁用
- 复制时源版本 current.json 不存在 → 创建空白（等同新建）

### 步骤 3: 版本切换逻辑

**涉及文件**: `src/transbridge/ui/main_window.py`（修改）, `src/transbridge/ui/context.py`（修改）

**实现要点**:
- `_on_switch_variant(name)`: 保存行为判断 → collect_from → save → load → apply_to → 刷新
- ctx.active_variant setter 触发 variant_changed 信号
- Step2 监听 variant_changed → 重新加载表格数据
- 标签库切换：从 VariantStore.label_library 恢复到 Step2._label_library

**边界条件**:
- 切换时 VariantStore.apply_to 不匹配的 entry_id → 跳过（源文件可能已变更）
- 切换前后为同一个版本 → 不执行任何操作

### 步骤 4: 版本工具栏集成

**涉及文件**: `src/transbridge/ui/workbench/_project_bar.py`（修改，S03 已创建）

**实现要点**:
- 版本下拉：列出 ProjectHandle.variants，标记 active_variant
- 版本管理按钮弹出菜单：新建/复制/删除版本

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/workbench/_variant_dialog.py` | 新建 | 版本对话框 |
| `src/transbridge/ui/main_window.py` | 修改 | 版本 CRUD + 切换逻辑 |
| `src/transbridge/ui/context.py` | 修改 | active_variant setter + 信号 |
| `src/transbridge/ui/workbench/step2.py` | 修改 | variant_changed 监听 + 标签库刷新 |

## 风险与注意事项

- **版本切换性能**: apply_to() 是 O(n) 遍历，10 万条目约 0.1s，可接受。未来如需要可加进度条
- **标签库独立性**: 每个版本有独立的 label_library，切换版本后标签库完全替换。用户需在每个版本中独立管理标签定义
