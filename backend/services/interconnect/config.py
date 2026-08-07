# -*- coding: utf-8 -*-
"""训练平台互连 — 配置存取 (SystemConfig KV: interconnect.config).

热路径契约: is_sampling_active() 被推理循环每帧调用, 必须 O(1)。
所以配置以模块级快照缓存, 只有首次读取 / 保存时才碰数据库。
"""
from __future__ import annotations

import copy
import json
import threading

KV_KEY = "interconnect.config"

DEFAULT_CONFIG: dict = {
    # 互连总开关: 关 = 入站 models/push 拒收 + 出站 worker 不发 (存量客户零差异)
    "enabled": False,
    # 对端 (YoloVision 训练平台) base URL, 例 http://192.168.1.10:8080
    "platform_url": "",
    # 共享令牌 (双向 X-Interconnect-Token), 现场部署时双方配同一随机串
    "token": "",
    # 设备名 (多台工控机连同一训练平台时的可读标识), 空 = 用主机名
    "device_name": "",
    "sampling": {
        "enabled": False,
        # 置信度带采样: 任一检测框 conf ∈ [conf_min, conf_max) → 疑难样本
        "low_conf_enabled": True,
        "conf_min": 0.2,
        "conf_max": 0.6,
        # 未检出帧采样 (负样本候选 / 漏检候选)
        "no_detection_enabled": False,
        # 未检出帧只在"周期进行中"(工件在检) 时采样。产线大部分时间没有工件,
        # 周期外的空帧是合法空景不是漏检; 关掉此守门才会把周期外空帧也采走
        # (仅适合"画面常年应有目标"的连续监控类场景)
        "no_detection_in_cycle_only": True,
        # 检出闪断采样: 某标签连续 ≥ dropout_min_frames 帧稳定出现后当前帧突然
        # 消失且周期仍进行中 → 目标物理上还在而模型没检出, 是漏检的强证据
        "dropout_enabled": True,
        "dropout_min_frames": 5,
        # NG 事件现场帧采样
        "ng_event_enabled": False,
        # 是否携带模型推理结果作预标注
        "include_annotations": True,
        # 限流: 同通道两次采样最小间隔 (秒) + 全局每小时上限
        "min_interval_s": 10.0,
        "max_per_hour": 60,
        "jpeg_quality": 85,
    },
    "queue": {
        "max_items": 500,
        "ttl_hours": 72,
    },
    # 模型分发拉取模式: 本机定期轮询训练平台"我的项目有没有新模型包"并下载入库。
    # 多台工控机 + 工厂 NAT 场景的正解 (平台无需反向直连每台机器);
    # push 模式 (models/push) 保留, 适合平台能直连设备的局域网单机场景。
    "model_pull": {
        "enabled": False,
        "interval_s": 300,
    },
}

_lock = threading.RLock()
_snapshot: dict | None = None
# 无锁读的热路径布尔 (CPython 赋值原子)
_sampling_active = False


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


def _load_from_db() -> dict:
    from backend.db.database import SessionLocal
    from backend.models.models import SystemConfig
    db = SessionLocal()
    try:
        row = db.query(SystemConfig).filter(SystemConfig.key == KV_KEY).first()
        if row is not None and row.value:
            try:
                data = json.loads(row.value)
                if isinstance(data, dict):
                    return data
            except (TypeError, json.JSONDecodeError):
                pass
        return {}
    finally:
        db.close()


def _set_snapshot(data: dict) -> None:
    global _snapshot, _sampling_active
    _snapshot = data
    _sampling_active = bool(data.get("enabled")) and bool(
        (data.get("sampling") or {}).get("enabled"))


def _ensure_loaded() -> None:
    if _snapshot is None:
        with _lock:
            if _snapshot is None:
                _set_snapshot(_deep_merge(DEFAULT_CONFIG, _load_from_db()))


def get_config() -> dict:
    """完整配置 (已并默认值), 返回副本。"""
    _ensure_loaded()
    with _lock:
        return copy.deepcopy(_snapshot)


def sampling_snapshot() -> dict:
    """采样子配置的缓存引用 (热路径只读, 调用方不得修改)。"""
    _ensure_loaded()
    return _snapshot.get("sampling") or {}


def is_sampling_active() -> bool:
    """推理循环每帧的 O(1) 早退判据。"""
    if _snapshot is None:
        _ensure_loaded()
    return _sampling_active


def save_config(new_cfg: dict) -> dict:
    """并入默认值后持久化到 SystemConfig, 并刷新快照 / 唤起上传 worker。"""
    merged = _deep_merge(DEFAULT_CONFIG, new_cfg or {})
    from backend.db.database import SessionLocal
    from backend.models.models import SystemConfig
    db = SessionLocal()
    try:
        row = db.query(SystemConfig).filter(SystemConfig.key == KV_KEY).first()
        encoded = json.dumps(merged, ensure_ascii=False)
        if row is None:
            row = SystemConfig(key=KV_KEY, value=encoded,
                               description="训练平台互连配置 (v3.47)")
            db.add(row)
        else:
            row.value = encoded
        db.commit()
    finally:
        db.close()
    with _lock:
        _set_snapshot(merged)
    # 启用时确保上传 worker 在跑 (幂等)
    if merged.get("enabled"):
        try:
            from backend.services.interconnect.uploader import ensure_worker
            ensure_worker()
        except Exception as e:  # noqa: BLE001 — worker 故障不阻塞配置保存
            print(f"[Interconnect] 上传 worker 启动失败 (已隔离): {e}")
        if (merged.get("model_pull") or {}).get("enabled"):
            try:
                from backend.services.interconnect.puller import ensure_puller
                ensure_puller()
            except Exception as e:  # noqa: BLE001
                print(f"[Interconnect] 模型拉取 worker 启动失败 (已隔离): {e}")
    return copy.deepcopy(merged)


def reload() -> None:
    """强制下次读取时从 DB 重载 (测试用)。"""
    global _snapshot, _sampling_active
    with _lock:
        _snapshot = None
        _sampling_active = False
