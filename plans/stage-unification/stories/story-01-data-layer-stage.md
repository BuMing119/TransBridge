# Story 01: 数据层 Stage 映射修正

**所属方案**: `plans/stage-unification/plan.md`
**技术模块**: converter
**状态**: 已确认
**创建日期**: 2026-05-07

## 前置依赖

### 上游 Story
- 无（本 Story 为独立起点）

### 引用的架构决策
- ADR-001: TranslationEntry 作为统一翻译数据模型

## 验收标准

- [ ] converter 中各数据源的 stage 赋值语义正确
- [ ] downloader 透传 ParaTranz stage 值正确
- [ ] 新增 Stage 常量定义模块供全项目引用

## 数据流

```
ESP 解析 → stage=0（未翻译）
XT/EET/Strings 导入（有译文）→ stage=1（已翻译）
DSD JSON 导入（有译文）→ stage=1（已翻译）
ParaTranz 下载 → stage=API返回值（0/1/2/3/5/9/-1）
```

## 关键接口

### 新增常量（translation_entry.py）

```python
# Stage 常量 — 对齐 ParaTranz 平台
STAGE_UNTRANSLATED = 0   # 未翻译
STAGE_TRANSLATED = 1     # 已翻译
STAGE_QUESTIONABLE = 2   # 有疑问
STAGE_CHECKED = 3        # 已检查
STAGE_REVIEWED = 5       # 已审核
STAGE_LOCKED = 9         # 已锁定
STAGE_HIDDEN = -1        # 已隐藏

STAGE_LABELS = {
    0: "未翻译",
    1: "已翻译",
    2: "有疑问",
    3: "已检查",
    5: "已审核",
    9: "已锁定",
    -1: "已隐藏",
}

STAGE_COLORS = {
    0: "#9E9E9E",
    1: "#2196F3",
    2: "#FF9800",
    3: "#00BCD4",
    5: "#4CAF50",
    9: "#B71C1C",
    -1: "#616161",
}
```

### 现有数据源 stage 赋值（语义确认）

| 位置 | 代码 | 当前语义 | 修正后语义 | 改动 |
|------|------|---------|-----------|------|
| `translation_entry.py:43` EET | `stage=1 if status==99 or traduit else 0` | 机翻 | **已翻译** | 仅注释 |
| `translation_entry.py:99` Plugin | `stage=0` | 未翻译 | **未翻译** | 无 |
| `translation_entry.py:155` XT | `entry.stage == 0` 判断 | 未翻译 | **未翻译** | 无 |
| `translation_entry.py:170` XT | `stage=1` | 机翻 | **已翻译** | 仅注释 |
| `translation_entry.py:290` DSD | `stage=1 if string else 0` | 有译文 | **已翻译** | 仅注释 |
| `collection.py:173/208` EET | `stage=1 if status==99 or traduit else 0` | 机翻 | **已翻译** | 仅注释 |
| `collection.py:320` XT | `stage=1` | 机翻 | **已翻译** | 仅注释 |
| `collection.py:370/403/458` | `stage=1` | 机翻 | **已翻译** | 仅注释 |
| `downloader.py:120` | `stage=stage` (API 透传) | 混合 | **直接透传** | 添加注释 |

## 实现步骤

### 步骤 1: 新增 Stage 常量定义

**涉及文件**: `src/transbridge/converter/translation_entry.py`（修改）

**实现要点**:
- 在 `TranslationEntry` 类定义之前添加 `STAGE_*` 常量和 `STAGE_LABELS`、`STAGE_COLORS` 映射
- 常量放在模块级别，供 `from src.transbridge.converter.translation_entry import STAGE_LABELS` 导入
- `_STAGE_COLORS` 从 `step2.py` 中移除（改为引用这里），`_strings_common.py` 中的 `_STAGE_LABELS` 改为引用这里

**边界条件**:
- 常量值使用 ParaTranz 实际值（负数、跳号都是正常的）
- `STAGE_LABELS` 不包含 `-2: "全部"`（那是 paratranz UI 专用哨兵值）

**伪代码**:
```python
# 在 translation_entry.py 中，TranslationEntry 类定义之前：

# Stage definitions aligned with ParaTranz platform
STAGE_UNTRANSLATED = 0
STAGE_TRANSLATED = 1
STAGE_QUESTIONABLE = 2
STAGE_CHECKED = 3
STAGE_REVIEWED = 5
STAGE_LOCKED = 9
STAGE_HIDDEN = -1

STAGE_LABELS: dict[int, str] = {
    0: "未翻译",
    1: "已翻译",
    2: "有疑问",
    3: "已检查",
    5: "已审核",
    9: "已锁定",
    -1: "已隐藏",
}

STAGE_COLORS: dict[int, str] = {
    0: "#9E9E9E",
    1: "#2196F3",
    2: "#FF9800",
    3: "#00BCD4",
    5: "#4CAF50",
    9: "#B71C1C",
    -1: "#616161",
}
```

### 步骤 2: 修正数据源 stage 注释和对齐引用

**涉及文件**: 
- `src/transbridge/converter/translation_entry.py`（修改）
- `src/transbridge/converter/translation_entry_collection.py`（修改）
- `src/transbridge/paratranz/workflow/downloader.py`（修改）

**实现要点**:
- 各 stage 赋值行的注释从"机翻/有译文"统一改为引用 `STAGE_*` 常量
- `downloader.py` 添加注释说明 ParaTranz API 返回值直接透传的合理性
- 实际 stage 值不变（`stage=1` 保持不变，仅语义更新）

**边界条件**:
- 不改变任何运行时的 stage 赋值逻辑
- 已有条目通过 `from_dict` 反序列化时 stage 值不变

**测试策略**:
- 解析一个 ESP → 验证所有条目 stage=0
- 导入 XT 迁移源 → 验证有译文条目 stage=1
- 导入 DSD JSON → 验证有译文条目 stage=1
- 下载 ParaTranz → 验证 stage 值正确透传

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/converter/translation_entry.py` | 修改 | 新增 STAGE_* 常量 + 修正注释 |
| `src/transbridge/converter/translation_entry_collection.py` | 修改 | 修正 stage 赋值注释 |
| `src/transbridge/paratranz/workflow/downloader.py` | 修改 | 添加透传注释 |

## 风险与注意事项

- **注意 1**: `STAGE_LABELS` 不包含 `-2: "全部"` 哨兵值。该值仅用于 `strings_tab.py` 的 UI 下拉框，应在 `_strings_common.py` 中单独维护
- **注意 2**: 常量放在 `translation_entry.py` 而非新文件，避免循环导入（其他模块已经 import translation_entry）
