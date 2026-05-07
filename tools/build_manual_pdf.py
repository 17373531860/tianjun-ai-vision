"""
把 docs/软件操作手册.md 渲染为美观的 PDF。

流程：
  1. 读 md (跳过开头 BOM)
  2. 用 markdown lib 渲染为 HTML (启用 tables / fenced_code / toc / sane_lists 扩展)
  3. 套用自定义美化 CSS (现代风、中英文字体、表格条纹、提示框、代码块、TOC、页眉页脚)
  4. 用 Playwright Chromium headless 打印为 PDF (含目录、页码)

输出: docs/软件操作手册.pdf
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from datetime import datetime

import markdown
from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_MD = PROJECT_ROOT / "docs" / "软件操作手册.md"
OUT_PDF = PROJECT_ROOT / "docs" / "软件操作手册.pdf"
OUT_HTML = PROJECT_ROOT / "docs" / "软件操作手册.html"


CSS = r"""
:root {
  --primary: #0ea5e9;
  --primary-dark: #0369a1;
  --accent: #06b6d4;
  --bg: #ffffff;
  --bg-alt: #f8fafc;
  --bg-code: #1e293b;
  --text: #1e293b;
  --text-muted: #64748b;
  --border: #e2e8f0;
  --border-strong: #cbd5e1;
  --success: #10b981;
  --warning: #f59e0b;
  --danger: #ef4444;
  --info: #3b82f6;
}

* { box-sizing: border-box; }

html { font-size: 11pt; }

body {
  margin: 0;
  padding: 0;
  font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Source Han Sans SC", "Noto Sans CJK SC", "Helvetica Neue", Arial, sans-serif;
  color: var(--text);
  line-height: 1.75;
  background: var(--bg);
  -webkit-font-smoothing: antialiased;
}

.page {
  max-width: 800px;
  margin: 0 auto;
  padding: 40px 60px;
}

/* 封面 */
.cover {
  page-break-after: always;
  height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, var(--primary-dark) 0%, var(--primary) 50%, var(--accent) 100%);
  color: white;
  text-align: center;
  padding: 60px;
}
.cover-logo {
  font-size: 96pt;
  margin-bottom: 30px;
  letter-spacing: 0.05em;
}
.cover h1 {
  font-size: 32pt;
  font-weight: 700;
  margin: 0 0 20px 0;
  border: none;
  color: white;
  text-shadow: 0 2px 8px rgba(0,0,0,0.2);
}
.cover .subtitle {
  font-size: 16pt;
  font-weight: 300;
  opacity: 0.9;
  margin-bottom: 60px;
  letter-spacing: 0.1em;
}
.cover .meta {
  border-top: 2px solid rgba(255,255,255,0.4);
  padding-top: 30px;
  font-size: 12pt;
  opacity: 0.85;
  line-height: 2.2;
}
.cover .meta strong { font-weight: 500; }

/* 目录页 */
.toc-page { page-break-after: always; padding-top: 40px; }
.toc-page h2 {
  font-size: 22pt;
  text-align: center;
  border: none;
  margin-bottom: 30px;
  padding: 0;
  color: var(--primary-dark);
}
.toc-page .toc {
  font-size: 11pt;
  line-height: 1.8;
}
.toc-page .toc ul {
  list-style: none;
  padding-left: 1.2em;
  margin: 4px 0;
}
.toc-page .toc > ul { padding-left: 0; }
.toc-page .toc a {
  color: var(--text);
  text-decoration: none;
  display: block;
  padding: 3px 0;
  border-bottom: 1px dotted transparent;
}
.toc-page .toc a:hover {
  color: var(--primary);
  border-bottom-color: var(--border);
}
.toc-page .toc > ul > li > a {
  font-weight: 600;
  color: var(--primary-dark);
  margin-top: 8px;
}

