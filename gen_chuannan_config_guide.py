# -*- coding: utf-8 -*-
"""生成《川南火工 MES 对接 · 从零配置操作手册》—— 先出精美 HTML, 再用 Chromium 转 PDF。

面向"刚下载、什么都没配的空白软件"客户, 从零手把手: 建项目 → 入站 → 出站 → 监控 → 装机。
证据来源: tests/uat/capture_chuannan_guide.py 在隔离空白栈跑出的真实实机截图。
用法: python gen_chuannan_config_guide.py
产物: docs/川南火工对接_配置操作手册.html / .pdf
"""
import base64
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(ROOT, "tests", "uat", "evidence_chuannan_guide")
SHOTS_FALLBACK = "/tmp/uat_shots"
OUT_HTML = os.path.join(ROOT, "docs", "川南火工对接_配置操作手册.html")
OUT_PDF = os.path.join(ROOT, "docs", "川南火工对接_配置操作手册.pdf")


def img(name):
    """图片转 base64 data URI (HTML/PDF 自包含)。优先仓库证据目录, 兜底 /tmp。"""
    for base in (SHOTS, SHOTS_FALLBACK):
        path = os.path.join(base, name)
        if os.path.exists(path):
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            return f"data:image/png;base64,{b64}"
    return ""


def figure(name, caption):
    src = img(name)
    if not src:
        return f'<div class="missing">[缺图: {name}]</div>'
    return (f'<figure><img src="{src}" alt="{caption}"/>'
            f'<figcaption>{caption}</figcaption></figure>')


