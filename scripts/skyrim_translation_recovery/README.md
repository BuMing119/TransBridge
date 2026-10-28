# Skyrim 本地译文恢复

入口：`scripts/recover_skyrim_translations.py`。用于从旧 ParaTranz 导出与 ANK 汉化 Strings
重建独立的本地项目，保留已有修订并补齐空译文。仅支持使用外置 Strings 的 Skyrim 插件。
沿用仓库现有 `uv` 环境，不需要网络、API 密钥或新的依赖。

## 输入与使用

- `--project`：现有项目记录 JSON，使用其中启用的 `plugin.sse` 来源清单。原项目不被修改。
- `--paratranz`：完整导出 ZIP。优先读取 `utf8/`，核对对应 `raw/` 内容，避免重复导入。
  支持以插件名命名的普通 JSON 和 `Skyrim_1.json` 等数字分卷；未识别的文件条目全部保留到未匹配清单。
- `--ank`：包含翻译后 `*_english.strings/dlstrings/ilstrings` 的目录。
- `--output`：必须是尚不存在的新目录。推荐使用被 Git 忽略的 `out/`，避免提交用户译文。
- `--language`：输入 Strings 的语言文件名，默认 `english`。文件名不区分大小写。
- `--name`：新项目名称。

PowerShell 示例，先生成预览：

```powershell
uv run python scripts/recover_skyrim_translations.py `
  --project 'data/projects/<原项目记录>.json' `
  --paratranz 'C:\Downloads\export.zip' `
  --ank 'D:\Mods\ANK\strings' `
  --output 'out/skyrim-recovery-preview'
```

确认规则适用于输入后，在新的输出目录运行；增加 `--apply` 生成项目包，增加
`--install-root data` 将副本导入当前本地项目列表：

```powershell
uv run python scripts/recover_skyrim_translations.py `
  --project 'data/projects/<原项目记录>.json' `
  --paratranz 'C:\Downloads\export.zip' `
  --ank 'D:\Mods\ANK\strings' `
  --output 'out/skyrim-recovery-applied' `
  --name '老滚5－本地恢复' --apply --install-root data
```

不传 `--install-root` 时可在软件中手动导入 `recovered.transbridge`。脚本不会切换当前工程，
打开着的界面可能需要刷新项目列表或重新启动。新工程不继承云端绑定，不上传任何内容。
重复运行请指定新目录；同名项目的导入由现有仓库接口拒绝，原项目不会被覆盖。

## 匹配与边界

1. 在同一插件内，优先按旧键 **和完整原文** 匹配 Para；同键不同日志原文绝不串用。
2. 无精确匹配时，可使用同一插件、同一字段类型、完全相同原文的唯一译文。
   多候选的译文或状态不一致，保持空译文并记录冲突，ANK 不覆盖此冲突。
3. Para 译文和状态原样保留；隐藏、锁定的空译文也不由 ANK 填充。
   非空但状态为 0 的译文不会自动升级，需用户审阅后改变状态才能按发布策略写出。
4. 其余空缺按 **插件名 + Strings 类型 + 数值 ID** 从 ANK 补入，状态设为 1（已翻译），
   不标为已审核。ANK 应来自相容的游戏版本；报告记录两边的 ID 差异和文件校验值。
   ID 一致并不单独证明版本或语义一致，补入内容仍需抽查。
5. 多个项目词条若引用同一物理 Strings ID，共用唯一的 Para 决定；相互冲突时整组留空，
   逐条记录原候选。不同 Strings 类型中的相同数值 ID 保持独立。
6. 当前解析器未提取成词条的 Strings 内容以及被解析器排除的定位冲突，列在报告中，
   **不声称已在项目中恢复**。源文件完整保留，供后续修复解析器或单独处理。

该工具重建的是项目和译文，不生成游戏安装补丁，也不改变生产解析器的定位规则。
本地复合身份保留重复日志的原文限定；后续同步到 Para 前仍需处理旧远程键的迁移。

## 输出与验证

- `report.json`：执行状态、来源校验值、匹配统计、解析器诊断、ANK ID 差异、未提取候选。
- `decisions.jsonl`：每个当前词条的完整原文、译文、状态、匹配原因、Para 行引用及初始选择。
- `unmatched-paratranz.json`：未采用的旧云端行，完整保留原字段。
- `paratranz-original.zip` / `source-project.json`：本次输入留档。
- `sources/`：独立插件与原始英文 Strings 快照，从 BSA 获取的表也物化为散装文件。
- `recovered.transbridge`：`--apply` 时生成的可移植项目包，包含完整来源和译文来源记录。

预览只在指定的新目录中写文件，不注册项目。应用通过正式项目创建、校验、归档与导入接口执行，
不直接拼写或覆盖持久化项目 JSON。失败时报告标为 `failed` 并保留成果供检查；若项目包已生成但
注册失败，可手动导入该包。输入文件和游戏目录不写入。脚本自建的临时项目数据库自动清理。

测试：`uv run pytest tests/scripts/test_skyrim_translation_recovery.py -q`。
