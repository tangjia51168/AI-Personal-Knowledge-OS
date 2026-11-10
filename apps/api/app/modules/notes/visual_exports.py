"""Bounded native SVG and OOXML renderers; no execution of source markup."""

import io
import json
import unicodedata
import zipfile
from typing import Any
from xml.etree import ElementTree as ET

from fastapi import HTTPException

from app.modules.notes.content import SECTION_LABELS

MAX_BYTES = 1_048_576
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
SVG = "http://www.w3.org/2000/svg"
for prefix, namespace in (("p", P), ("a", A), ("r", R)):
    ET.register_namespace(prefix, namespace)


def child(parent: ET.Element, ns: str, tag: str, **attrs: str) -> ET.Element:
    return ET.SubElement(parent, f"{{{ns}}}{tag}", attrs)


def encoded(element: ET.Element) -> bytes:
    # XML 1.0 forbids control/surrogate characters even when text is escaped.
    # Reject rather than emit a corrupt SVG/PPTX or silently lose source text.
    for node in element.iter():
        for value in (node.text, node.tail, *node.attrib.values()):
            if value is not None and any(
                not (
                    char in "\t\n\r"
                    or 0x20 <= ord(char) <= 0xD7FF
                    or 0xE000 <= ord(char) <= 0xFFFD
                    or 0x10000 <= ord(char) <= 0x10FFFF
                )
                for char in value
            ):
                raise HTTPException(409, detail={"code": "NOTE_EXPORT_TEXT_INVALID"})
    return ET.tostring(element, encoding="utf-8", xml_declaration=True)


def bounded(content: bytes) -> bytes:
    if len(content) > MAX_BYTES:
        raise HTTPException(413, detail={"code": "NOTE_EXPORT_LIMIT"})
    return content


def wrap_text(value: str, width: int = 70) -> list[str]:
    """Conservative East-Asian display width, preserving all characters and newlines."""
    lines: list[str] = []
    for paragraph in value.split("\n"):
        line, size = "", 0
        for char in paragraph:
            units = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
            if size + units > width and line:
                lines.append(line)
                line, size = "", 0
            line += char
            size += units
        lines.append(line)
    return lines