# ==================== 样式 ====================
CSS = """
:root{
  --brand:#0EA5E9; --brand-d:#0369A1; --ink:#1f2937; --muted:#6b7280;
  --line:#e5e7eb; --bg-soft:#f1f7fd; --red:#dc2626; --ok:#16a34a;
}
*{box-sizing:border-box;}
@page{size:A4; margin:0;}
body{font-family:"Noto Sans CJK SC","Microsoft YaHei","WenQuanYi Zen Hei",sans-serif;
  color:var(--ink); margin:0; font-size:14px; line-height:1.75; background:#fff;}
.page{max-width:980px; margin:0 auto; padding:0 32px;}

/* 封面 — 钉成整一页 A4 (296mm 防亚像素溢出导致空白页) */
.cover{height:296mm; display:flex; flex-direction:column; justify-content:center;
  background:linear-gradient(135deg,#0c4a6e 0%,#0EA5E9 100%); color:#fff;
  padding:0 70px; page-break-after:always; break-after:page;}
.cover .kicker{font-size:15px; letter-spacing:6px; opacity:.85; margin-bottom:18px;}
.cover h1{font-size:46px; margin:0 0 6px; font-weight:800; line-height:1.2;}
.cover h2{font-size:24px; margin:0 0 40px; font-weight:400; opacity:.95;}
.cover .meta{background:rgba(255,255,255,.12); border-radius:14px; padding:24px 28px;
  backdrop-filter:blur(4px); max-width:640px;}
.cover .meta div{display:flex; padding:7px 0; border-bottom:1px solid rgba(255,255,255,.18);}
.cover .meta div:last-child{border-bottom:none;}
.cover .meta b{width:120px; font-weight:600; opacity:.9;}
.cover .note{margin-top:40px; font-size:13px; opacity:.85; line-height:1.7; max-width:640px;}

/* 目录 — 不再独占一页, 让正文紧随其后填满版面 */
.toc{padding:40px 0 8px;}
.toc h2{font-size:22px; color:var(--brand-d); border-left:6px solid var(--brand);
  padding:4px 0 4px 14px; margin:0 0 14px;}
.toc ol{font-size:14.5px; line-height:1.95; padding-left:26px;}
.toc ol li{color:var(--ink);}
.toc .sub{color:var(--muted); font-size:13px; padding-left:14px;}

/* 章节 */
section{page-break-inside:auto; padding-top:22px;}
h2.sec{font-size:24px; color:var(--brand-d); border-left:6px solid var(--brand);
  padding:4px 0 4px 14px; margin:22px 0 14px; break-after:avoid; page-break-after:avoid;}
h3.sub{font-size:17px; color:var(--ink); margin:20px 0 8px;
  padding-bottom:6px; border-bottom:1px dashed var(--line);
  break-after:avoid; page-break-after:avoid;}
p{margin:8px 0;}
.lead{color:var(--muted);}
ul{margin:8px 0 8px 4px; padding-left:22px;} li{margin:4px 0;}
.tag{display:inline-block; background:var(--bg-soft); color:var(--brand-d);
  border:1px solid #cde6f7; border-radius:6px; padding:1px 8px; font-size:12px; margin:0 2px;}
.new{display:inline-block; background:#16a34a; color:#fff; border-radius:6px;
  padding:1px 8px; font-size:11.5px; margin-left:6px; vertical-align:middle; font-weight:600;}
code{background:#0f172a; color:#7dd3fc; padding:2px 7px; border-radius:5px;
  font-family:"DejaVu Sans Mono",monospace; font-size:12.5px;}
.callout{background:var(--bg-soft); border:1px solid #cde6f7; border-radius:10px;
  padding:14px 18px; margin:14px 0;}
.callout.warn{background:#fff7ed; border-color:#fed7aa;}
.callout.ok{background:#f0fdf4; border-color:#bbf7d0;}
.callout b{color:var(--brand-d);}

/* 图 — 压缩边距, 整图不跨页, 限高让单页能容两图区块以收敛底部留白 */
figure{margin:8px 0 12px; page-break-inside:avoid; break-inside:avoid; text-align:center;}
figure img{width:74%; height:auto; display:inline-block;
  border:1px solid var(--line); border-radius:10px; box-shadow:0 4px 16px rgba(2,32,71,.10);}
figcaption{margin-top:6px; font-size:12.5px; color:var(--muted); text-align:center;}
.missing{color:#b91c1c; padding:20px; border:1px dashed #fca5a5; border-radius:8px;}

/* 表 */
table{width:100%; border-collapse:collapse; margin:14px 0; font-size:13px;
  page-break-inside:auto;}
th{background:var(--brand-d); color:#fff; text-align:left; padding:9px 11px; font-weight:600;}
td{border:1px solid var(--line); padding:8px 11px; vertical-align:top;}
tr:nth-child(even) td{background:#f8fbfe;}
.ok-pill{color:#fff; background:var(--ok); border-radius:20px; padding:1px 9px; font-size:12px;}

.steps{counter-reset:s; margin:12px 0;}
.steps .item{position:relative; padding:6px 0 6px 38px; margin:6px 0;}
.steps .item::before{counter-increment:s; content:counter(s);
  position:absolute; left:0; top:4px; width:26px; height:26px; border-radius:50%;
  background:var(--brand); color:#fff; text-align:center; line-height:26px; font-weight:700; font-size:13px;}

footer{margin-top:40px; padding:18px 0; border-top:2px solid var(--brand);
  color:var(--muted); font-size:12px; text-align:center;}
"""


def build_html():
    F = figure
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"/>
<title>川南火工 MES 对接 · 从零配置操作手册</title><style>{CSS}</style></head><body>

<div class="cover">
  <div class="kicker">天军 AI 视觉检测系统 · 生产管控软件对接</div>
  <h1>川南火工 MES 对接</h1>
  <h2>从零配置操作手册</h2>
  <div class="meta">
    <div><b>适用版本</b><span>v3.29.0 及以上</span></div>
    <div><b>对接对象</b><span>川南火工 生产管控（中控）系统</span></div>
    <div><b>通信方式</b><span>HTTP / JSON 双向（入站接收 + 出站上报）</span></div>
    <div><b>本册定位</b><span>面向"刚装好、什么都没配"的空白软件，从零手把手</span></div>
    <div><b>读者</b><span>现场实施 / 设备工程师（无需懂代码）</span></div>
  </div>
  <div class="note">本手册假设贵司刚下载安装本软件，里面没有任何项目、模型与对接配置。
  我们从"建第一个检测项目"开始，一步一步带到"中控开工、报警闭环、完工上报"全部跑通。
  全部界面图均取自真实可见浏览器实机截图（非示意图）；所有参数都在软件界面里填写与修改、点保存即生效，<b>无需改软件或重装</b>。
  检测模型上传、检测步骤等通用操作详见随软件附带的《软件操作手册》，本册只讲对接落地必需的步骤。</div>
