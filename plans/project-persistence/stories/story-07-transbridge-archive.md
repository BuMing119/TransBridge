# Story 07: .transbridge 单体项目文件

**所属方案**: `plans/project-persistence/plan.md`
**技术模块**: `src/transbridge/ui/` (修改), `src/transbridge/main.py` (修改)
**状态**: 已确认
**创建日期**: 2026-05-08

## 前置依赖

### 上游 Story
- Story-05（同 plan）：启动恢复/保存流程已就位

### 引用的架构决策
- ADR-006: .transbridge 为 ZIP 压缩包，含所有版本数据

## 验收标准

- [ ] 「文件 → 导出 .transbridge」将整个项目目录打包为 ZIP
- [ ] ZIP 内容：project.json + 所有版本的 current.json + 所有快照
- [ ] 「文件 → 导入 .transbridge」解压到 data/projects/ 下
- [ ] 导入时检测项目名冲突，提示覆盖或重命名
- [ ] 支持双击 .transbridge 文件打开（通过命令行参数）

## 关键接口

```python
def export_transbridge(project_handle: ProjectHandle, output_path: Path) -> None:
    """
    打包项目目录为 .transbridge (ZIP):
      project.json
      {variant}/current.json
      {variant}/snapshots/*.json
    
    ZIP 内根目录为项目名/
    """

def import_transbridge(file_path: Path) -> dict:
    """
    解压 .transbridge 到 data/projects/
    返回 {"project_name": ..., "project_dir": ...}
    项目名冲突时抛 FileExistsError
    """
```

## 实现步骤

### 步骤 1: 导出实现

**涉及文件**: `src/transbridge/ui/main_window.py`（修改）

**实现要点**:
- 「文件 → 导出 .transbridge」→ QFileDialog 选择保存路径
- `export_transbridge()`: 使用 zipfile.ZipFile + glob 遍历项目目录
- 仅打包 JSON 文件（project.json + current.json + snapshots/*.json），不打包源文件

**边界条件**:
- 项目目录为空 → 导出空 ZIP 并提示
- 输出路径已存在 → 确认覆盖

### 步骤 2: 导入实现

**涉及文件**: `src/transbridge/ui/main_window.py`（修改）

**实现要点**:
- 「文件 → 导入 .transbridge」→ QFileDialog 选择文件
- 解压到 `data/projects/{project_name}/`
- 检测冲突：目录已存在 → 提示用户选择覆盖/重命名/取消
- 导入后自动加载项目

### 步骤 3: 命令行参数支持

**涉及文件**: `src/transbridge/main.py`（修改）

**实现要点**:
- `main.py` 检查 `sys.argv` 中的 `.transbridge` 文件路径
- 若存在 → 自动调用 import → 加载项目 → 启动 GUI
- 支持 Windows 文件关联（.transbridge → transbridge.exe "%1"）

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/main_window.py` | 修改 | 导出/导入菜单 + 逻辑 |
| `src/transbridge/main.py` | 修改 | 命令行参数支持 |

## 风险与注意事项

- **ZIP 编码**: Windows 上 zipfile 默认使用 CP437，中文文件名需显式处理。使用 `ZipFile(mode='w', metadata_encoding='utf-8')`（Python 3.11+ 支持）或手动 UTF-8 flag
- **大文件**: 含全部快照的 ZIP 可能几十 MB。导出时显示进度，不在主线程执行
