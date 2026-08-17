"""FOMOD 流水线编排：解包→diff→逐插件[键对齐→词典兜底→AI翻译→写回]→界面文本翻译→组装→打包。

纯 Python，无 PyQt 依赖（ADR-008）。翻译来源按 ADR-014 优先级：
① 旧归档（键对齐）→ ② 词典（文本/键兜底）→ ③ AI 翻译（AutoTranslator）。
运行时上下文（llm_config / tm_manager）由调用方（GUI）注入。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PipelineResult:
    extracted_count: int = 0
    diff: dict = field(default_factory=dict)
    inherited: int = 0            # 键对齐继承
    needs_review: list = field(default_factory=list)
    dict_applied: int = 0         # 词典套用命中
    ai_translated: int = 0        # AI 翻译条数
    plugins_processed: int = 0    # 处理的插件数
    kept_count: int = 0
    stripped_count: int = 0
    archive_path: str = ""

    def to_dict(self) -> dict:
        return {
            "extracted_count": self.extracted_count,
            "diff": self.diff,
            "inherited": self.inherited,
            "needs_review": self.needs_review,
            "dict_applied": self.dict_applied,
            "ai_translated": self.ai_translated,
            "plugins_processed": self.plugins_processed,
            "kept_count": self.kept_count,
            "stripped_count": self.stripped_count,
            "archive_path": self.archive_path,
        }


_PLUGIN_EXTS = {".esp", ".esm", ".esl"}


class FomodPipeline:
    """FOMOD 翻译流水线编排器。"""

    def __init__(self, rules=None, llm_config=None, tm_manager=None):
        self._rules = rules
        self._llm_config = llm_config
        self._tm_manager = tm_manager

    def run(self, new_archive: str, output_archive: str, *,
            old_archive: str | None = None,
            work_dir: str | None = None,
            fmt: str = "zip",
            target_lang: str = "zh_CN",
            ai_enabled: bool = True,
            progress_callback=None,
            stop_event: threading.Event | None = None) -> PipelineResult:
        """执行完整流水线，返回 PipelineResult。"""
        import tempfile
        from src.transbridge.fileops import extract, pack, diff_directories, FilterRules
        from src.transbridge.fomod.builder import assemble_output

        result = PipelineResult()
        stop = stop_event or threading.Event()
        base = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="tb_fomod_"))
        new_dir = base / "new"
        old_dir = base / "old"

        # 1. 解包
        extract(new_archive, str(new_dir))
        if old_archive:
            extract(old_archive, str(old_dir))

        # 2. diff
        if old_archive:
            rules_obj = self._rules or FilterRules()
            diff = diff_directories(str(old_dir), str(new_dir), skip_hash_exts=rules_obj.strip_exts)
            result.diff = diff.to_dict()

        # 3. 逐插件翻译：键对齐 → 词典兜底 → AI 翻译 → 写回
        if not stop.is_set():
            self._translate_plugins(new_dir, old_dir if old_archive else None, result, progress_callback, stop, ai_enabled)

        # 4. 界面文本翻译（ModuleConfig.xml）
        if not stop.is_set() and self._llm_config is not None and ai_enabled:
            self._translate_fomod_xml(new_dir, old_dir if old_archive else None, target_lang)

        # 5. 组装
        out_dir = base / "out"
        rules_obj = self._rules or FilterRules()
        ar = assemble_output(str(new_dir), str(out_dir), rules_obj)
        result.kept_count = ar["kept_count"]
        result.stripped_count = ar["stripped_count"]

        # 6. 打包
        result.archive_path = pack(str(out_dir), output_archive, fmt=fmt)
        return result

    # ── 逐插件翻译 ───────────────────────────────────────────────

    def _translate_plugins(self, new_dir: Path, old_dir, result, progress_callback, stop_event, ai_enabled=True):
        from src.transbridge.parser.plugin_parser import PluginParser
        from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
        from src.transbridge.migrator import migrate
        from src.transbridge.translation_memory import TranslationMemoryManager

        parser = PluginParser()
        tm = self._tm_manager or TranslationMemoryManager()
        if self._tm_manager is None:
            tm.load()

        plugins = [p for p in new_dir.rglob("*") if p.suffix.lower() in _PLUGIN_EXTS]
        for i, esp_path in enumerate(plugins):
            if stop_event.is_set():
                break
            rel = esp_path.relative_to(new_dir)
            # 解析新版
            new_entries = parser.parse_plugin(esp_path)
            new_collection = TranslationEntryCollection(new_entries)

            # 键对齐迁移（旧版同款插件）
            if old_dir is not None:
                old_esp = old_dir / rel
                if old_esp.exists():
                    old_entries = parser.parse_plugin(old_esp)
                    old_collection = TranslationEntryCollection(old_entries)
                    mr = migrate(old_collection, new_collection)
                    result.inherited += mr.inherited
                    result.needs_review.extend(mr.needs_review)

            # 词典兜底
            ar = tm.apply_to_collection(new_collection)
            result.dict_applied += ar.applied

            # AI 翻译剩余未翻译（ai_enabled=False 时跳过，剩余保留待翻译）
            if ai_enabled:
                ai_count = self._ai_translate(new_collection, esp_path, stop_event)
                result.ai_translated += ai_count

            # 写回
            self._write_back(esp_path, new_collection)
            result.plugins_processed += 1

            if progress_callback:
                progress_callback(i + 1, len(plugins), f"已处理 {rel}")

    def _ai_translate(self, collection, esp_path, stop_event) -> int:
        """用 AutoTranslator 翻译 stage=0 且无译文的条目，返回翻译条数。"""
        if self._llm_config is None:
            return 0
        untranslated = [e.key for e in collection if e.stage == 0 and not e.translation]
        if not untranslated:
            return 0
        try:
            from src.transbridge.ai_translator.translator import AutoTranslator, TranslatorConfig
            cfg = TranslatorConfig(llm_config=self._llm_config, esp_path=str(esp_path))
            translator = AutoTranslator(cfg)
            tr = translator.translate(
                collection, untranslated,
                progress_callback=lambda *a: None,
                stop_event=stop_event,
            )
            return tr.success_count
        except Exception:
            return 0

    def _write_back(self, esp_path, collection):
        """用 PluginWriter 把译文写回插件文件。"""
        try:
            from src.transbridge.parser.strings_file import PluginStringsLookup
            from src.transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext
            from src.transbridge.writer.plugin_writer import PluginWriter
            plugin = SSEPluginWithContext.from_file(esp_path)
            lookup = PluginStringsLookup.from_plugin(esp_path, language="english")
            writer = PluginWriter(plugin, strings_lookup=lookup, language="english")
            writer.apply_collection(collection)
            writer.write(esp_path)
        except Exception:
            # 写回失败不阻断流水线（组装阶段仍可产出未写回的副本）
            pass

    # ── 界面文本翻译 ─────────────────────────────────────────────

    def _translate_fomod_xml(self, new_dir: Path, old_dir, target_lang: str = "zh_CN"):
        from src.transbridge.infra.llm_client import create_llm_client
        from src.transbridge.fomod.fomod_xml import read_fomod_xml, write_fomod_xml, translate_module_config

        xml_path = new_dir / "fomod" / "ModuleConfig.xml"
        if not xml_path.exists():
            return
        old_xml = None
        if old_dir is not None and (old_dir / "fomod" / "ModuleConfig.xml").exists():
            old_xml = read_fomod_xml(str(old_dir / "fomod" / "ModuleConfig.xml"))
        new_xml = read_fomod_xml(str(xml_path))
        llm = create_llm_client(self._llm_config)
        out = translate_module_config(new_xml, old_xml, llm, target_lang=target_lang)
        write_fomod_xml(str(xml_path), out)