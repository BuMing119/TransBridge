# Skyrim STRINGS Literal Text Overlap Audit

[Bilingual audit conclusion](AUDIT_CONCLUSION.md) · [中文说明](README.md) ·
[English evidence chain](EVIDENCE_CHAIN.en.md) · [中文证据链](EVIDENCE_CHAIN.md)

This directory contains a one-off, reproducible audit tool for literal wording overlap between two sets of Skyrim
`.strings`, `.dlstrings`, and `.ilstrings` localization files. It reads the inputs without modifying them, aligns
records by logical filename and String ID, and deliberately avoids semantic models: equivalent meaning is not
treated as evidence of identical wording.

## Investigated publication and risk flag

- Investigated publication: [Unofficial Chinese Translation for SAE (Nexus Mods 175184)](https://www.nexusmods.com/skyrimspecialedition/mods/175184)
- Independent baseline: [重光ank (Nexus Mods 134478)](https://www.nexusmods.com/skyrimspecialedition/mods/134478)
- Comparison source: [With Light (Nexus Mods 139134)](https://www.nexusmods.com/skyrimspecialedition/mods/139134)
- Risk flag: **Suspected plagiarism or uncredited reuse; confirmation requires the authorization chain,
  publication chronology, and author statements.**

This flag is based on abnormally high, reproducible textual overlap. It is not a legal finding. Text comparison can
demonstrate substantial inheritance or a shared translation base, but similarity alone cannot establish the
direction of reuse or whether permission was granted.

## Audit results recorded on 2026-08-27

The audit selected 240 `_chinese` files from each translation set and excluded the duplicate `_english` mirrors:

- Nexus 175184 vs. With Light: among 99,010 non-empty paired texts, 59.09% were exact after normalization, 64.09%
  were exact or highly overlapping, and mean literal similarity was 75.64%. Exact matches remained 58.50% where
  both texts contained at least six characters.
- Nexus 175184 vs. 重光ank: 26.58% exact, 46.34% mean similarity, and 13.55% evidence-length exact.
- 重光ank vs. With Light: 21.66% exact, 42.10% mean similarity, and 11.20% evidence-length exact.

Across the 99,010 valid entries covered by all three sets:

- all three were identical: 17,981;
- only Nexus 175184 and With Light were identical: 40,520;
- only Nexus 175184 and 重光ank were identical: 8,335;
- only 重光ank and With Light were identical: 3,469;
- all three differed: 28,705.

For texts between 20 and 79 characters, Nexus 175184 and With Light were 60.94% exact and 74.61% exact-or-highly
overlapping. The corresponding 重光ank and With Light figures were only 7.67% and 9.49%. The Nexus 175184 and
With Light relationship therefore materially exceeds what would ordinarily be explained by translating the same
source text or by independent collisions on short terminology.

## Method

1. Accept a directory or `.7z` archive on each side.
2. Select `.strings`, `.dlstrings`, and `.ilstrings` filenames ending in `_chinese` by default.
3. Remove the language suffix, pair files by plugin name and extension, and pair records by UInt32 String ID.
4. Apply Unicode NFKC normalization and remove whitespace while preserving punctuation and textual content.
5. Calculate a multiset character n-gram Dice coefficient for contiguous literal overlap.
6. Report exact, high, medium, and low overlap separately, including evidence-length statistics.

## Run the English version

From the TransBridge repository root:

```powershell
$env:PYTHONPATH = "src"
python scripts\skyrim_strings_overlap_audit\compare_strings_similarity_en.py `
  "reference-directory-or.7z" `
  "compared-directory-or.7z" `
  "report-output-directory" `
  --left-report-source "public-reference-URL" `
  --right-report-source "public-compared-URL"
```

The two optional source arguments keep private machine paths out of a public report while the positional paths are
still used to read the local files.

The output directory contains:

- `summary.md`: human-readable English summary;
- `summary.json`: machine-readable overall and per-file statistics;
- `by_file.csv`: per-file statistics;
- `details.csv`: paired texts and scores by String ID.
CSV files use UTF-8 with a BOM for direct inspection in Excel. Run the following for all options:

```powershell
python scripts\skyrim_strings_overlap_audit\compare_strings_similarity_en.py --help
```

## Limits of interpretation

- High overlap supports further investigation of provenance, attribution, and authorization; it is not an automatic
  legal or factual determination.
- Short names, locations, and fixed terms collide naturally. Give more weight to evidence-length, 20-plus-character,
  and row-level results.
- Determining direction requires publication dates, version history, author statements, or other external evidence.
- This directory is distributed under the repository root [LICENSE](../../LICENSE).
