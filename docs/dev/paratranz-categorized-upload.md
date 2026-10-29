# ParaTranz 文件上传与分类布局

更新：2026-10-28。当前合同为 [ADR-047](../adr/047-opt-in-categorized-project-upload.md) 与 [ADR-049](../adr/049-task-result-lifecycle.md)；历史词条核验、批次关联保存与旧性能测量见 [S11 历史记录](../changelogs/paratranz-categorized-upload/story-11/2026-10-25-001-direct-upload-and-fixed-paths.md)，不再是常规 GUI 上传流程。

## 用户操作

单独和批量上传中选择普通或分类模式，默认普通。普通生成根目录 `插件名.json`；分类生成 `插件名/分类.json`。Quest 对话、未关联对话及合并任务日志位于 `插件/任务/`。插件名保留扩展名，同名来源使用稳定来源摘要消歧，单独与批量命名一致。

任务对话按 Quest ID 分组，文件名优先采用同一完整来源中准确 FormID 对应的 `QUST:FULL.original`：`任务/对话_[任务原名].json`。不用任务译名、日志或目标文本。名称缺失、空白或同一 Quest 有不同原名时回退 `对话_[QuestID].json`；无关联使用未关联任务。文件名清理/截断/大小写重名时追加 Quest ID，命名在文件选择前确定。分类与脚本共用唯一核心，不按每个文件重建名称索引。

选择文件后直接在后台上传。内容模式为仅更新原文、仅安全导入译文、仅强制导入译文、原文加安全译文。原文更新默认增量保留远端余项；仅译文模式跳过不存在的文件，也不会新增词条。安全模式保护人工译文，强制模式允许覆盖。译文导入需要项目管理员以上权限。

普通与分类都能在同一文件容纳同 key 不同原文的词条，不再生成 `__冲突_*.json`。原文限定身份（requires_original_match）使用 `__transbridge_key__:v1:o:<base64url原key>:<完整SHA256原文>` 作为传输 key，原文精确 UTF-8，不规范化空白、换行或 Unicode。普通 key 保持原值，命中保留前缀时以 v1:k 转义。编码由完整本地身份决定，不依赖选择、分类、顺序或译文；不改变本地/插件 key 和全局顺序，不传历史远端词条 ID。编码后仍重复的同身份词条明确失败。

## 官方 API 合同

