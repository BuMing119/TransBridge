# Story 02: 输出组装

**所属方案**: plans/fomod-translation/plan.md
**技术模块**: backend
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 跨 Plan 依赖
- FR16 fileops/filter_rules.py（过滤侵权资源）
- Story 01（翻译后的 fomod_xml）

### 引用的架构决策
- ADR-014 决策 6: 扩展名白名单过滤（复用 FR16 fileops/filter_rules.py）+ 目录复制

## 验收标准

- [ ] 新建 src/transbridge/fomod/builder.py
- [ ] 复用 fileops/filter_rules.py 过滤侵权资源
- [ ] 目录复制：保留翻译后的插件 + 脚本 + fomod 元数据
- [ ] 组装产出可打包的目录结构

## 关键接口

```python
def assemble_output(src_dir, dest_dir, rules):
    # 复用 fileops.filter_files 分类，复制 kept 到 dest_dir，返回统计
```

## 实现步骤

### 步骤 1: 组装逻辑

涉及文件: src/transbridge/fomod/builder.py（新建）

实现要点:
- 收集 src_dir 所有文件相对路径
- 调 fileops.filter_files(files, rules) 得 (kept, stripped)
- 复制 kept 到 dest_dir（保持目录结构），优先硬链接回退复制
- 返回统计（kept_count/stripped_count）

边界条件:
- 空目录 → 空统计
- 硬链接不可用（跨卷）→ 回退 shutil.copy2

## 文件变更清单

src/transbridge/fomod/builder.py（新建）、tests/fomod/test_builder.py（新建）