</div>

<div class="page">

<div class="toc">
  <h2>目录</h2>
  <ol>
    <li>开始之前（软件定位 / 两条数据通路 / 接口地址清单）</li>
    <li>第一步：建检测项目（项目名 = 产品代号）</li>
    <li>入站配置（工单接收）<span class="sub">字段映射 · 自动切项目 · 响应约定 · 报警闭环 · 监控横幅 · 开工四要素上屏</span></li>
    <li>出站配置（外部对接 → 报警 / 完工上报）<span class="sub">新建连接 · 附带截图 · 川南预设</span></li>
    <li>监控页效果（报警持续横幅 · 开工四要素常显）</li>
    <li>可靠性与装机（后端崩溃自恢复 · 开机自启 · 全屏看板）</li>
    <li>速查表（接口地址 · 业务错误码 · 配置项落位）</li>
    <li>常见问题</li>
  </ol>
</div>

<section>
  <h2 class="sec">一、开始之前</h2>
  <p>整套对接分两条<b>相互独立</b>的数据通路，各自在界面里配置，互不影响：</p>
  <div class="steps">
    <div class="item"><b>入站（中控 → 本机）：</b>中控系统调用本机接口，下发开工指令、消除报警、做健康探活。在「MES → 工单接收」配置。</div>
    <div class="item"><b>出站（本机 → 中控）：</b>本机把报警、完工等事件主动推给中控系统。在「MES → 外部对接」配置。</div>
  </div>
  <p>此外，监控页有一条「报警待消除」持续横幅：只要有报警未被中控消除就一直挂着，外观与字段也在「工单接收」里配。</p>

  <div class="callout"><b>配置入口在哪：</b>软件顶部菜单栏的「MES」。点进去后顶部有「工单接收 / 外部对接」等子页签——
  入站在「工单接收」，出站在「外部对接」。所有配置改完，点页面里的「保存」按钮即写入本机数据库，重启不丢。</div>

  <h3 class="sub">1.1 接口与地址清单（我方锁定，请中控按此调用）</h3>
  <table>
    <tr><th>接口</th><th>方向</th><th>方法</th><th>地址（我方）</th></tr>
    <tr><td>开工信息推送</td><td>中控 → 本机</td><td>POST</td><td><code>/api/v1/mes/inbound/task</code></td></tr>
    <tr><td>报警消除命令</td><td>中控 → 本机</td><td>POST</td><td><code>/api/v1/mes/inbound/alarm/clear</code></td></tr>
    <tr><td>健康检查</td><td>中控 → 本机</td><td>GET</td><td><code>/api/v1/mes/inbound/health</code></td></tr>
    <tr><td>报警信息上报</td><td>本机 → 中控</td><td>POST</td><td>贵司提供（如 <code>/api/v1/warning/report</code>），界面填写</td></tr>
    <tr><td>完工信息上报</td><td>本机 → 中控</td><td>POST</td><td>贵司提供（如 <code>/api/v1/task/complete</code>），界面填写</td></tr>
  </table>
  <div class="callout"><b>入站接收地址由我方固定（见上）</b>，请中控按此 URL 调用；其中接收主机为本机 IP、端口为软件后端端口（默认 8001）。
  健康检查返回 <code>{{"code":0,"message":"ok"}}</code>。出站地址在「外部对接」里随时填改。
  入站接收路径如需自定义，也可在「工单接收」页底部「自定义接收路径」里改。</div>
  <p class="lead">前置网络条件（一次性，由现场网络负责）：本机与中控同网段互通；中控侧防火墙放行本机后端端口；
  可信内网默认无需鉴权（如需鉴权，可在「工单接收 → 来源校验」加 IP 白名单或固定密钥头）。</p>
</section>

