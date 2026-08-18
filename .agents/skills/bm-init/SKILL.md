---
name: bm-init
description: 在仓库中检查或初始化 BM 工作流配置、文档目录和索引骨架。用户首次启用 BM skills、要求创建 .codex/bm_config、检查配置完整性、迁移旧 .Codex 配置或修复 BM 目录结构时使用。
---

# BM 初始化

## 目标

以幂等、最小侵入方式建立 BM skills 可选的项目配置。

## 模式

- 检查：只读取并报告，不写文件。用户提到 check、检查或状态时使用。
- 初始化：创建缺失项，保留已有内容。
- 强制更新：只有用户明确要求覆盖时使用；先创建带时间戳的备份，不覆盖已有索引内容。

## Codex 执行约定

- 统一使用小写 .codex/bm_config。Windows 上发现 .Codex 时先解析为同一路径；大小写敏感文件系统上提供迁移建议，不静默复制两套配置。
- 先扫描仓库现有 docs、plans、tests 和源码目录，再选择默认值。不要假设 server/web、pytest 或特定项目结构。
- 初始化是可逆的本地写入，可直接创建明确缺失项；已有配置与建议值冲突且会改变行为时才询问用户。
- 所有文本修改使用 apply_patch；目录只在确有文件需要写入时创建。

## 默认 paths.json

包含 _schema、docs_dir、plans_dir、changelogs_dir、adr_dir、test_reports_dir、tests_dir 和 src_dir。默认值优先采用仓库已有目录；常见回退为 docs、plans、docs/changelogs、docs/adr、docs/test-reports、tests、src。

## 默认 pilot.json

包含 _schema、dev_skill、defaults.complexity、skip_stages.story_detail、skip_stages.council_review。Codex 环境默认 dev_skill 为 bm-dev；不要保留“为其他模型规避限流”的默认假设。

## 工作流

1. 检查两份配置 JSON 是否存在、可解析，路径是否为仓库内相对路径。
2. 检查配置指向的目录和索引；把存在但格式不同的文件视为用户内容，不强制改成模板。
3. 检查模式下输出缺失、无效和建议项后结束。
4. 初始化模式下只创建缺失配置及必要的空索引。requirements、ADR、plan、test report 等内容文件由对应 skill 创建。
5. 重新解析 JSON，并确认所有新路径都位于工作区内。
6. 汇报创建、保留、迁移建议和下一步，不自动调用其他 skill。

## 最小索引

- docs/INDEX.md：文档入口，可包含需求、ADR、测试报告链接。
- plans/INDEX.md：方案入口。
- docs/changelogs/INDEX.md：增量记录入口。

只在文件不存在时创建简洁标题；后续 skill 可按仓库实际格式维护。

## 边界

- 不创建源码、测试样例、Git 仓库或依赖环境。
- 不删除旧配置，不覆盖非空索引，不把个人姓名/邮箱写入仓库默认配置。
- 不允许 paths.json 通过绝对路径或 .. 逃逸工作区。
