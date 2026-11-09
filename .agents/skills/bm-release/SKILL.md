---
name: bm-release
description: 为 TransBridge 验证发行候选包、制作 Windows 安装包、管理发布标签并创建或发布 GitHub Release。用户要求发版检查、打包、打发布标签、创建 Release 草稿、正式发布或提到 git release 时使用。
---

# BM 版本发布

## 目标

将候选提交、安装包、发布说明和验证证据绑定到同一版本。通过 Git 标签固定提交，通过 GitHub Release 分发安装包并为桌面更新器提供正式版本。

## 意图与写入权限

- “发布检查/准备发版”只检查并汇总候选版本、发布说明草案和验证结果。明确要求制作候选包时，在隔离环境构建并交付到指定位置，保留已有发行物。
- “打发布标签”按请求范围创建本地标签，推送标签须在请求范围内。“创建 GitHub Release 草稿”停在草稿，上传资产按请求范围执行。“发布预发行版”或“正式发布指定版本”包含为该版本创建并推送必要标签、上传发行资产和发布草稿，并须通过下方的发布文案确认。
- “git release”视为 GitHub Release 相关意图。只提供这个短语时，先做发布检查。改版本、改更新日志、签名或推送分支需要明确授权。

## 执行约定

1. 查看 Git 状态、目标提交和远端。以目标提交中的 `pyproject.toml` 的 `[project].version` 为准，检查 `src/transbridge/_version.py`、`build.bat`、`transbridge.spec` 和 `installer/setup.iss` 的版本传递。
2. 按 `plans/release-hardening-v2/plan.md` 验证。在目标提交的干净 checkout 或等价隔离环境先运行 `uv run --frozen pytest tests/packaging/test_package_baseline.py tests/packaging/test_clean_release_smoke.py -q`，再按风险扩展测试和最终 QA 门禁。需要构建时复用 `build.bat`，确认 Inno Setup 实际在 `installer/output/` 生成目标安装包。记录 Python、依赖锁、命令、提交及工作树状态。
3. 记录发行物的版本、大小、SHA-256 和许可证清单。验证安装态不依赖仓库 `src`，在目标安装态验证 GUI、CLI `--help` 和 MCP stdio，并在干净 Windows 环境验证安装、升级和卸载。

## 发布文案确认

- 根据 `docs/更新日志.md` 和上一个正式发布标签到目标提交的实际差异起草 GitHub Release 标题和正文。首次发布按实际交付范围整理。正文包含发布声明和面向用户的更新说明，按需写清功能变化、修复、升级注意事项与已知问题。桌面更新器会显示 Release 正文。用户另行要求更新公告时，一并列出目标渠道和公告全文。
- 完成已获授权的发布准备后，向用户展示版本、标题和将公开的完整文案，请用户明确确认。发布指令本身只授权发布动作。用户已明确同意本次使用的全文时，直接沿用。确认后若修改标题、正文或公告内容，展示修订稿并重新确认。等待确认期间，保留本地结果或已获授权的 GitHub 草稿。

## GitHub Release 流程

仅打本地标签时，核对候选版本、目标提交和本地同名标签后执行第 2 步的本地操作。

1. **确定目标。** 桌面更新器使用 `BuMing119/TransBridge`。核对 Git 远端的推送地址与传给 GitHub CLI 的 `--repo` 一致，检查 CLI 登录及仓库写权限。确认完整提交 SHA 已在目标远端可用，候选构建和验证均对应这个提交。确定版本、标签、标题、说明、发行资产及发布类型。

   在首次远端写入前，用 `gh release view $tag --repo $repo --json tagName,name,body,isDraft,isPrerelease,assets,url` 核对同名 Release。已有公开 Release 时核验并报告现状。已有草稿时核对标题、正文、发布类型及远端标签指向，遇到差异时停止。查询失败时先区分记录不存在、权限不足与网络故障。

2. **固定标签。** 正式版用 `vX.Y.Z.W`，与项目四段版本一致。检查本地和远端同名标签，附注标签比较其解引用后的提交。仅在授权范围内创建缺失的标签，显式指定已验证的提交（`git tag -a $tag $commit -m $title`），只推送该标签（`git push $remote "refs/tags/${tag}:refs/tags/${tag}"`）。推送后重新核对远端标签指向。发布所需的提交未推送或同名标签指向不同提交时停止，保留已有引用。
3. **创建或续接草稿。** 确认远端标签指向目标提交后，用 `gh release create $tag --repo $repo --verify-tag --draft --title $title --notes-file $notesPath` 创建缺失的草稿，预发行草稿加 `--prerelease`。已有草稿经核对后复用。
4. **上传并核对资产。** 用 `gh release upload $tag --repo $repo $assetPath` 上传明确列出的已验证文件。桌面更新所需安装包须为 `TransBridge_vX.Y.Z.W_Setup.exe`，大小为 1 至 2,147,483,647 字节。契约以 `src/transbridge/application/update_service.py` 为准。

   核对远端资产文件名、大小和 SHA-256，优先比对 GitHub REST 资产的 `digest`。若该字段缺失，从草稿下载并重算 SHA-256。若安装包缺少 `digest`，另上传并核对 `TransBridge_vX.Y.Z.W_Setup.exe.sha256`，内容为一行 ASCII 文本，由 64 个十六进制字符、两个空格和安装包文件名组成。若 `digest` 存在但无效或不匹配，视为校验失败。

   已有同名资产须验证内容相同后复用。发生中断时先重查远端标签、草稿和资产，再续接缺失步骤。发现冲突时报告差异，保留已有资产。

5. **发布并回读。** 在公开发布已获授权、用户已确认发布文案、远端草稿标题和正文与确认稿一致、远端标签及资产核验通过且没有发布阻断时，用 `gh release edit $tag --repo $repo --verify-tag --draft=false` 发布。官方仓库的新正式版默认作为桌面更新源，先确认四段版本高于当前 Latest（已有时），再显式加 `--latest`。预发行版保留 `--prerelease`，明确保留当前 Latest 的正式版使用 `--latest=false`。

   回读目标 Release 核对标签、公开状态、预发行状态和资产。若指定为 Latest，另查公开 `/repos/BuMing119/TransBridge/releases/latest`，核对桌面更新器所需标签、安装包 URL、大小及 SHA-256。

命令参数见 [GitHub CLI Release 手册](https://cli.github.com/manual/gh_release) 和 [GitHub Release Assets API](https://docs.github.com/en/rest/releases/assets)。

## 发布阻断

构建失败、核心入口无法启动、版本或提交不一致、安装态依赖源码路径、缺失必要许可证或干净 Windows 环境验证、安装包或校验和不符合更新契约，均标为 Blocker 并停止公开发布。报告具体失败步骤和可复查的证据。

## 报告

报告记录候选版本、目标提交和 Git 状态、环境与锁定依赖、验证命令和退出码、产物路径与 SHA-256、冒烟验证和许可证结果、未完成验证与原因。涉及远端操作时，附标签、Release 链接、草稿或公开状态、Latest 核验结果及尚待完成的步骤。

若用户要求保存报告，遵循仓库 `docs/test-reports/` 的既有命名和证据格式。
