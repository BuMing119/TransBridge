# Story 03: 资源过滤规则引擎

**所属方案**: `plans/agent-infra-tools/plan.md`
**技术模块**: backend
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 上游 Story
- Story 01（归档解包）：过滤作用于解包后的文件

### 引用的架构决策
- ADR-015: fileops/filter_rules.py 提供可配置扩展名保留/剔除规则
- ADR-014 决策6: 扩展名白名单过滤（剔除侵权资源，保留脚本 + fomod 元数据）

## 验收标准

- [ ] 新建 src/transbridge/fileops/filter_rules.py，提供可配置扩展名保留/剔除规则
- [ ] 规则集中在配置清单（keep/strip 列表），不同 mod 复用不同规则
- [ ] 支持目录级规则（同扩展名不同目录不同处理：fomod 图片保留 vs textures 贴图剔除）
- [ ] 注册 Agent 工具 filter_files 到 archive namespace，permission=read

## 关键接口

```python
@dataclass
class FilterRules:
    keep_exts: set[str]           # 全局保留扩展名
    strip_exts: set[str]          # 全局剔除扩展名
    dir_rules: dict[str, dict]    # 目录前缀 → {keep_exts, strip_exts} 覆盖规则

    @classmethod
    def from_json(cls, path: str) -> 'FilterRules': ...

def filter_files(files: list[str], rules: FilterRules) -> tuple[list[str], list[str]]:
    """返回 (kept, stripped) 两个相对路径列表。目录级规则优先于全局。"""

def _tool_filter_files(args: dict, ctx) -> ToolResult: ...
```

## 实现步骤

### 步骤 1: FilterRules 模型 + JSON 加载

**涉及文件**: `src/transbridge/fileops/filter_rules.py`（新建）

**实现要点**:
- keep_exts/strip_exts 为扩展名集合（含点，如 .dds）
- dir_rules 键为目录前缀，值含该目录下的 keep/strip 覆盖
- 默认规则集内置（BSA/贴图/模型/声音剔除，脚本/fomod 元数据保留）

### 步骤 2: filter_files 匹配逻辑

**实现要点**:
- 文件扩展名匹配：先查 dir_rules 是否有匹配目录前缀，有则用目录级，否则用全局
- 返回 kept/stripped 两个列表

**边界条件**:
- 扩展名不在任何规则 → 归 kept（保守保留）
- 空 files → 返回 ([], [])

### 步骤 3: Agent 工具注册

**涉及文件**: `src/transbridge/smart_assistant/tools/tool_archive.py`（修改）

**实现要点**: filter_files 加进 archive namespace，permission=read

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| src/transbridge/fileops/filter_rules.py | 新建 | 过滤规则 |
| src/transbridge/smart_assistant/tools/tool_archive.py | 修改 | 追加 filter_files |
| tests/fileops/test_filter_rules.py | 新建 | 单测 |

## 风险与注意事项

- 注意: 同扩展名不同目录处理（fomod 图片 vs textures 贴图）需 dir_rules 覆盖，仅扩展名白名单不够
- 注意: 规则配置格式建议用 JSON（.tbdict 也是 JSON，保持技术栈一致）或复用 TOML 惯例