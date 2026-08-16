#!/usr/bin/env python3
"""捷昌 B 站标注规范 Markdown → 带图 PDF。

用法: python scripts/docgen/gen_jc_annotation_spec_pdf.py <md路径>
产出: 与 md 同目录同名 .pdf（图片按 md 内相对路径解析为 file:// 绝对路径）。
渲染链: markdown(tables) → HTML(内联 CSS, CJK 字体) → Playwright chromium 打印。
"""
import os
import re
import sys

import markdown

CSS = """
@page { size: A4; margin: 18mm 15mm; }
* { box-sizing: border-box; }
body { font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
       font-size: 10.5pt; line-height: 1.75; color: #1a1a1a; }
h1 { font-size: 17pt; border-bottom: 3px solid #2563eb; padding-bottom: 8px; }
h2 { font-size: 13.5pt; color: #1e40af; border-left: 5px solid #2563eb;
     padding-left: 8px; margin-top: 22px; page-break-after: avoid; }
h3 { font-size: 11.5pt; color: #334155; margin-top: 16px; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9.5pt;
        page-break-inside: avoid; }
th, td { border: 1px solid #cbd5e1; padding: 4px 8px; text-align: left; }
th { background: #eff6ff; }
blockquote { background: #fefce8; border-left: 4px solid #eab308; margin: 10px 0;
             padding: 6px 12px; page-break-inside: avoid; }
code { background: #f1f5f9; padding: 1px 5px; border-radius: 3px;
       font-family: Menlo, monospace; font-size: 9pt; }
img { max-width: 100%; border: 1px solid #e2e8f0; margin: 6px 0;
      page-break-inside: avoid; }
li { margin: 3px 0; }
hr { border: none; border-top: 1px solid #cbd5e1; }
"""


def build_pdf(md_path: str) -> str:
    md_path = os.path.abspath(md_path)
    base_dir = os.path.dirname(md_path)
    with open(md_path, encoding="utf-8") as f:
        text = f.read()
    html_body = markdown.markdown(text, extensions=["tables", "fenced_code"])
    # 图片相对路径 → file:// 绝对路径, 空格等字符做最小转义
    def fix_src(m):
        src = m.group(1)
        if src.startswith(("http://", "https://", "file://", "/")):
            return m.group(0)
        return 'src="file://%s"' % os.path.join(base_dir, src).replace(" ", "%20")
    html_body = re.sub(r'src="([^"]+)"', fix_src, html_body)
    html = f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{html_body}</body></html>"

    out_pdf = os.path.splitext(md_path)[0] + ".pdf"
    tmp_html = os.path.splitext(md_path)[0] + ".tmp.html"
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(html)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("file://" + tmp_html.replace(" ", "%20"))
        page.wait_for_load_state("networkidle")
        page.pdf(path=out_pdf, format="A4", print_background=True,
                 margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        browser.close()
    os.remove(tmp_html)
    return out_pdf


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("用法: gen_jc_annotation_spec_pdf.py <md路径>")
    print("生成:", build_pdf(sys.argv[1]))
