"""外设协议 `modbus_pulse` —— 向 PLC 写「完成脉冲」(控气阀等执行机构)。

与其它外设协议的区别: 这是**唯一只写不读**的协议。别的协议是"设备吐数据 →
主程序解析入库"，本协议是"主程序判定完成 → 给 PLC 一个点动信号"。

典型场景 (台达 DVP32ES3 + 逐件覆盖/打螺丝):
    工人把一块板上的螺丝全部打完 → per_item 周期判 OK 落账 → 本协议给 PLC 的
    M100 写 ON 300ms 再写 OFF → PLC 程序检测到 M100 上升沿 → 控气阀吹气/推料。

线程模型 (关键不变量):
    推理线程只调 `notify_per_item_complete()` / `_enqueue_pulse()`，做的事就是
    "比一下冷却时间 + 往 deque 里 append + set 一个 Event"，**不碰任何 socket /
    串口 / 数据库**。真正的 Modbus 写由该设备自己的 `extdev-<id>` 线程在
    `_modbus_pulse_loop` 里执行。所以:
      - PLC 断线 / 网络不可达 → 最多卡住这台外设的线程，不影响检测帧率
      - 任何异常都吞在设备线程里, 只落 `conn.last_error` + external_device_logs

protocol_config 字段:
    transport      "tcp"(默认) | "rtu"
    host           tcp 用; 留空回落设备的 IP 字段
    tcp_port       tcp 用; 留空回落设备的端口字段, 再回落 502
    baudrate/parity/data_bits/stop_bits
                   rtu 用; 留空回落设备的串口/波特率字段
    slave_id       从站号, 默认 1
    target         "coil"(默认, 写线圈 功能码 05) | "register"(写保持寄存器 功能码 06)
    address        地址数字, 语义由 address_mode 决定
    address_mode   "delta_m"(默认, address 直接填台达 M 编号, 内部 +2048)
                   | "raw"(address 就是报文里的 0 基地址)
                   | "doc1"(address 是 1 基文档编号, 如 00001 / 40001 风格)
    on_value       脉冲有效值, 默认 1 (线圈按真假, 寄存器按数值)
    off_value      脉冲复位值, 默认 0
    pulse_ms       脉冲宽度毫秒, 默认 300 (0 = 只写 on 不复位, 由 PLC 自己清)
    timeout        单次 Modbus 连接/读写超时秒, 默认 3
    trigger_enabled  是否接受 per_item 自动触发, 默认 True (关掉只留手动试发)
    trigger_mode   "cycle_ok"(默认) 周期判 OK 落账才发
                   | "all_covered" 本周期所有 per_item 步骤首次全覆盖就发(不等结算)
    cooldown_ms    两次脉冲最小间隔毫秒, 默认 1000; 手动试发不受此限
"""
import logging
import threading
import time
from typing import Optional

from backend.services.external_device_models import DeviceConnection

logger = logging.getLogger(__name__)

# 台达 DVP 系列 (含 DVP32ES3) M 继电器: M0 → DVP 内部 0x0800 → Modbus 0 基地址 2048。
# 即 M100 → 2048 + 100 = 2148。部分上位机软件/手册按 1 基编号写作 002149，
# pymodbus 走 0 基, 所以这里用 2048 打底。现场以台达手册的 Modbus 对应表为准。
DELTA_M_COIL_BASE = 2048

DEFAULT_PULSE_MS = 300
DEFAULT_TRIGGER_MODE = "cycle_ok"
DEFAULT_COOLDOWN_MS = 1000
TRIGGER_MODES = ("cycle_ok", "all_covered")

# RTU 走物理串口, 多台设备/多次脉冲不能并发写同一条 485 总线。
_rtu_write_lock = threading.Lock()


def resolve_pulse_address(cfg: dict) -> int:
    """把界面上填的地址换算成 Modbus 报文里的 0 基地址。"""
    try:
        raw = int(cfg.get("address", 0) or 0)
    except (TypeError, ValueError):
        raw = 0
    mode = str(cfg.get("address_mode", "delta_m")).lower()
    if mode == "delta_m":
        return DELTA_M_COIL_BASE + raw
    if mode == "doc1":
        # 1 基文档编号: 00001-09999 线圈 / 30001- 输入寄存器 / 40001- 保持寄存器
        if 40001 <= raw <= 49999:
            return raw - 40001
        if 30001 <= raw <= 39999:
            return raw - 30001
        if raw >= 1:
            return raw - 1
        return 0
    return raw


