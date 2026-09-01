"""
v3.5.0 自定义导出系统 — 系统预设模板初始化

每次后端启动时调用 seed_builtin_templates()：
- 不存在的内置模板：插入
- 已存在的内置模板（按 builtin_id 匹配）：刷新 content / format / description
  这样客户升级版本就能拿到最新的预设，不需要手动同步。
- 用户复制后的自建模板（is_system=False, builtin_id=NULL）不动。
- 用户对内置模板的修改会被覆盖 — 让客户改自建副本而不是直接改预设。

新增内置模板的步骤：
1. 在 _BUILTIN_TEMPLATES 列表里加一项
2. 写好 builtin_id（永久标识，代码引用它）+ content
3. 重启后端
"""
from sqlalchemy.orm import Session as DBSession
from backend.db.database import engine
from backend.models.export_models import ExportTemplate


# ============================================================
# 内置模板内容
# ============================================================

# ---- 客户 SN.txt 三行模板（demo 用） ----
_TPL_SN_PASS_FAIL_TXT = """{% if cycle.is_good %}Pass{% else %}Fail{% if cycle.ng_code %},{{ cycle.ng_code }}{% endif %}{% endif %}
{% for v in cycle.test_values | default([]) %}{{ v }}{% if not loop.last %},{% endif %}{% endfor %}
REVISION={{ app.version }}"""

_DESC_SN_PASS_FAIL_TXT = (
    "客户 SN.txt 三行格式示例：\n"
    "  第1行：Pass / Fail,NG码\n"
    "  第2行：测试值列表（逗号分隔，需在项目配置里把检测结果映射到 cycle.test_values）\n"
    "  第3行：程式版本号 REVISION=Vx.y.z\n"
    "建议配合「实时输出 → 输入文件改写：read_template」模式使用，从 input_dir 找客户提供的 SN 模板文件。"
)


# ---- 单 cycle 简明文本（基础示例，演示常用字段） ----
_TPL_CYCLE_SIMPLE_TXT = """SN: {{ workpiece.serial_no or 'N/A' }}
Result: {{ '良品' if cycle.is_good else 'NG' }}
Cycle ID: {{ cycle.id }}
Start: {{ cycle.start_time }}
Duration: {{ '%.2f' | format(cycle.duration or 0) }}s
Project: {{ project.name or '-' }}
Operator: {{ operator.name or '-' }}
Channel: {{ session.channel_id }}
Steps:
{% for step in steps -%}
  - [{{ '良' if step.is_good else 'NG' }}] {{ step.label }} ({{ '%.2f' | format(step.duration or 0) }}s, conf {{ '%.3f' | format(step.confidence or 0) }})
{% endfor %}
{% if ng_steps -%}
NG Steps: {{ ng_steps | map(attribute='label') | join(', ') }}
{%- endif %}
App: {{ app.name }} {{ app.version }}
"""

_DESC_CYCLE_SIMPLE_TXT = "单 cycle 简明文本 — 演示如何引用工件/项目/步骤/操作员等基础字段，可作为编写自定义模板的起点。"


# ---- session 范围 CSV（基础示例） ----
_TPL_SESSION_CYCLES_CSV = """cycle_id,start_time,duration_s,is_good,ng_step
{% for c in stats.cycles -%}
{{ c.id }},{{ c.start_time }},{{ '%.3f' | format(c.duration or 0) }},{{ 1 if c.is_good else 0 }},{{ c.ng_step or '' }}
{% endfor %}"""

_DESC_SESSION_CYCLES_CSV = "session 范围 cycle 列表 CSV — 演示如何遍历 cycles 数组，适用于批量导出。"


