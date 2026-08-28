# 2026-08-28 FR5.16 发布候选验证记录

## 结论

本机完成了 FR5.16 regular 与 stress 各 10 个正式场景、每场景 5 轮，以及独立 Qt/TaskRuntime supplemental 测量。环境满足 Windows 11、至少 4 个物理核心、16 GiB 内存和 SSD/NVMe 的最低参考条件；20 份场景 manifest、supplemental JSON 与聚合 bundle 均有 SHA-256 artifact digest。

本发布候选 **不满足 FR5.16 SHALL**：regular/stress 峰值额外 RSS 超限，regular 的查询、历史、比较和 changelog 原始场景超时；增量相对全量耗时没有独立观测。正式场景完成后又修复了 SQLite 首屏/历史完整 payload 解码、相邻版本 canonical diff 快路、路径型来源快照和生产读取前 preflight，因此现有 bundle 不能证明最终工作树已达标。全部 terminology feature stage 保持 OFF，ADR-034 保持“提议”，Plan/Story 12 保持未完成。

## 证据集合

- regular：`results/2026-08-28-local-nvme-regular/`，包含 `full-cold`、`full-warm`、`repeat`、`changed-10pct`、`query`、`history`、`compare`、`report`、`changelog`、`cancel`。
- stress：`results/2026-08-28-local-nvme-stress/`，场景集合相同。
- UI supplemental：`results/2026-08-28-local-nvme-supplemental-ui.json`，digest `e7fc6289278cde5fcc213f2a07ffcfca777ffab0e43315f6b5bc8488f0fe76dd`。
- 聚合 bundle：`results/2026-08-28-local-nvme-release-bundle.json`，digest `f0e0b08aeaded79146b2db8020a0270d13ef4af478debc7d5152d0db96739d75`。
- 旧 `results/2026-08-28-local-smoke/full-cold.json` 与 `results/2026-08-28-production-formal-smoke-v2/` 继续作为诊断历史保留，没有覆盖或伪装为正式 release evidence。

环境记录：Windows 11 build 26200；Intel64 Family 6 Model 183 Stepping 1；24 个物理核/32 个逻辑核；33,954,381,824 bytes 内存；D: 为健康 NVMe SSD（Fanxiang S690MQ 1TB，NTFS，运行时约 441 GB 可用）；Python 3.12.12；TransBridge 0.1.1.1；psutil 7.2.2。`reference_device_id=win11-local-nvme-20260828`。未执行管理员级 Windows 文件缓存清除，manifest 明确记录 OS file cache 边界；未校准防病毒实时扫描状态。

## 预算结果

通过：

- FR5.16.33 regular/stress 固定数据规模与参考环境字段。
- FR5.16.34：可见反馈最大 `0.0000648 s`；ProgressHeartbeat 最大 `1.500176 s`；Qt 主线程最大间隙 `0.0129435 s`。
- FR5.16.35：regular 本地构建最大 `36.1788 s`；stress 最大 `122.5696 s`。
- FR5.16.36：五轮所有场景回收后最大稳定增长 `40,583,168 bytes`。
- FR5.16.37：取消可见态最大 `0.0000742 s`；生产取消终态最大 `0.0000419 s`。
- FR5.16.38：完全重复最大 `8.50034 s`；regular 实际变化 `9.7296%`；增量与同输入全量 canonical digest 一致。
- FR5.16.39：5 万术语/5 千冲突质量 Excel 最大 `47.4108 s`；质量报告与 changelog semantic manifest 均未截断，stress 20 万术语/2 万冲突及 20 万项 Markdown/Excel 也完成五轮。
- FR5.16.40：本地处理、外部 LLM 与外部 I/O 分桶完整；LLM disabled 路径无请求、无重试且可确定跳过。

失败或未运行：

- FR5.16.36 regular 峰值额外 RSS `1,295,310,848 bytes`，高于 1 GiB；stress `5,143,638,016 bytes`，高于 2.5 GiB。
- FR5.16.38 `incremental-vs-full` 为 `not-run`；runner 已验证 digest parity，但未独立计量增量耗时占全量百分比。
- FR5.16.39 原始 regular manifest：查询首屏 `13.8054 s`、历史打开 `6.82123 s`、比较摘要 `7.48440 s`、changelog `95.4002 s`，分别超过 0.5/2/5/30 秒预算。
- 正式运行后已把 query/history 的标量 ref 与 keyset SQL 从完整 BuildResult/Version 解码中拆开；独立复测 query 五轮 `0.2018–0.2059 s`、history 五轮 `0.000496–0.000752 s`，均在预算内。相邻版本 compare 也增加了持久化 canonical diff 快路，非相邻版本仍完整重算。上述最终实现尚未按同一最终代码身份重跑并聚合 regular/stress，因此旧 bundle 不能被口头替换成通过。
- 来源租约已改为 1 MiB 分块哈希与 path-backed snapshot，并在任何来源字节常驻前完成总量预检；这消除了最多 256 MiB 原始文件副本，但旧 manifest 显示 NDJSON 到完整 rows/evidence/candidate/conflict 对象图仍有约 28 倍放大，regular 规模仍未证明满足内存预算。
- supplemental `additional_checks` 故意为空；迁移、发布事务、disk-full、effective、回退与 artifact retry 虽有自动化测试通过，但尚未生成绑定最终构建身份的独立 release-check evidence，聚合器不会自动把它们标记为 passed。

## Feature gate 状态

门禁顺序固定为 analysis/report → draft/publish → effective → history/revert/changelog → optional partial publish：

- analysis/report：**OFF**。`fr516-shall-budgets-passed=failed`，且最终工作树没有完整重测；迁移/存储 release checks 未绑定进 bundle。
- draft/publish：**OFF**。前序 stage 关闭；发布与 disk-full 自动化合同不能越级开启。
- effective：**OFF**。前序 stage 关闭；生产翻译接线测试通过不等于 release gate 可越级。
- history/revert/changelog：**OFF**。前序 stage 关闭，原始 changelog 正式预算失败。
- optional partial publish：**OFF**。固定产品策略为 `stage-policy-disabled:partial-publish-not-supported`，没有独立 policy/确认/QA 证据。

关闭或回退不会删除 Project v3、SQLite、已发布版本、报告或 changelog；这些资产保持只读可追溯。无有效版本、门禁关闭或仓储只读/损坏时翻译继续使用原有 legacy 行为，且 Project/Variant 隔离不变。

## 后续重新开门禁的必要条件

1. 在最终代码身份上重跑 regular/stress 20 场景与 UI supplemental，记录管理员级缓存边界和防病毒状态。
2. 将 regular/stress 峰值额外 RSS 降到 1/2.5 GiB 内；当前生产 preflight 在任何来源字节常驻前拒绝单源超过 64 MiB、总量超过 256 MiB 或超过 50 个非流式来源，200-source stress 不会进入生产构建。
3. 在正式 runner 中重测 query/history 与相邻 compare 快路，并将 changelog 端到端场景降到 30 秒内；当前旧场景还把完整 build 持久化、发布和投影计入 renderer 预算，Markdown/Excel 也分别读取完整 change payload。
4. 增加 `incremental-vs-full` 独立计时。
5. 从实际通过的迁移、发布、effective、回退和 artifact retry 验证生成 digest-bound additional release checks；缺失检查继续是 `not-run`。
