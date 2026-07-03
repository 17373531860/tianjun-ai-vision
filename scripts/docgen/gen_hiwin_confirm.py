#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成：上银包装线 AI 视觉检测 — 现场流程与对接确认清单 (docx → pdf)。

给客户确认用。纯业务语言, 每个问题留"贵司确认"列直接填。
"""

import os

from docx import Document
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

# 输出到 docs/ (docx 被 .gitignore 排除, 仅本地生成用)
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DOCX = os.path.join(_ROOT, 'docs', '上银包装线检测_对接确认清单.docx')
CN_FONT = 'Noto Sans CJK SC'
BLUE = RGBColor(0x0D, 0x47, 0xA1)
DARK = RGBColor(0x1A, 0x1A, 0x1A)
RED = RGBColor(0xC0, 0x39, 0x2B)


# ── 基础工具 ─────────────────────────────────────────────────────────────────

def set_cn(run, size=10.5, bold=False, color=DARK):
    run.font.size = Pt(size)
    run.bold = bold
    run.font.color.rgb = color
    run.font.name = CN_FONT
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn('w:rFonts'))
    if rFonts is None:
        rFonts = OxmlElement('w:rFonts')
        rPr.append(rFonts)
    rFonts.set(qn('w:eastAsia'), CN_FONT)
    rFonts.set(qn('w:ascii'), CN_FONT)
    rFonts.set(qn('w:hAnsi'), CN_FONT)


def make_doc():
    doc = Document()
    sec = doc.sections[0]
    sec.top_margin = Inches(0.85)
    sec.bottom_margin = Inches(0.85)
    sec.left_margin = Inches(0.9)
    sec.right_margin = Inches(0.9)
    return doc


def h1(doc, t):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(4)
    set_cn(p.add_run(t), size=20, bold=True, color=BLUE)


def sub(doc, t):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(2)
    set_cn(p.add_run(t), size=10.5, color=RGBColor(0x55, 0x55, 0x55))


def h2(doc, t):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(14)
    p.paragraph_format.space_after = Pt(5)
    set_cn(p.add_run(t), size=14, bold=True, color=BLUE)


def h3(doc, t):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(2)
    set_cn(p.add_run(t), size=11.5, bold=True, color=DARK)


def body(doc, t, bold=False, size=10.5, color=DARK):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(3)
    set_cn(p.add_run(t), size=size, bold=bold, color=color)
    return p


def step(doc, n, t, warn=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.left_indent = Inches(0.1)
    set_cn(p.add_run(f'{n}  '), size=11, bold=True, color=BLUE)
    set_cn(p.add_run(t), size=10.5)
    if warn:
        set_cn(p.add_run('   ⚠ ' + warn), size=10, bold=True, color=RED)


def shade(cell, hexcolor):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), hexcolor)
    tcPr.append(shd)


def cell_text(cell, t, bold=False, size=10, color=DARK, align=None):
    cell.text = ''
    p = cell.paragraphs[0]
    if align:
        p.alignment = align
    set_cn(p.add_run(t), size=size, bold=bold, color=color)


# ── 问题清单表 ───────────────────────────────────────────────────────────────

def q_table(doc, rows):
    """rows: list of ('group'|'q', code, text)。group=分组小标题行, q=问题行。"""
    table = doc.add_table(rows=0, cols=3)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = 'Table Grid'
    widths = [Inches(0.55), Inches(4.95), Inches(1.7)]

    # 表头
    hr = table.add_row().cells
    cell_text(hr[0], '编号', bold=True, size=10, color=RGBColor(0xFF, 0xFF, 0xFF), align=WD_ALIGN_PARAGRAPH.CENTER)
    cell_text(hr[1], '需贵司确认的问题', bold=True, size=10, color=RGBColor(0xFF, 0xFF, 0xFF))
    cell_text(hr[2], '贵司确认 / 填写', bold=True, size=10, color=RGBColor(0xFF, 0xFF, 0xFF), align=WD_ALIGN_PARAGRAPH.CENTER)
    for c in hr:
        shade(c, '0D47A1')

    for kind, code, text in rows:
        cells = table.add_row().cells
        if kind == 'group':
            a = cells[0].merge(cells[1]).merge(cells[2])
            cell_text(a, code + '  ' + text, bold=True, size=10.5, color=BLUE)
            shade(a, 'E8F0FE')
        else:
            cell_text(cells[0], code, bold=True, size=10, color=BLUE, align=WD_ALIGN_PARAGRAPH.CENTER)
            # 问题正文 (支持高亮前缀)
            cells[1].text = ''
            p = cells[1].paragraphs[0]
            if text.startswith('[!]'):
                set_cn(p.add_run('【最关键】'), size=10, bold=True, color=RED)
                set_cn(p.add_run(text[3:]), size=10)
            else:
                set_cn(p.add_run(text), size=10)
            cell_text(cells[2], '', size=10)

    for row in table.rows:
        for i, c in enumerate(row.cells):
            c.width = widths[i]
    return table


# ── 正文 ─────────────────────────────────────────────────────────────────────

def build():
    doc = make_doc()

    h1(doc, '上银包装线 AI 视觉检测 · 对接确认清单')
    sub(doc, '天军 AI 视觉检测系统  ×  HIWIN 上银  ｜  现场作业流程梳理与待确认事项')
    sub(doc, '日期：2026-06-15　　版本：确认稿 v2（部分已确认，余项待回填）')

    # ── 一、流程理解 ──
    h2(doc, '一、我们理解的现场作业流程（请贵司确认是否准确）')
    body(doc, '以下是我们根据现场沟通整理的包装检测流程，请逐步核对，有出入的地方请直接标注：', size=10.5)
    step(doc, '①', '扫工单标签（此码即工单号）→ 系统主动向上银 MES 发起查询 → MES 返回本工单要做几箱、物料规格等信息。')
    step(doc, '②', '取第 1 个箱子，再次扫该标签（与工单号是同一个号）＝ 正式开始第 1 箱的检测。', warn='箱标签必须与工单号一致，不一致＝标签错，立即报警。')
    step(doc, '③', '贴标签 → 套内袋。')
    step(doc, '④', '取检测合格的托盘（视觉一直在数托盘里的滑块数量）放进箱子，同时下一个托盘进入检测区。', warn='托盘内滑块数量不达标＝报警。')
    step(doc, '⑤', '如此往复，一个箱子共放 4 个托盘 → 封箱（人工动作）。')
    step(doc, '⑥', '扫下一个箱标签（仍是同一个号）→ 此刻结算上一箱（判 OK/NG），并开始新一箱。')
    step(doc, '⑦', '重复直到做满工单规定箱数。扫到「不同的号」＝新工单，自动收尾上一张工单并回推 MES。')
    body(doc, '报警判定共 4 处：① 箱标签 ≠ 工单 → 标签错；② 托盘滑块数量不对 → 数量错；'
              '③ 没做满规定箱数就扫了新工单 → 漏箱；④ 做满后还继续 → 多箱。', size=10, bold=True)
    body(doc, '【已与贵司确认】箱标签与工单号完全相同，系统按「扫码次数＋检测节拍」识别动作：'
              '第 1 次扫某号＝开工单；第 2 次扫同号＝开始第 1 箱；之后每次扫同号＝结算上一箱并开下一箱；'
              '扫到不同的号＝视为新工单（先收尾旧工单）。', size=10, bold=True, color=BLUE)

    # ── 二、待确认问题 ──
    h2(doc, '二、需要贵司确认的问题（请在右侧逐条填写）')
    rows = [
        ('group', 'A 组', '工单与箱数'),
        ('q', 'A1', '请确认：MES 返回的「排产量」(dispatch_qty) 是否就是本工单要做的箱数？'
                    '若是，箱数直接取此值；若不是，请告知箱数由哪个字段给出、或如何计算。'),

        ('group', 'B 组', '标签与扫码格式（最关键）'),
        ('q', 'B1', '扫码枪扫出来的码，与工单 / 箱标签上显示的号之间的那个连字符「-」，需不需要我们系统这边独立加上？'
                    '如果需要加，是否一定固定为 15 位数字？（请附一组真实样例：扫码枪实际扫出的字符串 vs 显示的工单号，便于我们对齐）'),
        ('q', 'B2', '同一张工单的所有箱标签，是不是都是同一个号（与工单号一模一样）？会不会出现「尾箱」使用特殊号？'),

        ('group', 'C 组', '托盘与滑块数量'),
        ('q', 'C1', '每个托盘里规定的滑块数量，是固定值，还是随工单物料规格（spec）变化？若随物料变，这个数量从哪来（MES 字段 / 我们按规格表配置）？'),
        ('q', 'C2', '一箱固定 4 个托盘，这个「4」是固定的，还是随物料规格变化？'),
        ('q', 'C3', '检测时托盘是「静止」给一个稳定画面，还是在移动中？（影响数量判读的稳定性与调参）'),
        ('q', 'C4', '某个托盘数量不达标（少/多）时现场怎么处理：工人补满后重新检测，还是整托盘作废换一盘？（影响「凑满 4 托盘」的计数）'),

        ('group', 'D 组', '异常与边界处理'),
        ('q', 'D1', '扫工单后，MES 查询失败 / 工单不存在时怎么处理？（报警提示重扫，还是允许先离线作业）'),
        ('q', 'D2', '标签错（箱标签 ≠ 工单）报警后，是「阻断」（必须重扫正确标签才能继续）还是仅「提示」？报警后工人重扫正确标签能否直接续做？'),
        ('q', 'D3', '漏箱报警后（没做满规定箱数就扫了新工单），允许回头补做，还是当前工单作废？'),
        ('q', 'D4', '强制停止 / 待机时：当前没做满的箱按什么结算？（贵司已确认「与跟踪模式一致」——按当前进度结掉；请补充：未满箱算合格还是不合格）'),
    ]
    q_table(doc, rows)

    # ── 三、暂定处理方式 ──
    h2(doc, '三、我们暂定的系统处理方式（供参考，确认后实施）')
    body(doc, '· 视觉检测：每个托盘作为一个检测周期，自动数滑块数量是否达标，不达标即报警。')
    body(doc, '· 扫码：使用 USB 扫码枪。扫工单 → 向 MES 拉取工单；扫箱标签 → 与当前工单比对，不符报警。')
    body(doc, '· 结算：建立「工单 → 箱 → 托盘」三层计数与校验状态机；扫到新工单号时收尾上一单；箱数不符（漏箱 / 多箱）报警。')
    body(doc, '· 报警：以上 4 个报警点全部接入现场现有声光报警（灯塔 / 蜂鸣）。')
    body(doc, '说明：以上为初步方案，最终以贵司对第二节问题的确认为准。确认后我们再出详细的实施与验收方案。',
         size=10, bold=True, color=RGBColor(0x55, 0x55, 0x55))

    doc.save(OUT_DOCX)
    print('docx saved:', OUT_DOCX)


if __name__ == '__main__':
    build()
