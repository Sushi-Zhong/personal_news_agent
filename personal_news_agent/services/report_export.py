from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile
from typing import Any
from xml.sax.saxutils import escape as xml_escape


@dataclass(frozen=True)
class ReportExport:
    content: bytes
    filename: str
    media_type: str


def export_report(report: dict[str, Any], fmt: str) -> ReportExport:
    normalized = _report_template(report)
    safe_id = _safe_filename(report.get("report_id") or "report")
    if fmt == "docx":
        return ReportExport(
            content=_docx_bytes(normalized),
            filename=f"{safe_id}.docx",
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    if fmt == "pdf":
        return ReportExport(content=_pdf_bytes(normalized), filename=f"{safe_id}.pdf", media_type="application/pdf")
    raise ValueError("format must be pdf or docx")


def _report_template(report: dict[str, Any]) -> dict[str, Any]:
    sections = report.get("sections") or {}
    topic = report.get("topic") or "专题报告"
    sources = _export_scoped_items(report, report.get("sources") or [], topic)
    timeline = _export_scoped_items(report, report.get("timeline") or sections.get("三、关键时间线") or [], topic)
    source_claims = _merge_export_items_with_sources(
        _export_scoped_items(report, sections.get("各方说法") or sections.get("六、不同来源的主要说法") or [], topic),
        sources,
    )
    source_claims = _append_missing_export_sources(source_claims, sources)
    key_evidence = _merge_export_items_with_sources(_export_scoped_items(report, sections.get("关键证据") or source_claims, topic), sources)
    key_evidence = _append_missing_export_sources(key_evidence, sources)
    importance = _readable_lines(
        sections.get("为什么重要") or sections.get("五、主要争议点/看点"),
        [
            "当前材料会影响用户对主题主体、事实状态和后续风险的判断。",
            "需要把直接证据与同类案例分开阅读，避免把来源名、年份或金额碎片误当成结论。",
        ],
    )
    watch_points = _readable_lines(
        sections.get("后续观察点") or sections.get("七、可能影响与后续观察指标"),
        [
            "继续查找原始公告、权威媒体或关键主体的正式回应。",
            "对比新增报道是否与当前对话中已展示证据一致。",
        ],
    )
    modules = [
        (
            "一句话结论",
            _summary_lines(topic, sections.get("一句话结论") or sections.get("一、结论摘要") or sections.get("summary"), key_evidence),
        ),
        (
            "覆盖范围",
            [
                f"主题：{topic}",
                f"分类：{' / '.join(report.get('category_scope') or []) or '不限'}",
                f"相关证据：{len(sources)} 条",
            ],
        ),
        ("发生了什么", _as_lines(sections.get("发生了什么") or sections.get("二、事件背景") or "对话内暂未出现足够的事件背景。")),
        ("关键证据", _dict_lines(key_evidence, "对话内暂未出现可引用的关键证据。")),
        ("各方说法", _dict_lines(source_claims, "对话内暂未出现不同主体或来源的明确说法。")),
        ("为什么重要", importance),
        ("争议与不确定性", _as_lines(sections.get("争议与不确定性") or sections.get("八、来源列表与不确定性说明") or sections.get("uncertainty") or "对话内暂未出现明确争议或不确定性说明。")),
        ("后续观察点", watch_points),
        ("时间线", _timeline_lines(timeline)),
        ("证据来源列表", _source_lines(sources)),
    ]
    return {"title": f"专题报告：{topic}", "modules": modules}


def _as_lines(value: Any) -> list[str]:
    if isinstance(value, list):
        lines = [_clean_text(item) for item in value if _clean_text(item)]
        return lines or ["对话内暂未出现。"]
    text = _clean_text(value)
    return [text] if text else ["对话内暂未出现。"]


def _summary_lines(topic: str, value: Any, key_evidence: list[Any] | None = None) -> list[str]:
    lines = _as_lines(value)
    cleaned = [_clean_summary_line(topic, line) for line in lines]
    readable = [line for line in cleaned if line and not _looks_like_fragment(line)]
    if readable and not any(_looks_like_concatenated_evidence(line) for line in readable):
        return readable[:2]
    evidence_title = _first_evidence_title(key_evidence or [])
    if evidence_title:
        return [f"当前报告围绕“{topic}”整理对话中已出现的相关材料，核心证据指向“{evidence_title}”。涉及事实判断的部分仍需以原始公告或权威来源为准。"]
    return [f"当前报告围绕“{topic}”整理对话中已出现的相关材料；涉及事实判断的部分仍需以原始公告或权威来源为准。"]


def _clean_summary_line(topic: str, value: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    anchor = _export_topic_anchor(topic)
    if not anchor:
        return text
    pieces = [piece.strip() for piece in re.split(r"(?<=[。；;])", text) if piece.strip()]
    if len(pieces) <= 1:
        stripped = _strip_export_source_tail(text)
        compact = _compact_for_export_topic(stripped)
        return text if anchor in compact else ""
    kept = []
    for piece in pieces:
        stripped = _strip_export_source_tail(piece)
        compact = _compact_for_export_topic(stripped)
        if anchor in compact:
            kept.append(stripped)
    if 0 < len(kept) < len(pieces):
        return ""
    return "".join(kept)


def _first_evidence_title(values: list[Any]) -> str:
    for item in values:
        if not isinstance(item, dict):
            continue
        title = _strip_export_source_tail(_clean_text(item.get("title") or ""))
        if title:
            return title
    return ""


def _dict_lines(values: Any, empty: str) -> list[str]:
    if not isinstance(values, list) or not values:
        return [empty]
    lines = []
    for item in values:
        if isinstance(item, dict):
            title = _clean_text(item.get("title") or "未命名来源")
            source = _clean_text(item.get("source_id") or item.get("source") or "unknown")
            summary = _clean_export_text(_best_export_summary(item))
            lines.append(f"{title}（{source}）{f'：{summary}' if summary else ''}")
        elif _clean_text(item):
            lines.append(_clean_text(item))
    return lines or [empty]


def _best_export_summary(item: dict[str, Any]) -> Any:
    for key in ("full_text", "content", "content_excerpt", "summary", "snippet", "description", "quote"):
        value = item.get(key)
        if value:
            return value
    return ""


def _merge_export_items_with_sources(items: list[Any], sources: list[Any]) -> list[Any]:
    if not items or not sources:
        return items
    source_by_key: dict[str, dict[str, Any]] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in (source.get("article_id"), _compact_for_export_topic(_strip_export_source_tail(source.get("title") or ""))):
            if key:
                source_by_key[str(key)] = source
    merged = []
    for item in items:
        if not isinstance(item, dict):
            merged.append(item)
            continue
        if item.get("full_text") or item.get("content"):
            merged.append(item)
            continue
        source = source_by_key.get(str(item.get("article_id") or ""))
        source = source or source_by_key.get(_compact_for_export_topic(_strip_export_source_tail(item.get("title") or "")))
        merged.append({**source, **item} if source else item)
    return merged


def _append_missing_export_sources(items: list[Any], sources: list[Any]) -> list[Any]:
    if not sources:
        return items
    merged = list(items or [])
    seen = {
        str(item.get("article_id") or item.get("url") or _compact_for_export_topic(item.get("title") or ""))
        for item in merged
        if isinstance(item, dict)
    }
    for source in sources:
        if not isinstance(source, dict):
            continue
        key = str(source.get("article_id") or source.get("url") or _compact_for_export_topic(source.get("title") or ""))
        if key and key not in seen:
            merged.append(source)
            seen.add(key)
    return merged


def _clean_export_text(value: Any) -> str:
    text = _clean_text(value)
    text = re.sub(r"（已截断(?:，仅展示对话内摘录)?）$", "", text).strip()
    return text


def _readable_lines(value: Any, fallback: list[str]) -> list[str]:
    lines = _as_lines(value)
    readable = [line for line in lines if not _looks_like_fragment(line)]
    return readable or fallback


def _looks_like_fragment(value: str) -> bool:
    text = _clean_text(value).strip(" -—_·|：:，,。")
    if not text:
        return True
    lowered = text.lower()
    if lowered in {"36", "-36", "36氪", "kr36", "com", "同花顺", "亿元", "万元", "2025", "2026", "10"}:
        return True
    if text.isdigit() or re.fullmatch(r"20\d{2}", text):
        return True
    has_cjk = any("\u4e00" <= char <= "\u9fff" for char in text)
    return has_cjk and len(text) <= 4


def _timeline_lines(values: Any) -> list[str]:
    if not isinstance(values, list) or not values:
        return ["时间未知：对话内暂未出现足够内容形成时间线。"]
    lines = []
    for item in values:
        if not isinstance(item, dict):
            continue
        event = _clean_text(item.get("event") or item.get("title") or "")
        if event:
            lines.append(f"{item.get('date') or '时间未知'}：{event}")
    return lines or ["时间未知：对话内暂未出现足够内容形成时间线。"]


def _source_lines(values: Any) -> list[str]:
    if not isinstance(values, list) or not values:
        return ["对话内暂未出现可列出的证据来源。"]
    lines = []
    for index, item in enumerate(values, start=1):
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title") or "未命名来源")
        source = _clean_text(item.get("source_id") or "unknown")
        url = _clean_text(item.get("url") or "")
        lines.append(f"[{index}] {title}（{source}）{url}".strip())
    return lines or ["对话内暂未出现可列出的证据来源。"]


def _export_scoped_items(report: dict[str, Any], values: Any, topic: str) -> list[Any]:
    if not isinstance(values, list):
        return []
    if report.get("report_type") == "conversation_summary" or report.get("conversation_id"):
        return values
    return _filter_export_items_by_topic(values, topic)


def _filter_export_items_by_topic(values: Any, topic: str) -> list[Any]:
    if not isinstance(values, list) or not values:
        return []
    anchor = _export_topic_anchor(topic)
    if not anchor:
        return values
    filtered = []
    for item in values:
        if not isinstance(item, dict):
            filtered.append(item)
            continue
        title = item.get("title") or item.get("event") or ""
        text = _compact_for_export_topic(f"{_strip_export_source_tail(title)} {item.get('summary') or ''}")
        if anchor in text:
            filtered.append(item)
    return filtered


def _export_topic_anchor(topic: str) -> str:
    primary = re.split(r"[：:，,。；;|｜]", str(topic or ""), maxsplit=1)[0].strip()
    primary = re.sub(r"^(中新网相关热点|2026相关热点|相关热点)\s*", "", primary).strip()
    primary = re.sub(r"\s*[-—_]\s*(36氪|中新网|中国新闻网|新浪财经|新华网)$", "", primary).strip()
    compact = _compact_for_export_topic(primary)
    if 2 <= len(compact) <= 12 and not compact.isdigit():
        return compact
    return ""


def _strip_export_source_tail(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"\s*[-—_]\s*(财经\s*[-—]\s*)?同花顺\s*$", "", text)
    text = re.sub(r"\s*[-—_]\s*(36氪|新浪财经|新华网|中国新闻网|中新网)\s*$", "", text)
    return text.strip()


def _compact_for_export_topic(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _looks_like_concatenated_evidence(value: str) -> bool:
    text = _clean_text(value)
    if len(text) < 140:
        return False
    source_markers = sum(text.count(marker) for marker in ("。", "：", "（", "）"))
    return source_markers >= 6


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return cleaned or "report"


def _docx_bytes(report: dict[str, Any]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _docx_content_types())
        archive.writestr("_rels/.rels", _docx_rels())
        archive.writestr("word/document.xml", _docx_document_xml(report))
        archive.writestr("word/_rels/document.xml.rels", _docx_document_rels())
        archive.writestr("word/styles.xml", _docx_styles())
        archive.writestr("word/numbering.xml", _docx_numbering())
        archive.writestr("word/header1.xml", _docx_header())
        archive.writestr("word/footer1.xml", _docx_footer())
    return buffer.getvalue()


def _docx_document_xml(report: dict[str, Any]) -> str:
    modules = report["modules"]
    summary = modules[0][1] if modules else []
    scope = modules[1][1] if len(modules) > 1 else []
    body = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
        "<w:body>",
        _docx_paragraph(report["title"], "ReportTitle"),
        _docx_paragraph("Personal News Agent · 固定专题报告模板", "ReportSubtitle"),
    ]
    if summary:
        body.append(_docx_heading("1. 一句话结论"))
        body.append(_docx_callout(summary))
    if scope:
        body.append(_docx_heading("2. 覆盖范围"))
        body.append(_docx_scope_block(scope))
    for index, (title, lines) in enumerate(modules[2:], start=3):
        body.append(_docx_heading(f"{index}. {title}"))
        for line in lines:
            body.append(_docx_paragraph(line, "ReportBullet", bullet=True))
    body.append(_docx_section_props())
    body.append("</w:body></w:document>")
    return "".join(body)


def _docx_callout(lines: list[str]) -> str:
    runs = []
    for index, line in enumerate(lines):
        if index:
            runs.append("<w:r><w:br/></w:r>")
        runs.append(_docx_text_run(line, "ReportCallout"))
    return (
        '<w:p><w:pPr><w:pStyle w:val="ReportCallout"/>'
        '<w:shd w:val="clear" w:color="auto" w:fill="F3F7FF"/>'
        '<w:pBdr><w:top w:val="single" w:sz="8" w:space="5" w:color="C7D7FE"/>'
        '<w:left w:val="single" w:sz="8" w:space="12" w:color="C7D7FE"/>'
        '<w:bottom w:val="single" w:sz="8" w:space="5" w:color="C7D7FE"/>'
        '<w:right w:val="single" w:sz="8" w:space="12" w:color="C7D7FE"/></w:pBdr>'
        '<w:ind w:left="240" w:right="240"/><w:spacing w:before="80" w:after="260" w:line="360" w:lineRule="auto"/>'
        "</w:pPr>"
        f'{"".join(runs)}</w:p>'
    )


def _docx_scope_block(scope: list[str]) -> str:
    parts = []
    for line in scope[:3]:
        label, value = _split_label_value(line)
        parts.append(_docx_scope_line(label, value))
    parts.append('<w:p><w:pPr><w:spacing w:after="180"/></w:pPr></w:p>')
    return "".join(parts)


def _docx_scope_line(label: str, value: str) -> str:
    return (
        '<w:p><w:pPr><w:pStyle w:val="ReportScope"/>'
        '<w:shd w:val="clear" w:color="auto" w:fill="FBFCFE"/>'
        '<w:ind w:left="200" w:right="200"/><w:spacing w:before="20" w:after="20" w:line="300" w:lineRule="auto"/>'
        "</w:pPr>"
        f'{_docx_text_run(f"{label}：", "ReportLabel")}{_docx_text_run(value, "ReportBody")}'
        "</w:p>"
    )


def _docx_summary_table(summary: list[str]) -> str:
    text = "".join(_docx_text_run(line, "ReportCallout") for line in summary)
    return (
        '<w:tbl><w:tblPr><w:tblStyle w:val="ReportCalloutTable"/>'
        '<w:tblW w:w="9360" w:type="dxa"/><w:tblInd w:w="0" w:type="dxa"/>'
        '<w:tblBorders><w:top w:val="single" w:sz="8" w:color="C7D7FE"/>'
        '<w:left w:val="single" w:sz="8" w:color="C7D7FE"/>'
        '<w:bottom w:val="single" w:sz="8" w:color="C7D7FE"/>'
        '<w:right w:val="single" w:sz="8" w:color="C7D7FE"/>'
        '<w:insideH w:val="nil"/><w:insideV w:val="nil"/></w:tblBorders>'
        '<w:tblCellMar><w:top w:w="240" w:type="dxa"/><w:left w:w="260" w:type="dxa"/>'
        '<w:bottom w:w="240" w:type="dxa"/><w:right w:w="260" w:type="dxa"/></w:tblCellMar></w:tblPr>'
        '<w:tblGrid><w:gridCol w:w="1960"/><w:gridCol w:w="7400"/></w:tblGrid><w:tr>'
        f'{_docx_cell(_docx_paragraph("1. 一句话结论", "ReportLabel"), 1960, fill="F3F7FF")}'
        f'{_docx_cell(_docx_paragraph_raw_runs(text, "ReportCallout"), 7400, fill="F3F7FF")}'
        "</w:tr></w:tbl>"
        '<w:p><w:pPr><w:spacing w:after="180"/></w:pPr></w:p>'
    )


def _docx_scope_table(scope: list[str]) -> str:
    rows = []
    for line in scope[:3]:
        label, value = _split_label_value(line)
        rows.append(
            "<w:tr>"
            f'{_docx_cell(_docx_paragraph(label, "ReportLabel"), 1320, fill="FBFCFE")}'
            f'{_docx_cell(_docx_paragraph(value, "ReportBody"), 8040, fill="FBFCFE")}'
            "</w:tr>"
        )
    return (
        '<w:tbl><w:tblPr><w:tblW w:w="9360" w:type="dxa"/><w:tblInd w:w="0" w:type="dxa"/>'
        '<w:tblBorders><w:top w:val="nil"/><w:left w:val="nil"/><w:bottom w:val="nil"/>'
        '<w:right w:val="nil"/><w:insideH w:val="single" w:sz="4" w:color="E4E9F2"/>'
        '<w:insideV w:val="nil"/></w:tblBorders>'
        '<w:tblCellMar><w:top w:w="120" w:type="dxa"/><w:left w:w="220" w:type="dxa"/>'
        '<w:bottom w:w="120" w:type="dxa"/><w:right w:w="220" w:type="dxa"/></w:tblCellMar></w:tblPr>'
        '<w:tblGrid><w:gridCol w:w="1320"/><w:gridCol w:w="8040"/></w:tblGrid>'
        f'{"".join(rows)}</w:tbl>'
        '<w:p><w:pPr><w:spacing w:after="220"/></w:pPr></w:p>'
    )


def _docx_cell(content: str, width: int, fill: str | None = None) -> str:
    shading = f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>' if fill else ""
    return (
        f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shading}'
        '<w:vAlign w:val="center"/></w:tcPr>'
        f"{content}</w:tc>"
    )


def _docx_heading(text: str) -> str:
    return _docx_paragraph(text, "ReportHeading")


def _docx_paragraph(text: str, style: str, bullet: bool = False) -> str:
    bullet_props = '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if bullet else ""
    return (
        "<w:p>"
        f'<w:pPr><w:pStyle w:val="{style}"/>{bullet_props}</w:pPr>'
        f'{_docx_text_run(text, style)}'
        "</w:p>"
    )


def _docx_paragraph_raw_runs(runs: str, style: str) -> str:
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>{runs}</w:p>'


def _docx_text_run(text: str, style: str) -> str:
    preserve = ' xml:space="preserve"' if text.startswith(" ") or text.endswith(" ") else ""
    return (
        "<w:r>"
        f"<w:rPr>{_docx_run_props(style)}</w:rPr>"
        f"<w:t{preserve}>{xml_escape(text)}</w:t>"
        "</w:r>"
    )


def _docx_run_props(style: str) -> str:
    if style == "ReportTitle":
        return f'{_docx_font_props()}<w:b/><w:color w:val="172033"/><w:sz w:val="44"/>'
    if style == "ReportSubtitle":
        return f'{_docx_font_props()}<w:color w:val="667085"/><w:sz w:val="21"/>'
    if style == "ReportHeading":
        return f'{_docx_font_props()}<w:b/><w:color w:val="155EEF"/><w:sz w:val="28"/>'
    if style == "ReportLabel":
        return f'{_docx_font_props()}<w:b/><w:color w:val="155EEF"/><w:sz w:val="20"/>'
    if style == "ReportCallout":
        return f'{_docx_font_props()}<w:b/><w:color w:val="172033"/><w:sz w:val="23"/>'
    return f'{_docx_font_props()}<w:color w:val="242A31"/><w:sz w:val="21"/>'


def _docx_font_props() -> str:
    return '<w:rFonts w:ascii="Arial Unicode MS" w:hAnsi="Arial Unicode MS" w:eastAsia="Arial Unicode MS" w:cs="Arial Unicode MS"/>'


def _docx_section_props() -> str:
    return (
        '<w:sectPr><w:headerReference w:type="default" r:id="rIdHeader1"/>'
        '<w:footerReference w:type="default" r:id="rIdFooter1"/>'
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1240" w:right="1275" w:bottom="1240" w:left="1275" w:header="540" w:footer="540" w:gutter="0"/>'
        "</w:sectPr>"
    )


def _docx_content_types() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>
</Types>"""


def _docx_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def _docx_document_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rIdHeader1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>
<Relationship Id="rIdFooter1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>
<Relationship Id="rIdNumbering1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
</Relationships>"""


def _docx_styles() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr><w:spacing w:line="320" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial Unicode MS" w:hAnsi="Arial Unicode MS" w:eastAsia="Arial Unicode MS" w:cs="Arial Unicode MS"/><w:sz w:val="21"/><w:color w:val="242A31"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ReportTitle"><w:name w:val="Report Title"/><w:pPr><w:jc w:val="center"/><w:spacing w:before="520" w:after="220"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial Unicode MS" w:hAnsi="Arial Unicode MS" w:eastAsia="Arial Unicode MS" w:cs="Arial Unicode MS"/><w:b/><w:sz w:val="44"/><w:color w:val="172033"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ReportSubtitle"><w:name w:val="Report Subtitle"/><w:pPr><w:jc w:val="center"/><w:spacing w:after="420"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial Unicode MS" w:hAnsi="Arial Unicode MS" w:eastAsia="Arial Unicode MS" w:cs="Arial Unicode MS"/><w:sz w:val="21"/><w:color w:val="667085"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ReportHeading"><w:name w:val="Report Heading"/><w:pPr><w:keepNext/><w:spacing w:before="300" w:after="140"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial Unicode MS" w:hAnsi="Arial Unicode MS" w:eastAsia="Arial Unicode MS" w:cs="Arial Unicode MS"/><w:b/><w:sz w:val="28"/><w:color w:val="155EEF"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ReportBody"><w:name w:val="Report Body"/><w:pPr><w:spacing w:after="120" w:line="320" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial Unicode MS" w:hAnsi="Arial Unicode MS" w:eastAsia="Arial Unicode MS" w:cs="Arial Unicode MS"/><w:sz w:val="21"/><w:color w:val="242A31"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ReportBullet"><w:name w:val="Report Bullet"/><w:pPr><w:spacing w:after="110" w:line="320" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial Unicode MS" w:hAnsi="Arial Unicode MS" w:eastAsia="Arial Unicode MS" w:cs="Arial Unicode MS"/><w:sz w:val="21"/><w:color w:val="242A31"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ReportLabel"><w:name w:val="Report Label"/><w:pPr><w:spacing w:after="0"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial Unicode MS" w:hAnsi="Arial Unicode MS" w:eastAsia="Arial Unicode MS" w:cs="Arial Unicode MS"/><w:b/><w:sz w:val="20"/><w:color w:val="155EEF"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ReportCallout"><w:name w:val="Report Callout"/><w:pPr><w:spacing w:after="0" w:line="360" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial Unicode MS" w:hAnsi="Arial Unicode MS" w:eastAsia="Arial Unicode MS" w:cs="Arial Unicode MS"/><w:b/><w:sz w:val="23"/><w:color w:val="172033"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ReportScope"><w:name w:val="Report Scope"/><w:pPr><w:spacing w:after="20" w:line="300" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial Unicode MS" w:hAnsi="Arial Unicode MS" w:eastAsia="Arial Unicode MS" w:cs="Arial Unicode MS"/><w:sz w:val="21"/><w:color w:val="242A31"/></w:rPr></w:style>
<w:style w:type="table" w:styleId="ReportCalloutTable"><w:name w:val="Report Callout Table"/><w:tblPr><w:tblCellMar><w:top w:w="240" w:type="dxa"/><w:left w:w="260" w:type="dxa"/><w:bottom w:w="240" w:type="dxa"/><w:right w:w="260" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
</w:styles>"""


def _docx_numbering() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:abstractNum w:abstractNumId="1">
<w:multiLevelType w:val="singleLevel"/>
<w:lvl w:ilvl="0">
<w:start w:val="1"/>
<w:numFmt w:val="bullet"/>
<w:lvlText w:val="•"/>
<w:lvlJc w:val="left"/>
<w:pPr><w:ind w:left="360" w:hanging="180"/></w:pPr>
<w:rPr><w:rFonts w:ascii="Arial Unicode MS" w:hAnsi="Arial Unicode MS" w:eastAsia="Arial Unicode MS" w:cs="Arial Unicode MS"/><w:sz w:val="21"/></w:rPr>
</w:lvl>
</w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>"""


def _docx_header() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="4" w:space="1" w:color="E4E9F2"/></w:pBdr></w:pPr></w:p>
</w:hdr>"""


def _docx_footer() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:p><w:pPr><w:pBdr><w:top w:val="single" w:sz="4" w:space="1" w:color="E4E9F2"/></w:pBdr><w:tabs><w:tab w:val="right" w:pos="9360"/></w:tabs></w:pPr>
<w:r><w:rPr><w:rFonts w:ascii="Arial Unicode MS" w:hAnsi="Arial Unicode MS" w:eastAsia="Arial Unicode MS" w:cs="Arial Unicode MS"/><w:color w:val="667085"/><w:sz w:val="16"/></w:rPr><w:t>Personal News Agent</w:t></w:r>
<w:r><w:tab/></w:r>
<w:r><w:rPr><w:rFonts w:ascii="Arial Unicode MS" w:hAnsi="Arial Unicode MS" w:eastAsia="Arial Unicode MS" w:cs="Arial Unicode MS"/><w:color w:val="667085"/><w:sz w:val="16"/></w:rPr><w:t>Page </w:t></w:r>
<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> PAGE </w:instrText></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>
</w:p></w:ftr>"""


def _pdf_bytes(report: dict[str, Any]) -> bytes:
    docx_pdf = _pdf_from_docx_bytes(_docx_bytes(report))
    if docx_pdf:
        return docx_pdf
    return _reportlab_pdf_bytes(report)


def _pdf_from_docx_bytes(docx_bytes: bytes) -> bytes | None:
    soffice = _soffice_binary()
    if not soffice or not _has_cjk_font_file():
        return None
    with tempfile.TemporaryDirectory(prefix="pna_report_export_") as tmp:
        tmp_path = Path(tmp)
        input_path = tmp_path / "report.docx"
        output_dir = tmp_path / "out"
        profile_dir = tmp_path / "profile"
        cache_dir = tmp_path / "cache"
        font_config = _fontconfig_file(tmp_path, soffice)
        input_path.write_bytes(docx_bytes)
        output_dir.mkdir()
        cache_dir.mkdir()
        env = os.environ.copy()
        env["FONTCONFIG_FILE"] = str(font_config)
        env["XDG_CACHE_HOME"] = str(cache_dir)
        result = subprocess.run(
            [
                soffice,
                "--headless",
                f"-env:UserInstallation=file://{profile_dir}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output_dir),
                str(input_path),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=45,
            env=env,
        )
        pdf_path = output_dir / "report.pdf"
        if result.returncode == 0 and pdf_path.exists() and pdf_path.stat().st_size > 0:
            return pdf_path.read_bytes()
    return None


def _fontconfig_file(tmp_path: Path, soffice: str) -> Path:
    font_dirs = [
        "/Library/Fonts",
        "/System/Library/Fonts",
        "/System/Library/Fonts/Supplemental",
    ]
    font_dirs.extend(str(path) for path in _soffice_font_dirs(soffice))
    existing_dirs = [path for path in font_dirs if Path(path).exists()]
    cache_dir = tmp_path / "fontconfig-cache"
    cache_dir.mkdir()
    config = tmp_path / "fonts.conf"
    dirs_xml = "".join(f"<dir>{xml_escape(path)}</dir>" for path in existing_dirs)
    config.write_text(
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
        f"<fontconfig>{dirs_xml}<cachedir>{xml_escape(str(cache_dir))}</cachedir></fontconfig>",
        encoding="utf-8",
    )
    return config


def _soffice_font_dirs(soffice: str) -> list[Path]:
    path = Path(soffice)
    candidates = [
        path.parent.parent / "Resources" / "fonts" / "truetype",
        (path.parent / "../../native/libreoffice-headless/libreoffice/LibreOfficeDev.app/Contents/Resources/fonts/truetype").resolve(),
    ]
    return [candidate for candidate in candidates if candidate.exists()]


def _has_cjk_font_file() -> bool:
    return any(path.exists() for path in _pdf_font_candidates())


def _soffice_binary() -> str | None:
    configured = os.environ.get("PNA_SOFFICE_BIN")
    if configured and Path(configured).exists():
        return configured
    found = shutil.which("soffice")
    if found:
        return found
    for candidate in (
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/opt/homebrew/bin/soffice",
        "/usr/local/bin/soffice",
        "/usr/bin/soffice",
    ):
        if Path(candidate).exists():
            return candidate
    return None


def _reportlab_pdf_bytes(report: dict[str, Any]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font_name = _register_pdf_font()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=22 * mm,
        bottomMargin=22 * mm,
        title=report["title"],
    )
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName=font_name,
            fontSize=22,
            leading=30,
            textColor=colors.HexColor("#172033"),
            alignment=TA_CENTER,
            spaceAfter=8 * mm,
        ),
        "subtitle": ParagraphStyle(
            "ReportSubtitle",
            parent=base["Normal"],
            fontName=font_name,
            fontSize=10,
            leading=15,
            textColor=colors.HexColor("#667085"),
            alignment=TA_CENTER,
            spaceAfter=8 * mm,
        ),
        "label": ParagraphStyle(
            "ReportLabel",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#155EEF"),
        ),
        "callout": ParagraphStyle(
            "ReportCallout",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=11,
            leading=18,
            textColor=colors.HexColor("#172033"),
        ),
        "section": ParagraphStyle(
            "ReportSection",
            parent=base["Heading2"],
            fontName=font_name,
            fontSize=14,
            leading=19,
            textColor=colors.HexColor("#155EEF"),
            spaceBefore=6 * mm,
            spaceAfter=3 * mm,
        ),
        "body": ParagraphStyle(
            "ReportBody",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=10.5,
            leading=17,
            textColor=colors.HexColor("#242A31"),
            spaceAfter=2.5 * mm,
        ),
        "bullet": ParagraphStyle(
            "ReportBullet",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=10,
            leading=16,
            leftIndent=8 * mm,
            firstLineIndent=-4 * mm,
            bulletIndent=2 * mm,
            textColor=colors.HexColor("#2F3640"),
            spaceAfter=2 * mm,
        ),
    }
    story = [
        Paragraph(_xml(report["title"]), styles["title"]),
        Paragraph("Personal News Agent · 固定专题报告模板", styles["subtitle"]),
    ]
    summary = report["modules"][0][1] if report["modules"] else []
    scope = report["modules"][1][1] if len(report["modules"]) > 1 else []
    if summary:
        story.append(_pdf_summary_table(summary, styles, colors, mm))
        story.append(Spacer(1, 4 * mm))
    if scope:
        story.append(Paragraph("2. 覆盖范围", styles["section"]))
        story.append(_pdf_scope_table(scope, styles, colors, mm))
        story.append(Spacer(1, 5 * mm))
    for index, (title, values) in enumerate(report["modules"][2:], start=3):
        heading = Paragraph(_xml(f"{index}. {title}"), styles["section"])
        if values:
            first = Paragraph(_xml(values[0]), styles["bullet"], bulletText="•")
            story.append(KeepTogether([heading, first]))
            for line in values[1:]:
                story.append(Paragraph(_xml(line), styles["bullet"], bulletText="•"))
        else:
            story.append(heading)
        story.append(Spacer(1, 1.5 * mm))
    doc.build(story, onFirstPage=_pdf_page_decorator(font_name), onLaterPages=_pdf_page_decorator(font_name))
    return buffer.getvalue()