# ---- 客户 5 步产线 (拿取/正面涂黑/翻转/反面涂黑/放置) 完整 CSV ----
# v3.7.0+ 客户老胡场景: 把 session 下每个周期的 5 个步骤分别独立成列, 配会话头 + 计数器统计.
# 已规避 4 个常见坑:
#   - good_cycles/total_cycles 字段名 (不是 good_count/total_count)
#   - avg_cycle_time (不是 avg_duration)
#   - or '' 替代 |default('') 以处理 Python None
#   - '%.2f' % 格式化避免 5.239999999999999 浮点尾巴
_TPL_SESSION_5STEP_CSV = """会话信息
会话ID,{{ session.id }}
开始时间,{{ session.start_time or '' }}
结束时间,{{ session.end_time or '' }}
总周期数,{{ stats.cycles|length }}
合格数,{{ stats.good_cycles or 0 }}
不良数,{{ stats.ng_cycles or 0 }}
平均周期时间(秒),{{ '%.2f' % (stats.avg_cycle_time or 0) }}

计数器统计
计数器名称,数值
合格总数,{{ stats.good_cycles or 0 }}
不良总数,{{ stats.ng_cycles or 0 }}
总产量,{{ stats.total_cycles or 0 }}

周期序号,开始时间,结束时间,耗时(秒),周期间隔(秒),结果,事件,步骤序列,拿取,正面涂黑,翻转,反面涂黑,放置
{% for cycle in stats.cycles -%}
{% set step_by_label = {} -%}
{% for s in cycle.steps -%}
{% set _ = step_by_label.update({s.label: s}) -%}
{% endfor -%}
{{ cycle.cycle_number }},{{ cycle.start_time or '' }},{{ cycle.end_time or '' }},{{ '%.2f' % (cycle.duration or 0) }},{{ '%.2f' % (cycle.interval or 0) if cycle.interval else '' }},{{ '合格' if cycle.is_good else '不良' }},{{ cycle.event or '' }},{{ cycle.steps|map(attribute='label')|join(' -> ') }},{{ '%.2f' % (step_by_label['拿取'].duration if '拿取' in step_by_label else 0) }},{{ '%.2f' % (step_by_label['正面涂黑'].duration if '正面涂黑' in step_by_label else 0) }},{{ '%.2f' % (step_by_label['翻转'].duration if '翻转' in step_by_label else 0) }},{{ '%.2f' % (step_by_label['反面涂黑'].duration if '反面涂黑' in step_by_label else 0) }},{{ '%.2f' % (step_by_label['放置'].duration if '放置' in step_by_label else 0) }}
{% endfor %}"""

_DESC_SESSION_5STEP_CSV = (
    "session 5 步产线 CSV (客户老胡场景) —\n"
    "  • 数据范围选「单 session」, 系统会自动加载所有 cycle + 步骤明细\n"
    "  • 表头三段: 会话信息 / 计数器统计 / 周期明细 (每周期一行, 含 5 步独立列)\n"
    "  • 默认列名为「拿取/正面涂黑/翻转/反面涂黑/放置」, 如果产线步骤不一样,\n"
    "    复制本模板后, 修改表头那行 + for 循环里 5 个 step_by_label[...] 的中文名即可\n"
    "  • 数字已统一格式化为 2 位小数 (5.24 而非 5.239999999...)\n"
    "  • 没值的字段会显示为空白 (不再出现 None 字面量)"
)


# ---- 扫码器旁路三行 TXT (v3.7.2 客户场景, v3.7.3 调整文案) ----
# 场景: 客户扫码器无法接入软件, 但会在固定目录里生成 txt (空 / 仅一行序列号).
# 每个 cycle_end 时, 我们从该目录读 mtime 最新的 txt 文件, 把内容作为首行保留,
# 再追加 "Pass/Fail → 每步时长 → 软件版本" 三行, 写到客户指定的输出目录.
#
# Jinja2 helper:
#   latest_input_text() : 读输入目录最新 txt 文件内容 (空目录返回空)
#   latest_input_filename() : 取最新文件名 (用于"输出文件名跟扫码 txt 同名")
# 不传参时自动用 ExportRealtimeRule.input_dir.
#
# 模板版面:
#   第 1 行: 扫码序列号 (来自客户扫码 txt)
#   第 2 行: 空行 (客户要求)
#   第 3 行: Pass / Fail (v3.7.3: 由"合格/不合格"改成 Pass/Fail, 客户固定英文要求)
#   第 4 行: 各步骤检测时长 (v3.7.3: 去掉 label, 只按 step_order 顺序输出耗时, " | " 分隔)
#   第 5 行: 软件版本号 (v3.7.3 配合 env 注入修复, 打包后客户机不再显示 0.0.0)
_TPL_SCANNER_BYPASS_3LINE = """{{ latest_input_text() }}

{{ 'Pass' if cycle.is_good else 'Fail' }}
{% for s in steps %}{{ '%.2f' % (s.duration or 0) }}s{% if not loop.last %} | {% endif %}{% endfor %}
{{ app.version }}
"""

