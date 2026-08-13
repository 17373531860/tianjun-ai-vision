#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 docs/软件操作手册.md 生成 美化 HTML → PDF（google-chrome headless 打印）。

本轮重做重点：视觉美化（封面 / 配色 / 信息框分级 / 表格与代码框 / 按钮芯片）。
内容仍以 docs/软件操作手册.md 为唯一真源，本脚本只负责"好看地排版"。

用法：python3 gen_软件操作手册.py
依赖：python-markdown、google-chrome、系统中文字体（Noto Sans CJK SC）。
docx 另用 pandoc 生成（本机暂无 pandoc 时跳过）。
"""

import os
import re
import sys
import subprocess
import tempfile

import markdown

# 脚本已归位 scripts/docgen/, 仓库根 = 上两级
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MD = os.path.join(BASE, 'docs', '软件操作手册.md')
PDF = os.path.join(BASE, 'docs', '软件操作手册.pdf')

VERSION = 'v3.51.0'
UPDATED = '2026 年 8 月'


# ── GitHub 风格锚点（让 md 里手写的目录内链在 PDF 中可跳转）──────────────────
def gh_slug(value, separator):
    v = value.strip().lower()
    v = re.sub(r'[^\w \-]', '', v, flags=re.UNICODE)
    v = v.replace(' ', '-')
    return v


# ── 配色与排版主题 ────────────────────────────────────────────────────────────
# 主色：深蓝(品牌) + 亮蓝(强调) + 青绿(点缀/成功)，告警/危险走暖色。
CSS = r"""
:root{
  --p:#0D47A1;        /* 品牌深蓝 */
  --p2:#1565C0;       /* 链接亮蓝 */
  --p-soft:#E8F0FE;   /* 浅蓝底 */
  --p-line:#C7D8F2;   /* 蓝色描边 */
  --ink:#1f2430;      /* 正文墨色 */
  --muted:#6b7280;    /* 次要文字 */
  --teal:#0F9D8C;     /* 青绿点缀 */
}
@page { size: A4; margin: 16mm 14mm 15mm 14mm; }
* { box-sizing: border-box; }
html,body{ margin:0; }
body{
  font-family:"Noto Sans CJK SC","WenQuanYi Zen Hei","Microsoft YaHei",sans-serif;
  font-size:10.5pt; line-height:1.78; color:var(--ink);
  -webkit-print-color-adjust:exact; print-color-adjust:exact;
}

