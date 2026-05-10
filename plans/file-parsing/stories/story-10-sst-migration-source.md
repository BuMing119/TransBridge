# Story 10: SST 迁移源集成

**所属方案**: `plans/file-parsing/plan.md`
**技术模块**: converter, ui
**状态**: 已确认
**创建日期**: 2026-05-08

## 前置依赖

### 上游 Story
- Story-09（同 plan）：已完成 → 提供 `SST_Parser.from_file()`、`SST_Entry`、`create_from_sst_entry()`

### 跨 Plan 依赖
- `core-data-model/plan.md` → `TranslationEntry` dataclass（ADR-001 已冻结）
- `core-data-model/plan.md` → `TranslationEntryCollection` + `add(overwrite=True)`
- `ui-workbench/plan.md` → Step1 `CollectionSlot`、迁移源按钮区域布局

### 引用的架构决策
- ADR-001: TranslationEntry 统一数据模型
- ADR-002: Collection 数据中枢与双索引设计

## 验收标准

（从 plan 原样复制）

- [ ] `try_update_from_sst()` 正确匹配 form_id + index 相同的条目，返回更新后的 TranslationEntry
- [ ] `apply_sst_entries()` 批量合并返回正确统计（匹配/更新/跳过数）
- [ ] Step1 UI SST 加载按钮可见，点击弹出 .sst 文件选择对话框
- [ ] 加载后 SST 译文合并到当前集合，统计结果反馈用户
- [ ] SSU9 的 `translated_text` 作为译文来源；SSU8 无译文仅作原文参考
- [ ] 不修改 `XT_XmlParser` 的行为（FR1.9.4）

## 数据流

```
用户在 Step1 点击"加载 SST"
    │
    ▼
QFileDialog.getOpenFileName(filter="SST files (*.sst)")
    │
    ▼
ApiWorker（后台线程）
    ├─ SST_Parser.from_file(sst_path)
    │    └─ entries: list[SST_Entry]  (SSU8: 仅原文; SSU9: 原文+译文)
    │
    ├─ collection.apply_sst_entries(sst_entries)
    │    │
    │    └─ for each SST_Entry:
    │         └─ TranslationEntry.try_update_from_sst(entry, sst_entry)
    │              │
    │              ├─ 1. 解析 ESP ID
    │              │     entry.id = "{editor_id}:{form_id}|{index}~{TYPE}"
    │              │     → form_id_hex = id.split(":")[1].split("|")[0]
    │              │     → entry_index = int(id.split("|")[1].split("~")[0])
    │              │
    │              ├─ 2. form_id 匹配
    │              │     f"{sst_entry.form_id:08X}" == form_id_hex
    │              │
    │              ├─ 3. index 匹配
    │              │     entry_index == sst_entry.index
    │              │
    │              └─ 4. 更新条件
    │                    stage==0 AND translation=="" AND sst.translated_text!=""
    │                    → translation = sst.translated_text, stage = STAGE_TRANSLATED
    │
    └─ 返回 {matched: N, updated: N, skipped: N}
    ▼
UI 弹窗反馈: "SST 加载完成：匹配 X 条，更新 Y 条，跳过 Z 条"
```

## 关键接口

### try_update_from_sst

```python
# translation_entry.py — TranslationEntry 类方法

@classmethod
def try_update_from_sst(
    cls,
    entry: "TranslationEntry",
    sst: "SST_Entry",
) -> "TranslationEntry | None":
    """尝试用 SST_Entry 更新已有的 TranslationEntry。

    匹配策略: form_id + index（SST 有 form_id，比 XT 的 edid+index 更精确）

    :param entry: 集合中已有的 TranslationEntry（通常来自 ESP 解析）
    :param sst: SST 文件中的一条记录
    :return: 匹配但不满足更新条件 → 返回原 entry
             匹配且满足条件 → 返回更新后的新 entry
             不匹配 → 返回 None
    """
```

**匹配步骤**:

1. 从 `entry.id` 解析 form_id 和 index
   - 格式: `{editor_id}:{form_id}|{index}` 或 `{editor_id}:{form_id}|{index}~{TYPE}`
   - `id_right = entry.id.split(":")[1]`
   - `form_id_hex = id_right.split("|")[0]`（无 0x 前缀的 hex 字符串，如 "7729721C"）
   - `index_str = id_right.split("|")[1].split("~")[0]`
   - `entry_index = int(index_str) if index_str else None`

2. form_id 匹配: `f"{sst.form_id:08X}" == form_id_hex.upper()`

3. index 匹配: `entry_index is None or entry_index == sst.index`

4. 更新条件:
   - `entry.stage == STAGE_UNTRANSLATED (0)`
   - `not entry.translation`
   - `bool(sst.translated_text)`

5. 返回: `cls(translation=sst.translated_text, stage=STAGE_TRANSLATED, **保留其他字段)`

