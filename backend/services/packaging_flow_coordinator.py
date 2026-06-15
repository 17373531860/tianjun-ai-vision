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

import threading
import uuid
from typing import Any, Callable, Dict, List, Optional


_singleton: Optional["PackagingFlowCoordinator"] = None
_singleton_lock = threading.Lock()


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

    # =============================================================
    # 依赖注入 (M3 启动时 set 真实实现; 单测 set mock)
    # =============================================================

    def set_mes_fetcher(self, fn) -> None:
        self._mes_fetcher = fn

    def set_alarm_sink(self, fn) -> None:
        self._alarm_sink = fn

    def set_mes_pusher(self, fn) -> None:
        self._mes_pusher = fn

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
        }

    # =============================================================
    # 标签归一化 + 规格量解析
    # =============================================================

    @staticmethod
    def _normalize(code: str, cfg: Dict[str, Any]) -> str:
        """按配置把扫码原始串归一化, 用于工单/箱标签比对."""
        s = str(code or "").strip()
        mode = cfg.get("label_match", "strip_hyphen")
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
        try:
            if self._alarm_sink:
                self._alarm_sink(cfg, kind, msg)
            else:
                print(f"[PackagingFlow][ALARM:{kind}] {cfg.get('name')}: {msg}")
        except Exception as e:
            print(f"[PackagingFlow] alarm_sink 异常 (隔离): {e}")

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

            if run is None:
                # 第 1 次扫 = 开工单
                self._open_order(config_id, cfg, norm, code, db)
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

    def on_cycle_settled(self, channel_id: int, cycle_id: int, is_good: bool, db) -> None:
        with self._lock:
            config_id = self._channel_to_config.get(channel_id)
            if config_id is None:
                return  # 通道未参与包装结算 → 零差异
            cfg = self._configs.get(config_id)
            run = self._runs.get(config_id)
            if cfg is None or run is None or run["current_box_index"] == 0:
                return  # 没开工单 / 没开箱时来的托盘 → 忽略
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

    def on_forced_settle(self, config_id: int, db) -> None:
        """强制停止 / 待机: 按 on_forced_stop 策略处置进行中工单.

        settle = 收尾结算 (未满箱按 on_forced_stop_partial 判合格性) + 完成工单
        abort  = 直接作废 (不结算, 标 aborted)
        keep   = 原样保留进行中 (待恢复继续, 不动状态)
        """
        with self._lock:
            cfg = self._configs.get(config_id)
            run = self._runs.get(config_id)
            if cfg is None or run is None:
                return
            strategy = cfg.get("on_forced_stop", "settle")
            if strategy == "keep":
                return
            if strategy == "abort":
                self._abort_order(run, db)
                return
            # settle: 结算当前未结算的箱 + 完成工单
            if run["current_box_index"] > run["box_done"]:
                self._settle_current_box(run, cfg, db,
                                         force_partial=cfg.get("on_forced_stop_partial", "fail"))
            self._complete_order(run, cfg, db, forced=True)

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
                return  # 阻断: 不开工单
            mes_data = {}  # offline: 继续, 箱数未知 (0)
        spec = mes_data.get("spec")
        box_total = self._resolve_box_total(cfg, mes_data, spec)
        run = self._new_run_dict(config_id, norm, raw, spec, box_total)
        self._persist_run(run, db, create=True)
        self._runs[config_id] = run

    def _open_box(self, run: Dict[str, Any], cfg: Dict[str, Any], db) -> None:
        if run["box_total"] > 0 and run["box_done"] >= run["box_total"]:
            self._raise_alarm(cfg, "over_box",
                              f"工单 {run['order_no']} 已做满 {run['box_total']} 箱, 不再开新箱 (多箱)")
            self._persist_run(run, db)
            return
        run["current_box_index"] = run["box_done"] + 1
        run["current_box_trays"] = 0
        run["status"] = "running"
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
        self._persist_run(run, db)

    def _complete_order(self, run: Dict[str, Any], cfg: Dict[str, Any], db, forced: bool = False) -> None:
        # 收尾前结算最后一个未结算箱
        if run["current_box_index"] > run["box_done"]:
            self._settle_current_box(run, cfg, db,
                                     force_partial=(cfg.get("on_forced_stop_partial") if forced else None))
        final = "OK"
        if run["box_ng"] > 0:
            final = "NG"
        if run["box_total"] > 0 and run["box_done"] < run["box_total"]:
            final = "NG"  # 漏箱
        run["final_result"] = final
        run["status"] = "completed"
        # 工单完成回推 MES (可选, 默认关). 推成功才置 mes_pushed, 落库留痕.
        if self._push_mes(cfg, run):
            run["mes_pushed"] = True
        self._persist_run(run, db)
        self._runs.pop(run["config_id"], None)

    def _abort_order(self, run: Dict[str, Any], db) -> None:
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
            "box_total": int(box_total or 0),
            "box_done": 0,
            "box_ng": 0,
            "current_box_index": 0,
            "current_box_trays": 0,
            "box_details": [],
            "final_result": None,
            "mes_pushed": False,
            "status": "order_loaded",
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
            self._configs.clear()
            self._channel_to_config.clear()
            self._runs.clear()
            self._mes_fetcher = None
            self._alarm_sink = None
            self._mes_pusher = None


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
}


def _real_alarm_sink(cfg: Dict[str, Any], kind: str, msg: str) -> None:
    """包装结算异常 → 触发配置指定的项目事件 (复用其报警/语音/Toast/计数), 不打断检测周期.

    每种异常在配置里可挑一个「项目事件设置」里的事件 id; 命中则借该工位 VSM 的
    ``fire_external_event_response`` 联动报警+语音+Toast+计数 (不结算检测周期).
    未配置 / 事件没找到 / 拿不到工位 → 退回默认通用报警 (event2), 不静默以免漏报.
    """
    try:
        from backend.core.debug_center import debug_center
        debug_center.dbg("backend.packaging", f"报警[{kind}]", msg)
    except Exception:
        pass
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


def wire_real_hooks(coord: Optional["PackagingFlowCoordinator"] = None) -> None:
    """把真实拉单 / 报警 / 回推钩子注入协调器. 启动时调一次."""
    coord = coord or get_coordinator()
    coord.set_mes_fetcher(_real_mes_fetcher)
    coord.set_alarm_sink(_real_alarm_sink)
    coord.set_mes_pusher(_real_mes_pusher)