def sections(data: dict[str, Any]) -> list[tuple[str, str]]:
    revision, document = data["revision"], data["revision"]["document"]

    def item_text(item: dict[str, Any]) -> str:
        return item["text"] + " " + " ".join(f"[{key}]" for key in item["evidence_keys"])

    result = [
        ("导出范围", "仅指定修订；不是完整备份。不访问原链接，不调用模型。引用不代表事实核验。"),
        ("概述", item_text(document["overview"])),
    ]
    for section in document["sections"]:
        result.append(
            (SECTION_LABELS[section["kind"]], "\n".join(map(item_text, section["items"])))
        )
    result.append(
        (
            "限制与证据范围",
            "\n".join(document["limitations"])
            + "\n"
            + json.dumps(document["coverage"], ensure_ascii=False, sort_keys=True),
        )
    )
    if revision["origin"] == "user_edit":
        result.append(
            (
                "个人理解（未经核验）",
                document["edit_disclaimer"] + "\n" + document["personal_notes"],
            )
        )
    result.append(
        (
            "修订与来源",
            json.dumps(
                {
                    "note_id": data["note_id"],
                    "revision_id": revision["id"],
                    "version": revision["version"],
                    "created_by": revision["created_by"],
                    "created_at": revision["created_at"],
                    "source": data["source"],
                    "snapshot": data["snapshot"],
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    )
    result.extend((f"原文证据 {item['key']}", item["quote"]) for item in data["evidence"])
    return result


def svg_text(parent: ET.Element, x: int, y: int, value: str, size: int = 20) -> None:
    node = child(parent, SVG, "text", x=str(x), y=str(y), fill="#172033")
    node.set("font-family", "sans-serif")
    node.set("font-size", str(size))
    node.text = value


def render_image_svg(data: dict[str, Any]) -> bytes:
    rows: list[tuple[str, bool]] = [
        (line, True) for line in wrap_text(data["revision"]["title"], 65)
    ]
    for heading_text, text in sections(data):
        rows.extend([(heading_text, True), *[(line, False) for line in wrap_text(text, 90)]])
    height = 80 + len(rows) * 32
    if height > 60_000:
        raise HTTPException(413, detail={"code": "NOTE_EXPORT_LIMIT"})
    root = ET.Element(
        f"{{{SVG}}}svg", {"width": "1200", "height": str(height), "viewBox": f"0 0 1200 {height}"}
    )
    child(root, SVG, "rect", width="100%", height="100%", fill="#ffffff")
    for index, (line, heading) in enumerate(rows):
        if heading:
            child(
                root,
                SVG,
                "rect",
                x="30",
                y=str(30 + index * 32),
                width="1140",
                height="30",
                rx="6",
                fill="#eef3ff",
            )
        svg_text(root, 48, 52 + index * 32, line, 22 if heading else 19)
    return bounded(encoded(root))


def render_flowchart_svg(data: dict[str, Any]) -> bytes:
    # Only the explicit steps section defines order. Never infer causal edges
    # from arbitrary claims, numbers, evidence, or the overview.
    items = [
        item
        for section in data["revision"]["document"]["sections"]
        if section["kind"] == "steps"
        for item in section["items"]
    ]
    rows = [
        wrap_text(
            f"{i}. {item['text']} " + " ".join(f"[{key}]" for key in item["evidence_keys"]), 76
        )
        for i, item in enumerate(items, 1)
    ]
    height = 160 + sum(len(lines) * 28 + 65 for lines in rows)
    root = ET.Element(
        f"{{{SVG}}}svg", {"width": "1100", "height": str(height), "viewBox": f"0 0 1100 {height}"}
    )
    child(root, SVG, "rect", width="100%", height="100%", fill="#ffffff")
    svg_text(root, 40, 36, "流程图：仅复现原笔记步骤顺序；不推断因果、分支或新增步骤。", 21)
    svg_text(root, 40, 68, "修订：" + data["revision"]["id"], 16)
    if not rows:
        svg_text(root, 40, 120, "本修订没有明确步骤，未生成流程节点。")
    y = 105
    for lines in rows:
        box_height = len(lines) * 28 + 24
        child(
            root,
            SVG,
            "rect",
            x="70",
            y=str(y),
            width="960",
            height=str(box_height),
            rx="10",
            fill="#eef3ff",
            stroke="#526581",
        )
        for index, line in enumerate(lines):
            svg_text(root, 92, y + 30 + index * 28, line)
        y += box_height
        if lines is not rows[-1]:
            child(
                root,
                SVG,
                "path",
                d=f"M550 {y + 2} v30 m-6 -6 l6 6 l6 -6",
                stroke="#526581",
                fill="none",
            )
        y += 41
    child(root, SVG, "desc").text = json.dumps(
        {"source": data["source"], "snapshot": data["snapshot"], "evidence": data["evidence"]},
        ensure_ascii=False,
    )
    return bounded(encoded(root))


def sp_tree(parent: ET.Element) -> ET.Element:
    tree = child(parent, P, "spTree")
    nv = child(tree, P, "nvGrpSpPr")
    child(nv, P, "cNvPr", id="1", name="")
    child(nv, P, "cNvGrpSpPr")
    child(nv, P, "nvPr")
    group = child(tree, P, "grpSpPr")
    xfrm = child(group, A, "xfrm")
    for name, attrs in (
        ("off", {"x": "0", "y": "0"}),
        ("ext", {"cx": "0", "cy": "0"}),
        ("chOff", {"x": "0", "y": "0"}),
        ("chExt", {"cx": "0", "cy": "0"}),
    ):
        child(xfrm, A, name, **attrs)
    return tree


def relationships(entries: list[tuple[str, str, str]]) -> bytes:
    root = ET.Element(f"{{{REL}}}Relationships")
    for rid, kind, target in entries:
        child(root, REL, "Relationship", Id=rid, Type=R + "/" + kind, Target=target)
    return encoded(root)


def slide_xml(title: str, lines: list[str]) -> bytes:
    root = ET.Element(f"{{{P}}}sld")
    tree = sp_tree(child(root, P, "cSld"))
    shape = child(tree, P, "sp")
    nv = child(shape, P, "nvSpPr")
    child(nv, P, "cNvPr", id="2", name="Knowledge text")
    child(nv, P, "cNvSpPr", txBox="1")
    child(nv, P, "nvPr")
    xfrm = child(child(shape, P, "spPr"), A, "xfrm")
    child(xfrm, A, "off", x="500000", y="300000")
    child(xfrm, A, "ext", cx="11192000", cy="6258000")
    body = child(shape, P, "txBody")
    child(body, A, "bodyPr", wrap="square")
    child(body, A, "lstStyle")
    for index, value in enumerate([title, *lines]):
        para = child(body, A, "p")
        run = child(para, A, "r")
        child(run, A, "rPr", lang="zh-CN", sz="2600" if index == 0 else "1800")
        child(run, A, "t").text = value
    color = child(root, P, "clrMapOvr")
    child(color, A, "masterClrMapping")
    return encoded(root)


def render_pptx(data: dict[str, Any]) -> bytes:
    slides: list[tuple[str, list[str]]] = []
    title_lines = wrap_text(data["revision"]["title"], 65)
    slides.append(("知识笔记 / 指定修订", title_lines))
    for heading, text in sections(data):
        lines = wrap_text(text, 86)
        for offset in range(0, len(lines), 14):
            slides.append(
                (
                    heading + (f"（续 {offset // 14 + 1}）" if offset else ""),
                    lines[offset : offset + 14],
                )
            )
    if len(slides) > 200:
        raise HTTPException(413, detail={"code": "NOTE_EXPORT_LIMIT"})
    parts: dict[str, bytes] = {}
    presentation = ET.Element(f"{{{P}}}presentation")
    master_id = child(child(presentation, P, "sldMasterIdLst"), P, "sldMasterId", id="2147483648")
    master_id.set(f"{{{R}}}id", "rId1")
    slide_list = child(presentation, P, "sldIdLst")
    for index in range(1, len(slides) + 1):
        node = child(slide_list, P, "sldId", id=str(255 + index))
        node.set(f"{{{R}}}id", f"rId{index + 1}")
    child(presentation, P, "sldSz", cx="12192000", cy="6858000")
    child(presentation, P, "notesSz", cx="6858000", cy="9144000")
    parts["ppt/presentation.xml"] = encoded(presentation)
    parts["_rels/.rels"] = relationships([("rId1", "officeDocument", "ppt/presentation.xml")])
    parts["ppt/_rels/presentation.xml.rels"] = relationships(
        [
            ("rId1", "slideMaster", "slideMasters/slideMaster1.xml"),
            *[(f"rId{i + 1}", "slide", f"slides/slide{i}.xml") for i in range(1, len(slides) + 1)],
        ]
    )
    master = ET.Element(f"{{{P}}}sldMaster")
    sp_tree(child(master, P, "cSld"))
    child(
        master,
        P,
        "clrMap",
        bg1="lt1",
        tx1="dk1",
        bg2="lt2",
        tx2="dk2",
        accent1="accent1",
        accent2="accent2",
        accent3="accent3",
        accent4="accent4",
        accent5="accent5",
        accent6="accent6",
        hlink="hlink",
        folHlink="folHlink",
    )
    layout_id = child(child(master, P, "sldLayoutIdLst"), P, "sldLayoutId", id="2147483649")
    layout_id.set(f"{{{R}}}id", "rId1")
    parts["ppt/slideMasters/slideMaster1.xml"] = encoded(master)
    parts["ppt/slideMasters/_rels/slideMaster1.xml.rels"] = relationships(
        [
            ("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
            ("rId2", "theme", "../theme/theme1.xml"),
        ]
    )
    layout = ET.Element(f"{{{P}}}sldLayout", {"type": "blank", "preserve": "1"})
    sp_tree(child(layout, P, "cSld", name="Blank"))
    child(child(layout, P, "clrMapOvr"), A, "masterClrMapping")
    parts["ppt/slideLayouts/slideLayout1.xml"] = encoded(layout)
    parts["ppt/slideLayouts/_rels/slideLayout1.xml.rels"] = relationships(
        [("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")]
    )
    theme = ET.Element(f"{{{A}}}theme", {"name": "Knowledge OS"})
    elements = child(theme, A, "themeElements")
    colors = child(elements, A, "clrScheme", name="Knowledge OS")
    for name, value in zip(
        (
            "dk1",
            "lt1",
            "dk2",
            "lt2",
            "accent1",
            "accent2",
            "accent3",
            "accent4",
            "accent5",
            "accent6",
            "hlink",
            "folHlink",
        ),
        (
            "172033",
            "FFFFFF",
            "526581",
            "EEF3FF",
            "4063D8",
            "539B81",
            "B67536",
            "8462B7",
            "3B869B",
            "8B5A72",
            "0563C1",
            "954F72",
        ),
        strict=True,
    ):
        child(child(colors, A, name), A, "srgbClr", val=value)
    fonts = child(elements, A, "fontScheme", name="Knowledge OS")
    for name in ("majorFont", "minorFont"):
        font = child(fonts, A, name)
        for kind, value in (("latin", "Arial"), ("ea", "Microsoft YaHei"), ("cs", "Arial")):
            child(font, A, kind, typeface=value)
    fmt = child(elements, A, "fmtScheme", name="Knowledge OS")
    for name in ("fillStyleLst", "bgFillStyleLst"):
        target = child(fmt, A, name)
        for _ in range(3):
            child(child(target, A, "solidFill"), A, "schemeClr", val="phClr")
    line_styles = child(fmt, A, "lnStyleLst")
    for _ in range(3):
        child(
            child(child(line_styles, A, "ln", w="9525"), A, "solidFill"),
            A,
            "schemeClr",
            val="phClr",
        )
    effects = child(fmt, A, "effectStyleLst")
    for _ in range(3):
        child(child(effects, A, "effectStyle"), A, "effectLst")
    # OOXML requires fill/line/effect/background lists in this order.
    fmt[:] = sorted(
        fmt,
        key=lambda node: ("fillStyleLst", "lnStyleLst", "effectStyleLst", "bgFillStyleLst").index(
            node.tag.split("}")[-1]
        ),
    )
    parts["ppt/theme/theme1.xml"] = encoded(theme)
    for index, (title, lines) in enumerate(slides, 1):
        parts[f"ppt/slides/slide{index}.xml"] = slide_xml(title, lines)
        parts[f"ppt/slides/_rels/slide{index}.xml.rels"] = relationships(
            [("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml")]
        )
    types = ET.Element(f"{{{CT}}}Types")
    child(
        types,
        CT,
        "Default",
        Extension="rels",
        ContentType="application/vnd.openxmlformats-package.relationships+xml",
    )
    child(types, CT, "Default", Extension="xml", ContentType="application/xml")
    for name in parts:
        if not name.endswith(".xml"):
            continue
        kind = (
            "presentation.main"
            if name == "ppt/presentation.xml"
            else "slideMaster"
            if "/slideMasters/" in name
            else "slideLayout"
            if "/slideLayouts/" in name
            else "slide"
        )
        content_type = "application/vnd.openxmlformats-officedocument." + (
            "theme+xml" if name.startswith("ppt/theme/") else "presentationml." + kind + "+xml"
        )
        child(types, CT, "Override", PartName="/" + name, ContentType=content_type)
    parts["[Content_Types].xml"] = encoded(types)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(parts.items()):
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), content)
    return bounded(output.getvalue())