/* 标题 */
h1 {
  font-size: 24pt;
  font-weight: 700;
  color: var(--primary-dark);
  border-bottom: 3px solid var(--primary);
  padding-bottom: 12px;
  margin-top: 0;
  margin-bottom: 24px;
  page-break-before: always;
  page-break-after: avoid;
}
h1:first-of-type { page-break-before: auto; }

h2 {
  font-size: 18pt;
  font-weight: 600;
  color: var(--primary-dark);
  border-left: 5px solid var(--primary);
  padding-left: 14px;
  margin-top: 32px;
  margin-bottom: 16px;
  page-break-after: avoid;
}

h3 {
  font-size: 14pt;
  font-weight: 600;
  color: var(--text);
  margin-top: 24px;
  margin-bottom: 12px;
  padding-left: 8px;
  border-left: 3px solid var(--accent);
  page-break-after: avoid;
}

h4 {
  font-size: 12pt;
  font-weight: 600;
  color: var(--text);
  margin-top: 18px;
  margin-bottom: 8px;
  page-break-after: avoid;
}

p { margin: 8px 0 12px 0; }

/* 链接 */
a {
  color: var(--primary);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: border-color 0.2s;
}
a:hover { border-bottom-color: var(--primary); }

/* 列表 */
ul, ol { margin: 8px 0 14px 0; padding-left: 1.6em; }
li { margin: 3px 0; }
li > p { margin: 4px 0; }

/* 行内代码 */
code {
  background: #eff6ff;
  color: #1e40af;
  font-family: "SFMono-Regular", "Consolas", "Liberation Mono", Menlo, monospace;
  font-size: 0.85em;
  padding: 2px 6px;
  border-radius: 3px;
  border: 1px solid #dbeafe;
  white-space: nowrap;
}

/* 代码块 */
pre {
  background: var(--bg-code);
  color: #f1f5f9;
  padding: 14px 18px;
  border-radius: 6px;
  border-left: 4px solid var(--primary);
  overflow-x: auto;
  font-size: 9pt;
  line-height: 1.6;
  margin: 12px 0;
  page-break-inside: avoid;
}
pre code {
  background: transparent;
  border: none;
  color: inherit;
  padding: 0;
  white-space: pre;
  font-size: inherit;
}

