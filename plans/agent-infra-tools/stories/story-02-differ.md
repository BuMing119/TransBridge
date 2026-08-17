# Story 02: 目录与文件差异分析

**所属方案**: `plans/agent-infra-tools/plan.md`
**技术模块**: backend
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 上游 Story
- Story 01（归档解包）：解包后的目录作为 diff 输入

### 引用的架构决策
- ADR-015: fileops/differ.py 提供 diff_directories 接口

## 验收标准

- [ ] 新建 src/transbridge/fileops/differ.py，提供 diff_directories(old_dir, new_dir) 接口
- [ ] 按相对路径对齐，识别新增(new)/删除(removed)/内容变化(changed)/不变四种状态
- [ ] 内容变化按 SHA-256 哈希判断，支持跳过特定扩展名的哈希（大文件仅比较存在性）
- [ ] 处理新旧根目录层级不一致（路径归一化，向上查找公共锚点）
- [ ] 注册 Agent 工具 diff_directories 到 archive namespace，permission=read

## 关键接口

```python
@dataclass
class DiffResult:
    added: list[str]        # 仅新版存在（相对路径）
    removed: list[str]      # 仅旧版存在
    changed: list[str]      # 内容哈希不同
    unchanged: list[str]    # 完全一致

def normalize_root(dir: str) -> Path:
    """向上查找 fomod/ 或 ModuleConfig.xml 作为公共锚点，消除包裹层级差异。"""

def diff_directories(old_dir: str, new_dir: str, *, skip_hash_exts: set[str] | None = None) -> DiffResult:
    """对比两个目录清单。skip_hash_exts（如 {'.bsa'}）内的扩展名仅比较存在性不哈希。"""

def _tool_diff_directories(args: dict, ctx) -> ToolResult: ...
```

## 实现步骤

### 步骤 1: 路径归一化 normalize_root

**涉及文件**: `src/transbridge/fileops/differ.py`（新建）

**实现要点**:
- 从目录向上遍历，找 fomod/ 子目录或 ModuleConfig.xml / info.xml，作为锚点
- 新旧目录都归一化到锚点后，相对路径可比

**边界条件**:
- 无 fomod 目录（裸 ESP mod）→ 锚点取目录自身，相对路径 = 文件路径
- 锚点检测失败 → 回退目录自身

### 步骤 2: 清单对比 + 哈希

**实现要点**:
- 收集新旧目录的 relative_path → file 映射
- 交集比哈希（skip_hash_exts 内仅比存在性），差集归 added/removed
- SHA-256 用 hashlib，大文件分块读（16MB buffer）

### 步骤 3: Agent 工具注册

**涉及文件**: `src/transbridge/smart_assistant/tools/tool_archive.py`（修改）

**实现要点**: diff_directories 工具加进 archive namespace，permission=read

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| src/transbridge/fileops/differ.py | 新建 | diff 逻辑 |
| src/transbridge/smart_assistant/tools/tool_archive.py | 修改 | 追加 diff_directories |
| tests/fileops/test_differ.py | 新建 | 单测 |

## 风险与注意事项

- 风险: 大文件哈希耗时长 → 缓解: skip_hash_exts 跳过 BSA 等大文件 + 分块读
- 注意: 新旧根目录层级不一致是真实常见场景，normalize_root 必须先做