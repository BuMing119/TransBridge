# ADR-055：项目语言编码策略与来源读取事实

- 状态：提议；七项修订产品需求已确认，本地实现与相关 QA 完成；真实验证、既有失败与限制见[本轮记录](../changelogs/project-text-encoding/story-07-to-12/2026-11-14-001-language-reading-legacy-compatibility.md)。
- 日期：2026-11-14。
- 需求：[FR33](../requirements/project-text-encoding.md)；计划：[project-text-encoding](../../plans/project-text-encoding/plan.md)。
- 关联：[ADR-017](017-translation-io-kernel-v2.md)、[ADR-044](044-source-file-update.md)、[ADR-045](045-original-qualified-plugin-entries.md)、[ADR-054](054-project-storage-and-independent-tasks.md)。
- 修订说明：在同一决策域修订本文。此前严格单 codec 输入和独立编码重读的事实保留于 [S01～S06 历史记录](../changelogs/project-text-encoding/story-01-to-06/2026-11-14-001-project-text-encoding.md)；当前合同以本文为准，不改写历史日志。

## 背景与约束

项目保存已解码原文、格式定位和可失效的来源路径，不保存插件本体。项目设置说明当前用户意图，来源读取选项记录原文形成时的事实，两者不能混为覆盖配置。真实旧项目已有 UTF-8/UTF-8 设置但缺来源编码事实，按历史 Windows-1250 解释与已存条目相符；Windows-1252 会使一条 `Annaďg` 变为 `Annaïg`。配置补缺不能证明历史解码规则，导出不能隐式迁移原文。

xTranslator 在 UTF-8 模式下逐字段尝试语言备用码页，并对普通字段与 Strings 全量输出。TransBridge 采用该读取和全量输出语义，保留严格无损输出、定位身份、阶段策略、事务和原子发布；不照搬替换或截断。

修订开始时已有 `TextEncodings`、配置持久化、局部 codec、严格发布验证及独立 `ProjectTextEncodingService` 重读链。本轮扩展语言与读取策略，将原文迁移收敛到既有来源更新。本文是目标合同，实施完成状态以计划及实际 QA 为准。

## 决策与关键契约

### 1. 程序默认与项目意图只有两级

无 Qt 的 `config/text_encoding.py` 扩展 `TextEncodings`，保留 `input_encoding`、`output_encoding`，新增 `source_language`、`target_language`。项目键为 `text_input_encoding`、`text_output_encoding`、`text_source_language`、`text_target_language`；程序默认通过既有 `TextEncodingDefaults`/ConfigRepository 保存对应默认值。固定缺省是 `english`、`chinese`、`utf-8`、`utf-8`，不读取 AI profile。源/目标语言分别关联读取/输出默认选择，用户手选 codec 是明确输入。

新建时冻结全局默认；旧项目缺值使用固定缺省。`project_store/records.py`、`archive_validation.py` 等白名单同步四项字段。打开不写入，下次保存即使无其他修改也补缺；保留已有值、原文和译文状态，不读来源。成功后同步内存，检查数据库实际字段写入。非法值明确拒绝，项目格式主版本不变，不恢复旧 JSON 支持。

集中规范化 codec，支持 UTF-8、Windows-1250～1258、874、932、936、949、950，不开放任意 codec。语言设置只绑定编码，Strings 伴随文件路径命名继续采用既有显式 `language`/快照 `localized_language`；补写 `target_language=chinese` 不得擅自重命名原 English 伴随文件。语言主 codec 与备用不同：上游 Skyrim SE 语言表主 codec 均为 UTF-8，English 的 1252 等是 UTF-8 失败时的备用。

### 2. 当前读取策略与历史事实分离

`ParseRequest.options` 和来源持久化 `read_options` 传递 `input_encoding`、`source_language`、`text_read_policy`。来源登记白名单保留实际读取事实。`WriteRequest.options` 同时传递读取事实和 `output_encoding`；冻结运行选择，不允许 UI/助手临时覆盖文件编码。

读取策略合同：

- `xtranslator-v1`：新来源与明确更新用当前项目规则。UTF-8 对每个普通字段先严格解码，失败只尝试源语言备用 codec；显式非 UTF-8 直接使用该 codec。不可解释时保留异常并提供定位。
- `strict-v1`：保留前次明确严格来源的读取行为；暂存输出校验也须显式使用严格策略与输出 codec，不能以 fallback 验证错误输出为成功。
- `legacy-v1`：无策略记录的旧 vendor 插件按历史逐字段 UTF-8、cp1250、cp1252、cp1251、replacement 顺序解释；旧独立/关联 Strings 保持各自历史 BOM、尾 NUL 和 fallback decoder 边界，不套用插件列表。仅用于还原既有来源并与已存 baseline 验证一致。项目补写 UTF-8 不能据此推断 `strict-v1` 或新规则。

来源明确事实优先；缺事实按兼容规则判定，不能把当前设置填空后伪装为历史事实。新增/导入/明确更新用项目意图；历史导出、补充实际输入和保真源验证用来源事实。baseline 不匹配则阻止并引导“更新来源”，不能重建已存原文。项目设置保存不顺带登记虚构读取事实。

### 3. 格式边界全量转码，输出严格验证

