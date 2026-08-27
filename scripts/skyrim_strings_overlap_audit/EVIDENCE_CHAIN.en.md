# Skyrim STRINGS Text-Overlap Evidence Chain

[Bilingual audit conclusion](AUDIT_CONCLUSION.md) · [中文版](EVIDENCE_CHAIN.md) ·
[Tool documentation](README.en.md)

## Purpose and limits

This evidence chain tests whether the wording overlap between
[Nexus Mods 175184](https://www.nexusmods.com/skyrimspecialedition/mods/175184) and
[With Light (Nexus Mods 139134)](https://www.nexusmods.com/skyrimspecialedition/mods/139134) is materially above
the overlap normally observed between two independently produced translations.

The author of [重光ank (Nexus Mods 134478)](https://www.nexusmods.com/skyrimspecialedition/mods/134478)
states that it was independently translated and did not copy With Light. This audit therefore uses “重光ank vs.
With Light” as the declared non-copying negative-control baseline. The statistical tool does not independently
prove that declaration; it measures the investigated pair against that stated baseline.

The results support a finding of abnormally extensive shared wording between Nexus 175184 and With Light,
consistent with suspected uncredited reuse or a shared translation base. Similarity alone cannot establish the
direction of reuse, authorization status, or legal liability. Publication chronology, version history,
authorization records, and author statements remain necessary.

## Samples and common method

- Each translation set contributes the same 240 `_chinese` files; duplicate `_english` mirrors are excluded.
- Files are aligned by plugin and STRINGS type, and records are aligned by UInt32 String ID.
- All three comparisons use the same script, normalization, thresholds, and 99,010 non-empty paired texts.
- “Exact” means identical after Unicode NFKC normalization and whitespace removal; punctuation and wording remain.
- “Evidence-length” requires both normalized texts to contain at least six characters, reducing collisions caused
  by short names and fixed terminology.

## Evidence chain

### Evidence 1: normal negative control between independent translations

重光ank vs. With Light:

- All valid texts: 21.66% exact, 22.74% exact-or-high, and 42.10% mean similarity.
- At least six characters: 11.20% exact, 12.56% exact-or-high, and 36.66% mean similarity.
- 20–79 characters: 7.67% exact, 9.49% exact-or-high, and 35.35% mean similarity.

This pair supplies the audit’s normal baseline. It also demonstrates that short texts materially inflate the
overall exact-match rate, making the longer-text strata more probative.

### Evidence 2: investigated pair far above the normal baseline

Nexus 175184 vs. With Light:

- All valid texts: 59.09% exact, 64.09% exact-or-high, and 75.64% mean similarity.
- At least six characters: 58.50% exact, 65.01% exact-or-high, and 78.66% mean similarity.
- 20–79 characters: 60.94% exact, 74.61% exact-or-high, and 86.67% mean similarity.

Compared with the normal baseline:

- Overall exact overlap is 37.43 percentage points higher, or 2.73 times the baseline.
- Overall exact-or-high overlap is 41.35 points higher, or 2.82 times the baseline.
- Evidence-length exact overlap is 47.30 points higher, or 5.22 times the baseline.
- Exact overlap for 20–79-character texts is 53.27 points higher, or 7.95 times the baseline.

The excess is not confined to names, locations, or short fixed terminology; it becomes stronger in the
20–79-character stratum.

### Evidence 3: second control rejects a generally high 重光ank overlap

Nexus 175184 vs. 重光ank:

- All valid texts: 26.58% exact, 27.56% exact-or-high, and 46.34% mean similarity.
- At least six characters: 13.55% exact, 14.82% exact-or-high, and 38.52% mean similarity.
- 20–79 characters: 5.89% exact, 7.53% exact-or-high, and 35.06% mean similarity.

These figures are close to the negative-control baseline and far below the Nexus 175184 vs. With Light results.
The investigated pair therefore cannot be explained by 重光ank producing similarly high overlap with any base
translation.

### Evidence 4: mutually exclusive three-way partition

Among the 99,010 valid texts covered by all three sets:

- all three are identical: 17,981;
- only Nexus 175184 and With Light are identical: 40,520;
- only Nexus 175184 and 重光ank are identical: 8,335;
- only 重光ank and With Light are identical: 3,469;
- all three differ: 28,705.

Among the 73,574 texts of at least six characters, 37,010 match only between Nexus 175184 and With Light, while
only 1,635 match between 重光ank and With Light. For the 29,148 texts of at least 20 characters, the corresponding
counts are 15,244 and 174. This exclusive partition directly localizes the exceptional overlap to Nexus 175184 and
With Light rather than to fixed short game text shared by all translations.

## Reproduction

Obtain each published file from the three Nexus pages linked above, then run all three comparisons from the
TransBridge repository root using the same settings. The sample `inputs` and `reports` locations are neutral paths
prepared by the reviewer and do not expose a publisher's machine layout:

```powershell
$env:PYTHONPATH = "src"

python scripts\skyrim_strings_overlap_audit\compare_strings_similarity_en.py `
  ".\inputs\nexus-134478-chongguang-ank" `
  ".\inputs\nexus-139134-with-light" `
  ".\reports\chongguang-ank-vs-with-light-baseline" `
  --left-report-source "https://www.nexusmods.com/skyrimspecialedition/mods/134478" `
  --right-report-source "https://www.nexusmods.com/skyrimspecialedition/mods/139134"

python scripts\skyrim_strings_overlap_audit\compare_strings_similarity_en.py `
  ".\inputs\nexus-175184.7z" `
  ".\inputs\nexus-139134-with-light" `
  ".\reports\nexus-175184-vs-with-light" `
  --left-report-source "https://www.nexusmods.com/skyrimspecialedition/mods/175184" `
  --right-report-source "https://www.nexusmods.com/skyrimspecialedition/mods/139134"

python scripts\skyrim_strings_overlap_audit\compare_strings_similarity_en.py `
  ".\inputs\nexus-175184.7z" `
  ".\inputs\nexus-134478-chongguang-ank" `
  ".\reports\nexus-175184-vs-chongguang-ank-control" `
  --left-report-source "https://www.nexusmods.com/skyrimspecialedition/mods/175184" `
  --right-report-source "https://www.nexusmods.com/skyrimspecialedition/mods/134478"
```

Each output directory contains `summary.md`, `summary.json`, `by_file.csv`, and `details.csv`. The row-level file
preserves the logical filename, String ID, both source texts, and the similarity score for manual review.

## Preservation guidance

- Retain the original downloaded archive or source directory for each translation set without modifying it.
- Retain all three complete report directories and record the run date, script version, and input provenance.
- Present the negative control, investigated pair, and second control together rather than citing one percentage.
- Preserve publication dates, version histories, page descriptions, authorization records, and statements from the
  authors as separate evidence.
