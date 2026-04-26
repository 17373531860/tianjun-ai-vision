"""外设原始数据 pipeline mixin (解析/校验/派发/日志)"""
import json
import logging
import re
import socket
import threading
import time
from datetime import datetime
from typing import Optional

from backend.db.database import SessionLocal
from backend.models.mes_models import ExternalDevice, ExternalDeviceLog
from backend.services.external_device_models import DeviceConnection

logger = logging.getLogger(__name__)

class ExternalDevicePipelineMixin:
    def _on_raw_data(self, conn: DeviceConnection, raw: str):
        conn.last_data = raw
        conn.last_data_time = time.time()

        parsed = self._parse_data(conn, raw)
        if parsed is None:
            self._log_data(conn, raw, None, False, "解析失败")
            return

        conn.last_parsed = parsed

        # v2.7.5: 称重器稳定值判定 —— 抖动、空载、未稳定的数据不往下传
        if conn.device_role == "weight" and conn.stable_enabled:
            handled = self._handle_weight_stability(conn, parsed, raw)
            if not handled:
                return

        is_valid, error = self._validate(conn, parsed)
        barcode = parsed.get("barcode") or self._barcode_buffer.get(conn.device_id)

        # v2.7.5: 有重无码告警 —— 开启后超时才触发，默认关闭
        if conn.device_role == "weight" and conn.weight_no_barcode_alarm_enabled:
            self._check_weight_no_barcode_alarm(conn, parsed, barcode)

        self._log_data(conn, raw, parsed, is_valid, error, barcode)
        if not is_valid:
            logger.warning("[ExtDev] %s 校验失败（仍发送）: %s", conn.name, error)
        self._dispatch(conn, parsed, barcode)

    def _extract_weight(self, parsed: dict) -> Optional[float]:
        """从 parsed 里取出称重值，兼容 direct/regex/split/json_path 各种 parse_mode"""
        val = parsed.get("weight")
        if val is None:
            # 兜底：扫描第一个数值字段
            for k, v in parsed.items():
                if k in ("raw", "barcode"):
                    continue
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    def _handle_weight_stability(self, conn: DeviceConnection,
                                  parsed: dict, raw: str) -> bool:
        """称重稳定值判定：返回 True 表示这条数据应继续走后续派发，False 表示丢弃。

        状态机:
            idle         → 空载或首次数据 → stabilizing
            stabilizing  → 连续 stable_count 次 (max-min<=delta) → stable（首次上报）
            stable       → 值漂移 > delta → 回 stabilizing；< zero_threshold → 回 idle
        """
        val = self._extract_weight(parsed)
        if val is None:
            return False

        parsed["weight"] = val
        parsed["_raw_value"] = val  # 保留原始读数，方便日志

        # 空载：清 buffer、清状态
        if abs(val) < conn.zero_threshold:
            if conn._stable_state != "idle":
                logger.info("[ExtDev] %s 空载 (|%.4f|<%.4f)，重置稳定状态", conn.name, val, conn.zero_threshold)
            conn._stable_samples.clear()
            conn._stable_state = "idle"
            conn._stable_value = None
            conn._last_reported_value = None
            conn._weight_onset_time = 0
            conn._no_barcode_alarm_fired = False
            self._barcode_buffer.pop(conn.device_id, None)
            self._log_data(conn, raw, parsed, True, "idle_zero", None)
            return False

        # 进入/维持 stabilizing：累积样本
        conn._stable_samples.append(val)
        if len(conn._stable_samples) > max(conn.stable_count * 3, conn.stable_count + 10):
            conn._stable_samples = conn._stable_samples[-conn.stable_count * 3:]

        # 样本不足，等下一条
        if len(conn._stable_samples) < conn.stable_count:
            if conn._stable_state != "stabilizing":
                conn._stable_state = "stabilizing"
            self._log_data(conn, raw, parsed, True, f"stabilizing ({len(conn._stable_samples)}/{conn.stable_count})", None)
            return False

        window = conn._stable_samples[-conn.stable_count:]
        spread = max(window) - min(window)

        if spread > conn.stable_delta:
            # 仍在抖动
            conn._stable_state = "stabilizing"
            self._log_data(conn, raw, parsed, True, f"stabilizing spread={spread:.4f}", None)
            return False

        # 稳定 → 取中位数作为"最稳定代表值"
        sorted_window = sorted(window)
        median = sorted_window[len(sorted_window) // 2]
        parsed["weight"] = round(median, 4)  # 上报值以稳定后的中位数为准
        conn._stable_value = parsed["weight"]

        first_stable = conn._stable_state != "stable"
        conn._stable_state = "stable"

        # 节流：稳定窗口内只"对下游"上报一次，除非值变化超过 delta。
        # v2.7.9: 日志表不做节流——每一条都记录，方便前端实时观察；
        # 集群/extra_fields 的 dispatch 仍然节流，避免狂刷业务通道。
        if (conn._last_reported_value is not None
                and abs(parsed["weight"] - conn._last_reported_value) <= conn.stable_delta
                and not first_stable):
            self._log_data(conn, raw, parsed, True, "stable", None)
            return False

        conn._last_reported_value = parsed["weight"]
        if first_stable:
            logger.info("[ExtDev] %s 重量稳定 → %.4f (median of %d samples, spread=%.4f)",
                        conn.name, parsed["weight"], conn.stable_count, spread)
        return True

    def _check_weight_no_barcode_alarm(self, conn: DeviceConnection,
                                        parsed: dict, barcode: Optional[str]):
        """有重无码告警: 稳定非零但无条码持续 N 秒 → 触发一次性告警"""
        val = self._extract_weight(parsed) or 0.0
        if abs(val) < conn.zero_threshold or conn._stable_state != "stable":
            # 无重或还没稳定 → 不计时
            conn._weight_onset_time = 0
            conn._no_barcode_alarm_fired = False
            return

        if barcode:
            # 已有条码 → 清计时
            conn._weight_onset_time = 0
            conn._no_barcode_alarm_fired = False
            return

        now = time.time()
        if conn._weight_onset_time <= 0:
            conn._weight_onset_time = now
            return

        elapsed = now - conn._weight_onset_time
        if elapsed < conn.weight_no_barcode_alarm_delay_sec:
            return

        if conn._no_barcode_alarm_fired:
            return

        conn._no_barcode_alarm_fired = True
        logger.warning("[ExtDev] %s 有重无码告警: value=%.4f 持续 %.1fs 未扫码",
                       conn.name, val, elapsed)
        try:
            from backend.services.mes_gateway import get_mes_gateway
            gw = get_mes_gateway()
            gw.dispatch("weight_no_barcode", {
                "device_name": conn.name,
                "device_id": conn.device_id,
                "channel_id": conn.channel_id,
                "station_id": conn.station_id,
                "value": val,
                "elapsed_sec": round(elapsed, 1),
                "timestamp": datetime.now().isoformat(),
            }, channel_id=conn.channel_id)
        except Exception as e:
            logger.error("[ExtDev] weight_no_barcode 事件派发失败: %s", e)

        try:
            from backend.api.alarm import alarm_router
            alarm_router.trigger_alarm("weight_no_barcode",
                                        channel_id=conn.channel_id or 0)
        except Exception as e:
            logger.debug("[ExtDev] weight_no_barcode 报警灯触发失败: %s", e)

    def _parse_data(self, conn: DeviceConnection, raw: str) -> Optional[dict]:
        mode = conn.parse_mode
        cfg = conn.parse_config

        try:
            if mode == "direct":
                return self._parse_direct(raw, conn.device_role)
            elif mode == "regex":
                return self._parse_regex(raw, cfg)
            elif mode == "split":
                return self._parse_split(raw, cfg)
            elif mode == "json_path":
                return self._parse_json(raw, cfg)
            else:
                logger.warning("[ExtDev] %s 未知 parse_mode: %s", conn.name, mode)
                return None
        except Exception as e:
            logger.error("[ExtDev] %s 解析异常: %s", conn.name, e)
            return None

    def _parse_direct(self, raw: str, device_role: str) -> dict:
        raw = raw.strip()
        if device_role == "weight":
            num = re.search(r"[-+]?\d*\.?\d+", raw)
            if num:
                return {"weight": float(num.group()), "raw": raw}
        return {"value": raw, "raw": raw}

    def _parse_regex(self, raw: str, cfg: dict) -> Optional[dict]:
        pattern = cfg.get("pattern", "")
        fields = cfg.get("fields", {})
        m = re.search(pattern, raw)
        if not m:
            return None
        result = {"raw": raw}
        for name, group_idx in fields.items():
            try:
                val = m.group(int(group_idx))
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    pass
                result[name] = val
            except (IndexError, TypeError):
                pass
        return result

    def _parse_split(self, raw: str, cfg: dict) -> Optional[dict]:
        delimiter = cfg.get("delimiter", ",")
        fields = cfg.get("fields", {})
        parts = raw.split(delimiter)
        result = {"raw": raw}
        for name, idx in fields.items():
            idx = int(idx)
            if 0 <= idx < len(parts):
                val = parts[idx].strip()
                try:
                    val = float(val)
                except ValueError:
                    pass
                result[name] = val
        return result

    def _parse_json(self, raw: str, cfg: dict) -> Optional[dict]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        fields = cfg.get("fields", {})
        result = {"raw": raw}
        for name, path in fields.items():
            val = data
            for key in path.split("."):
                if isinstance(val, dict):
                    val = val.get(key)
                else:
                    val = None
                    break
            result[name] = val
        return result

    def _validate(self, conn: DeviceConnection, parsed: dict) -> tuple:
        rules = conn.validation_rules
        if not rules:
            return True, None

        for field_name, rule in rules.items():
            val = parsed.get(field_name)
            if val is None:
                continue
            try:
                val = float(val)
            except (ValueError, TypeError):
                continue
            min_val = rule.get("min")
            max_val = rule.get("max")
            if min_val is not None and val < min_val:
                return False, f"{field_name}={val} < 最小值{min_val}"
            if max_val is not None and val > max_val:
                return False, f"{field_name}={val} > 最大值{max_val}"
        return True, None

    def _dispatch(self, conn: DeviceConnection, parsed: dict, barcode: str = None):
        target = conn.data_target

        if target in ("cluster", "both"):
            self._dispatch_to_cluster(conn, parsed, barcode)

        if target in ("extra_fields", "both"):
            self._dispatch_to_extra(conn, parsed)

    def _dispatch_to_cluster(self, conn: DeviceConnection, parsed: dict, barcode: str = None):
        if not conn.station_id:
            logger.warning("[ExtDev] %s 未配置 station_id，无法注入集群", conn.name)
            return
        if not barcode:
            logger.debug("[ExtDev] %s 无条码，暂存数据等待扫码", conn.name)
            return

        try:
            from backend.services.cluster_collector import get_cluster_collector
            collector = get_cluster_collector()
            config = collector.get_config()
            if not config.get("enabled"):
                return

            is_good = True
            event_name = "ok"
            rules = conn.validation_rules
            if rules:
                for field_name, rule in rules.items():
                    val = parsed.get(field_name)
                    if val is None:
                        continue
                    try:
                        val = float(val)
                    except (ValueError, TypeError):
                        continue
                    min_val = rule.get("min")
                    max_val = rule.get("max")
                    if (min_val is not None and val < min_val) or \
                       (max_val is not None and val > max_val):
                        is_good = False
                        event_name = f"{field_name}_out_of_range"
                        break

            cycle_context = {
                "cycle": {
                    "result": "OK" if is_good else "NG",
                    "is_good": is_good,
                },
                "workpiece": {"serial_no": barcode},
                "device_data": parsed,
                "device_name": conn.name,
                "device_role": conn.device_role,
            }

            collector.receive_station_report(
                station_id=conn.station_id,
                box_serial=barcode,
                cycle_context=cycle_context,
                source_address=f"{conn.protocol}:{conn.ip or conn.serial_port}:{conn.port or ''}",
                channel_id=conn.channel_id,
                is_good=is_good,
                event_name=event_name,
            )
            logger.info("[ExtDev] %s → 集群汇总: %s = %s (%s)",
                        conn.name, barcode, parsed, "OK" if is_good else "NG")
        except Exception as e:
            logger.error("[ExtDev] %s 集群注入失败: %s", conn.name, e)

    def _dispatch_to_extra(self, conn: DeviceConnection, parsed: dict):
        try:
            from backend.services.mes_gateway import get_mes_gateway
            gw = get_mes_gateway()
            ch = conn.channel_id or 0
            current = gw.get_extra_fields(ch)
            current.update({k: v for k, v in parsed.items() if k != "raw"})
            gw.set_extra_fields(ch, current)
            logger.info("[ExtDev] %s → extra_fields channel %d: %s",
                        conn.name, ch, parsed)
        except Exception as e:
            logger.error("[ExtDev] %s extra_fields 注入失败: %s", conn.name, e)

    def _log_data(self, conn: DeviceConnection, raw: str, parsed: dict,
                  is_valid: bool, error: str = None, barcode: str = None):
        """写入 external_device_logs。

        v2.7.9: 高频推送（称重器每秒多次）+ 检测引擎并发写 → 容易 locked。
        原本 except Exception: pass 直接吞错导致前端"数据日志"永远空。
        现在做 3 次 locked 退避重试，其它异常打印出来方便排障。
        """
        import random
        from sqlalchemy.exc import OperationalError
        for attempt in range(3):
            db = None
            try:
                db = SessionLocal()
                log = ExternalDeviceLog(
                    device_id=conn.device_id,
                    raw_data=raw[:1024] if raw else None,
                    parsed_data=parsed,
                    box_serial=barcode,
                    is_valid=is_valid,
                    error_msg=error,
                )
                db.add(log)
                db.commit()
                return
            except OperationalError as e:
                if db is not None:
                    try:
                        db.rollback()
                    except Exception:
                        pass
                msg = str(e).lower()
                if ("locked" in msg or "busy" in msg) and attempt < 2:
                    time.sleep(0.05 + random.random() * 0.1)
                    continue
                logger.warning("[ExtDev] %s 日志写入失败 (attempt %d): %s",
                               conn.name, attempt + 1, e)
                return
            except Exception as e:
                if db is not None:
                    try:
                        db.rollback()
                    except Exception:
                        pass
                logger.warning("[ExtDev] %s 日志写入异常: %s", conn.name, e)
                return
            finally:
                if db is not None:
                    try:
                        db.close()
                    except Exception:
                        pass
