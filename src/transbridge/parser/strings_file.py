# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""
Skyrim strings file reader.

Reads .strings / .dlstrings / .ilstrings files from the Skyrim game data
directory and provides a lookup table from string ID to text.

File format (all integers little-endian):
  [Count:    UInt32]  — number of entries in directory
  [DataSize: UInt32]  — byte size of string data section
  [StringID: UInt32, Offset: UInt32] × Count  — directory
  <string data>  — Count null-terminated strings (.strings)
                   or Count length-prefixed strings (.dlstrings/.ilstrings)

Numeric string IDs can overlap across the three file types. Typed lookups
retain that scope; the merged dictionary remains a legacy read facade.
"""

import logging
from pathlib import Path

from transbridge.config.text_encoding import (
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_TEXT_ENCODING,
    XTRANSLATOR_READ_POLICY,
    TextReadOptions,
    language_from_suffix,
    normalize_text_encoding,
)
from transbridge.parser.localized_resources import (
    BASE_GAME_PLUGINS,
    LocalizedResource,
    capture_plugin_resources,
    capture_strings_directory,
)
from transbridge.parser.localized_string_records import encode_chunk, parse_records, render

log = logging.getLogger("SkyrimStringsFile")

# Canonical writer routing, also reused by typed lookups when numeric IDs overlap.
_STRINGS_SUBTYPES: frozenset[str] = frozenset({"FULL", "SHRT"})
_DLSTRINGS_SUBTYPES: frozenset[str] = frozenset({
    "DESC",
    "CNAM",
    "NNAM",
    "DNAM",
    "RNAM",
    "ONAM",
    "TNAM",
    "INAM",
    "ANAM",
    "BNAM",
    "ENAM",
    "GNAM",
    "ZNAM",
})
_ILSTRINGS_SUBTYPES: frozenset[str] = frozenset({"NAM1", "NAM2", "NAM3", "NAM4"})


class SkyrimStringsFile:
    """
    Reads a single Skyrim strings file (.strings, .dlstrings, or .ilstrings).
    """

    def __init__(self, strings: dict[int, str], source: str = "", *, fallback_count: int = 0) -> None:
        self._strings = strings
        self._source = source
        self.fallback_count = fallback_count

    @classmethod
    def from_file(
        cls,
        path: Path,
        *,
        input_encoding: str = DEFAULT_TEXT_ENCODING,
        source_language: str = DEFAULT_SOURCE_LANGUAGE,
        text_read_policy: str = XTRANSLATOR_READ_POLICY,
    ) -> "SkyrimStringsFile":
        """
        Load a strings file from disk.

        Args:
            path: Path to .strings, .dlstrings, or .ilstrings file.

        Returns:
            SkyrimStringsFile instance.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the file header is malformed.
        """
        data = path.read_bytes()
        return cls.from_bytes(
            data,
            path.suffix,
            source=str(path),
            input_encoding=input_encoding,
            source_language=source_language,
            text_read_policy=text_read_policy,
        )

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        ext: str,
        source: str = "",
        *,
        input_encoding: str = DEFAULT_TEXT_ENCODING,
        source_language: str = DEFAULT_SOURCE_LANGUAGE,
        text_read_policy: str = XTRANSLATOR_READ_POLICY,
        _legacy_linked: bool = False,
    ) -> "SkyrimStringsFile":
        """
        Parse a strings file from raw bytes (e.g. extracted from a BSA).

        Args:
            data: Raw file bytes.
            ext: File extension including dot, e.g. ``".strings"``.
            source: Optional label for log messages.
            input_encoding: Codec used to decode every string payload.

        Returns:
            SkyrimStringsFile instance.
        """
        length_prefixed = ext.lower() in {".dlstrings", ".ilstrings"}
        options = TextReadOptions(input_encoding, source_language, text_read_policy)
        try:
            records = parse_records(data, length_prefixed, read_options=options, legacy_linked=_legacy_linked)
        except ValueError as exc:
            raise ValueError(f"{source or ext}: {exc}") from exc
        strings = {record.string_id: record.text for record in records}
        log.debug(f"Parsed {len(strings)} strings from {source or ext}")
        return cls(strings, source=source, fallback_count=sum(record.used_fallback(options) for record in records))

    def get(self, string_id: int) -> str | None:
        """Return the string for the given ID, or None if not found."""
        return self._strings.get(string_id)

    def __len__(self) -> int:
        return len(self._strings)

    def __repr__(self) -> str:
        return f"SkyrimStringsFile({self._source!r}, {len(self)} entries)"


class PluginStringsLookup:
    """
    Aggregates all three strings files for a single plugin and provides
    a unified lookup interface.

    Discovery order:
    1. Loose files in ``<plugin_dir>/Strings/<stem>_<lang>.*``
    2. BSA archives in ``<plugin_dir>/`` that contain matching strings files

    Usage::

        lookup = PluginStringsLookup.from_plugin(Path("Data/Skyrim.esm"))
        if lookup:
            text = lookup.get(0x0001A3A5)
    """

    _BASE_GAME_PLUGINS = BASE_GAME_PLUGINS

    def __init__(
        self,
        merged: dict[int, str],
        resources: tuple[LocalizedResource, ...] = (),
        *,
        tables: dict[str, dict[int, str]] | None = None,
        fallback_count: int = 0,
    ) -> None:
        self._merged = merged
        self._resources = tuple(resources)
        self._tables = {} if tables is None else tables
        self.fallback_count = fallback_count
        seen, duplicates = set(), set()
        for table in self._tables.values():
            duplicates.update(seen.intersection(table))
            seen.update(table)
        self._variant_duplicates = frozenset(duplicates)

    @property
    def resources(self) -> tuple[LocalizedResource, ...]:
        """The exact original templates used to build this lookup, in type order."""
        return self._resources

    @classmethod
    def from_strings_dir(
        cls,
        strings_dir: Path,
        plugin_stem: str,
        language: str = "english",
        *,
        input_encoding: str = DEFAULT_TEXT_ENCODING,
        source_language: str | None = None,
        text_read_policy: str = XTRANSLATOR_READ_POLICY,
    ) -> "PluginStringsLookup | None":
        """
        从指定的 Strings 目录加载指定语言的 strings 文件，不依赖 plugin_path 推断目录。

        寻找 <strings_dir>/<plugin_stem>_<Language>.{strings,dlstrings,ilstrings}

        Args:
            strings_dir: 包含 strings 文件的目录（通常叫 Strings/）。
            plugin_stem: 插件名不含扩展名，如 "Skyrim" 或 "MyMod"。
            language: 语言标签，如 "chinese"、"english"（默认 "english"）。

        Returns:
            PluginStringsLookup if at least one strings file was found, otherwise None.
        """
        resources = capture_strings_directory(strings_dir, plugin_stem, language)
        if source_language is None:
            source_language = language_from_suffix(language)
        options = TextReadOptions(input_encoding, source_language, text_read_policy)
        return cls._from_resources(resources, options)

    @classmethod
    def from_plugin(
        cls,
        plugin_path: Path,
        language: str = "english",
        *,
        input_encoding: str = DEFAULT_TEXT_ENCODING,
        source_language: str | None = None,
        text_read_policy: str = XTRANSLATOR_READ_POLICY,
    ) -> "PluginStringsLookup | None":
        """
        Auto-discover and load all strings files for the given plugin.

        Searches loose files first, then falls back to BSA archives.

        Args:
            plugin_path: Path to the .esp/.esm file.
            language: Language suffix to look for (default: "english").

        Returns:
            PluginStringsLookup if at least one strings file was found,
            otherwise None.
        """
        resources = capture_plugin_resources(plugin_path, language, base_game_plugins=cls._BASE_GAME_PLUGINS)
        if source_language is None:
            source_language = language_from_suffix(language)
        options = TextReadOptions(input_encoding, source_language, text_read_policy)
        return cls._from_resources(resources, options)

    @classmethod
    def _from_resources(
        cls, resources: tuple[LocalizedResource, ...], options: TextReadOptions
    ) -> "PluginStringsLookup | None":
        merged: dict[int, str] = {}
        tables = {}
        fallback_count = 0
        for resource in resources:
            source = (
                str(resource.source_path)
                if resource.member_path is None
                else f"{resource.source_path}/{resource.member_path}"
            )
            try:
                strings = SkyrimStringsFile.from_bytes(
                    resource.content, resource.suffix, source=source, _legacy_linked=True, **options.as_options()
                )
            except ValueError as exc:
                log.warning("Failed to parse localized resource %s: %s", source, exc)
                raise
            merged.update(strings._strings)
            tables[resource.suffix] = strings._strings
            fallback_count += strings.fallback_count
            log.info("Loaded %s strings from %s", len(strings), source)
        if not merged:
            return None
        log.info("PluginStringsLookup ready: %s total strings", len(merged))
        return cls(merged, resources, tables=tables, fallback_count=fallback_count)

    def get(self, string_id: int) -> str | None:
        """
        Return the localised string for the given integer ID.

        Args:
            string_id: The UInt32 string index stored in the plugin record.

        Returns:
            The string text, or None if the ID is not present in any file.
        """
        return self._merged.get(string_id)

    def get_for_type(self, string_id: int, subrecord_type: str | None) -> str | None:
        """Resolve variant collisions through the existing subrecord routing policy.

        Unique IDs and injected legacy dictionaries retain ``get`` behavior.
        A shared ID without a type or its corresponding table is unresolved.
        """
        if string_id not in self._variant_duplicates:
            return self.get(string_id)
        if not subrecord_type:
            return None
        suffix = PluginStringsWriter._ext_for(subrecord_type)
        return self._tables.get(suffix, {}).get(string_id)

    def create_writer(self) -> "PluginStringsWriter":
        """Seed a complete write from the same decoded variant tables."""
        return PluginStringsWriter(self._tables)

    def __len__(self) -> int:
        return len(self._merged)

    def __bool__(self) -> bool:
        return bool(self._merged)

    def __repr__(self) -> str:
        return f"PluginStringsLookup({len(self)} entries)"


class SkyrimStringsWriter:
    """
    Serialises a mapping of string_id -> text into the binary strings file format.

    The format is identical to what SkyrimStringsFile reads:
      [Count: UInt32][DataSize: UInt32]          ← 8-byte header
      [StringID: UInt32, Offset: UInt32] × Count ← directory
      <string data>                               ← null-terminated (.strings)
                                                    or length-prefixed (.dlstrings/.ilstrings)
    """

    @staticmethod
    def to_bytes(
        strings: dict[int, str], length_prefixed: bool, *, output_encoding: str = DEFAULT_TEXT_ENCODING
    ) -> bytes:
        """
        Serialise *strings* into a strings-file binary blob.

        Args:
            strings: Mapping of string_id -> text.
            length_prefixed: True for .dlstrings / .ilstrings, False for .strings.
            output_encoding: Codec used to encode every string payload.

        Returns:
            Raw bytes ready to be written to disk.
        """
        output_encoding = normalize_text_encoding(output_encoding)
        chunks = []
        for string_id, text in strings.items():
            try:
                chunks.append((string_id, encode_chunk(text, output_encoding, length_prefixed)))
            except ValueError as exc:
                raise ValueError(f"String ID {string_id}: {exc}") from exc
        return render(chunks)


class PluginStringsWriter:
    """
    Collects translated strings (with subrecord-type routing) and writes them
    out as the three strings files a localised plugin expects.

    Usage::

        writer = PluginStringsWriter()
        writer.add(0x00012345, "你好，世界", "FULL")     # → Plugin_English.strings
        writer.add(0x00012346, "这是描述文本", "DESC")   # → Plugin_English.dlstrings
        writer.add(0x00012347, "对话文本", "NAM1")       # → Plugin_English.ilstrings
        paths = writer.write(Path("Data"), "MyMod")
    """

    def __init__(self, tables: dict[str, dict[int, str]] | None = None) -> None:
        self._by_ext: dict[str, dict[int, str]] = {
            ".strings": {},
            ".dlstrings": {},
            ".ilstrings": {},
        }
        self._source_tables = {} if tables is None else tables
        self._by_ext.update((ext, contents.copy()) for ext, contents in self._source_tables.items())

    @staticmethod
    def _ext_for(subrecord_type: str) -> str:
        """Return the file extension for a given subrecord type."""
        if subrecord_type in _STRINGS_SUBTYPES:
            return ".strings"
        if subrecord_type in _ILSTRINGS_SUBTYPES:
            return ".ilstrings"
        return ".dlstrings"

    def add(self, string_id: int, text: str, subrecord_type: str) -> None:
        """
        Register a translated string.

        Args:
            string_id: The UInt32 string index stored in the plugin record.
            text: Translated text.
            subrecord_type: Subrecord type (e.g. "FULL", "DESC", "NAM1") used
                to determine which strings file the entry belongs to.
        """
        ext = PluginStringsWriter._ext_for(subrecord_type)
        containing = [suffix for suffix, values in self._source_tables.items() if string_id in values]
        if len(containing) == 1:
            ext = containing[0]
        self._by_ext[ext][string_id] = text

    def write(
        self,
        output_dir: Path,
        plugin_stem: str,
        language: str = "english",
        *,
        output_encoding: str = DEFAULT_TEXT_ENCODING,
    ) -> list[Path]:
        """
        Write all three strings files (only those with content are created).

        Files are written to ``output_dir/Strings/<plugin_stem>_<Language>.*``.

        Args:
            output_dir: Parent directory (usually the game's ``Data`` folder or
                the folder that contains the translated ``.esp``).
            plugin_stem: Plugin filename without extension (e.g. "Skyrim").
            language: Language label used in the filename (default: "english").

        Returns:
            List of paths that were actually written.
        """
        prefix = f"{plugin_stem}_{language.capitalize()}"
        # Encode every table before creating any output files.
        payloads = {
            ext: SkyrimStringsWriter.to_bytes(strings, ext != ".strings", output_encoding=output_encoding)
            for ext, strings in self._by_ext.items()
        }
        strings_dir = output_dir / "Strings"
        strings_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for ext, data in payloads.items():
            strings = self._by_ext[ext]
            path = strings_dir / f"{prefix}{ext}"
            path.write_bytes(data)
            if strings:
                log.info(f"Wrote {len(strings)} strings to {path.name}")
            else:
                log.info(f"Wrote empty strings file: {path.name}")
            written.append(path)

        return written

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_ext.values())

    def __bool__(self) -> bool:
        return any(self._by_ext.values())
