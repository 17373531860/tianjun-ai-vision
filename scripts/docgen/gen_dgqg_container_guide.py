# -*- coding: utf-8 -*-
"""生成《东莞群光 包装工位「容器定界周期」配置指南》—— HTML → Chromium → PDF。

面向现场工程师: 为什么之前一直 NG/卡住、升级 v3.59.0 后怎么配「容器定界周期」。
截图来源: 本机 v3.59 开发栈「东莞群光-容器定界验证」项目实机截图（配置指南assets/）。
用法: python scripts/docgen/gen_dgqg_container_guide.py
产物: docs/customers/东莞群光/东莞群光_容器定界周期_配置指南.html / .pdf
"""
import base64
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHOTS = os.path.join(ROOT, "docs", "customers", "东莞群光", "配置指南assets")
OUT_HTML = os.path.join(ROOT, "docs", "customers", "东莞群光",
                        "东莞群光_容器定界周期_配置指南.html")
OUT_PDF = os.path.join(ROOT, "docs", "customers", "东莞群光",
                       "东莞群光_容器定界周期_配置指南.pdf")


def img(name):
    path = os.path.join(SHOTS, name)
    if not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def F(name, caption):
    src = img(name)
    if not src:
        return f'<div class="missing">[缺图: {name}]</div>'
    return (f'<figure><img src="{src}" alt="{caption}"/>'
            f'<figcaption>{caption}</figcaption></figure>')


CSS = """
:root{--brand:#0EA5E9;--brand-d:#0369A1;--ink:#1f2937;--muted:#6b7280;
  --line:#e5e7eb;--bg-soft:#f1f7fd;--red:#dc2626;--ok:#16a34a;}
*{box-sizing:border-box;}
@page{size:A4;margin:0;}
body{font-family:"Noto Sans CJK SC","PingFang SC","Microsoft YaHei",sans-serif;
  color:var(--ink);margin:0;font-size:14px;line-height:1.75;background:#fff;}
.page{max-width:980px;margin:0 auto;padding:0 32px;}
.cover{height:296mm;display:flex;flex-direction:column;justify-content:center;
  background:linear-gradient(135deg,#0c4a6e 0%,#0EA5E9 100%);color:#fff;
  padding:0 70px;page-break-after:always;break-after:page;}
.cover .kicker{font-size:15px;letter-spacing:6px;opacity:.85;margin-bottom:18px;}
.cover h1{font-size:42px;margin:0 0 6px;font-weight:800;line-height:1.3;}
.cover h2{font-size:22px;margin:0 0 40px;font-weight:400;opacity:.95;}
.cover .meta{background:rgba(255,255,255,.12);border-radius:14px;padding:24px 28px;max-width:640px;}
.cover .meta div{display:flex;padding:7px 0;border-bottom:1px solid rgba(255,255,255,.18);}
.cover .meta div:last-child{border-bottom:none;}
.cover .meta b{width:130px;font-weight:600;opacity:.9;}
.cover .note{margin-top:40px;font-size:13px;opacity:.85;line-height:1.7;max-width:640px;}
section{padding-top:22px;}
h2.sec{font-size:23px;color:var(--brand-d);border-left:6px solid var(--brand);
  padding:4px 0 4px 14px;margin:22px 0 14px;break-after:avoid;}
h3.sub{font-size:16.5px;margin:20px 0 8px;padding-bottom:6px;
  border-bottom:1px dashed var(--line);break-after:avoid;}
p{margin:8px 0;}
table{border-collapse:collapse;width:100%;margin:10px 0;font-size:13px;}
th,td{border:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top;}
th{background:var(--bg-soft);color:var(--brand-d);white-space:nowrap;}
figure{margin:14px 0;page-break-inside:avoid;}
figure img{width:100%;border:1px solid var(--line);border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.08);}
figcaption{font-size:12.5px;color:var(--muted);margin-top:6px;text-align:center;}
.callout{background:var(--bg-soft);border-left:4px solid var(--brand);border-radius:0 8px 8px 0;
  padding:10px 14px;margin:12px 0;font-size:13.5px;}
.warn{background:#fef2f2;border-left-color:var(--red);}
.okc{background:#f0fdf4;border-left-color:var(--ok);}
code{background:#f3f4f6;border-radius:4px;padding:1px 6px;font-size:12.5px;
  font-family:Menlo,Consolas,monospace;}
.ok-pill{background:#dcfce7;color:#166534;border-radius:99px;padding:1px 10px;font-size:12px;}
.ng-pill{background:#fee2e2;color:#991b1b;border-radius:99px;padding:1px 10px;font-size:12px;}
footer{margin:34px 0 26px;padding-top:12px;border-top:1px solid var(--line);
  color:var(--muted);font-size:12px;text-align:center;}
.missing{color:var(--red);border:1px dashed var(--red);padding:8px;border-radius:6px;margin:10px 0;}
"""


