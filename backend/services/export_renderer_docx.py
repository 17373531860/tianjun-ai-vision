"""
v3.5.0 Step 5: docx 渲染器（路线 A 自动样式 + 路线 B docxtpl 占位符模板）

路线 A — 纯文本 → docx 自动样式
  输入: 模板 content（Jinja2，渲染后是纯文本/简易 markdown）
  输出: 自动按行生成段落，识别 markdown 标题（# ## ###）→ 应用 Heading 样式
        识别 csv 风格的逗号分隔行 → 自动转表格

路线 B — 用户上传 .docx 模板（带 {{ }} 占位符）
  输入: ExportTemplate.template_file_path 指向 DATA_DIR 下的占位符 docx 文件
  输出: docxtpl 渲染填值

调用方:
- export_renderer.render_to_bytes() 在 fmt 为 'docx' 时转发到 render_docx_to_bytes()
- 实时规则同样
"""
from __future__ import annotations

import io
import os
import re
from typing import Any, Dict, Optional

from backend.core.config import DATA_DIR
from backend.services.export_renderer import render_string


# ============================================================
# 路线 A：自动样式渲染
# ============================================================

def render_docx_route_a(rendered_text: str) -> bytes:
    """把 Jinja2 渲染后的纯文本转成 docx，自动识别简单 markdown

    支持：
    - `# heading 1` 等 markdown 标题（最多 4 级）→ Heading 样式
    - 空行 → 段落分隔
    - 含 `,` 的连续行（≥2 行）→ 自动表格
    - 其余行 → 普通段落（中文等宽友好的 Calibri/SimSun）
    """
    from docx import Document
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    # 默认中文友好样式
    style = doc.styles['Normal']
    style.font.name = 'Calibri'
    style.font.size = Pt(11)

    # 简易 markdown / 表格识别
    lines = rendered_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            doc.add_paragraph()
            i += 1
            continue

        # markdown 标题
        m = re.match(r'^(#{1,4})\s+(.+)$', stripped)
        if m:
            level = min(len(m.group(1)), 4)
            doc.add_heading(m.group(2).strip(), level=level)
            i += 1
            continue

        # 检测连续 csv 行（≥ 2 行，包含逗号）
        if ',' in stripped:
            csv_block = []
            j = i
            while j < len(lines) and ',' in lines[j].strip() and lines[j].strip():
                csv_block.append(lines[j].strip())
                j += 1
            if len(csv_block) >= 2:
                _write_csv_table(doc, csv_block)
                i = j
                continue

        # 普通段落
        doc.add_paragraph(line)
        i += 1

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _write_csv_table(doc, lines):
    """把 csv 行写入 docx 表格，第一行作表头"""
    from docx.enum.table import WD_TABLE_ALIGNMENT

    rows = [ln.split(',') for ln in lines]
    cols = max(len(r) for r in rows)
    rows = [r + [''] * (cols - len(r)) for r in rows]

    table = doc.add_table(rows=len(rows), cols=cols)
    table.style = 'Light Grid Accent 1'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    # 表头加粗
    for ci, val in enumerate(rows[0]):
        cell = table.cell(0, ci)
        cell.text = val.strip()
        for run in cell.paragraphs[0].runs:
            run.bold = True

    # 数据行
    for ri in range(1, len(rows)):
        for ci in range(cols):
            table.cell(ri, ci).text = rows[ri][ci].strip()


# ============================================================
# 路线 B：docxtpl 占位符模板
# ============================================================

def render_docx_route_b(template_file_path: str,
                       context: Dict[str, Any]) -> bytes:
    """读取占位符 docx 文件，用 docxtpl 渲染填值

    占位符 docx 里写 {{ workpiece.serial_no }}、{% for c in stats.cycles %}{% endfor %} 等
    docxtpl 会保留原文档样式（字体、颜色、表格、图片等）。

    template_file_path: 绝对路径 或 DATA_DIR 下相对路径
    """
    from docxtpl import DocxTemplate

    abs_path = template_file_path
    if not os.path.isabs(abs_path):
        abs_path = os.path.join(DATA_DIR, template_file_path)

    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"docx 模板文件不存在: {abs_path}")

    tpl = DocxTemplate(abs_path)
    tpl.render(context)

    buf = io.BytesIO()
    tpl.save(buf)
    return buf.getvalue()


# ============================================================
# 统一入口（由 export_renderer.render_to_bytes 调用）
# ============================================================

def render_docx_to_bytes(template_content: str,
                         context: Dict[str, Any],
                         template_file_path: Optional[str] = None) -> bytes:
    """根据是否提供模板文件路径自动选路线 A 或 B

    路线 B 优先：如果 ExportTemplate.template_file_path 非空，走 docxtpl
    路线 A 兜底：直接 Jinja2 渲染 content 后用 python-docx 自动样式生成
    """
    if template_file_path:
        return render_docx_route_b(template_file_path, context)

    rendered = render_string(template_content, context)
    return render_docx_route_a(rendered)