def pulse_slave_kwarg(client_cls) -> str:
    """pymodbus 3.13+ 把 `slave=` 改名 `device_id=`，两版都要能跑。"""
    try:
        import inspect
        sig = inspect.signature(client_cls.write_coil)
        if "device_id" in sig.parameters:
            return "device_id"
    except Exception:
        pass
    return "slave"


def describe_pulse_target(cfg: dict) -> str:
    """给日志/UI 用的一句话目标描述, 如 'coil@2148 (台达 M100)'。"""
    target = str(cfg.get("target", "coil")).lower()
    addr = resolve_pulse_address(cfg)
    mode = str(cfg.get("address_mode", "delta_m")).lower()
    if mode == "delta_m":
        return f"{target}@{addr} (台达 M{cfg.get('address', 0)})"
    return f"{target}@{addr}"


class ExternalDevicePulseMixin:
    """`modbus_pulse` 协议循环 + 脉冲入队 + per_item 触发分发。

    宿主依赖 (ExternalDeviceService):
      - self._connections: dict[int, DeviceConnection]
      - self._log_data(conn, raw, parsed, is_valid, error)
    """

    # ──────────────────── 协议循环 (设备线程) ────────────────────
    def _modbus_pulse_loop(self, conn: DeviceConnection):
        """只写不读: 起来先探一次连通性给界面状态, 然后常驻等脉冲请求。

        本函数直到 stop_event 才返回 —— 不能因为"探测失败"就 return, 否则外层
        `_device_loop` 会当成断线不停 backoff 重连, PLC 没上电时刷一堆日志。
        """
        cfg = conn.protocol_config or {}
        ok, msg = self._pulse_probe(conn)
        conn.status = "connected" if ok else "error"
        conn.last_error = "" if ok else msg
        logger.info("[ExtDev] %s 完成脉冲通道就绪 (%s, %s): %s",
                    conn.name, str(cfg.get("transport", "tcp")).lower(),
                    describe_pulse_target(cfg), msg)

        while not conn._stop_event.is_set():
            conn._pulse_wakeup.wait(timeout=0.5)
            conn._pulse_wakeup.clear()
            while conn._pulse_queue and not conn._stop_event.is_set():
                req = conn._pulse_queue.popleft()
                try:
                    result = self._pulse_execute(conn, req)
                except Exception as e:
                    # 兜底: 任何漏网异常都不许把设备线程带走
                    result = {"success": False, "message": f"脉冲执行异常: {e}"}
                    logger.error("[ExtDev] %s 脉冲执行异常: %s", conn.name, e)
                req["result"] = result
                done = req.get("done")
                if done is not None:
                    done.set()

    def _pulse_build_client(self, conn: DeviceConnection):
        """按 transport 建 pymodbus 客户端, 返回 (client_cls, client)。"""
        cfg = conn.protocol_config or {}
        transport = str(cfg.get("transport", "tcp")).lower()
        timeout = float(cfg.get("timeout", 3) or 3)

        if transport == "rtu":
            from pymodbus.client import ModbusSerialClient
            port = cfg.get("port") or conn.serial_port
            if not port:
                raise ValueError("未配置串口号")
            return ModbusSerialClient, ModbusSerialClient(
                port=port,
                baudrate=int(cfg.get("baudrate") or conn.serial_baud or 9600),
                parity=cfg.get("parity", "N"),
                stopbits=cfg.get("stop_bits", cfg.get("stopbits", 1)),
                bytesize=cfg.get("data_bits", cfg.get("bytesize", 8)),
                timeout=timeout,
            )

        from pymodbus.client import ModbusTcpClient
        host = cfg.get("host") or conn.ip
        if not host:
            raise ValueError("未配置 PLC 的 IP 地址")
        return ModbusTcpClient, ModbusTcpClient(
            host=host,
            port=int(cfg.get("tcp_port") or conn.port or 502),
            timeout=timeout,
        )

    @staticmethod
    def _pulse_write_once(client, client_cls, cfg: dict, address: int, value: int):
        """写一次线圈/寄存器, 返回 pymodbus 响应对象。"""
        slave_kw = pulse_slave_kwarg(client_cls)
        slave_id = int(cfg.get("slave_id", 1) or 1)
        if str(cfg.get("target", "coil")).lower() == "register":
            return client.write_register(
                address=address, value=int(value) & 0xFFFF, **{slave_kw: slave_id})
        return client.write_coil(
            address=address, value=bool(value), **{slave_kw: slave_id})

    def _pulse_probe(self, conn: DeviceConnection) -> tuple:
        """只连一下不写任何值 —— 给界面一个"通不通"的初始状态。"""
        cfg = conn.protocol_config or {}
        try:
            _cls, client = self._pulse_build_client(conn)
        except ImportError:
            return False, "pymodbus 未安装"
        except Exception as e:
            return False, str(e)
        try:
            if client.connect():
                return True, "连接成功, 等待完成脉冲"
            transport = str(cfg.get("transport", "tcp")).lower()
            target = cfg.get("host") or conn.ip if transport != "rtu" else (
                cfg.get("port") or conn.serial_port)
            return False, f"连接失败: {target} (PLC 未上电 / 网络不通 / 从站号不对)"
        except Exception as e:
            return False, str(e)
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _pulse_execute(self, conn: DeviceConnection, req: dict) -> dict:
        """真正下发一次脉冲: 写 on → 等 pulse_ms → 写 off。"""
        cfg = conn.protocol_config or {}
        transport = str(cfg.get("transport", "tcp")).lower()
        address = resolve_pulse_address(cfg)
        on_value = int(cfg.get("on_value", 1) or 0)
        off_value = int(cfg.get("off_value", 0) or 0)
        pulse_ms = max(0, int(cfg.get("pulse_ms", DEFAULT_PULSE_MS) or 0))
        source = req.get("source") or "manual"
        desc = describe_pulse_target(cfg)
        raw = f"PULSE {desc} on={on_value} off={off_value} {pulse_ms}ms src={source}"
        started = time.time()

        def _finish(success: bool, message: str):
            elapsed = int((time.time() - started) * 1000)
            conn.last_pulse_result = message
            conn.last_pulse_at_wall = started
            if success:
                conn.pulse_count += 1
                conn.status = "connected"
                conn.last_error = ""
                logger.info("[ExtDev] %s 完成脉冲已下发: %s (%dms)", conn.name, desc, elapsed)
            else:
                conn.status = "error"
                conn.last_error = message
                logger.warning("[ExtDev] %s 完成脉冲失败: %s", conn.name, message)
            try:
                self._log_data(conn, raw, {
                    "pulse": "ok" if success else "fail",
                    "target": str(cfg.get("target", "coil")).lower(),
                    "address": address,
                    "pulse_ms": pulse_ms,
                    "source": source,
                    "duration_ms": elapsed,
                }, success, None if success else message)
            except Exception as e:
                logger.debug("[ExtDev] %s 脉冲日志写入失败: %s", conn.name, e)
            return {"success": success, "message": message, "address": address,
                    "duration_ms": elapsed, "source": source}

        try:
            client_cls, client = self._pulse_build_client(conn)
        except ImportError:
            return _finish(False, "pymodbus 未安装, 请 pip install pymodbus")
        except Exception as e:
            return _finish(False, f"配置错误: {e}")

        lock = _rtu_write_lock if transport == "rtu" else None
        try:
            if lock is not None:
                lock.acquire()
            if not client.connect():
                where = (cfg.get("host") or conn.ip) if transport != "rtu" else (
                    cfg.get("port") or conn.serial_port)
                return _finish(False, f"连接 PLC 失败: {where}")
            try:
                resp_on = self._pulse_write_once(client, client_cls, cfg, address, on_value)
                if hasattr(resp_on, "isError") and resp_on.isError():
                    return _finish(False, f"写 {desc}={on_value} 失败: {resp_on}")
                if pulse_ms > 0:
                    time.sleep(pulse_ms / 1000.0)
                    resp_off = self._pulse_write_once(
                        client, client_cls, cfg, address, off_value)
                    if hasattr(resp_off, "isError") and resp_off.isError():
                        return _finish(False, f"复位 {desc}={off_value} 失败: {resp_off}")
                return _finish(True, f"已给 {desc} 发 {pulse_ms}ms 脉冲")
            finally:
                try:
                    client.close()
                except Exception:
                    pass
        except Exception as e:
            return _finish(False, f"Modbus 通讯异常: {e}")
        finally:
            if lock is not None:
                lock.release()

    # ──────────────────── 入队 (调用方线程, 无 I/O) ────────────────────
    def _enqueue_pulse(self, conn: DeviceConnection, source: str = "manual",
                       wait: bool = False, respect_cooldown: bool = True) -> dict:
        """把一次脉冲请求排入设备线程队列。**只做时间比较 + append, 不做 I/O。**"""
        cfg = conn.protocol_config or {}
        now = time.time()
        if respect_cooldown:
            cooldown_ms = int(cfg.get("cooldown_ms", DEFAULT_COOLDOWN_MS) or 0)
            if cooldown_ms > 0 and conn._last_pulse_enqueued_at:
                remain = cooldown_ms / 1000.0 - (now - conn._last_pulse_enqueued_at)
                if remain > 0:
                    return {"success": False, "skipped": "cooldown",
                            "message": f"冷却中, 还需 {int(remain * 1000)}ms"}
        conn._last_pulse_enqueued_at = now
        req = {"source": source, "enqueued_at": now,
               "done": threading.Event() if wait else None, "result": None}
        conn._pulse_queue.append(req)
        conn._pulse_wakeup.set()
        return {"success": True, "queued": True, "request": req,
                "message": "脉冲已排入发送队列"}

    def pulse_device(self, device_id: int, source: str = "manual",
                     wait: bool = True) -> dict:
        """手动/API 下发一次脉冲(外部设备页「试发脉冲」按钮)。

        wait=True 时等设备线程真写完再返回真实结果, 便于界面直接给出"通了没"。
        """
        conn = self._connections.get(device_id)
        if conn is None:
            return {"success": False, "message": "设备未连接或未启用"}
        if conn.protocol != "modbus_pulse":
            return {"success": False, "message": "仅「Modbus 完成脉冲」设备支持发脉冲"}

        # 手动试发不受 cooldown 限制 —— 现场调试要能连点
        enq = self._enqueue_pulse(conn, source=source, wait=wait,
                                  respect_cooldown=False)
        if not enq.get("success"):
            return enq
        req = enq.get("request") or {}
        done = req.get("done")
        if done is None:
            return {"success": True, "message": enq["message"], "device_id": device_id}

        cfg = conn.protocol_config or {}
        budget = float(cfg.get("timeout", 3) or 3) * 2 \
            + max(0, int(cfg.get("pulse_ms", DEFAULT_PULSE_MS) or 0)) / 1000.0 + 2.0
        if not done.wait(timeout=budget):
            return {"success": False, "device_id": device_id,
                    "message": f"等待 {budget:.1f}s 未收到执行结果"
                               f"（设备线程是否在跑？设备已启用吗？）"}
        result = req.get("result") or {}
        return {"success": bool(result.get("success")),
                "message": result.get("message") or "无结果",
                "device_id": device_id}

    # ──────────────────── per_item 触发分发 (推理线程) ────────────────────
    def notify_per_item_complete(self, channel_id: Optional[int],
                                 trigger_mode: str) -> int:
        """逐件覆盖完成 → 给绑定本工位的 modbus_pulse 外设排队发脉冲。

        由推理线程调用, 因此: 不查库、不建连接、不抛异常, 返回实际排队的设备数。
        未绑定工位 (channel_id 为空) 的设备一律不发 —— 避免多工位互相误吹。
        """
        if channel_id is None:
            return 0
        mode = str(trigger_mode or "").lower()
        fired = 0
        try:
            for conn in list(self._connections.values()):
                if conn.protocol != "modbus_pulse" or not conn.enabled:
                    continue
                cfg = conn.protocol_config or {}
                if not cfg.get("trigger_enabled", True):
                    continue
                if str(cfg.get("trigger_mode", DEFAULT_TRIGGER_MODE)).lower() != mode:
                    continue
                if conn.channel_id is None:
                    continue
                try:
                    bound = int(conn.channel_id)
                except (TypeError, ValueError):
                    continue
                if bound != int(channel_id):
                    continue
                res = self._enqueue_pulse(conn, source=f"per_item:{mode}", wait=False)
                if res.get("success"):
                    fired += 1
                else:
                    logger.info("[ExtDev] %s 跳过完成脉冲: %s",
                                conn.name, res.get("message"))
        except Exception as e:
            logger.error("[ExtDev] 完成脉冲分发失败 (channel=%s, mode=%s): %s",
                         channel_id, trigger_mode, e)
        return fired

    # ──────────────────── 连通性测试 (test_connection 分支) ────────────────────
    def _test_modbus_pulse(self, ip, port, serial_port, serial_baud,
                           config: dict, timeout: float) -> dict:
        """只连不写: 保存前点「测试」用, 真要动 PLC 请用「试发脉冲」。"""
        cfg = dict(config or {})
        probe_conn = DeviceConnection(
            device_id=-1, name="pulse-test", device_role="plc",
            protocol="modbus_pulse", ip=ip, port=port,
            serial_port=serial_port, serial_baud=serial_baud or 9600,
            protocol_config=cfg, parse_mode="direct", parse_config={},
            station_id=None, channel_id=None, data_target="extra_fields",
            validation_rules={}, enabled=True,
        )
        cfg.setdefault("timeout", timeout)
        ok, msg = self._pulse_probe(probe_conn)
        if ok:
            return {"success": True,
                    "message": f"{msg}；目标 {describe_pulse_target(cfg)}"
                               f"（本测试不写 PLC，确认动作请点「试发脉冲」）"}
        return {"success": False, "message": msg}