def build_html():
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"/>
<title>东莞群光 容器定界周期 配置指南</title><style>{CSS}</style></head><body>

<div class="cover">
  <div class="kicker">天军 AI 视觉检测系统 · 现场配置指南</div>
  <h1>包装工位「容器定界周期」<br/>配置指南</h1>
  <h2>东莞群光 · 说明书/电池装盒工位</h2>
  <div class="meta">
    <div><b>适用版本</b><span>v3.59.0 及以上（低于此版本无「周期定界」选项）</span></div>
    <div><b>适用工位</b><span>包装盒常驻画面、盒内完成放说明书/放电池等动作</span></div>
    <div><b>文档日期</b><span>2026-09-17</span></div>
    <div><b>面向读者</b><span>现场部署/调试工程师</span></div>
  </div>
  <div class="note">本指南解答两个问题：① 之前为什么一直 NG、NG 后还"卡住不结算"；② 升级 v3.59.0 后如何用「容器定界周期」正确配置本工位。文中截图为真实软件界面。</div>
</div>

<div class="page">

<section>
  <h2 class="sec">一、背景：之前为什么一直 NG</h2>
  <p>本工位的业务节奏是：<b>包装盒放上工作台 → 周期开始 → 工人放入说明书、大号电池、小号电池（顺序不限，允许重复拿放）→ 盒子拿走 → 立即出 OK/NG</b>。</p>
  <p>旧版本没有与这个节奏对应的模式。用检测模式把「包装盒」配成结算步骤时，包装盒<b>常驻画面</b>，每次动作交替后都会被系统再记一次账，结算时必然报「重复步骤: 包装盒」判 NG——这是模式机制层面的冲突，<b>调参数解决不了</b>。此前现场反馈的"一直 NG、NG 后卡住不出下一个结果"就是这个原因。</p>
  <div class="callout okc"><b>v3.59.0 新增「容器定界周期」专治此场景</b>：包装盒只做"周期边界"（到位开周期、离场即结算），不再参与动作记账；周期内三个动作<b>做全即 OK</b>（顺序不限、重复不罚），缺任一动作 NG 并给出缺了哪步。升级后按第三节配置即可。</div>
</section>

<section>
  <h2 class="sec">二、升级软件</h2>
  <p>安装 <b>v3.59.0</b> 安装包（覆盖安装，项目配置与历史数据自动保留）。升级后在「项目配置 → 逻辑设置 → 自定义模式」下能看到「<b>周期定界</b>」下拉即为升级成功。</p>
</section>

<section>
  <h2 class="sec">三、配置步骤（三步完成）</h2>

  <h3 class="sub">3.1 步骤设置：只启用三个动作，其余关闭</h3>
  <table>
    <tr><th>模型标签</th><th>启用开关</th><th>说明</th></tr>
    <tr><td>拿取说明书</td><td><span class="ok-pill">开</span></td><td>动作步骤，参与完备判定</td></tr>
    <tr><td>拿取大号电池 / 拿取小号电池</td><td><span class="ok-pill">开</span></td><td>由「同标签区域拆分」从 拿取电池 拆出（锚点=电池盒），保持现有拆分规则</td></tr>
    <tr><td>包装盒</td><td><span class="ng-pill">关</span></td><td><b>必须关闭</b>——它是周期边界，不参与"动作做没做全"的判定</td></tr>
    <tr><td>拿取电池 / 电池盒</td><td><span class="ng-pill">关</span></td><td>拿取电池 已被拆分替代；电池盒 只做拆分锚点</td></tr>
  </table>
  {F("dgqg_step3_steps.png", "图 3-1 步骤设置：仅三个动作步骤启用；包装盒/电池盒/拿取电池 关闭；下方为电池拆分规则")}

  <h3 class="sub">3.2 逻辑设置：自定义模式 + 容器定界</h3>
  <table>
    <tr><th>配置项</th><th>填什么</th><th>说明</th></tr>
    <tr><td>逻辑模式</td><td>自定义模式</td><td></td></tr>
    <tr><td>基于模式</td><td>基于检测模式</td><td>动作无序完备判定的基础</td></tr>
    <tr><td>检测配置勾选</td><td>拿取说明书、拿取大号电池、拿取小号电池</td><td>三项全到 = OK</td></tr>
    <tr><td><b>周期定界</b></td><td><b>容器定界</b>（容器到位开周期，容器离场立即结算）</td><td>v3.59 新增，默认是"步骤驱动"，务必切换</td></tr>
    <tr><td>容器标签</td><td>包装盒</td><td></td></tr>
    <tr><td>到位确认（秒）</td><td>1 ~ 1.5</td><td>盒子稳定出现满该秒数才开周期，防闪现误开</td></tr>
    <tr><td>离场确认（秒）</td><td>3</td><td>盒子消失满该秒数才结算——工人俯身挡住盒子不会误结算；真拿走后约 3 秒出结果</td></tr>
  </table>
  {F("dgqg_step2_cycle_owner.png", "图 3-2 逻辑设置：周期定界=容器定界，容器标签=包装盒，到位/离场确认秒")}

  <h3 class="sub">3.3 保存并启用</h3>
  <p>事件设置保持 合格(OK)/不良(NG) 默认即可。点右上「保存配置」→「启用当前项目」→ 检测中心开始检测。</p>
  <div class="callout warn"><b>注意</b>：检测运行中保存项目配置会立即重载并打断进行中的周期。现场调参请先「停止检测」再改再保存。</div>
