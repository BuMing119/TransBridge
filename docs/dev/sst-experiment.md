# SST 读写复原实验

日期：2026-11-08。入口：`uv run python -m scripts.sst_lab`。

格式实现已由独立实验迁入 `src/transbridge/parser/xt/sst_codec.py`，命令行
`scripts/sst_lab.py` 复用生产模块；GUI 和助手经共享 I/O 接入实验性 SST 写出。
正式 MCP 入口尚未暴露读写工具，能力保持不支持。使用与边界见 [ADR-052](../adr/052-sst-io-integration.md)
及[集成计划](../../plans/sst-integration/plan.md)。
旧解析器已由严格 codec facade 替代；旧工程缺少 v2 物理身份时拒绝猜测写回，需重新导入并迁移译文。

## 格式依据

上游：MGuffin/xTranslator，固定提交 `9aa38d60860273401f8bb0dd1557c82a295c41b3`。
代码独立实现二进制合同，没有复制上游 Pascal 实现。

- [SaveSSTFile 与 loadSstEdit](https://github.com/MGuffin/xTranslator/blob/9aa38d60860273401f8bb0dd1557c82a295c41b3/TESVT_SSTFunc.pas#L203-L435)：头、计数、文本与记录的写入/读取顺序。
- [rEspPointerLite 与 sStrParams](https://github.com/MGuffin/xTranslator/blob/9aa38d60860273401f8bb0dd1557c82a295c41b3/TESVT_typedef.pas#L68-L97)：字段宽度及状态位。
- [StringHash](https://github.com/MGuffin/xTranslator/blob/9aa38d60860273401f8bb0dd1557c82a295c41b3/TESVT_Const.pas#L2452-L2463)：对 UTF-16 每个 code unit 的低字节执行 FNV-1a，并非 UTF-8 字节。

SSU8 文件头为 `magic[4] + flag[1] + label_count[int32 LE] + labels`。
SSU9 在 flag 后插入 `master_count[int32 LE] + masters`。master 是 int32 字节长度加 UTF-16LE；
label 是 int32 ID、int32 字节长度加 UTF-16LE。没有标签和 master 时，头分别为 9 和 13 字节。

之后逐条读至 EOF，每条结构为：

```text
list_index       uint8       0/1/2 三个文本列表
string_id        int32 LE
form_id          uint32 LE
record_type      4 bytes
field_type       4 bytes
index            uint16 LE   原始零基索引
index_max        uint16 LE
edid_hash        uint32 LE
collab_id        uint8
params           uint8
original_length  int32 LE    字节数
original         UTF-16LE
translation_len  int32 LE    字节数
translation      UTF-16LE
```

固定字段共 27 字节；没有记录总数、终止符、填充、嵌套子记录或任意尾部。
实验实现保留所有字段和顺序，包括未知四字节签名、状态位、空文本和原始 UTF-16 code units。
不完整字段、负数/奇数文本长度、非法计数、非法列表索引及数值溢出会明确报错并带偏移或字段信息。

## 旧实现为何会自检通过但无法互操作

旧 SSU8 解析器从偏移 16 开始，真实无标签头只有 9 字节；旧 SSU9 通过特征扫描找记录，
遗漏 `list_index` 和 `string_id`，把后续普通记录误当作 extra/subrecords。
旧 writer 把 master count 和字符串长度错当成 type/大端名称长度，无法生成正确的文件头。

旧 SSU9 的 `f2` 实为 `edid_hash`，`group_index` 实为 `index_max`；旧 SSU8 的身份字段也存在错位。
这些不是可直接套用到已保存工程的字段重命名。旧实现保存未解析字节，仍可能做到字节相等，
所以“解析后原样拼回”不能证明已经完整理解文件。旧 SSU8 在正常 EOF 下还会把最后记录重复放进 trailing。

真实语料结果：19 个原始样本、9,690 条记录全部严格消费到 EOF，逐字节往返一致。
其中 `hlioremi` 是 8,604 条（旧扫描为 8,487）；`_resourcepack` 是 27 条（旧为 26）；
`ccvsvsse002-pets` 是 171 条。两个 86 字节样本各有 1 条，旧解析器返回空集合。
`ssu8/` 文件夹中有 SSU9 文件，必须按 magic 判断。
历史 `_test_rt.sst`、`_test_roundtrip.sst` 的 master 长度非法，新读取器明确拒绝。

## 手动验证文件

已生成的交付文件位于 `out/sst-lab-20261108/`，受仓库已有 `/out/` 忽略规则保护，
不纳入提交。它们是供用户验收保留的成果，不是临时测试缓存。所有原样本保持不变。

1. `demo9_english_chinese.sst`：从零创建的 SSU9，3 条；三个文本列表各 1 条。
2. `demo8_english_chinese.sst`：相同内容的 SSU8，3 条。
3. `demo9_patched_english_chinese.sst`：第一条译文改为 `SST实验：修改后的长剑译文`。
4. `hlioremi_roundtrip_english_chinese.sst`：8,604 条，和原文件逐字节一致，作为打开流程的对照。
5. `hlioremi_patched_english_chinese.sst`：8,604 条，仅物理序号 0 的译文改为
   `SST实验：修改后的对白标题`；FormID `0503D44F`、`DIAL:FULL`。
6. `resourcepack_patched_english_chinese.sst`：27 条，仅物理序号 0 的译文改为
   `SST实验：修改后的激活物名称`；FormID `030000CF`、`ACTI:FULL`。

请使用 xTranslator 的 SST 词典编辑/打开入口，先打开两个 demo，确认能看到剑、书籍、对白的原文和中文。
再打开 patched demo，确认新译文；然后检查原始样本对照和两个真实文件修改版。
界面排序可能不同，测试工具的 `--record` 是文件内零基序号，不是界面行号。
若程序显示过滤后的数量，请先检查三个文本列表、过滤条件及旧数据状态。

如需验证反向兼容，可在 xTranslator 中修改 demo 的一条译文，并另存为新的 SST 文件；
再用 `inspect` 读取另存文件。尚未执行真实 xTranslator 打开、另存、插件应用验证；由用户手动完成。
已确认用户提供的本机程序版本为 1.6.0.1，但未启动或操控该程序。

## 可重复命令

在仓库根目录运行。输出路径父目录需存在，每个输出文件必须是新路径；工具拒绝覆盖任何现有文件。

```powershell
uv run python -m scripts.sst_lab inspect tests/trans_exe/xt/hlioremi_english_chinese.sst
uv run python -m scripts.sst_lab inspect out/sst-lab-20261108/demo9_patched_english_chinese.sst --record 0
uv run python -m scripts.sst_lab create-demo --format SSU9 --output out/new-demo.sst
uv run python -m scripts.sst_lab roundtrip out/new-demo.sst --output out/new-demo-copy.sst
uv run python -m scripts.sst_lab patch out/new-demo.sst --record 0 --translation "修改后的译文" --output out/new-demo-patched.sst
```

`inspect` 默认只打印数量和哈希，显式指定 `--record` 才打印文本。`patch` 仅改变指定译文，
保留状态位和所有身份信息；例如 pending 状态在 xTranslator 内可能触发重置，不能把文本写入等同于状态转换。
从零生成的 demo 用 translated 状态，不包含 pending。

## 自动验证与后续边界

聚焦回归：`uv run pytest tests/parser/test_sst_lab_codec.py tests/parser/test_sst_lab_cli.py -q`，
本次 93 passed。包含独立 struct 构造的 wire fixtures、19 个真实文件、2 个损坏旧产物、
长于 65,535 字节的文本、补充 Unicode、未知签名、全截断边界和 CLI 防覆盖/单条修改。
全库 `ruff check src tests`、`ruff format --check src tests` 加两个新脚本通过。
实际执行使用 `--offline --no-sync`、任务专用 uv 缓存及 pytest `--basetemp`，避免沙箱默认临时路径限制。

扩大回归命令为 `uv run pytest tests/parser tests/capability/test_capability_matrix.py -q`：
最终 **271 passed**，包括上述 93 项。首轮 269 passed、2 failed，两个失败均为插件写回测试访问
沙箱默认 TEMP 被拒绝；将该测试进程的 TEMP/TMP 指向任务专用目录后，同一范围全部通过。
未运行全仓 pytest、发布安装包或真实 xTranslator 测试；未提交或推送代码。

以上 93/271 项为独立实验阶段验证记录；集成阶段证据另记于集成计划和增量记录。
用户已手动反馈真实修改版可以使用；尚未全面验收从零生成词典在 xTranslator 中另存、
应用至插件及全部游戏/版本兼容性。能力维持实验性。

## New ANK 扩展语料

2026-11-08 对 New ANK 的80份 SST 完成验证：共99,359条记录，63份 SSU8、17份 SSU9。
80份未修改逐字节往返、每份单条译文修改及每个存在文本列表的空译文修改均通过。
原样本只读；交付产物与逐文件统计位于 `out/sst-ank-lab-20261108/manifest.json`。
对应回归185项通过；初次沙箱默认临时目录权限错误后，在沙箱外运行通过。
这批证据扩大真实语料覆盖，不等于所有文件已在 xTranslator GUI 人工打开、另存或应用插件。

## 软件中的使用方式

开始中心创建工程时，可通过“选择已有译文”选入 SST；未选择插件时将 SST 本身作为来源。
导入后可编辑、保存工程，并通过写回选择 `.sst` 输出位置；输出保留原 SSU8/SSU9。
插件来源在写回窗口选择“输出格式 → SST 词典 SSU9（实验性）”，选择新输出位置后预检并确认。
批量写回在全部来源为插件/SST 时提供 SST 选项，选择目录后分别输出；原 SSU8 保留其版本。
助手使用 `write_back(target="sst", path="…/dictionary.sst")` 或 `output_dir`，多来源通过 `source_keys` 选择。
旧工程若提示 `SST_REIMPORT_REQUIRED`，重新导入原 SST，再迁移现有译文；不要改写旧 key 绕过校验。
插件含 VMAD、未知可译字段或非 ASCII 主文件名时，当前拒绝从零生成；原 SST 写回不受这些生成限制。
