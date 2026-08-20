"""
MES 外部对接网关

职责:
1. 加载所有启用的 MESConnection 配置
2. 根据 push_events 过滤, 将事件分发到对应适配器
3. 失败重试 (retry_count × retry_interval_sec)
4. 记录每次通讯到 MESCommLog
"""
import base64
import json
import time
import traceback
from datetime import datetime
from typing import Optional

from backend.db.database import SessionLocal
from backend.models.mes_models import MESConnection, MESCommLog
from backend.services.mes_adapters import get_adapter
from backend.core import debug_center


# 截图压缩默认参数 (客户可在连接配置覆盖, 默认沿用历史行为)
_SNAPSHOT_DEFAULTS = {
    "quality_ladder": [60, 45, 30, 20, 12],  # 原尺寸逐级降质阶梯
    "scale_factor": 0.75,                     # 每轮缩边比例
    "scale_rounds": 4,                        # 最多缩几轮
    "min_edge": 32,                           # 缩到此边长以下放弃
    "scaled_quality": 35,                     # 缩图后用的质量
}


def _snapshot_compress_params(config: Optional[dict]) -> dict:
    """从连接配置取截图压缩参数, 缺省回落历史默认, 非法值忽略。"""
    cfg = config or {}
    out = dict(_SNAPSHOT_DEFAULTS)
    ladder = cfg.get("snapshot_quality_ladder")
    if isinstance(ladder, (list, tuple)) and ladder:
        clean = [int(q) for q in ladder if isinstance(q, (int, float)) and 1 <= int(q) <= 100]
        if clean:
            out["quality_ladder"] = clean
    for key, cfgkey, lo, hi, cast in (
        ("scale_factor", "snapshot_scale_factor", 0.1, 0.95, float),
        ("scale_rounds", "snapshot_scale_rounds", 0, 10, int),
        ("min_edge", "snapshot_min_edge", 1, 4096, int),
        ("scaled_quality", "snapshot_scaled_quality", 1, 100, int),
    ):
        v = cfg.get(cfgkey)
        if v is not None:
            try:
                cv = cast(v)
                if lo <= cv <= hi:
                    out[key] = cv
            except Exception:
                pass
    return out


def _recompress_jpeg_under(jpeg_bytes: bytes, max_bytes: int,
                           params: Optional[dict] = None) -> Optional[bytes]:
    """把一张 JPEG 压到 max_bytes 以内: 先逐级降质, 再逐级缩边长。

    压缩阶梯/缩放比例/最小边长/缩图质量可由 params 配置 (连接配置驱动), 缺省沿用历史值。
    都压不下去返回 None (调用方据此放弃)。无 cv2/numpy 或解码失败也返回 None。
    """
    p = params or _SNAPSHOT_DEFAULTS
    try:
        import cv2
        import numpy as np
    except Exception:
        return None
    try:
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        # ① 逐级降质 (原尺寸)
        for q in p.get("quality_ladder") or _SNAPSHOT_DEFAULTS["quality_ladder"]:
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(q)])
            if ok and len(buf) <= max_bytes:
                return buf.tobytes()
        # ② 仍超限 → 逐级缩边长 (每轮 ×scale_factor) 配合缩图质量
        factor = p.get("scale_factor", 0.75)
        rounds = p.get("scale_rounds", 4)
        min_edge = p.get("min_edge", 32)
        sq = int(p.get("scaled_quality", 35))
        h, w = img.shape[:2]
        for _ in range(int(rounds)):
            w = int(w * factor)
            h = int(h * factor)
            if w < min_edge or h < min_edge:
                break
            small = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, sq])
            if ok and len(buf) <= max_bytes:
                return buf.tobytes()
        return None
    except Exception:
        return None