def _pdf_summary_table(summary: list[str], styles: dict[str, Any], colors: Any, mm: Any) -> Any:
    from reportlab.platypus import Paragraph, Table, TableStyle

    text = "<br/>".join(_xml(line) for line in summary)
    table = Table(
        [[Paragraph("1. 一句话结论", styles["label"]), Paragraph(text, styles["callout"])]],
        colWidths=[34 * mm, 121 * mm],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F7FF")),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#C7D7FE")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 4 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4 * mm),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def _pdf_scope_table(scope: list[str], styles: dict[str, Any], colors: Any, mm: Any) -> Any:
    from reportlab.platypus import Paragraph, Table, TableStyle

    cells = []
    for line in scope[:3]:
        label, value = _split_label_value(line)
        cells.append([Paragraph(_xml(label), styles["label"]), Paragraph(_xml(value), styles["body"])])
    table = Table(cells, colWidths=[22 * mm, 133 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FBFCFE")),
                ("LINEBELOW", (0, 0), (-1, -2), 0.5, colors.HexColor("#E4E9F2")),
                ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def _split_label_value(line: str) -> tuple[str, str]:
    if "：" in line:
        label, value = line.split("：", 1)
        return label, value
    return "范围", line


def _register_pdf_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_name = "PNAReportCJK"
    if font_name in pdfmetrics.getRegisteredFontNames():
        return font_name
    for path in _pdf_font_candidates():
        if path.exists():
            pdfmetrics.registerFont(TTFont(font_name, str(path)))
            return font_name
    return "Helvetica"


def _pdf_font_candidates() -> list[Path]:
    return [
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
    ]


def _pdf_page_decorator(font_name: str):
    def draw(canvas, doc):
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm

        width, height = A4
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#E4E9F2"))
        canvas.setLineWidth(0.6)
        canvas.line(20 * mm, height - 15 * mm, width - 20 * mm, height - 15 * mm)
        canvas.line(20 * mm, 15 * mm, width - 20 * mm, 15 * mm)
        canvas.setFont(font_name, 8)
        canvas.setFillColor(colors.HexColor("#667085"))
        canvas.drawString(20 * mm, 9.5 * mm, "Personal News Agent")
        canvas.drawRightString(width - 20 * mm, 9.5 * mm, f"Page {doc.page}")
        canvas.restoreState()

    return draw


def _xml(value: Any) -> str:
    return xml_escape(_clean_text(value))
