# Story 09: 组合式作用域选择器

**所属方案**: `plans/ai-translation/plan.md`
**技术模块**: ui (PyQt6)
**状态**: 已确认
**创建日期**: 2026-05-07

## 前置依赖

### 上游 Story
- Story-05（同 plan）：AutoTranslator — 翻译入口 `translate(candidates)` 方法
- Story-22（ui-workbench）：标记系统 — `_entry_marks` 和 `_MARK_LABELS` 供作用域面板引用

### 跨 Plan 依赖
- `stage-unification` → `STAGE_LABELS, STAGE_COLORS`（已冻结）
- `ui-workbench` → `_ALL_CATEGORIES, _entry_category()`（分类映射）

### 引用的架构决策
- ADR-003: 三轮 AI 翻译策略
- ADR-004: QThread + 信号总线异步模式

## 验收标准

- [ ] 3 个 RadioButton 替换为组合式作用域面板（翻译状态 + 标记 + 分类三维度标签）
- [ ] 快捷预设按钮（全部未翻译/已翻译条目/当前主表视图）
- [ ] 翻译模式默认选中「未翻译」标签，润色模式默认选中「已翻译」标签
- [ ] 覆盖策略复选框保留现有行为
- [ ] `_update_estimate` 按三维度筛选实时显示预估条目数
- [ ] 不再调用 `get_selected_entries()`，与主表标记系统完全解耦

## 数据流

```
窗口打开 / 模式切换
  │
  ├─ 翻译模式 → _reset_scope_to_default(is_polish=False)
  │   └─ _scope_stage_filters = {0}（未翻译）
  │
  └─ 润色模式 → _reset_scope_to_default(is_polish=True)
      └─ _scope_stage_filters = {1,2,3,5}（有译文状态）

用户点击标签 → _scope_*_filters 更新 → _update_estimate()
  │
  └─ _build_scope_candidates()
      ├─ collection 全量 → 按 _scope_stage_filters 筛选
      ├─ 按 _scope_mark_filters → 查 _entry_marks（需从主表获取）
      └─ 按 _scope_category_filters → 用 _entry_category() 匹配
      → 排除 STAGE_LOCKED/STAGE_HIDDEN
      → 返回 candidates

开始翻译 → candidates = _build_scope_candidates()
  → if not overwrite → 排除已有译文
  → translator.translate(candidates)
```

## 关键接口

### 新增属性

```python
# 作用域筛选状态（替换 _scope_all/_scope_filtered/_scope_selected）
_scope_stage_filters: set[int]         # STAGE_* 值，空=不限
_scope_mark_filters: set[str]          # "star"/"question"/"confirmed"，空=不限
_scope_category_filters: set[str]      # 分类名，空=不限
_scope_preset: str | None              # "untranslated" / "translated" / "table_view" / None
```

### 新增方法

```python
def _build_scope_candidates(self) -> list[TranslationEntry]:
    """按三维度筛选候选条目，与主表完全解耦"""
    ...

def _reset_scope_to_default(self, is_polish: bool):
    """模式切换时重置作用域默认值"""
    ...

def _on_scope_stage_clicked(self, stage: int | None):
    """状态维度标签点击"""
    ...

def _on_scope_mark_clicked(self, mark: str | None):
    """标记维度标签点击"""
    ...

def _on_scope_category_clicked(self, cat: str | None):
    """分类维度标签点击"""
    ...

def _on_preset_clicked(self, preset: str):
    """快捷预设点击"""
    ...
```

### 修改的方法

```python
# _update_estimate 改为：
def _update_estimate(self):
    candidates = self._build_scope_candidates()
    # 应用 overwrite 策略
    # 调用 BatchPlanner.plan()
    # 更新 _estimate_lbl

# _on_start_translate 改为：
def _on_start_translate(self):
    candidates = self._build_scope_candidates()
    # 启动翻译 Worker
```

## 实现步骤

### 步骤 1: 替换 RadioButton 为三维度标签面板

