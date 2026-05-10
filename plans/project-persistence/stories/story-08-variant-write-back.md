# Story 08: 版本写回

**所属方案**: `plans/project-persistence/plan.md`
**技术模块**: `src/transbridge/ui/workbench/cards/`, `src/transbridge/persistence/`
**状态**: 已确认
**创建日期**: 2026-05-08

## 前置依赖

### 上游 Story
- Story-04（同 plan）：版本管理已实现，VariantStore 可切换

### 跨 Plan 依赖
- `ui-workbench/plan.md` → WriteCard 组件（现有写回逻辑）

### 引用的架构决策
- ADR-006: 分版本分目录输出，不修改源文件名

## 验收标准

- [ ] 写回对话框新增「写回模式」选项：仅当前版本 / 所有版本
- [ ] 仅当前版本：现有行为，输出到用户指定目录
- [ ] 所有版本：每个版本输出到独立子目录（`{output_dir}/{variant_name}/`）
- [ ] 不修改 ESP/EET/XML 源文件名
- [ ] workspace.json 中保存写回配置（默认模式、输出目录）

## 数据流

```
仅当前版本:
  用户选择输出目录
    → VariantStore.apply_to(entries)  # 确保数据最新
    → 现有 WriteCard 写回逻辑
    → 输出到 {output_dir}/

所有版本:
  用户选择输出根目录
    → 遍历 ctx.active_project.variants
    → for each variant:
        ├─ 保存当前版本 (collect_from → save)
        ├─ 加载目标版本 (VariantStore.load → apply_to)
        ├─ 写回到 {output_dir}/{variant_name}/
        └─ 继续下一个版本
    → 恢复原始活跃版本 (VariantStore.load → apply_to)
    → 显示写回完成摘要
```

## 关键接口

```python
# write_card.py 扩展
class WriteCard(OpCard):
    def _on_write(self):
        """覆写：添加写回模式选择"""
        dlg = _WriteTargetDialog(...)
        # 新增：写回模式单选组
        dlg.add_write_mode_selector()  # 仅当前版本 / 所有版本
        if dlg.exec() != Accepted:
            return
        mode = dlg.write_mode  # "current" | "all"
        output_dir = dlg.output_dir
        if mode == "current":
            self._do_write(output_dir)
        else:
            self._do_write_all_variants(output_dir)
    
    def _do_write_all_variants(self, base_dir: Path):
        """遍历所有版本，分别写回到 {base_dir}/{variant_name}/"""
        ctx = self._ctx
        project = ctx.active_project
        current_variant = ctx.active_variant
        
        results = {}
        for v in project.variants:
            vname = v["name"]
            # 切换到版本 vname
            ctx.active_variant = vname  # 触发 variant_changed → VariantStore 切换
            # 写回到子目录
            out_dir = base_dir / vname
            out_dir.mkdir(parents=True, exist_ok=True)
            result = self._do_write_single(out_dir)  # 复用现有写回逻辑
            results[vname] = result
        
        # 恢复原始版本
        ctx.active_variant = current_variant
        
        # 生成写回报告
        QMessageBox.information(self, "写回完成", 
            "\n".join(f"{k}: {v}" for k, v in results.items()))
```

## 实现步骤

### 步骤 1: 写回对话框扩展

**涉及文件**: `src/transbridge/ui/workbench/cards/write_card.py`（修改）

**实现要点**:
- WriteCard 的 `_WriteTargetDialog` 添加写回模式单选组：「仅当前版本」「所有版本」
- 所有版本模式下，输出目录选择变为"选择输出根目录"
- 显示提示：「每个版本将输出到独立子目录 {output_dir}/{variant_name}/」

**边界条件**:
- 仅当前版本 → 现有行为完全不变
- 项目无版本 → 隐藏模式选择，回退到现有行为

### 步骤 2: 全版本写回实现

**涉及文件**: `src/transbridge/ui/workbench/cards/write_card.py`（修改）

**实现要点**:
- `_do_write_all_variants()`: 循环切换版本 → 写回 → 恢复
- 写回过程显示进度（"正在写回: ank术语版 (2/3)"）
- 写回失败某版本 → 记录错误，继续下一版本
- 写回完成后显示汇总：成功 N/总数 M

**边界条件**:
- 某版本无任何译文 → 仍创建输出目录（空目录或仅 headers）
- 写回过程中用户取消 → 停止循环，恢复原始版本

### 步骤 3: 写回配置持久化

**涉及文件**: `src/transbridge/persistence/workspace.py`（修改）

**实现要点**:
- workspace.json settings.write_back 记录默认模式和上次输出目录
- 下次打开对话框时恢复上次配置

```json
"write_back": {
    "mode": "current_variant",
    "last_output_dir": "C:/Users/xxx/trans_output"
}
```

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/workbench/cards/write_card.py` | 修改 | 写回模式选择 + 全版本写回 |
| `src/transbridge/persistence/workspace.py` | 修改 | write_back 配置项 |

## 风险与注意事项

- **版本切换副作用**: 全版本写回时频繁切换版本会触发 UI 刷新。建议写回前冻结 Step2 更新信号（`blockSignals(True)`），完成后恢复
- **写回中途失败**: 某个版本写回失败不应影响其他版本。失败的版本记录到结果摘要中
