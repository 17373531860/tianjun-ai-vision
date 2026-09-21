"""
周期多码采集引擎（Scan Collect Engine）— v3.56

一个工件周期内采集多个分类码（槽位制），收尾码结算 OK/NG：
- 分类入槽：正则优先（多槽命中取第一个未满槽），正则不中按槽位顺序"依次填坑"
- 去重：本组内重复码（默认借 NG 事件报警并拒收）+ 可选跨工件历史重复
- 数量门：扫收尾码时任一槽位未凑齐 → NG（少扫）；槽位超量按策略拒收/报警
- 结算：借 VSM fire_external_event_response 事件响应面（灯/Toast/语音/计数器，
  不动检测周期 —— 与称重引擎同款姿势），逐码记录落 scan_collect_records，
  收尾码注册 Workpiece 供追溯，触发 scan_group_end 实时导出（txt 分类落盘）

接入点：
- mes_hooks._handle_scan 顶部优先路径（照 WorkpieceFlow 先例，互斥于单码绑定）
- channel_manager.set_channel_count → on_channel_removed（不变量 #4 配套清理）
- source_routes 检测结果载荷 scan_collect 段（Monitor 已扫列表轮询）

线程模型：on_scan 在 mes-hook-worker 单线程跑；纠错端点在 API 线程；
超时结算在 Timer 线程 —— 全部状态变更走 self._lock。
"""
from __future__ import annotations

import re
import threading
import traceback
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.models.scan_collect_models import ScanCollectConfig, ScanCollectRecord

DEFAULT_CONFIG: Dict[str, Any] = {
    "slots": [],
    "sequence_fallback": True,
    "dedup_in_group": "ng_alarm",
    "dedup_cross_group": "off",
    "settle_on": "closing",
    "on_overflow": "reject",
    "on_unmatched": "reject",
    "timeout_sec": 0,
    "event_ok_id": 1,
    "event_ng_id": 2,
    # v3.56.1 现场确认单增补 (全部默认关 = 存量行为零差异):
    # ng_pending: 少扫收尾不立即结算, 报警后组挂起 — 补扫缺码转 OK /
    #             人工"按NG放行"结算; 挂起期间多余码照 overflow 策略拦住
    "ng_pending": False,
    # vision_gate: 视觉+扫码双重验证 — 扫码侧 OK 时还要看本通道视觉周期
    #              最近一次判定 (窗口内), 视觉 NG 则整组 NG; 工件始末以扫码为准
    "vision_gate": False,
    "vision_window_sec": 300,
    "vision_missing": "ignore",  # ignore=无视觉结果按扫码判 | ng=缺视觉结果判 NG
    # v3.56.1b 六和现场反馈增补 (默认关 = 存量行为零差异):
    # idle_remind_sec: 催扫提醒 — 组开着且 N 秒无新码 → 借 NG 事件提醒面
    #   (灯/蜂鸣/Toast, remind_only 不计数不定格), 每 N 秒重复催直到扫码/结算/清空。
    #   与 timeout_sec (超时直接判 NG 结组) 相互独立, 可同时开。
    "idle_remind_sec": 0,
    # standby_silent: 待机静默 — 通道未在检测时, 扫码照常入槽/结算/追溯落库,
    #   但结算不借事件面 (不计数不亮灯) 也不派发 txt 导出 (现场答复 6=b)。
    "standby_silent": False,
    # count_on_settle: 结算是否执行事件上的计数动作。六和现场"一个工件结算两次":
    #   视觉 SOP 周期结算触发事件 1/2 计一次, 扫码组结算借同一对事件又计一次
    #   → 检测次数=产量×2。关掉后扫码结算只借 灯/语音/Toast (remind_only 档,
    #   同时跳过人工确认定格), 计数交给视觉周期 — 一件一次 (现场答复 5)。
    #   默认 True = 存量行为零差异 (纯扫码无视觉的工位靠它计数)。
    "count_on_settle": True,
    # v3.60.2 六和现场反馈: 视觉末步 (盖模具盖板) 结算为 OK 后, 少扫组还挂在
    # 面板上不翻篇 (收尾码没扫 → closing 结算永远不来)。开启后视觉周期结算
    # 即强制收口扫码组:
    #   - 缺码 → 本视觉周期直接判 NG (缺码原因并入周期 NG reason, 一件一账),
    #     扫码组按 ng_missing 落库导出关组 (跳过挂起, 不借事件面避免双报)
    #   - 码齐 → 组按 OK 收口 (常规时序下收尾码已提前结组, 此分支仅兜底)
    # 默认 False = 存量行为零差异。流程约束: 开启后收尾码必须在视觉末步
    # 完成前扫 (先扫码后盖盖板), 盖完才扫会被判少扫。
    "settle_on_vision_cycle": False,
}

# 槽位级可覆盖键 (值 "inherit"/缺省 = 用全局): dedup_cross_group / on_overflow
#   现场范式: 工装码是循环治具跨工件必然重复 → 该槽 dedup_cross_group="off"
#             芯子多扫直接判 NG、多扫母排(=忘收尾开新件)只拦不判 → 槽级 on_overflow