**涉及文件**: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`（修改）

**实现要点**:
- 移除 `_scope_group`, `_scope_all`, `_scope_filtered`, `_scope_selected` 控件
- 移除对应信号连接 (`toggled.connect(self._update_estimate)`)
- 新建 `_scope_widget` 区域，包含三行标签：
  - 翻译状态行（"状态：" + STAGE_LABELS 标签按钮）
  - 标记行（"标记：" + _MARK_LABELS 标签按钮 + "不限"）
  - 分类行（"分类：" + _ALL_CATEGORIES 标签按钮 + "不限"）
- 复用现有 `_TAG_NORMAL/_TAG_ACTIVE` 样式

**边界条件**:
- 无 collection 时所有标签禁用
- 标记维度标签需从主表的 `_entry_marks` 统计数据，但筛选逻辑独立

**伪代码**:
```python
def _init_scope_section(self, parent_layout):
    # 状态维度
    stage_row = QHBoxLayout()
    stage_row.addWidget(QLabel("状态："))
    for stage_val, label in STAGE_LABELS.items():
        btn = QPushButton(label)
        btn.clicked.connect(lambda checked, s=stage_val: self._on_scope_stage_clicked(s))
        stage_row.addWidget(btn)
    
    # 标记维度
    mark_row = QHBoxLayout()
    mark_row.addWidget(QLabel("标记："))
    all_btn = QPushButton("不限")
    all_btn.clicked.connect(lambda: self._on_scope_mark_clicked(None))
    mark_row.addWidget(all_btn)
    for mk, label in _MARK_LABELS.items():
        btn = QPushButton(label)
        btn.clicked.connect(lambda checked, m=mk: self._on_scope_mark_clicked(m))
        mark_row.addWidget(btn)
    
    # 分类维度（同上模式）
    ...
```

### 步骤 2: 快捷预设按钮

**涉及文件**: 同上

**实现要点**:
- 在作用域面板顶部添加预设按钮行
- 「全部未翻译」→ `_scope_stage_filters = {0}`, 其他维度清空
- 「已翻译条目」→ `_scope_stage_filters = {1,2,3,5}`, 其他维度清空
- 「当前主表视图」→ 从主表获取当前筛选后的条目 ID 集合，作为候选

**边界条件**:
- 「当前主表视图」快捷方式：需访问主表，通过 `self._step2._apply_all_filters()` 获取当前筛选结果，取 ID 集合
- 预设按钮互斥（选中一个取消其他）

**伪代码**:
```python
def _on_preset_clicked(self, preset: str):
    self._scope_stage_filters.clear()
    self._scope_mark_filters.clear()
    self._scope_category_filters.clear()
    
    if preset == "untranslated":
        self._scope_stage_filters = {0}
    elif preset == "translated":
        self._scope_stage_filters = {1, 2, 3, 5}
    elif preset == "table_view":
        self._scope_preset = "table_view"
    
    self._rebuild_scope_tags()
    self._update_estimate()
```

### 步骤 3: 翻译/润色自动适应

**涉及文件**: 同上

**实现要点**:
- 在模式切换回调（已有的 `_on_mode_changed` 或翻译/润色 radio toggle）中调用 `_reset_scope_to_default(is_polish)`
- 翻译模式 → `_scope_stage_filters = {0}`（未翻译）
- 润色模式 → `_scope_stage_filters = {1, 2, 3, 5}`（已翻译相关状态）
- 清空标记和分类维度

**边界条件**:
- 用户手动修改过作用域后切换模式 → 重置为默认值（覆盖用户修改）
- 或者：仅首次进入时设置默认值，模式切换保留用户设置 → 选一个策略

**推荐策略**: 模式切换时始终重置为默认值（最安全，用户可手动调回来）

**伪代码**:
```python
def _reset_scope_to_default(self, is_polish: bool):
    self._scope_mark_filters.clear()
    self._scope_category_filters.clear()
    self._scope_preset = None
    
    if is_polish:
        self._scope_stage_filters = {1, 2, 3, 5}
    else:
        self._scope_stage_filters = {0}
    
    self._rebuild_scope_tags()
    self._update_estimate()