<section>
  <h2 class="sec">二、第一步：建检测项目（项目名 = 产品代号）</h2>
  <p>刚装好的软件是空白的，项目列表为空。<b>对接川南最关键的第一步</b>：先建一个检测项目，
  并把项目名直接命名为该产品的<b>产品代号</b>——这样中控开工时下发产品代号，软件就能自动切到对应检测项目。</p>
  {F("00_blank_projects.png", "图2-1 空白软件：左侧项目列表为空（暂无项目）")}

  <h3 class="sub">2.1 点「新建项目」，项目名称填成产品代号</h3>
  <p>点右上角「新建项目」，在弹出框的「项目名称」里填入该产品的产品代号（例：<code>JS-2024-01</code>），任务类型保持「目标检测」，点「创建」。</p>
  {F("01_new_project_dialog.png", "图2-2 新建项目：项目名称填成产品代号（这是自动切项目的依据）")}

  <h3 class="sub">2.2 项目建好</h3>
  <p>创建后，左侧列表出现该项目。<b>检测模型上传、检测步骤/判定逻辑的配置属于通用操作，详见《软件操作手册》</b>，本册不再展开——
  对接川南只需保证：<b>每个要检测的产品，都有一个以其产品代号命名的项目</b>。多个产品就建多个同名项目即可。</p>
  {F("02_project_created.png", "图2-3 项目建好：列表出现以产品代号命名的项目")}
  <div class="callout ok"><b>命名约定就是全部。</b>只要项目名和中控下发的产品代号一致，开工即自动切换，零额外配置。
  若不想改项目名，也可在下一节的「产品代号 → 项目」对照表里手动指定。</div>
</section>

<section>
  <h2 class="sec">三、入站配置（工单接收）</h2>
  <p>路径：顶部「MES」→「工单接收」。本页自上而下：字段映射 → 收到任务后做什么 → 响应约定 → 报警闭环 → 监控横幅 → 开工四要素 → 自定义接收路径。逐块讲。</p>

  <h3 class="sub">3.1 字段映射（把中控报文字段对到本机）</h3>
  <p>「字段映射」把中控开工报文里的字段名，对到本机内部字段。例：中控发 <code>TaskNo</code>，本机 <code>task_no</code> 就填 <code>TaskNo</code>。
  常用：任务号 / 产品代号 / 工序工步 / 操作员 / 开工时间。字段名以贵司实际报文为准，随时可改。</p>
  {F("10_inbound_top.png", "图3-1 工单接收：真实接收地址 + 字段映射 + 响应约定（全可改）")}

  <h3 class="sub">3.2 收到任务后做什么：自动切项目</h3>
  <p>开「按产品码切项目」后，开工即按产品代号自动激活对应检测项目。默认「按项目名自动匹配」——
  <b>这正是第二步把项目命名为产品代号的用意，零配置切换</b>；无匹配再走「产品代号 → 项目」对照表；
  都没有则按固定话术回应（默认「未查询到当前产品代号检测模型」，可改）。</p>
  {F("11_inbound_switch_project.png", "图3-2 按产品码切项目 / 按项目名自动匹配 / 对照表（全可改）")}

  <h3 class="sub">3.3 报警闭环（触发 / 登记 / 去重 / 消除匹配）</h3>
  <ul>
    <li><b>哪些事件触发报警：</b>默认检测周期结束（cycle_end），可多选 / 换其它事件。</li>
    <li><b>只登记哪些结果：</b>默认 NG（不合格）；留空＝不过滤。</li>
    <li><b>去重窗口（秒）：</b>同一条报警在该窗口内只推一次、只记一次；<span class="tag">川南要求 5 秒可配</span>，0＝不去重。</li>
    <li><b>消除匹配字段：</b>中控发「消除」时按哪几个字段定位（默认 任务号＋产品码＋工步＋操作员，四者唯一）。</li>
    <li><b>台账字段映射：</b>报警台账每个字段从事件数据的哪个路径取值。</li>
  </ul>
  {F("12_inbound_alarm_closure.png", "图3-3 报警闭环：触发事件、登记结果、去重窗口、消除匹配字段、台账映射（全可改）")}

  <h3 class="sub">3.4 监控页报警横幅外观</h3>
  <p>横幅是监控页「报警待消除」的持续提示。可配：显示开关、停靠位置（顶/底）、主色（可换品牌色）、刷新间隔、每条显示哪些字段。</p>
  {F("13_inbound_banner.png", "图3-4 监控页报警横幅：开关 / 位置 / 颜色 / 刷新间隔 / 显示字段（全可改）")}

  <h3 class="sub">3.5 开工四要素上屏 <span class="new">v3.29.0 新增</span></h3>
  <p>「监控页任务信息条 → 持续显示要素」：开工后，把<b>任务号 / 产品代号 / 工序工步 / 操作员</b>四个要素持续显示在监控主界面的信息条上，
  让现场操作员一眼看到当前在做哪张工单。四个要素<b>各自独立勾选是否显示，默认全关</b>——
  <b>不勾任何一个时，界面与原来完全一致</b>，勾上才出现，绝不挤占原有布局。</p>
  {F("14_inbound_taskinfo.png", "图3-5 持续显示要素：任务号/产品代号/工序工步/操作员 逐项可勾（默认全关，勾上才显示）")}
  <div class="callout"><b>为什么默认全关：</b>不同客户对主界面信息密度要求不同。默认不显示＝保持原界面；
  需要的客户按需勾选，做到"想看才显示、不想看零干扰"。</div>
