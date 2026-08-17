# Story 01: 归档解包与打包

**所属方案**: `plans/agent-infra-tools/plan.md`
**技术模块**: backend
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 上游 Story
- 无（本 Epic 第一个 Story）

### 引用的架构决策
- ADR-015: fileops/ 独立包，archive.py 提供统一 extract/pack 接口
- ADR-014 决策4: py7zr（7z）+ zipfile（zip）+ rarfile（rar，捆绑 unrar.exe），不依赖用户环境装 7-Zip

## 验收标准

- [ ] 新建 src/transbridge/fileops/__init__.py + archive.py，提供统一 extract(archive_path, dest_dir) / pack(src_dir, archive_path) 接口
- [ ] 按扩展名分派：.7z→py7zr、.zip→zipfile、.rar→rarfile(捆绑 unrar.exe)
- [ ] 支持分层提取（按文件列表选择性提取，跳过 GB 级资源）
- [ ] 支持进度回调（复用 ApiWorker 的 make_progress_callback 模式）
- [ ] 解包失败（归档损坏/unrar 缺失）返回明确错误，不崩溃
- [ ] 实现 _find_unrar() 多路径探测（sys._MEIPASS + 应用目录 + PATH）
- [ ] 注册 Agent 工具 extract_archive / pack_archive 到 archive namespace，permission=write

## 数据流

```
归档文件(.7z/.zip/.rar) → Archive.extract() 按扩展名分派 → 后端库解压 → dest_dir
src_dir → Archive.pack() 按扩展名分派 → 后端库压缩 → 归档文件
Agent 调用: extract_archive(args, ctx) → Archive.extract() → ToolResult(data={dest_dir, file_count})
```

## 关键接口

### 函数签名

```python
# fileops/archive.py
def extract(archive_path: str, dest_dir: str, *, files: list[str] | None = None, progress: callable | None = None) -> dict:
    """解包归档到 dest_dir。files 非 None 时仅提取列表内相对路径（分层提取）。返回 {dest_dir, extracted_count}。"""

def pack(src_dir: str, archive_path: str, *, fmt: str = "zip", progress: callable | None = None) -> str:
    """将 src_dir 打包为 archive_path（fmt: zip/7z）。返回 archive_path。"""

def _find_unrar() -> str:
    """探测 unrar.exe 路径：sys._MEIPASS → 应用目录 → PATH。找不到抛 RuntimeError。"""

# Agent 工具
def _tool_extract_archive(args: dict, ctx) -> ToolResult: ...
def _tool_pack_archive(args: dict, ctx) -> ToolResult: ...
```

## 实现步骤

### 步骤 1: 新建 fileops 包骨架 + archive.py 格式分派

**涉及文件**: `src/transbridge/fileops/__init__.py`（新建）、`src/transbridge/fileops/archive.py`（新建）

**实现要点**:
- 定义 extract() 按 archive_path 后缀分派：.7z→py7zr、.zip→zipfile、.rar→rarfile
- pack() 按 fmt 参数分派；默认 zip（zipfile C 实现快），7z 走 py7zr
- 不产出 .rar（rar 仅解压）

**边界条件**:
- 归档不存在 → raise FileNotFoundError 或返回 ToolResult.fail
- 归档损坏 → 捕获异常返回明确错误，不崩溃
- .rar 且 unrar 缺失 → _find_unrar() 抛 RuntimeError，上层转友好提示

### 步骤 2: 分层提取 + 进度回调

**实现要点**:
- extract() 的 files 参数非 None 时，仅提取列表内相对路径（py7zr/zipfile/rarfile 均支持按成员提取）
- progress 回调签名 (current, total)，节流发射（每 N 个文件或每 M 毫秒一次）

**边界条件**:
- files 列表含不存在的成员 → 跳过并记录警告
- 空归档 → 返回 extracted_count=0

### 步骤 3: _find_unrar() 多路径探测

**实现要点**:
- 探测顺序：getattr(sys, '_MEIPASS', None)（PyInstaller 临时目录）→ Path(__file__).parent → shutil.which('unrar')
- 找到后设置 rarfile.UNRAR_TOOL 全局变量

### 步骤 4: Agent 工具注册

**涉及文件**: `src/transbridge/smart_assistant/tools/tool_archive.py`（新建）

**实现要点**:
- 定义 _tool_extract_archive / _tool_pack_archive，返回 ToolResult
- _register_archive_tools() 调 ToolRegistry.register_tools("archive", [...])
- 在 tools/__init__.py 的 register_all() 中导入并调用
- permission=write（产生文件系统副作用）

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| src/transbridge/fileops/__init__.py | 新建 | 导出 extract/pack |
| src/transbridge/fileops/archive.py | 新建 | 归档解包/打包 |
| src/transbridge/smart_assistant/tools/tool_archive.py | 新建 | Agent 工具注册 |
| src/transbridge/smart_assistant/tools/__init__.py | 修改 | register_all 导入 tool_archive |
| tests/fileops/test_archive.py | 新建 | 单元测试 |

## 风险与注意事项

- 风险: py7zr 大归档解压慢/内存高 → 缓解: 分层提取 + 进度回调 + 流式解压
- 风险: unrar.exe PyInstaller 打包路径 → 缓解: _find_unrar 多路径探测 + 打包冒烟测试
- 注意: py7zr 需 pycryptodome/texttable 传递依赖，需在 pyproject.toml 显式声明
- 注意: rarfile 仅解析目录，解压委托 unrar.exe；打包只出 zip/7z 不产 rar