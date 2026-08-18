# 横向质量、安全与发布审查（阶段性结果）

- Agent：`quality_iteration_review`
- 状态：阶段性；后续独立续跑。

## 已确认高优先级风险

- ParaTranz 401 输出完整 Token；LLM/Embedding/MCP/ParaTranz 凭据以明文配置保存。
- Archive 缺成员数、总展开大小、单文件大小和压缩比预算；边界判断与第三方解压行为需要恶意 fixture 验证。
- Agent Parser、MCP、EET/XT Writer 等现有测试多为 negative path 或无 spec mock，不能证明成功入口。
- FOMOD 测试仅覆盖浅层函数，无全流水线、取消、写失败、TM 冲突、根目录归一化和发布事务。
- 无 wheel 构建/安装/console script/import smoke；发布入口和命名空间存在确定漂移。
- 7z/RAR 代码依赖未在 pyproject/lock 声明。
- Windows MCP stdio、路径/UNC/drive-relative、PyInstaller 启动缺门禁。

## 初步质量门禁

- 每个 Use Case 的 adapter contract tests，GUI/Agent/MCP/FOMOD 使用相同 fixture 结果等价。
- ESP/EET/XT/SST/DSD/Strings 的 golden 与 round-trip fixtures。
- FOMOD 小型 archive E2E、取消、partial/fatal、事务发布。
- Entry/Stage/Variant/TM 属性测试与不变量测试。
- 并发 JobRuntime/Checkpoint 单调性和 late-event 拒绝测试。
- Windows 路径、MCP stdio、打包数据目录测试。
- wheel build/install/import/CLI 与 PyInstaller 启动 smoke 作为 release gate。

正式横向报告将补风险优先级、覆盖矩阵、性能基准和 CI 分层。