</section>

<section>
  <h2 class="sec">四、出站配置（外部对接 → 报警 / 完工上报）</h2>
  <p>路径：顶部「MES」→「外部对接」。这里配置本机把事件主动推给中控。</p>
  {F("20_gateway_list.png", "图4-1 外部对接：连接列表（空白软件初始为空，点右上「新建连接」添加；可建多条、各自启停、日志可查）")}

  <h3 class="sub">4.1 新建连接：地址、协议、推送时机</h3>
  <p>点「新建连接」：「类型」选 REST/JSON；「地址」填中控接收 URL（随时可改）；
  「推送时机」勾选哪些事件外推（报警上报勾"检测周期结束"）；可设只在 NG 时推；超时与重试也在这里配。</p>
  {F("21_gateway_dialog.png", "图4-2 新建连接：地址 / 协议 / 推送时机 / 结果过滤 / 超时重试")}

  <h3 class="sub">4.2 附带现场截图（纯 Base64）</h3>
  <p>中控要报警图片时，开「附带截图」：把当时检测画面压成 Base64 放进报文（占位符 <code>{{snapshot.image_base64}}</code>）。
  可设「截图大小上限（千字节）」自动压缩；川南要纯 Base64，故「带 dataURI 前缀」保持关闭。</p>
  {F("22_gateway_snapshot.png", "图4-3 附带截图开关 + 截图大小上限 + dataURI 前缀（全可改）")}

  <h3 class="sub">4.3 一键套用「川南报警上报」预设</h3>
  <p>「字段映射模板」点「选择预设模板 → 川南报警上报」，自动填好符合川南报文的 JSON：
  TaskNo / ProductCode / StepCode / Operator / WarningText / Image(Base64) / BeginTime，
  并自动设好「仅 NG 推送 + 附带截图 + 成功判定 code==0」。字段不对照样可手改任意键名。</p>
  {F("23_gateway_preset.png", "图4-4 套用川南报警预设：字段映射模板（含 {snapshot.image_base64}）一键填好，可再手改")}
</section>

<section>
  <h2 class="sec">五、监控页效果</h2>

  <h3 class="sub">5.1 报警持续横幅（红灯不灭式提醒）</h3>
  <p>配好后，只要有报警没被中控消除，监控页就持续挂横幅，含报警文本 + 任务号/产品/工步/操作员/时间；
  中控调消除接口后，横幅在下一个刷新周期自动撤场。</p>
  {F("30_monitor_banner_on.png", "图5-1 在途报警：顶部持续红色横幅（任务号/产品/工步/操作员/时间齐全）")}
  {F("31_monitor_banner_off.png", "图5-2 中控消除该报警后：横幅自动消失")}

  <h3 class="sub">5.2 开工四要素常显 <span class="new">v3.29.0 新增</span></h3>
  <p>在 3.5 勾选了「持续显示要素」后，开工建立工单的那一刻起，所勾选的四要素就持续显示在监控页的任务信息条上
  （与原有「工单号」显示在同一条信息栏，按勾选项依次排开）。中控下发新工单即自动刷新，无需人工干预。
  未勾选时该信息条不出现，界面与原版一致。</p>
  <div class="callout ok"><b>提示：</b>报警横幅（5.1）解决"出问题时醒目提醒"，四要素常显（5.2）解决"平时随时知道在做哪张工单"，两者可独立开关、互不影响。</div>
