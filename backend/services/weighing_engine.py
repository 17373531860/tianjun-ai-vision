"""原生称重投料检测引擎 (logic_mode='weighing' + 步骤门控融合模式)。

这是主程序**原生**检测模式之一 (与 sequential/detection/custom/tracking 并列), 但属于
**设备驱动**模式: 由称重器每帧读数推进状态机, 而非 YOLO 视频帧。对应《萍乡百斯特项目
终验收标准》第六章投料防错全套需求。

业务流程 (每件产品按型号分若干道料, 如钢帽水泥/钢脚水泥, 逐道投料称重):
    选人员/型号 → 扫码绑产品 →
      料别0: 放件 → 自动/手动去皮归零 → 投料 → 读数稳定 → 对比标准量 → 判定 → 记录
      料别1: 放件 → 去皮 → 投料 → 稳定 → 判定 → 记录
    → 全部料别完成 → 上传 → 自动置零复位

两种驱动模式 (v3.35 融合架构, 存量客户零差异):
- drive_mode='scale'    (默认, 与 v3.31 发布行为一致): 秤读数自主推进 WeighingStation 相位。
- drive_mode='step_gate' (融合): 周期主线是视觉顺序 SOP (logic_mode='sequential'),
  秤数据降级为**步骤完成门控** — 视觉步骤确认后先"武装"一个 DeviceGate, 秤读数满足
  条件 (去皮完成 / 称重判定通过) 才放行该步骤入周期。违序/缺步/超时报警由顺序模式免费继承。

设计原则 (与项目既有"状态机纯逻辑可单测"范式一致):
- WeighingStation / DeviceGate **不**直接调主程序副作用 (报警/去皮指令/上传);
  只推进状态 + **返回事件列表**。
- 副作用由 WeighingEngine 的执行层翻译成真正调用 (alarm/事件链/send_command/落库),
  这样状态机能脱离主程序纯跑单测 (喂合成重量序列 + 合成视觉标签即可验全流程)。

配置来自项目 pipeline_config.weighing (不新增 ORM 列, 符合"优先扩 JSON"原则)。
判定阈值/型号标准量/料别顺序/去皮方式/前置校验/报警事件映射/前置选择有效期全部可配。
"""
from __future__ import annotations

import datetime as _dt
import logging
import threading
import time
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)