/* 表格 */
table {
  border-collapse: collapse;
  margin: 14px 0;
  width: 100%;
  font-size: 10pt;
  page-break-inside: avoid;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  border-radius: 6px;
  overflow: hidden;
}
th {
  background: linear-gradient(135deg, var(--primary) 0%, var(--accent) 100%);
  color: white;
  font-weight: 600;
  text-align: left;
  padding: 10px 14px;
  border-right: 1px solid rgba(255,255,255,0.15);
}
th:last-child { border-right: none; }
td {
  padding: 8px 14px;
  border: 1px solid var(--border);
  vertical-align: top;
}
tr:nth-child(even) td { background: var(--bg-alt); }
tr:hover td { background: #eff6ff; }

/* 引用块 (用于提示框) */
blockquote {
  margin: 14px 0;
  padding: 12px 16px 12px 18px;
  border-left: 4px solid var(--info);
  background: #eff6ff;
  border-radius: 0 6px 6px 0;
  page-break-inside: avoid;
  color: var(--text);
}
blockquote p { margin: 4px 0; }
blockquote p:first-child { margin-top: 0; }
blockquote p:last-child { margin-bottom: 0; }

/* 不同提示样式 (按表情符号匹配) */
blockquote:has(strong:first-child:contains("⚠")),
blockquote.warn {
  border-left-color: var(--warning);
  background: #fffbeb;
}
blockquote:has(strong:first-child:contains("💡")),
blockquote.tip {
  border-left-color: var(--info);
  background: #eff6ff;
}
blockquote:has(strong:first-child:contains("✕")),
blockquote.danger {
  border-left-color: var(--danger);
  background: #fef2f2;
}

/* 分隔线 */
hr {
  border: none;
  border-top: 2px solid var(--border);
  margin: 28px 0;
}

/* 强调 */
strong { color: var(--primary-dark); font-weight: 600; }
em { color: var(--text-muted); }

/* 防止表格/代码被切到下一页 */
table, pre, blockquote { page-break-inside: avoid; }

/* 链接锚点定位 */
:target { scroll-margin-top: 30px; }
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
      <div><strong>适用</strong>　操作员 · 班组长 · 集成工程师</div>
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


def md_to_html(md_text: str) -> tuple[str, str]:
    md = markdown.Markdown(
        extensions=[
            "tables", "fenced_code", "sane_lists", "attr_list",
            "toc", "nl2br",
        ],
        extension_configs={
            "toc": {"toc_depth": "2-3"},
        },
    )
    body = md.convert(md_text)
    toc = md.toc
    return body, toc


def post_process_blockquotes(html: str) -> str:
    """根据 blockquote 内首个 emoji 给它打 class, 让 CSS 用不同颜色"""
    def classify(m):
        inner = m.group(1)
        cls = "blockquote"
        if "⚠" in inner[:30] or "注意" in inner[:30]:
            cls = "blockquote warn"
        elif "💡" in inner[:30] or "提示" in inner[:30]:
            cls = "blockquote tip"
        elif "✕" in inner[:30] or "❌" in inner[:30]:
            cls = "blockquote danger"
        return f'<blockquote class="{cls.split(" ", 1)[1] if " " in cls else ""}">' + inner + '</blockquote>'
    return re.sub(r'<blockquote>([\s\S]*?)</blockquote>', classify, html)


def main():
    if not SRC_MD.exists():
        print(f"找不到源文件: {SRC_MD}", file=sys.stderr)
        sys.exit(1)

    # 读 md, 跳过 BOM
    raw = SRC_MD.read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    md_text = raw.decode("utf-8")

    print(f"[1/4] 读取 md, 长度 {len(md_text)} 字符")

    # 提取 H1 标题作为 title (带容错)
    title = "操作手册"
    for line in md_text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break

    # 提取版本号 (查 "**版本**: vX.Y.Z")
    version = "v3.5.1"
    m = re.search(r"\*\*版本\*\*[：:]\s*(v?[\d.]+)", md_text)
    if m:
        version = m.group(1)
        if not version.startswith("v"):
            version = "v" + version

    # 把已有目录段从正文剔除 (我们用 markdown 的 [TOC] 自动生成的代替)
    md_text = re.sub(
        r"^## 📖 目录[\s\S]*?^---\s*$",
        "",
        md_text,
        count=1,
        flags=re.MULTILINE,
    )

    body, toc = md_to_html(md_text)
    body = post_process_blockquotes(body)
    print(f"[2/4] md → html, body {len(body)} 字符 / toc {len(toc)} 字符")

    html = HTML_TEMPLATE.format(
        css=CSS,
        title=title,
        brand="天军科技 AI 视觉检测系统",
        subtitle="软件操作手册",
        version=version,
        date=datetime.now().strftime("%Y 年 %m 月"),
        toc=toc,
        body=body,
    )
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"[3/4] 写入 HTML: {OUT_HTML}")

    # Chromium print-to-pdf
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
            header_template='<div style="font-size:8pt;color:#94a3b8;width:100%;padding:0 15mm;">天军科技 AI 视觉检测系统 操作手册</div>',
            footer_template=(
                '<div style="font-size:8pt;color:#94a3b8;width:100%;padding:0 15mm;'
                'display:flex;justify-content:space-between;">'
                '<span>v3.5.1 · 2026</span>'
                '<span>第 <span class="pageNumber"></span> / <span class="totalPages"></span> 页</span>'
                '</div>'
            ),
        )
        browser.close()
    print(f"[4/4] PDF 已生成: {OUT_PDF} ({OUT_PDF.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