_DESC_SCANNER_BYPASS_3LINE = (
    "扫码器旁路三行 TXT (v3.7.2 新增, v3.7.3 改 Pass/Fail + 仅耗时) —\n"
    "  适用场景: 客户扫码器无法接入软件, 但会在固定目录写 txt 文件.\n"
    "\n"
    "  输出文件版面 (共 5 行):\n"
    "    L1: 扫码序列号 (从 input_dir 读最新 txt, 内容原样保留)\n"
    "    L2: 空行\n"
    "    L3: Pass / Fail (v3.7.3 起统一英文, 客户要求)\n"
    "    L4: 各步骤耗时, 按 step_order 顺序, 仅数字+秒, 用 ' | ' 分隔, 不带步骤名\n"
    "    L5: 软件版本号 (例 3.7.3)\n"
    "\n"
    "  用法:\n"
    "  1. 复制本预设到自建模板\n"
    "  2. 新建「实时规则」, 触发事件选「cycle_end」\n"
    "  3. 输入目录 = 客户扫码器生成 txt 的目录 (例: D:/扫码器输入/)\n"
    "  4. 输入文件模式选「none」 (内容靠 Jinja2 helper 取, 不要选 append)\n"
    "  5. 输出目录 = 客户希望的结果目录 (例: D:/检测结果/)\n"
    "  6. 输出文件名模板选下面任一:\n"
    "     • 跟扫码 txt 同名: {{ latest_input_filename() }}\n"
    "     • 加 cycle ID:    {{ latest_input_filename() | replace('.txt','') }}_{{ cycle.id }}.txt\n"
    "     • 时间戳:         {{ now('%Y%m%d_%H%M%S') }}.txt\n"
    "     • 客户自定义:     按 Jinja2 语法自由写\n"
    "  7. 换行符建议选「crlf」(Windows 客户)\n"
    "  8. 「取文件策略」三选一 (新建规则默认 C 推荐):\n"
    "     • C (cycle_start_snapshot) 周期开始锁快照, 最稳, DB 留痕\n"
    "     • B (mtime_stable) 取最新 + 等稳定 + 限制年龄, 加保险\n"
    "     • A (mtime) 直接取 mtime 最新, 调试 / 客户不严格\n"
    "  9. 同名去重 (可选): 防止两周期撞同一份扫码 txt, 命中重名异步等下一个新文件,\n"
    "     超时后跳过本规则 (写 SKIPPED 日志, 不写文件)\n"
    "\n"
    "  输出示例 — Pass 周期 (扫码 txt 内容 = 'WP20260513_001', 5 步全部完成):\n"
    "    WP20260513_001\n"
    "    \n"
    "    Pass\n"
    "    2.34s | 5.67s | 1.89s | 2.10s | 0.98s\n"
    "    3.7.3\n"
    "\n"
    "  输出示例 — Fail 周期 (在第 3 步失败, 后续两步未执行):\n"
    "    WP20260513_002\n"
    "    \n"
    "    Fail\n"
    "    2.34s | 5.67s | 1.89s\n"
    "    3.7.3\n"
    "    ← 注意: Fail 时只输出已执行的步骤, 未走到的步骤不会以 0 占位\n"
    "      (因为 step_records 表里就没那几行). 客户如果要求 Fail 时仍输出\n"
    "      5 个固定列 (未到的填 0 / -), 改模板用 project.sequence_order\n"
    "      做「按预定顺序补位」循环, 见自定义导出文档.\n"
    "\n"
    "  注意:\n"
    "  • 扫码器生成的 txt 我们只读, 不动 (客户自己定期清理)\n"
    "  • 扫码 txt 为空时, 输出文件首行也是空 (cycle_end 不会报错)\n"
    "  • 多工位场景每个工位建一条规则, 用「通道过滤」字段区分\n"
    "  • C 策略下 cycle.external_meta 字段会留存「这一轮锁了哪份扫码 txt」, 事后可追溯\n"
    "  • app.version 来自 Electron 启动后端时注入的 env (v3.7.3 修复), 打包后不会再显示 0.0.0"
)


