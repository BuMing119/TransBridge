# Skyrim STRINGS 审计结论 / Audit Conclusion

[中文证据链](EVIDENCE_CHAIN.md) · [English evidence chain](EVIDENCE_CHAIN.en.md) ·
[审计工具说明](README.md)

## 中文

[With Light](https://www.nexusmods.com/skyrimspecialedition/mods/139134?tab=files) 最初上传于
2025-01-15。[Nexus 175184](https://www.nexusmods.com/skyrimspecialedition/mods/175184?tab=files) 最初上传于
2026-03-21，比 With Light 晚了约 14 个月。

两套汉化按文件和 String ID 对齐后，共有 99,010 条双方都不为空的文本。175184 与 With Light
完全一致的有 58,501 条，占 59.09%；把高度重合的文本也算进去，占 64.09%。

为了看看“翻译同一个游戏”本身会造成多少自然重合，又用[重光ank](https://www.nexusmods.com/skyrimspecialedition/mods/134478)
和 With Light 做了同样的对比。这个正常基线的完全一致率是 21.66%。去掉容易撞译的短文本后，
差距更明显：至少 6 字的文本，175184 与 With Light 有 58.50% 完全一致，正常基线只有 11.20%；
20–79 字的文本，两边是 60.94% 和 7.67%。

三套汉化放在一起看，只有 175184 与 With Light 完全相同的文本有 40,520 条，只有重光ank 与
With Light 相同的只有 3,469 条。只看至少 20 字的文本，两边分别是 15,244 条和 174 条。

这些事实说明，175184 与 With Light 的重合不是正常撞译，也不是几个术语恰好相同。两套文本之间
存在大规模、系统性的继承关系。结合发布时间，继承方向指向 **With Light → Nexus 175184**。
175184 不能被视为一套与 With Light 无关、完全独立完成的汉化。

---

## English

[With Light](https://www.nexusmods.com/skyrimspecialedition/mods/139134?tab=files) was originally uploaded on
15 January 2025. [Nexus 175184](https://www.nexusmods.com/skyrimspecialedition/mods/175184?tab=files) was originally
uploaded on 21 March 2026, about 14 months later.

After aligning the two translations by file and String ID, 99,010 paired texts were non-empty on both sides. Nexus
175184 and With Light are exactly identical in 58,501 of them, or 59.09%. Including highly overlapping texts raises
the figure to 64.09%.

To see how much overlap normally occurs simply because two projects translate the same game, the same comparison
was run between [重光ank](https://www.nexusmods.com/skyrimspecialedition/mods/134478) and With Light. That normal
baseline is 21.66% exact. Removing short texts that are more likely to match naturally makes the difference larger:
for texts of at least six characters, the figures are 58.50% and 11.20%; for texts between 20 and 79 characters,
they are 60.94% and 7.67%.

Looking at all three translations together, 40,520 texts match only between Nexus 175184 and With Light. Only 3,469
match between 重光ank and With Light. For texts of at least 20 characters, those counts are 15,244 and 174.

These facts show that the Nexus 175184–With Light overlap is not ordinary translation coincidence and is not
limited to a few shared terms. The two contain large-scale, systematic textual inheritance. Combined with the
publication dates, the direction is **With Light → Nexus 175184**. Nexus 175184 cannot be treated as a wholly
independent translation unrelated to With Light.
