# Story 09: scope 修改 + 分享/导入

**所属方案**: `plans/translation-memory/plan.md`
**技术模块**: backend + ui
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 上游 Story
- Story 07（已完成）：定位键 `mod_file_id`、文件名 `.tbdict`、load/save 就绪
- Story 06（已完成）：`Dictionary.scope` 为单值标签

### 跨 Plan 依赖
- `persistence/_utils.py` → `atomic_write_json`/`validate_name`

### 引用的架构决策
- ADR-014 更新节（2026-08-14）：scope 单值切换、分享/导入（导入/导出/打开目录三能力）、词典存 `data/translation_memory/`

## 验收标准

- [ ] 提供 GUI 入口修改词典 scope（global ↔ project 切换），单值覆盖
- [ ] 「导入词典」：选 `.tbdict` 复制进 `data/translation_memory/`，同名提示覆盖/跳过
- [ ] 「导出」：选目标位置复制出去；「打开词典目录」：定位到词典目录
- [ ] 导入时校验 `.tbdict` 后缀与内容格式，损坏文件报告明确错误

## 数据流

```
set_scope(mod_file_id, new_scope)
    ├─ dict = self._dict(mod_file_id)
    ├─ dict.scope = new_scope（单值覆盖，校验 VALID_SCOPES）
    └─ save() → 重写 {mod_file_id}.tbdict

import_dict(src_path: Path)
    ├─ 校验后缀 .tbdict + 内容可 from_dict
    ├─ 目标 = data/translation_memory/{basename}
    ├─ 目标已存在 → 返回「覆盖/跳过」信号，由 GUI 请示用户
    └─ 复制 → 重新 load

export_dict(mod_file_id, dest_dir: Path)
    └─ 复制 data/translation_memory/{mod_file_id}.tbdict → dest_dir
```

## 关键接口

### 函数签名

```python
class TranslationMemoryManager:
    def set_scope(self, mod_file_id: str, scope: str) -> None:
        """切换词典 scope（单值覆盖）。"""

    def import_dict(self, src_path: Path, overwrite: bool = False) -> bool:
        """导入外部 .tbdict，同步名校验，返回是否成功。"""

    def export_dict(self, mod_file_id: str, dest_dir: Path) -> Path:
        """导出词典到目标目录，返回目标路径。"""
```

## 实现步骤

### 步骤 1: manager 层 scope 切换与导入/导出

**涉及文件**: `src/transbridge/translation_memory/manager.py`（修改）

**实现要点**:
- `set_scope`：校验 scope ∈ VALID_SCOPES，覆盖 `dict.scope`，触发保存（或由调用方显式 save）
- `import_dict`：校验 `.tbdict` 后缀、`Dictionary.from_dict` 可解析；目标同名时返回冲突（不静默覆盖），由 GUI 决定覆盖/跳过
- `export_dict`：定位 `.tbdict` 路径复制到 dest_dir

**边界条件**:
- `import_dict` 后缀非 `.tbdict` → ValueError
- 内容损坏 → RuntimeError（保留现场）
- 同名词典 → 若 overwrite=True 覆盖，否则返回 False 提示
- `export_dict` 目标目录不存在 → 创建

**伪代码**:
```python
def set_scope(self, mod_file_id, scope):
    if scope not in VALID_SCOPES:
        raise ValueError(f"非法 scope: {scope!r}")
    d = self._dict(mod_file_id)
    d.scope = scope

def import_dict(self, src_path, overwrite=False):
    src = Path(src_path)
    if src.suffix.lower() != ".tbdict":
        raise ValueError("仅支持 .tbdict 词典文件")
    data = json.loads(src.read_text(encoding="utf-8"))
    d = Dictionary.from_dict(data)  # 损坏则抛
    target = self.default_dir() / src.name
    if target.exists() and not overwrite:
        return False
    import shutil
    shutil.copy2(src, target)
    self.load(self.default_dir())
    return True
```

### 步骤 2: GUI 面板 scope 切换 + 三按钮

**涉及文件**: `src/transbridge/ui/tools/dictionary_panel.py`（修改）

**实现要点**:
- 词典列表每行加 scope 标签 + 「改 scope」入口（下拉或右键），调 `set_scope` 后刷新
- 工具栏加「导入词典」「导出」「打开目录」三按钮
- 导入：`QFileDialog` 选 `.tbdict` → `import_dict` → 同名弹 `QMessageBox` 覆盖/跳过
- 导出：选目标目录 → `export_dict`
- 打开目录：`QDesktopServices.openUrl` 打开 `default_dir()`

**边界条件**:
- 无词典可选时导出按钮禁用
- 导入同名 → 覆盖需二次确认

**伪代码**:
```python
def _on_import_dict(self):
    path, _ = QFileDialog.getOpenFileName(self, "导入词典", "", "词典文件 (*.tbdict)")
    if not path:
        return
    try:
        ok = self._manager.import_dict(Path(path))
    except (ValueError, RuntimeError) as e:
        QMessageBox.critical(self, "导入失败", str(e))
        return
    if not ok and QMessageBox.question(self, "同名词典", "存在同名词典，覆盖？") == QMessageBox.Yes:
        self._manager.import_dict(Path(path), overwrite=True)
    self._refresh()
```

**测试策略**:
- 单测：`set_scope` 单值覆盖 + 非法 scope 抛；`import_dict` 后缀校验/损坏报错/同名冲突返回 False；`export_dict` 复制成功。
- GUI 测试：面板按钮存在、导入同名弹窗逻辑（用 mock）。

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/translation_memory/manager.py` | 修改 | set_scope/import_dict/export_dict |
| `src/transbridge/ui/tools/dictionary_panel.py` | 修改 | scope 切换入口 + 导入/导出/打开目录按钮 |
| `tests/test_translation_memory.py` | 修改 | scope 切换/导入导出单测 |
| `tests/test_translation_memory_gui.py` | 修改 | 面板按钮逻辑测试 |

## 风险与注意事项

- **风险 1**: `import_dict` 覆盖同名词典可能覆盖用户本地已有译文。缓解：默认不覆盖，GUI 二次确认。
- **风险 2**: 导入的 `.tbdict` 可能来自旧版本格式。缓解：`from_dict` 对未知字段宽容（忽略），旧 `source`→`source_mod` 兜底已在 Story 06 处理。
- **注意 1**: `set_scope` 改 scope 后需立即 `save()`，否则内存与磁盘不一致。
- **注意 2**: 打开目录用 `QDesktopServices.openUrl(QUrl.fromLocalFile(str(dir)))`，需保证目录已存在（先 `mkdir`）。