# ---- 多码采集码组 TXT (v3.56 六和焊接一号工位场景) ----
# 现场期望版面 (扫码回传/txt格式期望.png):
#   工装码: H-C0002-557B-2
#   母排码: M010200519A100005036272608310061
#   芯子码1: 9260000154409  (15:47:01)
#   ...
#   工位: 工位1
#   时间: 2026-08-26 15:48:45  结果: OK
# 模板不写死类别名 — 遍历 scan_collect.slots, 收尾槽(工件身份)置顶,
# 应扫 >1 的槽位自动编号; 每码带扫码时刻 (确认单 6.5 勾选)。
_TPL_SCAN_GROUP_TXT = """{% for s in scan_collect.slots if s.role == 'closing' -%}
{% for c in s.codes -%}
{{ s.label }}: {{ c.code }}
{% endfor -%}
{% endfor -%}
{% for s in scan_collect.slots if s.role != 'closing' -%}
{% for c in s.codes -%}
{{ s.label }}{% if s.expected > 1 %}{{ loop.index }}{% endif %}: {{ c.code }}  ({{ c.ts }})
{% endfor -%}
{% endfor -%}
工位: {{ channel.name or '-' }}
时间: {{ now_date }} {{ now_time }}  结果: {{ 'OK' if scan_collect.is_good else 'NG' }}
{% if not scan_collect.is_good -%}
原因: {{ scan_collect.reason }}
{% for m in scan_collect.missing -%}
缺扫: {{ m.label }} {{ m.got }}/{{ m.expected }}
{% endfor -%}
{% endif -%}
"""

_DESC_SCAN_GROUP_TXT = (
    "多码采集码组 TXT (v3.56 一号工位多码扫码场景) —\n"
    "  一个工件(码组)结算落一个 txt: 收尾码(工件身份)置顶, 其余按类别逐行,\n"
    "  应扫多个的类别自动编号, 每码带扫码时刻; NG 时附原因与缺扫明细。\n"
    "\n"
    "  输出示例 — OK:\n"
    "    工装码: H-C035-527-5\n"
    "    母排码: M010200519A100005036272608310061  (15:47:01)\n"
    "    芯子码1: 9260000154409  (15:47:12)\n"
    "    芯子码2: 9260000153896  (15:47:20)\n"
    "    ...\n"
    "    工位: 工位1\n"
    "    时间: 2026-08-26 15:48:45  结果: OK\n"
    "\n"
    "  输出示例 — NG(少扫):\n"
    "    (已扫各码逐行...)\n"
    "    时间: ...  结果: NG\n"
    "    原因: 少扫判 NG：芯子码缺2\n"
    "    缺扫: 芯子码 4/6\n"
    "\n"
    "  用法:\n"
    "  1. 先在「MES管理 → 扫码器 → 多码采集」给项目启用多码采集\n"
    "  2. 新建「实时规则」, 触发事件选「scan_group_end (码组结算)」\n"
    "  3. 输出目录 = 客户希望的落盘目录 (可与检测数据同目录)\n"
    "  4. 文件名模板默认「工件码_时间.txt」(工装码跨工件重复, 时间戳保证不覆盖)\n"
    "  5. 编码默认 utf-8-sig (带 BOM, Excel 直接打开不乱码), 换行 crlf\n"
    "  6. NG 的码组同样落盘并标记 NG (确认单 4.6); 不想落 NG 可在模板加 if 守门\n"
    "  7. 多工位场景用「通道过滤」为不同工位分目录建多条规则"
)

# 选这个模板新建规则时前端自动填 — 客户只需填输出目录。
_DEFAULT_RULE_SCAN_GROUP = {
    "trigger_event": "scan_group_end",
    "input_file_mode": "none",
    "filename_template":
        "{{ scan_collect.workpiece_sn }}_{{ now_ymdhms }}.txt",
    "encoding": "utf-8-sig",                 # Excel 打开不乱码 (确认单 6.6/6.7)
    "newline": "crlf",                       # Windows 客户
    "overwrite_policy": "overwrite",
}


# ============================================================
# 注册表
# ============================================================

# 每项: (builtin_id, name, format, scope, content, description)
# v3.7.2 扫码器旁路场景的 "推荐规则配置". 选这个模板新建规则时, 前端自动填.
# 客户只需要填 input_dir + output_dir 两个目录就能跑.
_DEFAULT_RULE_SCANNER_BYPASS = {
    "trigger_event": "cycle_end",
    "input_file_mode": "none",
    "filename_template": "{{ latest_input_filename() }}",
    "encoding": "utf-8",
    "newline": "crlf",                       # Windows 客户
    "overwrite_policy": "overwrite",
    "latest_file_strategy": "cycle_start_snapshot",   # C 策略
    "latest_file_wait_stable_ms": 100,       # 100ms 稳定等待
    "latest_file_max_age_sec": 60,           # 60s 内的扫码 txt 才采用
    "dedupe_same_filename": False,           # 默认关 — 客户决定是否开
    "dedupe_retry_max_sec": 5,
    "dedupe_retry_interval_ms": 100,
}