### apply_sst_entries

```python
# translation_entry_collection.py — TranslationEntryCollection 方法

def apply_sst_entries(
    self,
    sst_entries: Iterable["SST_Entry"],
) -> dict:
    """将 SST_Entry 批量应用到集合中的 TranslationEntry。

    遍历 SST entries，对每条通过 form_id 查找集合中匹配的条目，
    调用 try_update_from_sst() 尝试更新。

    :return: {"matched": int, "updated": int, "skipped": int}
    """
```

**实现要点**:
- 构建 form_id → SST_Entry 的查找索引（应对同一 form_id 多条 SST 记录的场景：多 index）
- 遍历集合条目，通过 `entry.id` 提取 form_id_hex，查找匹配 SST 条目
- 对找到的 SST 条目调用 `try_update_from_sst()`
- 更新后替换 `self._entries` 和 `self._key_index` 中的条目
- 统计: matched（找到对应 SST）、updated（实际更新了译文）、skipped（匹配但未满足更新条件）
- **SSU8 注意**: `translated_text` 为空时无条件跳过更新

### Step1 UI

**按钮位置**: 迁移源按钮区域，与"加载 EET""加载 XT""加载 Strings"并列

**实现要点**:
- 按钮文本: "加载 SST"
- 启用条件: 当前有已加载的集合（`slot.esp_path is not None` 或 slot 非空）
- 文件过滤: `"SST files (*.sst)"`
- 后台线程: 复用 `ApiWorker` 模式（参考现有 EET/XT 迁移源加载）
- 进度: `"正在解析 SST..."` → `"正在合并译文..."` → `"完成"`
- 完成后弹窗: `QMessageBox.information(f"SST 加载完成\n匹配: {matched} 条\n更新: {updated} 条\n跳过: {skipped} 条")`

## 实现步骤

### 步骤 1: try_update_from_sst()

**涉及文件**: `src/transbridge/converter/translation_entry.py`（修改）

**实现要点**:
- 在 `try_update_from_xt()` 方法之后新增 `try_update_from_sst()`
- 从 `TranslationEntry.id` 解析 form_id 和 index
- form_id 比较: int → hex string（`f"{sst.form_id:08X}"`）与 id 中的 hex 字符串比较
- 更新条件: stage==0, translation 为空, SST 有 translated_text
- 返回新实例时保留 `id/key/original/context/form_id_with_plugin/string_id/dsd_type/dsd_index/editor_id`

**边界条件**:
- `entry.id` 不含 `~` 后缀 → 正常解析，不会有 TYPE 部分
- `entry.id` 的 form_id 部分含 `|plugin` 后缀（如 "7729721C|Skyrim.esm"）→ 只取 `|plugin` 前的纯 hex 部分
- `sst.index == 0`（SSU9 无 sub-index 的记录）→ 匹配时 index 比较放宽：entry_index 为 None 或相等
- `sst.translated_text` 为空（SSU8）→ 不更新，返回原 entry
- form_id hex 大小写不一致 → 统一 upper()

**伪代码**:
```python
@classmethod
def try_update_from_sst(cls, entry, sst):
    # 1. 解析 ESP ID
    after_colon = entry.id.split(":", 1)[1]  # "form_id|index" or "form_id|index~TYPE"
    form_id_part = after_colon.split("|")[0]  # "7729721C" or "7729721C|Skyrim.esm"
    form_id_hex = form_id_part.split("|")[0]  # strip plugin suffix

    rest = after_colon.split("|", 1)[1]  # "index" or "index~TYPE"
    index_str = rest.split("~")[0]
    entry_index = int(index_str) if index_str else None

    # 2. form_id 匹配
    sst_form_id_hex = f"{sst.form_id:08X}"
    if sst_form_id_hex != form_id_hex.upper():
        return None

    # 3. index 匹配
    if entry_index is not None and entry_index != sst.index:
        return None

    # 4. 判断是否更新
    should_update = (
        entry.stage == STAGE_UNTRANSLATED
        and not entry.translation
        and bool(sst.translated_text)
    )
    if not should_update:
        return entry

    # 5. 返回更新后的实例
    return cls(
        id=entry.id, key=entry.key,
        original=entry.original,
        translation=sst.translated_text,
        stage=STAGE_TRANSLATED,
        context=entry.context,
        form_id_with_plugin=entry.form_id_with_plugin,
        string_id=entry.string_id,
        dsd_type=entry.dsd_type,
        dsd_index=entry.dsd_index,
        editor_id=entry.editor_id,
    )
```

**测试策略**:
- 构造 ESP 格式 ID 的 entry 和 SST_Entry，验证正确匹配和更新
- form_id 不匹配 → 返回 None
- index 不匹配 → 返回 None
- stage != 0 → 返回原 entry（不更新）
- 已有 translation → 返回原 entry（不更新）
- SST translated_text 为空 → 返回原 entry（不更新）
- form_id 含插件后缀 → 正确解析

