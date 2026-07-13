"""耗材寿命约束 + 跨视角联动状态机（逐帧复刻 demo）。

挂在主程序 detection_frame 钩子上（observe-only，不改主程序状态机）：
- 视角1（计数通道）：按 demo ProductCounter 逐帧跑锚动作生命周期 → 离开计 1 件
  → 棉签已用 +1；达 K → 锁定 + 报警提示（锁定后暂停计数，paused 同 demo）。
- 视角2（换棉签通道）：按 demo 换棉签稳定窗口检出更换动作 → 清零解锁。

接管检测计数：与 A 重构式不同，计数完全由插件按 demo 算法逐帧自计算，
不再依赖主程序周期事件，因此能逐件复刻 demo 的计数精度。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from .counter import (
    ProductCounter,
    SwabChangeWindow,
    StillFakeActionDetector,
    AbsentCountdown,
    det_in_roi,
)

# 调参基础设施: 设 SENSOR_RECORD_FRAMES=<path> 时, 视角1 每帧锚框情况追加到该文件,
# 供离线快速试不同计数参数 (默认关闭, 不影响生产)。
_RECORD_PATH = os.environ.get("SENSOR_RECORD_FRAMES") or ""

log = logging.getLogger("tianjun.plugin.sensor_clean")

CUSTOMER_CODE = "sensor-clean"
# 命名空间合法 key（write_system_config 要求 plugin_<customer_code 下划线化>_ 前缀）
CONFIG_KEY = "plugin_sensor_clean_config"

# 默认配置（detect6 算法参数，阈值归一化 @1728 宽；与 preset.SWAB_CONFIG 保持一致）
DEFAULT_CONFIG = {
    "count_channels": [0],                    # 视角1 计数通道
    "count_anchor_label": "查看产品有无脏污",   # 视角1 锚动作标签 (detect9(1) cls0, 如"正常产品")
    # v1.4.0 双类别计数许可 (detect9(1) _both_seen 语义, 替换 v1.3.0 同帧门槛):
    # 非空时该标签作为"计数许可"—— 它出现过(不必与锚框同帧)即解锁, 锚框位移达标
    # 才计 1 件; 计数后许可立即重置, 下一件需再次见到它; 锚框跟踪丢失销毁也重置。
    # 现场两类交替出现(先脏污后正常)也能正确计数, 这是与 v1.3.0 同帧门槛的本质区别。
    # 空串 = 不启用, 仅锚标签移动即计数 (v1.2.0 行为)。
    # v1.4.2 起默认开启 (客户真值视频标定的出厂值, 免手工配置)。
    "count_require_label": "擦拭产品",
    # v1.4.0 按标签 ROI 区域 (复用主程序归一化多边形约定, [[x,y],...] 0~1 坐标,
    # 中心点在多边形内才算数; 空/少于3点 = 不限制)。key 对应各判定标签角色:
    #   count_anchor / count_require → 视角1 计数锚标签 / 许可标签
    #   swap → 视角2 换棉签标签;  fake_wipe → 视角1 假擦拭标签
    "label_rois": {},
    "swap_channel": 1,                        # 视角2 换棉签通道
    "swap_label": "更换棉签",                  # 视角2 换棉签动作标签
    "max_uses_per_swab": 11,                  # 一根棉签最多擦几个产品 (K)
    # 视角2 换棉签解锁判定 (独立于视角1 计数; 不用 detect7 移动即计数 — 真值统计证明它
    # 对小幅换棉签动作严重漏检, 改用"出现稳定 + 持续时长"判定, 更贴真实换棉签次数)
    "swab_lock_time": 2.0,                    # 两次解锁最小间隔(秒), 防一次动作重复解锁
    "swab_min_sustain_sec": 0.0,             # 动作段最短持续(秒), 默认0=照搬 demo 不防抖; 现场要滤瞬时误检调大(如0.12), 用秒跨帧率鲁棒
    "swab_gap_sec": 0.2,                      # 动作段内允许的丢框间隔(秒), 超过算新段
    "alarm_event": "",                        # (兼容老配置) 锁定时直接触发的报警事件类型 (空=不触发)
    # —— v1.1.0 三判定 → 主程序事件 (走 host.trigger_event, 报警/计数器/Toast 全由主程序联动) ——
    # event_id 指向「对应通道激活项目」events_config 里已配好的事件 (1=合格 / 2=NG / 其它自定义);
    # 默认 2=系统 NG。设 0/空 = 关掉该判定 (默认假擦拭/超限开、操作员离开关, 保持零差异)。
    "swab_over_limit_event_id": 2,            # 判定1: 棉签寿命超限 → 触发的事件 id
    "fake_wipe_event_id": 2,                  # 判定2: 假擦拭 → 触发的事件 id
    "fake_wipe_label": "擦拭产品",             # 假擦拭追踪的标签 (视角1 cid1)
    "fake_wipe_still_time": 2.0,              # 静止超时秒 (detect7(1) SWAB_STILL_TIME)
    "fake_wipe_still_disp": 0.0058,           # 位移阈值 归一化 (detect7(1) 10px / 1728)
    "operator_absent_enabled": False,         # 判定3 开关 (默认关, 不误报)
    "operator_absent_event_id": 2,            # 判定3: 操作员离开超时 → 触发的事件 id
    "operator_absent_timeout_sec": 600,       # 离开超时秒 (detect7(1) 默认 600=10min)
    # 正常计件 → 主程序事件 (0/空=仅插件看板计数, 不联动灯/Toast/主页计数器)
    "normal_count_event_id": 1,               # 默认 1=合格 OK; 可改为 2=NG 或自定义事件 id
    # 主程序检测模式周期结算仍并行跑, 但插件已接管计件 → 默认抑制其塔灯 (只留三判定+计件事件)
    "suppress_main_settle_alarm": True,
    # 逐帧计数参数 (移动即计数 + 帧硬锁)。v1.4.0 起阈值对齐 detect9(1):
    # demo 在 960 宽显示帧上算像素距离 (MOVE=20px / LOCK_SPATIAL=25px),
    # 折算归一化 = 像素/960。客户视频真值回放 (视角1-正常.mp4 38 件) 逐件对齐。
    "move_threshold": 0.02083,                # 移动判定 (detect9(1) 20px / 960)
    "lock_spatial": 0.02604,                  # 位置锁范围 (detect9(1) 25px / 960)
    "lock_time": 3.0,                         # 位置锁 / 计数冷却 (秒)
    "move_confirm_frames": 3,                 # 连续 N 帧位移超阈值才确认移动
    "lost_frame_thresh": 5,                   # 连续丢失 N 帧确认产品离开
    # 纵向位移权重: demo 像素域欧氏距离折算归一化域时纵向要乘 (高/宽),
    # 1728x1080 与 16:9 现场取 0.625/0.5625; 1.0=等权 (v1.2.0 老行为)
    "dist_y_weight": 0.625,
    # 计数后强制锁定帧数 (detect7(1) 原值 40)。源帧率 <= 推理速度(约56fps)时不丢帧、
    # 实时时钟=视频时间, 40 即对齐基准; 高帧率源(如60fps test.mp4)丢帧需调大。
    "force_lock_frames": 40,
    # v1.4.0 时间制阈值 (>0 启用并替代上面对应帧数制; 0=帧数制老行为)。
    # 主程序实时推理丢帧 (如 30fps 源 23fps 推理), 帧数制会让跟踪存活过久 →
    # 短暂离场没被确认、重建后位移累积 → 小幅度视频多计 (真值 2 实测 4~5)。
    # 时间制按真实缺席时长判离场, 跨帧率语义一致。v1.4.2 定稿 0.25s:
    # 18~21fps 处理速率下正好复刻 demo「缺席 4 帧存活 / 5 帧销毁」语义,
    # 客户双真值视频主环境实测逐件对齐 (正常=38, 小幅度=2, 与 demo 一致)。
    "lost_gone_sec": 0.25,                    # 锚缺席 >= N 秒确认离开 (0=用帧数制)
    "force_lock_sec": 1.6,                    # 计数后强锁 N 秒 (0=用帧数制)
    # v1.4.1 插件内置信度地板 (detect9(1) CONF_THRES=0.7)。主程序把监控页
    # 置信度滑条以下的框全喂给钩子, 滑条调低 (如 0.25) 时低置信度误检会解锁
    # 许可 + 抖动跟踪 → 小幅度视频实测多计到 15 件。计数语义不该受滑条摆布,
    # 在插件内先按本值过滤 (0=不过滤, 完全跟随主程序滑条)。
    "min_confidence": 0.7,
}

# 模块级运行时（插件加载时由 register_plugin 调 set_host 注入）
_HOST = None
_LOCK = threading.Lock()

# 棉签状态（内存单组；多工位扩展见二期）
# total_products / ng_count / over_limit: 监控看板统计（v1.0.2 语义，擦满 K 后继续擦记 NG）
# absent_remaining: 操作员离开倒计时剩余秒 (-1=未启用/未知)
_state = {
    "swab_used": 0,
    "total_products": 0,
    "ng_count": 0,
    "over_limit": False,
    "last_alarm_ts": 0.0,
    "absent_remaining": -1,
    # 当前在用棉签累加器: None=未开始; 否则 {seq,start_ts,products,ng,over_limit_hit,channel}
    # 清零(换棉签/手动/整批)时定稿落库一条, 见棉签记录持久化小节。
    "cur_swab": None,
}
_ALARM_REPEAT_SEC = 2.0
_config_cache = None

# per-channel 逐帧判定器实例（lazy 建）
_counters = {}            # {channel_id: ProductCounter}
_swab_windows = {}        # {channel_id: SwabChangeWindow}
_fake_wipe_detectors = {} # {channel_id: StillFakeActionDetector} 视角1 假擦拭
_absent_countdowns = {}   # {channel_id: AbsentCountdown} 视角2 操作员离开
# v1.4.0 双类别计数许可 (detect9(1) _both_seen): {channel_id: bool}
# 许可标签出现 → True; 计数命中 / 锚跟踪销毁 / 重置配置 → False
_count_permits = {}


def set_host(host):
    global _HOST
    _HOST = host
    # 启动即建表 + 恢复棉签序号 (失败不影响主流程, 后续惰性重试)
    try:
        _ensure_records_table()
    except Exception as e:
        log.warning("[%s] set_host 阶段建表失败(隔离, 后续惰性重试): %s", CUSTOMER_CODE, e)


def _as_bool(val, default=False):
    """把库里的开关值规范成 bool（兼容历史脏数据: 1/'true'/'1' 等）。"""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)


def _load_config():
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    cfg = dict(DEFAULT_CONFIG)
    if _HOST is not None:
        try:
            raw = _HOST.read_system_config(CONFIG_KEY)
            if raw:
                cfg.update(json.loads(raw) if isinstance(raw, str) else raw)
        except Exception as e:
            log.warning("[%s] 读配置失败, 用默认: %s", CUSTOMER_CODE, e)
    cfg["operator_absent_enabled"] = _as_bool(
        cfg.get("operator_absent_enabled"), DEFAULT_CONFIG["operator_absent_enabled"]
    )
    _config_cache = cfg
    return cfg


def reload_config():
    global _config_cache
    _config_cache = None
    # 配置变了 → 判定器参数可能变, 清掉实例下次按新参数重建
    _counters.clear()
    _swab_windows.clear()
    _fake_wipe_detectors.clear()
    _absent_countdowns.clear()
    _count_permits.clear()
    return _load_config()


def get_state():
    with _LOCK:
        cfg = _load_config()
        used = _state["swab_used"]
        k = int(cfg["max_uses_per_swab"])
        over = _state["over_limit"]
        return {
            "swab_used": used,
            "max_uses_per_swab": k,
            "remaining": max(0, k - used),
            "over_limit": over,
            "total_products": _state["total_products"],
            "ng_count": _state["ng_count"],
            "locked": over,
            "absent_remaining": _state.get("absent_remaining", -1),
            # 供看板显示离岗状态: 启用态 + (remaining==0 → 已离岗告警中)
            "operator_absent_enabled": _as_bool(cfg.get("operator_absent_enabled"), False),
            "operator_absent_timeout_sec": int(cfg.get("operator_absent_timeout_sec", 600) or 600),
        }


def reset_swab():
    """手动更换棉签：本根棉签计数清零、解除超限报警（总产量/不良不清）。"""
    with _LOCK:
        _state["swab_used"] = 0
        _state["over_limit"] = False
        _state["last_alarm_ts"] = 0.0
    log.info("[%s] 手动更换棉签 → 本根计数清零、解除超限报警", CUSTOMER_CODE)
    _finalize_current_swab("manual")
    return get_state()


def reset_counts():
    """整批重置：总产量 / 不良 / 棉签全部清零（新班次 / 看板重置按钮）。"""
    with _LOCK:
        _state["swab_used"] = 0
        _state["total_products"] = 0
        _state["ng_count"] = 0
        _state["over_limit"] = False
        _state["last_alarm_ts"] = 0.0
    # detect9(1) reset_counts 同步清跟踪残留: 计数器 + 双类别许可一并归零
    _counters.clear()
    _count_permits.clear()
    log.info("[%s] 整批重置 → 总产量/不良/棉签全部清零", CUSTOMER_CODE)
    _finalize_current_swab("batch_reset")
    return get_state()


# ==================== 棉签记录持久化（插件自有表，主程序 schema 零污染）====================
# 一根棉签 = 从擦第 1 件起，到本根清零(检出换棉签 / 手动重置 / 整批重置)止。
# 清零那一刻把这根定稿落库一条；列表/图表/导出都读这张表。表名遵循插件命名规范 p_<code>_<table>。
_RECORDS_TABLE = "p_sensor_clean_swab_records"
_records_ready = False
_swab_seq = 0


def _ensure_records_table():
    """惰性建表 + 从库里恢复棉签序号（幂等）。返回是否就绪。"""
    global _records_ready, _swab_seq
    if _records_ready or _HOST is None:
        return _records_ready
    try:
        from sqlalchemy import text
        db = _HOST.get_db_session()
        try:
            db.execute(text(
                f"CREATE TABLE IF NOT EXISTS {_RECORDS_TABLE} ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "swab_seq INTEGER, channel INTEGER, "
                "start_ts REAL, end_ts REAL, duration_sec REAL, "
                "products INTEGER, ng INTEGER, over_limit INTEGER, "
                "end_reason TEXT, created_at REAL)"
            ))
            db.commit()
            row = db.execute(text(f"SELECT MAX(swab_seq) FROM {_RECORDS_TABLE}")).first()
            _swab_seq = int(row[0]) if row and row[0] is not None else 0
            _records_ready = True
        finally:
            db.close()
    except Exception as e:
        log.warning("[%s] 棉签记录表初始化失败(隔离, 不落库): %s", CUSTOMER_CODE, e)
    return _records_ready


def _swab_touch(channel_id, now_wall, is_ng, over):
    """计件命中时累加当前棉签（调用方须已持 _LOCK）。"""
    global _swab_seq
    cur = _state.get("cur_swab")
    if cur is None:
        _swab_seq += 1
        cur = {"seq": _swab_seq, "start_ts": now_wall, "products": 0,
               "ng": 0, "over_limit_hit": False, "channel": channel_id}
        _state["cur_swab"] = cur
    cur["products"] += 1
    if is_ng:
        cur["ng"] += 1
    if over:
        cur["over_limit_hit"] = True


def _finalize_current_swab(end_reason):
    """清零点定稿：把当前棉签落库一条并清空累加器（有产品才落，错误隔离）。"""
    now_wall = time.time()
    with _LOCK:
        cur = _state.get("cur_swab")
        _state["cur_swab"] = None
    if not cur or cur.get("products", 0) <= 0:
        return
    if not _ensure_records_table():
        return
    try:
        from sqlalchemy import text
        db = _HOST.get_db_session()
        try:
            db.execute(text(
                f"INSERT INTO {_RECORDS_TABLE} "
                "(swab_seq, channel, start_ts, end_ts, duration_sec, products, ng, over_limit, end_reason, created_at) "
                "VALUES (:seq,:ch,:st,:et,:dur,:p,:ng,:ov,:rs,:ca)"),
                {"seq": cur["seq"], "ch": cur["channel"], "st": cur["start_ts"],
                 "et": now_wall, "dur": max(0.0, now_wall - cur["start_ts"]),
                 "p": cur["products"], "ng": cur["ng"],
                 "ov": 1 if cur["over_limit_hit"] else 0, "rs": end_reason, "ca": now_wall})
            db.commit()
        finally:
            db.close()
        log.info("[%s] 棉签#%d 定稿落库: 产品=%d 不良=%d 超限=%s 原因=%s",
                 CUSTOMER_CODE, cur["seq"], cur["products"], cur["ng"],
                 cur["over_limit_hit"], end_reason)
    except Exception as e:
        log.warning("[%s] 棉签记录落库失败(隔离): %s", CUSTOMER_CODE, e)


def get_swab_records(limit=300, since=None, until=None, channel=None):
    """查询棉签记录 + 汇总，供数据页列表/图表/导出共用。"""
    empty = {"records": [], "summary": {
        "swab_count": 0, "total_products": 0, "total_ng": 0,
        "over_limit_count": 0, "avg_products": 0, "avg_duration_sec": 0}}
    if not _ensure_records_table():
        return empty
    try:
        from sqlalchemy import text
        db = _HOST.get_db_session()
        try:
            where, params = [], {}
            if since is not None:
                where.append("end_ts >= :since"); params["since"] = float(since)
            if until is not None:
                where.append("end_ts <= :until"); params["until"] = float(until)
            if channel is not None:
                where.append("channel = :ch"); params["ch"] = int(channel)
            wsql = (" WHERE " + " AND ".join(where)) if where else ""
            cols = ["id", "swab_seq", "channel", "start_ts", "end_ts",
                    "duration_sec", "products", "ng", "over_limit", "end_reason"]
            qp = dict(params); qp["lim"] = int(limit)
            rows = db.execute(text(
                f"SELECT {','.join(cols)} FROM {_RECORDS_TABLE}{wsql} "
                f"ORDER BY id DESC LIMIT :lim"), qp).fetchall()
            records = [dict(zip(cols, r)) for r in rows]
            agg = db.execute(text(
                "SELECT COUNT(*),COALESCE(SUM(products),0),COALESCE(SUM(ng),0),"
                "COALESCE(SUM(over_limit),0),COALESCE(AVG(products),0),"
                f"COALESCE(AVG(duration_sec),0) FROM {_RECORDS_TABLE}{wsql}"), params).first()
            summary = {
                "swab_count": int(agg[0] or 0),
                "total_products": int(agg[1] or 0),
                "total_ng": int(agg[2] or 0),
                "over_limit_count": int(agg[3] or 0),
                "avg_products": round(float(agg[4] or 0), 2),
                "avg_duration_sec": round(float(agg[5] or 0), 1),
            }
            return {"records": records, "summary": summary}
        finally:
            db.close()
    except Exception as e:
        log.warning("[%s] 查询棉签记录失败(隔离): %s", CUSTOMER_CODE, e)
        out = dict(empty); out["error"] = str(e)
        return out


def apply_preset_config():
    """一键把 preset 默认计数/耗材参数写入系统配置并热加载（可后续再改）。

    模型 + 项目配置不在这里强推到通道（激活模型属主程序检测控制范畴），
    由前端拿 get_templates() 的项目/模型字段去走主程序的项目应用 + 检测启动。
    """
    from .preset import SWAB_CONFIG, get_templates

    written = False
    if _HOST is not None:
        try:
            _HOST.write_system_config(
                CONFIG_KEY,
                json.dumps(SWAB_CONFIG, ensure_ascii=False),
                description="传感器清洁插件 计数/耗材参数（对齐 demo 默认，可改）",
            )
            written = True
        except Exception as e:
            log.warning("[%s] 写默认配置失败(隔离): %s", CUSTOMER_CODE, e)
    cfg = reload_config()
    templates = get_templates()
    return {
        "applied": written,
        "config": cfg,
        "view1_project": templates["view1_project"],
        "view2_project": templates["view2_project"],
        "state": get_state(),
    }


def get_config():
    """返回当前完整插件配置（前端配置面板预填用）。"""
    return dict(_load_config())


def save_config(patch):
    """合并写入插件配置（system_config）+ 热加载。"""
    cur = dict(_load_config())
    if isinstance(patch, dict):
        cur.update(patch)
    saved = False
    if _HOST is not None:
        try:
            _HOST.write_system_config(
                CONFIG_KEY,
                json.dumps(cur, ensure_ascii=False),
                description="传感器清洁插件 配置（三判定事件映射 + 阈值 + 计数参数）",
            )
            saved = True
        except Exception as e:
            log.warning("[%s] 写配置失败(隔离): %s", CUSTOMER_CODE, e)
    cfg = reload_config()
    return {"saved": saved, "config": cfg}


def _project_config_dict(proj, tpl):
    """把项目 ORM + 模板拼成 VSM set_project_config 需要的 dict。"""
    return {
        "id": proj.id, "name": proj.name, "task_type": "detection",
        "logic_mode": "detection", "steps_config": tpl["steps_config"],
        "pipeline_config": tpl.get("pipeline_config", {}) or {},
        "events_config": {}, "counters_config": {}, "data_config": {},
    }


def import_demo_projects():
    """一键导入传感器清洁双工位项目（幂等可重复点）。"""
    import os
    from .preset import VIEW1_PROJECT, VIEW2_PROJECT, SWAB_CONFIG

    if _HOST is None:
        return {"ok": False, "msg": "host 不可用 (插件未正确加载)"}

    result = {"ok": False, "projects": [], "channels": []}
    db = _HOST.get_db_session()
    try:
        from backend.models.models import Project, Model
        from backend.api.channel_manager import channel_manager

        try:
            channel_manager.set_channel_count(2)
        except Exception as e:
            log.warning("[%s] set_channel_count(2) 失败(继续): %s", CUSTOMER_CODE, e)

        for ch_id, tpl in [(0, VIEW1_PROJECT), (1, VIEW2_PROJECT)]:
            entry = {"ch": ch_id, "name": tpl["name"]}
            try:
                mp = tpl["model_path"]
                if not os.path.exists(mp):
                    entry.update(ok=False, msg=f"模型文件不存在: {mp}")
                    result["channels"].append(entry)
                    continue
                model = db.query(Model).filter(Model.file_path == mp).first()
                if model is None:
                    model = Model(
                        name=os.path.splitext(os.path.basename(mp))[0],
                        file_path=mp,
                        file_name=os.path.basename(mp),
                        file_size=os.path.getsize(mp),
                        framework="PyTorch",
                        labels=[s["label"] for s in tpl["steps_config"]],
                        status="idle",
                    )
                    db.add(model)
                    db.flush()
                proj = db.query(Project).filter(Project.name == tpl["name"]).first()
                if proj is None:
                    proj = Project(name=tpl["name"])
                    db.add(proj)
                proj.task_type = "detection"
                proj.logic_mode = "detection"
                proj.pipeline_config = tpl.get("pipeline_config", {}) or {}
                proj.steps_config = tpl["steps_config"]
                proj.default_model_id = model.id
                db.flush()
                loaded = channel_manager.load_model_for_channel(ch_id, mp, "auto")
                try:
                    channel_manager.save_channel_source(
                        ch_id, {"project_id": proj.id}, merge=True)
                except Exception as e:
                    log.warning("[%s] ch%d 绑 project_id 失败(忽略): %s",
                                CUSTOMER_CODE, ch_id, e)
                mgr = channel_manager.channels.get(ch_id)
                if mgr is not None:
                    mgr.set_project_config(_project_config_dict(proj, tpl))
                entry.update(ok=True, project_id=proj.id, model_loaded=bool(loaded))
            except Exception as e:
                entry.update(ok=False, msg=str(e))
                log.warning("[%s] ch%d 导入失败(隔离): %s", CUSTOMER_CODE, ch_id, e)
            result["channels"].append(entry)
            if entry.get("ok"):
                result["projects"].append({"name": entry["name"],
                                           "id": entry.get("project_id")})

        db.commit()
        result["ok"] = any(c.get("ok") for c in result["channels"])
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        result["msg"] = str(e)
        log.warning("[%s] import_demo_projects 失败(已回滚): %s", CUSTOMER_CODE, e)
    finally:
        db.close()

    try:
        if _HOST is not None:
            # 合并写: 以 SWAB_CONFIG 为默认底, 叠加用户已保存的覆盖值再写回。
            # 避免「一键导入」把现场调好的离岗超时/阈值等清回默认 ——
            # 这是客户反馈「保存退出后又恢复」的根因之一 (重复导入会 clobber 配置)。
            merged = dict(SWAB_CONFIG)
            try:
                raw = _HOST.read_system_config(CONFIG_KEY)
                if raw:
                    saved = json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(saved, dict):
                        merged.update(saved)
            except Exception:
                pass
            _HOST.write_system_config(
                CONFIG_KEY, json.dumps(merged, ensure_ascii=False),
                description="传感器清洁插件 计数/耗材参数（一键导入随项目下发，保留已存覆盖）")
            reload_config()
    except Exception as e:
        log.warning("[%s] 写计数配置失败(忽略): %s", CUSTOMER_CODE, e)

    result["state"] = get_state()
    return result


def _get_counter(cfg, channel_id):
    c = _counters.get(channel_id)
    if c is None:
        c = ProductCounter(
            move_threshold=cfg["move_threshold"],
            lock_spatial=cfg["lock_spatial"],
            lock_time=cfg["lock_time"],
            move_confirm_frames=int(cfg.get("move_confirm_frames", 3)),
            lost_frame_thresh=int(cfg.get("lost_frame_thresh", 5)),
            force_lock_frames=int(cfg.get("force_lock_frames", 40)),
            dist_y_weight=float(cfg.get("dist_y_weight", 1.0) or 1.0),
            lost_gone_sec=float(cfg.get("lost_gone_sec", 0.0) or 0.0),
            force_lock_sec=float(cfg.get("force_lock_sec", 0.0) or 0.0),
        )
        _counters[channel_id] = c
    return c


def _get_swab_window(cfg, channel_id):
    w = _swab_windows.get(channel_id)
    if w is None:
        w = SwabChangeWindow(
            lock_time=float(cfg.get("swab_lock_time", 2.0)),
            min_sustain_sec=float(cfg.get("swab_min_sustain_sec", 0.0)),
            gap_sec=float(cfg.get("swab_gap_sec", 0.2)),
        )
        _swab_windows[channel_id] = w
    return w


def _get_fake_wipe_detector(cfg, channel_id):
    d = _fake_wipe_detectors.get(channel_id)
    if d is None:
        d = StillFakeActionDetector(
            still_time=cfg.get("fake_wipe_still_time", 2.0),
            still_disp=cfg.get("fake_wipe_still_disp", 0.0058),
            lost_frame_thresh=int(cfg.get("lost_frame_thresh", 5)),
        )
        _fake_wipe_detectors[channel_id] = d
    return d


def _get_absent_countdown(cfg, channel_id):
    c = _absent_countdowns.get(channel_id)
    if c is None:
        c = AbsentCountdown(timeout_sec=cfg.get("operator_absent_timeout_sec", 600))
        _absent_countdowns[channel_id] = c
    return c


# 主程序 _trigger_event 周期结算常见 reason 片段 (非插件 trigger_event 路径)
_MAIN_SETTLE_REASON_MARKERS = (
    "检测完成",
    "顺序正确完成",
    "缺少步骤",
    "重复步骤",
    "顺序错误",
    "周期不完整",
)


def _is_main_cycle_settle_reason(reason: str) -> bool:
    """是否为主程序检测/顺序模式周期结算触发的 reason (非插件三判定/计件)."""
    if not reason:
        return False
    return any(m in reason for m in _MAIN_SETTLE_REASON_MARKERS)


def on_event_fire(ctx):
    """抑制主程序并行周期结算的塔灯 — 计件与三判定仍走插件 trigger_event."""
    try:
        cfg = _load_config()
        if not _as_bool(cfg.get("suppress_main_settle_alarm"), True):
            return None
        channel_id = ctx.get("channel_id")
        count_chs = set(cfg.get("count_channels") or [0])
        swap_ch = cfg.get("swap_channel")
        if channel_id not in count_chs and channel_id != swap_ch:
            return None
        reason = ctx.get("reason") or ""
        if not _is_main_cycle_settle_reason(reason):
            return None
        log.debug(
            "[%s] 抑制主程序周期结算塔灯 ch=%s event=%s reason=%r",
            CUSTOMER_CODE, channel_id, ctx.get("event_id"), reason,
        )
        return {"suppress_alarm": True}
    except Exception as e:
        log.warning("[%s] on_event_fire 异常(隔离): %s", CUSTOMER_CODE, e)
        return None


def _fire_event(channel_id, event_id, reason):
    """三判定命中 → 借主程序事件响应面 (报警+计数器+Toast, 不结算周期)。

    event_id 为 0/空 = 该判定关闭, 直接跳过 (错误隔离, 不影响检测)。
    """
    if _HOST is None or not event_id:
        return
    try:
        ok = _HOST.trigger_event(
            channel_id=channel_id, event_id=int(event_id), reason=reason)
        if not ok:
            log.warning(
                "[%s] trigger_event 未联动 (通道未注册或事件 id=%s 不在该项目): %s",
                CUSTOMER_CODE, event_id, reason)
    except Exception as e:
        log.warning("[%s] trigger_event 失败(隔离): %s", CUSTOMER_CODE, e)


def _swab_over_limit_alarm(cfg, channel_id, now, force=False):
    """棉签擦满/超限期间触发硬件报警（红灯+蜂鸣），节流，不计数、不结算周期。

    报警事件优先取「棉签超限·连接事件」对应的 event{id}（与该事件在报警页配的
    灯/蜂鸣联动）；兼容老配置直接写 alarm_event 字符串。force=True 时忽略节流
    （棉签刚擦满那一下立即响一次），否则按 _ALARM_REPEAT_SEC 节流持续响。
    """
    if _HOST is None:
        return
    legacy = cfg.get("alarm_event")
    ev_id = cfg.get("swab_over_limit_event_id")
    event_type = legacy or (f"event{int(ev_id)}" if ev_id else None)
    if not event_type:
        return
    with _LOCK:
        if not force and now - _state["last_alarm_ts"] < _ALARM_REPEAT_SEC:
            return
        _state["last_alarm_ts"] = now
    try:
        _HOST.trigger_alarm(
            channel_id=channel_id,
            event_type=event_type,
            reason=f"棉签已用满 {cfg['max_uses_per_swab']} 个产品, 请立即更换棉签",
        )
    except Exception as e:
        log.warning("[%s] trigger_alarm 失败(隔离): %s", CUSTOMER_CODE, e)


def on_detection_frame(ctx):
    """detection_frame 钩子处理：逐帧计数 / 锁定 / 解锁（observe-only）。"""
    try:
        cfg = _load_config()
        channel_id = ctx.get("channel_id")
        now = ctx.get("timestamp") or 0.0
        detections = ctx.get("detections") or []
        # v1.4.1 插件内置信度地板 (detect9(1) CONF_THRES): 主程序滑条低于本值时
        # 把低置信度误检挡在计数/许可/换棉签判定之外
        _floor = float(cfg.get("min_confidence", 0) or 0)
        if _floor > 0:
            detections = [d for d in detections
                          if (d.get("confidence") or 0) >= _floor]

        # 视角2：操作员离开超时 + 换棉签稳定窗口 → 解锁清零
        if channel_id == cfg["swap_channel"]:
            # 判定3: 操作员离开超时 (帧级倒计时, 无后台线程)。无任何检测目标视为离岗,
            # 持续超 timeout → 触发事件; 再检测到人自动解除。
            if cfg.get("operator_absent_enabled"):
                present = len(detections) > 0
                hit, remaining = _get_absent_countdown(cfg, channel_id).feed(present, now)
                with _LOCK:
                    _state["absent_remaining"] = remaining
                if hit:
                    log.info("[%s] 视角2 操作员离开超 %ss → 触发事件 %s",
                             CUSTOMER_CODE, cfg.get("operator_absent_timeout_sec"),
                             cfg.get("operator_absent_event_id"))
                    _fire_event(
                        channel_id, cfg.get("operator_absent_event_id"),
                        f"操作员离开岗位超过 {cfg.get('operator_absent_timeout_sec')} 秒")

            swap_label = cfg["swap_label"]
            swap_roi = (cfg.get("label_rois") or {}).get("swap")
            has_change = any(d.get("label") == swap_label and det_in_roi(d, swap_roi)
                             for d in detections)
            if _get_swab_window(cfg, channel_id).feed(has_change, now):
                with _LOCK:
                    was_over = _state["over_limit"]
                    _state["swab_used"] = 0
                    _state["over_limit"] = False
                    _state["last_alarm_ts"] = 0.0
                log.info("[%s] 视角2 检出更换棉签 → %s", CUSTOMER_CODE,
                         "解除超限报警并清零本根棉签" if was_over else "本根棉签计数清零")
                # 检出换棉签 = 本根结束, 定稿落库 (锁外调用, _finalize 自持 _LOCK)
                _finalize_current_swab("change")
            return None

        # 视角1：逐帧跑锚动作生命周期 → 移动计 1 件（擦满 K 后继续擦记 NG，不暂停）
        if channel_id in cfg["count_channels"]:
            anchor_label = cfg["count_anchor_label"]
            rois = cfg.get("label_rois") or {}
            # 判定2: 假擦拭 — 追踪"擦拭产品"框, 停留超时但几乎未移动 → 触发事件。
            if cfg.get("fake_wipe_event_id"):
                wipe_label = cfg.get("fake_wipe_label", "擦拭产品")
                wipe_roi = rois.get("fake_wipe")
                wipe_det = None
                for d in detections:
                    if d.get("label") == wipe_label and det_in_roi(d, wipe_roi):
                        if wipe_det is None or d.get("confidence", 0) > wipe_det.get("confidence", 0):
                            wipe_det = d
                if _get_fake_wipe_detector(cfg, channel_id).update(wipe_det, now):
                    log.info("[%s] 视角1 假擦拭命中 (擦拭框停留超时位移过小) → 触发事件 %s",
                             CUSTOMER_CODE, cfg.get("fake_wipe_event_id"))
                    _fire_event(channel_id, cfg.get("fake_wipe_event_id"),
                                "假擦拭: 擦拭产品框停留超时但几乎未移动")
            # 取本帧锚动作框 (ROI 内置信度最高的一个; demo 单锚)
            anchor_roi = rois.get("count_anchor")
            anchor = None
            for d in detections:
                if d.get("label") == anchor_label and det_in_roi(d, anchor_roi):
                    if anchor is None or d.get("confidence", 0) > anchor.get("confidence", 0):
                        anchor = d
            # v1.4.0 双类别计数许可 (detect9(1) _both_seen 语义):
            # 许可标签(如"脏污产品")出现过即解锁 —— 不必与锚框同帧, 两类交替出现
            # 也能计数; 锚框位移达标且许可已解锁才计件; 计数/跟踪销毁时重置许可。
            require_label = cfg.get("count_require_label") or ""
            if require_label:
                require_roi = rois.get("count_require")
                if any(d.get("label") == require_label and det_in_roi(d, require_roi)
                       for d in detections):
                    _count_permits[channel_id] = True
                allow_count = _count_permits.get(channel_id, False)
            else:
                allow_count = True
            if _RECORD_PATH:
                try:
                    rec = {"seq": ctx.get("frame_seq"), "ts": now,
                           "anchor": ({"x": anchor["x"], "y": anchor["y"], "w": anchor["w"],
                                       "h": anchor["h"], "confidence": anchor.get("confidence")}
                                      if anchor else None)}
                    with open(_RECORD_PATH, "a") as _f:
                        _f.write(json.dumps(rec) + "\n")
                except Exception:
                    pass
            counter = _get_counter(cfg, channel_id)
            counted = counter.update(anchor, now, paused=False, allow_count=allow_count)
            if require_label and (counted or counter.tracker_gone):
                # detect9(1): 计到一件 / 锚跟踪销毁 → 许可清零, 下一件需再见许可标签
                _count_permits[channel_id] = False
            if counted:
                k = int(cfg["max_uses_per_swab"])
                with _LOCK:
                    prev_used = _state["swab_used"]
                    _state["total_products"] += 1
                    _state["swab_used"] += 1
                    used = _state["swab_used"]
                    total = _state["total_products"]
                    # used==k: 棉签擦满 (这件仍合格); used>k: 满了还擦 = 不良
                    just_hit_limit = prev_used < k and used >= k
                    is_ng = used > k
                    if used >= k:
                        _state["over_limit"] = True   # 擦满即亮红 banner + 持续报警
                    if is_ng:
                        _state["ng_count"] += 1
                    ng = _state["ng_count"]
                    # 累加当前棉签 (落库在清零点定稿; 此处仅内存累加, 已持 _LOCK)
                    _swab_touch(channel_id, time.time(), is_ng, used >= k)
                log.info("[%s] 计数 总产量=%d 本根棉签=%d/%d NG=%d",
                         CUSTOMER_CODE, total, used, k, ng)
                if is_ng:
                    # 超限后继续擦 = 不良: 每件触发 NG 事件 → 红灯+蜂鸣+不良计数
                    _fire_event(channel_id, cfg.get("swab_over_limit_event_id"),
                                f"棉签超限仍擦拭, 本根第 {used} 件(上限 {k}) → 不良")
                else:
                    # 合格件 → 正常计件事件 (默认 OK 绿灯; 设 0 则只计看板不联动)
                    _fire_event(channel_id, cfg.get("normal_count_event_id"),
                                "正常计件: 产品计数 +1")
                if just_hit_limit:
                    # 棉签刚擦满 K: 立即点灯/蜂鸣提示换棉签 (硬件报警, 这件仍按合格计)
                    _swab_over_limit_alarm(cfg, channel_id, now, force=True)
            # 超限持续期间: 节流硬件报警 (红灯/蜂鸣), 不重复计数 (产品停了也持续响)
            with _LOCK:
                over = _state["over_limit"]
            if over:
                _swab_over_limit_alarm(cfg, channel_id, now)
        return None
    except Exception as e:
        log.warning("[%s] on_detection_frame 异常(隔离): %s", CUSTOMER_CODE, e)
        return None
