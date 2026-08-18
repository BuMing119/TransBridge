# Story 01：安装态包、版本、入口与依赖基线

- 所属 Plan：[Platform Contract Foundation V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR17.2/17.5、NFR3.1/NFR6.1；ADR-016；R-001/R-002
- 前置依赖：无；下游阻塞 S03/S05 和 release-hardening-v2

## 目标与原始验收

消除 `src.transbridge`、版本双源、无效 CLI 及声明/锁定/构建能力漂移。源码树和安装态都能导入；`--help` 不启动 GUI；版本唯一；缺可选依赖时报告 capability 而非崩溃。

## 当前事实与数据流

当前 `pyproject.toml` 声明 `transbridge=transbridge:main`，实际 GUI 入口位于 `ui.app.main()`；`src/transbridge/__init__.py` 与项目元数据版本不一致，代码广泛导入 `src.transbridge...`。目标启动流为：console metadata → 纯参数解析 → 选择 CLI/GUI/MCP adapter → Composition Root；版本流为单一 metadata source → 包 `__version__`/UI/CLI/build manifest。

## 接口与实施步骤

1. 选定 `pyproject.toml`/构建生成模块为版本权威源，`transbridge.__version__` 只读取该源。
2. 新增计划符号 `transbridge.cli:main` 与 `transbridge.entrypoints.mcp:main`；保留 `ui.app.main` 为 GUI adapter。
3. 按包逐步把生产 import 改为 `transbridge...`，必要时用重导出 facade 保持旧测试可运行，禁止运行时修改 `sys.path`。
4. 对 direct/optional/build-only 依赖生成 capability 表；确保 `uv.lock`、PyInstaller hidden imports 和许可证清单一致。
5. 明确 Python 3.12 环境重建命令，失效 `.venv` 不作为成功证据。

## 文件与边界

- 修改：`pyproject.toml`、`uv.lock`、`src/transbridge/__init__.py`、`main.py`、构建/installer 配置。
- 新增：`src/transbridge/cli.py`、`src/transbridge/entrypoints/`、`tests/packaging/`。
- GUI 不得在 import/`--help` 时构造 QApplication；可选依赖不得在 capability 查询前顶层导入。

## 迁移、回退与测试

按包迁移 import，每批保留兼容重导出；回退只切换 console target，不恢复版本双源。建议运行 `uv sync --frozen`、clean venv `pip install .`、`python -c "import transbridge"`、`transbridge --help`、依赖删除矩阵和 onedir import smoke。验收证据记录 Python/lock hash/构建产物摘要。
