# Story 02: Phase 1 — summary 字段 + build_tool_directory + build_tool_help

**所属方案**: `plans/tool-prompt-layering/plan.md`
**技术模块**: backend (smart_assistant)
**状态**: 已确认
**创建日期**: 2026-05-25

## 前置依赖

### 上游 Story
- Story 01（同 plan）：已完成 → 提供 token baseline 数据（作为 build 方法的输入参考）

### 跨 Plan 依赖
- 无

### 引用的架构决策
- ADR-008（SmartAssistant 代码分层）— 改动在 ToolRegistry 现有类内，不新建模块

## 验收标准

- [ ] `ToolSpec.summary` 字段存在，默认值 `""`
- [ ] 若未手动填写，`__post_init__` 自动从 `description` 的 ① 段提取
- [ ] `ToolRegistry.build_tool_directory()` 返回按 namespace 分组的精简目录（`[namespace] name — summary`）
- [ ] `ToolRegistry.build_tool_help(tool, namespace)` 支持三种模式：单工具 / namespace 批量 / 全局概览
- [ ] `build_tool_help` 返回格式为结构化参数表格（参数名 / 类型 / 必填 / 说明），非 prose 段落
- [ ] 41 个工具注册代码零改动（summary 全部自动提取）

## 数据流

```
ToolSpec 注册时 (__post_init__)
    │
    ├─ description 有 ①...② 标记
    │   └─→ 自动提取 summary（~30-50 chars）
    │
    ├─ description 无标记
    │   └─→ summary 保持 ""（降级到 description 前 50 字符）
    │
    ▼
ToolRegistry (已有 _namespaced_tools dict)
    │
    ├─ build_tool_directory()
    │   │  遍历 list_all_namespaces()
    │   │  过滤 deprecated
    │   │  格式化: [namespace] name — summary
    │   └─→ 返回 ~500 tokens 目录文本
    │
    └─ build_tool_help(tool, namespace)
        │  三种模式分支:
        │  ├─ tool 非空 → 单工具查找 → 参数表格
        │  ├─ namespace 非空 → 整组工具 → 逐个参数表格
        │  └─ 两者皆空 → 全局概览（按 ns 分组列出工具名+摘要）
        │
        └─→ 返回完整 Schema 文本
            │
            ▼
        供 build_system_prompt() 和 get_tool_help 工具使用
```

## 关键接口

### ToolSpec 新增字段

```python
@dataclass
class ToolSpec:
    name: str
    display_name: str
    description: str
    summary: str = ""          # NEW: 一句话摘要（~30-50 chars）
    parameters: dict = ...
    # ... 其余字段不变
```

### ToolSpec.__post_init__ (自动提取)

```python
def __post_init__(self):
    if not self.summary and self.description:
        import re
        m = re.match(r'①(.+?)(?:②|$)', self.description)
        if m:
            self.summary = m.group(1).strip()
```

### ToolRegistry.build_tool_directory

```python
@classmethod
def build_tool_directory(cls) -> str:
    """构建精简工具目录。按 namespace 分组，每条 name + summary。"""
```

### ToolRegistry.build_tool_help

```python
@classmethod
def build_tool_help(cls, tool: str | None = None, namespace: str | None = None) -> str:
    """返回指定工具或 namespace 的完整 Schema。
    
    三种模式：
    - tool="start_translation" → 单工具完整参数表格
    - namespace="translator" → 该 ns 全部工具完整 Schema
    - 无参数 → 按 namespace 分组的工具概览
    
    支持逗号分隔多 namespace: namespace="parser,translator"
    """
```

## 实现步骤

### 步骤 1: ToolSpec 新增 summary 字段 + 自动提取

**涉及文件**: `src/transbridge/smart_assistant/tool_registry.py`（修改）

**实现要点**:
- 在 ToolSpec dataclass 中新增 `summary: str = ""` 字段
- 添加 `__post_init__` 方法，用正则 `①(.+?)(?:②|$)` 从 description 提取
- 提取失败时 summary 保持 `""`（降级方案：调用方使用 `spec.summary or spec.description[:50]`）

**边界条件**:
- description 为 `""` → 正则不匹配，summary 保持 `""`
- description 只有 ① 无 ② → 正则 `(?:②|$)` 匹配到字符串末尾
- ① 段内容超过 80 chars → 截断到 80 chars
- 已手动填写 summary → 跳过自动提取（`if not self.summary` 守卫）

**伪代码/设计思路**:
```python
@dataclass
class ToolSpec:
    # ... existing fields ...
    summary: str = ""  # NEW
    
    def __post_init__(self):
        if not self.summary and self.description:
            import re
            m = re.match(r'①(.+?)(?:②|$)', self.description)
            if m:
                self.summary = m.group(1).strip()[:80]
```

**测试策略**:
- 单测：description 含 ①...②...③ → 正确提取 ① 段内容
- 单测：description 不含 ① → summary 保持 `""`
- 单测：summary 已手动填写 → 不覆盖
- 单测：① 段超过 80 chars → 截断

### 步骤 2: build_tool_directory() 实现

**涉及文件**: `src/transbridge/smart_assistant/tool_registry.py`（修改）

**实现要点**:
- 类方法，遍历 `list_all_namespaces()` 获取所有 ns→tools 映射
- 每条格式：`[namespace] name — summary`
- 过滤 `deprecated=True` 的工具
- ns 按字母序排列，default 排最前
- 估算输出 ~500 tokens（41 条 × ~50 chars）