</section>

<section>
  <h2 class="sec">四、运行时的判定规则（验收对照）</h2>
  <table>
    <tr><th>现场情况</th><th>系统行为</th></tr>
    <tr><td>包装盒放上台面（稳定 1~1.5 秒）</td><td>开新周期，检测中心步骤清单点亮</td></tr>
    <tr><td>周期内做完三个动作（任意顺序）</td><td>盒子拿走后约 3 秒 → <span class="ok-pill">OK</span></td></tr>
    <tr><td>某个动作重复做了几次</td><td>不判 NG（多拿一次说明书不是缺陷）</td></tr>
    <tr><td>少做了动作就把盒子拿走</td><td><span class="ng-pill">NG</span>，原因写明缺了哪步</td></tr>
    <tr><td>盒子不在台面时手在画面里晃动</td><td>动作<b>不入账</b>、不会误开周期</td></tr>
    <tr><td>工人俯身短暂挡住盒子（&lt; 离场确认秒）</td><td>周期照常继续，不会误结算</td></tr>
    <tr><td>空盒放上又拿走（没做动作）</td><td><span class="ng-pill">NG</span>（三步全缺）——收走成品空盒请勿在台面单独停留超过到位确认秒</td></tr>
  </table>
  <p>本配置已用现场实拍视频回放验证：<b>13 个周期 11 OK / 2 NG</b>，其中 1 个 NG 是片头收走空盒（符合上表语义）、1 个 NG 是下述拆分区域待校准问题。</p>
</section>

<section>
  <h2 class="sec">五、遗留待办：小号电池拆分区域需现场校准</h2>
  <p>验证中发现一次「缺 拿取小号电池」的误 NG：现有「同标签区域拆分」里<b>小号电池区域画得偏小</b>，工人手偏一点就落在区域外、被归成大号电池。请在现场：</p>
  <p>步骤设置 → 同标签区域拆分 → 编辑 拿取电池 规则 → 对照实际取料位置<b>重画两块区域</b>（锚点=电池盒，区域随电池盒位置自动跟随；v3.57 起区域支持画多块，取料位置分散可以给同一区域画多块）。改完用两三个实件试跑确认两种电池都能正确区分。</p>
</section>

<section>
  <h2 class="sec">六、常见问题</h2>
  <p><b>Q：盒子放上去周期不开始？</b><br/>A：① 确认「周期定界」已选容器定界、容器标签是 包装盒；② 检测中心看画面里包装盒有没有出检测框——没框是模型/置信度问题（步骤设置里包装盒行的置信度阈值同样生效，默认 50%）；③ 到位确认秒内盒子须持续被检出。</p>
  <p><b>Q：盒子还在，却提前出结果了？</b><br/>A：说明盒子被连续遮挡超过了离场确认秒——把「离场确认（秒）」调大（如 5 秒），代价是拿走后出结果慢一点。</p>
  <p><b>Q：想要顺序约束（必须先说明书后电池）？</b><br/>A：把「基于模式」换成 基于顺序模式 并排好步骤顺序即可，周期定界照旧生效；当前按贵司工艺（无序）配置。</p>
  <p><b>Q：升级后老项目会变吗？</b><br/>A：不会。周期定界默认"步骤驱动"=老行为，只有手动切到容器定界的项目才启用新逻辑。</p>
</section>

<footer>天军 AI 视觉检测系统 · 东莞群光 容器定界周期配置指南 · 适用 v3.59.0+　|　技术支持请联系天军机器人（苏州）有限公司</footer>
</div>
</body></html>"""


def main():
    html = build_html()
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print("HTML ->", OUT_HTML, os.path.getsize(OUT_HTML), "bytes")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page()
        pg.goto("file://" + OUT_HTML, wait_until="networkidle")
        pg.pdf(path=OUT_PDF, format="A4", print_background=True,
               margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        b.close()
    print("PDF  ->", OUT_PDF, os.path.getsize(OUT_PDF), "bytes")


if __name__ == "__main__":
    main()
