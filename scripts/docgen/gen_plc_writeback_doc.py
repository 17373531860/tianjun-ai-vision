# -*- coding: utf-8 -*-
"""生成《现场纠偏与最终配置 — PLC 写回 / 工件绑码 (S7-300 · DB1201)》PDF。

背景: 2026-08-13 现场反馈 ① PLC 要求写回检测结果编码 ② 虚拟按钮触发后
放行 (NG 停止 / OK 放行) ③ 工件编号没绑定、监控页看不到编号。
排查结论: 三项均为主程序已有能力 (RFC 13/14), 不生效根因是现场把
read_rules / write_rules 两段 JSON 的字段名填错 (引擎静默忽略)。

V1.1 (当日下午): PLC 方 4 项确认已回 —— 结果字 1/2/0、停止位 1停0放、
读完成进站保持出站自清 (无需握手回执)、反馈区为协议约定的 DB1201 (现场
配置的 DB1210 系笔误)。
V1.2 (终版): 停止位地址 PLC 方已定 DB1201.DBX0.4, 全部事项闭环,
本版即联调执行版。

跑法:  python scripts/docgen/gen_plc_writeback_doc.py
输出:  docs/现场纠偏与确认单_PLC写回与工件绑码_<date>.pdf
依赖:  playwright (chromium headless 渲染 HTML → PDF)
"""
from __future__ import annotations

import html as _html
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TODAY = f"{date.today():%Y-%m-%d}"
OUT = ROOT / "docs" / f"现场纠偏与确认单_PLC写回与工件绑码_{TODAY}.pdf"

# ---------------- 可直接粘贴的配置 (纯文本, 不进 f-string) ----------------

READ_RULES_JSON = """[
  {
    "name": "读完成(有码)→绑码",
    "when": [
      {"point": "read_done", "trigger": "rising"},
      {"point": "product_no", "trigger": "not_equals", "value": ""}
    ],
    "debounce_ms": 50,
    "min_interval_ms": 500,
    "ack_mode": "none",
    "actions": [
      {"do": "bind_sn", "template": "{{product_no}}", "channel": 0}
    ]
  },
  {
    "name": "读完成(无码)→视觉报警",
    "when": [
      {"point": "read_done", "trigger": "rising"},
      {"point": "product_no", "trigger": "equals", "value": ""}
    ],
    "debounce_ms": 50,
    "actions": [
      {"do": "alarm", "event": "event2"},
      {"do": "write_points", "writes": [
        {"point": "vis_alarm", "value": true}
      ]}
    ]
  },
  {
    "name": "工件出站→结果清零",
    "when": [
      {"point": "read_done", "trigger": "falling"}
    ],
    "debounce_ms": 50,
    "actions": [
      {"do": "write_points", "writes": [
        {"point": "result_code", "value": 0},
        {"point": "machine_stop", "value": false},
        {"point": "vis_alarm", "value": false}
      ]}
    ]
  }
]"""

WRITE_RULES_JSON = """[
  {
    "on": "cycle_end",
    "results": ["OK"],
    "writes": [
      {"point": "result_code", "value": 1},
      {"point": "machine_stop", "value": false}
    ]
  },
  {
    "on": "cycle_end",
    "results": ["NG"],
    "writes": [
      {"point": "result_code", "value": 2},
      {"point": "machine_stop", "value": true}
    ]
  },
  {
    "on": "heartbeat",
    "period_ms": 1000,
    "writes": [
      {"point": "vis_heartbeat", "value": "toggle"}
    ]
  }
]"""

RELEASE_ACTION_JSON = """[
  {"do": "write_points", "connection": "机越缸1号PLC", "writes": [
    {"point": "machine_stop", "value": false}
  ]}
]"""


def _code(text: str) -> str:
    return f"<pre>{_html.escape(text)}</pre>"


