# Story 04: 词条键对齐迁移

**所属方案**: `plans/agent-infra-tools/plan.md`
**技术模块**: backend
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 上游 Story
- 无（依赖 translation_memory 已有能力，但本 Story 独立于词典套用）

### 引用的架构决策
- ADR-015: migrator/key_migrator.py 独立包；键对齐(仅 entry.key 精确匹配)与词典套用(键+文本)严格分离
- ADR-014 决策1: 键匹配分层（键匹配 + 原文变化检测）

## 验收标准

- [ ] 新建 src/transbridge/migrator/__init__.py + key_migrator.py，提供 migrate(old_collection, new_collection) 接口
- [ ] 按 entry.key 精确匹配：命中且原文未变 → 继承译文（stage=已翻译）；命中但原文变化 → 标记需复核；未命中 → 保留待翻译
- [ ] 不做文本兜底（文本兜底是词典套用的职责，见 Story 05）
- [ ] 返回 MigrationResult（继承数/需复核数/未命中数统计）
- [ ] 注册 Agent 工具 migrate_entries 到 editor namespace，permission=write

## 关键接口

```python
@dataclass
class MigrationResult:
    inherited: int          # 键命中且原文未变，直接继承
    needs_review: list[str] # 键命中但原文变化（entry.key 列表）
    missed: int             # 键未命中，保留待翻译

def migrate(old_collection, new_collection) -> MigrationResult:
    """按 entry.key 将旧集合译文对齐到新集合同名键条目。不修改旧集合，仅填充新集合译文。"""

def _tool_migrate_entries(args: dict, ctx) -> ToolResult: ...
```

## 数据流

```
old_collection: {key → (original, translation)}
new_collection: 遍历每个条目 e:
    key 命中旧集合?
      ├─ 是，且 _normalize_text(旧original) == _normalize_text(e.original)
      │     → e.translation = 旧translation; e.stage = 已翻译; inherited++
      ├─ 是，但原文变化 → needs_review.append(e.key)（不套用）
      └─ 否 → missed++; 保留待翻译
```

## 实现步骤

### 步骤 1: MigrationResult + migrate 核心逻辑

**涉及文件**: `src/transbridge/migrator/__init__.py`（新建）、`src/transbridge/migrator/key_migrator.py`（新建）

**实现要点**:
- 构建 old_collection 的 key → entry 映射
- 遍历 new_collection，按键查旧映射，原文对比用现有 _normalize_text 规范化
- 原文变化判定：规范化后的 original 不一致则 needs_review

**边界条件**:
- 旧集合为空 → 全部 missed
- 新旧集合都空 → 返回全 0
- 原文规范化复用 converter._normalize_text（本地浅封装）

### 步骤 2: Agent 工具注册

**涉及文件**: `src/transbridge/smart_assistant/tools/`（新建 tool_migrator.py 或扩展 tool_editor.py）

**实现要点**: migrate_entries 挂 editor namespace，permission=write（修改集合译文）

## 关键注意（id vs key 语义）

**⚠ 重要**: TranslationEntry 字段语义经历史重构已混乱——

```python
id: str          # 当前主匹配键
key: str         # 注释「现在存储原来的id值」
context: str     # 注释「现在存储原来的key值」
```

ADR-002 已将主索引从 id 切换为 key，ADR-014 又称「匹配键取值来源是 TranslationEntry.id」。二者存在冲突。**编码前必须核实**：键对齐迁移和词典查询当前实际生效的匹配字段到底是 id 还是 key——以 translation_memory/manager.py 的 save_from_collection/apply_to_collection/query 实际使用的字段为准（当前代码用 e.id）。若确认应统一为 key，需在 S04/S05 一并修正并在 changelog 记录。

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| src/transbridge/migrator/__init__.py | 新建 | 导出 migrate |
| src/transbridge/migrator/key_migrator.py | 新建 | 键对齐逻辑 |
| src/transbridge/smart_assistant/tools/tool_editor.py 或新文件 | 修改 | migrate_entries 注册 |
| tests/migrator/test_key_migrator.py | 新建 | 单测 |

## 风险与注意事项

- 风险: id/key 语义混乱导致匹配键取错 → 缓解: 编码前核对 translation_memory 实际用字段，统一后测试锁定
- 注意: 键对齐不做文本兜底，键未命中直接 missed，不能跨文本复用（那是 S05 词典套用的事）