### 步骤 2: apply_sst_entries()

**涉及文件**: `src/transbridge/converter/translation_entry_collection.py`（修改）

**实现要点**:
- 参考 `apply_xt_entries()` 的两阶段模式，但 SST 匹配更简单（form_id 直接查找）
- Phase 1: 构建 `form_id → list[SST_Entry]` 索引
- Phase 2: 遍历集合条目，通过 form_id 查找候选 SST entries
- 对每个候选调用 `try_update_from_sst()`
- 注意: 同一 form_id 可能有多条 SST 记录（不同 index），需要全部尝试匹配
- 返回 `{"matched": int, "updated": int, "skipped": int}`

**边界条件**:
- 集合为空 → 返回 `{matched: 0, updated: 0, skipped: 0}`
- SST entries 为空 → 同上
- 同一 form_id 对应多条 SST（多 index 对话）→ 遍历全部，index 匹配的自然命中
- 一个 entry 匹配多条 SST → 只更新第一次匹配的（break）

**伪代码**:
```python
def apply_sst_entries(self, sst_entries):
    all_sst = list(sst_entries)

    # Phase 1: 按 form_id 建立索引
    sst_by_form_id = defaultdict(list)
    for sst in all_sst:
        form_id_hex = f"{sst.form_id:08X}"
        sst_by_form_id[form_id_hex].append(sst)

    # Phase 2: 遍历集合条目，尝试匹配
    matched = updated = skipped = 0
    for entry in list(self._entries.values()):
        after_colon = entry.id.split(":", 1)[1]
        form_id_hex = after_colon.split("|")[0].split("|")[0].upper()

        candidates = sst_by_form_id.get(form_id_hex, [])
        if not candidates:
            continue

        matched += 1
        entry_updated = False
        for sst in candidates:
            result = TranslationEntry.try_update_from_sst(entry, sst)
            if result is None:
                continue
            if result is not entry:
                self._entries[entry.id] = result
                self._key_index[entry.key] = result
                entry = result
                updated += 1
                entry_updated = True
            break  # 匹配成功（无论是否更新）

        if not entry_updated:
            skipped += 1

    return {"matched": matched, "updated": updated, "skipped": skipped}
```

**测试策略**:
- 空集合 + 空 SST → 全零统计
- 1 条匹配 → matched=1, updated=1, skipped=0
- 匹配但不满足更新条件 → matched=1, updated=0, skipped=1
- form_id 不匹配 → matched=0
- 同一 form_id 多条 SST → 正确按 index 匹配

### 步骤 3: Step1 UI SST 加载入口

**涉及文件**: `src/transbridge/ui/workbench/step1.py`（修改）

**实现要点**:
- 找到现有迁移源按钮区域（EET/XT/Strings 加载按钮所在位置）
- 新增 `QPushButton("加载 SST")`
- 连接 `clicked` → `self._on_load_sst()`
- `_on_load_sst()`:
  1. 获取当前 slot（`self._app_context.current_slot`）
  2. `QFileDialog.getOpenFileName(filter="SST files (*.sst)")`
  3. 创建 `ApiWorker`，在线程中执行: `SST_Parser.from_file()` → `collection.apply_sst_entries()`
  4. 进度回调: `"解析 SST…"` / `"合并译文…"`
  5. 完成回调: `QMessageBox` 显示统计
  6. 错误处理: SST 解析失败 → 显示错误对话框

**边界条件**:
- 无已加载集合 → 按钮禁用
- 用户取消文件选择 → 无操作
- SST 文件格式错误 → 显示错误信息，不崩溃
- SST 解析警告（截断等） → 日志记录，继续处理

**测试策略**:
- 手动测试: 点击按钮 → 选择 .sst → 查看统计弹窗

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/converter/translation_entry.py` | 修改 | 新增 `try_update_from_sst()` 类方法 |
| `src/transbridge/converter/translation_entry_collection.py` | 修改 | 新增 `apply_sst_entries()` 方法 |
| `src/transbridge/ui/workbench/step1.py` | 修改 | 新增"加载 SST"按钮 + `_on_load_sst()` 回调 |

## 风险与注意事项

- **风险 1**: 同一 form_id 在集合中存在多条（不同 editor_id 但同 FormID 的 INFO 条目）→ index 可作为二级区分；极端情况下 index 也相同时，先匹配的条目会被更新
- **注意 1**: SST 的 form_id 是 int，ESP ID 中的 form_id 是 hex 字符串（可能含 `|plugin` 后缀），需要统一格式后再比较
- **注意 2**: SSU8 的 `translated_text` 始终为空，SST 迁移源仅对 SSU9 有效
- **注意 3**: Step1 已有迁移源按钮的启用/禁用逻辑需要扩展，将 SST 按钮纳入状态管理
