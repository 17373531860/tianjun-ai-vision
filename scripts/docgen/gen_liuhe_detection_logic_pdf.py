# -*- coding: utf-8 -*-
"""生成《六和 焊接组装工位一 · 软件检测逻辑说明书》—— HTML → Chromium → PDF。

面向客户（产线管理者/工艺员/现场工程师）：讲清软件在本工位是**怎么判定的**——
视觉九步 + 多码采集两条线各自的逻辑、何时出结果、OK/NG 依据、典型案例。
语义基线: v3.60.2（随视觉周期结算为正式功能）。
截图来源: docs/customers/六和芯子装配/assets/（现场照片 + 软件真机截图）。
用法: python scripts/docgen/gen_liuhe_detection_logic_pdf.py
产物: docs/customers/六和芯子装配/软件检测逻辑说明书_工位一_v3.60.2.html / .pdf
"""
import base64
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHOTS = os.path.join(ROOT, "docs", "customers", "六和芯子装配", "assets")
OUT_HTML = os.path.join(ROOT, "docs", "customers", "六和芯子装配",
                        "软件检测逻辑说明书_工位一_v3.60.2.html")
OUT_PDF = os.path.join(ROOT, "docs", "customers", "六和芯子装配",
                       "软件检测逻辑说明书_工位一_v3.60.2.pdf")


def img(name):
    path = os.path.join(SHOTS, name)
    if not os.path.exists(path):
        return ""
    ext = os.path.splitext(name)[1].lstrip(".").lower()
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png"}.get(ext, "png")
    with open(path, "rb") as f:
        return f"data:image/{mime};base64," + base64.b64encode(f.read()).decode()


def F(name, caption, note=""):
    src = img(name)
    if not src:
        return f'<div class="missing">[缺图: {name}]</div>'
    n = f'<div class="fignote">{note}</div>' if note else ""
    return (f'<figure><img src="{src}" alt="{caption}"/>'
            f'<figcaption>{caption}</figcaption>{n}</figure>')


