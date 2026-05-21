"""
v3.8.x 定时导出 + 多格式快捷下载 — 共用 writer 模块

职责: 把"已经渲染好的 CSV 字符串"再转成不同输出格式的字节流。

跟 export_renderer.py 的分工:
- export_renderer.py    : Jinja2 模板 -> 文本/docx/xlsx/pdf (面向单 cycle 自定义渲染)
- export_scheduled_writers.py (本文件): CSV string -> 多格式 (面向"标准日报"批量数据)

使用场景:
1. 数据页 4 个快捷按钮 (导出当日/某周/某月/范围) 允许用户选 csv/txt/xlsx/docx/pdf
2. 定时导出规则按 cron 跑出来的 CSV, 客户要 xlsx / pdf 也能直接出

设计原则: KISS, 一行 CSV 一行表格, 不做花哨样式。客户要花样式去走自定义模板路径。
"""
from __future__ import annotations

import csv
import io
from typing import List


# ============================================================
# 公共: format 元数据
# ============================================================

FILE_EXTENSIONS = {
    "csv": "csv",
    "txt": "txt",
    "xlsx": "xlsx",
    "docx": "docx",
    "pdf": "pdf",
}

MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}

SUPPORTED_FORMATS = set(FILE_EXTENSIONS.keys())


def _parse_csv_rows(csv_str: str) -> List[List[str]]:
    """把 CSV 字符串解析回二维数组. 自动剥掉 BOM."""
    if csv_str.startswith("\ufeff"):
        csv_str = csv_str[1:]
    reader = csv.reader(io.StringIO(csv_str))
    return [row for row in reader]


# ============================================================
# 各 format writer
# ============================================================

def _write_csv(csv_str: str) -> bytes:
    """UTF-8 + BOM (Windows Excel 友好). 不去 BOM."""
    if not csv_str.startswith("\ufeff"):
        csv_str = "\ufeff" + csv_str
    return csv_str.encode("utf-8")


def _write_txt(csv_str: str) -> bytes:
    """把 CSV 换成制表符分隔的纯文本, 看起来更像"日报"样式."""
    rows = _parse_csv_rows(csv_str)
    buf = io.StringIO()
    for row in rows:
        buf.write("\t".join(row))
        buf.write("\n")
    return buf.getvalue().encode("utf-8")


def _write_xlsx(csv_str: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    rows = _parse_csv_rows(csv_str)
    wb = Workbook()
    ws = wb.active
    ws.title = "数据"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="305496", end_color="305496", fill_type="solid")
    center = Alignment(horizontal="center", vertical="center")

    for r_idx, row in enumerate(rows, start=1):
        for c_idx, val in enumerate(row, start=1):
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
            if r_idx == 1:
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = center

    # 自适应列宽 (近似)
    for col_idx in range(1, ws.max_column + 1):
        col_letter = ws.cell(row=1, column=col_idx).column_letter
        max_len = 0
        for r in range(1, min(ws.max_row + 1, 200)):
            v = ws.cell(row=r, column=col_idx).value
            if v is not None:
                # 中文按 2 字符宽估算
                w = sum(2 if ord(ch) > 127 else 1 for ch in str(v))
                max_len = max(max_len, w)
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 10), 50)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _write_docx(csv_str: str) -> bytes:
    """docx 渲染。

    性能注意: python-docx 对大表 (>200 行) 的 cell.text 赋值有 O(n) 的 XML 树
    刷新开销, 整体逼近 O(n²)。为支撑"标准日报"几百行的场景, 直接走 XML
    fast-path: 一次性 add_table 后, 通过 _tc element 直接塞文本, 比 cell.text
    快 5-10x。表头行单独走 cell.text 保留样式。
    """
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    rows = _parse_csv_rows(csv_str)
    if not rows:
        rows = [["(无数据)"]]

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(9)

    title = doc.add_heading("天军 AI 视觉检测 — 数据导出", level=1)
    title.alignment = 1

    n_cols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=n_cols)
    table.style = "Light Grid Accent 1"

    # 表头 (第 0 行) — 走完整样式
    if rows:
        for c_idx in range(n_cols):
            val = rows[0][c_idx] if c_idx < len(rows[0]) else ""
            cell = table.cell(0, c_idx)
            cell.text = val
            for para in cell.paragraphs:
                for run in para.runs:
                    run.font.bold = True
                    run.font.size = Pt(9)
                    run.font.color.rgb = RGBColor(0x30, 0x54, 0x96)

    # 数据行 — 走 XML fast-path, 直接写 <w:t> 文本节点
    for r_idx in range(1, len(rows)):
        row = rows[r_idx]
        tr = table.rows[r_idx]._tr
        for c_idx, tc in enumerate(tr.findall(qn("w:tc"))):
            if c_idx >= n_cols:
                break
            val = row[c_idx] if c_idx < len(row) else ""
            # 清掉 add_table 默认创建的空 <w:p>, 重新塞一个带文本的
            for p in tc.findall(qn("w:p")):
                tc.remove(p)
            p = OxmlElement("w:p")
            r = OxmlElement("w:r")
            t = OxmlElement("w:t")
            t.text = val
            t.set(qn("xml:space"), "preserve")
            r.append(t)
            p.append(r)
            tc.append(p)

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _write_pdf(csv_str: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    # 注册中文字体 (reportlab 内建 STSong-Light 走 CID, 无需外部 ttf, 100% 可用)
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        cn_font = "STSong-Light"
    except Exception:
        cn_font = "Helvetica"

    rows = _parse_csv_rows(csv_str)
    if not rows:
        rows = [["(无数据)"]]

    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=landscape(A4),
                            leftMargin=20, rightMargin=20, topMargin=24, bottomMargin=20)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleCN", parent=styles["Title"], fontName=cn_font, fontSize=14,
    )
    elements = [Paragraph("天军 AI 视觉检测 — 数据导出", title_style), Spacer(1, 8)]

    table = Table(rows, repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), cn_font),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#305496")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
    ]))
    elements.append(table)

    doc.build(elements)
    return out.getvalue()


# ============================================================
# 总入口
# ============================================================

def csv_string_to_format_bytes(csv_str: str, fmt: str) -> bytes:
    """统一入口: 把 CSV 字符串 (带或不带 BOM) 转成指定格式的字节流."""
    fmt = (fmt or "csv").lower().strip()
    if fmt == "csv":
        return _write_csv(csv_str)
    if fmt == "txt":
        return _write_txt(csv_str)
    if fmt == "xlsx":
        return _write_xlsx(csv_str)
    if fmt == "docx":
        return _write_docx(csv_str)
    if fmt == "pdf":
        return _write_pdf(csv_str)
    raise ValueError(f"unsupported format: {fmt!r}, supported: {sorted(SUPPORTED_FORMATS)}")