def build_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>现场纠偏与最终配置 — PLC 写回 / 工件绑码</title>
<style>
  @page {{ size: A4; margin: 16mm 14mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
         color: #1a202c; font-size: 10.5pt; line-height: 1.65; }}
  h1 {{ font-size: 20pt; margin: 0 0 4px; }}
  h2 {{ font-size: 14pt; border-left: 5px solid #0e7490; padding-left: 8px;
       margin: 22px 0 8px; page-break-after: avoid; }}
  h3 {{ font-size: 11.5pt; margin: 14px 0 4px; page-break-after: avoid; }}
  .sub {{ color: #64748b; margin-bottom: 14px; }}
  .cover {{ text-align: center; padding-top: 200px; page-break-after: always; }}
  .cover .band {{ color: #0e7490; font-size: 12pt; letter-spacing: 6px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 6px 0 10px;
          font-size: 9.5pt; page-break-inside: avoid; }}
  th, td {{ border: 1px solid #cbd5e1; padding: 4px 7px; text-align: left;
           vertical-align: top; }}
  th {{ background: #f1f5f9; }}
  code {{ background: #f1f5f9; padding: 1px 4px; border-radius: 3px;
         font-family: Menlo, Consolas, monospace; font-size: 9pt; }}
  pre {{ background: #0f172a; color: #e2e8f0; padding: 10px 12px;
        border-radius: 6px; font-family: Menlo, Consolas, monospace;
        font-size: 8.5pt; line-height: 1.5; overflow-x: hidden;
        white-space: pre-wrap; page-break-inside: avoid; }}
  .tip {{ background: #ecfeff; border-left: 4px solid #06b6d4;
         padding: 6px 10px; margin: 8px 0; }}
  .warn {{ background: #fffbeb; border-left: 4px solid #f59e0b;
          padding: 6px 10px; margin: 8px 0; }}
  .bad {{ background: #fef2f2; border-left: 4px solid #ef4444;
         padding: 6px 10px; margin: 8px 0; }}
  .ok {{ background: #f0fdf4; border-left: 4px solid #22c55e;
        padding: 6px 10px; margin: 8px 0; }}
  .foot {{ color: #94a3b8; font-size: 8.5pt; text-align: center; margin-top: 26px; }}
  li {{ margin: 2px 0; }}
  .blank {{ color: #94a3b8; }}
</style></head><body>

<div class="cover">
  <div class="band">天军 AI 视觉检测系统</div>
  <h1>现场纠偏与最终配置<br>PLC 写回 / 工件绑码</h1>
  <p class="sub">西门子 S7-300 · 读区 DB1200 / 反馈区 DB1201 · 依据《PLC对接协议_S7-300_DB交互》V0.1 + 贵方 {TODAY} 全部五项确认</p>
  <p>V1.3 终版 ｜ {TODAY} ｜ 面向现场调试工程师 + 贵方 PLC 工程师</p>
  <p class="sub">结论: 结果写回 / 虚拟按钮放行 / 工件编号绑定均为软件<b>已有能力, 不需要更新版本</b>。<br>
  全部确认事项已闭环, 按第 3 章替换点位表与两段规则 JSON 即可联调。</p>
</div>

<h2>1. 贵方确认事项 (已全部并入最终配置)</h2>
<table>
<tr><th style="width:24px">#</th><th>事项</th><th>贵方回复</th><th>并入配置</th></tr>
<tr><td>1</td><td>结果编码取值</td>
    <td>合格 = 1, 不合格 = 2, 检测中/无效 = 0;
        读完成清掉时结果跟着清</td>
    <td>结果走<b>结果字</b> (INT), 不需要单独 OK/NG 布尔位;
        出站自动写 0 (§3.2 规则③)</td></tr>
<tr><td>2</td><td>停止信号电平</td>
    <td>1 = 停止, 0 = 放行; 读完成清掉时跟着清</td>
    <td>NG → 写 1 停机; OK → 写 0; 出站兜底清 0 (§3.3)</td></tr>
<tr><td>3</td><td>「读完成」复位机制</td>
    <td>进站到位保持到出站, 工件出站 PLC 自动清掉</td>
    <td><b>无需握手回执位</b>; 读完成上升沿 = 取数绑码,
        下降沿 = 工件出站清结果 (§3.2)</td></tr>
<tr><td>4</td><td>反馈 DB 块</td>
    <td>按之前约定建 <b>DB1201</b>, 已建好</td>
    <td>写区全部指向 DB1201 —— 现场配置里的
        <b>DB1210 系笔误, 必须改</b> (§2.1)</td></tr>
<tr><td>5</td><td>停止位地址</td>
    <td><b>DB1201.DBX0.4</b> 当停止位</td>
    <td>machine_stop 点位地址落定 (§3.1)</td></tr>
</table>
<div class="ok">澄清: 此前口述的「编码 121」即上表第 1 项 —— 结果字取值
1 / 2 (/0), 不存在 121 这个码。</div>

<h2>2. 问题定位: 现有配置错在哪 (纠偏依据)</h2>
<h3>2.1 写区 DB 号与地址错</h3>
<div class="bad">现场把写点位配在 <code>DB1210.DBX0.0/0.1/0.2</code> 和
<code>DB1210.DBW20</code> —— 协议约定并已建好的反馈区是 <b>DB1201</b>,
结果字在 <b>DB1201.DBW2</b>。写错 DB 号后即使规则生效, PLC 也收不到。
另外按第 1 章确认, result_ok / result_ng 两个布尔位<b>不需要</b>
(结果走结果字 1/2), 点位表按 §3.1 重建。</div>

<h3>2.2 触发规则 (read_rules) — 字段名错, 规则从未触发</h3>
<div class="bad">现场填的是 <code>{{"point": "read_done", "edge": "rising",
"action": "bind_sn", ...}}</code> —— 软件不认识 <code>edge</code> /
<code>action</code> 字段, 规则被整条跳过, 所以绑码从未触发,
监控页看不到工件编号。软件认的格式是 <code>when</code> 条件数组 +
<code>actions</code> 动作数组 (动作用 <code>do</code> 指定), 见 §3.2。</div>

<h3>2.3 写回规则 (write_rules) — 字段名错, 写回从未生效</h3>
<div class="bad">现场填的是 <code>{{"event": "ok", "point": "result_ok",
"value": true, "pulse_ms": 500}}</code> —— 软件不认识 <code>event</code> /
<code>pulse_ms</code>, 正确字段是 <code>on</code> + <code>results</code> +
<code>writes</code>, 见 §3.3。</div>

<h3>2.4 其余没问题</h3>
<p>读区 DB1200 五个点位的地址、类型、方向都正确;
产品号 <code>DB1200.DBB2</code> + string_s7 可用, 长度建议 30 → <b>28</b>
(与协议 STRING[28] 一致)。</p>

<h2>3. 最终配置 (可直接复制粘贴)</h2>
<h3>3.1 点位表 (系统设置 → PLC 连接器 → 编辑连接)</h3>
<table>
<tr><th>key</th><th>地址</th><th>类型</th><th>方向</th><th>说明</th></tr>
<tr><td>plc_heartbeat</td><td>DB1200.DBX0.0</td><td>bool</td><td>读</td><td>不动</td></tr>
<tr><td>read_done</td><td>DB1200.DBX0.2</td><td>bool</td><td>读</td><td>不动</td></tr>
<tr><td>workpiece_pres</td><td>DB1200.DBX0.3</td><td>bool</td><td>读</td><td>不动</td></tr>
<tr><td>product_no</td><td>DB1200.DBB2</td><td>string_s7 · ascii</td><td>读</td><td>长度 30 → <b>28</b></td></tr>
<tr><td>cyl_type</td><td>DB1200.DBW32</td><td>int16</td><td>读</td><td>不动</td></tr>
<tr><td><b>result_code</b></td><td><b>DB1201.DBW2</b></td><td>int16</td><td>写</td>
    <td>结果字: 1=合格 2=不合格 0=检测中/无效 (原 DB1210.DBW20 改到这里)</td></tr>
<tr><td><b>machine_stop</b></td><td><b>DB1201.DBX0.4</b></td><td>bool</td><td>写</td>
    <td>1=停止 0=放行 (贵方 {TODAY} 确认)</td></tr>
<tr><td>vis_heartbeat</td><td>DB1201.DBX0.0</td><td>bool</td><td>写</td>
    <td>视觉心跳 1s 翻转 (协议第四节, 贵方不接可删)</td></tr>
<tr><td>vis_alarm</td><td>DB1201.DBX0.3</td><td>bool</td><td>写</td>
    <td>无码/异常报警位 (协议第四节, 贵方不接可删)</td></tr>
<tr><td colspan="5" style="color:#64748b">删除: result_ok / result_ng (结果走结果字, 不需要);
    data_received 数据已接收 (读完成自复位, 无需握手, 贵方要接再加 DB1201.DBX0.1)</td></tr>
</table>
<div class="tip">写方向点位不进实时读值缓存, 界面「实时值」列显示 “—” 是正常的;
验证写链路看连接详情页的 IO 日志。</div>

<h3>3.2 触发规则 read_rules (整段替换)</h3>
<p>三条规则: ① 读完成上升沿且有码 → 绑定工件编号 (进站取数);
② 读完成上升沿但产品号为空 → 无码报警 (协议异常约定①);
③ 读完成下降沿 = 工件出站 → 结果字/停止位/报警位全部清零 (贵方确认第 1/2 项)。</p>
{_code(READ_RULES_JSON)}
<div class="tip">若删掉了 vis_alarm 点位, 把规则②里 write_points 一段和规则③里
vis_alarm 一行同步删掉即可。</div>

<h3>3.3 写回规则 write_rules (整段替换)</h3>
<p>周期结束自动写: 合格 → 结果字 1 + 停止位 0 (放行);
不合格 → 结果字 2 + 停止位 1 (停机)。写后<b>保持</b>, 工件出站由规则③清零,
与贵方「读完成清掉跟着清」的约定一致。第三条为视觉心跳 (不接可删)。</p>
{_code(WRITE_RULES_JSON)}

<h3>3.4 虚拟按钮放行 (系统设置 → 触发中心)</h3>
<ol>
<li><b>结算按钮</b>: 新建触发源, 类型「虚拟按钮 (画面区域)」, 框选画面中
    手遮触发的区域; 动作选 <code>manual_settle</code> (手动结算), 工位 0。
    按下 → 当前周期立即结算 (合格/不合格仍按真实步骤与判定表判定,
    按钮只代替"时机") → §3.3 写回自动跟上: 合格放行、不合格停机。</li>
<li><b>NG 处理后放行按钮</b>: 再建一个虚拟按钮, 动作用
    <code>write_points</code> 写 0 解除停机 (结果字保持 2 不动,
    出站时统一清零):</li>
</ol>
{_code(RELEASE_ACTION_JSON)}
<div class="tip">connection 填 PLC 连接的名称或 ID。若判定逻辑本身会自动结算
(收尾步骤结算等), 第 1 个按钮可不建, 写回照样在周期结束时自动触发。</div>
<div class="warn"><b>需打补丁 v3.48.1g (含) 以上</b>: ① 旧版
<code>manual_settle</code> 只支持逐件覆盖模式, 缸体判型 (检测+判定表) 按了
无反应 (触发源日志报「未启用 per_item 模式」) —— 1g 起支持;
② 1g 起虚拟按钮标定的触发区域在<b>监控画面常驻显示</b>
(琥珀色虚线框 + 按钮名称), 操作员能直接看到往哪伸手;
停用触发源后区域框随之消失。旧版标定完画面上看不到区域, 属已修复问题。</div>

<h2>4. 补充确认 (不阻塞联调)</h2>
<div class="warn">协议第四节的增强项 —— 视觉心跳 (DBX0.0) / 数据已接收
(DBX0.1) / 检测完成 (DBX0.2) / 视觉报警 (DBX0.3) / 产品号回传 (DBB4) ——
贵方 PLC 程序实际接了哪几项? 本配置默认只接心跳 + 报警位, 其余不写;
要接/要删都只改点位表, 随时可调。</div>

<h2>5. 配置生效与验证步骤 (我方现场工程师照做)</h2>
<ol>
<li><b>替换配置</b>: 系统设置 → PLC 连接器 → 编辑该连接 → 按 §3.1 重建写点位
    (改 DB 号! 删 result_ok/result_ng) → 把 §3.2 / §3.3 两段 JSON 分别整段粘进
    「触发规则」「写回规则」→ 保存 (保存后连接自动热重载, 无需重启软件)。</li>
<li><b>确认工位有激活项目且在检测</b>: 绑码走扫码链路, 工位没有激活项目时
    编号会被静默丢弃 (后端日志出现 project_id empty 字样)。</li>
<li><b>验证绑码</b>: 让 PLC 过一个件。连接详情页 IO 日志应出现
    「触发 [读完成(有码)→绑码]」和「bind_sn → 工位0 编号 XXX」两行;
    监控页「当前工件」显示该编号; 数据中心该周期挂上编号。</li>
<li><b>验证写回</b>: 跑完一个检测周期 (或虚拟按钮手动结算), IO 日志应出现写
    result_code / machine_stop 记录; 请 PLC 工程师在 PLC 侧监视
    DB1201.DBW2 确认收到 1 或 2, 工件出站后回到 0。</li>
<li><b>常用自查口径</b>: 连接卡片「规则触发」计数 (rule_fires) 不涨 = 规则没
    命中 (查边沿/格式); IO 日志有触发但监控页无编号 = 查第 2 步激活项目。</li>
</ol>
<div class="warn">「读完成」上升沿必须先看到 0 再看到 1: 若软件连接时线上
已有工件 (该位已是 1), 要等这件出站、下一件进站才会触发第一次绑码。
联调从「工位无工件」状态开始。</div>

<p class="foot">天军 AI 视觉检测系统 · 现场纠偏与最终配置 (PLC 写回/工件绑码) V1.3 终版 · {TODAY} ·
全部确认事项已闭环, 本版即联调执行版 (V1.3: 虚拟按钮结算/区域显示随补丁 1g 生效)</p>
</body></html>"""


def main() -> int:
    html_path = ROOT / "docs" / "_plc_writeback_tmp.html"
    html_path.write_text(build_html(), encoding="utf-8")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.as_uri(), wait_until="networkidle")
        page.pdf(path=str(OUT), format="A4", print_background=True)
        browser.close()
    html_path.unlink(missing_ok=True)
    print(f"OK → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
