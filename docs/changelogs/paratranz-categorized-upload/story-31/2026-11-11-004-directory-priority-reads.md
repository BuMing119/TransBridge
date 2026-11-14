# S31：按优先目录补齐压缩包文件

- 日期：2026-11-11
- Epic/Story：paratranz-categorized-upload / S31。
- 关联：[计划](../../../../plans/paratranz-categorized-upload/plan.md)、[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)、[上一轮全包检索](2026-11-11-003-archive-member-discovery.md)。

## 用户要求与行为

用户明确要求先遍历路径索引、优先读取一个目录，只为缺少的文件到其他目录补齐；凑齐后停止，避免解压及比较重复编码副本。本次替换前一轮“物化全部候选并比较多副本”的方式，历史记录保留。

所有目标使用同一目录优先级，按文件需求读取：规范 raw 追加后缀与无歧义原名优先；通用目录 raw 优先于 utf8/utf-8，其他目录随后；同类目录优先覆盖较多所选文件的目录，最后按目录名稳定排序。取得一份有效内容后立即返回，不读取其他副本；文件缺失、空字节、编码/JSON/字段不合法或成员 CRC 损坏时尝试下一候选。归属歧义和大小安全上限不靠内容回退绕过。

空目录不产生文件候选；合法空词条列表 `[]` 是成功结果，不用另一副本填充。直接使用标准库 JSON 字节解析，支持 UTF-8、UTF-16、UTF-32 及 BOM；不根据文件夹名猜编码。规范 raw 保留 stage 可省略的既有兼容，原文、译文、key 以及字段类型/fileId 必须有效；通用候选仍要求显式 stage。

准备阶段仅建目录索引并核查最小大小预算；成员内容在 `get_file_translation` 调用时流式解压到单文件内存缓冲，不再写出逐候选临时 JSON。未读取副本不计解压预算，失败尝试计入实际读取预算，finally 关闭 ZIP 后清理临时目录。选取成功不再意味着其他副本已校验一致，此取舍由用户本轮明确要求。

## 可归属变更

- 修改 [artifact_member_index.py](../../../../src/transbridge/paratranz/artifact_member_index.py)：`selected_members` 返回按优先级排序的候选，新增 `_directory` 与 `_priority`，保留完整路径及全清单归属校验。
- 修改 [artifact_file_reads.py](../../../../src/transbridge/paratranz/artifact_file_reads.py)：prepare 只持有 ZIP 与成员索引；`_read_member` 流式、有界、可取消读取；`get_file_translation` 返回首份有效内容，诊断所有失败候选；`close` 关闭 ZIP 句柄后清理。
- 新增 [test_artifact_directory_priority.py](../../../../tests/paratranz/test_artifact_directory_priority.py)：19 项用例，以实际 ZIP 成员打开记录验证未用副本不会解压；覆盖完整/缺文件、无效内容补齐、六种编码、空目录与空列表、覆盖数排序、未用副本及失败尝试大小预算、读取中取消和全部候选失败诊断。
- 修改 [test_artifact_member_discovery.py](../../../../tests/paratranz/test_artifact_member_discovery.py)：多副本测试改为优先目录胜出且不比较其他副本。
- 修改 [test_artifact_file_downloads.py](../../../../tests/paratranz/test_artifact_file_downloads.py)：取消阶段断言跟随新的检索/索引进度文本。
- 修改 [ADR-047](../../../adr/047-opt-in-categorized-project-upload.md) 与 [计划](../../../../plans/paratranz-categorized-upload/plan.md) 的当前契约，新增本记录及 [索引](../../INDEX.md)。

工作区前轮全包检索改动是本轮起点；既有 `.agents/skills/bm-release/SKILL.md`、`.transbridge/` 未修改，不归本增量。没有修改依赖、lockfile、schema、安装器或上传流程；旧数据无需迁移。

## 验证

Windows / Python 3.12.12 / 现有 uv 环境。测试在本任务独立 `.tmp-artifact-priority-20261111` 下运行，因本机沙箱文件权限约束获准在沙箱外执行。

初轮新夹具把 tuple 当作 list 使用导致 2 项失败、95 项通过；修正夹具初始化后聚焦 97 项通过。继续增加取消、预算和诊断边界后，最终扩大回归 **469 项通过**，3 条既有 SWIG 弃用告警。

```powershell
$env:TEMP = 'D:\MyCode\TransBridge\.tmp-artifact-priority-20261111'
$env:TMP = $env:TEMP
uv run --no-sync pytest tests/paratranz/test_artifact_directory_priority.py tests/paratranz/test_artifact_member_discovery.py tests/paratranz/test_artifact_file_downloads.py -q -p no:cacheprovider --basetemp .tmp-artifact-priority-20261111/focused2 --tb=short
uv run --no-sync pytest tests/paratranz tests/contracts/paratranz/test_project_download_use_case.py -q -p no:cacheprovider --basetemp .tmp-artifact-priority-20261111/regression --tb=short
$env:UV_CACHE_DIR = 'D:\MyCode\TransBridge\.tmp-artifact-priority-20261111\uv-cache'
uv run --no-sync ruff check src tests --no-cache
uv run --no-sync ruff format --check src tests --no-cache
```

最终 Ruff 与格式检查通过（2540 文件）；中间一次长行格式问题已修正。

通过 `uv run --no-sync python -` 再次离线运行真实附件：模拟文件清单与本地下载器，经生产批量快照入口读取，与相同内容逐文件路径的快照完全一致。两个插件分别 8,451 / 11,711 条，合计 **20,162 条**；记录 `ZipFile.open` 确认 **189 个成员各打开一次**；临时 ZIP 目录清理成功。该验证没有真实网络请求，模拟清单从附件的完整相对路径生成。

## 验证边界

- 未运行全仓 pytest、GUI 交互、线上账号下载、安装包构建或发布。
- 验证了成员读取次数，没有独立耗时/峰值内存基准，不承诺具体性能数字。
- 不猜测 GBK 等非标准 JSON 编码；不支持的编码按文件回退，有效候选用尽才报错。
- 未读取副本不做一致性比较；按用户确认的优先级采用首份有效内容。
