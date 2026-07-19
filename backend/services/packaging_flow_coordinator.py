"""PackagingFlowCoordinator — 包装箱结算协调器 (v3.21+, 上银包装线场景).

客户视角:
  扫码驱动的"工单 → 箱 → 托盘"三层结算:
    ① 扫工单标签 → 向 MES 拉工单 → 拿到应做箱数.
    ② 再扫同一个号 → 开始第 1 箱 (箱标签 = 工单号).
    ③ 视觉数托盘里的滑块数量, 达标计入当前箱.
    ④ 满 N 个托盘 = 该箱够了, 封箱.
    ⑤ 再扫同号 → 结算上一箱并开下一箱; 扫到不同号 → 视为新工单, 收尾旧工单.
  报警点: 标签错 / 托盘数量不对 / 漏箱 / 多箱.

设计选择 (与 ChannelGroupCoordinator / WorkpieceFlowCoordinator 同构):
  - 进程内单例, get_coordinator() 拿.
  - 内部状态 (_configs / _runs / 反向索引) 用 threading.Lock 保护.
  - 不开后台线程: 全部事件驱动 (扫码 / cycle 结算 调进来即返回).
  - 与其它两个协调器完全独立, 不依赖, 不共享状态.

外部依赖用可注入钩子隔离 (便于单测 + 分期):
  - _mes_fetcher(cfg_dict, order_no) -> dict | None : 拉工单 (M3 接真实 puller).
  - _alarm_sink(cfg_dict, kind, msg) -> None         : 报警 (M3 接真实 alarm_router).
  默认为 None 时仅 print, 不影响状态机.

零差异底线:
  - 没有任何 packaging_flow_configs 启用时, on_scan / on_cycle_settled 入口
    直接 return, 与不配置时字节级一致.
  - 通道不在任何启用配置时, on_cycle_settled 直接 return.
"""
from __future__ import annotations

import math
import re
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple


def compute_box_plan(slider_total: int, items_per_box: int) -> Tuple[int, int]:
    """滑块总数 + 每箱滑块数 → (应做箱数, 尾箱滑块目标).

    尾箱 = 最后一箱 (不论是否整除都标记最后一箱为尾箱):
      - 非整除 (如 250/96 → 3 箱): 尾箱目标 = 余数 (250 - 2*96 = 58).
      - 整除   (如 240/24 → 10 箱): 尾箱目标 = 每箱数 (24), 第 10 箱仍标记尾箱.
    入参非法 (<=0) → (0, 0), 由上层走"箱数未知"兜底.
    """
    st = int(slider_total or 0)
    per = int(items_per_box or 0)
    if st <= 0 or per <= 0:
        return 0, 0
    box_total = math.ceil(st / per)
    remainder = st - (box_total - 1) * per
    tail_target = remainder if remainder > 0 else per
    return box_total, tail_target


_singleton: Optional["PackagingFlowCoordinator"] = None
_singleton_lock = threading.Lock()


def _pkg_dbg(action: str, detail: str = "") -> None:
    """包装结算调试埋点 (类别 backend.packaging, 默认关零开销)."""
    try:
        from backend.core import debug_center
        if debug_center.is_on("backend.packaging"):
            debug_center.dbg("backend.packaging", action, detail)
    except Exception:
        pass