</section>

<section>
  <h2 class="sec">六、可靠性与装机</h2>
  <p>对接配好后，工控机长期无人值守运行。下面三项保障"开机即用、崩了能自己起来、画面全屏看板"。</p>

  <h3 class="sub">6.1 后端崩溃自恢复（看门狗）<span class="new">v3.29.0 新增</span></h3>
  <p>软件桌面外壳内置看门狗：检测后台服务<b>意外退出</b>（崩溃、被系统杀进程等）时，自动尝试重启，无需人工。
  策略为指数退避（约 1 秒 → 2 秒 → 4 秒），60 秒内最多自动重启 3 次仍失败才弹错误框提示重启电脑——避免无限重启卡死。</p>
  <div class="callout warn"><b>重要：</b>后端自恢复后，正在进行的检测会停止（属于安全行为，避免脏状态）。
  此时界面会弹出一条提示，请操作员<b>重新点「开始检测」</b>即可继续。正常退出软件（关闭窗口）不会触发自恢复。</div>

  <h3 class="sub">6.2 开机自启 <span class="new">v3.29.0 新增</span></h3>
  <p>安装软件时，安装向导有一个可选项「开机时自动启动（适合工控机）」，<b>默认不勾</b>。
  勾选后，Windows 开机即自动拉起本软件，适合产线工控机"上电即用、无人值守"。
  装好后若想改：把开始菜单里的本软件快捷方式放进/移出 Windows 启动文件夹即可。</p>

  <h3 class="sub">6.3 全屏看板（kiosk 风格）</h3>
  <p>「设置 → 显示」里有主窗口全屏开关，开启后软件铺满整屏、隐藏窗口边框，作为产线看板使用。配合 6.2 的开机自启，实现工控机"开机即全屏看板"。</p>
  {F("40_settings_fullscreen.png", "图6-1 显示设置：主窗口全屏（kiosk 风格）开关")}
</section>

