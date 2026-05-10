# Story 03: UI Stage 7 级可视化

**所属方案**: `plans/stage-unification/plan.md`
**技术模块**: ui
**状态**: 已确认
**创建日期**: 2026-05-07

## 前置依赖

### 上游 Story
- Story-01（stage-unification）：数据层 Stage 常量定义 → 提供 `STAGE_LABELS`、`STAGE_COLORS` 导入

### 引用的架构决策
- ADR-004: QThread + 信号总线异步模式

## 验收标准

- [ ] 状态标签行显示 7 个 Stage 标签（计数为 0 的隐藏）
- [ ] 筛选按实际 stage 值精确匹配
- [ ] 行首 3px Stage 色条（使用 STAGE_COLORS）
- [ ] 行背景色按 stage 区分
- [ ] AI 翻译排除 locked/hidden 条目

## 数据流

```
TranslationEntry.stage (0/1/2/3/5/9/-1)
  │
  ├─ _build_stage_tags() → 7 个标签按钮（计数为0的隐藏）
  │   └─ 点击 → _stage_filters.add/discard(stage_value)
  │
  ├─ _apply_all_filters() → e.stage in _stage_filters（精确匹配）
  │
  ├─ _populate_table() → 行首色条（setData 或 setBackground 模拟）
  │   └─ 行背景色：
  │        stage=0      → 白色（默认）
  │        stage=1,2,3,5→ 浅绿 #E8F5E9
  │        stage=9      → 浅红 #FFEBEE
  │        stage=-1     → 浅灰 #F5F5F5
  │
  └─ translator.py → 排除 stage∈{9, -1} 的条目
```

## 关键接口

### step2.py 修改

```python
from src.transbridge.converter.translation_entry import STAGE_LABELS, STAGE_COLORS

# 移除当前 _STAGE_LABELS = {0: "未翻译", 1: "有疑问", 2: "已翻译"}
# 移除当前 _STAGE_COLORS 定义

# _build_stage_tags 改为:
#   for stage_val, label in STAGE_LABELS.items():
#       统计 _entries 中 stage==stage_val 的数量
#       创建标签按钮

# _apply_all_filters 中 stage 筛选改为:
#   if self._stage_filters:
#       result = [e for e in result if e.stage in self._stage_filters]

# _populate_table 中行背景色:
#   if stage in {1,2,3,5}: bg = _ROW_BG_GREEN
#   elif stage == 9: bg = QColor("#FFEBEE")
#   elif stage == -1: bg = QColor("#F5F5F5")

# 行色条: 在 Col 0（标记列）或 Col 1（Key列）设左边框色
#   使用 item.setData(Qt.DecorationRole) 或单独处理
```

### AI 翻译排除逻辑

```python
# translator.py 和 ai_translator_window.py
from src.transbridge.converter.translation_entry import STAGE_LOCKED, STAGE_HIDDEN

# 候选条目筛选增加：
candidates = [e for e in candidates if e.stage not in (STAGE_LOCKED, STAGE_HIDDEN)]
```

## 实现步骤

### 步骤 1: Stage 标签 7 级 + 筛选修正

**涉及文件**: `src/transbridge/ui/workbench/step2.py`（修改）

**实现要点**:
- 导入 `STAGE_LABELS` 替换本地 `_STAGE_LABELS`
- `_build_stage_tags` 改为遍历 `STAGE_LABELS.items()` 生成 7 个标签
- 标签数量为 0 且未被选中时隐藏该标签
- `_STAGE_LABELS` 类属性移除

**边界条件**:
- Stage 值为负数（-1）→ Counter 正常统计
- 全部 stage 计数为 0 → 不显示「全部」标签
- 标签数量动态更新（标记/编辑后 stage 变化）

**伪代码**:
```python
def _build_stage_tags(self):
    from collections import Counter
    counter = Counter()
    for e in self._entries:
        counter[e.stage] += 1
    
    for stage_val, label in STAGE_LABELS.items():
        count = counter.get(stage_val, 0)
        if count == 0 and stage_val not in self._stage_filters:
            continue
        btn = QPushButton(f"{label} {count}")
        btn.setStyleSheet(
            self._TAG_ACTIVE if stage_val in self._stage_filters else self._TAG_NORMAL
        )
        btn.clicked.connect(lambda checked, s=stage_val: self._on_stage_tag_clicked(s))
        ...
```

