# ADR-052：SST 完整快照读写与插件词典生成

- 日期：2026-11-08
- 状态：提议，已按用户 bm-pilot 授权实现并验证本地流程；能力保持实验性
- 关联：FR1.9、FR18.8；[实施计划](../../plans/sst-integration/plan.md)
- 替代范围：ADR-017 中 SST Writer 关闭策略；其余 I/O 发布契约保持

## 背景与证据

旧 SST 解析存在偏移、记录边界及身份误判，不能作为写回依据。固定上游
[xTranslator 9aa38d6](https://github.com/MGuffin/xTranslator/tree/9aa38d60860273401f8bb0dd1557c82a295c41b3)
的 SaveSSTFile、rEspPointerLite、StringHash、EdidName、sanitizeFormID 和字段定义为依据，
恢复 SSU8/SSU9 严格布局。19 个真实样本共 9,690 条记录可零修改逐字节往返。
详见[源码与实验记录](../dev/sst-experiment.md)。

## 决策

1. 单一生产 codec 拥有完整文件结构；parser/serializer facade、实验 CLI 和正式 adapter 共用。
   严格拒绝不完整文件，避免扫描跳过导致丢记录。两个版本均含原文和译文。
2. 来源快照保存完整文件；EntryKey 使用来源命名空间与 `sst-v2:{ordinal}`，metadata 保存版本、
   物理序号、记录签名和来源摘要。写出前检查完整绑定、原文及重复身份，拒绝旧身份猜测。
3. 原 SST 只应用明确译文编辑，保留全部元数据、原生状态位和未编辑记录。
   译文迁移采用 FormID、记录签名、原文的唯一匹配，无法证明唯一性则跳过。
4. 插件生成独立的 SSU9 builder，从保存的插件和本地化资源推导 masters、物理字段序号、
   文本列表和 StringID。哈希为 FNV-1a，处理每个 UTF-16 代码单元低字节；输入是实际记录
   EditorID 或经过上游 sanitizeFormID 的 `[XXXXXXXX]`，不是译文。相同输入与规则得到相同值。
   无法证明的字段、VMAD、删除记录、非 ASCII 主文件名明确拒绝，不拼造身份。
5. 输出格式与来源格式分离，仅允许同格式及 plugin.sse→sst.ssu9。GUI/助手复用预检、任务、
   staging、全文件回读比较、备份和原子发布；SST 输出仅一个文件，不生成插件资源包。
   校验阶段需重建已退休的插件解析器，取消保持 CANCELLED 并清理 staging。
6. 旧工程可继续打开编辑；缺少 codec v2 绑定时提示重新导入和迁移。没有自动重写身份或数据库升级。
   读取、写出、往返、GUI、Agent、发布维持实验性；正式 MCP 当前只有能力/上下文工具，读写不开放。

## 验收与限制

独立 wire fixtures、真实语料、损坏拒绝、Unicode、长文本、空译文、重复记录、保存重开、
源路径删除、全文件保真、生成哈希/masters/索引、UI/助手路由和取消均纳入测试。
用户手动反馈修改后的真实词典可用；新建词典全面应用及多版本交叉验证仍需人工完成。
不将内部往返测试视为发布级兼容，不自动运行 xTranslator，也不修改用户原样本。