/* ── 封面 ─────────────────────────────────────────────── */
.cover{
  position:relative; height:262mm; border-radius:14px; overflow:hidden;
  background:
     radial-gradient(120% 80% at 85% 8%, rgba(15,157,140,.28) 0%, rgba(15,157,140,0) 42%),
     linear-gradient(155deg,#0a3a86 0%, #0D47A1 45%, #1565C0 100%);
  color:#fff; page-break-after:always;
  box-shadow:inset 0 0 0 1px rgba(255,255,255,.10);
}
.cover .deco{ position:absolute; border-radius:50%; opacity:.10; background:#fff; }
.cover .d1{ width:330px; height:330px; right:-90px; top:-90px; }
.cover .d2{ width:200px; height:200px; left:-70px; bottom:120px; opacity:.07; }
.cover .grid{
  position:absolute; inset:0; opacity:.10;
  background-image:linear-gradient(rgba(255,255,255,.6) 1px,transparent 1px),
                   linear-gradient(90deg,rgba(255,255,255,.6) 1px,transparent 1px);
  background-size:26px 26px; mask-image:linear-gradient(180deg,#000,transparent 55%);
}
.cover .inner{ position:absolute; inset:0; padding:26mm 22mm; display:flex; flex-direction:column; }
.cover .brand{ display:flex; align-items:center; gap:12px; font-size:12.5pt; letter-spacing:.5px; }
.cover .brand .dot{ width:13px; height:13px; border-radius:4px; background:var(--teal); box-shadow:0 0 0 4px rgba(15,157,140,.25); }
.cover .brand small{ display:block; font-size:8.5pt; color:rgba(255,255,255,.66); letter-spacing:2px; margin-top:2px; }
.cover .mid{ margin-top:auto; }
.cover .kicker{ font-size:11pt; color:rgba(255,255,255,.78); letter-spacing:4px; margin-bottom:10px; }
.cover h1.title{ font-size:46pt; line-height:1.12; font-weight:800; margin:0;
  color:#ffffff; text-shadow:0 2px 14px rgba(0,0,0,.18); }
.cover h1.title .accent{ color:#8FE3D8; }
.cover .sub{ margin-top:14px; font-size:13pt; color:rgba(255,255,255,.85); letter-spacing:1px; }
.cover .rule{ width:78px; height:5px; border-radius:3px; background:var(--teal); margin:22px 0 0; }
.cover .meta{ margin-top:auto; display:flex; gap:10px; flex-wrap:wrap; }
.cover .chip{
  background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.22);
  border-radius:10px; padding:9px 14px; font-size:9.5pt; backdrop-filter:blur(2px);
}
.cover .chip b{ display:block; font-size:11.5pt; margin-top:2px; color:#fff; }
.cover .chip span{ color:rgba(255,255,255,.7); font-size:8.2pt; letter-spacing:1px; }

/* ── 标题层级 ─────────────────────────────────────────── */
h1,h2,h3,h4{ font-weight:700; line-height:1.34; }
h1{ font-size:22pt; color:var(--p); text-align:center; margin:6pt 0 4pt; }
h2{
  font-size:15.5pt; color:var(--p); margin:20pt 0 10pt; padding:7pt 12pt 7pt 14pt;
  background:linear-gradient(90deg,var(--p-soft),rgba(232,240,254,0));
  border-left:6px solid var(--p); border-radius:8px;
  page-break-before:always; page-break-after:avoid;
}
h2:first-of-type{ page-break-before:avoid; }
h3{
  font-size:12.5pt; color:var(--p); margin:15pt 0 5pt; padding-left:11px;
  border-left:4px solid var(--teal); page-break-after:avoid;
}
h4{ font-size:11pt; color:#11305f; margin:11pt 0 4pt; page-break-after:avoid; }
h4::before{ content:"▸ "; color:var(--teal); font-weight:700; }

p{ margin:5pt 0; }
ul,ol{ margin:5pt 0; padding-left:22pt; }
li{ margin:2pt 0; }
li::marker{ color:var(--p2); }
a{ color:var(--p2); text-decoration:none; }
strong{ color:#0b3a85; }
hr{ border:none; border-top:1px dashed #c5cfdd; margin:13pt 0; }

/* 按钮 / 菜单名「…」做成芯片 */
.ui{
  background:#eef4ff; border:1px solid var(--p-line); color:#11458f;
  border-radius:5px; padding:0 5px; font-size:9.3pt; font-weight:600; white-space:nowrap;
}

/* ── 目录卡片 ─────────────────────────────────────────── */
.toc-card{
  border:1px solid var(--p-line); background:#fbfdff; border-radius:12px;
  padding:6pt 16pt 10pt; margin:6pt 0 4pt;
}
.toc-card ol,.toc-card ul{ padding-left:20pt; }
.toc-card a{ color:#143f86; }

/* ── 表格 ─────────────────────────────────────────────── */
table{
  border-collapse:separate; border-spacing:0; width:100%; margin:9pt 0; font-size:9pt;
  border:1px solid var(--p-line); border-radius:10px; overflow:hidden; page-break-inside:auto;
}
tr{ page-break-inside:avoid; }
th,td{ border-bottom:1px solid #e3eaf4; border-right:1px solid #eef2f8; padding:5pt 7pt; text-align:left; vertical-align:top; }
th{ background:linear-gradient(180deg,#E3EDFB,#D6E4F7); color:#0b3a85; font-weight:700; border-bottom:1.5px solid var(--p-line); }
tr td:last-child,tr th:last-child{ border-right:none; }
tbody tr:last-child td{ border-bottom:none; }
tbody tr:nth-child(even){ background:#F5F9FF; }

/* ── 代码 / ASCII 图 ───────────────────────────────────── */
code{
  font-family:"DejaVu Sans Mono","Noto Sans Mono CJK SC",monospace;
  background:#eef1f5; color:#9b264b; padding:1px 4px; border-radius:3px; font-size:8.8pt;
}
pre{
  background:#0f2747; color:#dbe7fb; border:1px solid #11335f; border-radius:8px;
  padding:9pt 12pt; overflow-x:auto; font-size:8pt; line-height:1.5; page-break-inside:avoid;
  box-shadow:inset 3px 0 0 var(--teal);
}
pre code{ background:none; color:inherit; padding:0; font-size:8pt; white-space:pre; }

/* ── 信息框分级（蓝=提示 / 黄=警告 / 绿=成功 / 红=危险）──── */
blockquote{
  margin:7pt 0; padding:7pt 12pt 7pt 14pt; border-left:5px solid var(--p2);
  background:#F0F4FF; color:var(--ink); border-radius:0 8px 8px 0; page-break-inside:avoid;
}
blockquote p{ margin:2pt 0; }
blockquote.warn{ border-left-color:#E0A800; background:#FFF7E0; }
blockquote.ok{   border-left-color:#1f9d55; background:#EAF7EF; }
blockquote.bad{  border-left-color:#D64545; background:#FCEDED; }

/* 居中的"标题下副标题"块（首段 blockquote）保持低调 */
h1 + blockquote{ text-align:center; border:none; background:none; color:var(--muted); }
"""

HTML_TMPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>天军机器人（苏州）有限公司 · SOP-视觉AI行为分析智能体 操作手册</title>
<style>{css}</style>
</head>
<body>
{cover}
{body}
</body>
</html>
"""

COVER = """
<section class="cover">
  <div class="deco d1"></div>
  <div class="deco d2"></div>
  <div class="grid"></div>
  <div class="inner">
    <div class="brand">
      <span class="dot"></span>
      <div>天军机器人（苏州）有限公司
        <small>TYENJUN&nbsp;Robotics&nbsp;(Suzhou)&nbsp;Co.,&nbsp;Ltd.&nbsp;·&nbsp;www.tyenjunai.com</small>
      </div>
    </div>
    <div class="mid">
      <div class="kicker">SOP-视觉AI行为分析智能体</div>
      <h1 class="title">操作<span class="accent">手册</span></h1>
      <div class="sub">User Operation Manual　|　从安装到上线的完整指引</div>
      <div class="rule"></div>
    </div>
    <div class="meta">
      <div class="chip"><span>版本 VERSION</span><b>{ver}</b></div>
      <div class="chip"><span>更新 UPDATED</span><b>{upd}</b></div>
      <div class="chip"><span>适用 AUDIENCE</span><b>操作员 / 技术人员</b></div>
      <div class="chip"><span>密级 LEVEL</span><b>内部资料</b></div>
    </div>
  </div>
</section>
"""


def style_ui_chips(html_fragment):
    """把正文里的「按钮/菜单名」包成芯片样式，跳过 <pre> 代码块。"""
    parts = re.split(r'(<pre>.*?</pre>)', html_fragment, flags=re.DOTALL)
    for i, seg in enumerate(parts):
        if seg.startswith('<pre>'):
            continue
        parts[i] = re.sub(r'「([^」<>\n]{1,24})」',
                          r'<span class="ui">「\1」</span>', seg)
    return ''.join(parts)


def classify_callouts(html_fragment):
    """按 blockquote 首字符的 emoji 给信息框上色分级。"""
    rules = [
        (r'<blockquote>\s*(<p>\s*(?:⚠️|⚠|🚧))', 'warn'),
        (r'<blockquote>\s*(<p>\s*(?:✅|🎉|👍|✔️|✔))', 'ok'),
        (r'<blockquote>\s*(<p>\s*(?:❌|🚫|⛔|🛑))', 'bad'),
    ]
    for pat, cls in rules:
        html_fragment = re.sub(pat, rf'<blockquote class="{cls}">\1', html_fragment)
    return html_fragment


def wrap_toc(html_fragment):
    """把"目录"标题后的第一个有序列表包进卡片。"""
    m = re.search(r'(<h2[^>]*>[^<]*目录[^<]*</h2>)\s*(<ol>.*?</ol>)',
                  html_fragment, flags=re.DOTALL)
    if not m:
        return html_fragment
    block = m.group(1) + '<div class="toc-card">' + m.group(2) + '</div>'
    return html_fragment[:m.start()] + block + html_fragment[m.end():]


def main():
    with open(MD, encoding='utf-8') as f:
        md_text = f.read()

    # 去掉 md 顶部"标题 + 版本 blockquote + 第一条分隔线"——由封面替代，避免重复
    md_text = re.sub(r'^\s*#\s.*?\n-{3,}\s*\n', '', md_text, count=1, flags=re.DOTALL)

    md = markdown.Markdown(
        extensions=['tables', 'fenced_code', 'sane_lists', 'attr_list',
                    'toc', 'md_in_html'],
        extension_configs={'toc': {'slugify': gh_slug}},
    )
    body = md.convert(md_text)

    body = classify_callouts(body)
    body = wrap_toc(body)
    body = style_ui_chips(body)

    html = HTML_TMPL.format(
        css=CSS,
        cover=COVER.format(ver=VERSION, upd=UPDATED),
        body=body,
    )

    tmp_html = os.path.join(tempfile.gettempdir(), '软件操作手册_build.html')
    with open(tmp_html, 'w', encoding='utf-8') as f:
        f.write(html)

    chrome = subprocess.run(
        ['bash', '-lc', 'command -v google-chrome || command -v google-chrome-stable'],
        capture_output=True, text=True).stdout.strip()
    if not chrome:
        # macOS Chrome / Playwright Chromium 兜底（开发机跨平台出 PDF）
        import glob
        candidates = [
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '/Applications/Chromium.app/Contents/MacOS/Chromium',
        ] + sorted(glob.glob(os.path.expanduser(
            '~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/'
            '*.app/Contents/MacOS/*'
        )), reverse=True)
        chrome = next((c for c in candidates if os.path.exists(c)), '')
    if not chrome:
        print('未找到 google-chrome / Chromium', file=sys.stderr)
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
