# Story 02: 写回 Stage 修正

**所属方案**: `plans/stage-unification/plan.md`
**技术模块**: writer
**状态**: 已确认
**创建日期**: 2026-05-07

## 前置依赖

### 上游 Story
- Story-01（stage-unification）：数据层 Stage 常量定义 → 提供 `STAGE_*` 常量引用

### 引用的架构决策
- ADR-001: TranslationEntry 数据模型

## 验收标准

- [ ] EET 写回正确处理 stage>=1 的条目（stage=2 手动编辑不丢失）
- [ ] 已锁定（stage=9）强制写回译文
- [ ] 已隐藏（stage=-1）强制写回原文

## 数据流

```
TranslationEntry.stage
  │
  ├─ stage = 0（未翻译）     → EET status="0", 写原文
  ├─ stage = 1,2,3,5（有译文）→ EET status="99", 写译文
  ├─ stage = 9（已锁定）     → EET status="99", 强制写译文（忽略译文是否为空）
  └─ stage = -1（已隐藏）    → EET status="0", 强制写原文（忽略译文）
```

## 关键接口

### EET XML 写回逻辑（eet_xml_writer.py）

```python
# 当前（BUG）:
status_node.text = "99" if entry.stage == 1 else "0"

# 修正后:
if entry.stage == STAGE_HIDDEN:
    status_node.text = "0"
    # 强制写原文：不写入 translation 节点
elif entry.stage == STAGE_LOCKED:
    status_node.text = "99"
    # 强制写译文：无条件写入 translation
elif entry.stage >= STAGE_TRANSLATED and entry.translation:
    status_node.text = "99"
else:
    status_node.text = "0"
```

## 实现步骤

### 步骤 1: 修正 EET XML 写回

**涉及文件**: `src/transbridge/writer/eet_xml_writer.py`（修改）

**实现要点**:
- `status_node.text` 判断从 `stage == 1` 改为 `stage >= 1 and entry.translation`
- 新增 `stage == 9`（已锁定）强制写回译文，即使 translation 为空也写 status=99
- 新增 `stage == -1`（已隐藏）强制写回原文，不写入 translation 元素

**边界条件**:
- stage=2（有疑问）+ 有译文 → 写回 status=99（当前会错误写为 status=0）
- stage=9（已锁定）+ 译文为空 → 仍写 status=99（管理员锁定）
- stage=-1（已隐藏）+ 有译文 → 写 status=0，不输出译文（平台强制）

**伪代码**:
```python
from src.transbridge.converter.translation_entry import (
    STAGE_HIDDEN, STAGE_LOCKED, STAGE_TRANSLATED
)

def _write_entry(self, entry, ...):
    # 确定 status
    if entry.stage == STAGE_HIDDEN:
        status = "0"      # 强制原文
    elif entry.stage == STAGE_LOCKED:
        status = "99"     # 强制译文
    elif entry.stage >= STAGE_TRANSLATED and entry.translation:
        status = "99"     # 正常有译文
    else:
        status = "0"      # 未翻译
    
    status_node.text = status
    
    # 写回译文内容
    if status == "99":
        # 写入 translation 元素
        ...
    # status == "0" 时不写 translation（写原文）
```

**测试策略**:
- stage=2 有译文条目 → 写回后 status=99
- stage=9 条目 → 写回后 status=99（无论是否有译文）
- stage=-1 条目 → 写回后 status=0（无论是否有译文）

### 步骤 2: 检查 Plugin Writer

**涉及文件**: `src/transbridge/writer/plugin_writer.py`（检查）

**实现要点**:
- 检查 `plugin_writer.py` 中是否有类似的 stage 判断
- Plugin writer 写回 ESP 插件，需要确认是否也受 stage 值影响

**边界条件**:
- 如果 plugin_writer 直接用 `entry.translation` 判断是否写回，可能不需要改
- 如果用了 `entry.stage` 判断，需要同样修正

**测试策略**:
- Grep `plugin_writer.py` 搜索 stage 引用，确认是否需要修改

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/writer/eet_xml_writer.py` | 修改 | stage 判断逻辑修正 |
| `src/transbridge/writer/plugin_writer.py` | 检查 | 确认是否需要修改 |

## 风险与注意事项

- **风险 1**: EET 格式本身不支持 7 级状态（只有 status=0/99 二元），stage>=2 的精确状态在 EET 写回时会丢失。缓解：这是 EET 格式限制，ParaTranz 上传时会恢复精确状态
- **注意 1**: `status_node.text` 设置后，对应的 translation 元素也需要同步控制（status=0 不写 translation，status=99 写 translation）
