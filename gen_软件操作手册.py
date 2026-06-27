#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 docs/软件操作手册.md 生成 美化 HTML → PDF（google-chrome headless 打印）。
复刻现有 PDF 的生成链路（Creator: Chromium / Producer: Skia/PDF, A4）。
docx 另用 pandoc 生成（见同目录 README 注释 / 末尾命令）。

用法：python3 gen_软件操作手册.py
依赖：python-markdown、google-chrome、系统中文字体（Noto Sans CJK）。
"""

import os
import re
import sys
import subprocess
import tempfile

import markdown

BASE = os.path.dirname(os.path.abspath(__file__))
MD = os.path.join(BASE, 'docs', '软件操作手册.md')
PDF = os.path.join(BASE, 'docs', '软件操作手册.pdf')


# ── GitHub 风格锚点（让 md 里手写的目录内链在 PDF 中可跳转）──────────────────
def gh_slug(value, separator):
    v = value.strip().lower()
    # 保留：unicode 字（含中文）/ 数字 / 下划线 / 空格 / 连字符；其余（标点、emoji）剔除
    v = re.sub(r'[^\w \-]', '', v, flags=re.UNICODE)
    v = v.replace(' ', '-')
    return v


# ── CSS（A4 + 中文 + 蓝色主题，对齐 gen_doc 系列视觉）────────────────────────
CSS = r"""
@page { size: A4; margin: 18mm 15mm 16mm 15mm; }
* { box-sizing: border-box; }
body {
  font-family: "Noto Sans CJK SC", "WenQuanYi Zen Hei", "Microsoft YaHei", sans-serif;
  font-size: 10.5pt; line-height: 1.72; color: #1a1a1a; margin: 0;
}
h1, h2, h3, h4 { font-weight: 700; line-height: 1.35; }
h1 { font-size: 25pt; color: #0D47A1; text-align: center; margin: 6pt 0 4pt; }
h1 + blockquote { text-align: center; border: none; background: none; color: #666; }
h2 {
  font-size: 16pt; color: #0D47A1; margin: 22pt 0 8pt;
  padding-bottom: 4pt; border-bottom: 2px solid #0D47A1;
  page-break-before: always; page-break-after: avoid;
}
h2:first-of-type { page-break-before: avoid; }
h3 { font-size: 12.5pt; color: #0D47A1; margin: 14pt 0 5pt; page-break-after: avoid; }
h4 { font-size: 11pt; color: #1A1A1A; margin: 10pt 0 4pt; page-break-after: avoid; }
p { margin: 4pt 0; }
ul, ol { margin: 4pt 0; padding-left: 22pt; }
li { margin: 1.5pt 0; }
a { color: #1565C0; text-decoration: none; }
strong { color: #0b3a85; }
hr { border: none; border-top: 1px solid #d0d7de; margin: 12pt 0; }

table {
  border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9pt;
  page-break-inside: auto;
}
tr { page-break-inside: avoid; page-break-after: auto; }
th, td { border: 1px solid #c7d2e0; padding: 4pt 6pt; text-align: left; vertical-align: top; }
th { background: #D6E4F7; color: #0b3a85; font-weight: 700; }
tbody tr:nth-child(even) { background: #F5F9FF; }

code {
  font-family: "DejaVu Sans Mono", "Noto Sans Mono CJK SC", monospace;
  background: #eef1f5; padding: 1px 4px; border-radius: 3px; font-size: 8.8pt;
}
pre {
  background: #f6f8fa; border: 1px solid #d8dee4; border-radius: 5px;
  padding: 8pt 10pt; overflow-x: auto; font-size: 8pt; line-height: 1.45;
  page-break-inside: avoid;
}
pre code { background: none; padding: 0; font-size: 8pt; white-space: pre; }

blockquote {
  margin: 6pt 0; padding: 6pt 10pt; border-left: 4px solid #1565C0;
  background: #F0F4FF; color: #1a1a1a; border-radius: 0 4px 4px 0;
  page-break-inside: avoid;
}
blockquote.warn { border-left-color: #E0A800; background: #FFF7E0; }
blockquote p { margin: 2pt 0; }
"""

HTML_TMPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>天军科技 AI 视觉检测系统 - 操作手册</title>
<style>{css}</style>
</head>
<body>
{body}
</body>
</html>
"""


def main():
    with open(MD, encoding='utf-8') as f:
        md_text = f.read()

    md = markdown.Markdown(
        extensions=['tables', 'fenced_code', 'sane_lists', 'attr_list',
                    'toc', 'md_in_html'],
        extension_configs={'toc': {'slugify': gh_slug}},
    )
    body = md.convert(md_text)

    # 告警类 blockquote（含 ⚠ / ⚠️）标黄；其余保持蓝色信息框
    body = re.sub(
        r'<blockquote>\s*(<p>\s*(?:⚠️|⚠))',
        r'<blockquote class="warn">\1',
        body,
    )

    html = HTML_TMPL.format(css=CSS, body=body)

    tmp_html = os.path.join(tempfile.gettempdir(), '软件操作手册_build.html')
    with open(tmp_html, 'w', encoding='utf-8') as f:
        f.write(html)

    chrome = (subprocess.run(['bash', '-lc', 'command -v google-chrome || command -v google-chrome-stable'],
                             capture_output=True, text=True).stdout.strip())
    if not chrome:
        print('未找到 google-chrome', file=sys.stderr)
        sys.exit(1)

    cmd = [
        chrome, '--headless=new', '--disable-gpu', '--no-sandbox',
        '--no-pdf-header-footer',
        f'--print-to-pdf={PDF}',
        f'file://{tmp_html}',
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not os.path.exists(PDF):
        print('PDF 生成失败：\n' + r.stderr, file=sys.stderr)
        sys.exit(1)
    print(f'已生成 PDF：{PDF}（{os.path.getsize(PDF)//1024} KB）')
    print(f'中间 HTML：{tmp_html}')


if __name__ == '__main__':
    main()