CSS = """
:root{--brand:#0EA5E9;--brand-d:#0369A1;--ink:#1f2937;--muted:#6b7280;
  --line:#e5e7eb;--bg-soft:#f1f7fd;--red:#dc2626;--ok:#16a34a;--amber:#d97706;}
*{box-sizing:border-box;}
@page{size:A4;margin:0;}
body{font-family:"Noto Sans CJK SC","PingFang SC","Microsoft YaHei",sans-serif;
  color:var(--ink);margin:0;font-size:14px;line-height:1.78;background:#fff;}
.page{max-width:980px;margin:0 auto;padding:0 34px 30px;}
.cover{height:277mm;display:flex;flex-direction:column;justify-content:center;
  background:linear-gradient(135deg,#0c4a6e 0%,#0EA5E9 100%);color:#fff;
  padding:0 70px;page-break-after:always;break-after:page;}
.cover .kicker{font-size:15px;letter-spacing:8px;opacity:.85;margin-bottom:18px;}
.cover h1{font-size:44px;margin:0 0 8px;font-weight:800;line-height:1.3;}
.cover h2{font-size:21px;margin:0 0 44px;font-weight:400;opacity:.95;}
.cover .meta{background:rgba(255,255,255,.12);border-radius:14px;padding:24px 30px;max-width:660px;}
.cover .meta div{display:flex;padding:7px 0;border-bottom:1px solid rgba(255,255,255,.18);}
.cover .meta div:last-child{border-bottom:none;}
.cover .meta b{width:140px;font-weight:600;opacity:.9;flex:none;}
.cover .note{margin-top:42px;font-size:13px;opacity:.85;line-height:1.8;max-width:660px;}
section{padding-top:20px;}
h2.sec{font-size:23px;color:var(--brand-d);border-left:6px solid var(--brand);
  padding:4px 0 4px 14px;margin:26px 0 14px;break-after:avoid;}
h3.sub{font-size:16.5px;margin:22px 0 8px;padding-bottom:6px;
  border-bottom:1px dashed var(--line);break-after:avoid;}
p{margin:8px 0;}
b{color:#111827;}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:13px;break-inside:avoid;page-break-inside:avoid;}
th{background:var(--bg-soft);color:var(--brand-d);text-align:left;
  padding:8px 10px;border:1px solid var(--line);font-weight:600;}
td{padding:7px 10px;border:1px solid var(--line);vertical-align:top;}
tr:nth-child(even) td{background:#fafcff;}
figure{margin:14px 0;text-align:center;break-inside:avoid;page-break-inside:avoid;}
figure img{max-width:100%;max-height:790px;width:auto;border:1px solid var(--line);border-radius:10px;
  box-shadow:0 2px 10px rgba(15,80,140,.10);}
figcaption{font-size:12.5px;color:var(--muted);margin-top:6px;}
.fignote{font-size:12px;color:var(--amber);margin-top:2px;}
.callout{border-radius:10px;padding:12px 16px;margin:12px 0;break-inside:avoid;
  border:1px solid;font-size:13.5px;}
.callout.info{background:var(--bg-soft);border-color:#bae0f7;}
.callout.warn{background:#fef8ec;border-color:#f5dfb8;}
.callout.good{background:#f0fdf4;border-color:#bbe7c8;}
.callout .t{font-weight:700;margin-bottom:4px;display:block;}
.tag{display:inline-block;padding:1px 9px;border-radius:99px;font-size:12px;
  font-weight:600;margin-right:4px;}
.tag.ok{background:#dcfce7;color:var(--ok);}
.tag.ng{background:#fee2e2;color:var(--red);}
.tag.b{background:#e0f2fe;color:var(--brand-d);}
code{background:#f3f4f6;border:1px solid var(--line);border-radius:5px;
  padding:1px 6px;font-size:12.5px;font-family:"SF Mono",Menlo,Consolas,monospace;}
.missing{color:var(--red);border:1px dashed var(--red);padding:8px;margin:10px 0;
  border-radius:8px;font-size:13px;}
/* 双线时间轴 */
.flow{border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:14px 0;
  background:#fbfdff;break-inside:avoid;}
.flow .lane{display:flex;align-items:center;margin:10px 0;}
.flow .lanelab{flex:none;width:92px;font-weight:700;font-size:13px;color:var(--brand-d);}
.flow .steps{display:flex;flex-wrap:wrap;gap:6px;align-items:center;}
.flow .stp{background:#fff;border:1.5px solid var(--brand);color:var(--brand-d);
  border-radius:8px;padding:3px 9px;font-size:12.5px;font-weight:600;white-space:nowrap;}
.flow .stp.dim{border-color:#cbd5e1;color:#64748b;font-weight:400;}
.flow .stp.final{background:var(--brand);color:#fff;}
.flow .arr{color:#94a3b8;font-size:12px;}
.flow .verdict{border-left:3px solid var(--ok);padding-left:12px;margin-top:12px;
  font-size:13px;color:#374151;}
.toc{border:1px solid var(--line);border-radius:12px;padding:16px 24px;background:#fbfdff;}
.toc div{padding:4px 0;font-size:14px;}
.toc b{color:var(--brand-d);margin-right:10px;}
.footer{margin-top:28px;padding-top:12px;border-top:1px solid var(--line);
  font-size:12px;color:var(--muted);text-align:center;}
"""