# ==================== 配置默认值 ====================
# 单位统一 kg。默认偏保守, 现场按型号工艺改。
DEFAULT_WEIGHING_CONFIG = {
    # v3.35 驱动模式 (融合架构):
    #   scale     = 秤读数自主推进投料状态机 (v3.31 发布行为, 默认, 存量零差异)
    #   step_gate = 视觉顺序 SOP 为周期主线, 秤数据只做"步骤完成门控"
    #               (钢帽放秤→去皮门控 / 水泥称重→标准量判定门控)
    "drive_mode": "scale",

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

    # v3.35 前置选择有效期 (通用策略, 默认 never = 现行为零差异):
    #   mode: never | daily (每天 reset_time 失效) | shift (按班次表) | hours (每 N 小时)
    #   expire_fields: 失效时清哪些上下文 ("model" / "operator")
    "context_expiry": {
        "mode": "never",
        "reset_time": "08:00",         # daily 模式: 每天此刻后旧选择失效
        "shifts": [],                  # shift 模式: [{"name":"早班","start":"08:00","end":"16:00"}]
        "hours": 8,                    # hours 模式: 选择 N 小时后失效
        "expire_fields": ["model"],    # 默认只清型号 (客户诉求: 上班没切型号要报警)
    },

    # v3.35 视觉料源防错 (动作 × 固定区域 → 料别/违规判定, 默认关零差异):
    #   enabled: 总开关
    #   source:  region_action = 动作标签命中区域映射料别 (百斯特: 舀料 × 大盆区域)
    #            direct_label  = 检测标签本身就是料别 (料桶可视觉区分的客户)
    #   rules:   [{"name":"钢帽料盆", "labels":["舀料"], "polygon":[[x,y]*n],
    #              "material":"钢帽水泥", "min_frames":3, "mode":"map"|"restrict"}]
    #     mode=map      → 命中即上报料别标签 (喂 set_material_label, 走投错校验)
    #     mode=restrict → 限区: 该动作只允许出现在本区域, 区域外出现即报警
    #   wrong_block: 投错时 True=拦截等纠正(现行为) False=仅报警放行
    #   cooldown_sec: 同一规则报警冷却
    "visual_guard": {
        "enabled": False,
        "source": "region_action",
        "rules": [],
        "wrong_block": True,
        "cooldown_sec": 5.0,
    },

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
    """把项目里配的 weighing 配置深合并到默认值之上, 缺字段不崩。

    嵌套 dict 默认值 (context_expiry / visual_guard) 做一层深合并:
    用户只配了部分子键时其余子键仍取默认, 老项目 JSON 零迁移。
    """
    cfg = dict(DEFAULT_WEIGHING_CONFIG)
    if isinstance(user_cfg, dict):
        for k, v in user_cfg.items():
            if v is None:
                continue
            default_v = DEFAULT_WEIGHING_CONFIG.get(k)
            if isinstance(default_v, dict) and isinstance(v, dict):
                merged = dict(default_v)
                merged.update({sk: sv for sk, sv in v.items() if sv is not None})
                cfg[k] = merged
            else:
                cfg[k] = v
    return cfg


# ==================== 前置选择有效期 (v3.35, 纯函数可单测) ====================
def _parse_hhmm(s, default=(8, 0)):
    """'08:30' → (8, 30); 解析失败回默认。

    '24:00' 是班次表常见写法 (一天结束), 归一化为 00:00 —— 配合 _shift_key
    的跨午夜分支 (start > end) 语义正好等价于"到当天结束"。
    """
    try:
        hh, mm = str(s).strip().split(":")
        return int(hh) % 24, int(mm) % 60
    except Exception:
        return default


def _shift_key(dt: _dt.datetime, shifts: list):
    """返回 dt 所属班次的锚 (班次名, 班次开始日期); 不在任何班次 → (None, 当天)。

    跨午夜班次 (start > end, 如 22:00-06:00) 归属到"开始那天"。
    """
    t = dt.time()
    for s in shifts or []:
        sh, sm = _parse_hhmm(s.get("start"), (0, 0))
        eh, em = _parse_hhmm(s.get("end"), (0, 0))
        start = _dt.time(sh, sm)
        end = _dt.time(eh, em)
        name = s.get("name") or f"{s.get('start')}-{s.get('end')}"
        if start <= end:
            if start <= t < end:
                return (name, dt.date())
        else:
            if t >= start:
                return (name, dt.date())
            if t < end:
                return (name, dt.date() - _dt.timedelta(days=1))
    return (None, dt.date())


def context_expired(expiry_cfg: Optional[dict], set_at_ts, now_ts=None) -> bool:
    """判断某次前置选择 (人员/型号) 是否已按有效期策略失效。

    mode:
      never = 永不失效 (默认, 现行为零差异)
      hours = 选择后 N 小时失效
      daily = 每天 reset_time 之后, 之前的选择失效 (客户诉求: 上班没重选型号要报警)
      shift = 跨班次即失效 (含"选择时不在任何班次"与"当前不在任何班次"都算不同班)
    """
    exp = expiry_cfg or {}
    mode = exp.get("mode", "never")
    if mode == "never" or not set_at_ts:
        return False
    now_ts = now_ts if now_ts is not None else time.time()
    if now_ts <= set_at_ts:
        return False
    if mode == "hours":
        try:
            h = float(exp.get("hours", 8) or 8)
        except (TypeError, ValueError):
            h = 8.0
        return (now_ts - set_at_ts) >= h * 3600.0
    set_dt = _dt.datetime.fromtimestamp(set_at_ts)
    now_dt = _dt.datetime.fromtimestamp(now_ts)
    if mode == "daily":
        hh, mm = _parse_hhmm(exp.get("reset_time", "08:00"))
        boundary = now_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if boundary > now_dt:
            boundary -= _dt.timedelta(days=1)
        return set_dt < boundary
    if mode == "shift":
        return _shift_key(set_dt, exp.get("shifts")) != _shift_key(now_dt, exp.get("shifts"))
    return False


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
        # v3.35 前置选择时刻 (context_expiry 有效期判定用)
        self.ctx_set_at = {}    # {"operator": ts, "model": ts}

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
        now = time.time()
        if operator is not None:
            self.operator = operator
            self.ctx_set_at["operator"] = now
        if model_name is not None:
            self.model_name = model_name
            self.ctx_set_at["model"] = now

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


# ==================== 视觉料源防错 (v3.35, 纯逻辑可单测) ====================
def point_in_polygon(px: float, py: float, polygon) -> bool:
    """射线法判点在归一化多边形内 (纯 Python, 不依赖 cv2, 状态机可脱主程序单测)。"""
    if not polygon or len(polygon) < 3:
        return True
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = float(polygon[i][0]), float(polygon[i][1])
        xj, yj = float(polygon[j][0]), float(polygon[j][1])
        if (yi > py) != (yj > py):
            x_cross = (xj - xi) * (py - yi) / (yj - yi) + xi
            if px < x_cross:
                inside = not inside
        j = i
    return inside


class VisualGuardMatcher:
    """动作 × 固定区域 → 料别映射 / 限区违规 判定器 (纯逻辑)。

    每帧喂 YOLO 检测结果 (归一化坐标), 按规则输出事件:
      - {"action": "material_label", "material": ...}   命中料别映射区域 (mode=map)
      - {"action": "alarm", "kind": "guard_restrict"}   限区动作在区域外出现 (mode=restrict)

    连续帧确认 (min_frames) 防单帧误检; 报警按规则冷却防刷屏。
    百斯特场景: `舀料` 命中大盆区域 → 料别=钢帽水泥; `装杯` 只允许在小盆区域。
    direct_label 模式: 检测标签本身可视觉区分料别的客户 → 不画区域, 标签直接映射料别。
    """

    def __init__(self, guard_cfg: dict):
        self.cfg = guard_cfg or {}
        self.rules = [r for r in (self.cfg.get("rules") or []) if isinstance(r, dict)]
        self.source = self.cfg.get("source", "region_action")
        self.cooldown = float(self.cfg.get("cooldown_sec", 5.0) or 0)
        # 规则名 → 连续命中帧数 / 上次报警时刻
        self._streaks = {}
        self._last_alarm = {}

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled")) and bool(self.rules)

    def reset(self):
        self._streaks = {}
        self._last_alarm = {}

    @staticmethod
    def _det_center(det):
        try:
            x = float(det.get("x", 0))
            y = float(det.get("y", 0))
            w = float(det.get("w", 0))
            h = float(det.get("h", 0))
            return x + w / 2.0, y + h / 2.0
        except (TypeError, ValueError):
            return None

    def _rule_hit(self, rule, detections) -> bool:
        """本帧是否命中规则 (map: 动作在区域内 / restrict: 动作在区域外)。"""
        labels = set(rule.get("labels") or [])
        if not labels:
            return False
        mode = rule.get("mode", "map")
        polygon = rule.get("polygon")
        for det in detections or []:
            if det.get("label") not in labels:
                continue
            if self.source == "direct_label" or not polygon:
                # 标签直接映射: 出现即命中 (map); restrict 无区域无意义, 不命中
                if mode == "map":
                    return True
                continue
            center = self._det_center(det)
            if center is None:
                continue
            inside = point_in_polygon(center[0], center[1], polygon)
            if mode == "map" and inside:
                return True
            if mode == "restrict" and not inside:
                return True
        return False

    def feed(self, detections, now=None):
        """喂一帧检测结果, 返回事件列表。"""
        if not self.enabled:
            return []
        now = now if now is not None else time.time()
        events = []
        for rule in self.rules:
            name = rule.get("name") or str(id(rule))
            hit = self._rule_hit(rule, detections)
            streak = self._streaks.get(name, 0) + 1 if hit else 0
            self._streaks[name] = streak
            need = max(1, int(rule.get("min_frames", 3) or 1))
            if streak != need:
                # 只在恰好攒满的那一帧发事件 (持续命中不重复发, 离场清零后可再发)
                continue
            mode = rule.get("mode", "map")
            if mode == "map":
                material = rule.get("material")
                if material:
                    events.append({"action": "material_label",
                                   "material": material, "rule": name})
            elif mode == "restrict":
                last = self._last_alarm.get(name, 0.0)
                if self.cooldown > 0 and (now - last) < self.cooldown:
                    continue
                self._last_alarm[name] = now
                labels = "/".join(rule.get("labels") or [])
                events.append({
                    "action": "alarm", "kind": "guard_restrict", "rule": name,
                    "reason": f"动作限区违规: [{labels}] 出现在允许区域「{name}」之外",
                })
        return events


# ==================== 步骤外设门控 (v3.35 融合架构, 纯逻辑可单测) ====================
class StepGate:
    """单个"视觉步骤 × 外设条件"门控实例。

    视觉步骤确认后被"武装", 由秤读数推进:
      kind='tare'         毛重超阈稳定 → 发去皮指令 → 放行 (对应"钢帽放秤"步骤)
      kind='weight_judge' 净重稳定 → 按当前型号 × 指定料别判定标准量 → 记录 + 报警
                          → 判定合格放行; 不合格且 block=True 时留在门控等纠正
                          (重量变化后重新稳定会再判), block=False 记 NG 也放行

    与 WeighingStation 同款"只产事件列表"范式, 副作用由引擎执行层翻译。
    """

    def __init__(self, channel_id: int, label: str, gate_cfg: dict,
                 weight_device_id=None):
        self.channel_id = channel_id
        self.label = label
        self.cfg = gate_cfg or {}
        self.weight_device_id = weight_device_id
        self.status = "armed"          # armed / passed
        self._buf = deque(maxlen=32)
        self._tare_sent = False
        self._last_judged_net = None   # 防同一稳定读数反复判定/报警
        self._precheck_alarm_at = 0.0

    def _is_stable(self, wcfg) -> bool:
        min_n = int(wcfg.get("stable_min_samples", 3))
        tol = float(wcfg.get("stable_tol", 0.003))
        if len(self._buf) < min_n:
            return False
        recent = [w for _, w in list(self._buf)[-min_n:]]
        return (max(recent) - min(recent)) <= tol

    def on_weight(self, weight, ts, wcfg, station: "WeighingStation"):
        """喂一帧读数, 返回事件列表; 条件满足时置 self.status='passed'。"""
        events = []
        if self.status == "passed":
            return events
        self._buf.append((ts, float(weight)))
        kind = self.cfg.get("kind", "tare")

        if kind == "tare":
            if self._tare_sent:
                return events
            trigger = float(wcfg.get("tare_trigger_weight", 0.05))
            if weight >= trigger and self._is_stable(wcfg):
                events.append({"action": "send_tare",
                               "device_id": self.weight_device_id,
                               "channel_id": self.channel_id,
                               "gate_label": self.label})
                self._tare_sent = True
                self.status = "passed"
                events.append({"action": "gate_passed", "gate_label": self.label,
                               "channel_id": self.channel_id, "kind": kind})
            return events

        if kind == "weight_judge":
            min_w = float(wcfg.get("measure_min_weight", 0.005))
            if weight < min_w or not self._is_stable(wcfg):
                return events
            net = round(float(weight), 4)
            tol = float(wcfg.get("stable_tol", 0.003))
            if (self._last_judged_net is not None
                    and abs(net - self._last_judged_net) <= tol):
                return events  # 同一稳定读数已判过, 等重量变化 (工人补料/舀出)
            # 前置: 需要型号才能查标准量 (有效期失效由引擎在喂入前清上下文)
            if not station.model_name:
                now = ts or time.time()
                if now - self._precheck_alarm_at >= 5.0:
                    self._precheck_alarm_at = now
                    events.append({"action": "alarm", "kind": "precheck",
                                   "channel_id": self.channel_id,
                                   "reason": f"步骤[{self.label}] 称重判定需要先选产品型号"})
                return events
            material = self.cfg.get("material") or station._current_material()
            # 视觉料别防错: visual 校验开着且视觉识别与门控应投料别不符 → 拦截
            wrong = station._check_wrong_material(wcfg, material)
            if wrong:
                self._last_judged_net = net
                events.append({"action": "alarm", "kind": "wrong",
                               "channel_id": self.channel_id, "reason": wrong,
                               "material": material, "visual": station.visual_label})
                return events
            spec = get_model_spec(wcfg, station.model_name, material) if material else None
            if spec is None:
                verdict, standard = "no_spec", None
            else:
                verdict = judge_amount(net, spec)
                standard = float(spec.get("standard", 0))
            self._last_judged_net = net
            result = {
                "material": material, "standard": standard,
                "initial": 0.0, "net": net, "verdict": verdict, "ts": ts,
                "sn": station.product_sn, "model": station.model_name,
                "operator": station.operator, "channel_id": self.channel_id,
                "gate_label": self.label,
            }
            events.append({"action": "record", "result": result})
            if verdict in ("shortage", "over"):
                events.append({"action": "alarm", "kind": verdict,
                               "channel_id": self.channel_id,
                               "reason": _verdict_reason(verdict, material, net, standard),
                               "result": result})
                if self.cfg.get("block", True):
                    return events  # 拦截: 留在门控等纠正 (重量变化后重判)
            self.status = "passed"
            station.visual_label = None
            events.append({"action": "gate_passed", "gate_label": self.label,
                           "channel_id": self.channel_id, "kind": kind,
                           "verdict": verdict})
            return events

        # 未知门控类型: 直接放行, 不卡生产
        self.status = "passed"
        events.append({"action": "gate_passed", "gate_label": self.label,
                       "channel_id": self.channel_id, "kind": kind})
        return events


# ==================== 称重引擎 (单例: 工位注册表 + 配置 + 副作用执行) ====================
class WeighingEngine:
    """主程序级称重引擎。

    - 按通道持有 WeighingStation + 该通道的 weighing 配置 (来自激活项目)。
    - logic_mode='weighing' 或 weighing.drive_mode='step_gate' (v3.35 融合) 的通道
      才被 set_channel_config 登记; 其它通道喂数据直接忽略。
    - feed_weight: 外设管线每帧称重读数喂入 → 推进状态机 → 执行副作用。
    - 副作用 (报警/去皮置零/逐件记录) 懒加载主程序模块, 全部 try/except 隔离,
      引擎出问题绝不能拖垮外设数据主链路。
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._stations = {}      # {channel_id: WeighingStation}
        self._configs = {}       # {channel_id: merged cfg}  (weighing 模式 / step_gate 融合通道)
        self._records = {}       # {channel_id: deque(逐件逐料记录)}  6.1 台账(内存, 后续接DB/MES)
        # v3.35 步骤门控 (drive_mode='step_gate'): {channel_id: {label: StepGate}}
        self._gates = {}
        # v3.35 视觉料源防错: {channel_id: VisualGuardMatcher}
        self._guards = {}
        # 前置选择失效报警节流: {channel_id: ts}
        self._ctx_expire_alarm_at = {}

    # ---------- 通道登记 (set_project_config 时调) ----------
    def set_channel_config(self, channel_id: int, weighing_cfg: Optional[dict]):
        """激活称重能力时登记该通道配置; 传 None 注销 (切回别的模式)。

        两种登记来源 (source_project_config_apply 决定):
        - logic_mode='weighing' 项目 (drive_mode 默认 scale)
        - sequential 项目 + pipeline_config.weighing.drive_mode='step_gate' (融合)
        """
        with self._lock:
            if weighing_cfg is None:
                self._configs.pop(channel_id, None)
                self._stations.pop(channel_id, None)
                self._gates.pop(channel_id, None)
                self._guards.pop(channel_id, None)
                logger.info("[Weighing] ch%s 注销称重模式", channel_id)
                return
            cfg = merge_config(weighing_cfg)
            self._configs[channel_id] = cfg
            # 配置变了(料别/工位)重建状态机, 但保留人员/型号上下文 (含选择时刻)
            old = self._stations.get(channel_id)
            st = WeighingStation(channel_id,
                                 weight_device_id=cfg.get("weight_device_id"),
                                 name=cfg.get("station_name", ""))
            if old is not None:
                st.operator = old.operator
                st.model_name = old.model_name
                st.ctx_set_at = dict(old.ctx_set_at)
            self._stations[channel_id] = st
            self._gates[channel_id] = {}
            guard_cfg = cfg.get("visual_guard") or {}
            self._guards[channel_id] = VisualGuardMatcher(guard_cfg) \
                if guard_cfg.get("enabled") else None
            logger.info("[Weighing] ch%s 登记称重模式: drive=%s 料别=%s 型号数=%d 去皮=%s 料检=%s 视觉防错=%s",
                        channel_id, cfg.get("drive_mode", "scale"),
                        cfg.get("materials"), len(cfg.get("models") or {}),
                        cfg.get("tare_mode"), cfg.get("material_check"),
                        bool(guard_cfg.get("enabled")))

    def is_weighing_channel(self, channel_id: int) -> bool:
        return channel_id in self._configs

    def drive_mode(self, channel_id: int) -> str:
        cfg = self._configs.get(channel_id) or {}
        return cfg.get("drive_mode", "scale")

    # ---------- v3.35 前置选择有效期 ----------
    def _maybe_expire_context(self, channel_id: int, st: "WeighingStation", cfg: dict):
        """按 context_expiry 策略把过期的人员/型号选择清掉 (+节流报警提示重选)。

        调用点: feed_weight / start_product 入口 (持锁)。mode=never 一次比较即返回。
        """
        exp = cfg.get("context_expiry") or {}
        if exp.get("mode", "never") == "never":
            return
        fields = exp.get("expire_fields") or ["model"]
        expired = []
        now = time.time()
        if "model" in fields and st.model_name \
                and context_expired(exp, st.ctx_set_at.get("model"), now):
            st.model_name = None
            st.ctx_set_at.pop("model", None)
            expired.append("型号")
        if "operator" in fields and st.operator \
                and context_expired(exp, st.ctx_set_at.get("operator"), now):
            st.operator = None
            st.ctx_set_at.pop("operator", None)
            expired.append("人员")
        if expired:
            last = self._ctx_expire_alarm_at.get(channel_id, 0.0)
            if now - last >= 10.0:
                self._ctx_expire_alarm_at[channel_id] = now
                self._execute(channel_id, [{
                    "action": "alarm", "kind": "precheck", "channel_id": channel_id,
                    "reason": f"前置选择已过期({'/'.join(expired)}), 请重新选择后再作业",
                }])
            logger.info("[Weighing] ch%s 前置选择过期清除: %s", channel_id, expired)

    # ---------- v3.35 步骤门控 (drive_mode='step_gate', 顺序状态机调) ----------
    def arm_step_gate(self, channel_id: int, label: str, gate_cfg: dict) -> bool:
        """视觉步骤确认后武装门控。已武装/已登记则复用, 返回是否处于武装态。"""
        with self._lock:
            if channel_id not in self._configs:
                return False
            gates = self._gates.setdefault(channel_id, {})
            g = gates.get(label)
            if g is None:
                st = self._stations.get(channel_id)
                g = StepGate(channel_id, label, gate_cfg,
                             weight_device_id=(st.weight_device_id if st else None)
                             or (self._configs[channel_id] or {}).get("weight_device_id"))
                gates[label] = g
                logger.info("[Weighing] ch%s 武装步骤门控 [%s] kind=%s",
                            channel_id, label, (gate_cfg or {}).get("kind", "tare"))
            return g.status != "passed"

    def consume_gate_if_passed(self, channel_id: int, label: str) -> bool:
        """门控已放行 → 弹出实例返回 True (步骤此刻入周期, 下周期重新武装)。"""
        with self._lock:
            gates = self._gates.get(channel_id) or {}
            g = gates.get(label)
            if g is not None and g.status == "passed":
                gates.pop(label, None)
                return True
            return False

    def reset_gates(self, channel_id: int):
        """清空该通道全部门控 (周期强制结算 / 停止检测 / 切项目时调)。"""
        with self._lock:
            if channel_id in self._gates:
                self._gates[channel_id] = {}
            guard = self._guards.get(channel_id)
            if guard is not None:
                guard.reset()

    def gate_states(self, channel_id: int) -> dict:
        """{label: status} 供快照/前端展示"等秤中"状态。"""
        with self._lock:
            return {lbl: g.status for lbl, g in (self._gates.get(channel_id) or {}).items()}

    # ---------- v3.35 视觉料源防错 (推理线程每帧调, 无守卫时零开销早退) ----------
    def feed_detections(self, channel_id: int, detections):
        """喂一帧 YOLO 检测结果给视觉守卫: 料别映射 → set_material_label; 限区违规 → 报警。"""
        guard = self._guards.get(channel_id)
        if guard is None:
            return
        try:
            with self._lock:
                events = guard.feed(detections)
                st = self._stations.get(channel_id)
            for ev in events:
                if ev.get("action") == "material_label" and st is not None:
                    st.set_material_label(ev.get("material"))
                    logger.info("[Weighing] ch%s 视觉守卫命中规则[%s] → 料别=%s",
                                channel_id, ev.get("rule"), ev.get("material"))
                elif ev.get("action") == "alarm":
                    ev.setdefault("channel_id", channel_id)
                    self._execute(channel_id, [ev])
        except Exception as e:
            logger.warning("[Weighing] ch%s feed_detections 异常(隔离): %s", channel_id, e)

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
            self._maybe_expire_context(channel_id, st, cfg)
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
        # v3.35 融合模式: 驱动方式 + 各步骤门控状态 (armed=等秤中 / passed=已放行)
        snap["drive_mode"] = cfg.get("drive_mode", "scale")
        snap["gates"] = {lbl: g.status
                         for lbl, g in (self._gates.get(channel_id) or {}).items()}
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
        """外设管线每帧称重读数喂入。非称重通道直接忽略, 零影响。

        drive_mode='scale'    → 秤读数推进 WeighingStation 相位状态机 (v3.31 行为)。
        drive_mode='step_gate' → 读数只喂给已武装的步骤门控 (视觉 SOP 才是周期主线);
                                 状态机仅收读数入缓冲供看板显示实时重量。
        """
        if channel_id not in self._configs:
            return
        try:
            ts = timestamp or time.time()
            events = []
            with self._lock:
                st = self._stations.get(channel_id)
                cfg = self._configs.get(channel_id)
                if st is None or cfg is None:
                    return
                if st.weight_device_id is None and device_id is not None:
                    st.weight_device_id = device_id
                self._maybe_expire_context(channel_id, st, cfg)
                if cfg.get("drive_mode", "scale") == "step_gate":
                    st._push_weight(ts, float(weight))  # 仅供 live_weight 看板
                    for g in list((self._gates.get(channel_id) or {}).values()):
                        if g.status != "passed":
                            if g.weight_device_id is None:
                                g.weight_device_id = st.weight_device_id
                            g_events = g.on_weight(float(weight), ts, cfg, st)
                            for _ev in g_events:
                                # 门控判定记录同步进状态机台账 → 看板"本件结果"可见
                                if _ev.get("action") == "record" and _ev.get("result"):
                                    st.results.append(_ev["result"])
                            events += g_events
                else:
                    events = st.on_weight(float(weight), ts, cfg)
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
            "guard_restrict": cfg.get("alarm_event_guard", 2),
        }.get(kind, 2)

    def _do_alarm(self, cfg, ev):
        """报警借用主程序事件响应面 (三色灯+Toast+语音+计数器+人工确认定格)。

        v3.35 修复: 之前调 _trigger_event(event_id) 少传 reason 必抛 TypeError,
        永远走兜底打灯, 事件链 (require_ack 人工确认/计数器/Toast) 全部旁路。
        改为 fire_external_event_response —— 只借事件响应面, **不**结束检测周期
        (融合模式下周期由视觉 SOP 管, 秤报警不能把在制周期切掉), 且事件配了
        require_ack=True 时自动进入人工确认定格 (S5 报警确认闭锁的后端通路)。
        拿不到 VSM 时兜底直接打三色灯 alarm_router。
        """
        kind = ev.get("kind")
        channel_id = ev.get("channel_id", 0) or 0
        event_id = self._alarm_event_for(cfg, kind)
        reason = ev.get("reason", "")
        fired = False
        try:
            from backend.api.channel_manager import get_channel_manager
            mgr = get_channel_manager().get(channel_id)
            if hasattr(mgr, "fire_external_event_response"):
                fired = bool(mgr.fire_external_event_response(
                    event_id, reason, source="weighing"))
        except Exception as e:
            logger.debug("[Weighing] fire_external_event_response 失败, 兜底直接打灯: %s", e)
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
