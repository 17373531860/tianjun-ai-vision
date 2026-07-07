"""原生称重投料检测引擎 (logic_mode='weighing')。

这是主程序**原生**检测模式之一 (与 sequential/detection/custom/tracking 并列), 但属于
**设备驱动**模式: 由称重器每帧读数推进状态机, 而非 YOLO 视频帧。对应《萍乡百斯特项目
终验收标准》第六章投料防错全套需求。

业务流程 (每件产品按型号分若干道料, 如钢帽水泥/钢脚水泥, 逐道投料称重):
    选人员/型号 → 扫码绑产品 →
      料别0: 放件 → 自动/手动去皮归零 → 投料 → 读数稳定 → 对比标准量 → 判定 → 记录
      料别1: 放件 → 去皮 → 投料 → 稳定 → 判定 → 记录
    → 全部料别完成 → 上传 → 自动置零复位

设计原则 (与项目既有"状态机纯逻辑可单测"范式一致):
- WeighingStation **不**直接调主程序副作用 (报警/去皮指令/上传); 只推进状态 + **返回事件列表**。
- 副作用由 WeighingEngine 的执行层翻译成真正调用 (alarm/_trigger_event/send_command/落库),
  这样状态机能脱离主程序纯跑单测 (喂合成重量序列 + 合成视觉标签即可验全流程)。

配置来自项目 pipeline_config.weighing (不新增 ORM 列, 符合"优先扩 JSON"原则)。
判定阈值/型号标准量/料别顺序/去皮方式/前置校验/报警事件映射全部可配。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)


# ==================== 配置默认值 ====================
# 单位统一 kg。默认偏保守, 现场按型号工艺改。
DEFAULT_WEIGHING_CONFIG = {
    # 料别顺序 (two_pour: 每件先后两道料; 单料客户只填一个)
    "materials": ["钢帽水泥", "钢脚水泥"],

    # 型号 → 各料别标准量 + 上下容差
    # 结构: {型号名: {料别名: {"standard":标准量, "low_tol":下容差, "high_tol":上容差}}}
    "models": {},

    # 去皮方式: auto_stable=放件后毛重超阈并稳定自动发T; manual=等界面按钮
    "tare_mode": "auto_stable",
    "tare_trigger_weight": 0.05,   # kg, 放件后毛重超此值视为"已放好, 可去皮"
    "tare_settle_samples": 3,      # 连续 N 个稳定样本才触发自动去皮

    # 读数稳定判定 (自实现, 不依赖秤的稳定输出设置)
    "stable_tol": 0.003,           # kg, 采样窗口内波动小于此值算稳定
    "stable_min_samples": 3,
    "measure_min_weight": 0.005,   # kg, 投料净重低于此值不算有效投料(防空读误判)

    # 6.5 前置校验
    "require_operator": True,      # 未选人员 → 拦截 + 报警
    "require_model": True,         # 未选型号 → 拦截 + 报警

    # 6.3 物料防错: off=不校验; sequence=仅按料别顺序(无视觉无法拦投错);
    #              visual=用视觉识别的料别标签与"应投料别"比对, 不符则拦截报警
    "material_check": "sequence",

    # 完成动作
    "auto_zero_after_done": True,  # 一件全部料别完成后自动置零, 准备下一件

    # 报警事件映射 → 主程序 events_config[*].id (复用主程序事件链: 计数器+三色灯+Toast+语音)
    "alarm_event_shortage": 2,     # 缺料 (< 标准量下限)  (验收 6.2 核心)
    "alarm_event_over": 2,         # 超量 (> 标准量上限)
    "alarm_event_wrong": 2,        # 投错品类 (视觉料别与应投不符)  (验收 6.3)
    "alarm_event_precheck": 2,     # 未选人员/型号  (验收 6.5)
}


def merge_config(user_cfg: Optional[dict]) -> dict:
    """把项目里配的 weighing 配置深合并到默认值之上, 缺字段不崩。"""
    cfg = dict(DEFAULT_WEIGHING_CONFIG)
    if isinstance(user_cfg, dict):
        for k, v in user_cfg.items():
            if v is not None:
                cfg[k] = v
    return cfg


# ==================== 判定纯函数 ====================
def get_model_spec(cfg: dict, model_name: str, material: str):
    """取某型号某料别的标准量配置, 没配返回 None。"""
    models = cfg.get("models") or {}
    m = models.get(model_name)
    if not isinstance(m, dict):
        return None
    spec = m.get(material)
    return spec if isinstance(spec, dict) else None


def judge_amount(net: float, spec: dict) -> str:
    """对比投料净重与标准量(上下限容差), 返回 'ok' | 'shortage' | 'over'。

    缺料: net < 标准量 - 下容差 (验收 6.2 核心)
    超量: net > 标准量 + 上容差
    """
    std = float(spec.get("standard", 0))
    lo = float(spec.get("low_tol", 0))
    hi = float(spec.get("high_tol", 0))
    if net < std - lo:
        return "shortage"
    if net > std + hi:
        return "over"
    return "ok"


# ==================== 工位投料状态机 (纯逻辑) ====================
class WeighingStation:
    """单个工位 (通道) 的逐件投料状态机。

    相位:
        idle         等扫码开始一件
        await_tare   等放件后去皮 (auto: 毛重超阈稳定自动发T; manual: 等按钮)
        filling      已去皮归零, 等投料并稳定
        done         一件全部料别完成 (等复位/下一件)
    """

    def __init__(self, channel_id: int, weight_device_id=None, name: str = ""):
        self.channel_id = channel_id
        self.weight_device_id = weight_device_id
        self.name = name or f"工位{channel_id}"

        # 作业上下文 (前置必须先选)
        self.operator = None
        self.model_name = None

        # 当前件
        self.product_sn = None
        self.materials = []
        self.material_idx = 0
        self.phase = "idle"
        self.results = []

        # 视觉识别到的当前料别标签 (6.3, 由 set_material_label 喂入)
        self.visual_label = None

        # 读数稳定缓冲: (ts, weight)
        self._buf = deque(maxlen=32)

    # ---------- 上下文 / 生命周期 ----------
    def set_context(self, operator=None, model_name=None):
        if operator is not None:
            self.operator = operator
        if model_name is not None:
            self.model_name = model_name

    def set_material_label(self, label):
        """喂入视觉识别到的料别标签 (6.3 物料防错; 现场由模型识别水泥盆型号得到)。"""
        self.visual_label = label

    def reset(self):
        """复位到等下一件 (清当前件痕迹, 保留人员/型号上下文)。"""
        self.product_sn = None
        self.materials = []
        self.material_idx = 0
        self.phase = "idle"
        self.results = []
        self.visual_label = None
        self._buf.clear()

    def start_product(self, sn, cfg):
        """扫码开始一件。返回事件列表 (含前置校验未过的拦截事件)。"""
        events = []
        # 6.5 前置校验: 必须先选人员/型号
        if cfg.get("require_operator", True) and not self.operator:
            events.append({"action": "alarm", "kind": "precheck",
                           "channel_id": self.channel_id,
                           "reason": "未选择操作人员, 拦截投料"})
            return events
        if cfg.get("require_model", True) and not self.model_name:
            events.append({"action": "alarm", "kind": "precheck",
                           "channel_id": self.channel_id,
                           "reason": "未选择产品型号, 拦截投料"})
            return events

        self.product_sn = sn
        self.materials = list(cfg.get("materials") or [])
        self.material_idx = 0
        self.results = []
        self._buf.clear()
        self.phase = "await_tare" if self.materials else "idle"
        events.append({"action": "product_start", "sn": sn, "channel_id": self.channel_id,
                       "model": self.model_name, "operator": self.operator})
        return events

    # ---------- 稳定判定 ----------
    def _push_weight(self, ts, weight):
        self._buf.append((ts, float(weight)))

    def _is_stable(self, cfg) -> bool:
        min_n = int(cfg.get("stable_min_samples", 3))
        tol = float(cfg.get("stable_tol", 0.003))
        if len(self._buf) < min_n:
            return False
        recent = [w for _, w in list(self._buf)[-min_n:]]
        return (max(recent) - min(recent)) <= tol

    def _current_material(self):
        if 0 <= self.material_idx < len(self.materials):
            return self.materials[self.material_idx]
        return None

    # ---------- 6.3 视觉料别防错 ----------
    def _check_wrong_material(self, cfg, expected):
        """visual 模式下校验视觉料别与应投料别是否一致。

        返回 None=通过/不校验; 否则返回拦截 reason。
        """
        if cfg.get("material_check") != "visual":
            return None
        if self.visual_label is None:
            return None  # 还没识别到, 不拦 (等识别)
        if expected is not None and str(self.visual_label) != str(expected):
            return f"投错品类: 视觉识别为「{self.visual_label}」, 应投「{expected}」"
        return None

    # ---------- 手动去皮 (manual / 界面按钮) ----------
    def manual_tare_done(self):
        """界面按了去皮且主程序已发 T 后调用: 进入投料相位。"""
        if self.phase == "await_tare":
            self.phase = "filling"
            self._buf.clear()
            return True
        return False

    # ---------- 核心: 喂一帧称重读数 ----------
    def on_weight(self, weight, ts, cfg):
        """喂一帧净重读数, 推进状态机, 返回事件列表。

        事件 action: send_tare / record / alarm / product_done / send_zero
        """
        events = []
        if self.phase in ("idle", "done"):
            return events

        self._push_weight(ts, weight)
        material = self._current_material()
        if material is None:
            return events

        spec = get_model_spec(cfg, self.model_name, material)

        if self.phase == "await_tare":
            if cfg.get("tare_mode", "auto_stable") != "auto_stable":
                return events  # 手动模式: 等按钮
            trigger = float(cfg.get("tare_trigger_weight", 0.05))
            need = int(cfg.get("tare_settle_samples", 3))
            # 放件后毛重超阈且稳定 → 自动去皮 (6.4)
            if weight >= trigger and len(self._buf) >= need and self._is_stable(cfg):
                events.append({"action": "send_tare", "device_id": self.weight_device_id,
                               "channel_id": self.channel_id, "material": material})
                self.phase = "filling"
                self._buf.clear()
            return events

        if self.phase == "filling":
            # 6.3 投料过程中持续校验料别 (视觉模式): 一旦识别到投错品类立即拦截报警
            wrong = self._check_wrong_material(cfg, material)
            if wrong:
                events.append({"action": "alarm", "kind": "wrong",
                               "channel_id": self.channel_id, "reason": wrong,
                               "material": material, "visual": self.visual_label})
                return events  # 拦截: 不记录、不推进, 等纠正

            min_w = float(cfg.get("measure_min_weight", 0.005))
            if weight >= min_w and self._is_stable(cfg):
                net = round(float(weight), 4)
                if spec is None:
                    verdict = "no_spec"
                    standard = None
                else:
                    verdict = judge_amount(net, spec)
                    standard = float(spec.get("standard", 0))
                result = {
                    "material": material,
                    "standard": standard,
                    "initial": 0.0,      # 去皮归零后投料, 初值=0 (6.1)
                    "net": net,          # 终值=净投料量 (6.1)
                    "verdict": verdict,
                    "ts": ts,
                    "sn": self.product_sn,
                    "model": self.model_name,
                    "operator": self.operator,
                    "channel_id": self.channel_id,
                }
                self.results.append(result)
                events.append({"action": "record", "result": result})
                if verdict in ("shortage", "over"):
                    events.append({"action": "alarm", "kind": verdict,
                                   "channel_id": self.channel_id,
                                   "reason": _verdict_reason(verdict, material, net, standard),
                                   "result": result})
                # 推进到下一道料 / 完成
                self.material_idx += 1
                self.visual_label = None
                self._buf.clear()
                if self.material_idx < len(self.materials):
                    self.phase = "await_tare"
                else:
                    self.phase = "done"
                    events.append({"action": "product_done", "sn": self.product_sn,
                                   "channel_id": self.channel_id,
                                   "model": self.model_name, "operator": self.operator,
                                   "results": list(self.results)})
                    if cfg.get("auto_zero_after_done", True):
                        events.append({"action": "send_zero",
                                       "device_id": self.weight_device_id,
                                       "channel_id": self.channel_id})
            return events

        return events

    # ---------- 状态快照 (前端看板轮询) ----------
    def snapshot(self):
        return {
            "channel_id": self.channel_id,
            "name": self.name,
            "operator": self.operator,
            "model_name": self.model_name,
            "product_sn": self.product_sn,
            "phase": self.phase,
            "material_idx": self.material_idx,
            "current_material": self._current_material(),
            "materials": list(self.materials),
            "visual_label": self.visual_label,
            "results": list(self.results),
            "live_weight": (self._buf[-1][1] if self._buf else None),
        }


def _verdict_reason(verdict, material, net, standard):
    if standard is None:
        return f"{material} 型号未配标准量"
    if verdict == "shortage":
        return f"{material} 缺料: 实投 {net}kg < 标准 {standard}kg"
    if verdict == "over":
        return f"{material} 超量: 实投 {net}kg > 标准 {standard}kg"
    return f"{material} {verdict}"


# ==================== 称重引擎 (单例: 工位注册表 + 配置 + 副作用执行) ====================
class WeighingEngine:
    """主程序级称重引擎。

    - 按通道持有 WeighingStation + 该通道的 weighing 配置 (来自激活项目)。
    - 只有 logic_mode='weighing' 的通道才被 set_channel_config 登记; 其它通道喂数据直接忽略。
    - feed_weight: 外设管线每帧称重读数喂入 → 推进状态机 → 执行副作用。
    - 副作用 (报警/去皮置零/逐件记录) 懒加载主程序模块, 全部 try/except 隔离,
      引擎出问题绝不能拖垮外设数据主链路。
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._stations = {}      # {channel_id: WeighingStation}
        self._configs = {}       # {channel_id: merged cfg}  (仅 weighing 模式通道)
        self._records = {}       # {channel_id: deque(逐件逐料记录)}  6.1 台账(内存, 后续接DB/MES)

    # ---------- 通道登记 (set_project_config 时调) ----------
    def set_channel_config(self, channel_id: int, weighing_cfg: Optional[dict]):
        """激活 weighing 模式项目时登记该通道配置; 传 None 注销 (切回别的模式)。"""
        with self._lock:
            if weighing_cfg is None:
                self._configs.pop(channel_id, None)
                self._stations.pop(channel_id, None)
                logger.info("[Weighing] ch%s 注销称重模式", channel_id)
                return
            cfg = merge_config(weighing_cfg)
            self._configs[channel_id] = cfg
            # 配置变了(料别/工位)重建状态机, 但保留人员/型号上下文
            old = self._stations.get(channel_id)
            st = WeighingStation(channel_id,
                                 weight_device_id=cfg.get("weight_device_id"),
                                 name=cfg.get("station_name", ""))
            if old is not None:
                st.operator = old.operator
                st.model_name = old.model_name
            self._stations[channel_id] = st
            logger.info("[Weighing] ch%s 登记称重模式: 料别=%s 型号数=%d 去皮=%s 料检=%s",
                        channel_id, cfg.get("materials"), len(cfg.get("models") or {}),
                        cfg.get("tare_mode"), cfg.get("material_check"))

    def is_weighing_channel(self, channel_id: int) -> bool:
        return channel_id in self._configs

    def _get_station(self, channel_id: int) -> Optional[WeighingStation]:
        return self._stations.get(channel_id)

    # ---------- 控制面 (API 调) ----------
    def set_context(self, channel_id: int, operator=None, model_name=None):
        with self._lock:
            st = self._get_station(channel_id)
            if st is None:
                return None
            st.set_context(operator=operator, model_name=model_name)
            return st.snapshot()

    def set_material_label(self, channel_id: int, label):
        """喂入视觉识别料别标签 (6.3; 真模型/虚拟测试都走这)。"""
        with self._lock:
            st = self._get_station(channel_id)
            if st is None:
                return None
            st.set_material_label(label)
            return st.snapshot()

    def start_product(self, channel_id: int, sn: str):
        with self._lock:
            st = self._get_station(channel_id)
            cfg = self._configs.get(channel_id)
            if st is None or cfg is None:
                return None
            events = st.start_product(sn, cfg)
        self._execute(channel_id, events)
        return st.snapshot()

    def manual_tare(self, channel_id: int):
        with self._lock:
            st = self._get_station(channel_id)
            if st is None:
                return None
            ok = self._send_device(st.weight_device_id, "T", "去皮")
            st.manual_tare_done()
            return {"sent": ok, "snapshot": st.snapshot()}

    def manual_zero(self, channel_id: int):
        with self._lock:
            st = self._get_station(channel_id)
            if st is None:
                return None
            ok = self._send_device(st.weight_device_id, "Z", "置零")
            return {"sent": ok, "snapshot": st.snapshot()}

    def reset_station(self, channel_id: int):
        with self._lock:
            st = self._get_station(channel_id)
            if st is None:
                return None
            st.reset()
            return st.snapshot()

    def _augment(self, snap, channel_id):
        """给快照补前端看板要用的派生字段 (可选型号/料别表), 不污染状态机核心。"""
        if not snap:
            return snap
        cfg = self._configs.get(channel_id) or {}
        models = cfg.get("models") or {}
        snap["available_models"] = list(models.keys())
        snap["material_check"] = cfg.get("material_check", "sequence")
        # 当前型号各料标准量, 给看板展示"标准/实投"对比
        mname = snap.get("model_name")
        snap["model_specs"] = (models.get(mname) or {}) if mname else {}
        return snap

    def snapshot(self, channel_id: int = None):
        with self._lock:
            if channel_id is not None:
                st = self._get_station(channel_id)
                return self._augment(st.snapshot(), channel_id) if st else None
            return {"stations": [self._augment(st.snapshot(), st.channel_id)
                                 for st in self._stations.values()]}

    def get_records(self, channel_id: int, limit: int = 200):
        """逐件记录 (6.1)。优先读 DB (重启不丢); DB 不可用时回退内存台账。"""
        try:
            from backend.db.database import SessionLocal
            from backend.models.weighing_models import WeighingRecord
            db = SessionLocal()
            try:
                rows = (db.query(WeighingRecord)
                        .filter(WeighingRecord.channel_id == channel_id)
                        .order_by(WeighingRecord.id.asc())
                        .all())
                out = [{
                    "material": r.material, "standard": r.standard,
                    "initial": r.initial, "net": r.net, "verdict": r.verdict,
                    "ts": r.ts, "sn": r.product_sn, "model": r.model_name,
                    "operator": r.operator, "channel_id": r.channel_id,
                } for r in rows]
                return out[-limit:]
            finally:
                db.close()
        except Exception as e:
            logger.warning("[Weighing] ch%s 读库失败, 回退内存台账: %s", channel_id, e)
            with self._lock:
                dq = self._records.get(channel_id)
                return list(dq)[-limit:] if dq else []

    # ---------- 核心喂入 (外设管线调) ----------
    def feed_weight(self, channel_id: int, weight, timestamp=None, device_id=None):
        """外设管线每帧称重读数喂入。非 weighing 通道直接忽略, 零影响。"""
        if channel_id not in self._configs:
            return
        try:
            with self._lock:
                st = self._stations.get(channel_id)
                cfg = self._configs.get(channel_id)
                if st is None or cfg is None:
                    return
                if st.weight_device_id is None and device_id is not None:
                    st.weight_device_id = device_id
                events = st.on_weight(float(weight), timestamp or time.time(), cfg)
            if events:
                self._execute(channel_id, events)
        except Exception as e:
            logger.warning("[Weighing] ch%s feed_weight 异常(隔离): %s", channel_id, e)

    # ---------- 副作用执行层 (事件 → 主程序调用) ----------
    def _execute(self, channel_id: int, events):
        cfg = self._configs.get(channel_id) or {}
        for ev in events or []:
            action = ev.get("action")
            try:
                if action == "send_tare":
                    self._send_device(ev.get("device_id"), "T", "去皮")
                elif action == "send_zero":
                    self._send_device(ev.get("device_id"), "Z", "置零")
                elif action == "alarm":
                    self._do_alarm(cfg, ev)
                elif action == "record":
                    self._do_record(channel_id, ev.get("result"))
                elif action == "product_done":
                    self._do_product_done(ev)
                # product_start: 仅日志
            except Exception as e:
                logger.warning("[Weighing] ch%s 执行事件 %s 异常(隔离): %s",
                               channel_id, action, e)

    def _send_device(self, device_id, command, label):
        if device_id is None:
            logger.warning("[Weighing] 无法%s: 未绑定称重器设备id", label)
            return False
        try:
            from backend.services.external_device import get_external_device_service
            svc = get_external_device_service()
            res = svc.send_command(device_id, command)
            ok = bool(res.get("success")) if isinstance(res, dict) else bool(res)
            logger.info("[Weighing] %s 指令 %s → 设备%s = %s", label, command, device_id, ok)
            return ok
        except Exception as e:
            logger.warning("[Weighing] %s 发指令失败(隔离): %s", label, e)
            return False

    def _alarm_event_for(self, cfg, kind):
        return {
            "shortage": cfg.get("alarm_event_shortage", 2),
            "over": cfg.get("alarm_event_over", 2),
            "wrong": cfg.get("alarm_event_wrong", 2),
            "precheck": cfg.get("alarm_event_precheck", 2),
        }.get(kind, 2)

    def _do_alarm(self, cfg, ev):
        """报警走主程序统一事件链 (_trigger_event: 计数器+三色灯+Toast+语音)。

        拿不到 VSM / _trigger_event 时兜底直接打三色灯 alarm_router。
        """
        kind = ev.get("kind")
        channel_id = ev.get("channel_id", 0) or 0
        event_id = self._alarm_event_for(cfg, kind)
        reason = ev.get("reason", "")
        fired = False
        try:
            from backend.api.channel_manager import get_channel_manager
            mgr = get_channel_manager().get(channel_id)
            if hasattr(mgr, "_trigger_event"):
                mgr._trigger_event(event_id)
                fired = True
        except Exception as e:
            logger.debug("[Weighing] _trigger_event 失败, 兜底直接打灯: %s", e)
        if not fired:
            try:
                from backend.api.alarm import alarm_router
                alarm_router.trigger_alarm(f"event{event_id}", channel_id=channel_id)
            except Exception as e:
                logger.warning("[Weighing] 三色灯报警失败(隔离): %s", e)
        logger.info("[Weighing] 报警 %s ch=%s event=%s: %s", kind, channel_id, event_id, reason)

    def _do_record(self, channel_id, result):
        """逐件逐料记录 (6.1)。先落内存台账 (快, 供实时), 再落 DB (持久, 重启不丢)。

        DB 写入全程 try/except 隔离: 落库失败绝不能拖垮称重主链路 (内存台账仍在)。
        """
        if not result:
            return
        with self._lock:
            dq = self._records.get(channel_id)
            if dq is None:
                dq = deque(maxlen=2000)
                self._records[channel_id] = dq
            dq.append(result)
        # 持久化到 DB (6.1 长期可追溯)
        try:
            from backend.db.database import SessionLocal
            from backend.models.weighing_models import WeighingRecord
            db = SessionLocal()
            try:
                db.add(WeighingRecord(
                    channel_id=channel_id,
                    product_sn=result.get("sn"),
                    model_name=result.get("model"),
                    operator=result.get("operator"),
                    material=result.get("material"),
                    standard=result.get("standard"),
                    initial=result.get("initial", 0.0),
                    net=result.get("net", 0.0),
                    verdict=result.get("verdict", ""),
                    ts=result.get("ts"),
                ))
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.warning("[Weighing] ch%s 记录落库失败(隔离, 内存台账仍在): %s", channel_id, e)
        logger.info("[Weighing] 记录投料 ch%s: sn=%s 型号=%s 料别=%s 终值=%skg 判定=%s",
                    channel_id, result.get("sn"), result.get("model"),
                    result.get("material"), result.get("net"), result.get("verdict"))

    def _do_product_done(self, ev):
        """一件全部料别完成: 上传 (6.1)。后续阶段完善 MES payload/数据页。"""
        logger.info("[Weighing] 产品完成 ch%s: sn=%s 型号=%s 结果=%s",
                    ev.get("channel_id"), ev.get("sn"), ev.get("model"),
                    [(r.get("material"), r.get("verdict")) for r in ev.get("results", [])])
        try:
            from backend.services.mes_gateway import get_mes_gateway
            gw = get_mes_gateway()
            gw.dispatch("weighing_product_done", {
                "sn": ev.get("sn"),
                "model": ev.get("model"),
                "operator": ev.get("operator"),
                "channel_id": ev.get("channel_id"),
                "results": ev.get("results", []),
            }, channel_id=ev.get("channel_id") or 0)
        except Exception as e:
            logger.debug("[Weighing] product_done 上传失败(隔离): %s", e)


# ==================== 单例 ====================
_engine = None
_engine_lock = threading.Lock()


def get_weighing_engine() -> WeighingEngine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = WeighingEngine()
    return _engine