def build_html():
    body = f"""
<div class="cover">
  <div class="kicker">视觉 AI 行为引导系统 · 客户技术文档</div>
  <h1>软件检测逻辑说明书</h1>
  <h2>六和 · 焊接组装工位一（母排 + 六芯子装配）</h2>
  <div class="meta">
    <div><b>适用工位</b><span>焊接组装工位一：1 母排 + 6 芯子入工装 → 盖母排 → 扫工装码 → 盖模具盖板</span></div>
    <div><b>软件版本</b><span>v3.60.2（含「随视觉周期结算」正式功能）</span></div>
    <div><b>判定架构</b><span>视觉检测（装配九步） ＋ 多码采集（一件八码），双线独立判定</span></div>
    <div><b>验证方式</b><span>现场真实生产录像 + 现场同款模型完整回放验证</span></div>
    <div><b>文档日期</b><span>2026-09-21</span></div>
  </div>
  <div class="note">
    本说明书回答一个问题：<b>软件是依据什么，把一件工件判成合格（OK）或不合格（NG）的。</b><br/>
    参数怎么改请看《现场整改指引》；本文讲清判定逻辑本身，供产线管理者、工艺员与现场工程师查阅、培训与对账使用。
  </div>
</div>

<div class="page">

<section>
<h2 class="sec">目录</h2>
<div class="toc">
  <div><b>1</b> 总览：一件工件，两条判定线</div>
  <div><b>2</b> 现场操作与软件动作对照</div>
  <div><b>3</b> 视觉检测逻辑（装配九步怎么判）</div>
  <div><b>4</b> 扫码采集逻辑（八个码怎么判）</div>
  <div><b>5</b> 结果汇总、计数与追溯</div>
  <div><b>6</b> 典型判定案例</div>
  <div><b>7</b> 关键参数速查表</div>
</div>
</section>

<section>
<h2 class="sec">1. 总览：一件工件，两条判定线</h2>
<p>软件对每一件工件同时运行两条互相独立的判定线：</p>
<table>
<tr><th style="width:110px">判定线</th><th>管什么</th><th>依据什么</th><th>什么时候出结果</th></tr>
<tr><td><span class="tag b">视觉检测</span></td>
  <td><b>装配有没有做对</b>——九个装配步骤是否全部完成、有没有缺步/重复</td>
  <td>相机画面 + AI 模型识别</td>
  <td>模具盖板（最后一步）确认完成的<b>当场</b></td></tr>
<tr><td><span class="tag b">多码采集</span></td>
  <td><b>码有没有收齐</b>——母排码 ×1、芯子码 ×6、工装码 ×1 共八个码</td>
  <td>扫码枪扫入的条码</td>
  <td>与视觉同一瞬间（随视觉周期结算）</td></tr>
</table>

<div class="flow">
  <div class="lane"><div class="lanelab">工人操作</div><div class="steps">
    <span class="stp dim">放下层母排</span><span class="arr">→</span>
    <span class="stp dim">拿起扫母排码·放回</span><span class="arr">→</span>
    <span class="stp dim">装芯子×6·逐颗扫码</span><span class="arr">→</span>
    <span class="stp dim">盖母排</span><span class="arr">→</span>
    <span class="stp dim">扫工装码</span><span class="arr">→</span>
    <span class="stp dim">盖模具盖板</span>
  </div></div>
  <div class="lane"><div class="lanelab">视觉九步</div><div class="steps">
    <span class="stp">下层母排</span><span class="arr">→</span>
    <span class="stp">芯子1~6</span><span class="arr">→</span>
    <span class="stp">盖母排</span><span class="arr">→</span>
    <span class="stp final">模具盖板 ⟶ 当场结算</span>
  </div></div>
  <div class="lane"><div class="lanelab">扫码八码</div><div class="steps">
    <span class="stp">母排码×1</span>
    <span class="stp">芯子码×6</span>
    <span class="stp">工装码×1</span>
    <span class="arr">——</span>
    <span class="stp final">随视觉同瞬结算</span>
  </div></div>
  <div class="verdict">
    <b>结算瞬间（模具盖板确认完成）：</b>
    视觉九步齐 → 视觉 <span class="tag ok">OK</span>，缺步/重复 → <span class="tag ng">NG</span>；
    八码收齐 → 扫码 <span class="tag ok">OK</span>，缺码 → <span class="tag ng">NG</span>（原因注明缺哪类码）。
    两线各自记账、<b>互不连坐</b>，之后本件翻篇，下一件从零开始。
  </div>
</div>

<div class="callout info"><span class="t">为什么要两条线？</span>
装错了但码扫齐了，是装配问题——由视觉线抓；装对了但漏扫码，是追溯问题——由扫码线抓。
两条线各管一段、独立判定，任何一边 NG 都会报警提示，不会互相掩盖，也不会互相误伤。</div>
</section>

<section>
<h2 class="sec">2. 现场操作与软件动作对照</h2>
{F("工位全景.jpg", "图 2-1 工位全景：俯视相机 + 工装 + 扫码枪")}
<table>
<tr><th style="width:190px">工人动作</th><th>视觉线在做什么</th><th>扫码线在做什么</th></tr>
<tr><td>把下层母排放进工装</td><td>识别到「下层母排」→ <b>本件周期开始</b></td><td>—</td></tr>
<tr><td>拿起工件对扫码枪扫母排码，再放回</td><td>母排离开画面 3~4 秒——进入「消失等待（5 秒，不被打断）」，<b>不误判为做完、不拆成两件</b>（见 3.2）</td><td>母排码入「母排」类（1/1）</td></tr>
<tr><td>逐颗装入 6 颗芯子并扫芯子码</td><td>芯子1~6 逐步记账</td><td>芯子码入「芯子」类（最多 6/6），重复码当场报警</td></tr>
<tr><td>盖上盖母排</td><td>「盖母排」记账</td><td>—</td></tr>
<tr><td>扫工装码</td><td>—</td><td>工装码入「工装」类（1/1；治具循环使用，跨件重复豁免）</td></tr>
<tr><td>盖上模具盖板</td><td>「模具盖板」稳定确认（连续 2 帧 + 消失确认 2 秒）→ <b>本件当场结算</b></td><td>视觉结算的同一瞬间，对本件码组一并结算</td></tr>
</table>
{F("扫母排码动作_裁剪.jpg", "图 2-2 扫母排码：工件被竖起、母排短暂离开俯视画面——软件按 3.2 的规则不误切")}
</section>

<section>
<h2 class="sec">3. 视觉检测逻辑（装配九步怎么判）</h2>

<h3 class="sub">3.1 一个步骤怎么算「完成」</h3>
<p>每个步骤对应一个 AI 识别目标（如「下层母排」「芯子3」）。判定分三个阶段：</p>
<table>
<tr><th style="width:120px">阶段</th><th>规则</th></tr>
<tr><td>① 识别到</td><td>模型给出的置信度 ≥ 50%，且连续检出达到「最少帧数」（防止一闪而过的误检；模具盖板要求连续 2 帧）</td></tr>
<tr><td>② 在场</td><td>目标持续在画面中 = 这一步正在进行</td></tr>
<tr><td>③ 消失确认</td><td>目标从画面消失后，先等「消失等待」时间；等满仍未回来，才正式记为<b>这一步完成</b>。等待期间回来了，就当作从未离开</td></tr>
</table>
<p>每步启用「单次接受」：一个周期内该步骤只记一次账，检测框的轻微抖动不会被重复记账、
也就不会在结算时误报「重复步骤」。</p>

<h3 class="sub">3.2 拿起扫码，为什么不会拆成两件</h3>
<p>本工位的特殊点：放好母排后要<b>拿起整个工件</b>对扫码枪扫码。俯视相机里母排会离开画面
3~4 秒——若按一般规则，软件会把这一抬一放误判成「上一件做完 + 新一件开始」。针对这一点，
「下层母排」这一步配置了两道保险：</p>
<table>
<tr><th style="width:170px">配置</th><th>作用</th></tr>
<tr><td>消失等待 = 5 秒</td><td>母排离开画面后先等 5 秒。扫码抬离实测 2~4 秒，5 秒足以覆盖；两件工件的真实间隔在 7 秒以上，5 秒不会把两件粘成一件</td></tr>
<tr><td>等待不被打断 = 开</td><td>等待期间即使画面里出现其他识别目标（旁边料盒里的芯子、竖起工件的侧面），5 秒<b>照走不误</b>。没有这个开关，等待会被其他目标瞬间清零——这正是曾经「调到 5 秒也没用」的原因</td></tr>
</table>
{F("步骤设置_等待不被打断.png", "图 3-1 步骤设置：下层母排「消失等待 5 秒 + 等待不被打断」；模具盖板「最少帧数 2 + 消失确认 2 秒」")}

<h3 class="sub">3.3 周期怎么开、怎么结、怎么判</h3>
<table>
<tr><th style="width:120px">环节</th><th>规则</th></tr>
<tr><td>周期开始</td><td>九步中的<b>第一步（下层母排）</b>被识别到，本件周期开始。中途步骤不要求顺序，先装哪颗芯子都可以</td></tr>
<tr><td>周期结算</td><td>结算方式 = <b>末步完成结算</b>：最后一步（模具盖板）按 3.1 的规则确认完成的瞬间，本件<b>当场</b>出结果，不必等下一件</td></tr>
<tr><td><span class="tag ok">OK</span></td><td>结算时九步全部记账齐全</td></tr>
<tr><td><span class="tag ng">NG</span></td><td>缺步（原因注明缺了哪几步，如「缺少: 芯子4」）或重复步骤。判定原因随周期记录保存，数据页可查</td></tr>
<tr><td>兜底</td><td>空闲超时 / 周期超时均为 0（关闭）——本工位工人扫码间隙可达 10 秒左右，开小超时会把一件从中间切断，因此周期边界完全交给「末步结算」</td></tr>
</table>
{F("逻辑设置_检测模式.png", "图 3-2 逻辑设置：需检测的步骤九步全勾；第一个勾选步骤开周期、最后一个结算周期")}
{F("结算方式卡_裁剪.png", "图 3-3 结算方式 = 末步完成结算：模具盖板一确认，本件当场出 OK/NG")}
</section>

<section>
<h2 class="sec">4. 扫码采集逻辑（八个码怎么判）</h2>

<h3 class="sub">4.1 码怎么分类、怎么防错</h3>
<p>扫码枪扫入的每个码，按「码值格式（正则）」自动归入对应类别；格式都不匹配时按扫码顺序兜底归类。</p>
<table>
<tr><th>码类别</th><th>应扫数量</th><th>码值格式</th><th>防错规则</th></tr>
<tr><td>母排码</td><td>1</td><td><code>M</code> 开头、总长 30~34 位</td><td>本件内重复扫同一码 → 判 NG 报警；超数量多扫 → 拒收并提示</td></tr>
<tr><td>芯子码</td><td>6</td><td>13 位纯数字</td><td>同上；历史已用过的码再次出现按全局策略处理</td></tr>
<tr><td>工装码</td><td>1</td><td><code>H-C</code> 开头</td><td><b>治具循环使用，跨件重复豁免</b>——同一块工装板反复用不报重复</td></tr>
</table>
{F("多码采集配置.png", "图 4-1 多码采集 · 码类别配置（本工位真机截图）")}

<h3 class="sub">4.2 随视觉周期结算：和视觉同一瞬间出结果</h3>
<p>本工位启用「<b>随视觉周期结算</b>」：扫码线自己不定结算时机，而是<b>锚定视觉线</b>——
模具盖板确认完成、视觉结算的同一瞬间，对本件已扫的码一并清点：</p>
<table>
<tr><th style="width:150px">结算瞬间清点</th><th>结果</th></tr>
<tr><td>1 母排 + 6 芯子 + 1 工装全齐</td><td>扫码 <span class="tag ok">OK</span></td></tr>
<tr><td>缺任何码</td><td>扫码 <span class="tag ng">NG</span>，原因注明缺哪类缺几个（如「少扫码: 芯子码缺2」）</td></tr>
</table>
<div class="callout warn"><span class="t">缺码不跨件补扫</span>
结算后本件码组立即翻篇：漏扫就是这一件 NG，<b>不能</b>再拿下一件时补扫上一件的码「转 OK」。
下一件的第一枪从全新的码组开始，不会被上一件的缺码拖累。这样保证「一件一账」，
码和工件永远对得上。</div>
<div class="callout info"><span class="t">组归属锚</span>
每组码严格锚定「本周期」：本件盖板确认之后、下一件开始之前扫入的码，一律记给下一件，
不会误关下一件的码组，也不会拿下一件的码填上一件的账。</div>
{F("多码采集_判定策略_随视觉周期.png", "图 4-2 判定策略 ·「随视觉周期结算」开关（界面示意图，具体数值以第 7 节速查表为准）")}

<h3 class="sub">4.3 为什么「视觉+扫码双重验证」是关闭的</h3>
<p>工人扫完工装码<b>马上</b>盖盖板，而视觉要等盖板消失确认（约 2 秒）才出本件结果——
若开启双重验证，扫码侧只能拿到<b>上一件</b>的视觉结果：上一件若是 NG，会把当前这件
（码明明收齐）连坐误判。因此本工位将其关闭，两线独立把关，能力不打折：
装配缺步由视觉报 NG，少扫码由扫码报 NG。该结论经现场录像回放实测验证。</p>
</section>

<section>
<h2 class="sec">5. 结果汇总、计数与追溯</h2>
<table>
<tr><th style="width:150px">项</th><th>口径</th></tr>
<tr><td>产量 / 合格 / 不良计数</td><td>以<b>视觉周期</b>为准（扫码结算不重复计数，只借灯与语音提醒），一件只计一次</td></tr>
<tr><td>周期记录</td><td>数据页一件一条：九步耗时、OK/NG 与原因、录像回放</td></tr>
<tr><td>扫码记录</td><td>一件一组：八个码的原文、归类、扫入时间；组结果 OK/NG 及缺码明细</td></tr>
<tr><td>工件追溯</td><td>MES → 工件追溯：按任一组件码（母排/芯子/工装）反查整件，八码分节展示</td></tr>
<tr><td>文本落盘</td><td>每件一份 txt（分类落盘格式见 数据中心 → 自定义导出 ·「多码采集」字段组），供上位系统取用</td></tr>
<tr><td>报警提示</td><td>任一线 NG：监控页弹窗 + 语音 + 灯塔；重复扫码 / 多扫当场报警不等结算</td></tr>
</table>
</section>

<section>
<h2 class="sec">6. 典型判定案例</h2>
<table>
<tr><th style="width:230px">情形</th><th style="width:110px">视觉线</th><th style="width:130px">扫码线</th><th>说明</th></tr>
<tr><td>标准作业：九步做齐、八码扫齐</td>
  <td><span class="tag ok">OK</span></td><td><span class="tag ok">OK</span></td>
  <td>盖板确认瞬间双线同时报 OK，合格 +1</td></tr>
<tr><td>拿起扫母排码、再放回</td>
  <td colspan="2" style="text-align:center">不产生任何判定</td>
  <td>5 秒不被打断的消失等待把「抬起-放回」当成一次连续放置：不拆件、不重复记账、不报警</td></tr>
<tr><td>少扫 1 颗芯子码，直接盖盖板</td>
  <td><span class="tag ok">OK</span></td><td><span class="tag ng">NG</span></td>
  <td>装配齐了视觉 OK；扫码 NG 原因「少扫码: 芯子码缺1」。本件翻篇，下一件母排码正常开新组</td></tr>
<tr><td>漏装 1 颗芯子，码却扫齐了</td>
  <td><span class="tag ng">NG</span></td><td><span class="tag ok">OK</span></td>
  <td>视觉 NG 原因「缺少: 芯子X」——码齐不能掩盖装配缺步</td></tr>
<tr><td>本件内同一芯子码扫两次</td>
  <td>—</td><td><span class="tag ng">当场报警</span></td>
  <td>重复码不入账、立即报警提醒换码重扫，不等结算</td></tr>
<tr><td>工装板循环回用</td>
  <td>—</td><td><span class="tag ok">正常入账</span></td>
  <td>工装码跨件重复豁免，不报「历史重复」</td></tr>
<tr><td>录像回放基准（半中间开始录的一个半周期）</td>
  <td><span class="tag ok">1 OK</span></td><td><span class="tag ng">1 NG</span> + <span class="tag ok">1 OK</span></td>
  <td>半截件视觉不开周期（开录前首步已过）、扫码缺码报 NG；完整件双线 OK。合计 1NG + 1OK，与现场工程师预判一致</td></tr>
</table>
</section>

<section>
<h2 class="sec">7. 关键参数速查表</h2>
<p>本表为定案参数（已用现场录像回放验证）。日常<b>不需要</b>改动；对账或复核时对照即可，
表里没提的参数保持软件现状。</p>
<h3 class="sub">步骤设置（项目「六芯子」→ 步骤设置）</h3>
<table>
<tr><th>步骤</th><th>置信度</th><th>消失等待(秒)</th><th>等待不被打断</th><th>最少帧数</th><th>单次接受</th></tr>
<tr><td><b>下层母排</b></td><td>50%</td><td><b>5.00</b></td><td><b>开</b></td><td>1</td><td>开</td></tr>
<tr><td>芯子1 ~ 芯子6</td><td>50%</td><td>0.00</td><td>关</td><td>1</td><td>开</td></tr>
<tr><td>盖母排</td><td>50%</td><td>0.00</td><td>关</td><td>1</td><td>开</td></tr>
<tr><td><b>模具盖板</b></td><td>50%</td><td><b>2.00</b></td><td>关</td><td><b>2</b></td><td>开</td></tr>
</table>
<h3 class="sub">逻辑设置（项目「六芯子」→ 逻辑设置）</h3>
<table>
<tr><th>参数</th><th>值</th></tr>
<tr><td>逻辑模式</td><td>检测模式；需检测的步骤：九步全勾（下层母排 → 芯子1~6 → 盖母排 → 模具盖板）</td></tr>
<tr><td>结算方式</td><td><b>末步完成结算</b></td></tr>
<tr><td>空闲超时 / 周期超时</td><td>0 / 0（关闭）</td></tr>
<tr><td>违规出现时 / 结算缺步骤时</td><td>不处理——等周期结算再判 / 直接判 NG</td></tr>
</table>
<h3 class="sub">多码采集（MES → 扫码器 → 多码采集）</h3>
<table>
<tr><th>参数</th><th>值</th></tr>
<tr><td>码类别</td><td>母排 busbar ×1（<code>^M.{{29,33}}$</code>）；芯子 chip ×6（<code>^\\d{{13}}$</code>）；工装 fixture ×1（<code>^H-C</code>，历史重复豁免）</td></tr>
<tr><td><b>随视觉周期结算</b></td><td><b>开</b>（v3.60.2 正式功能）</td></tr>
<tr><td>视觉+扫码双重验证</td><td><b>关</b>（原因见 4.3）</td></tr>
<tr><td>扫码结算计数</td><td>不计数——视觉已计</td></tr>
<tr><td>本组内重复扫同一码 / 超出应扫数量</td><td>判 NG 报警 / 拒收并提示</td></tr>
<tr><td>扫码器「须先扫码才开始周期」</td><td>关（多码采集独占扫码，此条件永远满足不了，开着会导致数据页无周期记录）</td></tr>
</table>

<div class="callout good"><span class="t">验证声明</span>
以上逻辑与参数已使用贵司现场真实生产录像与现场同款模型（best(9).pt）在软件内完整回放验证：
判定结果与现场工程师对录像的人工预判完全一致（1 NG + 1 OK）。任何与本文描述不符的判定行为，
请保留当天数据页周期记录与后端日志（rizhi.txt）联系我们。</div>

<div class="footer">天军科技 · 视觉 AI 行为引导系统 · 软件检测逻辑说明书（六和 焊接组装工位一）· v3.60.2 · 2026-09-21</div>
</section>

</div>
"""
    return ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'/>"
            f"<title>软件检测逻辑说明书 · 六和工位一 · v3.60.2</title>"
            f"<style>{CSS}</style></head><body>{body}</body></html>")


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
               margin={"top": "10mm", "bottom": "10mm", "left": "0", "right": "0"})
        b.close()
    print("PDF  ->", OUT_PDF, os.path.getsize(OUT_PDF), "bytes")


if __name__ == "__main__":
    main()
