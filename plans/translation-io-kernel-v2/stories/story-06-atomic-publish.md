# Story 06：Staging、验证、备份与原子发布

- 所属 Plan：[Translation I/O Kernel V2](../plan.md)
- 状态：已实现并增量验证通过（2026-08-18）
- 追溯：FR18.7、NFR2.1/NFR3.1；ADR-017；R-018
- 依赖：S01/S04/S05；platform filesystem/security ports

## 目标与验收

生成失败、验证失败、取消和冲突均保留旧正式文件；成功产物可重解析且有 manifest；Windows 非 ASCII/长路径可用，临时资源按策略清理。

## 事件顺序与接口

WriteRequest → `PublishCoordinator.prepare`（同卷 staging）→ adapter render → validators（结构/重解析/fidelity）→ manifest/hash → conflict/backup policy → cancellation/run guard → atomic replace → cleanup。计划类型：`PublishTarget`、`ConflictPolicy`、`BackupPolicy`、`StagedArtifact`、`ValidationReport`、`PublishManifest/Result`；FilesystemPort 提供 exclusive create/fsync/replace/remove。

## 实施步骤

1. 在 application publish 包实现 coordinator，target parent 下创建不可预测 staging 名并限制权限。
2. adapter 只写 staging；按 format 注册 validator，ESP/EET/XT/Strings 至少重解析并比对关键摘要。
3. replace 前重新校验 target fingerprint、run_id 与 cancellation；冲突按 fail/backup/explicit-overwrite。
4. Windows 同卷 replace 失败时返回明确 capability/error，不降级为先删目标。
5. finally 清理 staging；保留失败产物仅限显式 debug policy 且记录路径。

## 边界、迁移与测试

跨卷、网络盘、无 fsync/atomic rename 能力时正式发布不可宣称 atomic。旧 Writer.write 由 facade 调 coordinator；备份恢复单独验证。测试 fault injection 覆盖 render/validate/fsync/replace/cleanup/权限/磁盘满、目标并发变化和 cancel race；真实 parse-write-reparse 成功链检查旧文件保留与 manifest hash。
