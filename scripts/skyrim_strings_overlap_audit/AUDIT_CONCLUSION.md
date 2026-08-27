# Skyrim STRINGS 审计结论 / Audit Conclusion

[中文证据链](EVIDENCE_CHAIN.md) · [English evidence chain](EVIDENCE_CHAIN.en.md) ·
[审计工具说明](README.md)

## 中文结论

### 审计对象

- 被调查作品：[Unofficial Chinese Translation for SAE（Nexus Mods 175184）](https://www.nexusmods.com/skyrimspecialedition/mods/175184)
- 被大量重合的作品：[With Light（Nexus Mods 139134）](https://www.nexusmods.com/skyrimspecialedition/mods/139134)
- 独立翻译基线：[重光ank（Nexus Mods 134478）](https://www.nexusmods.com/skyrimspecialedition/mods/134478)

### 明确结论

在把作者声明为独立完成、没有抄袭 With Light 的“重光ank 与 With Light”作为正常负对照后，
Nexus 175184 与 With Light 的文字一致程度远远超出独立翻译基线。异常不仅存在于容易自然撞译的
短名称和固定术语中，在至少 6 字以及 20–79 字的文本中反而更加突出。

**就文字来源而言，现有统计证据不支持“Nexus 175184 是完全独立翻译”的解释；数据明显更符合
大量直接或间接复用 With Light 译文，或二者继承同一中文底稿的情形。**

因此，本审计将 Nexus 175184 标记为：**疑似抄袭或未署名复用作品**。如果发布者无法提供早于
With Light 的独立版本历史、合法授权或可验证的共同来源，现有文字证据支持对未署名复用或抄袭的
质疑。

### 支撑结论的核心数据

- 全部有效文本完全一致率：Nexus 175184 对 With Light 为 59.09%，正常基线为 21.66%；前者高
  37.43 个百分点，是基线的 2.73 倍。
- 至少 6 字的完全一致率：58.50% 对 11.20%；前者高 47.30 个百分点，是基线的 5.22 倍。
- 20–79 字的完全一致率：60.94% 对 7.67%；前者高 53.27 个百分点，是基线的 7.95 倍。
- 三方共同覆盖的文本中，仅 Nexus 175184 与 With Light 完全相同的有 40,520 条，仅重光ank 与
  With Light 完全相同的只有 3,469 条。
- 在至少 20 字的文本中，上述排他一致数量分别为 15,244 条和 174 条。

### 结论边界

本结论是文字来源审计结论，不是司法裁判。统计可以排除“普通独立翻译自然重合”作为充分解释，
但不能单独确定复用方向、授权状态或具体责任人。发布时间、历史版本、授权记录和作者陈述应作为
独立证据与本报告共同使用。

---

## English Conclusion

### Works examined

- Investigated publication: [Unofficial Chinese Translation for SAE (Nexus Mods 175184)](https://www.nexusmods.com/skyrimspecialedition/mods/175184)
- Work with extensive shared wording: [With Light (Nexus Mods 139134)](https://www.nexusmods.com/skyrimspecialedition/mods/139134)
- Independent-translation baseline: [重光ank (Nexus Mods 134478)](https://www.nexusmods.com/skyrimspecialedition/mods/134478)

### Explicit conclusion

Using the author-declared independent “重光ank vs. With Light” comparison as the normal negative control, the
wording agreement between Nexus 175184 and With Light is far beyond the independent-translation baseline. The
anomaly is not limited to short names or fixed terminology that might naturally collide; it becomes substantially
stronger for texts of at least six characters and for texts between 20 and 79 characters.

**As a text-provenance finding, the available statistical evidence does not support the claim that Nexus 175184
was translated wholly independently. The data are much more consistent with extensive direct or indirect reuse of
With Light wording, or with both works inheriting the same Chinese translation base.**

This audit therefore marks Nexus 175184 as **suspected plagiarism or uncredited reuse**. Unless the publisher can
provide an independently dated history predating With Light, valid authorization, or a verifiable common source,
the present textual evidence supports the concern that substantial translation content was reused without credit.

### Core figures supporting the conclusion

- Exact overlap across all valid texts is 59.09% for Nexus 175184 vs. With Light and 21.66% for the normal baseline:
  a 37.43-point difference and 2.73 times the baseline.
- Evidence-length exact overlap is 58.50% vs. 11.20%: a 47.30-point difference and 5.22 times the baseline.
- Exact overlap for 20–79-character texts is 60.94% vs. 7.67%: a 53.27-point difference and 7.95 times the baseline.
- Among texts covered by all three works, 40,520 are identical only between Nexus 175184 and With Light, while only
  3,469 are identical between 重光ank and With Light.
- For texts of at least 20 characters, the corresponding mutually exclusive counts are 15,244 and 174.

### Limits of the conclusion

This is a text-provenance audit conclusion, not a judicial determination. The statistics can reject ordinary
independent-translation collision as a sufficient explanation, but cannot alone determine reuse direction,
authorization status, or individual responsibility. Publication dates, historical versions, authorization records,
and author statements should be considered alongside this report as separate evidence.