def _reencode_jpeg_quality(jpeg_bytes: bytes, quality: int) -> Optional[bytes]:
    """按指定质量重编码一张 JPEG (客户直接控制图像质量)。失败返回 None。"""
    try:
        import cv2
        import numpy as np
    except Exception:
        return None
    try:
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        ok, buf = cv2.imencode(".jpg", img,
                               [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
        return buf.tobytes() if ok else None
    except Exception:
        return None


class MESGateway:

    def __init__(self):
        self.enabled = True
        self._extra_fields: dict[int, dict] = {}
        # v3.38 推送熔断器 (内存态, 按连接 id):
        # {conn_id: {"fails": 连续失败数, "open_until": 熔断截止时间戳, "episodes": 累计熔断次数}}
        # 端点不在线时旧行为是每个周期傻等完整超时 (默认 30s×重试), 白耗 MES 工作
        # 线程、拖慢工件统计落库。熔断后冷却期内直接跳过, 到点放一次探测请求,
        # 成功自动恢复。默认开 (阈值 5 / 冷却 30s), 连接配置可改/可关。
        self._cb_state: dict[int, dict] = {}

    # ==================== 推送熔断器 ====================
    @staticmethod
    def _cb_config(config: dict) -> tuple:
        """返回 (enabled, fail_threshold, cooldown_sec)。"""
        return (
            bool(config.get("cb_enabled", True)),
            max(int(config.get("cb_fail_threshold", 5) or 5), 1),
            max(float(config.get("cb_cooldown_sec", 30) or 30), 1.0),
        )

    def _cb_should_skip(self, db, conn, config: dict, event_type: str) -> bool:
        """熔断打开且未到探测时间 → True (跳过本次推送, 不发网络请求)。"""
        enabled, _th, _cd = self._cb_config(config)
        if not enabled:
            return False
        st = self._cb_state.get(conn.id)
        if not st or st.get("open_until", 0) <= 0:
            return False
        if time.time() >= st["open_until"]:
            # 冷却到期 → 半开: 放本次请求当探测, 成功恢复 / 失败重新熔断
            if debug_center.is_on("backend.gateway"):
                debug_center.dbg("backend.gateway", "熔断半开探测",
                                 f"conn={getattr(conn, 'name', None) or conn.id} event={event_type}")
            return False
        if debug_center.is_on("backend.gateway"):
            debug_center.dbg("backend.gateway", "熔断中跳过推送",
                             f"conn={getattr(conn, 'name', None) or conn.id} event={event_type} "
                             f"剩余{st['open_until'] - time.time():.0f}s")
        return True

    def _cb_record(self, db, conn, config: dict, success: bool):
        """按本次真实推送结果推进熔断状态机 (打开/关闭时各落一条通信日志留证)。"""
        enabled, threshold, cooldown = self._cb_config(config)
        if not enabled:
            return
        st = self._cb_state.setdefault(conn.id, {"fails": 0, "open_until": 0.0, "episodes": 0})
        if success:
            if st["open_until"] > 0:
                print(f"[MES Gateway] 熔断恢复: {conn.name} (探测成功, 恢复正常推送)", flush=True)
                debug_center.dbg("backend.gateway", "熔断恢复",
                                 f"conn={getattr(conn, 'name', None) or conn.id}")
                try:
                    self._log(db, conn.id, "circuit_breaker", "push",
                              error_msg="熔断恢复: 探测成功, 恢复正常推送", success=True)
                except Exception:
                    pass
            st["fails"] = 0
            st["open_until"] = 0.0
            return
        st["fails"] += 1
        if st["fails"] >= threshold:
            was_open = st["open_until"] > 0
            st["open_until"] = time.time() + cooldown
            if not was_open:
                st["episodes"] += 1
                print(f"[MES Gateway] 熔断开启: {conn.name} 连续 {st['fails']} 次失败, "
                      f"{cooldown:.0f}s 内跳过推送后自动探测", flush=True)
                debug_center.dbg("backend.gateway", "熔断开启",
                                 f"conn={getattr(conn, 'name', None) or conn.id} "
                                 f"fails={st['fails']} cooldown={cooldown:.0f}s")
                try:
                    self._log(db, conn.id, "circuit_breaker", "push",
                              error_msg=f"熔断开启: 连续{st['fails']}次失败, "
                                        f"冷却{cooldown:.0f}s后自动探测", success=False)
                except Exception:
                    pass

    def reset_circuit(self, conn_id: int):
        """人工干预复位 (测试连接成功 / 修改连接配置后调用), 立即恢复推送。"""
        if conn_id in self._cb_state:
            self._cb_state.pop(conn_id, None)
            debug_center.dbg("backend.gateway", "熔断人工复位", f"conn_id={conn_id}")

    def get_circuit_state(self, conn_id: int) -> dict:
        """健康状态接口用: 返回该连接熔断器快照。"""
        st = self._cb_state.get(conn_id)
        if not st:
            return {"open": False, "fails": 0, "episodes": 0}
        now = time.time()
        return {
            "open": st["open_until"] > now,
            "fails": st["fails"],
            "episodes": st["episodes"],
            "reopen_in_sec": max(round(st["open_until"] - now), 0) if st["open_until"] > now else 0,
        }

    def set_extra_fields(self, channel_id: int, fields: dict):
        """Monitor 页实时输入的额外字段 (如 weight)"""
        self._extra_fields[channel_id] = fields

    def get_extra_fields(self, channel_id: int) -> dict:
        return self._extra_fields.get(channel_id, {})

    def dispatch(self, event_type: str, context: dict, channel_id: int = None):
        """分发事件到所有匹配的外部 MES 连接"""
        if not self.enabled:
            return

        db = SessionLocal()
        try:
            # 报警事件"同任务同标签 N 秒去重": 窗口内同唯一键报警不重复推送给外部
            # (川南 v4 第 4 条; 默认惰性: 仅 alarm_event_name 配置 + dedup_sec>0 时生效)。
            if self._alarm_dedup_should_skip(db, event_type, context):
                if debug_center.is_on("backend.gateway"):
                    debug_center.dbg("backend.gateway", "报警去重跳过推送",
                                     f"event={event_type}")
                return
            connections = (
                db.query(MESConnection)
                .filter(MESConnection.enabled == True)
                .all()
            )
            if debug_center.is_on("backend.gateway"):
                debug_center.dbg("backend.gateway", "dispatch 入口", f"event={event_type} channel={channel_id if channel_id is not None else '-'} enabled_conns={len(connections)}")
            sent_any = False
            for conn in connections:
                # v3.38 逐连接错误隔离: 逐连接提交 (见下) 会使 ORM 对象过期,
                # 若循环中途连接行被并发删除, 重查会抛错 — 一条连接的任何异常
                # 都不允许拖垮其余连接的推送。
                try:
                    events = conn.push_events or []
                    if event_type not in events:
                        continue
                    bound = conn.bound_channels
                    if bound and channel_id is not None and channel_id not in bound:
                        continue
                    # 只有真正投递成功 (非被 push_on_result 过滤 / 非失败) 才算"推出去过"
                    if self._send_to_connection(db, conn, event_type, context, channel_id):
                        sent_any = True
                    # v3.38 川南"框冻结"根因修复: 每推完一条连接立刻提交。
                    # 原来统一在循环外提交 → 前一条连接失败落日志 (_log 内 flush) 时
                    # SQLite 写锁已被本会话握住, 下一条连接的 HTTP 超时等待 (对不在线
                    # 端点可达 30s) 期间锁一直不放; 推理线程此刻写步骤记录被堵到
                    # busy_timeout 边缘 (实测 8~15s) → 前端检测框冻结、后续类别漏检
                    # 误判 NG。逐连接提交把锁窗口收敛回毫秒级, 网络等待期间绝不持锁。
                    db.commit()
                except Exception as _ce:
                    db.rollback()
                    debug_center.dbg("backend.gateway", "单连接推送异常(已隔离,继续其余连接)",
                                     f"event={event_type} err={_ce}")
                    print(f"[MES Gateway] 单连接推送异常(已隔离): {_ce}", flush=True)
            # 在途报警台账: 报警类事件成功推给外部后登记一条, 供外部"报警消除"命令匹配 + 监控页横幅。
            # 惰性: 仅当入站配置指定了 alarm_event_name 且本次确实成功推送过才登记 (默认全关零开销)。
            if sent_any:
                self._record_active_alarm_if_alarm(db, event_type, context, channel_id)
            db.commit()
        except Exception as e:
            db.rollback()
            debug_center.dbg("backend.gateway", "dispatch 异常(含 payload 构建)", f"event={event_type} channel={channel_id if channel_id is not None else '-'} err={e}")
            print(f"[MES Gateway] dispatch failed: {e}", flush=True)
            traceback.print_exc()
        finally:
            db.close()

    @staticmethod
    def _resolve_alarm_event(db, event_type: str, context: dict):
        """判断本次事件是否为配置的"报警事件"。

        是 → 返回 (fields<台账字段>, dedup_sec, match_fields); 否 → (None, 0, None)。
        默认惰性: 入站配置 alarm_event_name 为空 → 视为非报警, 零副作用。
        登记 (record) 与去重 (dedup) 两处共用本判定, 避免逻辑漂移。
        """
        from backend.services.mes_inbound import get_mes_inbound
        from backend.services.mes_adapters.base import _get_nested

        cfg = get_mes_inbound().get_config(db)
        # alarm_event_name 支持单个字符串或列表 (多事件源都可当报警源)
        names = cfg.get("alarm_event_name") or ""
        allowed = [str(n).strip() for n in names] if isinstance(names, list) \
            else [s.strip() for s in str(names).split(",")]
        allowed = [n for n in allowed if n]
        if not allowed or event_type not in allowed:
            return None, 0, None

        # 结果过滤: 默认只认报警类结果 (NG), 避免 OK 周期被当报警 (空列表 = 不过滤)
        record_on = cfg.get("alarm_record_on_results")
        if record_on is None:
            record_on = ["NG"]
        if record_on:
            result = (_get_nested(context, "cycle.result")
                      or context.get("overall_result")
                      or context.get("result") or "")
            if str(result).upper() not in [str(x).upper() for x in record_on]:
                return None, 0, None

        field_map = cfg.get("alarm_ledger_field_map") or {}
        fields = {}
        for our_field, path in field_map.items():
            if not path:
                continue
            val = _get_nested(context, path)
            if val is not None:
                fields[our_field] = val
        # 去重/登记的唯一键字段与消除口径共用同一份配置, 避免漂移
        match_fields = cfg.get("alarm_clear_match_fields") or None
        return fields, int(cfg.get("alarm_dedup_sec") or 0), match_fields

    def _alarm_dedup_should_skip(self, db, event_type: str, context: dict) -> bool:
        """报警事件在去重窗口内已报过 → True (跳过整次推送)。错误一律放行 (返回 False)。"""
        try:
            fields, dedup_sec, match_fields = self._resolve_alarm_event(
                db, event_type, context)
            if fields is None or dedup_sec <= 0:
                return False
            from backend.services.external_alarm import find_recent_active_alarm
            return find_recent_active_alarm(
                db, fields, dedup_sec, match_fields=match_fields) is not None
        except Exception as e:
            debug_center.dbg("backend.gateway", "报警去重判断异常(放行推送)", str(e))
            return False

    def _record_active_alarm_if_alarm(self, db, event_type: str,
                                      context: dict, channel_id=None):
        """若本次事件是配置的"报警事件", 登记一条在途报警 (全程错误隔离, 失败不影响推送)。

        默认惰性: 入站配置 alarm_event_name 为空 → 直接返回, 零副作用。
        """
        try:
            from backend.services.external_alarm import record_active_alarm
            fields, dedup_sec, match_fields = self._resolve_alarm_event(
                db, event_type, context)
            if fields is None:
                return
            record_active_alarm(
                db, fields, event_type=event_type, channel_id=channel_id,
                dedup_sec=dedup_sec, match_fields=match_fields,
            )
        except Exception as e:
            debug_center.dbg("backend.gateway", "在途报警登记失败(已忽略)", str(e))

    def _send_to_connection(self, db, conn: MESConnection,
                            event_type: str, context: dict, channel_id: int = None) -> bool:
        """向单个连接发送数据, 含重试。返回 True=真正投递成功; False=被结果过滤/适配器缺失/重试耗尽失败。"""
        config = conn.config or {}

        # v3.38 熔断守门: 端点连续失败已熔断且未到探测时间 → 不构建载荷不发网络,
        # 直接跳过 (放在最前, 连截图/模板渲染的开销都省掉)
        if self._cb_should_skip(db, conn, config, event_type):
            return False

        static = config.get("static_fields", {})
        extra = self._extra_fields.get(channel_id or 0, {})

        full_context = {
            **context,
            "extra": {**extra},
            "timestamp": datetime.now().isoformat(),
        }
        for dotted_key, val in static.items():
            parts = dotted_key.split(".", 1)
            if len(parts) == 2:
                ns, key = parts
                if ns not in full_context:
                    full_context[ns] = {}
                if isinstance(full_context[ns], dict):
                    full_context[ns][key] = val
            else:
                full_context[dotted_key] = val

        # 按结果过滤: push_on_result=["OK","NG"] 默认全推; ["NG"] 只推 NG.
        # 适用场景: 客户只关心 NG 情况, OK 无需上报.
        # v3.40 川南修复: cycle_end 事件的结果在 cycle.result (嵌套), 原来只读顶层
        # overall_result/result → 周期事件取到空串被无条件放行, "仅 NG"对周期推送
        # 从未生效 (合格周期照样从报警连接推出去)。补上嵌套路径。
        push_on = config.get("push_on_result")
        if push_on:
            cyc = full_context.get("cycle")
            cycle_result = cyc.get("result") if isinstance(cyc, dict) else None
            r = str(full_context.get("overall_result")
                    or full_context.get("result")
                    or cycle_result or "").upper()
            allowed = [str(x).upper() for x in push_on]
            if r and r not in allowed:
                self._log(db, conn.id, event_type, "push",
                          url=config.get("url"),
                          error_msg=f"skipped by push_on_result={allowed}, result={r}",
                          success=True)
                return False

        # 出站附带"当前画面截图": 仅当连接配置 attach_snapshot=true 时, 抓该工位当前帧转 base64
        # 注入上下文, 供模板引用 {snapshot.image_base64} / {snapshot.image_data_uri}.
        # 放在 push_on 过滤之后, 被过滤掉的推送不浪费抓帧; 抓帧失败静默跳过, 绝不阻断推送.
        # 默认关 → full_context 无 snapshot 字段, 与历史字节级一致.
        if config.get("attach_snapshot"):
            img_b64 = self._capture_snapshot_base64(channel_id, config)
            if img_b64:
                snap = full_context.get("snapshot")
                if not isinstance(snap, dict):
                    snap = {}
                snap["image_base64"] = img_b64
                if config.get("snapshot_data_uri"):
                    snap["image_data_uri"] = f"data:image/jpeg;base64,{img_b64}"
                full_context["snapshot"] = snap

        # 物料名称映射: 把 ng_items 里的中文步骤名替换成客户 MES 的物料代码.
        # mode=replace (默认): 直接替换原数组;
        # mode=keep_both: 原数组保留, 新增 ng_items_mapped 字段.
        label_mapping = config.get("label_mapping") or {}
        if label_mapping and isinstance(full_context.get("ng_items"), list):
            mapped = [label_mapping.get(x, x) for x in full_context["ng_items"]]
            if config.get("label_mapping_mode", "replace") == "replace":
                full_context["ng_items"] = mapped
            else:
                full_context["ng_items_mapped"] = mapped

        # 鉴权统一处理: bearer/api_key/custom_header 合并进 headers, basic 留给 adapter.
        effective_config = self._apply_auth_to_headers(config)

        try:
            adapter = get_adapter(conn.adapter_type)
        except ValueError as e:
            debug_center.dbg("backend.gateway", "适配器不存在,推送中止", f"conn={getattr(conn, 'name', None) or conn.id} adapter={getattr(conn, 'adapter_type', '-')} err={e}")
            self._log(db, conn.id, event_type, "push",
                      error_msg=str(e), success=False)
            return False

        payload = adapter.build_payload(full_context, effective_config)
        request_body = json.dumps(payload, ensure_ascii=False, default=str)

        retry_count = conn.retry_count or 0
        retry_interval = conn.retry_interval_sec or 5
        # 重试退避策略 (可配, 默认 fixed = 历史行为不变):
        #   fixed       : 每次固定 retry_interval 秒
        #   exponential : retry_interval × 2^(attempt-1) (川南 §5.1 要 1→2→4, 设 interval=1)
        retry_backoff = str(config.get("retry_backoff") or "fixed").lower()
        # 是否对 4xx 重试 (默认 True = 历史行为; 川南 §5.1 要求仅 5xx/超时重试 → 设 False)
        retry_on_4xx = config.get("retry_on_4xx", True)
        # v3.49: 单事件重试总耗时预算 (秒, 0=不限=历史行为)。MES 长时间不通时,
        # 防止 重试次数×(间隔+请求超时) 把一次推送拖成几十秒 (捷昌: 3×5s+超时≈20s/箱)。
        # 预算耗尽 → 提前收场记失败, 熔断/失败日志逻辑照常走。
        try:
            retry_budget_sec = float(config.get("retry_budget_sec") or 0)
        except (TypeError, ValueError):
            retry_budget_sec = 0
        dispatch_started = time.monotonic()
        budget_exhausted = False
        last_result = None

        if debug_center.is_on("backend.gateway"):
            debug_center.dbg("backend.gateway", "推送发起", f"conn={getattr(conn, 'name', None) or conn.id} event={event_type} adapter={getattr(conn, 'adapter_type', '-')} retry_max={retry_count} backoff={retry_backoff} retry4xx={retry_on_4xx}")
        for attempt in range(1 + retry_count):
            if attempt > 0:
                delay = retry_interval * (2 ** (attempt - 1)) \
                    if retry_backoff == "exponential" else retry_interval
                if retry_budget_sec > 0 and \
                        (time.monotonic() - dispatch_started) + delay >= retry_budget_sec:
                    budget_exhausted = True
                    print(f"[MES Gateway] retry budget exhausted "
                          f"({retry_budget_sec}s), giving up: {conn.name}", flush=True)
                    if debug_center.is_on("backend.gateway"):
                        debug_center.dbg("backend.gateway", "重试预算耗尽",
                                         f"conn={getattr(conn, 'name', None) or conn.id} event={event_type} "
                                         f"budget={retry_budget_sec}s attempt={attempt}/{retry_count}")
                    break
                time.sleep(delay)
                print(f"[MES Gateway] retry {attempt}/{retry_count}: {conn.name}", flush=True)
                if debug_center.is_on("backend.gateway"):
                    debug_center.dbg("backend.gateway", "推送重试", f"conn={getattr(conn, 'name', None) or conn.id} event={event_type} attempt={attempt}/{retry_count} delay={delay}s")

            result = adapter.send(payload, effective_config)
            last_result = result
            is_ok = adapter.check_response(result, effective_config)

            if not is_ok and not retry_on_4xx:
                # 仅 5xx / 网络超时重试; 4xx (客户端错误) 不重试, 直接收场
                sc = (result or {}).get("status_code") or 0
                if 400 <= sc < 500:
                    if debug_center.is_on("backend.gateway"):
                        debug_center.dbg("backend.gateway", "4xx 不重试", f"conn={getattr(conn, 'name', None) or conn.id} status={sc}")
                    break

            if is_ok:
                self._log(
                    db, conn.id, event_type, "push",
                    method=config.get("method", "POST"),
                    url=config.get("url"),
                    request_body=request_body,
                    response_body=json.dumps(result.get("body"), ensure_ascii=False, default=str)
                        if result.get("body") else None,
                    status_code=result.get("status_code"),
                    duration_ms=result.get("duration_ms"),
                    success=True,
                )
                conn.last_sync_at = datetime.now()
                db.flush()
                print(f"[MES Gateway] push success: {conn.name} ({event_type})", flush=True)
                if debug_center.is_on("backend.gateway"):
                    debug_center.dbg("backend.gateway", "推送成功", f"conn={getattr(conn, 'name', None) or conn.id} event={event_type} status={result.get('status_code') or '-'} attempt={attempt} duration_ms={result.get('duration_ms') or '-'}")
                self._cb_record(db, conn, config, success=True)
                return True

        error_msg = last_result.get("error") if last_result else "未知错误"
        if not error_msg and last_result:
            error_msg = f"HTTP {last_result.get('status_code')}: 响应校验失败"
        if budget_exhausted:
            error_msg = f"重试预算耗尽({retry_budget_sec}s): {error_msg}"
        self._log(
            db, conn.id, event_type, "push",
            method=config.get("method", "POST"),
            url=config.get("url"),
            request_body=request_body,
            response_body=json.dumps(last_result.get("body"), ensure_ascii=False, default=str)
                if last_result and last_result.get("body") else None,
            status_code=last_result.get("status_code") if last_result else 0,
            duration_ms=last_result.get("duration_ms") if last_result else 0,
            success=False,
            error_msg=error_msg,
        )
        print(f"[MES Gateway] push failed: {conn.name} ({event_type}) - {error_msg}", flush=True)
        debug_center.dbg("backend.gateway", "推送失败(重试耗尽)", f"conn={getattr(conn, 'name', None) or conn.id} event={event_type} attempts={1 + retry_count} status={(last_result or {}).get('status_code') or '-'} err={error_msg or '-'}")
        self._cb_record(db, conn, config, success=False)
        return False

    @staticmethod
    def _capture_snapshot_base64(channel_id, config: dict) -> Optional[str]:
        """抓指定工位当前画面 → JPEG → base64 字符串.

        gated 调用: 仅在连接 config.attach_snapshot=true 时才走这里.
        - channel_id 为 None 时回落工位 0
        - 工位未配置 / 无帧 / 编码失败 → 返回 None (静默, 调用方据此不注入)
        - config.snapshot_max_bytes>0 且 JPEG 超限 → 返回 None
          (客户 MES 常有 413 请求体上限, 超大截图直接放弃而不是发了被拒)
        """
        try:
            from backend.api.channel_manager import get_channel_manager
            cid = channel_id if channel_id is not None else 0
            mgr = get_channel_manager().get(cid)
            if mgr is None:
                return None
            jpeg = mgr.get_snapshot()
            if not jpeg:
                return None
            # 客户直接控制图像质量: 配了 snapshot_quality 就按该质量重编码 (源默认 70)
            q = config.get("snapshot_quality")
            if isinstance(q, (int, float)) and 1 <= int(q) <= 100:
                requ = _reencode_jpeg_quality(jpeg, int(q))
                if requ is not None:
                    jpeg = requ
            max_bytes = config.get("snapshot_max_bytes")
            if isinstance(max_bytes, int) and max_bytes > 0 and len(jpeg) > max_bytes:
                # 超限: 逐级降质重压再传 (兑现"自动压缩"承诺), 连最低质量也超才放弃
                params = _snapshot_compress_params(config)
                shrunk = _recompress_jpeg_under(jpeg, max_bytes, params)
                if shrunk is None:
                    debug_center.dbg("backend.gateway", "截图压到最低仍超限,放弃",
                                     f"channel={cid} bytes={len(jpeg)} max={max_bytes}")
                    return None
                debug_center.dbg("backend.gateway", "截图超限已自动压缩",
                                 f"channel={cid} {len(jpeg)}→{len(shrunk)} max={max_bytes}")
                jpeg = shrunk
            return base64.b64encode(jpeg).decode("ascii")
        except Exception as e:
            debug_center.dbg("backend.gateway", "截图入上下文失败",
                             f"channel={channel_id} err={e}")
            return None

    @staticmethod
    def _apply_auth_to_headers(config: dict) -> dict:
        """统一鉴权到 headers.

        auth.type 取值:
          - none          : 不加
          - basic         : 保持在 auth 里, 给 adapter 走 requests.auth
          - bearer        : Authorization: Bearer <token>
          - api_key       : {header|X-API-Key}: <value>
          - custom_header : 把 auth.headers 合并进 config.headers

        同时把顶层 config.custom_headers (列表 [{key,value}]) 合并进 config.headers,
        方便前端 UI 提供 "额外请求头" 编辑器.
        """
        new_config = dict(config) if isinstance(config, dict) else {}
        headers = dict(new_config.get("headers") or {})

        custom = new_config.get("custom_headers")
        if isinstance(custom, list):
            for item in custom:
                if isinstance(item, dict):
                    k = item.get("key") or item.get("name")
                    v = item.get("value")
                    if k:
                        headers[k] = "" if v is None else str(v)
        elif isinstance(custom, dict):
            for k, v in custom.items():
                headers[k] = "" if v is None else str(v)

        auth = new_config.get("auth") or {}
        atype = auth.get("type") if isinstance(auth, dict) else None
        if atype == "bearer":
            token = auth.get("token") or ""
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif atype == "api_key":
            hdr = auth.get("header") or "X-API-Key"
            val = auth.get("value") or ""
            headers[hdr] = val
        elif atype == "custom_header":
            extra = auth.get("headers") or []
            if isinstance(extra, list):
                for item in extra:
                    if isinstance(item, dict):
                        k = item.get("key") or item.get("name")
                        v = item.get("value")
                        if k:
                            headers[k] = "" if v is None else str(v)
            elif isinstance(extra, dict):
                for k, v in extra.items():
                    headers[k] = "" if v is None else str(v)

        new_config["headers"] = headers
        return new_config

    def _log(self, db, connection_id: int, event_type: str, direction: str, **kwargs):
        log = MESCommLog(
            connection_id=connection_id,
            direction=direction,
            event_type=event_type,
            method=kwargs.get("method"),
            url=kwargs.get("url"),
            request_body=kwargs.get("request_body"),
            response_body=kwargs.get("response_body"),
            status_code=kwargs.get("status_code"),
            success=kwargs.get("success", True),
            error_msg=kwargs.get("error_msg"),
            duration_ms=kwargs.get("duration_ms"),
        )
        db.add(log)
        db.flush()

    def build_context_from_cycle(self, db, cycle_id: int,
                                 workpiece_id: int = None,
                                 order_id: int = None,
                                 is_good: bool = True,
                                 event_name: str = None,
                                 result_reason: str = None,
                                 duration: float = None,
                                 step_sequence: list = None,
                                 project_id: int = None) -> dict:
        """从 cycle 数据构建标准化上下文"""
        context = {
            "cycle": {
                "id": cycle_id,
                "is_good": is_good,
                "result": "OK" if is_good else "NG",
                "duration": duration,
                "event_name": event_name,
                "ng_reason": result_reason,
            },
            "project": {"id": project_id},
            "steps": [],
            "defects": [],
            "workpiece": {},
            "order": {},
            "operator": {},
        }

        if project_id:
            from backend.models.models import Project
            proj = db.query(Project).filter(Project.id == project_id).first()
            if proj:
                context["project"]["name"] = proj.name

        from backend.models.models import DetectionCycle, StepRecord
        # v3.10+ 阶段 4: cycle.operator_id 语义改为 user_id, 查 User 表
        from backend.models.auth_models import User
        cycle = db.query(DetectionCycle).filter(DetectionCycle.id == cycle_id).first()
        if cycle:
            # DetectionCycle ORM 模型本身没有 completed_steps/total_steps 字段，
            # 直接属性访问会 AttributeError 把 cycle_end → MES → 集群分发整条链路冲崩。
            # 用 getattr 兜底，缺失时从 step_sequence 推算。
            completed_steps = getattr(cycle, 'completed_steps', None)
            total_steps = getattr(cycle, 'total_steps', None)
            if total_steps is None and isinstance(cycle.step_sequence, list):
                total_steps = len(cycle.step_sequence)
            context["cycle"]["completed_steps"] = completed_steps
            context["cycle"]["total_steps"] = total_steps
            context["cycle"]["start_time"] = cycle.start_time.isoformat() if cycle.start_time else None
            context["cycle"]["end_time"] = cycle.end_time.isoformat() if cycle.end_time else None

            # v3.13.1 RFC 10 CG.8: 工位组字段透传, 让 cluster collector 跨机聚 box 时
            # 顶层能看到 "本工位是不是组级 NG 联动"; 客户 MES 模板可以引用
            # {channel_group.settle_result} {channel_group.id}. 通道不在任何组时这些字段
            # 都是 None / [], cluster 侧也 OK (None 不影响 MES payload 字段缺省).
            cg_id = getattr(cycle, 'channel_group_id', None)
            cg_result = getattr(cycle, 'group_settle_result', None)
            cg_settled_with = getattr(cycle, 'group_settled_with', None) or []
            context["channel_group"] = {
                "id": cg_id,
                "settle_result": cg_result,
                "settled_with": list(cg_settled_with) if isinstance(cg_settled_with, list) else [],
            }
            # 同时也挂在 cycle 子树, 让旧模板可用 {cycle.group_settle_result} 风格直接引用
            context["cycle"]["channel_group_id"] = cg_id
            context["cycle"]["group_settle_result"] = cg_result
            context["cycle"]["group_settled_with"] = context["channel_group"]["settled_with"]
            # operator_id 列保留字段名 (SQLite 无法 rename), 值改为 user.id
            # MES payload key "operator" 字段名稳定: name / employee_no / id (向后兼容客户模板)
            u_id = getattr(cycle, 'operator_id', None)
            if u_id:
                u = db.query(User).filter(User.id == u_id).first()
                if u:
                    context["operator"] = {
                        "name": u.display_name or u.username,
                        "employee_no": u.username,
                        "id": u.id,
                    }

        steps = db.query(StepRecord).filter(StepRecord.cycle_id == cycle_id).order_by(StepRecord.id).all()
        for s in steps:
            # StepRecord 模型字段名：step_order / duration / is_valid
            # 历史上这里写过 step_index / duration_seconds / is_good，会触发 AttributeError
            # 并导致整个 cycle_end 构造 context 失败 → 进而 _cluster_dispatch 不被触发。
            # 用 getattr 防御式访问，兼容模型重命名。
            idx = getattr(s, 'step_index', None)
            if idx is None:
                idx = getattr(s, 'step_order', None)
            dur = getattr(s, 'duration_seconds', None)
            if dur is None:
                dur = getattr(s, 'duration', None)
            good = getattr(s, 'is_good', None)
            if good is None:
                good = bool(getattr(s, 'is_valid', True))
            context["steps"].append({
                "label": getattr(s, 'step_label', None),
                "index": idx,
                "duration": dur,
                "is_good": good,
                "confidence": getattr(s, 'confidence', None),
                "start_time": s.start_time.isoformat() if s.start_time else None,
                "end_time": s.end_time.isoformat() if s.end_time else None,
            })

        # 便利字段：让模板端不写取值/过滤逻辑即可直接拿到 NG 步骤明细
        # （Gateway 模板是自研 {key.path} 占位符替换，不是 Jinja2）
        # ng_steps = 所有 is_good=False 的步骤；missing_step_count = 缺失步骤数
        context["ng_steps"] = [s for s in context["steps"] if s.get("is_good") is False]
        completed = context["cycle"].get("completed_steps")
        total = context["cycle"].get("total_steps")
        if isinstance(completed, int) and isinstance(total, int):
            context["cycle"]["missing_step_count"] = max(0, total - completed)
        else:
            context["cycle"]["missing_step_count"] = len(context["ng_steps"])

        if workpiece_id:
            from backend.models.mes_models import Workpiece, DefectRecord
            wp = db.query(Workpiece).filter(Workpiece.id == workpiece_id).first()
            if wp:
                context["workpiece"] = {
                    "id": wp.id,
                    "serial_no": wp.serial_no,
                    "status": wp.status,
                    "inspection_count": wp.inspection_count,
                }
            defects = db.query(DefectRecord).filter(
                DefectRecord.workpiece_id == workpiece_id,
                DefectRecord.cycle_id == cycle_id
            ).all()
            for d in defects:
                context["defects"].append({
                    "defect_code": d.defect_code,
                    "defect_name": d.defect_name,
                    "category": d.defect_category,
                    "severity": d.severity,
                })

        if order_id:
            from backend.services.work_order import WorkOrderService
            wo_svc = WorkOrderService()
            summary = wo_svc.get_order_summary(db, order_id)
            if summary:
                context["order"] = summary

        return context

    def build_context_from_session(self, db, session_id: int,
                                   project_id: int = None) -> dict:
        """从 session 数据构建标准化上下文"""
        from backend.models.models import DetectionSession
        context = {
            "session": {"id": session_id},
            "project": {"id": project_id},
            "order": {},
        }

        session = db.query(DetectionSession).filter(DetectionSession.id == session_id).first()
        if session:
            context["session"].update({
                "total_cycles": session.total_cycles,
                "good_cycles": session.good_cycles,
                "ng_cycles": session.ng_cycles,
                "start_time": session.start_time.isoformat() if session.start_time else None,
                "end_time": session.end_time.isoformat() if session.end_time else None,
            })
            if hasattr(session, 'order_id') and session.order_id:
                from backend.services.work_order import WorkOrderService
                summary = WorkOrderService().get_order_summary(db, session.order_id)
                if summary:
                    context["order"] = summary

        if project_id:
            from backend.models.models import Project
            proj = db.query(Project).filter(Project.id == project_id).first()
            if proj:
                context["project"]["name"] = proj.name

        return context

    def manual_push(self, connection_id: int, event_type: str, context: dict,
                    channel_id: int = 0) -> dict:
        """手动推送 (API 调用), 返回结果"""
        db = SessionLocal()
        try:
            conn = db.query(MESConnection).filter(MESConnection.id == connection_id).first()
            if not conn:
                return {"success": False, "error": "连接不存在"}
            # 手动推送是操作员显式动作: 熔断中也放行 (充当探测, 成功即恢复)
            st = self._cb_state.get(conn.id)
            if st and st.get("open_until", 0) > time.time():
                st["open_until"] = time.time()
            self._send_to_connection(db, conn, event_type, context, channel_id)
            db.commit()
            log = (
                db.query(MESCommLog)
                .filter(MESCommLog.connection_id == connection_id)
                .order_by(MESCommLog.id.desc())
                .first()
            )
            if log:
                return {
                    "success": log.success,
                    "status_code": log.status_code,
                    "error": log.error_msg,
                    "duration_ms": log.duration_ms,
                }
            return {"success": False, "error": "日志记录缺失"}
        except Exception as e:
            db.rollback()
            return {"success": False, "error": str(e)}
        finally:
            db.close()


_gateway_instance: Optional[MESGateway] = None


def get_mes_gateway() -> MESGateway:
    global _gateway_instance
    if _gateway_instance is None:
        _gateway_instance = MESGateway()
    return _gateway_instance