def get_coordinator() -> "PackagingFlowCoordinator":
    """获取单例 (惰性初始化, 进程内一份)."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = PackagingFlowCoordinator()
    return _singleton


def reset_coordinator_for_testing() -> None:
    """测试用: 重置单例, 防 fixture 互相污染."""
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            try:
                _singleton.cleanup_for_testing()
            except Exception:
                pass
        _singleton = None


_TERMINAL_STATES = {"completed", "aborted", "short"}


class PackagingFlowCoordinator:
    """包装箱结算协调器.

    内部数据:
      _configs:           config_id → 配置 dict (DB enabled 行)
      _channel_to_config: channel_id → config_id (反向索引)
      _runs:              config_id → 进行中运行态 dict (一个配置同时只追一张工单)
    """

    def __init__(self) -> None:
        self._configs: Dict[int, Dict[str, Any]] = {}
        self._channel_to_config: Dict[int, int] = {}
        self._runs: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        # 外部依赖钩子 (M3 注入真实实现)
        self._mes_fetcher: Optional[Callable[[Dict[str, Any], str], Optional[Dict[str, Any]]]] = None
        self._alarm_sink: Optional[Callable[[Dict[str, Any], str, str], None]] = None
        self._mes_pusher: Optional[Callable[[Dict[str, Any], Dict[str, Any]], None]] = None
        # v3.22 上银 MES 闭环钩子 (默认 None, sliders 模式才用; 单测可 mock)
        #   _box_target_setter(channel_id, target): 把"当前箱滑块目标"反向设回检测层容器累加器,
        #       让合格判定 (含尾箱余数) 始终正确. 不注入时不影响 trays 模式.
        #   _project_activator(spec, cfg) -> bool: 按规格自动激活对应项目, 成功返回 True.
        #   _paper_order_probe(channel_id, step_label) -> bool: 尾箱"放工单"步骤是否已 covered.
        self._box_target_setter: Optional[Callable[[int, int], None]] = None
        self._project_activator: Optional[Callable[[str, Dict[str, Any]], bool]] = None
        self._paper_order_probe: Optional[Callable[[int, Optional[str]], bool]] = None
        # v3.43.1 提前放工单判定用: 当前箱实时已进箱件数 (None=拿不到/不适用).
        # 上一箱一落账"当前箱"就翻到尾箱, 但物理上尾箱可能一件没装 — 靠它区分
        # "尾箱正做着(正常放工单)"和"尾箱还没开始(提前放工单)".
        self._box_progress_getter: Optional[Callable[[int], Optional[int]]] = None
        # v3.43 等放工单时限报警的一次性 Timer (config_id → Timer). 挂"等放工单收尾"
        # 时布防、收尾时撤防; 不是常驻后台线程, 不违背"全事件驱动"的设计基调.
        self._paper_timers: Dict[int, threading.Timer] = {}

    # =============================================================
    # 依赖注入 (M3 启动时 set 真实实现; 单测 set mock)
    # =============================================================

    def set_mes_fetcher(self, fn) -> None:
        self._mes_fetcher = fn

    def set_alarm_sink(self, fn) -> None:
        self._alarm_sink = fn

    def set_mes_pusher(self, fn) -> None:
        self._mes_pusher = fn

    def set_box_target_setter(self, fn) -> None:
        self._box_target_setter = fn

    def set_project_activator(self, fn) -> None:
        self._project_activator = fn

    def set_paper_order_probe(self, fn) -> None:
        self._paper_order_probe = fn

    def set_box_progress_getter(self, fn) -> None:
        self._box_progress_getter = fn

    # =============================================================
    # 配置加载 / 卸载
    # =============================================================

    def reload_configs(self, db) -> int:
        """重新加载 packaging_flow_configs 表 (启动 / 配置变更时调). 返回启用配置数."""
        from backend.models.mes_models import PackagingFlowConfig

        rows = db.query(PackagingFlowConfig).filter(
            PackagingFlowConfig.enabled.is_(True)
        ).all()
        with self._lock:
            self._configs.clear()
            self._channel_to_config.clear()
            for row in rows:
                cfg = self._row_to_dict(row)
                self._configs[row.id] = cfg
                ch = cfg.get("channel_id")
                if isinstance(ch, int):
                    self._channel_to_config[ch] = row.id
        return len(rows)

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "enabled": bool(row.enabled),
            "channel_id": int(row.channel_id or 0),
            "scan_device_id": row.scan_device_id,
            "pull_conn_id": row.pull_conn_id,
            "box_count_source": row.box_count_source or "field",
            "box_count_field": row.box_count_field or "dispatch_qty",
            "tray_qty_mode": row.tray_qty_mode or "fixed",
            "tray_qty_fixed": int(row.tray_qty_fixed or 0),
            "tray_qty_table": row.tray_qty_table or {},
            "trays_per_box_mode": row.trays_per_box_mode or "fixed",
            "trays_per_box_fixed": int(row.trays_per_box_fixed or 4),
            "trays_per_box_table": row.trays_per_box_table or {},
            "label_match": row.label_match or "strip_hyphen",
            "label_len": int(row.label_len or 0),
            "hyphen_template": row.hyphen_template,
            "hyphen_pos": int(getattr(row, "hyphen_pos", 0) or 0),
            # v3.30.1 复合条码取段 (默认关零差异)
            "composite_label_enabled": bool(getattr(row, "composite_label_enabled", False)),
            "composite_delimiter": getattr(row, "composite_delimiter", None) or "|",
            "composite_pick_mode": getattr(row, "composite_pick_mode", None) or "prefix",
            "composite_prefix": getattr(row, "composite_prefix", None) or "",
            "composite_index": int(getattr(row, "composite_index", 1) or 1),
            # v3.30.1 工单号识别规则 (默认空=不过滤)
            "order_code_pattern": getattr(row, "order_code_pattern", None) or "",
            "on_mes_fail": row.on_mes_fail or "block",
            "on_label_mismatch": row.on_label_mismatch or "warn",
            "on_short_box": row.on_short_box or "redo",
            "on_forced_stop_partial": row.on_forced_stop_partial or "fail",
            "on_forced_stop": row.on_forced_stop or "settle",
            "forced_settle_on_standby": (
                bool(row.forced_settle_on_standby)
                if row.forced_settle_on_standby is not None else True
            ),
            "push_on_complete": bool(row.push_on_complete),
            "push_event_type": row.push_event_type or "packaging_complete",
            # 组⑥ 异常 → 项目事件映射 (None=走默认通用报警)
            "event_short_box": row.event_short_box,
            "event_over_box": row.event_over_box,
            "event_tray_ng": row.event_tray_ng,
            "event_box_ng": row.event_box_ng,
            "event_label_mismatch": row.event_label_mismatch,
            "event_label_len": row.event_label_len,
            "event_mes_fail": row.event_mes_fail,
            # 组⑦ v3.22 滑块口径 + 尾箱 + 自动切项目 + 塞工单 gate (默认 trays 零差异)
            "count_unit": getattr(row, "count_unit", None) or "trays",
            "items_per_box_source": getattr(row, "items_per_box_source", None) or "project",
            "items_per_box_fixed": int(getattr(row, "items_per_box_fixed", 0) or 0),
            "slider_total_field": getattr(row, "slider_total_field", None) or "dispatch_qty",
            "auto_switch_project": bool(getattr(row, "auto_switch_project", False)),
            "spec_to_project": getattr(row, "spec_to_project", None) or {},
            "match_project_by_name": bool(getattr(row, "match_project_by_name", False)),
            "name_match_strict_boundary": bool(getattr(row, "name_match_strict_boundary", False)),
            "tail_paper_order_required": bool(getattr(row, "tail_paper_order_required", False)),
            "tail_paper_step_label": getattr(row, "tail_paper_step_label", None),
            # v3.34.1 放工单=尾箱收尾动作 (默认关=老行为); v3.43 语义: 箱归周期结算,
            # 放工单归工单收尾 (等放工单收尾 + 扫新单/时限两个可配报警出口)
            "tail_paper_as_close_action": bool(getattr(row, "tail_paper_as_close_action", False)),
            "event_missing_paper": getattr(row, "event_missing_paper", None),
            "tail_paper_scan_alarm": (
                bool(row.tail_paper_scan_alarm)
                if getattr(row, "tail_paper_scan_alarm", None) is not None else True
            ),
            "tail_paper_timeout_s": int(getattr(row, "tail_paper_timeout_s", 0) or 0),
            # v3.23 缺油嘴 gate (每箱查, 默认关零差异)
            "oil_nozzle_required": bool(getattr(row, "oil_nozzle_required", False)),
            "oil_nozzle_step_label": getattr(row, "oil_nozzle_step_label", None),
            "event_missing_nozzle": getattr(row, "event_missing_nozzle", None),
            # v3.42.1 已完成(OK)工单重扫拦截 (默认关零差异)
            "block_completed_order_rescan": bool(getattr(row, "block_completed_order_rescan", False)),
            "event_completed_order_rescan": getattr(row, "event_completed_order_rescan", None),
        }

    # =============================================================
    # 标签归一化 + 规格量解析
    # =============================================================

    @staticmethod
    def _order_code_ok(norm: str, cfg: Dict[str, Any]) -> bool:
        """工单号识别规则 (v3.30.1, 默认关): 归一化后的码须从头匹配配置的正则才放行.
        规则为空 = 不过滤 (存量零差异); 正则写错 = 视为不过滤并留调试痕迹, 不吞码."""
        pattern = str(cfg.get("order_code_pattern") or "").strip()
        if not pattern:
            return True
        try:
            ok = re.match(pattern, norm) is not None
        except re.error as e:
            _pkg_dbg("工单号识别规则异常", f"正则 {pattern!r} 无效 ({e}), 本次不过滤")
            return True
        if not ok:
            _pkg_dbg("工单号识别规则拒绝", f"norm={norm!r} 不匹配 {pattern!r}")
        return ok

    @staticmethod
    def _extract_composite(s: str, cfg: Dict[str, Any]) -> str:
        """复合条码取段 (v3.30.1, 默认关): 正式产线箱标签常是多段拼接码
        (如 ORD260300050-2|JOB260600268-13|54.00|<校验串>), 整串比对必不符.
        启用后按分隔符拆段, 按前缀/序号取出工单段, 再交给 _normalize 走原有归一化.
        无分隔符的码 (工单纸直扫) 原样返回 → 两种码可混扫; 取段失败也原样返回不吞码.
        """
        if not cfg.get("composite_label_enabled"):
            return s
        delim = cfg.get("composite_delimiter") or "|"
        if delim not in s:
            return s
        parts = [p.strip() for p in s.split(delim) if p.strip()]
        if not parts:
            return s
        mode = cfg.get("composite_pick_mode") or "prefix"
        if mode == "index":
            idx = int(cfg.get("composite_index") or 1)
            if 1 <= idx <= len(parts):
                out = parts[idx - 1]
                _pkg_dbg("复合条码取段 index", f"第{idx}段 → {out!r} (共{len(parts)}段)")
                return out
            _pkg_dbg("复合条码取段失败", f"index={idx} 超界(共{len(parts)}段), 原样返回")
            return s
        # prefix 模式: 取以指定前缀开头的段; 多段命中取最长 (最具体)
        prefix = str(cfg.get("composite_prefix") or "").strip()
        if prefix:
            hits = [p for p in parts if p.startswith(prefix)]
            if hits:
                out = max(hits, key=len)
                _pkg_dbg("复合条码取段 prefix", f"前缀={prefix!r} → {out!r} (共{len(parts)}段)")
                return out
        _pkg_dbg("复合条码取段失败", f"前缀={prefix!r} 无命中段, 原样返回")
        return s

    @staticmethod
    def _normalize(code: str, cfg: Dict[str, Any]) -> str:
        """按配置把扫码原始串归一化, 用于工单/箱标签比对. 复合码先取段再归一."""
        s = str(code or "").strip()
        s = PackagingFlowCoordinator._extract_composite(s, cfg)
        mode = cfg.get("label_match", "strip_hyphen")
        if mode == "insert_char":
            # 上银: 标签是 JOB150700114-1, 但扫码枪丢了 '-' 扫成 JOB1507001141 (序号仍在).
            # 把符号补回固定位置 → 还原成 JOB150700114-1, 之后记录/查 MES 永远用这个完整值.
            # 主单号定长(如 JOB+9位=12), 补在第 12 位之后, 序号 1~3 位都适配.
            # 先去掉已有同种符号再插, 保证扫到带符号的码也归一到同一结果(幂等).
            ch = cfg.get("hyphen_template") or "-"
            pos = int(cfg.get("hyphen_pos") or 0)
            base = s.replace(ch, "")
            if 0 < pos < len(base):
                out = base[:pos] + ch + base[pos:]
            else:
                out = base
            _pkg_dbg("条码正常化 insert_char", f"raw={s!r} pos={pos} ch={ch!r} → {out!r}")
            return out
        if mode == "strip_hyphen":
            return s.replace("-", "")
        if mode == "digits_only":
            return "".join(ch for ch in s if ch.isdigit())
        return s  # exact

    @staticmethod
    def _trays_per_box(cfg: Dict[str, Any], spec: Optional[str]) -> int:
        if cfg.get("trays_per_box_mode") == "by_spec" and spec:
            return int((cfg.get("trays_per_box_table") or {}).get(spec, cfg.get("trays_per_box_fixed", 4)))
        return int(cfg.get("trays_per_box_fixed", 4) or 4)

    @staticmethod
    def _tray_qty(cfg: Dict[str, Any], spec: Optional[str]) -> int:
        if cfg.get("tray_qty_mode") == "by_spec" and spec:
            return int((cfg.get("tray_qty_table") or {}).get(spec, cfg.get("tray_qty_fixed", 0)))
        return int(cfg.get("tray_qty_fixed", 0) or 0)

    def _resolve_box_total(self, cfg: Dict[str, Any], mes_data: Dict[str, Any], spec: Optional[str]) -> int:
        """按配置算应做箱数: field=直接取字段 / formula=字段 ÷ (每箱托盘数 × 每托盘数量)."""
        field = cfg.get("box_count_field", "dispatch_qty")
        try:
            raw = int(mes_data.get(field, 0) or 0)
        except (ValueError, TypeError):
            raw = 0
        if cfg.get("box_count_source") == "formula":
            per_box = self._trays_per_box(cfg, spec) * max(1, self._tray_qty(cfg, spec))
            return raw // per_box if per_box else 0
        return raw

    # =============================================================
    # 报警 / MES 钩子
    # =============================================================

    def _raise_alarm(self, cfg: Dict[str, Any], kind: str, msg: str) -> None:
        _pkg_dbg(f"报警[{kind}]", msg)
        try:
            if self._alarm_sink:
                self._alarm_sink(cfg, kind, msg)
            else:
                print(f"[PackagingFlow][ALARM:{kind}] {cfg.get('name')}: {msg}")
        except Exception as e:
            print(f"[PackagingFlow] alarm_sink 异常 (隔离): {e}")

    @staticmethod
    def _latest_run_completed_ok(config_id: int, order_no: str, db) -> bool:
        """该配置下这个工单号的最近一次运行是否「已完成且 OK」(重扫拦截判据).

        取最近一条 (id 最大) 而非任意历史: 完成 OK 后被人工重开又做 NG 的单,
        以最新状态为准允许再处置. 查库异常按"不拦"处理 (不因判据故障卡住产线).
        """
        try:
            from backend.models.mes_models import PackagingFlowRun
            row = (db.query(PackagingFlowRun)
                   .filter(PackagingFlowRun.flow_config_id == config_id,
                           PackagingFlowRun.order_no == order_no)
                   .order_by(PackagingFlowRun.id.desc())
                   .first())
            return (row is not None and row.status == "completed"
                    and (row.final_result or "").upper() == "OK")
        except Exception as e:
            print(f"[PackagingFlow] 重扫拦截查库异常 (隔离, 按不拦处理): {e}")
            return False

    def _pull_mes(self, cfg: Dict[str, Any], order_no: str) -> Optional[Dict[str, Any]]:
        if self._mes_fetcher is None:
            return None  # M2 无 fetcher: 视为拉单未接入, 走 on_mes_fail 策略
        try:
            return self._mes_fetcher(cfg, order_no)
        except Exception as e:
            print(f"[PackagingFlow] mes_fetcher 异常 (隔离): {e}")
            return None

    def _push_mes(self, cfg: Dict[str, Any], run: Dict[str, Any]) -> bool:
        """工单完成回推 MES (可选). 成功返回 True. 无 pusher / 未开启 / 异常都返回 False, 不阻断."""
        if not cfg.get("push_on_complete"):
            return False
        if self._mes_pusher is None:
            return False
        try:
            self._mes_pusher(cfg, run)
            return True
        except Exception as e:
            print(f"[PackagingFlow] mes_pusher 异常 (隔离): {e}")
            return False

    # =============================================================
    # 事件入口 1: 扫码 (扫工单 / 扫箱标签)
    # =============================================================

    def on_scan(self, code: str, db, channel_id: Optional[int] = None,
                scan_device_id: Optional[int] = None) -> None:
        with self._lock:
            if not self._configs:
                return  # 没有任何包装结算配置 → 零差异
            config_id = self._resolve_config_for_scan(channel_id, scan_device_id)
            if config_id is None:
                return
            cfg = self._configs[config_id]
            norm = self._normalize(code, cfg)
            if not norm:
                return
            run = self._runs.get(config_id)
            _pkg_dbg("扫码入口",
                     f"cfg={cfg.get('name')} ch={channel_id} raw={code!r} norm={norm!r} "
                     f"unit={cfg.get('count_unit', 'trays')} has_run={run is not None}")

            # v3.42.1 已完成(OK)工单重扫拦截 (默认关): 扫到"最近一次运行已完成且 OK"
            # 的工单号 → 报警提示且不重新录入. 在途同号刷新不受影响 (走下面原逻辑);
            # 完成但 NG 的单不拦 (允许重扫补做). 单点守门, 覆盖开首单/换单全部入口.
            if (cfg.get("block_completed_order_rescan")
                    and (run is None or norm != run.get("order_no"))
                    and self._latest_run_completed_ok(config_id, norm, db)):
                self._raise_alarm(cfg, "completed_order_rescan",
                                  f"工单 {norm} 已完成(OK), 重复扫码已拦截, 不再重新录入")
                return

            if run is None:
                # 工单号识别规则 (v3.30.1, 默认空=不过滤): 仅在没有在途工单、准备开第一单时生效.
                # 挡掉误扫的数量/物料等非工单条码 (如 '80.00' 开出垃圾工单).
                # 已有在途工单时走原有标签不符/复合取段逻辑, 不受此规则影响.
                if not self._order_code_ok(norm, cfg):
                    self._raise_alarm(cfg, "order_code_reject",
                                      f"扫到非工单码 {norm} 已忽略 (不符合工单号识别规则, 可能误扫了数量/物料条码)")
                    return
                # 第 1 次扫 = 开工单
                self._open_order(config_id, cfg, norm, code, db)
                return

            if cfg.get("count_unit") == "sliders":
                # sliders 口径: 箱由检测周期驱动, 扫码只管工单层 (开工单 / 换工单)
                self._on_scan_sliders(config_id, cfg, run, norm, code, db)
                return

            if norm == run["order_no"]:
                # 同号
                if run["current_box_index"] == 0:
                    # 第 2 次扫同号 = 开始第 1 箱
                    self._open_box(run, cfg, db)
                else:
                    # 已开过箱, 这次扫 = 结算当前箱 + 开新箱
                    self._settle_current_box(run, cfg, db)
                    if run["box_total"] > 0 and run["box_done"] >= run["box_total"]:
                        self._raise_alarm(cfg, "over_box",
                                          f"工单 {run['order_no']} 已做满 {run['box_total']} 箱仍在扫码 (多箱)")
                        self._persist_run(run, db)
                    else:
                        self._open_box(run, cfg, db)
            else:
                # 箱标签不符判定: 工单已开但还没开始做任何箱 (current_box_index==0) 时扫到
                # 不同号, 更可能是"首个箱标签贴错"而非换工单 (一箱都没做, 谈不上漏箱).
                #   off  = 不判, 走下面换工单旧逻辑
                #   warn = 报警后仍按换工单处理 (跳过漏箱误判)
                #   block= 报警且不切, 丢弃本次扫码, 等工人扫回正确标签
                if run["current_box_index"] == 0 and cfg.get("on_label_mismatch", "warn") != "off":
                    self._raise_alarm(cfg, "label_mismatch",
                                      f"工单 {run['order_no']} 开工后首个箱标签 {norm} 与工单号不符 (疑似贴错标签)")
                    if cfg.get("on_label_mismatch") == "block":
                        self._persist_run(run, db)
                        return
                    # warn: 报警后按换工单处理 (没做箱, 不叠加漏箱)
                    self._open_order(config_id, cfg, norm, code, db)
                    return
                # 不同号 = 新工单. 先把当前正在进行的箱封箱结算 (扫"下一个标签"=上一箱封箱时刻),
                # 再判漏箱 — 否则最后一箱 (靠扫新工单收尾) 会被误判为漏箱.
                if run["current_box_index"] > run["box_done"]:
                    self._settle_current_box(run, cfg, db)
                if run["box_total"] > 0 and run["box_done"] < run["box_total"]:
                    self._raise_alarm(cfg, "short_box",
                                      f"工单 {run['order_no']} 应做 {run['box_total']} 箱, 仅做 {run['box_done']} 箱就扫了新标签 (漏箱)")
                    if cfg.get("on_short_box") == "redo":
                        # 补做: 不切工单, 等工人扫回正确工单标签继续做剩余箱
                        self._persist_run(run, db)
                        return
                    self._abort_order(run, db)
                else:
                    self._complete_order(run, cfg, db)
                self._open_order(config_id, cfg, norm, code, db)

    def _on_scan_sliders(self, config_id: int, cfg: Dict[str, Any], run: Dict[str, Any],
                         norm: str, code: str, db) -> None:
        """sliders 口径扫码: 箱由检测周期驱动, 扫码只在工单层动作 (开 / 换 / 漏箱)."""
        # 工单挂「等放工单收尾」时的扫码出口 (v3.43, 判定方式二选一互斥):
        #   探到放工单 → 工单正常收尾 (两种模式一致);
        #   扫新单且仍没放:
        #     scan 模式 (默认) = 扫新单就是判定点 → 报警 + 旧单判 NG 收尾再开新单;
        #     timeout 模式 = 扫新单不参与判定 → 拒收本次扫码 (提示稍候), 判定权归时限;
        #   同号重扫且仍没放 → 提醒继续等 (两种模式一致).
        if run.get("awaiting_paper"):
            deferred_alarm = None
            if self._probe_paper_order(cfg, run):
                self._close_awaiting_paper(cfg, run, db, ok_paper=True)
            elif norm != run["order_no"]:
                if self._paper_judge_mode(cfg) == "timeout":
                    aw = run.get("awaiting_paper") or {}
                    left = max(0, int(cfg.get("tail_paper_timeout_s", 0) or 0)
                               - int(time.time() - aw.get("since", 0)))
                    self._raise_alarm(cfg, "missing_paper",
                                      f"工单 {run['order_no']} 等放工单收尾中 (时限判定, "
                                      f"剩余约 {left} 秒), 本次扫码未受理, 请稍候再扫")
                    _pkg_dbg("时限判定中扫新单→拒收",
                             f"old={run.get('order_no')} new={norm} left={left}s")
                    self._persist_run(run, db)
                    return
                # v3.43.1 报警延后到开新单之后再触发: 开新单可能触发按规格切项目
                # (换项目=重载模型+同步配置=重置检测运行时), 会把刚立起的人工确认
                # 定格抹掉 (确认框闪一下就消失). 先收尾开单, 定格立在切换之后.
                deferred_alarm = self._close_awaiting_paper(cfg, run, db,
                                                            ok_paper=False, defer_alarm=True)
            else:
                self._raise_alarm(cfg, "missing_paper",
                                  f"工单 {run['order_no']} 仍未检测到放工单动作 (等放工单收尾)")
                self._persist_run(run, db)
                return
            # 工单已收尾 (在途已移除); 扫的是新单则直接开新单.
            if norm != run["order_no"]:
                self._open_order(config_id, cfg, norm, code, db)
            if deferred_alarm:
                self._raise_alarm(cfg, "missing_paper", deferred_alarm)
            return
        if norm == run["order_no"]:
            # 中途扫同号: 不开 / 不结箱 (上银扫码仅为开工单), 仅刷新落库
            _pkg_dbg("扫码同号刷新", f"order={norm} box_done={run.get('box_done')}/{run.get('box_total')}")
            self._persist_run(run, db)
            return
        # 不同号, 但一箱都还没做完 (box_done==0): 更可能是"首个箱标签贴错"而非换工单
        # (一箱都没做谈不上漏箱). 走标签错判定 — block=报警不切等扫回正确标签 / warn=报警后换单.
        if run["box_done"] == 0 and cfg.get("on_label_mismatch", "warn") != "off":
            self._raise_alarm(cfg, "label_mismatch",
                              f"工单 {run['order_no']} 箱标签 {norm} 与工单号不符 (疑似贴错标签)")
            if cfg.get("on_label_mismatch") == "block":
                self._persist_run(run, db)
                return
            self._open_order(config_id, cfg, norm, code, db)  # warn: 报警后换单
            return
        # 不同号 + 已做过箱 = 新工单. 旧工单还在 (= 没做满) → 漏箱处置
        if run["box_total"] > 0 and run["box_done"] < run["box_total"]:
            self._raise_alarm(cfg, "short_box",
                              f"工单 {run['order_no']} 应做 {run['box_total']} 箱, "
                              f"仅做 {run['box_done']} 箱就扫了新工单 (漏箱)")
            if cfg.get("on_short_box") == "redo":
                self._persist_run(run, db)
                return  # 不切工单, 等工人扫回原工单继续补做剩余箱
            self._abort_order(run, db)
        else:
            self._complete_order(run, cfg, db)
        self._open_order(config_id, cfg, norm, code, db)

    def _resolve_config_for_scan(self, channel_id: Optional[int],
                                 scan_device_id: Optional[int]) -> Optional[int]:
        if scan_device_id is not None:
            for cid, cfg in self._configs.items():
                if cfg.get("scan_device_id") == scan_device_id:
                    return cid
        if channel_id is not None and channel_id in self._channel_to_config:
            return self._channel_to_config[channel_id]
        if len(self._configs) == 1:
            return next(iter(self._configs))
        return None

    # =============================================================
    # 事件入口 2: 一个托盘检测周期结算
    # =============================================================

    def on_cycle_settled(self, channel_id: int, cycle_id: int, is_good: bool, db,
                         slider_count: Optional[int] = None,
                         remediation: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            config_id = self._channel_to_config.get(channel_id)
            if config_id is None:
                return  # 通道未参与包装结算 → 零差异
            cfg = self._configs.get(config_id)
            run = self._runs.get(config_id)
            if cfg is None or run is None:
                return  # 没开工单 → 忽略
            # v3.23: 已挂起等补做时, 检测层又来一个周期 → 忽略, 由人工补做/重做接口推进,
            # 避免阻塞态被新周期覆盖.
            if run.get("status") == "pending_remediation":
                return
            if cfg.get("count_unit") == "sliders":
                # sliders 口径: 一个检测周期 = 一个箱 (即便没开箱也会自动开第 1 箱)
                self._on_cycle_settled_sliders(cfg, run, cycle_id, bool(is_good),
                                               slider_count, db, remediation)
                return
            _pkg_dbg("周期结算 trays",
                     f"order={run.get('order_no')} box={run.get('current_box_index')} "
                     f"cycle={cycle_id} is_good={is_good}")
            # trays 默认口径 (v3.21 原行为, 零差异)
            if run["current_box_index"] == 0:
                return  # 没开箱时来的托盘 → 忽略
            if is_good:
                run["current_box_trays"] += 1
            else:
                # 托盘数量不达标 (检测层已判 NG): 报警, 不计入. 补做/作废由现场处置.
                self._raise_alarm(cfg, "tray_ng",
                                  f"工单 {run['order_no']} 第 {run['current_box_index']} 箱出现不达标托盘 (cycle={cycle_id})")
            self._persist_run(run, db)

    # =============================================================
    # 强制停止 / 待机结算 (M3 接停止/待机事件挂接点)
    # =============================================================

    def on_forced_settle(self, config_id: int, db,
                         reason: Optional[str] = None,
                         operator: Optional[str] = None,
                         strategy_override: Optional[str] = None) -> bool:
        """强制停止 / 待机 / 管理员手动强制结案: 处置进行中工单.

        settle = 收尾结算 (未满箱按 on_forced_stop_partial 判合格性) + 完成工单
        abort  = 直接作废 (不结算, 标 aborted)
        keep   = 原样保留进行中 (待恢复继续, 不动状态)

        reason/operator: 管理员手动「强制结案」时填的必填理由 + 授权账号, 落库审计.
        strategy_override: 手动强制结案时强制走 settle (不受配置 keep/abort 影响) — 传 "settle".
        返回 True = 真的动了工单 (结算/作废); False = 无工单 / keep 未动.
        """
        with self._lock:
            cfg = self._configs.get(config_id)
            run = self._runs.get(config_id)
            if cfg is None or run is None:
                return False
            strategy = strategy_override or cfg.get("on_forced_stop", "settle")
            if strategy == "keep":
                return False
            if reason:
                run["forced_reason"] = reason
            if operator:
                run["forced_by"] = operator
            if strategy == "abort":
                self._abort_order(run, db)
                return True
            # settle: 结算当前未结算的箱 + 完成工单.
            # sliders 口径箱已逐周期即时结算, 半箱 (开了没结算) 直接丢弃, 不走托盘口径结算.
            if run.get("awaiting_paper"):
                # 工单挂等放工单收尾: 管理员强制结案 = 豁免放工单, 工单按各箱成绩收尾
                # (forced_reason/forced_by 已写入 run, 落库留痕).
                self._close_awaiting_paper(cfg, run, db, ok_paper=True)
                return True
            if (cfg.get("count_unit") != "sliders"
                    and run["current_box_index"] > run["box_done"]):
                self._settle_current_box(run, cfg, db,
                                         force_partial=cfg.get("on_forced_stop_partial", "fail"))
            self._complete_order(run, cfg, db, forced=True)
            return True

    def force_settle_manual(self, config_id: int, db, reason: str,
                            operator: Optional[str] = None) -> bool:
        """管理员/主管「强制结案」: 必带理由, 强制走 settle 收尾 (忽略配置 keep/abort).

        返回 True = 已结案; False = 该配置当前无进行中工单.
        """
        return self.on_forced_settle(config_id, db, reason=reason,
                                     operator=operator, strategy_override="settle")

    def on_forced_settle_by_channel(self, channel_id: int, db,
                                    is_standby: bool = False) -> None:
        """source 停止 / 待机时按工位触发收尾. 待机是否收尾受 forced_settle_on_standby 控制.

        没有进行中工单 / 通道未参与包装结算时静默返回 — 零差异.
        """
        with self._lock:
            config_id = self._channel_to_config.get(channel_id)
            if config_id is None:
                return
            cfg = self._configs.get(config_id)
            if cfg is None or self._runs.get(config_id) is None:
                return
            if is_standby and not cfg.get("forced_settle_on_standby", True):
                return
            self.on_forced_settle(config_id, db)  # RLock 可重入

    def on_step_detected(self, channel_id: int, label: str) -> None:
        """步骤出现即时通知 (v3.42.1): 检测层确认一个序列外步骤出现时调进来.

        两个消费方 (都只认"放工单"标签, 其余情况直接返回, 零差异):
          1. 工单「等放工单收尾」等待态 (v3.43) — 放工单在尾箱落账之后才做
             (周期外补做) 时, 出现即完成工单, 不必等下一次扫码 / 下一个周期结算;
          2. 提前放工单报警 (v3.43.1) — 尾箱放工单功能开着, 但动作出现在**非尾箱**
             作业期间 (工单纸只该进尾箱) → 当场报警提醒纠正, 每箱只报一次.
             不改箱成绩不拦结算 (位置错是操作提醒, 箱好坏仍归检测周期).
        调用方在检测线程, 必须错误隔离 (调用点已 try/except)。
        """
        if not label or not self._configs:
            return
        with self._lock:
            config_id = self._channel_to_config.get(channel_id)
            if config_id is None:
                return
            cfg = self._configs.get(config_id)
            run = self._runs.get(config_id)
            if cfg is None or run is None:
                return
            if label != (cfg.get("tail_paper_step_label") or ""):
                return
            if run.get("awaiting_paper"):
                _pkg_dbg("放工单出现→工单即时收尾",
                         f"order={run.get('order_no')} ch={channel_id}")
                from backend.db.database import SessionLocal
                db = SessionLocal()
                try:
                    self._close_awaiting_paper(cfg, run, db, ok_paper=True)
                finally:
                    db.close()
                return
            # 提前放工单: 功能开着 + 放的时机不对 → 报警提醒. 两种"提前":
            #   a) 还没做到尾箱 (含扫单后未开箱) — 工单纸只该进尾箱;
            #   b) 名义上已翻到尾箱, 但尾箱实际一件没装 (上一箱一落账"当前箱"就
            #      翻页, 物理上尾箱可能根本没开始) — 放工单是装完的收尾动作,
            #      空箱就放纸同样是提前. 实时进箱数拿不到时 (钩子缺/非容器混合)
            #      保守放行, 宁漏报不误报.
            if not cfg.get("tail_paper_order_required"):
                return
            box_total = int(run.get("box_total") or 0)
            cur_box = int(run.get("current_box_index") or 0)
            if box_total <= 0:
                return  # 箱数未知不判
            if cur_box >= box_total:
                progress = None
                if self._box_progress_getter is not None:
                    try:
                        progress = self._box_progress_getter(channel_id)
                    except Exception as e:
                        print(f"[PackagingFlow] 进箱数探测异常 (隔离): {e}")
                if progress is None or int(progress) > 0:
                    return  # 尾箱已在装 (或拿不到进度) = 正常动作
                where = f"尾箱(第 {box_total} 箱)尚未开始装箱"
            else:
                where = (f"第 {cur_box} 箱(非尾箱)" if cur_box > 0 else "开箱前")
            if run.get("early_paper_alarm_box") == cur_box:
                return  # 本箱已报过, 不重复轰炸
            run["early_paper_alarm_box"] = cur_box
            self._raise_alarm(cfg, "early_paper",
                              f"工单 {run['order_no']} {where}检测到放工单动作, "
                              f"工单纸应在尾箱装完时放入, 请取出纠正")
            _pkg_dbg("提前放工单报警",
                     f"order={run.get('order_no')} box={cur_box}/{box_total} "
                     f"ch={channel_id} where={where}")

    # =============================================================
    # v3.23 NG 补做: 少装挂起后人工补做 (补滑块) / 重做
    # =============================================================

    def supplement_sliders(self, config_id: int, db,
                           target_count: Optional[int] = None,
                           operator: Optional[str] = None,
                           reason: Optional[str] = None) -> bool:
        """对挂起中的少装箱「补滑块」: 补齐数量后直接落账, 不重置周期.

        target_count: None=自动补齐到目标; 传值=操作员手动指定最终数 (clamp 到 [0, 目标]).
        operator/reason: 审计留痕 (谁补的 / 理由), 写进 box_details[].remediated.
        返回 True=补做成功落账; False=当前无挂起箱.
        """
        with self._lock:
            cfg = self._configs.get(config_id)
            run = self._runs.get(config_id)
            if cfg is None or run is None:
                return False
            pend = run.get("pending_box")
            if not pend or run.get("status") != "pending_remediation":
                return False
            target = int(pend.get("target") or 0)
            orig = int(pend.get("sliders") or 0)
            if target_count is None:
                final_sc = target
            else:
                final_sc = max(0, min(int(target_count), target))
            ok = (target <= 0 or final_sc == target)
            remediated = {
                "by": operator or "未知", "from": orig, "to": final_sc,
                "reason": (reason or "补滑块"), "ts": time.time(),
            }
            is_tail = bool(pend.get("is_tail"))
            cycle_id = pend.get("cycle")
            run["pending_box"] = None
            run["status"] = "running"
            _pkg_dbg("补滑块落账",
                     f"order={run.get('order_no')} box={run.get('current_box_index')} "
                     f"{orig}->{final_sc}/{target} by={operator}")
            self._finalize_box_sliders(run, cfg, db, final_sc, ok, is_tail,
                                       cycle_id, target, remediated=remediated)
            return True

    def redo_pending(self, config_id: int, db,
                     operator: Optional[str] = None) -> bool:
        """对挂起中的少装箱「重做」: 丢弃本箱, 保持同一箱号等下一检测周期重新结算.

        返回 True=已转回进行中等重测; False=当前无挂起箱.
        """
        with self._lock:
            run = self._runs.get(config_id)
            if run is None or run.get("status") != "pending_remediation":
                return False
            run["pending_box"] = None
            run["current_box_sliders"] = 0
            run["status"] = "running"
            _pkg_dbg("少装重做",
                     f"order={run.get('order_no')} box={run.get('current_box_index')} by={operator}")
            self._persist_run(run, db)
            return True

    # =============================================================
    # 状态机内部步骤
    # =============================================================

    def _open_order(self, config_id: int, cfg: Dict[str, Any], norm: str, raw: str, db) -> None:
        # 长度校验
        if cfg.get("label_len", 0) and len(norm) != cfg["label_len"]:
            self._raise_alarm(cfg, "label_len",
                              f"工单标签长度异常: 期望 {cfg['label_len']} 位, 实得 {len(norm)} 位 ({norm})")
        mes_data = self._pull_mes(cfg, norm)
        if mes_data is None:
            self._raise_alarm(cfg, "mes_fail", f"拉取工单 {norm} 失败")
            if cfg.get("on_mes_fail") == "block":
                _pkg_dbg("开工单阻断", f"order={norm} MES拉单失败 on_mes_fail=block")
                return  # 阻断: 不开工单
            mes_data = {}  # offline: 继续, 箱数未知 (0)
        spec = mes_data.get("spec")
        _pkg_dbg("MES拉单成功",
                 f"order={norm} spec={spec!r} keys={list(mes_data.keys())[:8]}")
        if cfg.get("count_unit") == "sliders":
            self._open_order_sliders(config_id, cfg, norm, raw, spec, mes_data, db)
            return
        # trays 默认路径 (v3.21 原行为, 零差异)
        box_total = self._resolve_box_total(cfg, mes_data, spec)
        run = self._new_run_dict(config_id, norm, raw, spec, box_total)
        run["cust_name"] = mes_data.get("cust_name")
        self._persist_run(run, db, create=True)
        self._runs[config_id] = run

    # =============================================================
    # v3.22 上银 MES 闭环: sliders 计数口径 + 尾箱 + 自动切项目 + 塞工单 gate
    #   语义: 一个检测周期 (封箱步骤结算) = 一个箱完成. 扫工单只开工单 + 拉 MES + 算箱数/尾箱.
    #   每箱合格判定: 检测层容器累加器按"当前箱目标"判 (反向钩子设, 含尾箱余数) → is_good,
    #   协调器再用进箱滑块数与目标双重核对 + 编排尾箱/塞工单 gate + 多箱/漏箱报警.
    # =============================================================

    def _open_order_sliders(self, config_id: int, cfg: Dict[str, Any], norm: str,
                            raw: str, spec: Optional[str], mes_data: Dict[str, Any], db) -> None:
        # 1) 按规格自动激活对应项目 (开关默认关). 失败兜底: 报警但用当前项目继续.
        if cfg.get("auto_switch_project") and spec:
            switched = self._activate_project_by_spec(spec, cfg)
            _pkg_dbg("按规格切项目", f"spec={spec!r} ok={switched}")
            if not switched:
                self._raise_alarm(cfg, "mes_fail",
                                  f"规格 {spec} 自动切项目失败, 用当前激活项目继续")
        # 2) 每箱滑块数 (project=读激活项目容器目标 / config=固定值)
        items_per_box = self._resolve_items_per_box(cfg)
        # 3) MES 滑块总数
        field = cfg.get("slider_total_field", "dispatch_qty")
        try:
            slider_total = int(mes_data.get(field, 0) or 0)
        except (ValueError, TypeError):
            slider_total = 0
        # 4) 箱数 + 尾箱目标
        box_total, tail_target = compute_box_plan(slider_total, items_per_box)
        run = self._new_run_dict(config_id, norm, raw, spec, box_total)
        run["cust_name"] = mes_data.get("cust_name")
        run["count_unit"] = "sliders"
        run["slider_total"] = slider_total
        run["items_per_box"] = items_per_box
        run["tail_target"] = tail_target
        self._persist_run(run, db, create=True)
        self._runs[config_id] = run
        _pkg_dbg("开工单 sliders",
                 f"order={norm} spec={spec!r} slider_total={slider_total} "
                 f"items_per_box={items_per_box} box_total={box_total} tail_target={tail_target}")
        # 5) 立即开第 1 箱并把当前箱目标设回检测层 (不依赖第 2 次扫码)
        self._open_box_sliders(run, cfg, db)

    def _resolve_items_per_box(self, cfg: Dict[str, Any]) -> int:
        if cfg.get("items_per_box_source") == "config":
            return int(cfg.get("items_per_box_fixed", 0) or 0)
        return self._read_active_project_item_target()

    @staticmethod
    def _read_active_project_item_target() -> int:
        """读当前激活项目的容器整箱滑块目标 (pipeline_config.custom_mix_container_item_target)."""
        try:
            from backend.db.database import SessionLocal
            from backend.models.models import Project
            db = SessionLocal()
            try:
                proj = db.query(Project).filter(Project.is_active == True).first()  # noqa: E712
                if proj is not None and proj.pipeline_config:
                    return int((proj.pipeline_config or {}).get(
                        "custom_mix_container_item_target", 0) or 0)
            finally:
                db.close()
        except Exception as e:
            print(f"[PackagingFlow] 读激活项目每箱滑块数异常 (隔离): {e}")
        return 0

    def _activate_project_by_spec(self, spec: str, cfg: Dict[str, Any]) -> bool:
        if self._project_activator is None:
            return False
        try:
            return bool(self._project_activator(spec, cfg))
        except Exception as e:
            print(f"[PackagingFlow] 自动切项目异常 (隔离): {e}")
            return False

    def _current_box_target(self, run: Dict[str, Any]) -> int:
        """当前箱滑块目标: 普通箱 = 每箱数, 尾箱 (最后一箱) = 尾数."""
        is_tail = run["box_total"] > 0 and run["current_box_index"] >= run["box_total"]
        if is_tail and int(run.get("tail_target", 0) or 0) > 0:
            return int(run["tail_target"])
        return int(run.get("items_per_box", 0) or 0)

    def _apply_box_target(self, cfg: Dict[str, Any], target: int) -> None:
        """把当前箱目标反向设回检测层容器累加器 (让含尾箱的合格判定正确)."""
        if self._box_target_setter is None or target <= 0:
            return
        try:
            self._box_target_setter(int(cfg.get("channel_id", 0) or 0), int(target))
            _pkg_dbg("设当前箱滑块目标", f"ch={cfg.get('channel_id')} target={target}")
        except Exception as e:
            print(f"[PackagingFlow] 设当前箱目标异常 (隔离): {e}")

    def _open_box_sliders(self, run: Dict[str, Any], cfg: Dict[str, Any], db) -> None:
        if run["box_total"] > 0 and run["box_done"] >= run["box_total"]:
            self._raise_alarm(cfg, "over_box",
                              f"工单 {run['order_no']} 已做满 {run['box_total']} 箱, 不再开新箱 (多箱)")
            self._persist_run(run, db)
            return
        run["current_box_index"] = run["box_done"] + 1
        run["current_box_sliders"] = 0
        run["status"] = "running"
        self._apply_box_target(cfg, self._current_box_target(run))
        self._persist_run(run, db)

    def _probe_paper_order(self, cfg: Dict[str, Any], run: Dict[str, Any]) -> bool:
        if run.get("paper_order_done"):
            return True
        if self._paper_order_probe is None:
            return True  # 无探测器 → gate 退化为不阻断 (避免误卡)
        try:
            return bool(self._paper_order_probe(
                int(cfg.get("channel_id", 0) or 0), cfg.get("tail_paper_step_label")))
        except Exception as e:
            print(f"[PackagingFlow] 塞工单探测异常 (隔离): {e}")
            return True

    def _probe_oil_nozzle(self, cfg: Dict[str, Any]) -> bool:
        """探测本周期"放油嘴"步骤是否已 covered (每箱缺油嘴 gate, 复用塞工单同款步骤探测钩子).

        与塞工单 gate 区别: 每箱都重判 (不缓存 paper_order_done), 且无探测器 / 没配步骤标签时
        返回 True (不阻断) —— 每箱 gate 若误卡会卡死整条线, 故未配置时退化为放行.
        """
        if self._paper_order_probe is None:
            return True
        label = cfg.get("oil_nozzle_step_label")
        if not label:
            return True
        try:
            return bool(self._paper_order_probe(int(cfg.get("channel_id", 0) or 0), label))
        except Exception as e:
            print(f"[PackagingFlow] 缺油嘴探测异常 (隔离): {e}")
            return True

    @staticmethod
    def _paper_judge_mode(cfg: Dict[str, Any]) -> str:
        """缺工单判定方式 (二选一互斥, v3.43):
          "scan"    = 扫新单时判定 (默认): 下一单扫码进来发现没放工单 → 报警 + NG 收尾,
                      无时间限制, 不布防 Timer;
          "timeout" = 按时限判定: 到点没放 → 报警 + NG 收尾 (终局); 扫新单不参与判定
                      (时限内扫新单被拒收, 等判定出结果).
        兜底归一: 两个字段组合非法 (都关/都开) 时以扫新单为准 — 用户口径"默认是扫新码"."""
        if not cfg.get("tail_paper_scan_alarm", True) \
                and int(cfg.get("tail_paper_timeout_s", 0) or 0) > 0:
            return "timeout"
        return "scan"

    def _close_awaiting_paper(self, cfg: Dict[str, Any], run: Dict[str, Any], db,
                              ok_paper: bool,
                              defer_alarm: bool = False) -> Optional[str]:
        """收掉「等放工单收尾」状态并完成工单 (v3.43).

        v3.43 语义: 尾箱在周期结算时已按自身成绩落账 (箱结算归周期), 这里只做
        **工单层**收尾 — 放工单是工单结算动作, 不再回头改箱成绩.

        ok_paper=True  = 放工单已到位 → 工单按各箱成绩正常收尾;
        ok_paper=False = 判定为没放 (扫新单判定命中 / 时限到点) → 报警 + 工单判 NG 收尾.

        defer_alarm=True (v3.43.1, 仅扫新单判定路径用): NG 收尾照做, 但缺工单报警
        **不在这里触发**, 把报警文案返回给调用方, 由它在开新单 (含按规格切项目 /
        重载模型 / 重置运行时) 之后再触发 — 否则报警立起的人工确认定格会被随后的
        项目切换抹掉 (确认框闪退). 返回值: 需要延后触发的报警文案, 无则 None.
        """
        aw = run.pop("awaiting_paper", None)
        if not aw:
            return None
        self._cancel_paper_timer(run["config_id"])
        if ok_paper:
            run["paper_order_done"] = True
            _pkg_dbg("放工单到位→工单收尾",
                     f"order={run.get('order_no')} 等待 {time.time() - aw.get('since', 0):.0f}s")
            self._complete_order(run, cfg, db)
            return None
        msg = f"工单 {run['order_no']} 未放工单, 工单判 NG 收尾"
        _pkg_dbg("未放工单→工单判NG收尾",
                 f"order={run.get('order_no')} defer_alarm={defer_alarm}")
        self._complete_order(run, cfg, db, paper_missing=True)
        if defer_alarm:
            return msg
        self._raise_alarm(cfg, "missing_paper", msg)
        return None

    def _arm_paper_timer(self, cfg: Dict[str, Any], run: Dict[str, Any]) -> None:
        """时限判定模式布防 Timer (扫新单模式不布防). 到点 = 终局判定:
        最后探一次放工单, 到位则 OK 收尾, 没到位报警 + NG 收尾."""
        if self._paper_judge_mode(cfg) != "timeout":
            return
        timeout = int(cfg.get("tail_paper_timeout_s", 0) or 0)
        config_id = run["config_id"]
        self._cancel_paper_timer(config_id)
        t = threading.Timer(timeout, self._on_paper_timeout,
                            args=(config_id, run.get("run_uuid"), timeout))
        t.daemon = True
        t.start()
        self._paper_timers[config_id] = t

    def _cancel_paper_timer(self, config_id: int) -> None:
        t = self._paper_timers.pop(config_id, None)
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass

    def _on_paper_timeout(self, config_id: int, run_uuid: Optional[str],
                          timeout: int) -> None:
        """时限到点回调 (Timer 线程) — 终局判定: 还在等同一张工单 → 最后探一次
        放工单, 到位 OK 收尾 / 没到位报警 + NG 收尾. 错误隔离, 不能带崩调用线程."""
        with self._lock:
            self._paper_timers.pop(config_id, None)
            cfg = self._configs.get(config_id)
            run = self._runs.get(config_id)
            if cfg is None or run is None or run.get("run_uuid") != run_uuid:
                return
            if not run.get("awaiting_paper"):
                return
            _pkg_dbg("放工单时限到点→终局判定",
                     f"order={run.get('order_no')} timeout={timeout}s")
            from backend.db.database import SessionLocal
            db = SessionLocal()
            try:
                ok = self._probe_paper_order(cfg, run)
                self._close_awaiting_paper(cfg, run, db, ok_paper=ok)
            except Exception as e:
                print(f"[PackagingFlow] 放工单时限判定异常 (隔离): {e}")
            finally:
                db.close()

    def _on_cycle_settled_sliders(self, cfg: Dict[str, Any], run: Dict[str, Any],
                                  cycle_id: int, is_good: bool,
                                  slider_count: Optional[int], db,
                                  remediation: Optional[Dict[str, Any]] = None) -> None:
        # 工单已挂「等放工单收尾」: 各箱已全部落账, 后续周期只用来探测放工单动作,
        # 不当新箱结算. 探不到也不报警 (报警出口只有扫新单/时限两个可配点).
        if run.get("awaiting_paper"):
            if self._probe_paper_order(cfg, run):
                self._close_awaiting_paper(cfg, run, db, ok_paper=True)
            else:
                self._persist_run(run, db)
            return
        # 没开箱 → 自动开第 1 箱 (鲁棒: 扫工单后第一个检测周期来即开箱)
        if run["current_box_index"] == 0:
            self._open_box_sliders(run, cfg, db)
            if run["current_box_index"] == 0:
                return  # 已做满 / 开箱失败
        sc = int(slider_count or 0)
        run["current_box_sliders"] = sc
        is_tail = run["box_total"] > 0 and run["current_box_index"] >= run["box_total"]
        target = self._current_box_target(run)
        _pkg_dbg("周期结算 sliders",
                 f"order={run.get('order_no')} box={run.get('current_box_index')}/"
                 f"{run.get('box_total')} cycle={cycle_id} sliders={sc}/{target} "
                 f"is_good={is_good} is_tail={is_tail}")
        # v3.23 缺油嘴 gate (每箱查, 开关默认关): 封箱前没检测到放油嘴 → 暂不收尾, 报警, 等补放
        if cfg.get("oil_nozzle_required"):
            if not self._probe_oil_nozzle(cfg):
                self._raise_alarm(cfg, "missing_nozzle",
                                  f"工单 {run['order_no']} 第 {run['current_box_index']} 箱未检测到放油嘴动作, 暂不收尾 (等放油嘴)")
                _pkg_dbg("缺油嘴 gate 未过",
                         f"order={run.get('order_no')} box={run.get('current_box_index')} 暂不收尾")
                self._persist_run(run, db)
                return
            _pkg_dbg("缺油嘴 gate 通过",
                     f"order={run.get('order_no')} box={run.get('current_box_index')}")
        # 尾箱塞工单视觉 gate (开关默认关): 两种模式语义分道 (tail_paper_as_close_action 子开关):
        #   关 = 老行为: 没探到放工单 → 本箱暂不收尾, 每次周期结算都报警等着,
        #       下个周期结算时按那个周期自己的数据重走本 gate;
        #   开 = v3.43 箱归周期/放工单归工单: 尾箱照常按自身成绩当场落账, 放工单只管
        #       **工单**收尾 — 此处不拦不报警, 工单层的等待与报警在 _finalize_box_sliders
        #       的收尾口统一处理 (扫新单/时限两个可配报警出口).
        if is_tail and cfg.get("tail_paper_order_required"):
            if self._probe_paper_order(cfg, run):
                run["paper_order_done"] = True
                _pkg_dbg("尾箱塞工单 gate 通过", f"order={run.get('order_no')}")
            elif not cfg.get("tail_paper_as_close_action"):
                _pkg_dbg("尾箱塞工单 gate 未过", f"order={run.get('order_no')} 暂不收尾")
                self._raise_alarm(cfg, "missing_paper",
                                  f"工单 {run['order_no']} 尾箱未检测到放工单动作, 暂不收尾 (等放工单)")
                self._persist_run(run, db)
                return
        # 本箱合格: 进箱滑块数正好达目标 + 检测步骤齐 (is_good 由检测层按当前箱目标判过)
        ok = bool(is_good) and (target <= 0 or sc == target)
        # v3.23 补滑块: 仅"少装"(检测步骤齐, 仅滑块数不足) 且项目开了"补数量"策略时, 挂起本箱
        # 等人工补做 (延迟落账), 不立即记 NG. 多装 / 检测步骤不齐 / 没开策略 → 走原行为立即结算.
        rem = remediation or {}
        if (not ok and bool(is_good) and target > 0 and sc < target
                and rem.get("enabled") and rem.get("allow_count")):
            run["pending_box"] = {
                "box": run["current_box_index"], "sliders": sc, "target": target,
                "is_tail": is_tail, "cycle": cycle_id, "reason": "short_sliders",
            }
            run["status"] = "pending_remediation"
            self._raise_alarm(cfg, "box_ng",
                              f"工单 {run['order_no']} 第 {run['current_box_index']} 箱滑块 {sc}/{target} "
                              f"少装, 等补做 / 人工确认")
            _pkg_dbg("少装挂起等补做",
                     f"order={run.get('order_no')} box={run.get('current_box_index')} "
                     f"sliders={sc}/{target}")
            self._persist_run(run, db)
            return
        self._finalize_box_sliders(run, cfg, db, sc, ok, is_tail, cycle_id, target)

    def _finalize_box_sliders(self, run: Dict[str, Any], cfg: Dict[str, Any], db,
                              sc: int, ok: bool, is_tail: bool, cycle_id,
                              target: int,
                              remediated: Optional[Dict[str, Any]] = None) -> None:
        """落账一个 sliders 口径箱 (正常结算 / 补做后结算共用), 并推进到下一箱或工单收尾."""
        run["current_box_sliders"] = sc
        run["box_done"] += 1
        result = "OK" if ok else "NG"
        if not ok:
            run["box_ng"] += 1
            self._raise_alarm(cfg, "box_ng",
                              f"工单 {run['order_no']} 第 {run['current_box_index']} 箱滑块 {sc}/{target} "
                              f"或检测步骤不合格, 判 NG")
        detail = {
            "box": run["current_box_index"], "sliders": sc, "target": target,
            "is_tail": is_tail, "result": result, "cycle": cycle_id,
        }
        if remediated:
            detail["remediated"] = remediated  # 补做留痕: 谁补的 / 从几补到几 / 理由
        run["box_details"].append(detail)
        # 做满 → 完成工单; 否则开下一箱 (更新目标, 尾箱会切到尾数)
        if run["box_total"] > 0 and run["box_done"] >= run["box_total"]:
            # v3.43 放工单=工单收尾动作: 各箱已全部落账但放工单还没探到 → 工单挂
            # 「等放工单收尾」, 不完成工单. 此处不报警 (正常作业本来就是先封箱后放
            # 工单); 报警只在两个可配出口: 扫新单发现没放 / 等待超时限.
            # 收口在此处 (而非周期结算入口) 是为了少装补做后补落账的尾箱同样被守住.
            if (cfg.get("tail_paper_order_required")
                    and cfg.get("tail_paper_as_close_action")
                    and not run.get("paper_order_done")
                    and not self._probe_paper_order(cfg, run)):
                run["awaiting_paper"] = {"since": time.time(), "alarmed": False}
                run["status"] = "awaiting_paper"
                _pkg_dbg("各箱已落账→工单挂等放工单收尾",
                         f"order={run.get('order_no')} result={result} "
                         f"timeout_s={cfg.get('tail_paper_timeout_s', 0)}")
                self._arm_paper_timer(cfg, run)
                self._persist_run(run, db)
                return
            _pkg_dbg("本箱结算完成→工单收尾",
                     f"order={run.get('order_no')} result={result} box_done={run['box_done']}")
            self._complete_order(run, cfg, db)
        else:
            _pkg_dbg("本箱结算→开下一箱",
                     f"order={run.get('order_no')} result={result} next_box={run['box_done'] + 1}")
            self._open_box_sliders(run, cfg, db)

    def _open_box(self, run: Dict[str, Any], cfg: Dict[str, Any], db) -> None:
        if run["box_total"] > 0 and run["box_done"] >= run["box_total"]:
            self._raise_alarm(cfg, "over_box",
                              f"工单 {run['order_no']} 已做满 {run['box_total']} 箱, 不再开新箱 (多箱)")
            self._persist_run(run, db)
            return
        run["current_box_index"] = run["box_done"] + 1
        run["current_box_trays"] = 0
        run["status"] = "running"
        _pkg_dbg("开箱 trays",
                 f"order={run.get('order_no')} box={run['current_box_index']}/{run.get('box_total')}")
        self._persist_run(run, db)

    def _settle_current_box(self, run: Dict[str, Any], cfg: Dict[str, Any], db,
                            force_partial: Optional[str] = None) -> None:
        if run["current_box_index"] <= run["box_done"]:
            return  # 当前箱已结算过, 防重复
        need = self._trays_per_box(cfg, run.get("spec"))
        trays = run["current_box_trays"]
        if force_partial is not None:
            ok = (force_partial == "pass")
        else:
            ok = need > 0 and trays >= need
        run["box_done"] += 1
        result = "OK" if ok else "NG"
        if not ok:
            run["box_ng"] += 1
            if force_partial is None:
                self._raise_alarm(cfg, "box_ng",
                                  f"工单 {run['order_no']} 第 {run['current_box_index']} 箱托盘不足 ({trays}/{need}), 判 NG")
        run["box_details"].append({
            "box": run["current_box_index"], "trays": trays, "need": need, "result": result,
        })
        _pkg_dbg("结箱 trays",
                 f"order={run.get('order_no')} box={run['current_box_index']} "
                 f"trays={trays}/{need} result={result} force_partial={force_partial}")
        self._persist_run(run, db)

    def _complete_order(self, run: Dict[str, Any], cfg: Dict[str, Any], db,
                        forced: bool = False, paper_missing: bool = False) -> None:
        # 收尾前结算最后一个未结算箱 (sliders 口径已逐周期即时结算, 跳过托盘口径半箱结算)
        if (cfg.get("count_unit") != "sliders"
                and run["current_box_index"] > run["box_done"]):
            self._settle_current_box(run, cfg, db,
                                     force_partial=(cfg.get("on_forced_stop_partial") if forced else None))
        final = "OK"
        if run["box_ng"] > 0:
            final = "NG"
        if run["box_total"] > 0 and run["box_done"] < run["box_total"]:
            final = "NG"  # 漏箱
        if paper_missing:
            final = "NG"  # v3.43 始终未放工单 (箱成绩不回改, 工单层判 NG)
            run["forced_reason"] = run.get("forced_reason") or "尾箱始终未放工单, 工单判 NG 收尾"
        run["final_result"] = final
        run["status"] = "completed"
        _pkg_dbg("工单完成",
                 f"order={run.get('order_no')} final={final} box_done={run.get('box_done')}/"
                 f"{run.get('box_total')} box_ng={run.get('box_ng')} forced={forced}")
        # 工单完成回推 MES (可选, 默认关). 推成功才置 mes_pushed, 落库留痕.
        if self._push_mes(cfg, run):
            run["mes_pushed"] = True
        self._persist_run(run, db)
        self._runs.pop(run["config_id"], None)

    def _abort_order(self, run: Dict[str, Any], db) -> None:
        _pkg_dbg("工单作废", f"order={run.get('order_no')} box_done={run.get('box_done')}")
        run["status"] = "aborted"
        run["final_result"] = "NG"
        self._persist_run(run, db)
        self._runs.pop(run["config_id"], None)

    def _new_run_dict(self, config_id: int, norm: str, raw: str,
                      spec: Optional[str], box_total: int) -> Dict[str, Any]:
        return {
            "config_id": config_id,
            "run_db_id": None,
            "run_uuid": uuid.uuid4().hex,
            "order_no": norm,
            "order_raw": raw,
            "spec": spec,
            "cust_name": None,  # MES 客户名 (扫工单后回显进度卡, 仅内存不落库)
            "box_total": int(box_total or 0),
            "box_done": 0,
            "box_ng": 0,
            "current_box_index": 0,
            "current_box_trays": 0,
            "box_details": [],
            "final_result": None,
            "mes_pushed": False,
            "status": "order_loaded",
            # v3.22 sliders 口径运行态 (trays 模式保持默认 0/trays, 不影响原行为)
            "count_unit": "trays",
            "slider_total": 0,
            "items_per_box": 0,
            "tail_target": 0,
            "current_box_sliders": 0,
            "paper_order_done": False,
            "forced_reason": None,  # 强制结案理由 (管理员手动收尾时填, 审计)
            "forced_by": None,      # 强制结案授权账号
            # v3.23 少装挂起等补做的箱快照 (None=无挂起); status=pending_remediation 时有值
            "pending_box": None,
            # v3.43 工单「等放工单收尾」等待态 (None=没在等): 各箱已全部落账,
            # 只差放工单动作完成工单. {"since": 挂起时刻, "alarmed": 时限报警是否已报}
            "awaiting_paper": None,
        }

    def _persist_run(self, run: Dict[str, Any], db, create: bool = False) -> None:
        """把内存 run 同步到 PackagingFlowRun 行 (断电恢复 + 历史). 异常隔离, 不阻断状态机."""
        try:
            from backend.models.mes_models import PackagingFlowRun
            if create or run.get("run_db_id") is None:
                row = PackagingFlowRun(
                    flow_config_id=run["config_id"],
                    run_uuid=run["run_uuid"],
                    order_no=run["order_no"],
                    spec=run.get("spec"),
                    box_total=run["box_total"],
                    status=run["status"],
                    count_unit=run.get("count_unit", "trays"),
                    slider_total=int(run.get("slider_total", 0) or 0),
                    items_per_box=int(run.get("items_per_box", 0) or 0),
                    tail_target=int(run.get("tail_target", 0) or 0),
                )
                db.add(row)
                db.commit()
                db.refresh(row)
                run["run_db_id"] = row.id
                return
            row = db.query(PackagingFlowRun).filter(
                PackagingFlowRun.id == run["run_db_id"]
            ).first()
            if not row:
                return
            row.box_total = run["box_total"]
            row.box_done = run["box_done"]
            row.box_ng = run["box_ng"]
            row.status = run["status"]
            row.current_box_index = run["current_box_index"]
            row.current_box_trays = run["current_box_trays"]
            row.box_details = list(run["box_details"])
            row.final_result = run.get("final_result")
            row.mes_pushed = bool(run.get("mes_pushed"))
            row.forced_reason = run.get("forced_reason")
            row.forced_by = run.get("forced_by")
            # v3.22 sliders 口径运行态落库 (断电恢复 + 历史)
            row.count_unit = run.get("count_unit", "trays")
            row.slider_total = int(run.get("slider_total", 0) or 0)
            row.items_per_box = int(run.get("items_per_box", 0) or 0)
            row.tail_target = int(run.get("tail_target", 0) or 0)
            row.current_box_sliders = int(run.get("current_box_sliders", 0) or 0)
            row.paper_order_done = bool(run.get("paper_order_done", False))
            if run["status"] in _TERMINAL_STATES:
                from sqlalchemy.sql import func as _func
                row.completed_at = _func.now()
            db.commit()
        except Exception as e:
            print(f"[PackagingFlow] _persist_run 异常 (隔离, 不阻断状态机): {e}")
            try:
                db.rollback()
            except Exception:
                pass

    # =============================================================
    # 启动恢复
    # =============================================================

    def abort_in_progress_on_startup(self, db) -> int:
        from backend.models.mes_models import PackagingFlowRun
        from sqlalchemy import or_

        rows = db.query(PackagingFlowRun).filter(
            or_(
                PackagingFlowRun.status == "order_loaded",
                PackagingFlowRun.status == "running",
                # v3.43 等放工单收尾: 重启后等待态(内存Timer)已丢, 一并作废防幽灵在途
                PackagingFlowRun.status == "awaiting_paper",
            )
        ).all()
        n = 0
        for r in rows:
            r.status = "aborted"
            n += 1
        if n:
            db.commit()
        return n

    # =============================================================
    # 查询
    # =============================================================

    def get_config(self, config_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            cfg = self._configs.get(config_id)
            return dict(cfg) if cfg else None

    def get_state(self, config_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            run = self._runs.get(config_id)
            return dict(run) if run else None

    def resolve_config_id(self, channel_id: Optional[int] = None,
                          scan_device_id: Optional[int] = None) -> Optional[int]:
        """按工位 / 扫码设备解析归属的包装结算配置 id (扫码端点回带状态用)."""
        with self._lock:
            return self._resolve_config_for_scan(channel_id, scan_device_id)

    def list_loaded_config_ids(self) -> List[int]:
        with self._lock:
            return list(self._configs.keys())

    # =============================================================
    # 测试清理
    # =============================================================

    def cleanup_for_testing(self) -> None:
        with self._lock:
            for cid in list(self._paper_timers):
                self._cancel_paper_timer(cid)
            self._configs.clear()
            self._channel_to_config.clear()
            self._runs.clear()
            self._mes_fetcher = None
            self._alarm_sink = None
            self._mes_pusher = None
            self._box_target_setter = None
            self._project_activator = None
            self._paper_order_probe = None
            self._box_progress_getter = None


# =============================================================
# M3: 真实系统钩子 (拉单 / 报警). 与状态机解耦, 启动时注入.
#   全部异常隔离 — 任何外部系统挂掉都不能阻断状态机或主程序.
# =============================================================

def _real_mes_fetcher(cfg: Dict[str, Any], order_no: str) -> Optional[Dict[str, Any]]:
    """按配置选定的拉单连接, 实时拉这张工单的原始 MES 记录.

    返回 MES 返回数组里第一条 *原始字段* dict (箱数取 dispatch_qty 等原生字段),
    并把规格字段兜底归一到 'spec' 键 (by_spec 模式按规格查表用).
    拿不到 → None, 由状态机走 on_mes_fail 策略 (block / offline).

    注: 此函数在扫码线程持锁内被调, 内含一次同步 HTTP. 包装节拍是秒级 (人工扫码),
    短暂阻塞可接受; 若未来节拍变快需把 HTTP 移到锁外.
    """
    conn_id = cfg.get("pull_conn_id")
    if not conn_id:
        return None
    from backend.db.database import SessionLocal
    from backend.models.mes_models import MESConnection
    from backend.services.mes_puller import get_mes_puller

    db = SessionLocal()
    try:
        conn = db.query(MESConnection).filter(MESConnection.id == conn_id).first()
        if conn is None:
            return None
        pull_cfg = (conn.config or {}).get("pull") or {}
        if not pull_cfg.get("url"):
            return None
        puller = get_mes_puller()
        res = puller.test_connection(pull_cfg, job_no=order_no)
        if not res.get("success"):
            return None
        rows = puller._extract_array(res.get("raw_body"), pull_cfg.get("array_path") or "")
        if not rows or not isinstance(rows[0], dict):
            return None
        item = dict(rows[0])
        if "spec" not in item:
            for k in ("spec", "productSpec", "product_spec", "specification", "spec_no"):
                if item.get(k):
                    item["spec"] = item[k]
                    break
        return item
    except Exception as e:
        print(f"[PackagingFlow] 真实拉单异常 (隔离): {e}")
        return None
    finally:
        db.close()


# 异常类型 → 配置里对应的"项目事件 id"字段. 留空=退回默认通用报警 (event2), 不静默.
_KIND_TO_EVENT_FIELD = {
    "short_box": "event_short_box",
    "over_box": "event_over_box",
    "tray_ng": "event_tray_ng",
    "box_ng": "event_box_ng",
    "label_mismatch": "event_label_mismatch",
    "label_len": "event_label_len",
    "mes_fail": "event_mes_fail",
    "missing_paper": "event_missing_paper",
    # 提前放工单 (非尾箱出现放工单动作) 复用缺工单事件档: 同属"工单纸位置异常"
    "early_paper": "event_missing_paper",
    "missing_nozzle": "event_missing_nozzle",
    "completed_order_rescan": "event_completed_order_rescan",
}


def _real_alarm_sink(cfg: Dict[str, Any], kind: str, msg: str) -> None:
    """包装结算异常 → 触发配置指定的项目事件 (复用其报警/语音/Toast/计数), 不打断检测周期.

    每种异常在配置里可挑一个「项目事件设置」里的事件 id; 命中则借该工位 VSM 的
    ``fire_external_event_response`` 联动报警+语音+Toast+计数 (不结算检测周期).
    未配置 / 事件没找到 / 拿不到工位 → 退回默认通用报警 (event2), 不静默以免漏报.
    """
    ch = int(cfg.get("channel_id", 0) or 0)
    try:
        event_id = cfg.get(_KIND_TO_EVENT_FIELD.get(kind, ""))
        if event_id:
            from backend.api.channel_manager import get_channel_manager
            mgr = get_channel_manager().channels.get(ch)
            if mgr is not None and mgr.fire_external_event_response(
                    event_id, f"[包装] {msg}", source="packaging"):
                return  # 已走配置事件响应, 不再叠加默认报警
        # 没配事件 / 没拿到工位 / 事件没找到 → 默认通用报警
        from backend.api.alarm import alarm_router
        alarm_router.trigger_alarm("event2", channel_id=ch)
    except Exception as e:
        print(f"[PackagingFlow] 触发报警异常 (隔离): {e}")


def _real_mes_pusher(cfg: Dict[str, Any], run: Dict[str, Any]) -> None:
    """工单完成回推 MES — 走现有网关 dispatch, 按事件名 + 工位自动选连接.

    现场只需在某条 MES 连接的"推送事件"里加上 push_event_type (默认 packaging_complete)
    并配好 endpoint / 字段模板; 模板里可引用 {packaging.order_no} / {packaging.final_result} 等.
    """
    from backend.services.mes_gateway import get_mes_gateway
    event_type = cfg.get("push_event_type") or "packaging_complete"
    ch = int(cfg.get("channel_id", 0) or 0)
    context = {
        "packaging": {
            "order_no": run.get("order_no"),
            "spec": run.get("spec"),
            "box_total": run.get("box_total"),
            "box_done": run.get("box_done"),
            "box_ng": run.get("box_ng"),
            "final_result": run.get("final_result"),
            "box_details": run.get("box_details"),
            "run_uuid": run.get("run_uuid"),
            "status": run.get("status"),
        }
    }
    get_mes_gateway().dispatch(event_type, context, channel_id=ch)


def _real_box_target_setter(channel_id: int, target: int) -> None:
    """把当前箱滑块目标反向设回检测层容器累加器 (普通箱=每箱数 / 尾箱=余数).

    让检测层合格判定 (含尾箱) 始终用当前箱真实目标. 拿不到工位 / 无容器混合 → 静默.
    """
    try:
        from backend.api.channel_manager import get_channel_manager
        mgr = get_channel_manager().channels.get(int(channel_id))
        if mgr is None:
            return
        mix = getattr(mgr, "_custom_mix", None)
        if mix is None:
            return
        mix.set_container_item_target(int(target))
    except Exception as e:
        print(f"[PackagingFlow] 设容器目标钩子异常 (隔离): {e}")


def _real_project_activator(spec: str, cfg: Dict[str, Any]) -> bool:
    """按规格激活对应项目。取项目口径见 services.project_match.resolve_project_id_by_spec
    (对照表精确 → 通配符 → 自动同名子串, 与 MES 入站完全一致)。

    均未命中 → False (上层报警兜底, 用当前项目继续).
    """
    try:
        from backend.db.database import SessionLocal
        from backend.models.models import Project
        from backend.services.project_match import resolve_project_id_by_spec
        from backend.api.projects import (
            _reload_model_for_active_project, _sync_project_config_to_channels)

        db = SessionLocal()
        try:
            pid, hit_by = resolve_project_id_by_spec(
                db, spec, cfg.get("spec_to_project") or {},
                match_by_name=cfg.get("match_project_by_name", False),
                strict_boundary=cfg.get("name_match_strict_boundary", False))
            if pid is None:
                _pkg_dbg("切项目未命中", f"spec={spec!r} 对照表/通配符/同名项目均无")
                return False
            proj = db.query(Project).filter(Project.id == int(pid)).first()
            if proj is None:
                _pkg_dbg("切项目未命中", f"spec={spec!r} 命中 id#{pid} 但项目不存在")
                return False
            # v3.43.1 已是当前激活项目 → 原地不动. 重激活会重载模型 (几秒卡顿) 并重置
            # 检测运行时, 把同一次扫码里刚立起的人工确认定格 (_pending_ack) 一并抹掉
            # (真实事故: 扫新单触发缺工单确认框, 被随后的同项目重激活闪退).
            if proj.is_active:
                _pkg_dbg("切项目命中·已激活", f"spec={spec!r} → 项目#{proj.id} 原地不动")
                return True
            _pkg_dbg("切项目命中", f"spec={spec!r} → 项目#{proj.id} (经{hit_by})")
            db.query(Project).update({Project.is_active: False})
            proj.is_active = True
            db.commit()
            db.refresh(proj)
            try:
                _reload_model_for_active_project(db, proj)
            except Exception as e:
                print(f"[PackagingFlow] 切项目模型重载异常 (隔离): {e}")
            try:
                _sync_project_config_to_channels(proj)
            except Exception as e:
                print(f"[PackagingFlow] 切项目配置同步异常 (隔离): {e}")
            return True
        finally:
            db.close()
    except Exception as e:
        print(f"[PackagingFlow] 自动切项目钩子异常 (隔离): {e}")
        return False


def _real_paper_order_probe(channel_id: int, step_label: Optional[str]) -> bool:
    """探测当前通道"放工单"步骤本周期是否已 covered (尾箱塞工单 gate).

    拿不到工位 / 工位不支持探测 → False (gate 守住, 等真正放了工单再放行).
    """
    try:
        from backend.api.channel_manager import get_channel_manager
        mgr = get_channel_manager().channels.get(int(channel_id))
        if mgr is None:
            return False
        probe = getattr(mgr, "is_packaging_paper_order_covered", None)
        if probe is None:
            return False
        if bool(probe(step_label)):
            return True
        # v3.34.1: 上面的探测优先读"上一个已结算周期"的步骤缓存, 会遮蔽当前还没结算的
        # 周期 — 尾箱挂起等放工单期间, 补放动作恰恰落在当前周期里, 这里补查实时步骤集.
        if step_label:
            live = getattr(mgr, "current_cycle_steps", None) or []
            return step_label in live
        return False
    except Exception as e:
        print(f"[PackagingFlow] 塞工单探测钩子异常 (隔离): {e}")
        return False


def _real_box_progress_getter(channel_id: int) -> Optional[int]:
    """当前箱实时已进箱件数 (提前放工单判定用). 拿不到 / 非容器混合 → None (保守放行)."""
    try:
        from backend.api.channel_manager import get_channel_manager
        mgr = get_channel_manager().channels.get(int(channel_id))
        if mgr is None:
            return None
        mix = getattr(mgr, "_custom_mix", None)
        if mix is None:
            return None
        total = mix.container_settled_item_total()
        return int(total) if total is not None else None
    except Exception as e:
        print(f"[PackagingFlow] 进箱数探测钩子异常 (隔离): {e}")
        return None


def wire_real_hooks(coord: Optional["PackagingFlowCoordinator"] = None) -> None:
    """把真实拉单 / 报警 / 回推 / 箱目标 / 切项目 / 塞工单探测 / 进箱数钩子注入协调器. 启动时调一次."""
    coord = coord or get_coordinator()
    coord.set_mes_fetcher(_real_mes_fetcher)
    coord.set_alarm_sink(_real_alarm_sink)
    coord.set_mes_pusher(_real_mes_pusher)
    coord.set_box_target_setter(_real_box_target_setter)
    coord.set_project_activator(_real_project_activator)
    coord.set_paper_order_probe(_real_paper_order_probe)
    coord.set_box_progress_getter(_real_box_progress_getter)