`parser/plugin/binary_io.py`、`text_subrecords.py` 仅适配当前解析实例，复用第三方结构、压缩、VMAD 和 BSA，不做进程级 monkeypatch。`parser/localized_string_records.py` 共享 Strings 结构；读取策略置于无 Qt、独立可测的 codec 模块。原生 SST/EET/XML 等 adapter 保持自身格式规则。

按读取事实取得全部普通文本，以 StagePolicy 叠加可用译文，再将全部受控文本编码为目标 codec。不能以 codec 名称相等或修改数为零跳过转码；UTF-8/UTF-8 也可能需要转换 fallback 字段。Strings 覆盖全部物理记录，保留 ID、顺序、未引用条目和结构。非文本及专用字段保留原规则。

`parser/game_text.py` 保持严格编码和回读 Unicode 相等，不 replace/ignore、不截断。NUL 终止字段拒绝内嵌 NUL，长度前缀 DLSTRINGS/ILSTRINGS 保留合法内嵌 NUL。普通 inline 字段超过 65535 字节明确失败，不自动扩展结构；入站 `XXXX` 不透明载荷仍保留字节。

源验证用历史读取事实，输出用 `strict-v1` 和目标 codec 回读，直接比较预期文本、结构与关联，不新增哈希。不用输入 fallback 校验输出。沿用整组暂存、备份、取消及原子发布，验证失败不覆盖目标。

### 4. 设置保存与来源更新分开

编码 service 保留设置读写职责，移除独立主动重读业务及 UI 入口。保存语言/codec 只改变之后新解析的规则，不读取插件、不重写 baseline 或 variant。提交走权威项目变更与生命周期边界，保留未保存译文，不能借设置保存提前提交用户译文。

已有原文采用新规则时复用 `application/projects/source_update.py` 的 prepare/commit/discard、预览、补充、备份及事务。既有指纹用于来源核验，不引入新 hash；字节未变仅在策略也未变时足以判定无变化。策略变化允许同文件进入比较，并在成功更新后登记新事实。

复用 `text_encoding_matching.py` 的可靠一对一定位，迁移 EntryKey 和全部 variants。唯一稳定 local_key 可直接匹配；重复项须依赖格式身份与持久定位，不能按临时列表顺序盲配，无法证明则阻止。保留译文、标签、显式清空、业务引用及锁定/隐藏保护；原文变化且有译文时待复核。

`ProjectSourceRevisionStore` 在既有事务内保存来源事实、baseline、所有版本和恢复点，不新增第二套更新事务。沿用 owner/generation、前像、预览后输入核验和取消，失败不提交部分版本；成功后生命周期发布，恢复时还原读取事实和身份，保护其他版本译文。设置保存与来源更新为两个动作，更新失败不虚构先前设置保存也已回滚。

Qt coordinator 仅协调设置、既有来源更新界面和后台任务，不承载 codec/匹配。GUI、CLI/MCP、助手、批量/多方案、FOMOD 共用应用层规则，原生存档不显示误导性编码控制。

## 备选方案

- 当前设置覆盖全部历史读取：破坏 baseline/导出身份，拒绝。
- 文件级覆盖或遍历码页猜测：增加配置层且难追踪，拒绝；只保留明确语言备用及历史兼容。
- 独立全来源编码重读：重复来源更新、预览及恢复边界，与已确认操作模型冲突，移除。
- 分离项目意图、来源事实和严格输出，复用来源更新：兼容可追踪，采用。

## 影响、迁移与回退

新 UTF-8 读取允许语言备用；无事实旧来源维持历史规则，明确严格来源不静默改写。全量转码允许必要文本字节变化，即使两端同名 UTF-8，仍保持语义与结构。设置保存不迁移原文；更新来源失败保留原文与全部版本，成功可从既有恢复点恢复。新增字段不保证旧程序接受新项目包。

真实输入/项目只读，验收在隔离副本进行，用户数据不纳入仓库。新增 codec/事实决议责任独立可测，大模块只接线，不永久缓存插件。未知语言、歧义身份、事实不足或无法无损编码均给可行动诊断。

## 上游与本地证据

xTranslator 固定提交 [`9aa38d60860273401f8bb0dd1557c82a295c41b3`](https://github.com/MGuffin/xTranslator/tree/9aa38d60860273401f8bb0dd1557c82a295c41b3)：`fstreamSave` 132–181 为解码 fallback，189–245 为语言码页选择（强制 UTF-8 仍保留备用）；`Data/SkyrimSE/codepage.txt` 的 English 为 `utf8,1252` 等；`MainLoader` 1448–1508 逐字段读取、1573–1589 全普通字段输出；`StringsFunc` 295 同规则读取、344–404 全列表输出。`fstreamSave` 366 附近 `limitStringSize` 会截断，本项目不采用。SST UTF-16LE 与 XML UTF-8 继续原生规则。

本地 ESP-ESM Translator 4.35 只读 IL：`GetTraductionBytes`（token `06002977`）仅匹配且 `STATUS99validated` 时调用编码，未匹配内嵌字段保留旧字节；`StringsFile.Traduire`（`06002549`）重编码全部原文或已验证译文。默认 UTF-8 与 `outputGetEncoding.GetBytes` 可能替换不可表示字符，不提供严格输出保证。因此全量插件转码主要参考 xTranslator，无损输出是本项目约束，不宣称两款工具行为完全一致。
