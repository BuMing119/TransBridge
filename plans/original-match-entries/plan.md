# 原文限定的冲突词条

状态：S01～S05 本地完成；全入口审计确认的遗漏已修复并完成相关回归。日期：2026-10-06。

对应 [ADR-045](../../docs/adr/045-original-qualified-plugin-entries.md)。

## 目标与非目标

key/id 不变；requires_original_match 默认 false，重复 key 组设为 true。不同原文可独立显示、翻译、保存和回写；同 key 同原文无法区分的项跳过 warning。不实现跨版本位置身份，不尝试猜测已翻译 ESP 中歧义条目的对应关系，不修改用户 ESP。

## S01：身份与保存

- 扩展 identity.py、TranslationEntry、EntrySnapshot 的原文限定身份；普通序列化保持不变。
- 校验持久化 schema、集合 mutation、UI 投影与恢复使用完整身份。
- 验收：相同 key/id 不同原文可共存，独立修改及保存恢复；旧 JSON 默认 false，标记项不能通过修改 original 改变身份。

## S02：解析与回写

- plugin_with_context.py 提供不合并重复的提取路径；新 converter/plugin_entry_conflicts.py 专责冲突分组；PluginParser 与 adapter 共享结果。
- PluginWriter 与 plugin_write.py 校验精确原文并使用预先确定的子记录引用。
- 验收：四条同阶段日志全部保留；[A,A,B] 只保留 B 且仍标记 true；空白差异不合并；交叉译文不串写；不合法身份不能写出文件。

## S03：导入与相邻入口

- migration_import.py、旧匹配路径针对标记项要求精确原文；不匹配时 warning，不阻断正常词条。
- 检查源更新、ParaTranz 与 UI 的裸 key 索引，不允许静默覆盖。
- 验收：能证明原文的迁移正确，已翻译 ESP 不能证明时跳过，普通导入和原有保护规则不回退。

## S04：QA 与记录

- 聚焦身份、解析、回写、工程保存与 UI 集成测试，再扩展相关套件。
- 只读复核用户 Neiva 与 Remiel 文件；不向用户文件写入。
- Ruff check/format 与差异审查，记录真实测试结果和兼容限制，清理自建测试目录。

## 风险与实现约束

先完整枚举再去歧义，否则底层字典会吞掉完全重复记录。保留正常路径性能，索引一次建立，不引入逐条全表扫描。已有大模块仅做职责内局部修复，新增冲突规则放独立模块，避免扩大现有模块责任。普通身份兼容；新身份无法无损降级给旧程序。

## S05：全入口适配收尾（已完成）

- 词典入库保留精确原文身份；新旧套用入口禁止给标记项套用归一化匹配或原文变化的译文。迁移规划按 key 与精确原文匹配。
- DSD 导出重读保留冲突项；旧 EET/XT writer 严格定位；复用插件实例二次写回仍正确。
- 助手查询、编辑、选中范围、后处理和润色回收统一使用完整身份；ParaTranz 直接上传与旧下载遵守跳过警告规则。
- 加载时的初始译文状态用完整身份传递；旧工程包识别完整身份并验证来源，不放宽裸 ID 歧义保护。
- 验收：为审计的九类遗漏分别加入能复现旧行为的测试；相关模块回归、静态检查及增量记录完成，保留普通词条兼容。
- 额外修正：集合逐条 add 不再按裸 key 重绑定标记项；DSD QUST 迁回插件按格式实际提供的插件限定 FormID、类型与精确原文唯一定位；GUI 初始状态在单来源边界完成 legacy namespace 的安全重绑定。
- 最终证据：跨模块 1140 passed；最后补充路径 92 passed；Neiva 4 条标记项 DSD 重读及迁回均保留 4 条，源 ESP 哈希不变。Ruff check/diff check 通过，format check 仅剩任务前已有的一个混合换行文件。
- 本轮逐文件记录及测试边界见 [S05 增量](../../docs/changelogs/original-match-entries/story-05/2026-10-06-001-complete-entry-paths.md)。

## 完成证据

- S01：完整身份贯通集合、标签、编辑投影、工程保存/重开、旧版缓存及源更新；相同原文保持译文，原文变化按新项处理。
- S02：解析与回写支持不同原文的同 key 词条；同 key 同原文全部跳过 warning；合成插件覆盖交叉译文、localized 和 FOMOD 写回。
- S03：迁移和旧导入入口要求精确原文；AI 翻译、校对、预览和任务结果使用内部完整身份别名，源 key/id 不变；ParaTranz 同步跳过歧义项及其远端同 key 项，防止覆盖或删除。
- S04：最终核心回归 91 passed；最终 AI/报告/校对恢复 25 项通过（属于 69 项复跑，其中另有 43 项助手测试通过、1 项既有竞态失败）。只读解析 Neiva 得到 2263 项，其中 4 项带标记；Remiel 得到 8311 项，其中 20 项带标记，二者未跳过词条且身份均唯一。
- 扩展回归一轮 716 passed、1 failed（旧 to_dict 断言未包含新增字段，已修正且最终核心回归通过）；另一轮 2001 passed、1 skipped、5 failed、1 deselected，失败证据见增量记录，不记为全绿。
- Ruff check 通过；format --check 剩余一个任务前已有的混合换行文件，其他 1534 个文件通过。未调用真实 LLM，未构建/发布，未修改用户 ESP。

逐文件变更、命令、失败归因与迁移说明见 [增量记录](../../docs/changelogs/original-match-entries/story-01-to-04/2026-10-06-001-original-qualified-entries.md)。
