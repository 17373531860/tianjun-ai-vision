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
    "operator_from_users": False,  # v3.45 监控页人员改名单下拉 (用户系统启用账号); 默认自由填写
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

    # ==================== v3.39 两阶段流水线模式 (drive_mode='pipeline') ====================
    # 萍乡百斯特场景: 工件坐秤装料 + 上一件在压机侧"加钢脚水泥"并行 —— 同时最多两件在制。
    # 阶段一(秤上): 上秤→去皮→装料→净重就绪→离秤瞬间冻结重量事实并触发 OK/NG;
    # 阶段二(秤下): 冻结件进"待收尾队列", 等收尾标签(加钢脚水泥) FIFO 结案后才推 MES/落库。
    # 秤指令严格重量驱动(视觉只加速/佐证), 模型漏检不影响称重主链路。
    "pipeline": {
        "material": "钢帽水泥",          # 本模式单料判定: 净重合格带查 models[型号][此料别]
        "label_onscale": "工件上秤",     # 标签①(双语义: 空秤+皮重范围=新件; 装料中=回秤)
        "label_fill": "加水泥",          # 标签②(错盆守卫用, 配 visual_guard forbid 规则)
        "label_finalize": "加钢脚水泥",   # 标签③(收尾: FIFO 结案队头件)
        "onscale_polygon": None,        # 标签①有效域(秤台区 Z1, 归一化多边形); None=不过滤
        "tare_min_kg": 0.2,             # 皮重合法范围(工件+容器自重), 型号级可覆盖
        "tare_max_kg": 10.0,            #   (models[型号]["_tare_range"]={"min":..,"max":..})
        "queue_depth": 2,               # 待收尾队列深度保护(超出=节拍异常报警)
    },

    # v3.39 秤指令时序参数 (方案 3.1 节, 全部现场可调; 仅 pipeline 模式消费)
    "timing": {
        # 去皮触发源: weight_first=重量为主(标签①仅缩短稳定窗, 推荐)
        #             dual_confirm=严格双确认(标签①+重量都满足) / weight_only=忽略视觉
        "tare_trigger_source": "weight_first",
        "tare_delay_ms": 0,             # 触发条件满足后延迟多久发 T (0=立即)
        "tare_stable_ms": 1000,         # 皮重稳定窗口
        "tare_stable_tol_kg": 0.005,    # 皮重稳定公差
        "net_stable_ms": 1500,          # 净重稳定窗口
        "net_stable_tol_kg": 0.005,     # 净重稳定公差
        "shortage_alarm_sec": 3.0,      # 稳定低于下限持续多久报缺料(防瞬时误报)
        "depart_confirm_ms": 500,       # 骤降后空秤水平稳定多久算"确认离秤"(记账点)
        "zero_delay_ms": 0,             # 确认离秤记账后延迟多久发 Z (0=紧随记账)
        "zero_verify_ms": 2000,         # 发 Z 后多久内读数应归零
        "zero_retry": 1,                # 不归零自动重发次数
        "label1_min_frames": 3,         # 标签①连续帧确认
        "label1_fresh_sec": 3.0,        # 标签①"新鲜期"(去皮加速/双确认窗口)
        "label3_min_frames": 5,         # 标签③连续帧确认
        "label3_cooldown_sec": 2.0,     # 距队头件离秤结算多久内不受理收尾(防误归属)
        "fill_timeout_sec": 300,        # 装料阶段最长时长
        "finalize_timeout_sec": 120,    # 待收尾最长时长(超时按"收尾未确认"结案+报警)
    },

    # 完成动作
    "auto_zero_after_done": True,  # 一件全部料别完成后自动置零, 准备下一件

    # 报警事件映射 → 主程序 events_config[*].id (复用主程序事件链: 计数器+三色灯+Toast+语音)
    "alarm_event_shortage": 2,     # 缺料 (< 标准量下限)  (验收 6.2 核心)
    "alarm_event_over": 2,         # 超量 (> 标准量上限)
    "alarm_event_wrong": 2,        # 投错品类 (视觉料别与应投不符)  (验收 6.3)
    "alarm_event_precheck": 2,     # 未选人员/型号  (验收 6.5)

    # v3.39 pipeline 模式补充映射
    "alarm_event_ok": 1,           # 离秤结算合格 → OK 事件 (绿灯/计数)
    "alarm_event_tare_range": 2,   # 上秤自重超皮重范围
    "alarm_event_residue": 2,      # 清零后秤面残留
    "alarm_event_takt": 2,         # 节拍异常 (装料超时/队列超深)
    "alarm_event_finalize_timeout": 2,  # 超时未见收尾动作
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
        # v3.35.1 皮重: 去皮指令发出那一刻的毛重 = 工件/容器自重 (看板显示用)
        self.tare_weight = None

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
        self.tare_weight = None
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
            if self._buf:
                self.tare_weight = round(float(self._buf[-1][1]), 4)
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
                self.tare_weight = round(float(weight), 4)   # 去皮前毛重 = 工件/容器自重
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
                    "tare": self.tare_weight,   # 皮重 = 去皮那一刻的工件/容器自重 (v3.35.1 外推用)
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
            # v3.35.1 看板数值: 皮重(去皮时的工件/容器自重); 去皮后秤已归零,
            # live_weight 本身就是净重(已投料量)
            "tare_weight": self.tare_weight,
        }


