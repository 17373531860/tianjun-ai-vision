# -*- coding: utf-8 -*-
"""生成《雷鸟包装作业工位 区域事件模式 配置指南》—— HTML → Chromium → PDF。

面向现场工程师: 四料盒组内按序 + 严格模式 + 监控页展示序列 的完整配置与参数定案。
截图来源: 本机 v3.62 开发栈实机截图 + 现场视频区域标注（配置指南assets/）。
用法: python scripts/docgen/gen_rayneo_packing_guide.py
产物: docs/customers/雷鸟包装/雷鸟包装工位_区域事件配置指南.html / .pdf
"""
import base64
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHOTS = os.path.join(ROOT, "docs", "customers", "雷鸟包装", "配置指南assets")
OUT_HTML = os.path.join(ROOT, "docs", "customers", "雷鸟包装",
                        "雷鸟包装工位_区域事件配置指南.html")
OUT_PDF = os.path.join(ROOT, "docs", "customers", "雷鸟包装",
                       "雷鸟包装工位_区域事件配置指南.pdf")


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
.cover h1{font-size:40px;margin:0 0 6px;font-weight:800;line-height:1.3;}
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
th{background:var(--bg-soft);font-weight:600;white-space:nowrap;}
code{background:#eef2f7;border-radius:4px;padding:1px 6px;font-size:12.5px;
  font-family:Menlo,Consolas,monospace;}
figure{margin:14px 0;text-align:center;break-inside:avoid;}
figure img{max-width:100%;border:1px solid var(--line);border-radius:8px;}
figcaption{font-size:12.5px;color:var(--muted);margin-top:6px;}
.callout{border-left:5px solid var(--brand);background:var(--bg-soft);
  border-radius:0 8px 8px 0;padding:10px 16px;margin:12px 0;break-inside:avoid;}
