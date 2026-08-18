# Story 01：ArchivePolicy、安全预算与 MOD 根归一化

- 所属 Plan：[FOMOD Pipeline V2](../plan.md)
- 状态：已实现并增量验证通过（2026-08-18）
- 追溯：FR23.4/23.5、NFR4.1；ADR-015；R-045/R-046
- 依赖：platform filesystem/security ports

## 目标与验收

ZIP/7z/RAR 执行同一成员预算；路径逃逸、链接/特殊文件、炸弹在写入前失败；多个 MOD 根候选要求确认；取消清理 staging。

## 数据流与接口

archive → `ArchiveInspector.list_members`（不提取）→ `ArchivePolicy.evaluate`（规范化路径、count/size/ratio/depth/type）→ approved manifest → staging extractor with cancel/progress → root detector → zero/one/many RootCandidates。计划类型：`ArchiveMember`, `ArchiveBudget`, `ArchiveManifest`, `RootCandidate`, `ExtractionResult`。

## 实施步骤

1. 将 `fileops.archive` 的 zip/7z/rar 成员枚举统一为 adapter，不允许直接 extractall。
2. 使用 resolved destination containment 检查绝对路径、`..`、驱动器、UNC、junction/symlink/special file。
3. 在任何写入前累计预算；实际展开量继续在线检查，超限取消并清理。
4. root detector 根据 plugin/Data/fomod 结构评分，唯一候选才自动选择，多候选返回确认 plan。
5. progress/cancel 复用 TaskRuntime token，结果含拒绝 member/预算诊断。

## 测试、边界与回退

最小自有 ZIP/7z/RAR corpus 覆盖正常、zip-slip、绝对/Unicode、链接、嵌套深度、高压缩比、多根/无根和取消；断言拒绝时目标目录零写入。无法安全枚举的库/格式 capability 为 unavailable，不降级旧 extractall。