def _verdict_reason(verdict, material, net, standard):
    if standard is None:
        return f"{material} 型号未配标准量"
    if verdict == "shortage":
        return f"{material} 缺料: 实投 {net}kg < 标准 {standard}kg"
    if verdict == "over":
        return f"{material} 超量: 实投 {net}kg > 标准 {standard}kg"
    return f"{material} {verdict}"


# ==================== 两阶段流水线状态机 (v3.39, 纯逻辑可单测) ====================
class PipelineStation(WeighingStation):
    """萍乡百斯特两阶段流水线状态机 (drive_mode='pipeline')。

    产线事实: 工件坐秤装料的同时, 上一件在压机侧被"加钢脚水泥"收尾 —— 同时最多两件在制。
    据此拆两阶段:

    阶段一 (秤上, 本状态机相位):
        empty     空秤 (含清零调度/验证/快手兜底)
        filling   已去皮, 装料中 (兼容秤上装料/拿下装料两种习惯; 读数=净重)
        departing 读数骤降, 等空秤稳定"确认离秤"
        确认离秤那一刻 = **重量事实冻结 + OK/NG 结算** (record + product_settled),
        随后按 zero_delay 发 Z 并验证归零。

    阶段二 (秤下): 结算件进 pending 待收尾队列 (FIFO), 视觉标签"加钢脚水泥"到达时
    结案队头件 (finalize → 推 MES); 超时未见收尾 → 按"收尾未确认"结案+报警。

    设计要点:
    - 秤指令**严格重量驱动**: 视觉标签①只作加速/双确认佐证, 模型漏检不影响称重主链路。
    - 全部时序参数来自 cfg["timing"] (方案 3.1 节 15 项, 现场可调)。
    - 零点基线 self._baseline: 离秤未清零期间空秤显示 = -上一件皮重, 有效读数
      eff = 读数 - 基线。快手 (新件在 Z 发出前上秤) 时跳过 Z, 皮重按差值折算,
      T 指令重新归零 —— 与方案 5.5 节"快手兜底"一致。
    - 与父类同款"只产事件列表"范式, 副作用由引擎执行层翻译。
    """

    def __init__(self, channel_id: int, weight_device_id=None, name: str = ""):
        super().__init__(channel_id, weight_device_id, name)
        self.phase = "empty"
        self._buf = deque(maxlen=96)   # 时间窗稳定判定需要更深的缓冲
        self._baseline = 0.0
        self._piece_seq = 0
        # 当前件 (阶段一)
        self._fill_started_at = None
        self._last_stable_net = None
        self._offscale = False
        self._depart_since = 0.0
        # 去皮时序
        self._tare_due_at = None
        self._label1_at = 0.0
        self._label1_streak = 0
        self._label3_streak = 0
        # 清零时序
        self._zero_due_at = None
        self._zero_sent_at = None
        self._zero_tries = 0
        # 判定节流 (值不变不重复报)
        self._over_alarmed_net = None
        self._shortage_since = None
        self._shortage_alarmed_net = None
        self._fill_timeout_alarmed = False
        self._alarm_at = {}
        # 阶段二: 待收尾队列
        self.pending = []
        self._last_settle_ts = 0.0
        self.settled_count = 0

    # ---------- 配置便捷取值 ----------
    @staticmethod
    def _t(cfg):
        return cfg.get("timing") or {}

    @staticmethod
    def _p(cfg):
        return cfg.get("pipeline") or {}

    def _tare_range(self, cfg):
        """皮重合法范围: 型号级 _tare_range 覆盖 > pipeline 全局默认。"""
        p = self._p(cfg)
        m = (cfg.get("models") or {}).get(self.model_name) or {}
        tr = m.get("_tare_range") if isinstance(m, dict) else None
        tr = tr if isinstance(tr, dict) else {}
        lo = float(tr.get("min", p.get("tare_min_kg", 0.2)))
        hi = float(tr.get("max", p.get("tare_max_kg", 10.0)))
        return lo, hi

    def _stable_for(self, now, window_ms, tol) -> bool:
        """时间窗稳定: 缓冲覆盖整窗且窗内读数波动 ≤ tol。"""
        span = max(0.05, float(window_ms) / 1000.0)
        if not self._buf or self._buf[0][0] > now - span:
            return False   # 数据尚未覆盖整个窗口
        ws = [w for t, w in self._buf if t >= now - span]
        if len(ws) < 2:
            return False
        return (max(ws) - min(ws)) <= float(tol)

    def _alarm(self, kind, reason, now, throttle=5.0, remind=False):
        """remind=True = "过程提醒"档: 借灯/语音/Toast 催工人, 不计数不定格
        (NG 计数只在离秤结算记一次, 2026-07 萍乡 NG 刷屏整改)。"""
        if throttle > 0:
            last = self._alarm_at.get(kind, 0.0)
            if now - last < throttle:
                return []
            self._alarm_at[kind] = now
        return [{"action": "alarm", "kind": kind, "remind": bool(remind),
                 "channel_id": self.channel_id, "reason": reason}]

    # ---------- 视觉标签喂入 (引擎 feed_detections 每帧调) ----------
    def feed_labels(self, hit_onscale: bool, hit_finalize: bool, ts, cfg):
        """喂一帧"标签①命中/标签③命中", 连续帧确认后生效。"""
        events = []
        t = self._t(cfg)
        self._label1_streak = self._label1_streak + 1 if hit_onscale else 0
        if self._label1_streak >= max(1, int(t.get("label1_min_frames", 3))):
            self._label1_at = ts   # 持续刷新: 新鲜期内可加速去皮 / 满足双确认
        self._label3_streak = self._label3_streak + 1 if hit_finalize else 0
        if self._label3_streak == 0:
            self._label3_fired = False
        elif (self._label3_streak >= max(1, int(t.get("label3_min_frames", 5)))
                and not getattr(self, "_label3_fired", False)):
            fired = self._on_finalize_label(ts, cfg)
            if fired:
                # 一次"出现"只结案一次; 被冷却期挡住时持续重试到冷却结束
                self._label3_fired = True
                events += fired
        return events

    def _on_finalize_label(self, ts, cfg):
        """标签③(加钢脚水泥) 确认 → FIFO 结案队头件。冷却期防误归属。"""
        if not self.pending:
            return []
        cd = float(self._t(cfg).get("label3_cooldown_sec", 2.0))
        if ts - self.pending[0].get("enqueued_at", 0.0) < cd:
            return []   # 队头刚离秤, 此刻的收尾动作属于更早(已结案)的件
        entry = self.pending.pop(0)
        return [{"action": "finalize", "entry": entry, "status": "label",
                 "channel_id": self.channel_id}]

    # ---------- 手动去皮 (界面按钮, 引擎已发 T) ----------
    def manual_tare_done(self):
        if self.phase != "empty":
            return False
        eff = (self._buf[-1][1] - self._baseline) if self._buf else 0.0
        self._begin_fill(eff, time.time(), events=None)
        return True

    def reset(self):
        """复位当前件 (保留待收尾队列 —— 队里是已冻结的事实, 不随复位丢)。"""
        pending = self.pending
        settled = self.settled_count
        seq = self._piece_seq
        super().reset()
        self.phase = "empty"
        self._baseline = 0.0
        self._tare_due_at = None
        self._zero_due_at = None
        self._zero_sent_at = None
        self._zero_tries = 0
        self._last_stable_net = None
        self._offscale = False
        self._fill_started_at = None
        self.pending = pending
        self.settled_count = settled
        self._piece_seq = seq

    # ---------- 核心: 喂一帧读数 ----------
    def on_weight(self, weight, ts, cfg):
        events = []
        w = float(weight)
        self._push_weight(ts, w)
        t = self._t(cfg)
        p = self._p(cfg)
        events += self._check_finalize_timeout(ts, cfg)
        if self.phase == "empty":
            events += self._empty_tick(w, ts, cfg, t, p)
        elif self.phase == "filling":
            events += self._filling_tick(w, ts, cfg, t, p)
        elif self.phase == "departing":
            events += self._departing_tick(w, ts, cfg, t, p)
        return events

    # ---------- empty: 清零调度/验证 + 去皮触发 ----------
    def _empty_tick(self, w, ts, cfg, t, p):
        events = []
        lo, hi = self._tare_range(cfg)

        # 快手兜底: Z **尚未发出**而新件已上秤 → 取消清零调度 (此刻发 Z 会把件重清掉;
        # 皮重按差值折算, T 指令会重新归零)。已发出的 Z 无法撤回, 走归零验证自纠。
        if self._zero_due_at is not None and (w - self._baseline) >= lo * 0.5:
            self._zero_due_at = None

        # 清零调度/验证先行: 归零确认会把零点基线归位, 必须在新件判定之前
        events += self._zero_tick(w, ts, t, cfg)
        eff = w - self._baseline

        if lo <= eff <= hi:
            src = t.get("tare_trigger_source", "weight_first")
            window = float(t.get("tare_stable_ms", 1000))
            fresh = (ts - self._label1_at) <= float(t.get("label1_fresh_sec", 3.0))
            if src == "weight_first" and fresh:
                window = window / 2.0   # 视觉佐证到位 → 缩短稳定等待
            if src == "dual_confirm" and not fresh:
                return events           # 严格双确认: 没标签不发 T
            if not self._stable_for(ts, window, t.get("tare_stable_tol_kg", 0.005)):
                return events
            # 前置校验: 型号必选 (否决去皮+持续报警); 人员缺失只兜底报警不拦 (登录把门)
            if cfg.get("require_model", True) and not self.model_name:
                events += self._alarm("precheck", "未选择产品型号, 已暂停自动去皮", ts)
                return events
            if cfg.get("require_operator", True) and not self.operator:
                events += self._alarm("precheck", "匿名作业: 未登录/未选择操作人员", ts)
            delay = float(t.get("tare_delay_ms", 0)) / 1000.0
            if delay > 0:
                if self._tare_due_at is None:
                    self._tare_due_at = ts + delay
                if ts < self._tare_due_at:
                    return events
            self._tare_due_at = None
            self._begin_fill(eff, ts, events)
        else:
            self._tare_due_at = None
            if eff > hi:
                events += self._alarm(
                    "tare_range",
                    f"上秤自重 {eff:.3f}kg 超出皮重范围 [{lo}, {hi}]kg, 疑似放错物品", ts)
        return events

    def _begin_fill(self, eff, ts, events):
        """发 T 去皮 → 进装料相位。events=None 表示手动去皮 (T 已由引擎发过)。"""
        self.tare_weight = round(float(eff), 4)
        self._piece_seq += 1
        self.product_sn = "W{}-{}-{}".format(
            _dt.datetime.fromtimestamp(ts).strftime("%m%d"),
            self.channel_id, self._piece_seq)
        if events is not None:
            events.append({"action": "send_tare", "device_id": self.weight_device_id,
                           "channel_id": self.channel_id})
        self.phase = "filling"
        self._baseline = 0.0   # T 之后秤显示归零
        self._zero_due_at = None
        self._zero_sent_at = None
        self._zero_tries = 0
        self._fill_started_at = ts
        self._last_stable_net = None
        self._offscale = False
        self._over_alarmed_net = None
        self._shortage_since = None
        self._shortage_alarmed_net = None
        self._fill_timeout_alarmed = False
        self._buf.clear()

    def _clear_command_event(self, w, t):
        """结算后清秤指令的智能选择 (2026-07 萍乡百斯特现场缺陷修复)。

        台秤的置零(Z)有硬件量程限制(通常仅零点附近 ±4% 量程有效): 离秤后秤面
        显示 -皮重-净重 (如 -5kg), 此时发 Z 会被秤**静默拒绝**, 秤面卡在负值。
        去皮(T)则任何读数下都有效(把当前毛重设为显示零点)。
        故读数在零点附近才用 Z (顺带校正真零漂移), 偏离远直接用 T 保证必归零。
        阈值可配 timing.zero_cmd_range_kg, 默认 0.5kg。
        """
        zero_range = float(t.get("zero_cmd_range_kg", 0.5))
        action = "send_zero" if abs(w) <= zero_range else "send_tare"
        return {"action": action, "device_id": self.weight_device_id,
                "channel_id": self.channel_id}

    def _zero_tick(self, w, ts, t, cfg=None):
        """清零调度 (延迟可配) + 归零验证 + 自动重发 + 残留报警。"""
        events = []
        if self._zero_due_at is not None and ts >= self._zero_due_at:
            self._zero_due_at = None
            self._zero_sent_at = ts
            self._zero_tries = 0
            events.append(self._clear_command_event(w, t))
        elif self._zero_sent_at is not None:
            eps = max(0.01, float(t.get("tare_stable_tol_kg", 0.005)) * 2)
            if abs(w) <= eps:
                self._zero_sent_at = None
                self._zero_tries = 0
                self._baseline = 0.0   # 归零确认
            elif w > eps:
                # 正读数的两种成因, 必须区分 (2026-07 萍乡百斯特现场缺陷):
                #   A. 清零已执行且新件已上秤 (快手竞态) → 零点=0, 件自重=w
                #   B. 清零被秤拒绝(读数原本为负)且新件已上秤 → 零点=baseline,
                #      件自重 = w - baseline (如 -4kg 卡住 + 4.5kg 件 = +0.5)
                # 旧实现盲判 A: B 场景下件自重被吞成 0.5, 低于皮重下限不去皮,
                # 等水泥加到 ~1.2kg 落进皮重区间才误去皮把已加的水泥吞掉。
                # 用皮重合法区间做合理性核对: 哪种解释算出的自重合法就信哪种。
                self._zero_sent_at = None
                self._zero_tries = 0
                if cfg is not None:
                    lo, hi = self._tare_range(cfg)
                    a_ok = lo <= w <= hi
                    b_ok = lo <= (w - self._baseline) <= hi
                    if b_ok and not a_ok:
                        pass               # 信 B: 保留零点基线, 去皮判定按差值折算
                    else:
                        self._baseline = 0.0   # 信 A (含两可/两不可: 维持旧行为)
                else:
                    self._baseline = 0.0
            elif ts - self._zero_sent_at >= float(t.get("zero_verify_ms", 2000)) / 1000.0:
                # 重发前守门: 新件疑似已上秤 (读数较零点基线明显抬升) 时绝不能再发
                # 清秤指令 —— 此刻 T 会把件重吞进秤内皮重寄存器, 件从此"隐形"。
                # 放弃清秤, 保留基线折算, 让去皮判定接管。
                piece_maybe_on = False
                if cfg is not None:
                    lo, _hi = self._tare_range(cfg)
                    piece_maybe_on = (w - self._baseline) >= lo * 0.5
                if piece_maybe_on:
                    self._zero_sent_at = None
                    self._zero_tries = 0
                elif self._zero_tries < int(t.get("zero_retry", 1)):
                    self._zero_tries += 1
                    self._zero_sent_at = ts
                    # 重发升级: 首发若是 Z 且没归零, 大概率是量程外被拒 → 按当前
                    # 读数重选指令 (偏离零点远则改发 T)
                    events.append(self._clear_command_event(w, t))
                else:
                    events += self._alarm(
                        "residue", f"清零后读数 {w:.3f}kg 未归零, 秤面疑有残留", ts)
                    self._baseline = w   # 接受漂移为新零点, 不卡生产
                    self._zero_sent_at = None
        return events

    # ---------- filling: 两种习惯兼容 + 净重稳定判定 + 离秤识别 ----------
    def _filling_tick(self, w, ts, cfg, t, p):
        events = []
        material = p.get("material") or (cfg.get("materials") or ["料"])[0]
        spec = get_model_spec(cfg, self.model_name, material)
        tare = self.tare_weight or 0.0

        # 读数跌到 -皮重 附近 = 件不在秤上
        if tare > 0 and w <= -tare * 0.5:
            if self._last_stable_net is not None:
                # 已有净重事实 → 疑似离秤, 进确认窗
                self.phase = "departing"
                self._depart_since = ts
            else:
                self._offscale = True   # 拿下装料习惯 (还没有净重, 不是离秤)
            return events
        self._offscale = False

        timeout = float(t.get("fill_timeout_sec", 300))
        if (not self._fill_timeout_alarmed and self._fill_started_at
                and ts - self._fill_started_at >= timeout):
            self._fill_timeout_alarmed = True
            events += self._alarm(
                "takt", f"装料超时: 本件已装料 {int(ts - self._fill_started_at)}s 未完成", ts)

        min_w = float(cfg.get("measure_min_weight", 0.005))
        if w >= min_w and self._stable_for(
                ts, t.get("net_stable_ms", 1500), t.get("net_stable_tol_kg", 0.005)):
            net = round(w, 4)
            self._last_stable_net = net
            if spec is not None:
                verdict = judge_amount(net, spec)
                std = float(spec.get("standard", 0))
                # 投料中缺料/超量 = "过程提醒"档: 周期性催工人 (默认每 10s 一次,
                # remind_repeat_sec 可配), 只借灯/语音/Toast, 不计 NG 不定格 ——
                # NG 计数由该件离秤结算记一次。(2026-07 萍乡现场: 秤读数 ±3g 微抖
                # 让旧"值变才重报"门形同虚设, 提醒借完整 NG 事件面计数,
                # 30 秒把 NG 计数刷了 +16)
                remind_sec = float(t.get("remind_repeat_sec", 10.0))
                if verdict == "over":
                    self._over_alarmed_net = net
                    events += self._alarm(
                        "over", _verdict_reason("over", material, net, std), ts,
                        throttle=remind_sec, remind=True)
                elif verdict == "shortage":
                    if self._shortage_since is None:
                        self._shortage_since = ts
                    if ts - self._shortage_since >= float(t.get("shortage_alarm_sec", 3.0)):
                        self._shortage_alarmed_net = net
                        events += self._alarm(
                            "shortage", _verdict_reason("shortage", material, net, std),
                            ts, throttle=remind_sec, remind=True)
                else:
                    self._shortage_since = None
        elif w < min_w:
            self._shortage_since = None
        return events

    # ---------- departing: 空秤稳定确认 → 冻结结算 ----------
    def _departing_tick(self, w, ts, cfg, t, p):
        tare = self.tare_weight or 0.0
        if w > -tare * 0.5:
            self.phase = "filling"   # 虚晃 (抖动/又放回) → 继续装料
            return []
        confirm_ms = float(t.get("depart_confirm_ms", 500))
        if (ts - self._depart_since >= confirm_ms / 1000.0
                and self._stable_for(ts, confirm_ms,
                                     float(t.get("tare_stable_tol_kg", 0.005)) * 2)):
            return self._settle(w, ts, cfg, t, p)
        return []

    def _settle(self, w, ts, cfg, t, p):
        """确认离秤 = 冻结重量事实 + OK/NG 结算 + 入待收尾队列 + 调度清零。"""
        events = []
        material = p.get("material") or (cfg.get("materials") or ["料"])[0]
        spec = get_model_spec(cfg, self.model_name, material)
        net = self._last_stable_net
        if spec is None:
            verdict, standard = "no_spec", None
        else:
            verdict = judge_amount(net, spec)
            standard = float(spec.get("standard", 0))
        result = {
            "material": material, "standard": standard, "initial": 0.0,
            "net": net, "tare": self.tare_weight, "verdict": verdict, "ts": ts,
            "sn": self.product_sn, "model": self.model_name,
            "operator": self.operator, "channel_id": self.channel_id,
        }
        self.results.append(result)
        if len(self.results) > 50:
            self.results = self.results[-50:]
        events.append({"action": "record", "result": result})
        events.append({"action": "product_settled", "result": result})
        if verdict in ("shortage", "over"):
            events.append({"action": "alarm", "kind": verdict,
                           "channel_id": self.channel_id,
                           "reason": "离秤结算: " + _verdict_reason(verdict, material, net, standard),
                           "result": result})
        # 阶段二: 入待收尾队列 (FIFO, 等标签③结案)
        entry = dict(result)
        entry["enqueued_at"] = ts
        self.pending.append(entry)
        depth = max(1, int(p.get("queue_depth", 2)))
        if len(self.pending) > depth:
            events += self._alarm(
                "takt", f"待收尾队列 {len(self.pending)} 件超过深度 {depth}, 节拍异常", ts)
        # 清零调度: 空秤显示 = -皮重, 先作 Z 前零点基线 (快手兜底靠它折算)
        self._baseline = w
        self._zero_due_at = ts + float(t.get("zero_delay_ms", 0)) / 1000.0
        self._zero_sent_at = None
        self._zero_tries = 0
        self._last_settle_ts = ts
        self.settled_count += 1
        # 复位单件状态, 回 empty 迎下一件
        self.phase = "empty"
        self.tare_weight = None
        self.product_sn = None
        self._last_stable_net = None
        self._buf.clear()
        return events

    def _check_finalize_timeout(self, ts, cfg):
        events = []
        timeout = float(self._t(cfg).get("finalize_timeout_sec", 120))
        while self.pending and ts - self.pending[0].get("enqueued_at", ts) >= timeout:
            entry = self.pending.pop(0)
            events.append({"action": "finalize", "entry": entry, "status": "timeout",
                           "channel_id": self.channel_id})
            events += self._alarm(
                "finalize_timeout",
                f"件 {entry.get('sn')} 超 {int(timeout)}s 未见收尾动作, 按「收尾未确认」结案",
                ts, throttle=0)
        return events

    # ---------- 快照 ----------
    def snapshot(self):
        snap = super().snapshot()
        snap.update({
            "drive_mode": "pipeline",
            "pipeline_phase": self.phase,
            "effective_weight": round((self._buf[-1][1] - self._baseline), 4) if self._buf else None,
            "last_stable_net": self._last_stable_net,
            "offscale": self._offscale,
            "pending": [{"sn": e.get("sn"), "net": e.get("net"),
                         "verdict": e.get("verdict"),
                         "enqueued_at": e.get("enqueued_at")} for e in self.pending],
            "settled_count": self.settled_count,
        })
        return snap


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
                station.tare_weight = round(float(weight), 4)  # 皮重 = 工件/容器自重
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
                "tare": station.tare_weight,   # 皮重 (v3.35.1 外推用)
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
        # 网关推送后台线程 (2026-07 萍乡百斯特现行日志抓到: 达梦不可达时
        # dispatch 同步重试 3×5s, 把推理线程卡死 15s → 画面冻结 + 收尾步骤
        # 耗时被灌成 15s 误判 NG。检测/称重热路径绝不等网络 — 不变量 15,
        # 推送一律出让到本线程)。队列有界, 满则挤掉最旧 (网关自有通讯日志兜底)
        self._push_q = deque(maxlen=256)
        self._push_evt = threading.Event()
        self._push_worker = None

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
            station_cls = (PipelineStation
                           if cfg.get("drive_mode") == "pipeline" else WeighingStation)
            st = station_cls(channel_id,
                             weight_device_id=cfg.get("weight_device_id"),
                             name=cfg.get("station_name", ""))
            if old is not None:
                st.operator = old.operator
                st.model_name = old.model_name
                st.ctx_set_at = dict(old.ctx_set_at)
                # pipeline: 待收尾队列是已冻结的事实, 配置重载不能丢
                if isinstance(st, PipelineStation) and isinstance(old, PipelineStation):
                    st.pending = list(old.pending)
                    st.settled_count = old.settled_count
                    st._piece_seq = old._piece_seq
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
                # 新一轮去皮门控武装 = 新工件上秤, 清上一件皮重避免看板显示残值
                if (gate_cfg or {}).get("kind", "tare") == "tare":
                    st2 = self._stations.get(channel_id)
                    if st2 is not None:
                        st2.tare_weight = None
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
        """喂一帧 YOLO 检测结果。

        - 视觉守卫 (visual_guard): 料别映射 → set_material_label; 限区违规 → 报警。
        - v3.39 pipeline 模式: 提取标签①(工件上秤, 可配 Z1 有效域过滤)/标签③(加钢脚水泥)
          的逐帧命中喂状态机 (连续帧确认 + 去皮加速/双确认 + FIFO 结案)。
        """
        guard = self._guards.get(channel_id)
        st = self._stations.get(channel_id)
        is_pipeline = isinstance(st, PipelineStation)
        if guard is None and not is_pipeline:
            return
        try:
            pipe_events = []
            with self._lock:
                events = guard.feed(detections) if guard is not None else []
                if is_pipeline:
                    cfg = self._configs.get(channel_id) or {}
                    p = cfg.get("pipeline") or {}
                    lbl_on = p.get("label_onscale")
                    lbl_fin = p.get("label_finalize")
                    poly = p.get("onscale_polygon")
                    hit_on = hit_fin = False
                    for det in detections or []:
                        label = det.get("label")
                        if lbl_fin and label == lbl_fin:
                            hit_fin = True
                        if lbl_on and label == lbl_on:
                            if poly:
                                c = VisualGuardMatcher._det_center(det)
                                if c is None or not point_in_polygon(c[0], c[1], poly):
                                    continue
                            hit_on = True
                    pipe_events = st.feed_labels(hit_on, hit_fin, time.time(), cfg)
            for ev in events:
                if ev.get("action") == "material_label" and st is not None:
                    st.set_material_label(ev.get("material"))
                    logger.info("[Weighing] ch%s 视觉守卫命中规则[%s] → 料别=%s",
                                channel_id, ev.get("rule"), ev.get("material"))
                elif ev.get("action") == "alarm":
                    ev.setdefault("channel_id", channel_id)
                    self._execute(channel_id, [ev])
            if pipe_events:
                self._execute(channel_id, pipe_events)
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
        # v3.45 作业员从用户名单选择 (可选, 默认关=自由填写)
        snap["operator_from_users"] = bool(cfg.get("operator_from_users"))
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
                elif action == "product_settled":
                    self._do_product_settled(cfg, channel_id, ev.get("result"))
                elif action == "finalize":
                    self._do_finalize(channel_id, ev.get("entry"), ev.get("status"))
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
            # v3.39 pipeline 模式
            "tare_range": cfg.get("alarm_event_tare_range", 2),
            "residue": cfg.get("alarm_event_residue", 2),
            "takt": cfg.get("alarm_event_takt", 2),
            "finalize_timeout": cfg.get("alarm_event_finalize_timeout", 2),
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
        # v3.45 "过程提醒"档: 投料中缺料/超量的周期性催料只借灯/语音/Toast,
        # 不计数不定格 (NG 计数由离秤结算记一次)
        remind_only = bool(ev.get("remind"))
        fired = False
        try:
            from backend.api.channel_manager import get_channel_manager
            mgr = get_channel_manager().get(channel_id)
            if hasattr(mgr, "fire_external_event_response"):
                try:
                    fired = bool(mgr.fire_external_event_response(
                        event_id, reason, source="weighing",
                        remind_only=remind_only))
                except TypeError:
                    # 旧版主程序无 remind_only 形参 (热补丁混装场景) → 老签名兜底
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
        # v3.39 人员归属: 未显式选人时自动归属当前登录用户 (账号鉴权体系把门)
        if not result.get("operator"):
            u = _current_login_username()
            if u:
                result["operator"] = u
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

    # ---------- v3.39 pipeline 模式副作用 ----------
    def _do_product_settled(self, cfg, channel_id, result):
        """确认离秤结算 (方案 T2): 合格件借主程序 OK 事件响应面 (绿灯/语音/计数)。

        NG (缺料/超量) 的报警在 _settle 已单独产 alarm 事件, 这里只管合格反馈。
        """
        if not result:
            return
        logger.info("[Weighing] ch%s 离秤结算: sn=%s 皮重=%s 净重=%s 判定=%s (待收尾)",
                    channel_id, result.get("sn"), result.get("tare"),
                    result.get("net"), result.get("verdict"))
        if result.get("verdict") != "ok":
            return
        event_id = cfg.get("alarm_event_ok", 1)
        try:
            from backend.api.channel_manager import get_channel_manager
            mgr = get_channel_manager().get(channel_id)
            if hasattr(mgr, "fire_external_event_response"):
                mgr.fire_external_event_response(
                    event_id, f"称重合格: 净重 {result.get('net')}kg", source="weighing")
        except Exception as e:
            logger.debug("[Weighing] 合格事件联动失败(隔离): %s", e)

    # ---------- 网关推送后台线程 (热路径绝不等网络) ----------
    def _gateway_push_async(self, event_type, payload, channel_id):
        """把网关推送入队即返回, 由后台守护线程串行投递。

        调用方是推理线程 (feed_detections→finalize) 或外设数据线程 (feed_weight),
        网关 dispatch 内含 HTTP/数据库直连的超时与重试 (最坏 15s+), 绝不允许在
        这两条热路径上同步等待 (2026-07 萍乡百斯特现场日志抓到 15s 画面冻结现行)。
        """
        with self._lock:
            if self._push_worker is None or not self._push_worker.is_alive():
                self._push_worker = threading.Thread(
                    target=self._push_loop, name="weighing-gateway-push", daemon=True)
                self._push_worker.start()
        if len(self._push_q) == self._push_q.maxlen:
            logger.warning("[Weighing] 网关推送队列已满, 挤掉最旧一条 (外部系统长时间不可达?)")
        self._push_q.append((event_type, payload, channel_id))
        self._push_evt.set()

    def _push_loop(self):
        while True:
            self._push_evt.wait(timeout=5.0)
            self._push_evt.clear()
            while self._push_q:
                event_type, payload, channel_id = self._push_q.popleft()
                try:
                    from backend.services.mes_gateway import get_mes_gateway
                    get_mes_gateway().dispatch(event_type, payload,
                                               channel_id=channel_id)
                except Exception as e:
                    logger.warning("[Weighing] 后台网关推送失败(隔离): %s", e)

    def _do_finalize(self, channel_id, entry, status):
        """正式结案 (方案 T3): 收尾标签到达 (status=label) 或超时 (status=timeout)。

        此刻整件事实闭合 → 推 MES/达梦 (经后台推送线程, 不阻塞调用方)。
        记录本体在 T2 已落库, 这里补推送。
        """
        if not entry:
            return
        logger.info("[Weighing] ch%s 结案(%s): sn=%s 净重=%s 判定=%s",
                    channel_id, status, entry.get("sn"), entry.get("net"),
                    entry.get("verdict"))
        shift = None
        try:
            from backend.api.channel_manager import get_channel_manager
            vsm = get_channel_manager().get(channel_id or 0)
            if vsm is not None:
                shift = vsm._get_current_shift()
        except Exception:
            pass
        payload = {k: v for k, v in entry.items() if k != "enqueued_at"}
        payload["finalize_status"] = "confirmed" if status == "label" else "unconfirmed"
        payload["shift"] = shift
        self._gateway_push_async("weighing_product_done", payload, channel_id or 0)

    def _do_product_done(self, ev):
        """一件全部料别完成: 上传 (6.1)。后续阶段完善 MES payload/数据页。"""
        logger.info("[Weighing] 产品完成 ch%s: sn=%s 型号=%s 结果=%s",
                    ev.get("channel_id"), ev.get("sn"), ev.get("model"),
                    [(r.get("material"), r.get("verdict")) for r in ev.get("results", [])])
        # 当前班次 (v3.35.1): 从该通道 VSM 取, 拿不到不阻断推送
        shift = None
        try:
            from backend.api.channel_manager import get_channel_manager
            vsm = get_channel_manager().get(ev.get("channel_id") or 0)
            if vsm is not None:
                shift = vsm._get_current_shift()
        except Exception:
            pass
        self._gateway_push_async("weighing_product_done", {
            "sn": ev.get("sn"),
            "model": ev.get("model"),
            "operator": ev.get("operator"),
            "channel_id": ev.get("channel_id"),
            "shift": shift,
            "results": ev.get("results", []),
        }, ev.get("channel_id") or 0)


def _current_login_username():
    """当前登录用户名 (账号鉴权体系落盘的活跃用户); 未登录/鉴权关闭返回 None。"""
    try:
        from backend.core.auth import get_current_user_id
        uid = get_current_user_id()
        if uid is None:
            return None
        from backend.db.database import SessionLocal
        from backend.models.auth_models import User
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.id == uid).first()
            return u.username if u else None
        finally:
            db.close()
    except Exception:
        return None


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
