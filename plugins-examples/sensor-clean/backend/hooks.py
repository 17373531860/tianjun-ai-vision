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

from .counter import ProductCounter, SwabChangeWindow

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
    "count_anchor_label": "查看产品有无脏污",   # 视角1 锚动作标签 (detect6 cls0)
    "swap_channel": 1,                        # 视角2 换棉签通道
    "swap_label": "更换棉签",                  # 视角2 换棉签动作标签
    "max_uses_per_swab": 11,                  # 一根棉签最多擦几个产品 (K)
    "alarm_event": "",                        # 锁定时触发的报警事件类型 (空=不触发)
    # detect6 逐帧计数参数 (移动即计数 + 帧硬锁; 阈值 = detect6 像素值 / 1728 宽)
    "move_threshold": 0.0116,                 # 移动判定 (detect6 20px / 1728)
    "lock_spatial": 0.0145,                   # 位置锁范围 (detect6 25px / 1728)
    "lock_time": 3.0,                         # 位置锁 / 计数冷却 (秒, detect6)
    "move_confirm_frames": 3,                 # 连续 N 帧位移超阈值才确认移动 (detect6)
    "lost_frame_thresh": 5,                   # 连续丢失 N 帧确认产品离开 (detect6)
    # 计数后强制锁定帧数 (detect6 原值 40)。源帧率 <= 推理速度(约56fps)时不丢帧、
    # 实时时钟=视频时间, 40 即对齐基准 44; 高帧率源(如60fps test.mp4)丢帧需调大。
    "force_lock_frames": 40,
}

# 模块级运行时（插件加载时由 register_plugin 调 set_host 注入）
_HOST = None
_LOCK = threading.Lock()

# 棉签状态（内存单组；多工位扩展见二期）
_state = {"swab_used": 0, "locked": False}
_config_cache = None

# per-channel 逐帧计数器实例（lazy 建）
_counters = {}        # {channel_id: ProductCounter}
_swab_windows = {}    # {channel_id: SwabChangeWindow}


def set_host(host):
    global _HOST
    _HOST = host


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
    _config_cache = cfg
    return cfg


def reload_config():
    global _config_cache
    _config_cache = None
    # 配置变了 → 计数器参数可能变, 清掉实例下次按新参数重建
    _counters.clear()
    _swab_windows.clear()
    return _load_config()


def get_state():
    with _LOCK:
        cfg = _load_config()
        used = _state["swab_used"]
        k = int(cfg["max_uses_per_swab"])
        return {
            "swab_used": used,
            "max_uses_per_swab": k,
            "remaining": max(0, k - used),
            "locked": _state["locked"],
        }


def reset_swab():
    """手动更换棉签（等同视角2 检出换棉签）。"""
    with _LOCK:
        _state["swab_used"] = 0
        _state["locked"] = False
    log.info("[%s] 手动重置棉签 → 解锁清零", CUSTOMER_CODE)
    return get_state()


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
        )
        _counters[channel_id] = c
    return c


def _get_swab_window(cfg, channel_id):
    w = _swab_windows.get(channel_id)
    if w is None:
        w = SwabChangeWindow(lock_time=cfg["lock_time"])
        _swab_windows[channel_id] = w
    return w


def _trigger_lock_alarm(cfg, channel_id):
    if _HOST is None or not cfg.get("alarm_event"):
        return
    try:
        _HOST.trigger_alarm(
            channel_id=channel_id,
            event_type=cfg["alarm_event"],
            reason=f"棉签已用满 {cfg['max_uses_per_swab']} 个产品, 请更换棉签",
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

        # 视角2：换棉签稳定窗口 → 解锁清零
        if channel_id == cfg["swap_channel"]:
            swap_label = cfg["swap_label"]
            has_change = any(d.get("label") == swap_label for d in detections)
            if _get_swab_window(cfg, channel_id).feed(has_change, now):
                with _LOCK:
                    was_locked = _state["locked"]
                    _state["swab_used"] = 0
                    _state["locked"] = False
                log.info("[%s] 视角2 检出更换棉签 → %s", CUSTOMER_CODE,
                         "解锁并清零" if was_locked else "计数清零")
            return None

        # 视角1：逐帧跑锚动作生命周期 → 离开计 1 件
        if channel_id in cfg["count_channels"]:
            anchor_label = cfg["count_anchor_label"]
            # 取本帧锚动作框 (取置信度最高的一个; demo 单锚)
            anchor = None
            for d in detections:
                if d.get("label") == anchor_label:
                    if anchor is None or d.get("confidence", 0) > anchor.get("confidence", 0):
                        anchor = d
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
            with _LOCK:
                paused = _state["locked"]
            counter = _get_counter(cfg, channel_id)
            counted = counter.update(anchor, now, paused=paused)
            if counted:
                just_locked = False
                with _LOCK:
                    if not _state["locked"]:
                        _state["swab_used"] += 1
                        used = _state["swab_used"]
                        k = int(cfg["max_uses_per_swab"])
                        if used >= k:
                            _state["locked"] = True
                            just_locked = True
                log.info("[%s] 产品计数 %d/%d", CUSTOMER_CODE, used, k)
                if just_locked:
                    log.info("[%s] 棉签擦满 → 锁定计数, 等待更换棉签", CUSTOMER_CODE)
                    _trigger_lock_alarm(cfg, channel_id)
        return None
    except Exception as e:
        log.warning("[%s] on_detection_frame 异常(隔离): %s", CUSTOMER_CODE, e)
        return None
