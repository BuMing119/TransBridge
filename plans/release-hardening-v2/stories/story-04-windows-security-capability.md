# Story 04：Windows 路径、安全与格式/依赖能力矩阵

- 所属 Plan：[Quality Foundation and Release Hardening V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：NFR3.1/NFR4.1/NFR5.1；R-002/R-004/R-046/R-047
- 依赖：S01/S02；platform/I-O/FOMOD contracts

## 验收记录

- 实现完成（2026-08-18）：新增 `tests/capability/matrix_generator.py`、`tests/capability/test_capability_matrix.py`、`tests/security/test_windows_paths.py`、`tests/security/test_archive_attack_corpus.py`、`tests/security/test_dependency_degraded.py`、`tests/security/test_secret_canary.py`。
- 验证：`uv run --locked python -m pytest tests/security tests/capability -q -p no:cacheprovider` → 71 passed, 3 skipped（junction/长路径/非法文件名字符需特权或系统支持，标注跳过并说明，不以跳过算通过）。
- evidence：run_id `qa-20260818T113135.348795Z-4b1648a19b08`，verify → `VALID ... (passed)`；ruff 0 错误。
- 结论：每格式每入口矩阵闭合（11×7=77 cell）；SST Writer write/roundtrip/publish 固定 unsupported；UI/Agent/MCP 返回同一 capability id/reason；disabled 检索零加载；secret canary 全产物脱敏后不存在。

## 目标与验收

Windows 10/11、Unicode/长路径、junction/symlink/路径逃逸、归档预算和可选依赖有可重放证据；unsupported/experimental 不被入口宣称支持；disabled 检索零加载；secret canary 全产物无泄露。

## 数据流与接口

capability registry + dependency/build probes + format contract results → generated CapabilityMatrix（read/write/roundtrip/GUI/Agent/MCP/publish）→ entrypoint assertions/release report。安全 corpus 先经过共同 PathGrant/ArchivePolicy，再检查目标目录零写入。

## 实施步骤

1. 建 Windows path matrix：驱动器、UNC（若支持）、Unicode、长路径、case、junction/symlink、nonexistent target parent。
2. 对 ZIP/7z/RAR 执行同一恶意 corpus/预算；记录库 capability。
3. 在缺 rank-bm25/FAISS/py7zr/rarfile 的隔离环境探测 degraded；disabled 用 import/load spy。
4. 从测试结果生成格式/入口矩阵，UI/Agent/MCP 返回同一 capability id/reason。
5. 扫描 stdout/stderr/log/JUnit/report/artifact manifest 的 secret canary。

## 边界、迁移与测试

无法创建真实 junction/长路径的环境标 blocked，不以跳过算通过；需要管理员权限的 case 单独标注。矩阵生成必须拒绝缺失结果，SST Writer 固定 unsupported。失败归属对应业务 Story，质量 Story保留证据。
