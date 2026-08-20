# -*- coding: utf-8 -*-
"""生成《现场配置手册 — 缸体判型 二期增补 (v3.49)》PDF。

内容: 2026-08-11 客户反馈五项的配置方法 (Monitor 大字卡+实时缸型 / 工序顺序
检查 / 少装补救闭环+结算挂起 / 追踪参数按标签覆盖 / PLC 缸型+工件编号对接),
外加"纯配置项"章节 (非 combo 步骤严格顺序 / 每步骤独立阈值帧数 / bind_sn)。

配图直接取可见浏览器 UAT 证据截图 (tests/manual_uat/evidence/
combo_guard_phase2_<date>/), 保证图与真实界面一致。

跑法:  python scripts/docgen/gen_cylinder_phase2_manual.py
输出:  docs/现场配置手册_缸体判型二期增补_<date>.pdf
依赖:  playwright (chromium headless 渲染 HTML → PDF)
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TODAY = f"{date.today():%Y-%m-%d}"
OUT = ROOT / "docs" / f"现场配置手册_缸体判型二期增补_{TODAY}.pdf"


def _find_evidence() -> Path:
    base = ROOT / "tests" / "manual_uat" / "evidence"
    cands = sorted(base.glob("combo_guard_phase2_*"), reverse=True)
    if not cands:
        raise SystemExit("未找到 combo_guard_phase2_* UAT 证据目录, 先跑 UAT")
    return cands[0]


def _img(ev: Path, name: str, caption: str) -> str:
    p = ev / name
    if not p.exists():
        return f'<p class="warn">[缺图 {name}]</p>'
    return (f'<figure><img src="{p.as_uri()}">'
            f'<figcaption>{caption}</figcaption></figure>')


def build_html(ev: Path) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>现场配置手册 — 缸体判型 二期增补</title>
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
  .cover {{ text-align: center; padding-top: 220px; page-break-after: always; }}
  .cover .band {{ color: #0e7490; font-size: 12pt; letter-spacing: 6px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 6px 0 10px;
          font-size: 9.5pt; page-break-inside: avoid; }}
  th, td {{ border: 1px solid #cbd5e1; padding: 4px 7px; text-align: left;
           vertical-align: top; }}
  th {{ background: #f1f5f9; }}
  code {{ background: #f1f5f9; padding: 1px 4px; border-radius: 3px;
         font-family: Menlo, Consolas, monospace; font-size: 9pt; }}
  figure {{ margin: 8px 0 14px; page-break-inside: avoid; }}
  figure img {{ width: 100%; border: 1px solid #cbd5e1; border-radius: 4px; }}
  figcaption {{ color: #64748b; font-size: 8.5pt; margin-top: 3px; }}
  .tip {{ background: #ecfeff; border-left: 4px solid #06b6d4;
         padding: 6px 10px; margin: 8px 0; }}
  .warn {{ background: #fffbeb; border-left: 4px solid #f59e0b;
          padding: 6px 10px; margin: 8px 0; }}
  .foot {{ color: #94a3b8; font-size: 8.5pt; text-align: center; margin-top: 26px; }}
  li {{ margin: 2px 0; }}
</style></head><body>

<div class="cover">
  <div class="band">天军 AI 视觉检测系统</div>
  <h1>现场配置手册 · 缸体判型 二期增补</h1>
  <p class="sub">Monitor 大字实时卡 · 实时缸型 · 工序顺序检查 · 少装补救闭环 · 结算挂起 · 按标签追踪参数</p>
  <p>适用版本 v3.49+ ｜ {TODAY}</p>
  <p class="sub">本册所有新功能<b>默认关闭</b>——不配置时软件行为与升级前完全一致。<br>
  与《现场配置手册 — 缸体装配判型与触发中心 V1.0 (2026-08-10)》配套使用。</p>
</div>

<h2>0. 本次新增能力总览 (客户反馈 → 配置项)</h2>
<table>
<tr><th>现场诉求 (2026-08-11)</th><th>对应能力</th><th>配置位置</th></tr>
<tr><td>缸号显示在界面上, 切换时显示缸型; 计数搞大放合适位置</td>
    <td>Monitor 大字实时卡 (计数 + 实时缸型)</td>
    <td>逻辑设置 → 判定表卡 → <b>监控大字实时卡</b></td></tr>
<tr><td>做工每步按顺序, 不按顺序报警</td>
    <td>判型标签间工序顺序检查</td>
    <td>切步数量门 → <b>工序顺序检查</b></td></tr>
<tr><td>每步做完少了件数直接报警不结算, 要有补救</td>
    <td>少装报警 + 补齐自动消警 + 结算挂起等补</td>
    <td>切步数量门 → 处置档 / <b>结算数量不符时</b></td></tr>
<tr><td>每个类相关参数单独配置, 不要统一参数</td>
    <td>追踪参数按标签覆盖</td>
    <td>计数口径(位置去重) → <b>按标签单独覆盖追踪参数</b></td></tr>
<tr><td>PLC 传入缸型和工件编号</td>
    <td>缸型点位 cyl_type + 工件编号 bind_sn (已有能力)</td>
    <td>PLC 连接器 (见第 5 章)</td></tr>
</table>

<h2>1. Monitor 判型实时看板 + 实时缸型</h2>
<p>打开后, 视频下方与 SOP 流程卡片<b>同一行右侧</b>停靠「判型实时看板」:
每个判型标签一块大数字瓦片 + 最右侧琥珀色「当前缸型」瓦片 (切缸型即时刷新),
<b>不遮挡视频画面</b>。底部原有小字信息条保留。</p>
<h3>配置项 (逻辑设置 → 计数组合判定表 → 监控大字实时卡)</h3>
<table>
<tr><th>配置</th><th>默认</th><th>说明</th></tr>
<tr><td>开关</td><td>关</td><td>关 = 只有底部小字条 (现状)</td></tr>
<tr><td>字号</td><td>大字</td><td>大字 / 普通 两档</td></tr>
<tr><td>显示缸型</td><td>开</td><td>关 = 只显示计数</td></tr>
<tr><td>缸型显示 PLC 连接/点位 (选填)</td><td>空</td>
    <td>独立点位来源; <b>留空自动用切步数量门里配的连接与点位</b>。
        独立配置后, 数量门没开也能显示缸型</td></tr>
</table>
<div class="tip">缸型标记反查: 界面显示的是判定表机型行里 <b>PLC 码</b> 匹配行的
「机型标记 (tag)」(如点位值 4 → 显示 "机型X/4缸"); 没配 PLC 码时直接显示原始值。</div>
{_img(ev, "01_bigcard_plc4.png", "图 1-1 判型实时看板 (SOP 卡片行右侧停靠): 各标签大数字瓦片 + 当前缸型 (PLC 值 4 → 机型X)")}
{_img(ev, "02_bigcard_plc6.png", "图 1-2 PLC 切缸型 4→6, 看板缸型瓦片即时切换为 机型Y")}

<h2>2. 工序顺序检查 (判型标签间)</h2>
<p>以判定表「参与判型的步骤标签」的配置顺序为工序顺序。开启后: 后面工序已开始,
又回头给前面工序<b>新增</b>计数 → 当场报「工序乱序」。</p>
<ul>
<li><b>补救豁免</b>: 该标签正处于「少装报警」中时回头补件 = 补救, 不算乱序;</li>
<li><b>报警不拦计数</b>: 乱序只报警提示, 计数照常累计, 结算仍按查表;</li>
<li>处置档与数量门共用 (当场提示 / 立即NG), 同违规只报一次。</li>
</ul>
<div class="warn">分工注意: 本开关只管<b>判型标签之间</b>的顺序 (如 座瓦→盖瓦)。
非判型步骤 (如 擦拭缸体→座瓦启动) 的顺序请用「步骤设置」里的<b>严格顺序</b>
(steps_config strict_order, 已有能力, 见第 6 章)。</div>
{_img(ev, "03_order_banner.png", "图 2-1 区B 已开始后又回头装区A → 红幅「工序乱序」")}

<h2>3. 少装补救闭环</h2>
<h3>3.1 提示档 (hint) 补救链</h3>
<p>少装切步红幅报警 → 工人直接回头补件 (计数持续累计) → 补到允许值
<b>自动消警</b>: 红幅转绿「已补齐」→ 收尾正常结算。</p>
<table>
<tr><th>配置</th><th>默认</th><th>说明</th></tr>
<tr><td>提示事件</td><td>不提示 (仅日志)</td>
    <td>违规红幅出现时额外触发的报警事件 (灯/蜂鸣/弹窗/计数)</td></tr>
<tr><td>已补齐事件</td><td>不触发</td>
    <td>补齐消警时额外触发的事件 (响一声/计一笔); 默认只消警+日志</td></tr>
</table>
<div class="warn"><b>提示事件 / 已补齐事件必须用自建专用事件</b>: 请先到【事件设置】
新建 (如「数量门警告」「已补齐提示」, 报警灯/蜂鸣/弹窗按需勾选), 再回数量门里选。
系统「合格(OK) / 不合格(NG)」是<b>结算事件</b>, 下拉里不可选 (老配置里选过的
也会自动失效, 只出横幅和日志) —— 这样 OK/NG 统计只反映真实结算, 不会被提示类
报警污染。提示类事件只借"报警响应面" (灯/蜂鸣/弹窗/自定义计数), <b>绝不结算周期</b>。</div>
{_img(ev, "04_under_red.png", "图 3-1 区A 只装 1 件就切区B → 红幅「切步数量不符」")}
{_img(ev, "05_resolved_green.png", "图 3-2 回头补第 2 件 → 绿幅「已补齐」自动消警")}
{_img(ev, "06_settle_ok.png", "图 3-3 补齐后收尾正常结算 OK, 绿幅随周期结束消失")}

<h3>3.2 结算挂起档 (on_settle_mismatch = 挂起等补)</h3>
<p>收尾结算时计数组合查表未命中, 默认直接 NG (防呆)。切到「挂起等补」档后:
<b>不结算不清计数</b>, 琥珀横幅提示差多少, 周期保持打开等工人补;
补齐命中判定表任一行 → <b>自动按该行结算</b> (OK 行判 OK); 超时未补 → 按原因 NG 落账。</p>
<table>
<tr><th>配置</th><th>默认</th><th>说明</th></tr>
<tr><td>结算数量不符时</td><td>直接NG</td><td>直接NG(现状) / 挂起等补</td></tr>
<tr><td>挂起超时(秒)</td><td>120</td><td>0 = 不限时; 超时按「计数组合未匹配」NG 落账</td></tr>
</table>
<div class="warn">挂起档建议配合「收尾步骤结算」(last_step) 使用;
「首步重现结算」下一件工件已经上线, 挂起旧周期意义有限。</div>
{_img(ev, "07_hold_amber.png", "图 3-4 查表未命中 → 琥珀横幅「结算挂起等补 (剩余秒数)」")}
{_img(ev, "08_hold_ok.png", "图 3-5 挂起中补齐 → 自动按命中行结算 OK")}
{_img(ev, "09_hold_timeout_ng.png", "图 3-6 超时未补 → 按原因 NG 落账, 挂起解除")}

<h2>4. 追踪参数按标签覆盖</h2>
<p>「按位置去重」计数口径的 6 个追踪参数, 原来全标签统一; 现在可给每个判型标签
单独配一套 (座瓦目标小抖动大 → 单独调低 IoU; 盖瓦动作慢 → 单独加大确认帧数)。</p>
<p>入口: 计数口径(位置去重) 参数区下方 → <b>按标签单独覆盖追踪参数</b> (点击展开)。
每行一个标签、六个格子; <b>留空的格子用上方全局值</b>, 全留空 = 与原来完全一致。</p>
<table>
<tr><th>参数</th><th>全局默认</th><th>调法速记</th></tr>
<tr><td>同位置判定 IoU</td><td>0.4</td><td>相邻位置被并掉→调小; 抖动多计→调大</td></tr>
<tr><td>位置平滑步长</td><td>0.6</td><td>越大锁框跟随越快</td></tr>
<tr><td>新位置确认帧数</td><td>3</td><td>误检多→调大; 动作快漏计→调小 (1=首帧即计)</td></tr>
<tr><td>候选保留节拍</td><td>10</td><td>断检后候选保留的推理帧数</td></tr>
<tr><td>单位置消失超时</td><td>0 (常驻)</td><td>已计位置断检 N 帧后作废重等确认</td></tr>
<tr><td>整类空闲重置</td><td>0 (不重置)</td><td>该类全部消失 N 帧后清空该类已计位置</td></tr>
</table>
{_img(ev, "00_phase2_config.png", "图 4-1 二期配置区总览: 顺序检查/已补齐事件/结算处置/大字卡/按标签覆盖")}

<h2>5. PLC 对接: 缸型 + 工件编号 (纯配置, 已有能力)</h2>
<h3>5.1 缸型点位 cyl_type (S7-300 DB 协议 V0.2)</h3>
<ol>
<li>系统设置 → PLC 连接器 → 新建连接 (驱动 S7; 联调可先用「虚拟 PLC」模板);</li>
<li>点位表加 <code>cyl_type</code> (V0.2 协议地址 <code>DB100.DBW32</code>, 类型 int16, 方向 读);</li>
<li>判定表机型行逐行填「PLC 码」(4缸行填 4, 6缸行填 6 — 与点位实时值宽松比对);</li>
<li>用途一 · 数量门: 切步数量门 → 期望数量依据 = <b>PLC 缸型点位</b> (或 自动),
    选该连接 + 点位 key <code>cyl_type</code> → 期望数量锁定到该缸型行;</li>
<li>用途二 · 大字显示: 大字卡「显示缸型」开 → 自动复用数量门的点位;
    数量门没开则在大字卡里单独配「缸型显示 PLC 连接/点位」。</li>
</ol>
<h3>5.2 工件编号 bind_sn (读写头追溯)</h3>
<p>PLC 连接器 → 读取规则: 当 <code>read_done</code> 上升沿 → 动作
<code>bind_sn</code>, 模板 <code>{{{{product_no}}}}</code>, 绑定到对应工位 —
工件编号自动建工件并随周期进数据中心/MES。详见 V1.0 手册第 5 章与
《PLC对接协议_S7-300_DB交互_V0.2》时序图。</p>

<h2>6. 纯配置项备忘 (不用等新版本的已有能力)</h2>
<table>
<tr><th>诉求</th><th>已有能力</th><th>配置位置</th></tr>
<tr><td>非判型步骤按顺序做, 乱序报警</td>
    <td>步骤严格顺序 (strict_order)</td>
    <td>逻辑设置 → NG 判定与处置 → 违规出现时 (提示/立即NG) + 步骤顺序按步骤表排列</td></tr>
<tr><td>每个步骤各自的识别灵敏度</td>
    <td>步骤级独立 置信度阈值 / 确认帧数</td>
    <td>步骤设置 → 每行步骤的 阈值 / 最少帧数 列 (无需全局统一)</td></tr>
<tr><td>工件编号追溯</td><td>bind_sn (见 5.2)</td><td>PLC 连接器读取规则</td></tr>
</table>

<h2>7. 新增配置键速查 (给维护工程师)</h2>
<table>
<tr><th>配置键 (pipeline_config.combo_table.*)</th><th>默认</th><th>作用</th></tr>
<tr><td><code>live_display.enabled/size/position/show_plc_type</code></td>
    <td>关/large/top/开</td><td>Monitor 大字实时卡</td></tr>
<tr><td><code>plc_display.connection_id/point</code></td><td>空</td>
    <td>缸型显示独立点位 (缺省回落 step_guard 点位)</td></tr>
<tr><td><code>step_guard.check_order</code></td><td>false</td><td>判型标签间工序顺序检查</td></tr>
<tr><td><code>step_guard.resolved_event_id</code></td><td>null</td><td>补齐消警时触发的事件</td></tr>
<tr><td><code>step_guard.on_settle_mismatch</code></td><td>ng</td><td>结算查表未命中: ng / hold</td></tr>
<tr><td><code>step_guard.hold_timeout_s</code></td><td>120</td><td>挂起超时秒数 (0=不限)</td></tr>
<tr><td><code>tracking_per_label.{{标签}}.{{参数}}</code></td><td>空</td>
    <td>追踪六参数按标签覆盖 (留空回落全局 tracking)</td></tr>
</table>
<div class="tip">以上全部默认关/空 —— 老项目升级后不动配置, 行为与升级前逐帧一致。</div>

<p class="foot">天军 AI 视觉检测系统 · 现场配置手册 缸体判型二期增补 V1.0 · {TODAY} ·
如与软件实际界面不符, 以软件为准</p>
</body></html>"""


def main() -> int:
    ev = _find_evidence()
    html_path = ROOT / "docs" / "_phase2_manual_tmp.html"
    html_path.write_text(build_html(ev), encoding="utf-8")
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
