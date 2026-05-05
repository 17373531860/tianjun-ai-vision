"""
v3.5.0 Step 5: xlsx 渲染器（路线 A 自动转表格 + 路线 B openpyxl 占位符模板）

路线 A — Jinja2 渲染出 csv 文本 → xlsx
  把 csv 行写入 .xlsx 第一个 sheet，自动识别表头加粗、自动列宽、数字识别。

路线 B — 用户上传 .xlsx 占位符模板
  打开 .xlsx → 遍历所有 cell → 若 value 是 str 且含 {{ }} → Jinja2 渲染替换
  保留原表格样式、合并单元格、公式（不渲染公式里的占位符）。
"""
from __future__ import annotations

import io
import os
import re
from typing import Any, Dict, Optional

from backend.core.config import DATA_DIR
from backend.services.export_renderer import render_string


# ============================================================
# 路线 A
# ============================================================

def render_xlsx_route_a(rendered_text: str,
                        sheet_name: str = "Export") -> bytes:
    """把 Jinja2 渲染后的 csv 文本写入 xlsx

    自动：
    - 第一行作为表头：加粗、灰色背景
    - 自适应列宽（最长内容 * 1.2）
    - 数字字符串自动转 float
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name[:31]  # Excel 限制 sheet 名 ≤ 31

    lines = [ln for ln in rendered_text.splitlines() if ln.strip()]
    if not lines:
        ws['A1'] = '(empty)'
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    rows = [_split_csv_line(ln) for ln in lines]
    cols = max(len(r) for r in rows)
    rows = [r + [''] * (cols - len(r)) for r in rows]

    # 表头样式
    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill('solid', fgColor='4F81BD')
    header_align = Alignment(horizontal='center', vertical='center')

    # 写入
    for ri, row in enumerate(rows, start=1):
        for ci, val in enumerate(row, start=1):
            cell = ws.cell(row=ri, column=ci, value=_coerce_value(val))
            if ri == 1:
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_align

    # 自适应列宽
    for ci in range(1, cols + 1):
        col_letter = ws.cell(row=1, column=ci).column_letter
        max_len = max(len(str(rows[ri][ci - 1])) for ri in range(len(rows)))
        ws.column_dimensions[col_letter].width = min(max(max_len * 1.2, 8), 40)

    # 冻结表头
    ws.freeze_panes = 'A2'

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _split_csv_line(line: str) -> list:
    """简易 CSV 拆分 — 不处理引号转义复杂情况，遵循"逗号分隔 + 去首尾空白"
    上层模板若需要"含逗号的字段"应用 ; 之类替代分隔符或自己加双引号。
    """
    return [s.strip() for s in line.split(',')]


_NUM_RE = re.compile(r'^-?\d+(?:\.\d+)?$')


def _coerce_value(s: str):
    """把字符串里能转数字的转 float/int，方便 Excel 后续做计算"""
    if not s:
        return s
    if _NUM_RE.match(s):
        try:
            return int(s) if '.' not in s else float(s)
        except ValueError:
            return s
    return s


# ============================================================
# 路线 B：openpyxl 占位符模板
# ============================================================

def render_xlsx_route_b(template_file_path: str,
                        context: Dict[str, Any]) -> bytes:
    """读取占位符 xlsx 文件，遍历单元格替换 {{ }} 占位符

    保留：
    - 原始字体、填充色、对齐
    - 合并单元格
    - 列宽行高
    - 公式（=A1+B1 不渲染）

    占位符位置：直接写在单元格 value 里。每个 cell 视为独立 Jinja2 字符串模板渲染。
    """
    from openpyxl import load_workbook

    abs_path = template_file_path
    if not os.path.isabs(abs_path):
        abs_path = os.path.join(DATA_DIR, template_file_path)

    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"xlsx 模板文件不存在: {abs_path}")

    wb = load_workbook(abs_path)

    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if isinstance(v, str) and '{{' in v:
                    # 跳过公式（=...）
                    if v.startswith('='):
                        continue
                    try:
                        cell.value = render_string(v, context)
                    except Exception:
                        # 渲染失败保留原占位符，便于客户排查
                        pass

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ============================================================
# 统一入口
# ============================================================

def render_xlsx_to_bytes(template_content: str,
                         context: Dict[str, Any],
                         template_file_path: Optional[str] = None) -> bytes:
    if template_file_path:
        return render_xlsx_route_b(template_file_path, context)
    rendered = render_string(template_content, context)
    return render_xlsx_route_a(rendered)
