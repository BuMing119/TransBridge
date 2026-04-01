# parser/xt_parser.py
from collections.abc import Callable, Iterator
import csv
from dataclasses import asdict, dataclass
import json
import xml.etree.ElementTree as ET
from pathlib import Path


@dataclass(frozen=True)
class XT_Entry:
    """对应 <Content><String>...</String> 的一条记录。"""

    list_id: int | None
    edid: str
    rec: str
    source: str
    dest: str
    index: int = 1

    @staticmethod
    def _to_int(value: str | None) -> int | None:
        if value is None:
            return None
        value = value.strip()
        if value == "":
            return None
        try:
            return int(value)
        except ValueError:
            return None


class XT_XmlParser:
    """
    解析固定结构的 XML：

    <SSTXMLRessources>
      <Params>...</Params>
      <Content>
        <String List="0">
          <EDID>...</EDID>
          <REC>...</REC>
          <Source>...</Source>
          <Dest>...</Dest>
        </String>
        ...
      </Content>
    </SSTXMLRessources>
    """

    def __init__(self, params: dict[str, str], entries: list[XT_Entry]):
        self.params = params
        self.entries = entries
        self._index_by_edid: dict[str, list[XT_Entry]] = {}
        for e in entries:
            self._index_by_edid.setdefault(e.edid, []).append(e)

    # ---------- 工厂方法 ----------
    @classmethod
    def from_file(cls, xml_path: str) -> "XT_XmlParser":
        """
        解析 XML 并保存 tree 供 writer 使用。
        """
        xml_path = Path(xml_path)
        tree = ET.parse(xml_path)
        root = tree.getroot()

        params = {}
        params_node = root.find("Params")
        if params_node is not None:
            for child in list(params_node):
                params[child.tag] = (child.text or "").strip()

        entries = []
        for e in root.findall(".//Content/String"):
            list_attr = e.attrib.get("List")
            list_id = XT_Entry._to_int(list_attr)

            def text_of(tag: str) -> str:
                node = e.find(tag)
                return (node.text or "").strip() if node is not None else ""

            # 获取REC节点的id属性（XT XML 中 index 从 0 开始，内部统一从 1 开始）
            rec_node = e.find("REC")
            rec_index = 1  # 默认值为1
            if rec_node is not None and "id" in rec_node.attrib:
                rec_index = (XT_Entry._to_int(rec_node.attrib.get("id")) or 0) + 1

            entries.append(
                XT_Entry(
                    list_id=list_id,
                    edid=text_of("EDID"),
                    rec=text_of("REC"),
                    source=text_of("Source"),
                    dest=text_of("Dest"),
                    index=rec_index,
                )
            )

        parser = cls(params=params, entries=entries)
        parser._xml_path = xml_path
        parser._tree = tree
        return parser

    # ---------- 解析 Params ----------
    @staticmethod
    def _parse_params(xml_path: str) -> dict[str, str]:
        """
        小而稳定：直接 parse 一次拿 Params。
        如果你非常在意性能，也可以改成 iterparse 一次完成。
        """
        tree = ET.parse(xml_path)
        root = tree.getroot()
        params_node = root.find("Params")
        params: dict[str, str] = {}
        if params_node is None:
            return params
        for child in list(params_node):
            # 例如 <Addon>...</Addon> -> {"Addon": "..."}
            params[child.tag] = (child.text or "").strip()
        return params

    # ---------- 流式迭代 Content/String ----------
    @staticmethod
    def _iter_entries(xml_path: str) -> Iterator[XT_Entry]:
        """
        流式解析所有 <String> 节点，避免一次性加载全部节点树。
        """
        # 只监听 end 事件，拿到完整节点后再读取内容
        context = ET.iterparse(xml_path, events=("end",))
        for event, elem in context:
            if elem.tag != "String":
                continue

            list_attr = elem.attrib.get("List")
            list_id = XT_Entry._to_int(list_attr)

            def text_of(tag: str) -> str:
                node = elem.find(tag)
                return (node.text or "").strip() if node is not None else ""

            # 获取REC节点的id属性（XT XML 中 index 从 0 开始，内部统一从 1 开始）
            rec_node = elem.find("REC")
            rec_index = 1  # 默认值为1
            if rec_node is not None and "id" in rec_node.attrib:
                rec_index = (XT_Entry._to_int(rec_node.attrib.get("id")) or 0) + 1

            entry = XT_Entry(
                list_id=list_id,
                edid=text_of("EDID"),
                rec=text_of("REC"),
                source=text_of("Source"),
                dest=text_of("Dest"),
                index=rec_index,
            )

            yield entry

            # 清理已处理节点，降低内存占用
            elem.clear()

    # ---------- 查询 ----------
    def get_by_edid(self, edid: str) -> list[XT_Entry]:
        """按 EDID 精确匹配（可能有重复 EDID，因此返回 list）。"""
        return list(self._index_by_edid.get(edid, []))

    def find(self, predicate: Callable[[XT_Entry], bool]) -> list[XT_Entry]:
        """按自定义条件过滤。"""
        return [e for e in self.entries if predicate(e)]

    def iter(self) -> Iterator[XT_Entry]:
        """迭代所有条目。"""
        return iter(self.entries)

    # ---------- 导出 ----------
    def to_json(self, ensure_ascii: bool = False, indent: int = 2) -> str:
        data = {
            "params": self.params,
            "entries": [asdict(e) for e in self.entries],
        }
        return json.dumps(data, ensure_ascii=ensure_ascii, indent=indent)

    def to_json_file(self, path: str, ensure_ascii: bool = False, indent: int = 2) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json(ensure_ascii=ensure_ascii, indent=indent))

    def to_csv_file(self, path: str) -> None:
        fieldnames = ["list_id", "edid", "rec", "source", "dest", "index"]
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for e in self.entries:
                writer.writerow(asdict(e))