<section>
  <h2 class="sec">七、速查表</h2>

  <h3 class="sub">7.1 业务错误码（入站，界面可改）</h3>
  <table>
    <tr><th>业务码</th><th>默认含义</th><th>触发场景</th></tr>
    <tr><td>0</td><td>成功</td><td>处理成功</td></tr>
    <tr><td>40001</td><td>重复开工</td><td>同任务重复下发（启用拒重时）</td></tr>
    <tr><td>40002</td><td>产品码未知</td><td>找不到该产品码对应检测项目</td></tr>
    <tr><td>40004</td><td>字段缺失</td><td>缺 task_no 等必填项</td></tr>
    <tr><td>40005</td><td>报文格式错误</td><td>报文解析失败</td></tr>
    <tr><td>40006</td><td>状态不允许 / 未启用</td><td>对接开关未开等</td></tr>
    <tr><td>40007</td><td>报警消除未找到</td><td>消除命令匹配不到在途报警</td></tr>
  </table>
  <p class="lead">说明：各码数值与文案均可在「工单接收 → 响应约定」逐项修改，可完全对齐贵司协议编号。</p>

  <h3 class="sub">7.2 配置项落位速查</h3>
  <table>
    <tr><th>想配的东西</th><th>在哪配</th><th>可改</th></tr>
    <tr><td>新建检测项目（命名=产品代号）</td><td>顶部「项目」→ 新建项目</td><td><span class="ok-pill">是</span></td></tr>
    <tr><td>中控字段名对到本机字段</td><td>工单接收 → 字段映射</td><td><span class="ok-pill">是</span></td></tr>
    <tr><td>按产品码自动切项目 / 项目名匹配 / 对照表</td><td>工单接收 → 收到任务后做什么</td><td><span class="ok-pill">是</span></td></tr>
    <tr><td>开工 / 切项目的回应码与文案</td><td>工单接收 → 响应约定</td><td><span class="ok-pill">是</span></td></tr>
    <tr><td>什么事件算报警 / 只记 NG</td><td>工单接收 → 报警闭环</td><td><span class="ok-pill">是</span></td></tr>
    <tr><td>报警去重时间窗（5 秒可改）</td><td>工单接收 → 报警闭环 → 去重窗口</td><td><span class="ok-pill">是</span></td></tr>
    <tr><td>中控按什么字段消除报警</td><td>工单接收 → 报警闭环 → 消除匹配字段</td><td><span class="ok-pill">是</span></td></tr>
    <tr><td>横幅开关 / 位置 / 颜色 / 字段</td><td>工单接收 → 监控页报警横幅</td><td><span class="ok-pill">是</span></td></tr>
    <tr><td>开工四要素是否上屏（逐项）</td><td>工单接收 → 持续显示要素</td><td><span class="ok-pill">是</span></td></tr>
    <tr><td>入站接收路径自定义</td><td>工单接收 → 自定义接收路径</td><td><span class="ok-pill">是</span></td></tr>
    <tr><td>来源校验（IP 白名单 / 密钥头）</td><td>工单接收 → 来源校验（默认关）</td><td><span class="ok-pill">是</span></td></tr>
    <tr><td>上报地址 URL / 协议 / 超时重试</td><td>外部对接 → 新建连接</td><td><span class="ok-pill">是</span></td></tr>
    <tr><td>报警报文字段名与结构</td><td>外部对接 → 字段映射模板</td><td><span class="ok-pill">是</span></td></tr>
    <tr><td>是否带截图 / 截图大小 / 前缀</td><td>外部对接 → 附带截图</td><td><span class="ok-pill">是</span></td></tr>
    <tr><td>开机自启</td><td>安装向导可选项（默认关）</td><td><span class="ok-pill">是</span></td></tr>
    <tr><td>全屏看板</td><td>设置 → 显示</td><td><span class="ok-pill">是</span></td></tr>
  </table>
</section>

<section>
  <h2 class="sec">八、常见问题</h2>
  <p><b>Q：刚装好什么都没有，从哪开始？</b><br/>A：先按第二节建检测项目（项目名 = 产品代号），再按第三/四节配入站、出站。模型与检测步骤参考《软件操作手册》。</p>
  <p><b>Q：产品码切不到项目？</b><br/>A：把检测项目命名成与产品代号完全一致（默认按名自动匹配），或在「产品代号 → 项目」对照表里手动指定。</p>
  <p><b>Q：URL 或字段以后变了怎么办？</b><br/>A：直接在对应页面改地址或模板，点保存即生效，无需我们改软件或重装。</p>
  <p><b>Q：报警一直挂着不消？</b><br/>A：检查中控是否调了消除接口，且「消除匹配字段」与上报时的字段一致；匹配不上会回 40007。</p>
  <p><b>Q：开工四要素不显示？</b><br/>A：到「工单接收 → 持续显示要素」勾选要显示的项（默认全关）；且需中控已下发工单（开工）。</p>
  <p><b>Q：横幅太显眼 / 想换色？</b><br/>A：工单接收 → 监控页报警横幅 → 主色，换成任意颜色；也可整体关闭横幅。</p>
  <p><b>Q：软件崩了会怎样？</b><br/>A：内置看门狗会自动重启后端；恢复后界面会提示重新点「开始检测」。若 60 秒内连崩 3 次，会弹框提示重启电脑。</p>
  <p><b>Q：中控暂时不接消除接口？</b><br/>A：报警靠去重窗口避免刷屏，横幅可手动关；等中控就绪再打开消除即可，软件无需改动。</p>
</section>

<footer>天军 AI 视觉检测系统 · 川南火工 MES 对接 从零配置手册　|　本手册随软件版本演进，最终以双方联调结果为准</footer>
</div>
</body></html>"""
    return html


def main():
    html = build_html()
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print("HTML ->", OUT_HTML, os.path.getsize(OUT_HTML), "bytes")

    # Chromium 渲染 → PDF (高保真, CSS 渐变/阴影/中文字体全保留)
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