**边界条件**:
- 某工具 summary 为空 → 使用 `description[:50]` 截断
- 某个 namespace 无工具 → 跳过该 ns
- 全部工具 deprecated → 返回"无可用工具"提示

**伪代码/设计思路**:
```python
@classmethod
def build_tool_directory(cls) -> str:
    lines = ["## 可用工具目录"]
    all_ns = cls.list_all_namespaces()
    # default 排最前，其余按字母序
    ns_order = ["default"] + sorted(
        [ns for ns in all_ns if ns != "default"]
    )
    for ns in ns_order:
        tools = all_ns.get(ns, [])
        if not tools:
            continue
        for spec in sorted(tools, key=lambda s: s.name):
            if spec.deprecated:
                continue
            summary = spec.summary or spec.description[:50]
            lines.append(f"[{ns}] {spec.name} — {summary}")
    return "\n".join(lines)
```

**测试策略**:
- 单测：返回文本包含 `[translator] start_translation — ...`
- 单测：deprecated 工具不出现
- 单测：default ns 排最前
- 单测：空 summary 降级到 description[:50]

### 步骤 3: build_tool_help() 实现

**涉及文件**: `src/transbridge/smart_assistant/tool_registry.py`（修改）

**实现要点**:
- 三种模式分支：
  1. `tool` 非空 → `get(name=...)` 单工具查找 → 参数表格
  2. `namespace` 非空 → 支持逗号分隔 → `list_namespace(ns)` 逐个 → 参数表格
  3. 两者皆空 → 全局概览（按 ns 列出工具名 + 一句话摘要）

- 参数表格格式（标准化，非 prose）：
  ```
  ## start_translation
  > ①功能简述...
  
  | 参数 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | entry_ids | list[str] | 否 | 条目ID列表 |
  
  **返回**: ToolResult(...)
  **规则**: 1. xxx  2. yyy
  ```

- 不存在的 tool 名 → Levenshtein 距离 ≤3 模糊匹配建议

**边界条件**:
- tool 和 namespace 同时提供 → tool 优先（忽略 namespace）
- tool 不存在且无模糊匹配 → "未找到工具 'xxx'。使用 get_tool_help() 查看可用工具列表。"
- namespace 不存在 → "未找到命名空间 'xxx'。可用: default, translator, ..."
- 多 namespace（逗号分隔）→ 依次查找并合并结果
- 工具无 parameters → 参数表格显示"（无参数）"

**伪代码/设计思路**:
```python
@classmethod
def build_tool_help(cls, tool: str | None = None, 
                    namespace: str | None = None) -> str:
    if tool is not None:
        return cls._help_single_tool(tool)
    elif namespace is not None:
        return cls._help_namespaces(namespace)
    else:
        return cls._help_overview()

@classmethod
def _help_single_tool(cls, name: str) -> str:
    spec = cls.get(name)
    if spec is None:
        # Levenshtein 模糊匹配
        all_names = [s.name for s in cls.list_all()]
        matches = [n for n in all_names if levenshtein(name, n) <= 3]
        if matches:
            return f"未找到 '{name}'，您是否要找: {', '.join(matches)}？"
        return f"未找到工具 '{name}'。使用 get_tool_help() 查看可用工具列表。"
    return cls._format_tool_schema(spec)

@classmethod
def _help_namespaces(cls, ns_str: str) -> str:
    parts = []
    for ns in ns_str.split(","):
        ns = ns.strip()
        tools = cls.list_namespace(ns)
        if not tools:
            parts.append(f"## {ns}\n（命名空间不存在或为空）")
            continue
        parts.append(f"## {ns}")
        for spec in sorted(tools, key=lambda s: s.name):
            parts.append(cls._format_tool_schema(spec))
    return "\n\n".join(parts)

@staticmethod
def _format_tool_schema(spec: ToolSpec) -> str:
    lines = [f"### {spec.name}", f"> {spec.description}"]
    if spec.parameters:
        lines.append("| 参数 | 类型 | 必填 | 说明 |")
        lines.append("|------|------|------|------|")
        for pname, pinfo in spec.parameters.items():
            required = "是" if pinfo.get("required") else "否"
            ptype = pinfo.get("type", "str")
            desc = pinfo.get("description", "")
            lines.append(f"| {pname} | {ptype} | {required} | {desc} |")
    else:
        lines.append("（无参数）")
    return "\n".join(lines)
```

**测试策略**:
- 单测：`build_tool_help(tool="start_translation")` → 包含参数表格
- 单测：`build_tool_help(namespace="translator")` → 包含该 ns 所有工具
- 单测：`build_tool_help()` → 全局概览，按 ns 分组
- 单测：`build_tool_help(tool="non_existent")` → 模糊匹配建议
- 单测：`build_tool_help(namespace="parser,translator")` → 两个 ns 合并
- 单测：Levenshtein 距离 2 → 匹配，距离 4 → 不匹配

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/smart_assistant/tool_registry.py` | 修改 | +summary 字段 + __post_init__ + build_tool_directory + build_tool_help + _help_single_tool + _help_namespaces + _format_tool_schema（共 ~48 行） |

## 风险与注意事项

- **注意**: ToolSpec 新增字段不影响现有 41 个工具的注册代码（dataclass 默认值 `""` 确保兼容）
- **注意**: `__post_init__` 只在 dataclass 实例化时执行，修改已有实例的 summary 不会触发自动提取
- **注意**: Levenshtein 距离计算避免引入新依赖，手动实现 3 行即可（或使用 Python 标准库 `difflib`）
- **注意**: 参数表格中的 `parameters` dict 格式已由 `@validate_params` 标准化，直接遍历即可