class _GroupState:
    """一个通道当前在采的"码组"（≈一个工件）。"""

    def __init__(self, project_id: int):
        self.group_id: str = uuid.uuid4().hex
        self.project_id = project_id
        self.started_at = datetime.now()
        self.last_scan_at = self.started_at
        self.seq = 0
        # [{record_id, slot_key, slot_label, code, seq, ts}]
        self.codes: List[Dict[str, Any]] = []
        self.timer: Optional[threading.Timer] = None
        # v3.56.1b 催扫提醒定时器 (与 timer=超时判NG 相互独立)
        self.remind_timer: Optional[threading.Timer] = None
        # v3.56.1 NG 挂起态: {"reason", "missing"} — 少扫收尾后等补扫/人工放行
        self.pending_ng: Optional[Dict[str, Any]] = None

    def slot_count(self, slot_key: str) -> int:
        return sum(1 for c in self.codes if c["slot_key"] == slot_key)

    def has_code(self, code: str) -> bool:
        return any(c["code"] == code for c in self.codes)


class ScanCollectEngine:
    """单例。配置按项目缓存；组状态按通道隔离。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._groups: Dict[int, _GroupState] = {}
        # 最近一次结算摘要（结算后组已清空，Monitor 还要能看到上一件的结果）
        self._last_settled: Dict[int, Dict[str, Any]] = {}
        # project_id -> {"enabled": bool, "config": dict} ；None 值=查过但没配置
        self._cfg_cache: Dict[int, Optional[Dict[str, Any]]] = {}
        # v3.56.1 视觉门: 每通道最近一次视觉周期判定 {is_good, reason, ts}
        # (mes_hooks._handle_cycle_end 喂入, 仅 vision_gate 开启的项目消费)
        self._vision_last: Dict[int, Dict[str, Any]] = {}

    # ============================================================
    # 配置
    # ============================================================

    def get_config(self, db: Optional[Session],
                   project_id: Optional[int]) -> Optional[Dict[str, Any]]:
        """返回启用中的合并配置；未配置/未启用返回 None。

        db 可传 None：缓存未命中时内部临时开会话（Monitor 高频轮询走缓存零开销）。
        """
        if not project_id:
            return None
        with self._lock:
            if project_id in self._cfg_cache:
                cached = self._cfg_cache[project_id]
            else:
                cached = None
                own_db = None
                try:
                    if db is None:
                        from backend.db.database import SessionLocal
                        own_db = SessionLocal()
                        db = own_db
                    row = (db.query(ScanCollectConfig)
                           .filter(ScanCollectConfig.project_id == project_id)
                           .first())
                    if row is not None:
                        cached = {"enabled": bool(row.enabled),
                                  "config": dict(row.config or {})}
                except Exception as e:
                    print(f"[ScanCollect] 读配置失败 project={project_id}: {e}", flush=True)
                    return None  # 读库失败不缓存，下次再试
                finally:
                    if own_db is not None:
                        own_db.close()
                self._cfg_cache[project_id] = cached
            if not cached or not cached.get("enabled"):
                return None
            merged = dict(DEFAULT_CONFIG)
            merged.update(cached.get("config") or {})
            if not merged.get("slots"):
                return None
            return merged

    def invalidate_config(self, project_id: Optional[int] = None):
        with self._lock:
            if project_id is None:
                self._cfg_cache.clear()
            else:
                self._cfg_cache.pop(project_id, None)

    def handles(self, db: Session, channel_id: int, project_id: Optional[int]) -> bool:
        return self.get_config(db, project_id) is not None

    # ============================================================
    # 扫码主入口（mes-hook-worker 线程）
    # ============================================================

    def on_scan(self, db: Session, channel_id: int, code: str, raw: str,
                project_id: int, device_id: Optional[int] = None) -> Dict[str, Any]:
        """处理一次扫码。返回 {accepted, msg} 供 ScanLog 审计。"""
        cfg = self.get_config(db, project_id)
        if cfg is None:
            return {"accepted": False, "msg": "多码采集未启用"}
        code = (code or "").strip()
        if not code:
            return {"accepted": False, "msg": "空码"}

        with self._lock:
            state = self._groups.get(channel_id)
            if state is not None and state.project_id != project_id:
                # 切项目残留的组：作废（防跨项目串账）
                self._void_group(db, state, reason="切换项目作废")
                state = None
            if state is None:
                state = _GroupState(project_id)
                self._groups[channel_id] = state

            # ---- 去重：本组内 ----
            if state.has_code(code):
                return self._reject(db, channel_id, cfg, code,
                                    policy=cfg.get("dedup_in_group", "ng_alarm"),
                                    reason=f"重复扫码：{code} 本工件内已扫过")

            # ---- 分类入槽（先分类再跨组去重, 槽位级策略要知道码属于哪类） ----
            slot, how, ovf_slot = self._classify(cfg, state, code)
            if slot is None:
                if how == "overflow":
                    # 槽位级多扫策略覆盖 (如: 芯子多扫判 NG / 多扫母排只拦提示)
                    policy = self._slot_policy(ovf_slot, "on_overflow",
                                               cfg.get("on_overflow", "reject"))
                    label = (ovf_slot or {}).get("label") or "对应槽位"
                    extra = "（当前工件挂起处理中）" if state.pending_ng else ""
                    return self._reject(db, channel_id, cfg, code,
                                        policy=policy,
                                        reason=f"超出应扫数量：{code} {label}已扫满{extra}")
                return self._reject(db, channel_id, cfg, code,
                                    policy=cfg.get("on_unmatched", "reject"),
                                    reason=f"无法识别的码：{code} 不属于任何类别")

            # ---- 去重：跨工件历史（槽位可豁免, 如循环使用的工装码） ----
            cross = self._slot_policy(slot, "dedup_cross_group",
                                      cfg.get("dedup_cross_group", "off"))
            if cross != "off" and self._seen_before(db, project_id, code, state.group_id):
                return self._reject(db, channel_id, cfg, code,
                                    policy=cross,
                                    reason=f"重复扫码：{code} 在历史工件中已使用")

            # ---- 入组落库 ----
            state.seq += 1
            state.last_scan_at = datetime.now()
            rec = ScanCollectRecord(
                group_id=state.group_id, channel_id=channel_id,
                project_id=project_id, slot_key=slot["key"],
                slot_label=slot.get("label") or slot["key"],
                code=code, seq=state.seq, status="scanned",
                scanned_at=state.last_scan_at,
            )
            try:
                db.add(rec)
                db.flush()
                record_id = rec.id
            except Exception as e:
                db.rollback()
                print(f"[ScanCollect] 记录落库失败 ch{channel_id} {code}: {e}", flush=True)
                record_id = None
            state.codes.append({
                "record_id": record_id, "slot_key": slot["key"],
                "slot_label": slot.get("label") or slot["key"],
                "code": code, "seq": state.seq,
                "ts": state.last_scan_at.strftime("%H:%M:%S"),
                # v3.56.1b: NG 挂起期间补扫的码打标记 (txt 落盘带 [补扫], 现场答复 7)
                "remedied": bool(state.pending_ng),
            })
            try:
                db.commit()
            except Exception:
                db.rollback()

            # ---- 结算判定 ----
            is_closing = (slot.get("role") == "closing")
            settle_on = cfg.get("settle_on", "closing")
            if cfg.get("settle_on_vision_cycle"):
                # v3.60.1c: 结算主权归视觉周期 — 收尾码/码齐/超时都不自行
                # 触发结算, 码只进组, 组一直开到视觉末步结算收口(码够 OK/
                # 不够 NG, 无条件翻篇)。挂起/超时结算全部让位。
                # 现场事故复盘(2026-09-21 六和): closing 触发的 NG 挂起把组
                # 占住 → 下一件母排码被"超出应扫数量"拒收 → 码从此对应不上
                # 工件, 级联混码; 催扫提醒(_arm_remind)保留不受影响。
                pass
            elif state.pending_ng:
                # NG 挂起中的补扫: 缺码补齐即转 OK 结算 (收尾码此前已扫)
                if self._all_filled(cfg, state):
                    self._settle(db, channel_id, cfg, state, trigger="closing")
            elif settle_on == "closing" and is_closing:
                self._settle(db, channel_id, cfg, state, trigger="closing")
            elif settle_on == "all_filled" and self._all_filled(cfg, state):
                self._settle(db, channel_id, cfg, state, trigger="all_filled")
            else:
                self._arm_timer(channel_id, cfg, state)
            # v3.56.1b 催扫提醒: 组仍开着(含挂起) → 重置空闲计时
            if self._groups.get(channel_id) is state:
                self._arm_remind(channel_id, cfg, state)
            return {"accepted": True,
                    "msg": f"入槽 {slot.get('label') or slot['key']} ({state.seq})"}

    @staticmethod
    def _slot_policy(slot: Optional[Dict], key: str, global_value: str) -> str:
        """槽位级策略覆盖: 槽位值缺省/"inherit" 用全局, 否则用槽位自己的。"""
        if not slot:
            return global_value
        v = (slot.get(key) or "").strip()
        return v if v and v != "inherit" else global_value

    # ============================================================
    # 分类
    # ============================================================

    def _classify(self, cfg: Dict, state: _GroupState,
                  code: str) -> Tuple[Optional[Dict], str, Optional[Dict]]:
        """返回 (入槽的槽位, 判定方式, 溢出槽位)。

        溢出槽位仅 how=="overflow" 时有值 (正则命中但已满的第一个槽),
        供槽位级 on_overflow 策略取用; 顺序兜底全满的溢出无特定槽位。
        """
        slots = cfg.get("slots") or []
        matched = []
        for s in slots:
            pattern = (s.get("regex") or "").strip()
            if not pattern:
                continue
            try:
                if re.search(pattern, code):
                    matched.append(s)
            except re.error:
                continue  # 坏正则视为不匹配（配置端已校验，这里兜底）
        if matched:
            for s in matched:
                if state.slot_count(s["key"]) < int(s.get("count") or 1):
                    return s, "regex", None
            return None, "overflow", matched[0]
        if cfg.get("sequence_fallback", True):
            has_regex_slot = False
            for s in slots:
                if (s.get("regex") or "").strip():
                    has_regex_slot = True
                    continue  # 有正则的槽位只收匹配它的码
                if state.slot_count(s["key"]) < int(s.get("count") or 1):
                    return s, "seq", None
            return None, ("unmatched" if has_regex_slot else "overflow"), None
        return None, "unmatched", None

    def _all_filled(self, cfg: Dict, state: _GroupState) -> bool:
        for s in (cfg.get("slots") or []):
            if state.slot_count(s["key"]) < int(s.get("count") or 1):
                return False
        return True

    def _missing_detail(self, cfg: Dict, state: _GroupState) -> List[Dict[str, Any]]:
        out = []
        for s in (cfg.get("slots") or []):
            expected = int(s.get("count") or 1)
            got = state.slot_count(s["key"])
            if got < expected:
                out.append({"slot_key": s["key"],
                            "label": s.get("label") or s["key"],
                            "expected": expected, "got": got})
        return out

    # ============================================================
    # 拒收 / 报警
    # ============================================================

    def _reject(self, db: Session, channel_id: int, cfg: Dict, code: str,
                *, policy: str, reason: str) -> Dict[str, Any]:
        self._warn(channel_id, code, reason)
        if policy == "ng_alarm":
            self._fire_event(channel_id, cfg.get("event_ng_id", 2), reason)
        print(f"[ScanCollect] 拒收 ch{channel_id} {code}: {reason} (policy={policy})",
              flush=True)
        return {"accepted": False, "msg": reason}

    def _warn(self, channel_id: int, code: str, reason: str):
        """借 MES Hook 的扫码警告 toast 通路（前端 handleScanToast）。"""
        try:
            from backend.services.mes_hooks import get_mes_hook
            get_mes_hook()._emit_scan_warning(channel_id, code, reason)
        except Exception as e:
            print(f"[ScanCollect] warn toast 失败(隔离): {e}", flush=True)

    def _fire_event(self, channel_id: int, event_id, reason: str,
                    remind_only: bool = False) -> bool:
        """借事件响应面（灯/Toast/语音/计数器），不动检测周期。

        remind_only=True: 催扫提醒档 — 只借灯/蜂鸣/Toast, 跳过计数器与
        人工确认定格 (同 v3.45 称重过程提醒姿势, 否则催一次 NG 计数 +1)。
        """
        if not event_id:
            return False
        try:
            from backend.api.channel_manager import get_channel_manager
            mgr = get_channel_manager().get(channel_id)
            if hasattr(mgr, "fire_external_event_response"):
                return bool(mgr.fire_external_event_response(
                    event_id, reason, source="scan_collect",
                    remind_only=remind_only))
        except Exception as e:
            print(f"[ScanCollect] fire_event 失败(隔离) ch{channel_id} "
                  f"event={event_id}: {e}", flush=True)
        return False

    def _channel_detecting(self, channel_id: int) -> bool:
        """待机静默档用: 拿不到状态按检测中处理 (保守 = 照常计数/导出)。"""
        try:
            from backend.api.channel_manager import get_channel_manager
            mgr = get_channel_manager().get(channel_id)
            return bool(getattr(mgr, "is_detecting", True))
        except Exception:
            return True

    # ============================================================
    # 结算
    # ============================================================

    def _settle(self, db: Session, channel_id: int, cfg: Dict,
                state: _GroupState, *, trigger: str, allow_pending: bool = True,
                fire_events: bool = True):
        """收尾结算（调用方已持锁）。trigger: closing / all_filled / timeout
        / manual / vision_cycle (v3.60.2 随视觉周期收口)

        allow_pending=False 用于人工"按NG放行"与视觉周期收口: 跳过挂起强制出结果。
        fire_events=False 用于视觉周期收口: 计数/灯/语音已由视觉周期事件承担。
        """
        missing = self._missing_detail(cfg, state)
        # v3.56.1b 待机静默: 待机时结算不借事件面(不计数不亮灯)、不派发导出
        standby = bool(cfg.get("standby_silent")) \
            and not self._channel_detecting(channel_id)

        # ---- v3.56.1 NG 挂起 (默认关): 少扫收尾不关组, 报警后等补扫/人工放行 ----
        # v3.56.1b: trigger 扩到 manual (面板「本件扫完」按钮同样走挂起补扫);
        #   挂起报警改 remind_only 提醒档 — 此刻还不是最终判定, 借完整 NG 事件面
        #   会 不良+1/总产量+1, 补扫转 OK 后再 OK 计数 = 一件计两次 (v3.56.0 缺陷)
        if (missing and trigger in ("closing", "manual") and allow_pending
                and cfg.get("ng_pending") and not state.pending_ng):
            miss_txt = "、".join(f"{m['label']}缺{m['expected'] - m['got']}"
                                 for m in missing)
            reason = f"少扫 NG 挂起：{miss_txt}，请补扫缺码或按 NG 放行"
            state.pending_ng = {"reason": reason, "missing": missing,
                                "since": datetime.now().strftime("%H:%M:%S")}
            if not standby:
                self._fire_event(channel_id, cfg.get("event_ng_id", 2), reason,
                                 remind_only=True)
            self._warn(channel_id, "", reason)
            print(f"[ScanCollect] NG挂起 ch{channel_id} group={state.group_id}: "
                  f"{reason}", flush=True)
            return

        self._cancel_timer(state)
        self._cancel_remind(state)
        if trigger == "timeout":
            verdict, result_key = False, "ng_timeout"
            reason = "扫码超时未收尾，本工件判 NG"
        elif missing:
            verdict, result_key = False, "ng_missing"
            miss_txt = "、".join(f"{m['label']}缺{m['expected'] - m['got']}"
                                 for m in missing)
            reason = (f"随视觉周期收口，少扫判 NG：{miss_txt}"
                      if trigger == "vision_cycle" else f"少扫判 NG：{miss_txt}")
        else:
            verdict, result_key = True, "ok"
            reason = f"多码采集完成，共 {len(state.codes)} 码"
            if state.pending_ng:
                reason = f"补扫齐全转 OK，共 {len(state.codes)} 码"

        # ---- v3.56.1 视觉双重验证 (默认关): 扫码 OK 还要视觉侧也 OK ----
        # v3.60.1c: 随视觉周期收口时跳过 — _vision_last 由 _handle_cycle_end
        # 异步回喂, 收口线程跑在钩子之前, 窗口里还是上一件的判定, 开着会把
        # 上件 NG 连坐本件 (或上件 OK 把本件少扫漏过去)。视觉对扫码的影响
        # 已经由 peek_vision_settle_gate 并入周期事件, 组结果只反映扫码齐缺。
        vision_info = None
        if cfg.get("vision_gate") and trigger != "vision_cycle":
            vision_info, verdict, result_key, reason = self._apply_vision_gate(
                cfg, channel_id, verdict, result_key, reason)

        closing_code = next(
            (c["code"] for c in reversed(state.codes)
             if self._slot_role(cfg, c["slot_key"]) == "closing"), None)
        workpiece_sn = closing_code or f"SC-{state.group_id[:8].upper()}"

        # 注册工件（收尾码为身份），供追溯页反查
        workpiece_id = None
        try:
            from backend.services.workpiece import WorkpieceService
            svc = WorkpieceService()
            wp = svc.register(db, workpiece_sn, state.project_id,
                              channel_id=channel_id, scan_source="scanner",
                              raw_barcode=closing_code)
            wp.status = "ok" if verdict else "ng"
            wp.last_inspect_at = datetime.now()
            if not wp.first_inspect_at:
                wp.first_inspect_at = wp.last_inspect_at
            db.flush()
            workpiece_id = wp.id
        except Exception as e:
            db.rollback()
            print(f"[ScanCollect] 工件注册失败(隔离) {workpiece_sn}: {e}", flush=True)

        # 回填逐码记录
        settled_at = datetime.now()
        try:
            record_ids = [c["record_id"] for c in state.codes if c["record_id"]]
            if record_ids:
                (db.query(ScanCollectRecord)
                 .filter(ScanCollectRecord.id.in_(record_ids))
                 .update({"group_result": result_key,
                          "workpiece_id": workpiece_id,
                          "settled_at": settled_at},
                         synchronize_session=False))
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"[ScanCollect] 结算回填失败 group={state.group_id}: {e}", flush=True)

        # 事件响应（灯/Toast/计数器）— 最终判定只在这里计一次数;
        # 挂起进入的报警是 remind_only 提醒档不计数 (v3.56.1b)。待机静默跳过。
        # count_on_settle=False: 只借灯/语音/Toast 不计数 (视觉周期已计过,
        # 否则视觉+扫码同借事件 1/2 → 一件计两次, 2026-09-07 六和现场)。
        if not standby and fire_events:
            event_id = cfg.get("event_ok_id", 1) if verdict else cfg.get("event_ng_id", 2)
            self._fire_event(channel_id, event_id, reason,
                             remind_only=not cfg.get("count_on_settle", True))

        summary = {
            "group_id": state.group_id,
            "result": result_key,
            "is_good": verdict,
            "reason": reason,
            "workpiece_sn": workpiece_sn,
            "workpiece_id": workpiece_id,
            "total": len(state.codes),
            "missing": missing,
            "settled_at": settled_at.strftime("%H:%M:%S"),
            "codes": list(state.codes),
            "vision": vision_info,
            # v3.56.1b: 补救留痕 (txt 模板消费) + 待机静默标记
            "was_pending": bool(state.pending_ng),
            "standby": standby,
        }
        self._last_settled[channel_id] = summary
        self._groups.pop(channel_id, None)
        print(f"[ScanCollect] 结算 ch{channel_id} group={state.group_id} "
              f"{result_key}: {reason} wp={workpiece_sn}"
              f"{' [待机静默]' if standby else ''}", flush=True)

        # 实时导出（txt 分类落盘）— 与结算解耦，失败不连坐; 待机静默不落盘
        if not standby:
            try:
                self._dispatch_export(db, channel_id, cfg, state, summary)
            except Exception:
                print(f"[ScanCollect] 导出派发失败(隔离):\n{traceback.format_exc()}",
                      flush=True)

    def _slot_role(self, cfg: Dict, slot_key: str) -> str:
        for s in (cfg.get("slots") or []):
            if s["key"] == slot_key:
                return s.get("role") or ""
        return ""

    # ============================================================
    # 视觉双重验证 (v3.56.1, 默认关)
    # ============================================================

    def on_vision_cycle(self, channel_id: int, is_good: bool, reason: str = ""):
        """mes_hooks._handle_cycle_end 喂入每次视觉周期判定 (纯内存, 零开销)。"""
        with self._lock:
            self._vision_last[channel_id] = {
                "is_good": bool(is_good), "reason": reason or "",
                "ts": datetime.now(),
            }

    # ============================================================
    # v3.60.2 随视觉周期结算 (settle_on_vision_cycle, 默认关)
    # ============================================================

    def peek_vision_settle_gate(self, channel_id: int,
                                cycle_anchor_ts: Optional[datetime] = None,
                                ) -> Optional[str]:
        """推理线程探针: 视觉周期结算瞬间查扫码组缺码状态。

        返回缺码文案 (并入周期 NG reason) 或 None (无组/码齐/未开启)。
        ⚠ 推理线程调用 — 必须零阻塞: try-lock 拿不到 (恰逢扫码线程持锁结算,
        说明组正在关) 即返回 None; 配置只读 _cfg_cache 纯内存 (组存在则扫码
        路径必然已灌缓存), 绝不碰 DB。

        cycle_anchor_ts (v3.60.1c 组归属锚): 本视觉周期末步(如模具盖板)首次
        出现时间。开于锚点之后的组是下一件的码组(上一件末步消失确认的空档里
        操作员已在扫下一件母排码), 不参与本周期判定 — v3.60.1a 现场事故:
        无锚探针拿下一件的缺码把本件视觉 OK 翻成 NG。
        """
        if not self._lock.acquire(blocking=False):
            return None
        try:
            state = self._groups.get(channel_id)
            if state is None or not state.codes:
                return None
            if cycle_anchor_ts is not None and state.started_at > cycle_anchor_ts:
                return None   # 锚点之后才开的组 = 下一件的, 不碰
            cached = self._cfg_cache.get(state.project_id)
            if not cached or not cached.get("enabled"):
                return None
            cfg = cached.get("config") or {}
            if not cfg.get("settle_on_vision_cycle"):
                return None
            missing = self._missing_detail(cfg, state)
            if not missing:
                return None
            miss_txt = "、".join(f"{m['label']}缺{m['expected'] - m['got']}"
                                 for m in missing)
            return f"少扫码: {miss_txt}"
        finally:
            self._lock.release()

    def has_group_for_vision_settle(self, channel_id: int) -> bool:
        """纯内存零阻塞守门: 当前是否有开着"随视觉周期结算"的码组。

        视觉结算点起收口线程前的开销守门 (开关默认关的项目不起线程不碰 DB)。
        try-lock 拿不到 → 组态不明, 返回 True 让幂等的收口线程自行判空。
        """
        if not self._lock.acquire(blocking=False):
            return True
        try:
            state = self._groups.get(channel_id)
            if state is None or not state.codes:
                return False
            cached = self._cfg_cache.get(state.project_id)
            if not cached or not cached.get("enabled"):
                return False
            return bool((cached.get("config") or {})
                        .get("settle_on_vision_cycle"))
        finally:
            self._lock.release()

    def settle_by_vision_cycle(self, db: Session, channel_id: int,
                               cycle_anchor_ts: Optional[datetime] = None,
                               ) -> bool:
        """视觉周期结算瞬间收口扫码组 (视觉结算侧后台线程调用)。

        码够 → ok; 不够 → ng_missing 落库导出, 无条件关组翻篇 (跳过挂起,
        下一件的码永不被旧组拒收)。
        不借事件面 (fire_events=False): 计数/灯/语音已由视觉周期事件承担,
        这里再报一次 = 一件双报 (v3.56.1b count_on_settle 同款教训)。
        未开启 settle_on_vision_cycle / 无组 / 组开于锚点之后(下一件的,
        见 peek_vision_settle_gate) 时零操作。
        """
        with self._lock:
            state = self._groups.get(channel_id)
            if state is None or not state.codes:
                return False
            if cycle_anchor_ts is not None and state.started_at > cycle_anchor_ts:
                return False   # 下一件的组, 留给下个周期收口
            cfg = self.get_config(db, state.project_id)
            if cfg is None or not cfg.get("settle_on_vision_cycle"):
                return False
            self._settle(db, channel_id, cfg, state,
                         trigger="vision_cycle", allow_pending=False,
                         fire_events=False)
            return True

    def _apply_vision_gate(self, cfg: Dict, channel_id: int, verdict: bool,
                           result_key: str, reason: str):
        """扫码判定与视觉判定融合: 两边都 OK 才 OK (工件始末以扫码为准)。

        返回 (vision_info, verdict, result_key, reason)。
        扫码侧已 NG 时不再看视觉 (NG 优先, 只补记视觉信息)。
        """
        v = self._vision_last.get(channel_id)
        window = float(cfg.get("vision_window_sec") or 0)
        fresh = bool(v) and (window <= 0 or
                             (datetime.now() - v["ts"]).total_seconds() <= window)
        vision_info = {
            "is_good": v["is_good"] if fresh else None,
            "reason": (v.get("reason") or "") if fresh else "窗口内无视觉周期结果",
            "at": v["ts"].strftime("%H:%M:%S") if fresh else None,
        }
        if not verdict:
            return vision_info, verdict, result_key, reason
        if fresh:
            if not v["is_good"]:
                verdict, result_key = False, "ng_vision"
                reason = f"视觉检测 NG：{v.get('reason') or '装配判定不合格'}（扫码已齐）"
        elif (cfg.get("vision_missing") or "ignore") == "ng":
            verdict, result_key = False, "ng_vision_missing"
            reason = "视觉双重验证开启但窗口内无视觉周期结果，判 NG"
        return vision_info, verdict, result_key, reason

    # ============================================================
    # NG 挂起人工放行 (API 线程)
    # ============================================================

    def resolve_ng(self, db: Session, channel_id: int) -> Tuple[bool, str]:
        """人工"按NG放行": 挂起组按 NG 结算导出, 开放下一工件。"""
        with self._lock:
            state = self._groups.get(channel_id)
            if state is None or not state.pending_ng:
                return False, "当前没有挂起待处理的码组"
            cfg = self.get_config(db, state.project_id)
            if cfg is None:
                return False, "多码采集配置已停用"
            self._settle(db, channel_id, cfg, state,
                         trigger="closing", allow_pending=False)
            return True, "已按 NG 放行结算"

    def settle_now(self, db: Session, channel_id: int) -> Tuple[bool, str]:
        """v3.56.1b 面板「本件扫完」按钮: 立即按当前已扫码结算 (现场答复 3c)。

        六和范式下码序不固定、收尾码可能先扫 — 现场用「扫满结算」时少扫组
        永远开着, 这个按钮是人工收口出口: 齐 → OK; 缺码 → ng_pending 开着走
        挂起(补扫转 OK / 按 NG 放行), 关着直接判 NG。
        """
        with self._lock:
            state = self._groups.get(channel_id)
            if state is None or not state.codes:
                return False, "当前没有在采集的码组"
            if state.pending_ng:
                return False, "码组已挂起：请补扫缺码，或点「按 NG 放行」"
            cfg = self.get_config(db, state.project_id)
            if cfg is None:
                return False, "多码采集配置已停用"
            self._settle(db, channel_id, cfg, state, trigger="manual")
            if self._groups.get(channel_id) is state and state.pending_ng:
                return True, "缺码已挂起：请补扫缺码或按 NG 放行"
            return True, "已手动结算本工件"

    def _void_group(self, db: Session, state: _GroupState, *, reason: str):
        """作废一个组（不触发事件不导出）。调用方已持锁。"""
        self._cancel_timer(state)
        self._cancel_remind(state)
        try:
            record_ids = [c["record_id"] for c in state.codes if c["record_id"]]
            if record_ids:
                (db.query(ScanCollectRecord)
                 .filter(ScanCollectRecord.id.in_(record_ids))
                 .update({"status": "void", "group_result": "void"},
                         synchronize_session=False))
                db.commit()
        except Exception:
            db.rollback()
        print(f"[ScanCollect] 组作废 group={state.group_id}: {reason}", flush=True)

    # ============================================================
    # 超时
    # ============================================================

    def _arm_timer(self, channel_id: int, cfg: Dict, state: _GroupState):
        self._cancel_timer(state)
        timeout = float(cfg.get("timeout_sec") or 0)
        if timeout <= 0:
            return
        group_id = state.group_id

        def _on_timeout():
            from backend.db.database import SessionLocal
            db = SessionLocal()
            try:
                with self._lock:
                    cur = self._groups.get(channel_id)
                    if cur is None or cur.group_id != group_id or not cur.codes:
                        return
                    cfg_now = self.get_config(db, cur.project_id)
                    if cfg_now is None:
                        return
                    self._settle(db, channel_id, cfg_now, cur, trigger="timeout")
            except Exception:
                print(f"[ScanCollect] 超时结算异常:\n{traceback.format_exc()}",
                      flush=True)
            finally:
                db.close()

        t = threading.Timer(timeout, _on_timeout)
        t.daemon = True
        t.name = f"scan-collect-timeout-ch{channel_id}"
        state.timer = t
        t.start()

    def _cancel_timer(self, state: _GroupState):
        if state.timer is not None:
            try:
                state.timer.cancel()
            except Exception:
                pass
            state.timer = None

    # ============================================================
    # 催扫提醒 (v3.56.1b, 默认关)
    # ============================================================

    def _arm_remind(self, channel_id: int, cfg: Dict, state: _GroupState):
        """组开着且 N 秒无新码 → 借 NG 事件提醒面催扫 (remind_only 不计数),
        然后重新武装 = 每 N 秒重复催, 直到 扫码/结算/清空/裁撤。"""
        self._cancel_remind(state)
        interval = float(cfg.get("idle_remind_sec") or 0)
        if interval <= 0:
            return
        group_id = state.group_id

        def _on_remind():
            from backend.db.database import SessionLocal
            db = SessionLocal()
            try:
                with self._lock:
                    cur = self._groups.get(channel_id)
                    if cur is None or cur.group_id != group_id or not cur.codes:
                        return
                    cfg_now = self.get_config(db, cur.project_id)
                    if cfg_now is None:
                        return
                    idle = (datetime.now() - cur.last_scan_at).total_seconds()
                    if idle + 0.5 < interval:
                        # 期间有新码 (定时器竞态兜底), 按剩余空闲重新武装
                        self._arm_remind(channel_id, cfg_now, cur)
                        return
                    missing = self._missing_detail(cfg_now, cur)
                    miss_txt = "、".join(
                        f"{m['label']}缺{m['expected'] - m['got']}"
                        for m in missing) or "未收尾"
                    reason = (f"扫码停留提醒：{miss_txt}，已 {int(idle)} 秒无扫码"
                              f"，请继续扫码或点「本件扫完」")
                    standby = bool(cfg_now.get("standby_silent")) \
                        and not self._channel_detecting(channel_id)
                    if not standby:
                        self._fire_event(channel_id,
                                         cfg_now.get("event_ng_id", 2),
                                         reason, remind_only=True)
                    self._warn(channel_id, "", reason)
                    print(f"[ScanCollect] 催扫 ch{channel_id} "
                          f"group={cur.group_id}: {reason}", flush=True)
                    self._arm_remind(channel_id, cfg_now, cur)  # 重复催
            except Exception:
                print(f"[ScanCollect] 催扫提醒异常:\n{traceback.format_exc()}",
                      flush=True)
            finally:
                db.close()

        t = threading.Timer(interval, _on_remind)
        t.daemon = True
        t.name = f"scan-collect-remind-ch{channel_id}"
        state.remind_timer = t
        t.start()

    def _cancel_remind(self, state: _GroupState):
        if state.remind_timer is not None:
            try:
                state.remind_timer.cancel()
            except Exception:
                pass
            state.remind_timer = None

    # ============================================================
    # 纠错（API 线程）
    # ============================================================

    def remove_code(self, db: Session, channel_id: int,
                    record_id: int) -> Tuple[bool, str]:
        with self._lock:
            state = self._groups.get(channel_id)
            if state is None:
                return False, "当前没有在采集的码组"
            idx = next((i for i, c in enumerate(state.codes)
                        if c["record_id"] == record_id), None)
            if idx is None:
                return False, "该码不在当前码组中（可能已结算）"
            removed = state.codes.pop(idx)
            try:
                (db.query(ScanCollectRecord)
                 .filter(ScanCollectRecord.id == record_id)
                 .update({"status": "deleted"}, synchronize_session=False))
                db.commit()
            except Exception:
                db.rollback()
            print(f"[ScanCollect] 删码 ch{channel_id} {removed['code']} "
                  f"({removed['slot_label']})", flush=True)
            return True, f"已删除 {removed['code']}"

    def clear_group(self, db: Session, channel_id: int) -> Tuple[bool, str]:
        with self._lock:
            state = self._groups.pop(channel_id, None)
            if state is None:
                return False, "当前没有在采集的码组"
            self._void_group(db, state, reason="人工清空重扫")
            return True, f"已清空 {len(state.codes)} 个码，请重新扫码"

    # ============================================================
    # 状态查询（Monitor 轮询 / API）
    # ============================================================

    def get_state(self, db: Optional[Session], channel_id: int,
                  project_id: Optional[int]) -> Optional[Dict[str, Any]]:
        cfg = self.get_config(db, project_id)
        if cfg is None:
            return None
        with self._lock:
            state = self._groups.get(channel_id)
            slots_view = []
            total_expected = 0
            total_got = 0
            for s in (cfg.get("slots") or []):
                expected = int(s.get("count") or 1)
                total_expected += expected
                codes = ([c for c in state.codes if c["slot_key"] == s["key"]]
                         if state else [])
                total_got += len(codes)
                slots_view.append({
                    "key": s["key"],
                    "label": s.get("label") or s["key"],
                    "role": s.get("role") or "",
                    "expected": expected,
                    "got": len(codes),
                    "codes": codes,
                })
            return {
                "enabled": True,
                "group_id": state.group_id if state else None,
                "collecting": bool(state and state.codes),
                "seq": state.seq if state else 0,
                "total_expected": total_expected,
                "total_got": total_got,
                "slots": slots_view,
                "pending_ng": state.pending_ng if state else None,
                "last_settled": self._last_settled.get(channel_id),
            }

    # ============================================================
    # 生命周期
    # ============================================================

    def on_channel_removed(self, channel_id: int):
        """通道裁撤配套清理（AGENTS.md 不变量 #4）。"""
        with self._lock:
            state = self._groups.pop(channel_id, None)
            if state is not None:
                self._cancel_timer(state)
                self._cancel_remind(state)
            self._last_settled.pop(channel_id, None)
            self._vision_last.pop(channel_id, None)

    # ============================================================
    # 内部
    # ============================================================

    def _seen_before(self, db: Session, project_id: int, code: str,
                     current_group: str) -> bool:
        try:
            row = (db.query(ScanCollectRecord.id)
                   .filter(ScanCollectRecord.project_id == project_id,
                           ScanCollectRecord.code == code,
                           ScanCollectRecord.status == "scanned",
                           ScanCollectRecord.group_id != current_group)
                   .first())
            return row is not None
        except Exception:
            return False

    def _dispatch_export(self, db: Session, channel_id: int, cfg: Dict,
                         state: _GroupState, summary: Dict[str, Any]):
        from backend.services.export_realtime import dispatch_scan_group_export
        dispatch_scan_group_export(
            db, channel_id=channel_id, project_id=state.project_id,
            summary=summary, slots_cfg=cfg.get("slots") or [])


_engine: Optional[ScanCollectEngine] = None
_engine_lock = threading.Lock()


def get_scan_collect_engine() -> ScanCollectEngine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = ScanCollectEngine()
    return _engine


# PATCHED_V3601C