### 步骤 2: 行色条 + 行背景色

**涉及文件**: `src/transbridge/ui/workbench/step2.py`（修改）

**实现要点**:
- 导入 `STAGE_COLORS`
- `_populate_table` 中为每行第一列（Key 列, _COL_KEY）设置左侧边框色条
- 通过设置 Key item 的 `setBackground` + 行背景色配合实现
- 实际实现：在每行的所有 cell 中，Key 列左侧加 3px 色条（用 QColor 设置 cell 背景，行背景色覆盖其余部分）
- 简化方案：直接为每行的 Key 单元格设 3px 宽的彩色背景条（通过 QBrush 或自定义 delegate）→ 最简单是用 `item.setData(Qt.BackgroundRole, ...)` 只对 _COL_KEY 列设置 Stage 色作为背景边框

**简化实现**（避免自定义 delegate）:
```python
# 在 _populate_table 循环中
stage_color = QColor(STAGE_COLORS.get(entry.stage, "#9E9E9E"))
# Key 列使用 stage 色作为文字颜色（同时保持可读性）
# 行背景色作为主背景
```

**实际推荐方案**（最简）：Key 列文字用 Stage 色着色，配合已有的行背景色，一眼可见 stage：

```python
key_item.setForeground(QColor(STAGE_COLORS.get(entry.stage, "#000000")))
```

**边界条件**:
- stage 值不在 STAGE_COLORS 中 → 默认黑色
- 行背景色和 Stage 色协调

### 步骤 3: AI 翻译排除 locked/hidden

**涉及文件**:
- `src/transbridge/ai_translator/translator.py`（修改）
- `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`（修改）

**实现要点**:
- 导入 `STAGE_LOCKED, STAGE_HIDDEN`
- 翻译候选条目筛选增加排除逻辑
- `ai_translator_window.py` 中的 `_update_estimate` 同样排除

**伪代码**:
```python
# translator.py 开始翻译前过滤
candidates = [
    e for e in candidates
    if e.stage not in (STAGE_LOCKED, STAGE_HIDDEN)
]
```

**测试策略**:
- Paratranz 下载含 stage=9 条目 → AI 翻译不选中
- Paratranz 下载含 stage=-1 条目 → AI 翻译不选中

### 步骤 4: 同步引用 `_strings_common.py`

**涉及文件**: `src/transbridge/ui/paratranz/_strings_common.py`（修改）

**实现要点**:
- `_strings_common.py` 中的 `_STAGE_LABELS` 保留 `-2: "全部"` 哨兵值，其余值改为从 `translation_entry.STAGE_LABELS` 合并
- `_strings_common.py` 中的 `_STAGE_COLORS` 改为从 `translation_entry.STAGE_COLORS` import

**简化方案**:
```python
from src.transbridge.converter.translation_entry import STAGE_LABELS, STAGE_COLORS

_STAGE_LABELS = {-2: "全部", **STAGE_LABELS}
_STAGE_COLORS = STAGE_COLORS
```

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/workbench/step2.py` | 修改 | Stage 标签 7 级 + 筛选 + 色条 + 背景色 |
| `src/transbridge/ai_translator/translator.py` | 修改 | 排除 locked/hidden |
| `src/transbridge/ui/tools/ai_translator/ai_translator_window.py` | 修改 | 排除 locked/hidden |
| `src/transbridge/ui/paratranz/_strings_common.py` | 修改 | 引用统一常量 |

## 风险与注意事项

- **风险 1**: 7 个 Stage 标签可能导致标签行过长。缓解：计数为 0 的标签自动隐藏（最常见情况只有 2-3 个标签）；可换行显示
- **注意 1**: `STAGE_COLORS` 中 stage=-1 的颜色 `#616161`（深灰）用于文字可能对比度不够。可用于背景色条而非文字色
- **注意 2**: `_strings_common.py` 被 ParaTranz 管理面板使用，修改其 `_STAGE_LABELS` 时需确保不影响 ParaTranz 标签页的 stage 下拉框