_BUILTIN_TEMPLATES = [
    {
        "builtin_id": "builtin_sn_pass_fail_txt",
        "name": "客户 SN.txt（Pass/Fail 三行）",
        "format": "txt",
        "scope": "realtime",
        "content": _TPL_SN_PASS_FAIL_TXT,
        "description": _DESC_SN_PASS_FAIL_TXT,
        "default_rule_config": None,
    },
    {
        "builtin_id": "builtin_cycle_simple_txt",
        "name": "单 cycle 简明文本",
        "format": "txt",
        "scope": "both",
        "content": _TPL_CYCLE_SIMPLE_TXT,
        "description": _DESC_CYCLE_SIMPLE_TXT,
        "default_rule_config": None,
    },
    {
        "builtin_id": "builtin_session_cycles_csv",
        "name": "session cycle 列表 CSV",
        "format": "csv",
        "scope": "batch",
        "content": _TPL_SESSION_CYCLES_CSV,
        "description": _DESC_SESSION_CYCLES_CSV,
        "default_rule_config": None,
    },
    {
        "builtin_id": "builtin_session_5step_csv",
        "name": "session 5 步产线 CSV (会话信息+计数器+周期明细)",
        "format": "csv",
        "scope": "batch",
        "content": _TPL_SESSION_5STEP_CSV,
        "description": _DESC_SESSION_5STEP_CSV,
        "default_rule_config": None,
    },
    {
        "builtin_id": "builtin_scanner_bypass_3line_txt",
        "name": "扫码器旁路三行 TXT (序列号+OK/NG+步骤时长+版本)",
        "format": "txt",
        "scope": "realtime",
        "content": _TPL_SCANNER_BYPASS_3LINE,
        "description": _DESC_SCANNER_BYPASS_3LINE,
        "default_rule_config": _DEFAULT_RULE_SCANNER_BYPASS,
    },
    {
        "builtin_id": "builtin_scan_group_txt",
        "name": "多码采集码组 TXT (一工件一文件, 分类分行)",
        "format": "txt",
        "scope": "realtime",
        "content": _TPL_SCAN_GROUP_TXT,
        "description": _DESC_SCAN_GROUP_TXT,
        "default_rule_config": _DEFAULT_RULE_SCAN_GROUP,
    },
]


def seed_builtin_templates() -> None:
    """启动时刷新所有内置模板，幂等。"""
    inserted = 0
    updated = 0
    with DBSession(engine) as db:
        for tpl_spec in _BUILTIN_TEMPLATES:
            builtin_id = tpl_spec["builtin_id"]
            row = db.query(ExportTemplate).filter(
                ExportTemplate.builtin_id == builtin_id
            ).first()
            if row is None:
                row = ExportTemplate(
                    builtin_id=builtin_id,
                    name=tpl_spec["name"],
                    format=tpl_spec["format"],
                    scope=tpl_spec["scope"],
                    content=tpl_spec["content"],
                    description=tpl_spec["description"],
                    default_rule_config=tpl_spec.get("default_rule_config"),
                    is_system=True,
                )
                db.add(row)
                inserted += 1
            else:
                changed = False
                # 名字保留用户修改的（避免覆盖客户改的中文名），但允许首次显示我们的默认名
                if row.format != tpl_spec["format"]:
                    row.format = tpl_spec["format"]; changed = True
                if row.scope != tpl_spec["scope"]:
                    row.scope = tpl_spec["scope"]; changed = True
                if row.content != tpl_spec["content"]:
                    row.content = tpl_spec["content"]; changed = True
                if row.description != tpl_spec["description"]:
                    row.description = tpl_spec["description"]; changed = True
                # v3.7.2: default_rule_config 总是用预设最新版覆盖
                # (这是"系统推荐", 客户改了 rule 字段时是改 ExportRealtimeRule, 不是模板)
                new_drc = tpl_spec.get("default_rule_config")
                if (row.default_rule_config or None) != new_drc:
                    row.default_rule_config = new_drc; changed = True
                if not row.is_system:
                    row.is_system = True; changed = True
                if changed:
                    updated += 1

        db.commit()

    if inserted or updated:
        print(f"[Export] 系统预设模板: 新增 {inserted} 条, 更新 {updated} 条 "
              f"(共 {len(_BUILTIN_TEMPLATES)} 条预设)")