.callout.warn{border-color:#d97706;background:#fffbeb;}
.callout.danger{border-color:var(--red);background:#fef2f2;}
.callout b.tag{display:inline-block;margin-right:6px;}
ul,ol{margin:6px 0;padding-left:24px;}
li{margin:4px 0;}
footer{margin:36px 0 28px;padding-top:12px;border-top:1px solid var(--line);
  font-size:12px;color:var(--muted);text-align:center;}
.missing{color:var(--red);border:1px dashed var(--red);padding:8px;margin:10px 0;}
.pill-ok{color:var(--ok);font-weight:700;}
.pill-ng{color:var(--red);font-weight:700;}
"""


def build_html():
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"/>
<title>雷鸟包装作业工位 区域事件配置指南</title><style>{CSS}</style></head><body>

<div class="cover">
  <div class="kicker">天军 AI 视觉检测系统 · 现场配置指南</div>
  <h1>雷鸟包装作业工位<br/>区域事件模式配置指南</h1>
  <h2>四料盒取放顺序校验 · 严格模式 · 监控页展示序列</h2>
  <div class="meta">
    <div><b>适用版本</b><span>v3.62.0 及以上</span></div>
    <div><b>逻辑模式</b><span>区域事件（region_events）</span></div>
    <div><b>工艺</b><span>一托盘一周期：拿5→检→放→拿6→放→拿4→检→放→拿2→检→放→收盘</span></div>
    <div><b>验证基线</b><span>两段现场视频 23 周期判定全对；28~31fps 应力零回退</span></div>
    <div><b>编制日期</b><span>2026-09-24</span></div>
  </div>
  <div class="note">本指南给出该工位的全部布点坐标、规则参数与参数定案理由；
  换机位 / 换品种（SKU）时的注意事项见第七节，务必先读再动手。</div>
</div>

<div class="page">

<section>
  <h2 class="sec">一、工位与工艺</h2>
  <p>俯拍机位。桌面下排四个料盒（左起：<b>6 号</b>绿、<b>2 号</b>蓝、<b>4 号</b>黄、<b>5 号</b>灰），
  上方为托盘操作区。每托盘一个作业周期，标准动作 12 步：</p>
  <p><code>拿料5号 → 检查 → 放入 → 拿料6号 → 放入 → 拿料4号 → 检查 → 放入 → 拿料2号 → 检查 → 放入 → 收盘</code></p>
  <ul>
    <li><b>拿料四步允许乱序</b>（现场习惯 5→6→4→2，但不强制），每盒<b>每周期必须恰好拿 1 次</b>：
        少拿=NG（缺料），多拿=NG（重复）。</li>
    <li>勾选「组内按序」后，拿料顺序偏离 5→6→4→2 也会 NG（当前配置<b>已开</b>，按现场 SOP 定）。</li>
    <li>6 号料不需检查，直接放入；其余三种料拿后须检查再放入。</li>
    <li>收盘（托盘撤离）触发本周期结算。</li>
  </ul>
</section>

<section>
  <h2 class="sec">二、区域布点</h2>
  {F('zones_reference.png', '区域布点参考：四个拿料列区 + 托盘放入/收盘区（叠加在现场画面上）')}
  <table>
    <tr><th>区域</th><th>归一化坐标 (x1, y1, x2, y2)</th><th>说明</th></tr>
    <tr><td>拿料6号区</td><td><code>0.00, 0.35, 0.23, 0.98</code></td><td>绿盒整列（盒口偏高，y 顶取 0.35）</td></tr>
    <tr><td>拿料2号区</td><td><code>0.23, 0.50, 0.50, 0.98</code></td><td>蓝盒整列</td></tr>
    <tr><td>拿料4号区</td><td><code>0.50, 0.50, 0.77, 0.98</code></td><td>黄盒整列</td></tr>
    <tr><td>拿料5号区</td><td><code>0.77,0.42,1.00,0.98</code> + 补块 <code>0.93,0.36,1.00,0.42</code></td><td>灰盒双块（右上角盒沿补块）</td></tr>
    <tr><td>放入 / 收盘区</td><td>放入 <code>0.40, 0.06, 0.78, 0.56</code>；收盘 <code>0.40, 0.18, 0.78, 0.56</code></td><td>托盘操作带；收盘区略窄避开背景</td></tr>
  </table>
  <div class="callout warn"><b class="tag">⚠ 相机动过必须重画区域。</b>
  区域是按画面归一化坐标存的，相机平移/变焦后料盒会错位到别的列区，
  轻则拿料归错盒、重则整段误 NG。移机后在项目配置里逐区重新框选并跑一段试料验证。</div>
</section>

<section>
  <h2 class="sec">三、规则配置表</h2>
  <table>
    <tr><th>事件名</th><th>类型</th><th>主体/目标</th><th>关键参数</th></tr>
    <tr><td>拿料5号</td><td>进区（region_enter）</td><td>手</td><td>连续帧 <code>min_frames=3</code>，离区确认 <code>gone_seconds=0.6</code></td></tr>
    <tr><td>拿料6号</td><td>进区</td><td>手</td><td>同上 <code>3 / 0.6</code></td></tr>
    <tr><td>拿料4号</td><td>进区</td><td>手</td><td><code>min_frames=3</code>（v3.62 由 4 下调，见第六节）</td></tr>
    <tr><td>拿料2号</td><td>进区</td><td>手</td><td><code>min_frames=7</code>（防过顶误报，见第六节）</td></tr>
    <tr><td>检查</td><td>重叠（overlap）</td><td>物料 ∩ 手</td><td>持续 <code>min_seconds=0.5</code>，消失确认 <code>gone=1.2s</code>，不限区域</td></tr>
    <tr><td>放入</td><td>出区（region_exit）</td><td>物料</td><td>区域内 ≥<code>5</code> 帧后消失 ≥<code>5</code> 帧</td></tr>
    <tr><td>收盘</td><td>出区，<b>结算</b></td><td>盒子（托盘）</td><td>区域内 ≥<code>3</code> 帧后消失 ≥<code>10</code> 帧 → 触发周期结算</td></tr>
  </table>
  <p>类别置信度：手/物料/2号盒 <code>0.25</code>，盒子/5号盒/6号盒/4号盒 <code>0.5</code>；
  断帧容忍 <code>gap_tolerance_frames=5</code>。</p>
</section>

<section>
  <h2 class="sec">四、序列校验与结算判定</h2>
  {F('logic_tab_seq_block.png', '项目配置 → 逻辑设置：流程顺序校验（严格模式 + 无序组「拿料」组内按序 + 监控页展示序列）与结算判定表')}
  <h3 class="sub">4.1 流程顺序校验</h3>
  <ul>
    <li>顺序模板（不含拿料）：<code>检查 → 放入 → 放入 → 检查 → 放入 → 检查 → 放入 → 收盘</code></li>
    <li><b>严格模式：开</b>——不在期望位置的确认会被吸收（不入账），防多余接触灌满账本。</li>
    <li><b>无序组「拿料」</b>：成员 拿料5号/6号/4号/2号，每成员每周期 <code>1</code> 次，<b>组内按序：开</b>（按成员表顺序 5→6→4→2 走）。</li>
    <li><b>监控页展示序列</b>：按现场 SOP 摆 12 步（纯显示用，引擎不消费；留空则回退规则表顺序）。</li>
  </ul>
  <h3 class="sub">4.2 结算判定（自上而下首条命中）</h3>
  <table>
    <tr><th>#</th><th>条件</th><th>结果</th></tr>
    <tr><td>1</td><td>完整流程（模板+组全部达成）</td><td class="pill-ok">合格 OK</td></tr>
    <tr><td>2~5</td><td>某拿料事件出现 ≥2 次（四盒各一条）</td><td class="pill-ng">不合格 NG（多拿）</td></tr>
    <tr><td>6~9</td><td>缺某拿料事件（四盒各一条）</td><td class="pill-ng">不合格 NG（少拿）</td></tr>
    <tr><td>10</td><td>其余全部（兜底）</td><td class="pill-ng">不合格 NG</td></tr>
  </table>
  <p>事件设置里 OK / NG 两事件均已开启「显示提示框」（toast），NG 事件同时接报警联动。</p>
</section>

<section>
  <h2 class="sec">五、监控页效果</h2>
  {F('monitor_display_order.png', '检测主页：步骤表与 SOP 卡片按 12 步展示序列排布，右侧步骤统计逐步点亮')}
  {F('toast_0.png', 'NG 提示框实拍（视频开头半托盘被撤走 → 缺拿料 → 判 NG）')}
</section>

<section>
  <h2 class="sec">六、参数定案理由（调参前必读）</h2>
  {F('grab_confirm_example.png', '拿料确认机制：手框中心（圆点）落入列区并持续满 min_frames 帧才确认（绿=进区，红=区外）')}
  <table>
    <tr><th>参数</th><th>定值</th><th>为什么</th></tr>
    <tr><td>拿料2号 min_frames</td><td><b>7</b></td><td>2 号列在动线中央，去 4/5 号盒的手会从其上空掠过；
        7 帧（约 0.25s）把掠过滤掉。实测掠过误报导致「重复≥2次」假 NG，调 7 后消失。</td></tr>
    <tr><td>拿料4号 min_frames</td><td><b>3</b></td><td>料盒见底时最后一块贴壁料的抓取，手深入盒内被遮挡
        仅 3 帧弱检出（实测 19:32 场次末件），4 帧门槛会漏检误 NG；下调 3 后修复且全量回放零回退。</td></tr>
    <tr><td>检查 min_seconds</td><td><b>0.5</b>（勿上调）</td><td>提到 0.8s 以上会误伤快速检查动作，
        实测把 17 个合格周期误杀到 9 个。多余「检查」确认由严格模式吸收，不影响判定。</td></tr>
    <tr><td>收盘 gone_frames</td><td><b>10</b></td><td>收盘撤盘手会短暂遮挡托盘，10 帧消失确认防止遮挡误结算。</td></tr>
  </table>
  <div class="callout"><b class="tag">ℹ 灯序偶尔"跳步"属正常。</b>
  「检查」规则不限区域（放料/整理时手贴着物料同样满足条件），偶尔会把下一步的检查/放入灯提前点亮、
  拿料灯随后补亮——<b>这只影响点灯顺序，不影响合格判定</b>（多余确认被严格模式吸收）。
  现场如被问到，按此口径解释即可。</div>
</section>

<section>
  <h2 class="sec">七、验证记录与边界条件</h2>
  <h3 class="sub">7.1 现场视频回放对账（2026-09-24 批测）</h3>
  <table>
    <tr><th>视频段</th><th>周期数</th><th>判定</th><th>备注</th></tr>
    <tr><td>09-20 18:43（全程 8.5 分钟）</td><td>18</td><td>17 OK + 1 期望 NG</td><td>NG 为视频开头半托盘被撤，判缺料，正确</td></tr>
    <tr><td>09-20 19:32（料盒见底段）</td><td>6</td><td>5 OK + 1 期望 NG</td><td>NG 为视频从周期中段开始录，正确</td></tr>
  </table>
  <h3 class="sub">7.2 帧率鲁棒性（现场推理 28~31fps）</h3>
  <p>按 28fps / 31fps 时间缩放、1/15 丢帧、28fps+丢帧组合共 5 档应力回放：<b>判定全部零回退</b>。
  余量边界：丢帧超过 1/10（推理跌破约 27fps）开始出现个别误判——现场若见 FPS 长期低于 27，先解决算力/占用。</p>
  <h3 class="sub">7.3 换品种（SKU）红线</h3>
  <div class="callout danger"><b class="tag">⛔ 当前模型只认本 SKU。</b>
  批测中三段其他品种（深色料+黑托盘，09-20 上午/20:36/20:47 场次）物料识别率仅 1%~24%，
  <b>不能直接沿用本配置检测其他品种</b>。换品种需：采集该品种视频 → 补标注训练 → 重新验证，
  联系天军技术支持走模型迭代流程。</div>
</section>

<section>
  <h2 class="sec">八、常见问题</h2>
  <p><b>Q：某个拿料一直不亮 / 报缺料？</b><br/>
  A：① 检测中心确认手在该盒上方时有「手」检测框；② 确认该列区坐标没有因移机错位（对照第二节表格）；
  ③ 料快见底、员工贴壁抓取时检出会变弱，若频发可将该盒 min_frames 降到 3（勿低于 3）。</p>
  <p><b>Q：没人碰某个料盒却报了拿料？</b><br/>
  A：手从盒上空掠过被计入。把该盒 min_frames 升 1~2 帧（参考 2 号盒定 7 的先例），改后必须回放一段现场视频确认不引入漏检。</p>
  <p><b>Q：合格周期里灯的点亮顺序和实际动作对不上？</b><br/>
  A：见第六节说明——检查/放入灯可能提前，判定不受影响，无需处理。</p>
  <p><b>Q：托盘还在就出结果了？</b><br/>
  A：收盘区被身体/手长时间遮挡会被当作离场。把收盘 gone_frames 调大（如 15），代价是收盘后出结果稍慢。</p>
  <p><b>Q：升级 v3.62 后老项目行为会变吗？</b><br/>
  A：不会。严格模式/无序组/组内按序/展示序列全部默认关闭或为空，只有本工位这类显式配置的项目才启用。</p>
</section>

<footer>天军 AI 视觉检测系统 · 雷鸟包装作业工位 区域事件配置指南 · 适用 v3.62.0+　|　技术支持请联系天军机器人（苏州）有限公司</footer>
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
