# S02：来源提取与新建项目

- 状态：本地实现与相关回归完成；运行证据和限制见上级计划。
- 所属：[project-storage-redesign](../plan.md#s02来源提取与新建项目)。
- 架构：[ADR-054](../../../docs/adr/054-project-storage-and-independent-tasks.md)。
- 依赖：S01 的格式标识、事务和字段合同；下游 S03/S05/S07/S08。

## 目标与原始验收标准

插件仅在导入操作中读取和解析，正式项目/任务/日志摘要不含插件本体、完整解析树、历史来源路径；必要原文、上下文、顺序及完整身份保存后可独立读取；多来源建项全成或全败；EET/SST 等原生翻译记录不因抽取丢失。

## 现有事实与切入位置

`persistence/project_provisioning.py::TranslationIoProjectSourcePreparer` 生成 PreparedProjectSource；`saved_project_sources.py::SavedProjectSourcePreparer` 将它交给 ProjectSourceArtifactStore。PreparedSourceHydration 含 SourceSnapshot 和全部 entries；artifact codec 递归处理 metadata 中的对象和 bytes。

`application/projects/profile_entry_inputs.py`、`bootstrap/terminology_sources.py`、`bootstrap/history_search.py` 以及对话、AI 和写回消费者使用来源 metadata。不能只看工作台可见列决定最小字段。现有 EetAdapter/SstAdapter 依赖来源快照构造原格式文档，须将翻译原生字段的保存与插件写回模板的禁止分开。

## 实现接口与载荷

`application/projects/source_extraction.py` 负责解析结果到持久翻译内容的转换；`source_fields.py` 定义版本化白名单。它们不读写数据库、不调用 Qt、不负责插件原格式输出。

实现的 ExtractedSource 包含 source identity、FormatId、名称、业务解析选项、条目序列和格式专用翻译字段；它不包含 location、SourceDescriptor.uri、SourceSnapshot.content、lease、解析器对象或通用 prepared_source。名称只保留文件名或用户名称，不能保留完整路径作为隐式查找提示。

每个字段须有生产消费方、保留理由、类型、大小边界和 round-trip 测试：

- 身份：namespace、local_key、original 的存在性和值；来源关系及 plugin scope。不能以 EditorID 或 FormID 单独覆盖现有完整身份。
- 编辑与展示：真实原文、上下文、记录/字段类型、来源顺序、必要的说话者/NPC/任务关系、来源名称。
- 匹配：FormID/EditorID、重复项区分、Strings 表类型及 ID、格式自身物理记录序号等现有可靠证据。指纹仅保留既有身份所需的值，不新增全量打开哈希。
- 版本：译文、Stage、标签、外部引用、provenance 等按版本保存，不复制到来源 metadata；初始值由提取结果明确提供。
- 翻译文件保真：EET/SST 的原生翻译记录、重复物理记录、必要 header/cache/编码字段，以及 XML 的必要原生信息按格式独立建模。未知原生字段不能默默丢弃；先完善格式模型和测试，不用通用对象 blob 填洞。

metadata、外部引用、错误诊断和 provenance 也必须经过持久字段边界。不能只删除顶层 path、content 就放行任意嵌套对象。禁止字段返回可定位错误，不以静默过滤导致业务内容缺失。

## 数据流与实施步骤

1. 建立字段消费清单，逐项追踪历史搜索、术语/命名方案、对话、AI 输入、同步与所有 writer。每项标明临时操作输入、项目事实或可重建视图，避免同一字段保存三份。
2. 定义白名单与各格式提取器，使用现有 parser 的已验证结果；原始路径只保留在当前操作的请求对象，不交给项目模型和任务日志序列化。
3. 创建草稿项目时先在后台解析全部所选来源并提取，校验身份唯一、顺序、真实文本和格式字段，再由 S01 事务发布来源与默认版本。取消/第二个来源失败时，不发布部分工程或活动指针。
4. 数据库先提交，目录索引再更新；索引失败报告“项目已创建、列表待刷新”，可从新项目根修复，不回滚已发布内容或重建第二个项目。活动 UI 安装失败同样按实际提交状态报告。
5. 释放解析对象、input lease 和本次 staging。异常中断残留标明所属 operation，后续回收仅处理本次可证明的临时内容；用户输入和输出不在回收范围。
6. 给 S05 提供相同的翻译数据编码边界；任务不得直接调用旧 RecoveryStorage.pack 保存 prepared_source/raw plugin payload。

## 故障与取消

- 空项目合法；有来源但不含可翻译词条时按现有解析结果说明，不补造条目。
- 同名不同来源按身份区分；重复 identity 不以文件路径是否相同代替判断。
- 非零 Strings ID 缺内容应失败或报告当前既有合法部分结果，不能过滤条目后宣称完整建项。
- 数据库写入/磁盘满在提交前回滚；提交成功后的 index/UI 错误不假报数据全未保存。
- 界面关闭、取消、worker 迟到结果不能继续发布旧草稿；项目写入成功后才更新活动状态。

## 文件与验证

新增位置为本 Story 的 source_extraction/source_fields、project_store/repository；旧 provisioning 接口只接线，不增加 artifact 兼容分支。S09 最终移除旧项目专用 artifact 运行入口，不能在此悄悄扫描旧项目。

实现 `tests/persistence/project_store/test_source_extraction.py`、`test_exchange_fidelity.py` 与 `tests/integration/bootstrap/test_project_storage_composition.py`。

至少测试插件字节标记和原路径标记不进入 DB、临时文件之外的附件、任务报告或快照；测试嵌套 metadata 旁路、localized 多表重号、多来源同 local_key、original None/空串、对话关系、EET/SST 重复定位和全部物理记录。消除原输入后再验证项目读和翻译导出，不以同进程缓存通过代替持久化独立性。

已按此字段边界实现；不能以无法还原格式为理由重新保存插件。格式限制与运行验证在上级计划及交付记录维护。
