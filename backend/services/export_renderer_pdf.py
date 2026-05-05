"""
v3.5.0 Step 5: pdf 渲染器

只做路线 A — Jinja2 渲染出文本/markdown → reportlab 生成 PDF。
路线 B（用户上传 PDF 模板）实现复杂、字段定位难，本版不支持。

中文支持：
- reportlab 默认不支持中文，需要注册 CID 字体 STSong-Light（系统自带）
- 兜底：若 STSong-Light 不可用，退回 ASCII（中文显示为 ?）
"""
from __future__ import annotations

import io
import re
from typing import Any, Dict, Optional

from backend.services.export_renderer import render_string


# ============================================================
# 中文字体注册（一次性，幂等）
# ============================================================

_FONT_REGISTERED = False
_CN_FONT = "Helvetica"   # 兜底字体（非中文）


def _ensure_cn_font():
    """注册 reportlab 的中文 CID 字体 STSong-Light（Adobe 内置 CMap）"""
    global _FONT_REGISTERED, _CN_FONT
    if _FONT_REGISTERED:
        return _CN_FONT
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        _CN_FONT = 'STSong-Light'
        _FONT_REGISTERED = True
    except Exception as e:
        print(f"[PDF] 注册中文字体失败，将以 Helvetica 渲染（中文可能丢失）: {e}", flush=True)
        _FONT_REGISTERED = True
    return _CN_FONT


# ============================================================
# 路线 A：纯文本 → PDF
# ============================================================

def render_pdf_route_a(rendered_text: str,
                       page_size: str = "A4",
                       title: str = "Tianjun Export") -> bytes:
    """把 Jinja2 渲染后的纯文本/简易 markdown 转 PDF

    支持：
    - markdown 标题 # / ## / ### → 加粗大字号
    - csv 风格行（≥ 2 行连续逗号分隔）→ 自动表格
    - 普通文本 → 段落
    """
    from reportlab.lib.pagesizes import A4, LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    )
    from reportlab.lib import colors

    cn_font = _ensure_cn_font()

    psize = A4 if page_size.upper() == 'A4' else LETTER
    buf = io.BytesIO()

    doc = SimpleDocTemplate(
        buf, pagesize=psize, title=title,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()

    body_style = ParagraphStyle(
        'CNBody', parent=styles['Normal'],
        fontName=cn_font, fontSize=10, leading=14,
    )
    h1_style = ParagraphStyle(
        'CNH1', parent=styles['Heading1'],
        fontName=cn_font, fontSize=18, leading=22, spaceAfter=6,
    )
    h2_style = ParagraphStyle(
        'CNH2', parent=styles['Heading2'],
        fontName=cn_font, fontSize=14, leading=18, spaceAfter=4,
    )
    h3_style = ParagraphStyle(
        'CNH3', parent=styles['Heading3'],
        fontName=cn_font, fontSize=12, leading=16, spaceAfter=2,
    )

    story = []
    lines = rendered_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            story.append(Spacer(1, 6))
            i += 1
            continue

        # markdown 标题
        m = re.match(r'^(#{1,3})\s+(.+)$', stripped)
        if m:
            level = len(m.group(1))
            text = _escape(m.group(2).strip())
            style = [h1_style, h2_style, h3_style][level - 1]
            story.append(Paragraph(text, style))
            i += 1
            continue

        # csv 表格识别
        if ',' in stripped:
            csv_block = []
            j = i
            while j < len(lines) and ',' in lines[j].strip() and lines[j].strip():
                csv_block.append([s.strip() for s in lines[j].split(',')])
                j += 1
            if len(csv_block) >= 2:
                cols = max(len(r) for r in csv_block)
                csv_block = [r + [''] * (cols - len(r)) for r in csv_block]
                tbl = Table(csv_block, repeatRows=1)
                tbl.setStyle(TableStyle([
                    ('FONTNAME', (0, 0), (-1, -1), cn_font),
                    ('FONTSIZE', (0, 0), (-1, -1), 9),
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4F81BD')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#999999')),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1),
                     [colors.white, colors.HexColor('#F5F5F5')]),
                ]))
                story.append(tbl)
                story.append(Spacer(1, 8))
                i = j
                continue

        # 普通段落
        story.append(Paragraph(_escape(line), body_style))
        i += 1

    if not story:
        story.append(Paragraph("(empty)", body_style))

    doc.build(story)
    return buf.getvalue()


def _escape(text: str) -> str:
    """reportlab Paragraph 用 HTML-like 标签，需要转义 & < >"""
    return (text.replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;'))


# ============================================================
# 统一入口
# ============================================================

def render_pdf_to_bytes(template_content: str,
                        context: Dict[str, Any],
                        template_file_path: Optional[str] = None) -> bytes:
    """目前只支持路线 A（纯文本/markdown），路线 B 不实现"""
    if template_file_path:
        # 兼容性：路径 B 不支持，退回 A
        print(f"[PDF] 路线 B 不支持，使用路线 A 渲染。template_file_path={template_file_path}",
              flush=True)
    rendered = render_string(template_content, context)
    return render_pdf_route_a(rendered)
