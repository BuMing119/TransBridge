from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET


def _text_or_empty(elem: ET.Element | None) -> str:
    """取元素文本；若为空/自闭合则返回空串；保留换行等原样。"""
    if elem is None:
        return ""
    # ElementTree 对于 <TAG /> 会得到 elem.text is None
    return elem.text if elem.text is not None else ""


def _int_or_none(s: str) -> int | None:
    s = s.strip()
    if s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


@dataclass(frozen=True)
class EET_Entry:
    """对应 <ESP> 节点的一条记录。"""

    grup: str
    id: str
    edid: str
    champ: str
    original: str
    traduit: str
    perso: str
    index: int | None
    status: int | None
    idstexte: int | None
    commentaire: str
    icon: int | None

    @property
    def key(self) -> tuple[str, str, str, str]:
        """常用的唯一键：GRUP + ID + EDID + CHAMP（可按需调整）。"""
        return (self.grup, self.id, self.edid, self.champ)

    @classmethod
    def from_xml(cls, esp_elem: ET.Element) -> "EET_Entry":
        def get(tag: str) -> str:
            return _text_or_empty(esp_elem.find(tag))

        return cls(
            grup=get("GRUP").strip(),
            id=get("ID").strip(),
            edid=get("EDID").strip(),
            champ=get("CHAMP").strip(),
            original=get("ORIGINAL"),
            traduit=get("TRADUIT"),
            perso=get("PERSO"),
            index=_int_or_none(get("INDEX")),
            status=_int_or_none(get("STATUS")),
            idstexte=_int_or_none(get("IDSTEXTE")),
            commentaire=get("COMMENTAIRE"),
            icon=_int_or_none(get("ICON")),
        )


class EET_XmlParser:
    """
    解析你这种结构的 XML：
    <DocumentElement>
      <ESP>...</ESP>
      <ESP>...</ESP>
    </DocumentElement>
    """

    def __init__(self, entries: list[EET_Entry]):
        self.entries = entries

        # 建索引：便于快速按字段查
        self._by_key: dict[tuple[str, str, str, str], list[EET_Entry]] = {}
        self._by_grup: dict[str, list[EET_Entry]] = {}
        self._by_id: dict[str, list[EET_Entry]] = {}
        self._by_edid: dict[str, list[EET_Entry]] = {}

        for e in entries:
            self._by_key.setdefault(e.key, []).append(e)
            self._by_grup.setdefault(e.grup, []).append(e)
            self._by_id.setdefault(e.id, []).append(e)
            if e.edid:
                self._by_edid.setdefault(e.edid, []).append(e)

    # ----------- 构造入口 -----------
    @classmethod
    def from_file(cls, path: str | Path, encoding: str | None = None) -> "EET_XmlParser":
        """
        从文件解析。通常不需要传 encoding；ElementTree 会读 XML 头里的 encoding。
        """
        path = Path(path)
        if encoding:
            text = path.read_text(encoding=encoding)
            return cls.from_string(text)
        tree = ET.parse(path)
        return cls._from_root(tree.getroot())

    @classmethod
    def from_string(cls, xml_text: str) -> "EET_XmlParser":
        root = ET.fromstring(xml_text)
        return cls._from_root(root)

    @classmethod
    def _from_root(cls, root: ET.Element) -> "EET_XmlParser":
        # 允许 root 不是 DocumentElement，但里面有 ESP 也行
        entries = [EET_Entry.from_xml(e) for e in root.findall(".//ESP")]
        return cls(entries)

    # ----------- 常用查询 -----------
    def __iter__(self) -> Iterator[EET_Entry]:
        return iter(self.entries)

    def find(
        self,
        *,
        grup: str | None = None,
        id: str | None = None,
        edid: str | None = None,
        champ: str | None = None,
        original_contains: str | None = None,
        traduit_contains: str | None = None,
        status: int | None = None,
    ) -> list[EET_Entry]:
        """
        通用过滤查询（简单好用，但不是最极致性能）。
        """
        res: Iterable[EET_Entry] = self.entries

        if grup is not None:
            res = (e for e in res if e.grup == grup)
        if id is not None:
            res = (e for e in res if e.id == id)
        if edid is not None:
            res = (e for e in res if e.edid == edid)
        if champ is not None:
            res = (e for e in res if e.champ == champ)
        if status is not None:
            res = (e for e in res if e.status == status)

        if original_contains is not None:
            res = (e for e in res if original_contains in e.original)
        if traduit_contains is not None:
            res = (e for e in res if traduit_contains in e.traduit)

        return list(res)

    def get_by_key(self, grup: str, id: str, edid: str, champ: str) -> list[EET_Entry]:
        return list(self._by_key.get((grup, id, edid, champ), []))

    def get_by_grup(self, grup: str) -> list[EET_Entry]:
        return list(self._by_grup.get(grup, []))

    def get_by_id(self, id: str) -> list[EET_Entry]:
        return list(self._by_id.get(id, []))

    def get_by_edid(self, edid: str) -> list[EET_Entry]:
        return list(self._by_edid.get(edid, []))

    # ----------- 方便导出 -----------
    def to_dicts(self) -> list[dict]:
        """转成 dict 列表（方便 json / pandas）。"""
        out = []
        for e in self.entries:
            out.append(
                dict(
                    GRUP=e.grup,
                    ID=e.id,
                    EDID=e.edid,
                    CHAMP=e.champ,
                    ORIGINAL=e.original,
                    TRADUIT=e.traduit,
                    PERSO=e.perso,
                    INDEX=e.index,
                    STATUS=e.status,
                    IDSTEXTE=e.idstexte,
                    COMMENTAIRE=e.commentaire,
                    ICON=e.icon,
                )
            )
        return out
