"""FOMOD 安装界面文本（ModuleConfig.xml / info.xml）解析与翻译。

处理 UTF-16LE 编码与 BOM（fomod 元数据约定 UTF-16），
按名称层级键（moduleName/installStep/group/plugin/description）与旧版对齐复用译文，
新增/变化文本走 LLM 翻译。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET


_BOM_UTF16_LE = bytes([0xFF, 0xFE])
_BOM_UTF16_BE = bytes([0xFE, 0xFF])
_BOM_UTF8 = bytes([0xEF, 0xBB, 0xBF])


def read_fomod_xml(path: str) -> str:
    """读取 fomod XML，显式处理 BOM 与编码（UTF-16LE / UTF-8），返回解码后的文本。"""
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(_BOM_UTF16_LE):
        return raw[2:].decode("utf-16-le")
    if raw.startswith(_BOM_UTF16_BE):
        return raw[2:].decode("utf-16-be")
    if raw.startswith(_BOM_UTF8):
        return raw[3:].decode("utf-8")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-16-le")


def write_fomod_xml(path: str, content: str) -> None:
    """写回 fomod XML 为 UTF-16LE + BOM（fomod 约定 UTF-16）。"""
    with open(path, "wb") as f:
        f.write(_BOM_UTF16_LE)
        f.write(content.encode("utf-16-le"))


def _extract_text_nodes(xml_str: str) -> list:
    """提取 XML 的可翻译文本节点，返回 [(层级键, 文本)] 列表。"""
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return []
    nodes = []

    def _walk(elem, prefix):
        tag = elem.tag.split("}")[-1]
        name = elem.get("name", "")
        key = prefix + "/" + tag + ("[name=" + name + "]" if name else "")
        text = (elem.text or "").strip()
        children = list(elem)
        if text and not children:
            nodes.append((key, text))
        for child in children:
            _walk(child, key)

    _walk(root, "")
    return nodes


def _build_translation_prompt(text: str, target_lang: str = "zh_CN") -> list:
    """构造 fomod 界面文本的翻译指令（短文本）。"""
    lang_names = {"zh_CN": "中文", "en": "英文", "zh_TW": "繁体中文", "ja": "日文"}
    lang = lang_names.get(target_lang, target_lang)
    prompt = (f"你是 Skyrim Mod 本地化翻译助手。请把下面的 FOMOD 安装界面文本翻译成{lang}，"
              "只输出译文，不要解释、不要引号、不要额外文字：")
    return [{"role": "user", "content": prompt + "\n\n" + text}]


def translate_module_config(new_xml: str, old_xml, llm, target_lang: str = "zh_CN") -> str:
    """对 ModuleConfig.xml 做层级键对齐 + AI 翻译。

    old_xml 非 None 时，同名层级键复用旧译；新增/变化文本走 llm.chat 翻译。
    返回翻译后的 XML 字符串。llm 需提供 chat(messages) -> str。
    """
    old_map = {}
    if old_xml:
        old_map = {k: v for k, v in _extract_text_nodes(old_xml)}

    try:
        root = ET.fromstring(new_xml)
    except ET.ParseError:
        return new_xml

    def _translate(elem, prefix):
        tag = elem.tag.split("}")[-1]
        name = elem.get("name", "")
        key = prefix + "/" + tag + ("[name=" + name + "]" if name else "")
        text = (elem.text or "").strip()
        children = list(elem)
        if text and not children:
            if key in old_map:
                elem.text = old_map[key]
            elif llm is not None:
                try:
                    elem.text = llm.chat(_build_translation_prompt(text, target_lang)).strip()
                except Exception:
                    elem.text = text
        for child in children:
            _translate(child, key)

    _translate(root, "")
    body = ET.tostring(root, encoding="unicode")
    body = re.sub(r"<\?xml[^>]*\?>\s*", "", body, count=1)
    return body