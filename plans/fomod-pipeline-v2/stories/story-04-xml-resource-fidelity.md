# Story 04：FOMOD XML 与资源保真、过滤规则修正

- 所属 Plan：[FOMOD Pipeline V2](../plan.md)
- 状态：已确认（2026-08-18）
- 追溯：FR23.7；ADR-014/015；R-045
- 依赖：S02/S03

## 目标与验收

只改变可翻译文本，保留未知节点、属性、namespace、顺序/BOM（在声明保真范围内）和图片引用；过滤按目录语义，未知资源默认保留；skip 只有 hash 一致才成立。

## 数据流与接口

XML source snapshot → extract stable node locators/text → Candidate translations → patch copy → serialize → structural/fidelity validator。资源流：archive manifest → `ResourceRoleClassifier`（FOMOD UI/plugin/data/unknown）→ FilterDecision(reason) → output manifest。source arbitration 生成显式来源/摘要。

## 实施步骤

1. 替换仅递归文本写入的隐式定位，为节点 path+attribute/text kind 建 locator。
2. 解析/序列化保存 encoding/BOM/namespaces；未知节点不参与翻译但必须保留。
3. 默认 FilterRules 不以图片扩展名全局删除，结合 `fomod/` 目录和引用图。
4. skip-hash 同时比较来源/目标摘要与 policy version；不一致重新处理。
5. 输出 fidelity report：changed nodes、preserved resources、lossy diagnostics。

## 测试、边界与迁移

golden fixtures 覆盖 UTF-8/UTF-16LE BOM、namespace、未知属性/节点、图片/相对引用、同名目录、hash 命中/错配。语义比较为主，若要求 byte fidelity 明确列入 capability。无法解析 XML 为 fatal，不能忽略后继续发布。
