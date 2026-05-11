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


# ============================================================
# 注册表
# ============================================================

# 每项: (builtin_id, name, format, scope, content, description)
_BUILTIN_TEMPLATES = [
    (
        "builtin_sn_pass_fail_txt",
        "客户 SN.txt（Pass/Fail 三行）",
        "txt",
        "realtime",
        _TPL_SN_PASS_FAIL_TXT,
        _DESC_SN_PASS_FAIL_TXT,
    ),
    (
        "builtin_cycle_simple_txt",
        "单 cycle 简明文本",
        "txt",
        "both",
        _TPL_CYCLE_SIMPLE_TXT,
        _DESC_CYCLE_SIMPLE_TXT,
    ),
    (
        "builtin_session_cycles_csv",
        "session cycle 列表 CSV",
        "csv",
        "batch",
        _TPL_SESSION_CYCLES_CSV,
        _DESC_SESSION_CYCLES_CSV,
    ),
    (
        "builtin_session_5step_csv",
        "session 5 步产线 CSV (会话信息+计数器+周期明细)",
        "csv",
        "batch",
        _TPL_SESSION_5STEP_CSV,
        _DESC_SESSION_5STEP_CSV,
    ),
]


def seed_builtin_templates() -> None:
    """启动时刷新所有内置模板，幂等。"""
    inserted = 0
    updated = 0
    with DBSession(engine) as db:
        for builtin_id, name, fmt, scope, content, description in _BUILTIN_TEMPLATES:
            row = db.query(ExportTemplate).filter(
                ExportTemplate.builtin_id == builtin_id
            ).first()
            if row is None:
                row = ExportTemplate(
                    builtin_id=builtin_id,
                    name=name,
                    format=fmt,
                    scope=scope,
                    content=content,
                    description=description,
                    is_system=True,
                )
                db.add(row)
                inserted += 1
            else:
                changed = False
                # 名字保留用户修改的（避免覆盖客户改的中文名），但允许首次显示我们的默认名
                if row.format != fmt:
                    row.format = fmt; changed = True
                if row.scope != scope:
                    row.scope = scope; changed = True
                if row.content != content:
                    row.content = content; changed = True
                if row.description != description:
                    row.description = description; changed = True
                if not row.is_system:
                    row.is_system = True; changed = True
                if changed:
                    updated += 1

        db.commit()

    if inserted or updated:
        print(f"[Export] 系统预设模板: 新增 {inserted} 条, 更新 {updated} 条 "
              f"(共 {len(_BUILTIN_TEMPLATES)} 条预设)")
