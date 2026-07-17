# -*- coding: utf-8 -*-
"""萍乡百斯特 · 项目实施总手册 v2.1 生成器。

v2.0 的 markdown 源稿随 /tmp 重启丢失, 本脚本按 v2.0 PDF 全文反推重建,
内容与 v2.0 逐节一致, 仅两处修订:
  1. [关键勘误] 2.14 达梦推送「推送时机」由 cycle_end 更正为 weighing_product_done
     —— 两阶段流水线正式结案推的是专用事件, 照 v2.0 填 cycle_end 达梦一条都收不到;
  2. 版面页脚版本号 v2.0 → v2.1, 文末加修订记录。
配图: docs/百斯特萍乡_总手册v2配图/ (自 v2.0 PDF 无损提取, 语义命名)。
产出: 仓库根 百斯特萍乡_项目实施总手册_v2.1.pdf (随 docs/ 下 .html 中间产物)。
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
DOCS = os.path.join(ROOT, "docs")
IMG = "百斯特萍乡_总手册v2配图"
OUT_HTML = os.path.join(DOCS, "百斯特萍乡_项目实施总手册_v2.1.html")
OUT_PDF = os.path.join(ROOT, "百斯特萍乡_项目实施总手册_v2.1.pdf")

CSS = """
:root{--navy:#16324f; --cyan:#0e7490; --ink:#26303a; --muted:#66707a;
      --line:#dde3e9; --soft:#f4f7fa; --ok:#0a7d43; --bad:#b42318; --amber:#92600a;}
*{box-sizing:border-box;}
@page{size:A4; margin:0;}
body{font-family:"Noto Sans CJK SC","Microsoft YaHei","WenQuanYi Zen Hei",sans-serif;
  color:var(--ink); margin:0; font-size:13px; line-height:1.75; background:#fff;}
.page{max-width:980px; margin:0 auto; padding:0 46px 30px;}

.head{background:var(--navy); color:#fff; margin:0 -46px; padding:34px 46px 26px;}
.head .org{font-size:12px; letter-spacing:4px; opacity:.75; margin-bottom:10px;}
.head h1{font-size:27px; margin:0 0 8px; font-weight:700;}
.head .sub{font-size:13.5px; opacity:.92; line-height:1.7;}
.head .meta{font-size:12px; opacity:.8; margin-top:12px;}
.head .meta span{margin-right:22px;}

h2.part{font-size:20px; color:#fff; background:var(--navy); padding:10px 18px;
  margin:34px -8px 16px; border-radius:6px; page-break-before:always; break-before:page;}
h2.part.first{page-break-before:auto; break-before:auto;}
h3{font-size:16px; color:var(--navy); border-left:4px solid var(--cyan);
  padding-left:10px; margin:26px 0 10px; break-after:avoid; page-break-after:avoid;}
h4{font-size:14px; color:var(--cyan); margin:18px 0 8px; break-after:avoid; page-break-after:avoid;}
p{margin:8px 0;}
b{color:var(--navy);}
ul,ol{margin:8px 0; padding-left:22px;}
li{margin:4px 0;}
code{font-family:Consolas,monospace; background:#eef4f8; border-radius:3px;
  padding:1px 6px; font-size:12px; color:var(--navy);}

table{border-collapse:collapse; width:100%; margin:10px 0; font-size:12.5px;}
th{background:var(--soft); color:var(--navy); font-weight:700; text-align:left;}
th,td{border:1px solid var(--line); padding:6px 9px; vertical-align:top; line-height:1.6;}
tr{break-inside:avoid; page-break-inside:avoid;}
td.k{white-space:nowrap; font-weight:700; color:var(--navy);}
td.v{white-space:nowrap; color:var(--cyan); font-weight:700;}

.shot{margin:12px 0; text-align:center; break-inside:avoid; page-break-inside:avoid;}
.shot img{max-width:100%; border:1px solid var(--line); border-radius:6px;}
.shot .cap{font-size:12px; color:var(--muted); margin-top:4px;}

.note{background:var(--soft); border-left:4px solid var(--cyan); padding:8px 14px;
  margin:12px 0; border-radius:0 6px 6px 0; font-size:12.5px;}
.warn{background:#fdf3f2; border-left:4px solid var(--bad); padding:8px 14px;
  margin:12px 0; border-radius:0 6px 6px 0; font-size:12.5px;}
.warn b{color:var(--bad);}
.flow{background:var(--soft); border:1px solid var(--line); border-radius:6px;
  padding:10px 16px; margin:12px 0; font-size:12.5px; color:var(--navy); font-weight:600;}
.toc{background:var(--soft); border:1px solid var(--line); border-radius:8px;
  padding:14px 22px; margin:20px 0;}
.toc div{margin:4px 0;}
.rev{color:var(--muted); font-size:12px; margin-top:26px; border-top:1px solid var(--line);
  padding-top:12px;}
"""


def img(name, cap=""):
    name = name.replace(".png", ".jpg")  # 配图统一 JPEG (q88), 控制 PDF 体积
    c = f'<div class="cap">{cap}</div>' if cap else ""
    return f'<div class="shot"><img src="{IMG}/{name}"/>{c}</div>'


BODY = f"""
<div class="head">
  <div class="org">天军科技 · 项目实施</div>
  <h1>萍乡百斯特项目 · 实施总手册（合订本）v2.1</h1>
  <div class="sub">胶装水泥称重、行为监测及预警项目 —— 标注规范 + 现场配置 + 联调验收的唯一交付文档。<br/>
  阅读对象：标注/训练人员（第一部分）、现场实施工程师（第二、三部分）、验收双方（第四部分）。</div>
  <div class="meta"><span>更新时间：2026-07-17</span><span>替代版本：v2.0（2026-07-16）</span></div>
</div>

<div class="warn"><b>v2.1 勘误（必读）：</b>v2.0 手册 2.14 节「推送时机」误写为 <code>cycle_end</code>。
两阶段流水线正式结案推送的是专用事件 <code>weighing_product_done</code>，照 v2.0 填写达梦将一条数据都收不到。
本版已更正，已按 v2.0 配置过的现场请按 2.14 节改一处即可。其余内容与 v2.0 一致。</div>

<div class="note">本版依据：两阶段流水线方案（草案 v1.5 评审通过后的已实现版本）——工件离秤瞬间称重结算、
收尾动作结案上一件、秤指令全部由重量轨迹驱动、17 项时序参数全部可配置。<br/>
<b>与 v1.0 手册（7 月 13 日发出版）的关系</b>：v1.0 基于"四步顺序 SOP + 秤门控"的旧工艺理解编写。
7 月 15 日经现场视频逐帧核实，确认真实节拍是两件并行的流水线（一件在秤上称重、上一件在旁边等加钢脚水泥收尾），
软件已按新方案完成开发与全流程实测。本手册全面替代 v1.0：标注规范从 4 标签改为 3 标签，配置流程按新界面截图重写。</div>

<div class="toc">
  <b>目录</b>
  <div>第一部分 · 视觉模型标注规范 v3.0（3 标签 + 正误示例实拍图 + 区域为什么不进标注 + 旧数据处置）</div>
  <div>第二部分 · 现场配置手册（每一步、每个按钮、每个参数：箭头截图 + 填什么值 + 为什么）</div>
  <div>第三部分 · 联调与开机自检（秤联机、模拟走一件、常见故障）</div>
  <div>第四部分 · 上线验收标准</div>
</div>

<h2 class="part first">第一部分 · 视觉模型标注规范 v3.0</h2>

<h3>1.1 先看懂流程，再动手标注</h3>
<p>现场确认的真实节拍（视频逐帧核实）：<b>秤上永远只有一件在称重，同时旁边总有上一件在等收尾</b>。单件走完是这样：</p>
<ol>
  <li><b>工件上秤</b> —— 空杯放上电子秤，软件自动去皮（秤归零）；</li>
  <li><b>加水泥</b> —— 工人从钢帽水泥盆取料装杯（有人拿下秤装、有人坐秤装，两种习惯都兼容），看读数装到目标重量；</li>
  <li><b>离秤</b> —— 装够了把件端走，软件在离秤瞬间冻结重量、判合格/缺料/超量，秤自动清零迎接下一件；</li>
  <li><b>加钢脚水泥（收尾）</b> —— 这件被端到旁边，等下一件正在称重时，工人对它做加钢脚水泥的收尾动作——软件看到这个动作，把上一件正式结案上传。</li>
</ol>
<p>所以模型只需要认 3 个动作。注意每个标签在系统里的用途不同，这直接决定标注的松紧：</p>
<table>
<tr><th>#</th><th>标签名</th><th>系统用途</th><th>误报的代价</th><th>标注要求</th></tr>
<tr><td>①</td><td class="k">工件上秤</td><td>去皮的加速佐证（重量为主，视觉只是帮忙提前放行）</td><td>低——重量不满足时视觉单独不触发</td><td>正常严格</td></tr>
<tr><td>②</td><td class="k">加水泥</td><td>错盆报警的主判定（动作出现在钢脚盆→报警）</td><td>高——误报会瞎报警骚扰工人</td><td>最严格，负样本最重要</td></tr>
<tr><td>③</td><td class="k">加钢脚水泥</td><td>结案上一件的触发（先进先出归属）</td><td>中——有防抖帧数+冷却期兜底</td><td>严格</td></tr>
</table>
<div class="note">称重记账链路（去皮/称重/判定/清零）对模型零依赖——模型全漏检称重照样准，只是结案要等超时兜底。
这是本方案最重要的可靠性底线，也意味着：<b>标注宁缺毋滥，误报比漏报伤害大得多</b>。</div>

<h3>1.2 三条总原则（每一条都对应旧数据集的一个真实教训）</h3>
<ol>
  <li><b>只标"动作进行中"，绝不标静置。</b>手（或料流）必须在画面里参与。杯静静坐在秤上、成品摆着等收尾——全部留白。旧数据集 732 框静置误标，直接导致模型"无人也报"。</li>
  <li><b>动作框 = 最小完整包络</b>：手 + 器具/工件 + 接触对象一起框，不带无关背景。框太大学背景、框太小学不到关系。</li>
  <li><b>标签里没有位置和料名。</b>"在哪个盆"由软件里画的区域判定（见 1.5 节），"哪种料"由型号选择决定。同一个装料动作，不管发生在哪个盆，都标 <code>加水泥</code>——发生在错误盆时恰恰更要标对，因为要靠它触发错盆报警。</li>
</ol>

<h3>1.3 区域标定说明（回答"区域要不要标注"）</h3>
<p><b>结论：区域不进模型、不进标注文件，只在软件项目配置里画。</b>理由：</p>
<ul>
  <li>区域是相机坐标系的产物，相机一动区域就变，模型如果学了位置，挪一次相机就要重训；软件里重画区域只要 10 分钟；</li>
  <li>两种水泥外观无法可靠区分，模型认不出"料"，但"动作 × 固定料盆区域"可以 100% 确定地判定料源。</li>
</ul>
<p>五个区域已按现场机位标定（配置方法见第二部分 2.5/2.8 节）：</p>
{img("1_3_五区域标定.png", "五区域标定底图（现场机位实拍）")}
<table>
<tr><th>区域</th><th>归一化坐标 (x1,y1)-(x2,y2)</th><th>角色</th></tr>
<tr><td class="k">Z1 秤台区</td><td>(0.417, 0.524)-(0.615, 0.868)</td><td>标签①有效域：上秤动作中心必须落在 Z1 内才算数，屏蔽别处拿件误报</td></tr>
<tr><td class="k">Z2 钢帽水泥盆</td><td>(0.310, 0.463)-(0.467, 0.684)</td><td>合法装料域：<code>加水泥</code> 在此为正常作业</td></tr>
<tr><td class="k">Z3 钢脚水泥盆</td><td>(0.635, 0.581)-(0.781, 0.809)</td><td>错盆报警域：<code>加水泥</code> 动作中心落入 → 报警（对应错误示范视频）；正常蘸钢脚浆不出事件</td></tr>
<tr><td class="k">Z4 压机/暂存区</td><td>(0.242, 0.107)-(0.394, 0.401)</td><td>待收尾观察（辅助，不硬判）</td></tr>
<tr><td class="k">Z5 成品筐</td><td>(0.190, 0.665)-(0.583, 0.993)</td><td>屏蔽域：筐内取放不参与判定</td></tr>
</table>
<div class="note">标签③ <code>加钢脚水泥</code> 不做任何区域过滤——两次实拍收尾分别发生在压机侧和料盆边，位置不固定，只看动作本身。</div>

<h3>1.4 标签定义与判定要件</h3>
<p>同一个动作不同工人做出来差别很大（单手/双手、俯身/侧身、离秤装/坐秤装）。判定只看"哪些要素发生接触"，不看人、不看姿态：</p>
<table>
<tr><th>标签</th><th>判定要件（全部满足才标）</th><th>明确不看</th><th>明确不标</th></tr>
<tr><td class="k">工件上秤</td><td>① 手接触工件（空杯或装满的杯）② 杯在秤台上或正落向秤台</td><td>谁放的、从哪个方向、单双手</td><td>杯静置秤上无手；在别处（非秤台）拿放杯</td></tr>
<tr><td class="k">加水泥</td><td>① 手持杯/舀具 ② 正在向杯中装填或从料面取料</td><td>舀料还是直接装、器具颜色、盆的位置</td><td>空手/空勺划过料盆上方；搅拌料盆</td></tr>
<tr><td class="k">加钢脚水泥</td><td>① 操作对象是白色成品绝缘子 ② 手正对它施加钢脚水泥</td><td>发生位置（压机侧/料盆边都算）</td><td>成品静置等待；纯搬运成品（无施料动作）</td></tr>
</table>
<p>标签①和③怎么区分？<b>看操作对象，不看位置</b>：</p>
{img("1_4_标签1vs3对比.png", "① 操作对象是杯（金属色容器）｜③ 操作对象是白色成品绝缘子")}
<div class="note">标签①的一个重要特性：「放空杯上秤」和「装完料把杯放回秤」画面上是同一个动作（同一双手、同一种杯、同一台秤），
<b>共用这一个标签</b>，软件按当时的称重状态自动区分语义（空闲=新件去皮，装料中=回秤称净重）。标注时不需要也不允许区分这两种情况——都标 <code>工件上秤</code>。</div>

<h3>1.5 正误示例（现场真实帧逐个过）</h3>
<p>绿色实线框 = 正确标注，红色虚线框 = 错误标注。开标前把本节全部过一遍对齐口径。</p>
<h4>① 工件上秤 —— 框 = 手 + 杯 + 秤台局部</h4>
{img("1_5_工件上秤_正误A.png")}
{img("1_5_工件上秤_正误B.png")}
<h4>② 加水泥 —— 框 = 手 + 杯/舀具 + 料盆沿局部</h4>
{img("1_5_加水泥_正误A.png")}
{img("1_5_加水泥_正误B.png")}
{img("1_5_加水泥_正误C.png")}
{img("1_5_加水泥_正误D.png")}
<div class="note">旧规范拆的「舀料」「装杯」两个标签合并为这一个：逐帧对比确认它们本质都是"向杯中加钢帽水泥"，
只是工人习惯（持杯舀 / 放盆沿装）不同。拆两个标签只会让每类样本减半、边界帧两边乱标。</div>
<h4>③ 加钢脚水泥 —— 框 = 白色成品 + 手 + 施料处</h4>
{img("1_5_加钢脚_正误A.png")}
{img("1_5_加钢脚_正误B.png")}
<h4>负样本 —— 无动作帧整帧留白（占全集 10-15%）</h4>
{img("1_5_负样本A.png")}
{img("1_5_负样本B.png")}
{img("1_5_负样本C.png")}
{img("1_5_负样本D.png")}
<p>必须覆盖的负样本类型：杯静置秤上无人碰、成品摆着等收尾、空秤台、空手/空勺划过盆上方、擦手整理工装、转身走动、双人经过背景。</p>

<h3>1.6 逐条标注规则</h3>
<ol>
  <li><b>时间边界</b>：按"接触开始 → 分离结束"取帧；边界前后 2-3 帧的过渡姿态不标。</li>
  <li><b>静置不标（硬规则）</b>：画面里没有"手在参与这个动作"就不标，没有例外。</li>
  <li><b>遮挡</b>：目标被遮挡超过 50% 的帧丢弃；轻微遮挡正常标。</li>
  <li><b>运动模糊</b>：肉眼看不清杯/手轮廓的帧丢弃。</li>
  <li><b>同帧多动作</b>：一帧同时有 <code>工件上秤</code> 和 <code>加钢脚水泥</code>（双周期并行的常态！）→ 各标各的；旁观/路过人员不标。</li>
  <li><b>一致性</b>：同一标签全程由同一人标完；换标注员先对照已标样本校准 20 帧。</li>
  <li><b>拿不准就过要件表（1.4 节）</b>：有一条不满足就丢帧，禁止"看起来像"式标注。</li>
</ol>

<h3>1.7 跨工人泛化的执行手段</h3>
<ol>
  <li><b>每人先定标 50 帧</b>：每位工人的素材开标前，复核人按要件表先标 50 帧作"参考答案"，标注员对齐后再量产。</li>
  <li><b>每标签每工人 ≥ 300 框</b>；只有一个人的素材再多也不算够。</li>
  <li><b>验证集必须含"没见过的工人"</b>：留至少一名工人的素材完全不进训练集。这名工人指标掉得多 = 模型在背人，回头补多样性。</li>
  <li><b>新工人上岗</b>：补录 1 天素材增量训练，要件表不用改。</li>
</ol>

<h3>1.8 抽帧与数据集组织</h3>
<table>
<tr><th>素材段</th><th>抽帧率</th><th>说明</th></tr>
<tr><td class="k">动作发生段</td><td>3-5 fps</td><td>覆盖起始/中段/收尾姿态</td></tr>
<tr><td class="k">静置/等待段</td><td>0.2-0.5 fps</td><td>专门作负样本，凑够全集 10-15%</td></tr>
</table>
<ol>
  <li><b>切分防泄漏</b>：按时间段切 train/val（如前 4 天训练、后 1 天验证），禁止随机按帧切。</li>
  <li><b>按工人二次切分</b>：留至少一名工人只进验证集。</li>
  <li><b>量级目标</b>：每标签 ≥ 1000 框起步；<code>加水泥</code> 是错盆防错主判定，优先堆到 ≥ 2000 框。</li>
  <li><b>负样本硬指标</b>：空标签帧占 10-15%，其中至少一半是"物体静置"类。</li>
</ol>

<h3>1.9 客户补录清单</h3>
<ul>
  <li>机位固定不动（区域防错依赖固定机位）；</li>
  <li>不同班次、不同工人、不同光照各覆盖 ≥ 2 天；每位会上岗的工人都出镜、每人 ≥ 30 分钟；</li>
  <li>必录场景：正常完整作业 ≥ 2 小时；故意错误示范各 ≥ 10 次（到钢脚盆装杯、跳过收尾直接码筐、缺料/超量停手）；双人同时作业 ≥ 30 分钟；静置空镜 ≥ 20 分钟（无人、杯摆秤上、成品摆着——负样本原料）；</li>
  <li>分辨率 ≥ 1080p、帧率 ≥ 15fps、避免逆光。</li>
</ul>

<h3>1.10 旧数据处置（7.12 两版数据集）</h3>
<p>7.12 数据集按旧 4 标签体系标注（称重清零/舀料/装杯/加钢脚水泥），机器全量复检发现 732 框静置误标 + 241 框框太小 + 629 框连拍重复（逐帧清单见此前交付的审计包）。迁移到新 3 标签：</p>
<table>
<tr><th>旧标签</th><th>新标签</th><th>迁移动作</th></tr>
<tr><td class="k">称重清零</td><td class="v">工件上秤</td><td>改名 + 过滤：只留"手接触杯"的帧；静置帧删框、留图当负样本</td></tr>
<tr><td class="k">舀料</td><td class="v">加水泥</td><td>合并改名，剔除复检清单命中的坏框</td></tr>
<tr><td class="k">装杯</td><td class="v">加水泥</td><td>合并改名（与舀料同类），剔除坏框</td></tr>
<tr><td class="k">加钢脚水泥</td><td class="v">加钢脚水泥</td><td>名字不变 + 重框：旧框大多只框了杯/料流，扩到"成品+手+施料处"</td></tr>
</table>
<p>清洗顺序：整目录备份 → 类别名清单改 3 行（舀料/装杯合并后类别 id 重排，脚本一行 sed）→ 按审计清单逐类处理
（静置删框留图 / 太小重框 / 连拍每组留 1/3）→ 每类抽 30 帧过要件表抽查 → 与新补录合并切分。清洗解决不了负样本类型和工人多样性，仍靠 1.9 补录。</p>

<h3>1.11 模型验收口径</h3>
<table>
<tr><th>指标</th><th>门槛</th></tr>
<tr><td class="k">mAP@0.5（验证集）</td><td>≥ 0.85</td></tr>
<tr><td class="k">现场连跑半天误报</td><td>≤ 1 次/小时</td></tr>
<tr><td class="k">静置空镜连放 30 分钟</td><td>零误报（针对旧版缺陷的专项验收）</td></tr>
<tr><td class="k">三动作逐件识别（现场实测 20 件）</td><td><code>加钢脚水泥</code> 漏检 ≤ 1 次（有超时兜底不致命），<code>加水泥</code> 错盆演示 10 次全报</td></tr>
<tr><td class="k">跨工人抽测（未进训练集工人 10 件）</td><td>漏检 ≤ 1 次</td></tr>
</table>
<p>未达标优先补数据（按 1.9 定向补录），其次调置信度阈值，最后才考虑换模型规格。</p>

<h2 class="part">第二部分 · 现场配置手册（每个按钮每个参数）</h2>
<p>本部分按配置顺序走完全程，每一步给：带编号箭头的实机截图 + 每个编号"点哪里、填什么、为什么"。
截图中的数值就是百斯特现场的推荐值——照抄即可开跑，需要按现场微调的会单独说明。</p>
<div class="flow">配置总路线（约 40 分钟）：① 上传模型 → ② 新建项目(称重投料模式) → ③ 驱动模式选流水线 → ④ 流水线参数
→ ⑤ 画秤台区 → ⑥ 秤指令时序(17项,默认值即可) → ⑦ 前置要求+有效期 → ⑧ 错盆防错规则
→ ⑨ 型号标准量表 → ⑩ 校验与报警映射 → ⑪ 事件设置 → ⑫ 保存+启用 → ⑬ 接电子秤(外部设备) → ⑭ 达梦直写(推送网关) → ⑮ 监控页开跑</div>

<h3>2.0 开始之前</h3>
<table>
<tr><th>准备项</th><th>说明</th></tr>
<tr><td class="k">模型文件</td><td>按第一部分规范训练的 3 标签模型（<code>.pt</code>）。没训好时可用占位模型先配流程，换模型不丢配置</td></tr>
<tr><td class="k">电子秤</td><td>RS232，9600/8/N/1，切到指令应答模式（与连续输出二选一，秤面板调，已确认现场调好）</td></tr>
<tr><td class="k">USB 转串口线</td><td>接工控机后设备管理器确认 COM 口号（本手册以 COM3 为例）</td></tr>
<tr><td class="k">达梦数据库</td><td>客户 IT 已回执：IP、端口 5236、账号、目标表名（见已交付的对接问讯回执）</td></tr>
<tr><td class="k">登录账号</td><td>本项目人员身份走登录制：给每位操作工建账号（设置 → 账号鉴权），记录自动归属登录人</td></tr>
</table>

<h3>2.1 上传模型</h3>
<p>模型管理页 → 上传模型 → 选模型文件 → 上传完成后点"详情"确认类别数为 3、类别名与项目里要填的标签名逐字一致
（<code>工件上秤</code> / <code>加水泥</code> / <code>加钢脚水泥</code>）。</p>
<div class="note">换新模型时：同样方式上传 → 项目"基础设置"换绑模型 → 保存。其余配置全部保留。</div>

<h3>2.2 新建项目</h3>
<p>项目管理页，点右上角「+ 新建项目」：</p>
{img("2_2_新建项目.png")}
<table>
<tr><th>编号</th><th>操作</th><th>填什么 / 为什么</th></tr>
<tr><td>1</td><td class="k">项目名称</td><td>如 <code>百斯特称重工位1</code>。一台秤一个项目</td></tr>
<tr><td>2</td><td class="k">任务类型</td><td>保持默认 <code>目标检测 (Object Detection)</code></td></tr>
<tr><td>3</td><td class="k">逻辑模式</td><td><b>必选「称重投料模式」</b> —— 选错模式后面看不到称重配置页签</td></tr>
<tr><td>4</td><td class="k">点「创建」</td><td>创建后自动进入项目配置页</td></tr>
</table>
<p>创建后在「基础设置」页签把绑定模型换成 2.1 上传的模型，选好视频源（现场相机）。</p>

<h3>2.3 进入称重配置页签</h3>
{img("2_3_称重配置页签.png")}
<p>编号 1：点顶部页签栏的「称重配置」。本部分 2.4～2.10 的所有卡片都在这个页签里，从上往下依次配置。</p>

<h3>2.4 驱动模式 —— 选「两阶段流水线」</h3>
{img("2_4_驱动模式.png")}
<table>
<tr><th>编号</th><th>操作</th><th>填什么 / 为什么</th></tr>
<tr><td>1</td><td class="k">点开「驱动模式」下拉框</td><td>出现两个选项</td></tr>
<tr><td>2</td><td class="k">选 <code>两阶段流水线（秤上称重结算 + 秤下收尾动作结案，两件并行）</code></td><td>这就是百斯特节拍：一件在秤上称、上一件在旁边等加钢脚水泥。另一项"逐道投料"是传统单件流，本项目不要选</td></tr>
</table>
<p>选完下方出现流水线专属卡片（2.5～2.6）。</p>

<h3>2.5 流水线参数 —— 标签绑定 / 皮重范围 / 队列</h3>
{img("2_5_流水线参数.png")}
<table>
<tr><th>编号</th><th>参数</th><th>百斯特填值</th><th>说明</th></tr>
<tr><td>1</td><td class="k">标签①上秤动作</td><td class="v">工件上秤</td><td>必须与模型输出的类别名逐字一致（含大小写/空格）。作去皮加速佐证</td></tr>
<tr><td>2</td><td class="k">标签②装料动作</td><td class="v">加水泥</td><td>错盆守卫用，本卡只登记名字，规则在 2.8 配</td></tr>
<tr><td>3</td><td class="k">标签③收尾动作</td><td class="v">加钢脚水泥</td><td>看到它 → 结案待收尾队列里最老的一件</td></tr>
<tr><td>4</td><td class="k">判定料别</td><td class="v">钢帽水泥</td><td>查型号标准量表用哪一行（2.9 表里的料别列）</td></tr>
<tr><td>5</td><td class="k">皮重下限(kg)</td><td class="v">1.100</td><td>空杯合法重量区间下限。按现场实秤 10 个空杯取最轻再留 0.05 余量</td></tr>
<tr><td>6</td><td class="k">皮重上限(kg)</td><td class="v">1.300</td><td>超上限报"放错物品"不去皮（两件叠放/放了工具都拦住）</td></tr>
<tr><td>7</td><td class="k">待收尾队列深度</td><td class="v">2</td><td>正常节拍恰好 2（一件在称+一件待收尾）。第三件结算时最老件还没收尾 → 报节拍异常</td></tr>
<tr><td>8</td><td class="k">点「画秤台区」</td><td class="v">画 Z1</td><td>弹出画区域窗口，见 2.5.1。画完按钮变绿"已画秤台区(4点)"</td></tr>
</table>

<h4>2.5.1 画秤台区（Z1）</h4>
{img("2_5_画秤台区.png")}
<table>
<tr><th>编号</th><th>操作</th></tr>
<tr><td>1</td><td>在画面上单击加顶点，把秤台完整圈进去（贴 1.3 节 Z1 坐标：秤台面+显示表头，四个点就够）。点回第一个点闭合（靠近时变绿）</td></tr>
<tr><td>2</td><td>画错一步点「撤销上一点」</td></tr>
<tr><td>3</td><td>「清除全部」重来</td></tr>
<tr><td>4</td><td>顶点闭合后点「完成绘制」</td></tr>
<tr><td>5</td><td>点右下角「保存 ROI」——<b>不点保存等于白画</b></td></tr>
</table>
<div class="note">作用：<code>工件上秤</code> 动作中心必须落在此区域内才有效——工人在成品筐或料盆边拿放杯子不会误触发去皮佐证。
相机挪动后必须重画（对照 1.3 节底图，全程约 10 分钟）。</div>

<h3>2.6 秤指令时序 —— 17 项全部可调（默认值即百斯特推荐值）</h3>
<p>这张卡决定"秤指令什么时候发、等多久、多稳算稳"。截图里的值就是推荐值，首次上线全部照抄，跑起来再按现场节拍微调：</p>
{img("2_6_秤指令时序.png")}
<table>
<tr><th>编号</th><th>参数</th><th>推荐值</th><th>什么意思</th><th>什么时候需要动它</th></tr>
<tr><td>1</td><td class="k">去皮触发源</td><td class="v">重量为主</td><td>重量满足就去皮，标签①出现只是提前放行</td><td>模型误报多想收紧 → 换"严格双确认"（标签①+重量都要）；模型没上线 → "仅重量"</td></tr>
<tr><td>2</td><td class="k">去皮延迟(ms)</td><td class="v">0</td><td>触发后延迟多久发 T 指令</td><td>工人抱怨"手还没离开就去皮" → 加 300~500</td></tr>
<tr><td>3</td><td class="k">皮重稳定窗口(ms)</td><td class="v">1000</td><td>读数连续稳 1 秒才算"皮重稳定"</td><td>秤跳动大 → 加大；嫌去皮慢 → 减到 500</td></tr>
<tr><td>4</td><td class="k">皮重稳定公差(kg)</td><td class="v">0.005</td><td>稳定判定允许 ±5g 波动</td><td>车间振动大 → 放宽到 0.01</td></tr>
<tr><td>5</td><td class="k">净重稳定窗口(ms)</td><td class="v">1500</td><td>装料后读数稳 1.5 秒算"净重就绪"</td><td>同上</td></tr>
<tr><td>6</td><td class="k">净重稳定公差(kg)</td><td class="v">0.005</td><td>同 4，用于净重</td><td>同上</td></tr>
<tr><td>7</td><td class="k">缺料报警持续阈值(秒)</td><td class="v">3</td><td>稳定但低于下限持续 3 秒才响灯（防瞬时误报）</td><td>想更快提醒 → 减到 1~2</td></tr>
<tr><td>8</td><td class="k">离秤确认时长(ms)</td><td class="v">500</td><td>骤降后空秤水平稳 0.5 秒 = 确认离秤 = 记账点</td><td>工人拿件特别快 → 减到 300；常有碰秤误判离秤 → 加到 800</td></tr>
<tr><td>9</td><td class="k">清零延迟(ms)</td><td class="v">0</td><td>确认离秤记账后立即发 Z</td><td>希望秤显示保留一会给工人看 → 加 1000~2000</td></tr>
<tr><td>10</td><td class="k">清零验证窗口(ms)</td><td class="v">2000</td><td>发 Z 后 2 秒内应看到读数归零</td><td>秤响应慢 → 加大</td></tr>
<tr><td>11</td><td class="k">清零自动重发次数</td><td class="v">1</td><td>不归零自动补发一次 Z，仍不归零 → 秤面残留报警</td><td>保持</td></tr>
<tr><td>12</td><td class="k">标签①确认帧数</td><td class="v">3</td><td>上秤动作连续 3 帧才算一次有效佐证</td><td>模型闪报 → 加大</td></tr>
<tr><td>13</td><td class="k">标签①新鲜期(秒)</td><td class="v">3</td><td>动作出现后 3 秒内算有效佐证</td><td>保持</td></tr>
<tr><td>14</td><td class="k">标签③确认帧数</td><td class="v">5</td><td>收尾动作连续 5 帧才受理（防挥手误报）</td><td>模型误报收尾 → 加大到 8~10</td></tr>
<tr><td>15</td><td class="k">标签③冷却期(秒)</td><td class="v">2</td><td>刚记账 2 秒内不受理收尾（防同帧竞态张冠李戴）</td><td>保持</td></tr>
<tr><td>16</td><td class="k">装料超时(秒)</td><td class="v">300</td><td>去皮后 5 分钟没装够 → 按超时处置</td><td>按实际最慢节拍放大</td></tr>
<tr><td>17</td><td class="k">收尾超时(秒)</td><td class="v">120</td><td>待收尾挂 2 分钟没等到加钢脚动作 → 记录标"收尾未确认"上传+报警（不硬判 NG，避免模型漏检制造假不良）</td><td>按实际收尾节拍调；试运行期建议放大到 300 观察</td></tr>
</table>

<h3>2.7 前置要求 + 前置选择有效期</h3>
{img("2_7_前置要求.png")}
<table>
<tr><th>编号</th><th>开关</th><th>百斯特设置</th><th>为什么</th></tr>
<tr><td>1</td><td class="k">开始前必须先选操作人员</td><td class="v">开</td><td>对应验收 6.5。人员=登录账号，监控页会显示当前登录人</td></tr>
<tr><td>2</td><td class="k">开始前必须先选水泥型号</td><td class="v">开</td><td>未选型号就上秤 → 否决去皮+持续报警</td></tr>
<tr><td>3</td><td class="k">本件完成后自动给秤置零</td><td class="v">开</td><td>流水线模式的自动清零总开关，关了就要人工按秤</td></tr>
</table>
{img("2_7_有效期.png")}
<table>
<tr><th>编号</th><th>参数</th><th>百斯特填值</th><th>为什么</th></tr>
<tr><td>1</td><td class="k">失效策略</td><td class="v">每天定时失效</td><td>治"昨天选的型号今天忘了换"</td></tr>
<tr><td>2</td><td class="k">每天失效时刻</td><td class="v">08:00</td><td>早班开工前强制重选</td></tr>
<tr><td>3</td><td class="k">失效时清哪些选择</td><td class="v">勾 产品型号，操作人员不勾</td><td>人员跟登录走，不需要每天重选</td></tr>
</table>

<h3>2.8 视觉料源防错 —— 错盆报警就配这一条规则</h3>
{img("2_8_视觉料源防错.png")}
<table>
<tr><th>编号</th><th>操作</th><th>填什么 / 为什么</th></tr>
<tr><td>1</td><td class="k">总开关</td><td class="v">开</td></tr>
<tr><td>2</td><td class="k">识别方式</td><td><code>动作 × 固定区域</code> —— 两种水泥外观分不出，只能靠"动作发生在哪个盆"</td></tr>
<tr><td>3</td><td class="k">投错拦截等纠正</td><td>百斯特<b>关</b>（流水线模式只报警不定格；要定格再开）</td></tr>
<tr><td>4</td><td class="k">同规则报警冷却(秒)</td><td class="v">5</td><td></td></tr>
<tr><td>5</td><td class="k">点「+ 加规则」</td><td>生成一行新规则</td></tr>
<tr><td>6</td><td class="k">规则名</td><td class="v">错盆拦截</td></tr>
<tr><td>7</td><td class="k">动作标签</td><td><code>加水泥</code>（与 2.5 编号 2 一致）</td></tr>
<tr><td>8</td><td class="k">类型</td><td><code>动作限区</code> —— 动作只允许出现在画的区域内，区域外报警</td></tr>
<tr><td>9</td><td class="k">连续帧</td><td class="v">3</td><td></td></tr>
<tr><td>10</td><td class="k">判定区域 点「画区域」</td><td>画允许装料的合法区：把 Z1 秤台区 + Z2 钢帽盆一起圈进去（画法同 2.5.1）。工人到 Z3 钢脚盆装杯时动作在合法区外 → 立报"动作限区违规"</td></tr>
</table>
<div class="note">为什么画"允许区"而不是画"禁止的钢脚盆"？画允许区连"到别的乱七八糟位置装料"也一起管住了，规则更干净。
连续帧填 3 = 连续 3 帧命中才报，防闪报；冷却 5 = 同一违规 5 秒内不重复响。</div>

<h3>2.9 型号标准量表</h3>
{img("2_9_型号标准量表.png")}
<table>
<tr><th>编号</th><th>操作</th><th>填什么</th></tr>
<tr><td>1</td><td class="k">输入新型号名</td><td>如 <code>XP-70</code>（按客户实际产品型号名）</td></tr>
<tr><td>2</td><td class="k">点「添加型号」</td><td>表格里出现该型号一行</td></tr>
<tr><td>3</td><td class="k">标准量</td><td><code>3.500 kg</code> —— 演示值！现场按工艺卡实填每型号钢帽水泥净重</td></tr>
<tr><td>4</td><td class="k">下公差(允许少)</td><td><code>0.250 kg</code> —— 净重 &lt; 标准量−下公差 → 缺料报警</td></tr>
<tr><td>5</td><td class="k">上公差(允许多)</td><td><code>0.250 kg</code> —— 净重 &gt; 标准量+上公差 → 超量报警</td></tr>
</table>
<p>有几个型号加几行。工人在监控页开工时选当班型号（见 2.15 编号 5）。</p>

<h3>2.10 校验与报警映射</h3>
{img("2_10_校验报警映射.png")}
<table>
<tr><th>编号</th><th>参数</th><th>百斯特填值</th><th>说明</th></tr>
<tr><td>1</td><td class="k">料别校验方式</td><td class="v">关闭料别校验</td><td>流水线模式单料别（钢帽水泥），错盆已由 2.8 规则管，不需要按顺序校验料别</td></tr>
<tr><td>2</td><td class="k">缺料 → 触发事件</td><td class="v">不合格</td><td>五种异常各自映射到哪个事件。事件的行为（响灯/定格/需人工确认）在 2.11 配</td></tr>
<tr><td>3</td><td class="k">超量 → 触发事件</td><td class="v">不合格</td><td></td></tr>
<tr><td>4</td><td class="k">料别错 → 触发事件</td><td class="v">不合格</td><td></td></tr>
<tr><td>5</td><td class="k">前置未满足 → 触发事件</td><td class="v">不合格</td><td>未登录/未选型号就上秤</td></tr>
<tr><td>6</td><td class="k">动作限区违规 → 触发事件</td><td class="v">不合格</td><td>2.8 的错盆规则触发时走这里</td></tr>
</table>
<div class="note">需要把某类异常与其他异常区分统计时，先去「事件设置」新增自定义事件（如"错盆"），再回这里改映射。首版全部映射 <code>不合格</code> 最简单。</div>

<h3>2.11 事件设置 —— 报警行为与人工确认</h3>
<p>点顶部「事件设置」页签：</p>
{img("2_11_事件设置.png")}
<table>
<tr><th>编号</th><th>操作</th><th>说明</th></tr>
<tr><td>1</td><td class="k">（改完任何页签都）点右上角「保存配置」</td><td>不保存就切页签不会丢，但关页面会丢</td></tr>
<tr><td>2</td><td class="k">「合格」事件</td><td>系统预设，保持默认即可；可加计数器动作</td></tr>
<tr><td>3</td><td class="k">「不合格」事件的「需人工确认」勾选框</td><td>百斯特建议勾上：触发时检测线定格、弹红色确认框，工人处理完异常按确认（界面点击或 USB 确认按钮，按钮选型见 v1.0 手册第四部分，仍然有效）才恢复。不勾 = 只报警不拦</td></tr>
</table>
<div class="note">三色灯报警：报警中心把 <code>不合格</code> 事件绑到灯塔输出即可（报警中心 → 规则 → 事件选不合格 → 输出选已配置的灯塔串口），与其他项目做法完全一致。</div>

<h3>2.12 保存并启用</h3>
{img("2_12_保存启用.png")}
<table>
<tr><th>编号</th><th>操作</th></tr>
<tr><td>1</td><td>「保存配置」——把 2.4~2.11 全部落库</td></tr>
<tr><td>2</td><td>「启用当前项目」——激活到工位。顶部项目选择器随即显示本项目</td></tr>
</table>

<h3>2.13 接电子秤（外部设备）</h3>
<p>MES 管理 → 外部设备 页签 → 「添加设备」：</p>
{img("2_13_外部设备.png")}
<table>
<tr><th>编号</th><th>参数</th><th>百斯特填值</th><th>说明</th></tr>
<tr><td>1</td><td class="k">名称</td><td class="v">工位1称重器</td><td></td></tr>
<tr><td>2</td><td class="k">设备类型</td><td class="v">称重器</td><td>选了称重器读数才会喂给称重引擎</td></tr>
<tr><td>3</td><td class="k">通信协议</td><td class="v">串口指令应答 (发R读数/T去皮)</td><td>秤已调到应答模式就选这个。上位机发指令、仪表回一帧</td></tr>
<tr><td>4</td><td class="k">串口号</td><td class="v">COM3</td><td>按设备管理器实际 COM 口填（截图为例值）。Linux 填 /dev/ttyUSB0</td></tr>
<tr><td>5</td><td class="k">波特率</td><td class="v">9600</td><td>与秤面板一致；数据位 8 / 校验 无(N) / 停止位 1</td></tr>
<tr><td>6</td><td class="k">数据位/校验/停止位</td><td class="v">8 / 无(N) / 1</td><td>客服确认的秤参数</td></tr>
<tr><td>7</td><td class="k">查询/去皮/置零指令</td><td class="v">R / T / Z</td><td>与秤协议书一致，默认即可；指令结尾符 \\r\\n 保持默认</td></tr>
<tr><td>8</td><td class="k">轮询间隔(秒)</td><td class="v">1.00 改为 0.2</td><td><b>流水线模式靠读数轨迹判定，必须改到 0.2 秒</b>（每秒 5 次）才能及时看到离秤骤降</td></tr>
<tr><td>9</td><td class="k">绑定工位</td><td class="v">工位 1</td><td>必须与启用项目的工位一致，绑错了称重引擎收不到读数</td></tr>
<tr><td>10</td><td class="k">启用</td><td class="v">开</td><td></td></tr>
<tr><td>11</td><td class="k">「保存」</td><td></td><td>保存后设备卡片上有「测试」「去皮」「置零」按钮可手动验证（见第三部分）</td></tr>
</table>
<div class="note">「数据流向」「配对模式」「稳定值判定」等其余字段与本项目无关（那是扫码配对/集群场景用的），保持默认。
称重引擎的稳定判定用的是 2.6 时序卡的参数，不是这里的。</div>

<h3>2.14 达梦数据库直写（推送网关）</h3>
<p>MES 管理 → 外部对接 页签 → 「新建连接」：</p>
{img("2_14_达梦网关.png")}
<table>
<tr><th>编号</th><th>参数</th><th>百斯特填值</th><th>说明</th></tr>
<tr><td>1</td><td class="k">连接名称</td><td class="v">百斯特达梦入库</td><td></td></tr>
<tr><td>2</td><td class="k">适配器类型</td><td class="v">数据库直写</td><td>选中后下方出现数据库连接区</td></tr>
<tr><td>3</td><td class="k">数据库类型</td><td class="v">达梦 DM</td><td>未装达梦驱动时测试会明确提示，按 v1.0 手册 4.3 装驱动即可</td></tr>
<tr><td>4</td><td class="k">主机 / 端口</td><td>客户 IT 回执的 IP / 5236</td><td></td></tr>
<tr><td>5</td><td class="k">用户名 / 密码</td><td>客户 IT 回执</td><td></td></tr>
<tr><td>6</td><td class="k">目标表名</td><td>客户 IT 回执的表（可带 schema，如 <code>PROD.T_WEIGH_RECORD</code>）</td><td>下方「字段映射模板」把工件号/型号/操作员/净重/判定/时间映射到表列，对着回执表结构填</td></tr>
<tr><td>7</td><td class="k">推送时机</td><td class="v">weighing_product_done</td><td><b>【v2.1 更正】</b>在输入框里手动输入 <code>weighing_product_done</code> 后回车（老版本下拉列表里没有这一项，新版本已内置）。这是流水线模式"正式结案"（收尾动作归属或超时兜底）的专用事件，工件号/皮重/净重/判定/收尾状态等字段只有它才带。<b>不要选 cycle_end</b>——那是普通检测周期事件，称重记录不走它，选了达梦一条都收不到</td></tr>
<tr><td>8</td><td class="k">按结果过滤</td><td class="v">OK 和 NG 都勾</td><td>验收 6.1 要求全量上传，"收尾未确认"记录带标志字段上传</td></tr>
<tr><td>9</td><td class="k">重试次数 / 间隔</td><td class="v">3 / 5秒</td><td>达梦断网自动补推，通信日志页可查每次推送</td></tr>
</table>
<p>填完先点「测试」验证连通再启用。</p>

<h3>2.15 监控页 —— 工人每天看的界面</h3>
{img("2_15_监控页.png")}
<table>
<tr><th>编号</th><th>元素</th><th>说明</th></tr>
<tr><td>1</td><td class="k">阶段徽章</td><td>实时显示 <code>空秤·待上件 → 去皮中 → 装料中 → 净重就绪 → 离秤结算</code> 流转</td></tr>
<tr><td>2</td><td class="k">皮重(工件)</td><td>本件去皮时冻结的皮重；空秤时显示 --</td></tr>
<tr><td>3</td><td class="k">实时重量</td><td>秤每 0.2 秒的读数直显（图中 1.200 = 有件刚上秤）</td></tr>
<tr><td>4</td><td class="k">人员</td><td>登录制下自动显示当前登录人，无需手输</td></tr>
<tr><td>5</td><td class="k">型号选择</td><td>当班型号（2.9 表里的型号）。每天 08:00 失效强制重选</td></tr>
<tr><td>6</td><td class="k">「确定人员/型号」</td><td>选完点它，前置校验通过才允许去皮</td></tr>
<tr><td>7</td><td class="k">秤上卡片</td><td>当前在秤上这件的状态（图为空秤）</td></tr>
<tr><td>8</td><td class="k">待收尾队列</td><td>已称重结算、等加钢脚水泥的记录（图中 W0716-0-5 净重 3.000kg 合格）。收尾动作到 → 此行消失、正式结案上传</td></tr>
<tr><td>9</td><td class="k">「去皮」「置零」手动按钮</td><td>异常恢复用，正常生产全程不用碰——秤指令由重量自动驱动</td></tr>
<tr><td>10</td><td class="k">「开始」</td><td>开检测。配好后先按第三部分走一遍联调再正式开跑</td></tr>
</table>
<div class="note">皮重/净重数值显示可在 设置 → 显示设置 里开关；班次信息显示同理（班次配置在数据中心用于统计筛选）。</div>

<h2 class="part">第三部分 · 联调与开机自检</h2>

<h3>3.1 秤联机自检（5 分钟）</h3>
<ol>
  <li>外部设备页，设备卡片点「测试」→ 应返回当前读数（空秤 ≈ 0）。报"串口打开失败/Permission denied" → 换 COM 口号重试；Linux 下执行 <code>sudo usermod -aG dialout &lt;用户&gt;</code> 后重新登录；</li>
  <li>点「去皮」→ 秤显示归零；放个空杯点「置零」→ 显示归零。两个按钮都通 = 指令链路 OK；</li>
  <li>秤面板确认在应答模式——如果软件读数每秒自动跳变而你没发指令，说明还在连续输出模式，按秤说明书切换。</li>
</ol>

<h3>3.2 模拟走一件（10 分钟，不用真水泥）</h3>
<p>监控页点「开始」后：</p>
<ol>
  <li>放空杯上秤 → 阶段徽章 <code>去皮中</code>，1 秒左右秤归零、皮重(工件)显示 ≈1.2 kg；</li>
  <li>往杯里放砝码/任意重物到合格带（如 3.5kg 标准量 → 放 3.3~3.7kg）→ 徽章 <code>装料中</code> → 读数稳 1.5 秒 → <code>净重就绪</code>；</li>
  <li>把杯端走 → 0.5 秒后徽章回 <code>空秤·待上件</code>，待收尾队列出现一行，秤自动清零；</li>
  <li>对着镜头做加钢脚水泥动作（或等 120 秒超时）→ 队列该行消失，数据中心多一条记录，达梦表多一行；</li>
  <li>故意只装 2kg 停手 3 秒 → 三色灯响缺料报警，补到 3.3kg 以上自动解除。</li>
</ol>
<p><b>5 步全通 = 整线就绪。</b></p>

<h3>3.3 常见故障速查</h3>
<table>
<tr><th>现象</th><th>原因</th><th>处置</th></tr>
<tr><td class="k">测试按钮报权限错误</td><td>串口被占用/无权限</td><td>关掉其他串口调试工具；Linux 加 dialout 组</td></tr>
<tr><td class="k">读数一直 0</td><td>秤在连续模式收不到 R 应答 / 接线 TX RX 反</td><td>切应答模式；换交叉线</td></tr>
<tr><td class="k">放件不去皮</td><td>皮重超出 1.1~1.3 区间 / 未登录 / 未选型号</td><td>看监控页红字提示对症处理</td></tr>
<tr><td class="k">离秤不记账</td><td>轮询间隔还是 1 秒，骤降被漏掉</td><td>2.13 编号 8 改 0.2 秒</td></tr>
<tr><td class="k">收尾一直不结案</td><td>模型没识别到加钢脚水泥</td><td>等 120s 超时兜底会自动结案并标"收尾未确认"；长期如此按 1.11 补模型</td></tr>
<tr><td class="k">达梦推送失败</td><td>驱动未装 / 网络不通 / <b>推送时机填错</b></td><td>通信日志看具体报错；先核对 2.14 编号 7 是 <code>weighing_product_done</code>；重试机制会自动补推</td></tr>
</table>

<h2 class="part">第四部分 · 上线验收标准</h2>
<p>模型 + 配置 + 秤 + 达梦全就绪后，双方按下表逐项验收：</p>
<table>
<tr><th>#</th><th>验收项</th><th>通过标准</th><th>对应终验收条款</th></tr>
<tr><td>1</td><td class="k">自动去皮</td><td>空杯上秤稳定后 1 秒内秤自动归零，皮重上屏</td><td>6.4</td></tr>
<tr><td>2</td><td class="k">缺料立即报警</td><td>装到低于标准量−公差停手 3 秒 → 三色灯响；补料达标自动解除</td><td>6.2</td></tr>
<tr><td>3</td><td class="k">超量报警</td><td>装超上公差 → 报警</td><td>6.1 引申</td></tr>
<tr><td>4</td><td class="k">离秤结算</td><td>端走瞬间记录进待收尾队列，秤 1 秒内归零可迎下一件</td><td>6.1</td></tr>
<tr><td>5</td><td class="k">收尾结案</td><td>加钢脚水泥动作后队列行消失、记录落库+推达梦</td><td>6.1</td></tr>
<tr><td>6</td><td class="k">双周期并行</td><td>下一件在秤上称重时做上一件收尾，两条记录互不干扰</td><td>现场节拍</td></tr>
<tr><td>7</td><td class="k">错盆拦截</td><td>故意到钢脚盆做装料动作 10 次 → 次次报警</td><td>6.3</td></tr>
<tr><td>8</td><td class="k">未登录/未选型号拦截</td><td>匿名/未选型号上秤 → 否决去皮+持续报警；次日 08:00 不重选型号 → 拦截</td><td>6.5</td></tr>
<tr><td>9</td><td class="k">秤通信故障自声明</td><td>拔掉串口线 → 通信故障报警；插回自动恢复</td><td>6.6</td></tr>
<tr><td>10</td><td class="k">收尾超时兜底</td><td>挡住镜头不做收尾 → 120s 后自动结案并标"收尾未确认"上传</td><td>可靠性</td></tr>
<tr><td>11</td><td class="k">数据入库</td><td>每件一条记录：皮重/净重/型号/人员/判定/各时间戳，达梦表同步可查</td><td>6.1</td></tr>
<tr><td>12</td><td class="k">静置零误报</td><td>无人操作 30 分钟无任何动作误触发</td><td>模型专项（1.11）</td></tr>
<tr><td>13</td><td class="k">连续多批次稳定</td><td>试运行一周，节拍异常/漏记为零</td><td>6.6</td></tr>
</table>
<p>模型侧验收（mAP/误报率/跨工人泛化）见 1.11 节，两套都过才算交付。</p>

<div class="rev">
<b>修订记录</b><br/>
v2.1（2026-07-17）：更正 2.14「推送时机」cycle_end → weighing_product_done（关键勘误，照旧值填达梦收不到数据）；3.3 故障速查同步补充该排查项。其余内容与 v2.0 一致。<br/>
v2.0（2026-07-16）：按两阶段流水线方案全面重写（替代 v1.0 四步顺序 SOP 版）；标注规范 4 标签 → 3 标签；全部配置界面截图取自已实测通过的软件版本（流水线全流程 14 项自动化验收全绿）。随文档包交付：旧数据集逐帧复检清单（沿用此前审计包）。
</div>
"""


def main():
    html = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            f"<style>{CSS}</style></head><body><div class='page'>{BODY}</div></body></html>")
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print("HTML ->", OUT_HTML, os.path.getsize(OUT_HTML), "bytes")

    footer = ("<div style='width:100%; font-size:9px; color:#8a949e; "
              "text-align:center; font-family:sans-serif;'>"
              "萍乡百斯特 · 项目实施总手册 v2.1 — 第 "
              "<span class='pageNumber'></span> / <span class='totalPages'></span> 页</div>")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page()
        pg.goto("file://" + OUT_HTML, wait_until="networkidle")
        pg.pdf(path=OUT_PDF, format="A4", print_background=True,
               display_header_footer=True, header_template="<div></div>",
               footer_template=footer,
               margin={"top": "10mm", "bottom": "16mm", "left": "0", "right": "0"})
        b.close()
    print("PDF  ->", OUT_PDF, os.path.getsize(OUT_PDF), "bytes")


if __name__ == "__main__":
    main()