```

### 步骤 4: _update_estimate 重写

**涉及文件**: 同上

**实现要点**:
- 调用 `_build_scope_candidates()` 获取候选条目
- 应用 overwrite 策略（翻译模式不覆盖已有译文，润色模式覆盖）
- 调用 `BatchPlanner.plan(candidates)` 计算预估
- 更新 `_estimate_lbl` 显示

**边界条件**:
- candidates 为空 → 显示 "无匹配条目，请调整作用域"
- 「当前主表视图」预设 → 直接从主表获取筛选结果

**伪代码**:
```python
def _update_estimate(self):
    collection = self._ctx.collection
    if collection is None:
        self._estimate_lbl.setText("预计：— 条（需先加载集合）")
        return
    
    candidates = self._build_scope_candidates()
    
    overwrite = self._overwrite_check.isChecked()
    if not overwrite:
        candidates = [e for e in candidates if not e.translation or e.stage == 0]
    
    # 排除 locked/hidden
    from src.transbridge.converter.translation_entry import STAGE_LOCKED, STAGE_HIDDEN
    candidates = [e for e in candidates if e.stage not in (STAGE_LOCKED, STAGE_HIDDEN)]
    
    if not candidates:
        self._estimate_lbl.setText("预计：0 条（无匹配条目，请调整作用域）")
        return
    
    planner = BatchPlanner(max_tokens_per_batch=self._tokens_spin.value())
    plan = planner.plan(candidates)
    self._estimate_lbl.setText(f"预计：{plan.total_entries()} 条 ...")
```

### 步骤 5: 与主表解耦

**涉及文件**: 同上

**实现要点**:
- 移除所有 `self._step2.get_selected_entries()` 调用
- 翻译开始时调用 `_build_scope_candidates()` 构建候选列表
- 窗口不再依赖主表的选中状态

**边界条件**:
- 「当前主表视图」预设仍需与主表交互（读取筛选结果），但这是"读主表筛选状态"而非"读主表选中状态"，语义不同
- `_scope_filtered` 旧 RadioButton 的概念被「当前主表视图」预设替代

**伪代码**:
```python
def _build_scope_candidates(self) -> list[TranslationEntry]:
    collection = self._ctx.collection
    if collection is None:
        return []
    
    if self._scope_preset == "table_view":
        # 从主表获取当前筛选后的条目
        return self._step2._apply_all_filters()
    
    candidates = list(collection)
    
    # Stage 筛选
    if self._scope_stage_filters:
        candidates = [e for e in candidates if e.stage in self._scope_stage_filters]
    
    # 标记筛选（从主表获 取 _entry_marks）
    if self._scope_mark_filters:
        marks = self._step2._entry_marks
        candidates = [e for e in candidates if e.id and marks.get(e.id) in self._scope_mark_filters]
    
    # 分类筛选
    if self._scope_category_filters:
        candidates = [e for e in candidates if _entry_category(e) in self._scope_category_filters]
    
    return candidates
```

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/tools/ai_translator/ai_translator_window.py` | 修改 | 作用域面板重构（核心改动） |
| `src/transbridge/ai_translator/translator.py` | 检查 | 确认无需修改（已在 Stage-03 排除 locked/hidden） |

## 风险与注意事项

- **风险 1**: 标记维度数据来自主表 `_step2._entry_marks`，形成弱耦合。缓解：仅读取数据，不修改主表状态；若窗口需要独立标记数据，后续 FR8 持久化会解决
- **注意 1**: 分类维度需要 `_entry_category()` 函数和 `_ALL_CATEGORIES` 列表，当前在 step2.py 中定义。建议不移动（避免循环导入），直接在 ai_translator_window.py 中复制引用或 import
- **注意 2**: 旧 RadioButton 的 `toggled.connect` 信号需彻底清理，避免残留信号触发已删除的 UI 更新