依据 [官网 OpenAPI 0.5.7](https://paratranz.cn/api-docs?format=json)，2026-10-25 只读核查：

- POST /projects/{id}/files：真实内容创建，path 指定虚拟目录。响应读取 file.id。
- POST /projects/{id}/files/{fileId}：只更新原文。incremental=true 保留缺失项；平台默认 false 会删除上传内容中缺失的词条。skip=false 启用平台哈希检查，重复内容返回 hashMatched。
- POST /projects/{id}/files/{fileId}/translation：按目标文件内 key 匹配，仅导入 translation/stage，不新增词条，不改 original/context。直接 JSON 使用 items 和带扩展名 filename；不发送自造 hash。force=false 保留服务端安全导入规则。
- GET /projects/{id}/files/{fileId}/translation：一次返回全部词条，不分页，仅用于下载等读取场景，不是上传必经步骤。

hashMatched/unchanged 是成功无变更；empty 表示没有导入内容，不能报成功。423 表示文件被其他更新/回滚锁定。全局官方上限为每用户每分钟 480 请求，本应用文件网关保守地每秒最多发起一次，多个任务共享同一用户预算。明确 429 按 Retry-After 有界等待重试，等待可取消，不自动重试超时或其他不明 POST 结果。

## 执行及恢复

本地生成所选文件 → 一次云端文件列表 → 按完整路径查当前 ID → 文件创建/原文更新/译文导入 → 文件回执。

正常上传不下载云端词条，不逐条发送 strings 请求，不回读每个词条，不保存上传关联，不隐式保存本地未保存编辑。旧绑定缺失或无效不阻止新上传。

同路径文件被删除重建后，使用当次文件列表里的新 ID。执行时明确返回 404，最多刷新列表并重新定位一次；不会因为 ID 变化要求额外审阅。超时保留结果待核实，不自动重发。权限失败、文件忙与部分完成有明确文件结果；原文已提交但译文失败会分别说明。

普通/分类、单独/批量上传结束后，有失败、待核实、取消或待处理文件时显示“补传未完成文件（数量）”按钮。点击后，后台重新获取云端文件列表，再按上次固定的内容和策略补传；响应丢失但云端已创建的文件会按当前路径取得 ID，不会重新创建同路径文件。已成功和服务端确认无变化的文件及分片不重传，部分完成后仅保留剩余项。运行时按钮隐藏并阻止重复启动；全部补传成功后按钮隐藏，状态显示“分类补传完成”或“文件补传完成”。云端列表读取失败时保留补传入口，可恢复连接后再点。

修改当前勾选或内容策略不改变上次的补传范围；需要采用新内容或策略时点击普通上传按钮，开始新的一轮。尚未捕获输入就失败或取消时，补传按原选择重新捕获；已捕获时沿用固定内容，本地后续编辑不会被补传覆盖。补传范围仅保留在当前上传窗口中，关闭窗口后不恢复；补传仍检查原工程、版本和账号，不改投其他目标。仅译文模式不会创建缺失文件，权限不足、本地输入无效等确定性错误仍需先解决原因，补传不会绕过这些规则。

413 时原文/创建请求按确定性序号拆文件，保留传输 key 与来源顺序。译文导入过大则在原文件内拆批；单条仍过大则失败，不回退到逐词条接口。旧文件和旧分片不会自动删除。

开始上传时固定内容、目标和执行配置；捕获完成后的本地编辑与设置修改不使当前上传失效，也不改变后续请求内容。切换工程或版本仍保护任务归属，不改投新目标；已发生的云端写入不会撤销。取消等待当前请求退出，显示已完成和未完成结果。GUI 每 100ms 消费最新进度，统计按文件，详情限制 200 行/32,000 字符。

## 下载与复核

GUI 单/多插件与 AI 助手 download_entries 共用应用层下载用例；GUI 使用“下载并保存”，助手传入当前或显式 source_ids。打开窗口只列出工程插件；点击后在同一后台 worker 中读取所选来源、下载、匹配并保存。

下载只定位插件层级：普通根文件、数字分片及插件目录下全部 JSON 文件。任务标题命名保持；下载前不运行分类器，不重建任务标题或分类路径，不以本地缺少标题排除云端文件，不建立目录索引或新增校验。对话引用的任务即使只在主文件中定义，也不影响其云端命名文件被读取。

新布局优先，缺失时才用省略插件后缀的旧布局。插件隔离、大小写消歧、全部启用来源共同决定旧名归属与同代 404 重定位保持，不混读两代内容。所选插件目录内的旧任务名、ID 和拆分文件一并匹配，其他插件目录不参与。

原有本地匹配按同插件精确原文、key 及已有译文确定候选，唯一非空译文自动采用，真正多译文在本轮确认。仅更新已有译文及对应状态，保留原文、上下文、顺序，不新增或删除源词条。

下载列表、文件内容、匹配输入和选择只留本轮内存，不写下载成果、合入回执、覆盖前内容或还原点。完成、失败、取消、关闭或改变插件/策略结束本轮时清理，下一次下载重新获取。窗口不再提供重开、另存下载成果或恢复覆盖前译文；旧下载成果也不再作为输入。助手不再返回可供 get_task_result/merge_task_result 处理的下载 result_id，不设项目分页 2000 条上限；只返回合入统计和本次未采用说明。正常诊断日志、工程来源成果和用户主动创建的工程历史保持原有职责。

成功合入后自动保存当前版本。至少一个所选来源完整读取成功时，也可保存已有未保存编辑；全部来源缺文件或失败时不宣称保存成功。保存失败保持原工作状态、清理下载输入，重试重新下载。既有原子保存、并发编辑和取消处理保持。

在线下载及离线 JSON 沿用已有传输 key 编解码。无需重传、改名、迁移或删除云端文件；历史工程包附件兼容保持。

## 本地脚本与清理边界

`export_to_categorized_json_files(collection, output_dir, ...)` 保留签名，仅把共享分类结果写到输出目录；任务文件位于 `output_dir/任务/`。两个脚本可从源码检出目录执行，也可在其他工作目录通过脚本绝对路径调用：

```powershell
uv run python scripts/convert_plugin_to_jsons.py plugin.esp output_dir
uv run python scripts/plugin_eet_to_json.py plugin.esp translations.xml output_dir
```

脚本不再含个人硬编码路径，支持 `--indent`、`--ensure-ascii`。第二个脚本保留原转换函数签名。分类对未知类型使用其他兜底，源顺序及哈希 key 与上传一致。

旧分类上传方法、专用冲突/选择窗口、旧 Sync GUI 执行器、单文件兼容上传适配、旧工作流上传器及旧工程保存适配已删除。普通和分类上传共用文件发布服务，下载共用批量成果与局部合入服务；规划、候选匹配、文件协议解析和现行传输工具保留。历史工程扩展字段只在导入边界处理，不为清理代码而改写原工程。

## 代码与排查

- application/file_upload.py：布局、序列化、文件执行/恢复及回执；retry_files 保留未完成文件和实际分片输入。
- application/io/paratranz_classification.py：共享分类、QUST:FULL 原名索引、重名与顺序；converter/translation_entry_collection_export.py 仅负责本地写入。
- application/io/paratranz_keys.py：唯一传输 key 编解码；paratranz_mapping.py 负责 JSON 往返。
- paratranz/api/paratranz_files_api.py：带官方使用备注的基础接口。
- paratranz/file_publication.py：文件传输、统一请求节流、429 等待与文件响应解析。
- paratranz/download_paths.py：只按插件层级选择根文件、数字分片及目录下全部 JSON，不重算分类路径。
- paratranz/batch_download_snapshot.py 与 application/sync/batch_download.py：共享文件列表、来源隔离、404 完整重定位、逐来源规划和本地编辑重基。
- application/sync/reviewed_snapshot.py 与 ui/operations/download_session.py：本轮不可变下载内容及会话复用，授权和执行不重复联网。
- ui/operations/categorized_upload_bridge.py 和 categorized_upload_dialog.py：工程快照、来源范围、后台任务、内容策略与文件结果；会话保存补传输入，窗口按 retry_count 提供手动补传，诊断用 retry 区分补传操作。
- application/sync/project_download.py、download_merge.py、download_run.py：GUI/助手共享编排、内容合入及本轮清理；UI 和助手分别注入既有执行许可。
- application/projects/durable_variant.py：下载候选的完整版本事务保存、写后核验和内存发布；普通编辑默认仍沿用原工作状态提交。
- ui/operations/facade.py 和 production.py：普通生产入口接入文件执行；分类入口共用同一流程。

沿用现有应用日志与 PARATRANZ_CATEGORIZED_UPLOAD 作用域。file_http_response 记录方法、相对端点、耗时及有界业务状态；file_upload_result 记录文件状态和原文/译文阶段是否提交；operation_failed 保留操作与异常。日志不输出翻译载荷或凭据。phase_finished 表示函数返回，最终结果须看文件回执，不能据此断言所有翻译均被覆盖。

验证使用受控 HTTP、真实本地工程及用户插件的隔离空工程。真实云端仅执行 GET；231 个分类文件完整读取，截图三句回填且保存重开一致，未生成下载成果文件，验证目录已清理。网络耗时不作为新旧性能对比或提速倍数依据。
