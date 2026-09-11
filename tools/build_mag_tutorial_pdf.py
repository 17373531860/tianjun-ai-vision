"""把 docs/customers/装弹计数/装弹计数Demo教程_自训YOLO逐压计数.md 渲染为 PDF。

复用 build_manual_pdf 的 CSS 与 md→html 管线，HTML 落在 md 同目录以便相对图片路径生效。
用法：tianjun env 的 python 直接跑本文件。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_manual_pdf import CSS, md_to_html, post_process_blockquotes  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOC_DIR = PROJECT_ROOT / "docs" / "customers" / "装弹计数"
SRC_MD = DOC_DIR / "装弹计数Demo教程_自训YOLO逐压计数.md"
OUT_HTML = DOC_DIR / "装弹计数Demo教程_自训YOLO逐压计数.html"
OUT_PDF = DOC_DIR / "装弹计数Demo教程_自训YOLO逐压计数.pdf"

EXTRA_CSS = """
img { max-width: 100%; border-radius: 8px; border: 1px solid var(--border);
      margin: 10px 0; page-break-inside: avoid; }
pre, pre code { white-space: pre-wrap; word-break: break-all; }
"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <style>{css}</style>
</head>
<body>
  <div class="cover">
    <div class="cover-logo">🎯</div>
    <h1>{brand}</h1>
    <div class="subtitle">{subtitle}</div>
    <div class="meta">
      <div><strong>版本</strong>　{version}</div>
      <div><strong>适用</strong>　现场/集成工程师 · 客户演示</div>
      <div><strong>更新</strong>　{date}</div>
    </div>
  </div>
  <div class="toc-page">
    <h2>📖 目录</h2>
    <div class="toc">{toc}</div>
  </div>
  <div class="page">{body}</div>
</body>
</html>
"""


def main():
    md_text = SRC_MD.read_text(encoding="utf-8")
    title = next((ln[2:].strip() for ln in md_text.splitlines()
                  if ln.startswith("# ")), "装弹计数 Demo 教程")
    body, toc = md_to_html(md_text)
    body = post_process_blockquotes(body)
    html = HTML_TEMPLATE.format(
        css=CSS + EXTRA_CSS,
        title=title,
        brand="天军科技 AI 视觉检测系统",
        subtitle="装弹逐压计数 Demo · 自训 YOLO 模型工程师教程",
        version="v1.0",
        date=datetime.now().strftime("%Y 年 %m 月"),
        toc=toc,
        body=body,
    )
    OUT_HTML.write_text(html, encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(OUT_HTML.as_uri(), wait_until="networkidle")
        page.pdf(
            path=str(OUT_PDF),
            format="A4",
            print_background=True,
            margin={"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"},
            display_header_footer=True,
            header_template='<div style="font-size:8pt;color:#94a3b8;width:100%;'
                            'padding:0 15mm;">天军科技 AI 视觉检测系统 · 装弹逐压计数 Demo 教程</div>',
            footer_template=(
                '<div style="font-size:8pt;color:#94a3b8;width:100%;padding:0 15mm;'
                'display:flex;justify-content:space-between;">'
                '<span>v1.0 · 2026</span>'
                '<span>第 <span class="pageNumber"></span> / <span class="totalPages"></span> 页</span>'
                '</div>'
            ),
        )
        browser.close()
    print(f"PDF 已生成: {OUT_PDF} ({OUT_PDF.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
