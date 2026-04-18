"""
报警器管理 API
支持 USB 串口报警器的检测、配置和控制
支持多通道（每个工位独立串口设备）
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
import serial
import serial.tools.list_ports
import threading
import time
import json
import os

router = APIRouter()

ALARM_CONFIG_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'alarm_config.json')

PROTOCOLS = {
    'modbus_4color': {
        'name': 'MODBUS四色指示灯（推荐）',
        'red_on': bytes([0x01, 0x05, 0x00, 0x01, 0x01, 0x00, 0x9D, 0x9A]),
        'red_off': bytes([0x01, 0x05, 0x00, 0x01, 0x00, 0x00, 0x9C, 0x0A]),
        'red_slow': bytes([0x01, 0x05, 0x00, 0x01, 0x02, 0x00, 0x9D, 0x6A]),
        'red_fast': bytes([0x01, 0x05, 0x00, 0x01, 0x03, 0x00, 0x9C, 0xFA]),
        'green_on': bytes([0x01, 0x05, 0x00, 0x02, 0x01, 0x00, 0x6D, 0x9A]),
        'green_off': bytes([0x01, 0x05, 0x00, 0x02, 0x00, 0x00, 0x6C, 0x0A]),
        'green_slow': bytes([0x01, 0x05, 0x00, 0x02, 0x02, 0x00, 0x6D, 0x6A]),
        'green_fast': bytes([0x01, 0x05, 0x00, 0x02, 0x03, 0x00, 0x6C, 0xFA]),
        'blue_on': bytes([0x01, 0x05, 0x00, 0x03, 0x01, 0x00, 0x3C, 0x5A]),
        'blue_off': bytes([0x01, 0x05, 0x00, 0x03, 0x00, 0x00, 0x3D, 0xCA]),
        'yellow_on': bytes([0x01, 0x05, 0x00, 0x04, 0x01, 0x00, 0x8D, 0x9B]),
        'yellow_off': bytes([0x01, 0x05, 0x00, 0x04, 0x00, 0x00, 0x8C, 0x0B]),
        'buzzer_on': bytes([0x01, 0x05, 0x00, 0x05, 0x01, 0x00, 0xDC, 0x5B]),
        'buzzer_off': bytes([0x01, 0x05, 0x00, 0x05, 0x00, 0x00, 0xDD, 0xCB]),
        'buzzer_slow': bytes([0x01, 0x05, 0x00, 0x05, 0x02, 0x00, 0xDC, 0xAB]),
        'buzzer_fast': bytes([0x01, 0x05, 0x00, 0x05, 0x03, 0x00, 0xDD, 0x3B]),
        'red_buzzer_on': bytes([0x01, 0x05, 0x00, 0x06, 0x01, 0x00, 0x2C, 0x5B]),
        'red_buzzer_off': bytes([0x01, 0x05, 0x00, 0x06, 0x00, 0x00, 0x2D, 0xCB]),
        'red_buzzer_fast': bytes([0x01, 0x05, 0x00, 0x06, 0x03, 0x00, 0x2D, 0x3B]),
        'green_buzzer_on': bytes([0x01, 0x05, 0x00, 0x07, 0x01, 0x00, 0x7D, 0x9B]),
        'green_buzzer_off': bytes([0x01, 0x05, 0x00, 0x07, 0x00, 0x00, 0x7C, 0x0B]),
        'all_off': bytes([0x01, 0x05, 0x00, 0x0A, 0x00, 0x00, 0xED, 0xC8]),
        'light_on': bytes([0x01, 0x05, 0x00, 0x01, 0x01, 0x00, 0x9D, 0x9A]),
        'light_off': bytes([0x01, 0x05, 0x00, 0x0A, 0x00, 0x00, 0xED, 0xC8]),
        'all_on': bytes([0x01, 0x05, 0x00, 0x06, 0x01, 0x00, 0x2C, 0x5B]),
    },
    'simple_ascii': {
        'name': '简单ASCII',
        'light_on': b'1',
        'light_off': b'0',
        'buzzer_on': b'2',
        'buzzer_off': b'3',
        'all_on': b'A',
        'all_off': b'B',
    },
    'hex_simple': {
        'name': '简单十六进制',
        'light_on': bytes([0x01]),
        'light_off': bytes([0x00]),
        'buzzer_on': bytes([0x02]),
        'buzzer_off': bytes([0x03]),
        'all_on': bytes([0xFF]),
        'all_off': bytes([0x00]),
    },
    'hex_relay': {
        'name': '继电器控制',
        'light_on': bytes([0xA0, 0x01, 0x01, 0xA2]),
        'light_off': bytes([0xA0, 0x01, 0x00, 0xA1]),
        'buzzer_on': bytes([0xA0, 0x02, 0x01, 0xA3]),
        'buzzer_off': bytes([0xA0, 0x02, 0x00, 0xA2]),
        'all_on': bytes([0xA0, 0x01, 0x01, 0xA2]),
        'all_off': bytes([0xA0, 0x01, 0x00, 0xA1]),
    },
    'custom': {
        'name': '自定义',
        'light_on': b'',
        'light_off': b'',
        'buzzer_on': b'',
        'buzzer_off': b'',
        'all_on': b'',
        'all_off': b'',
    }
}

DEFAULT_CHANNEL_CONFIG = {
    'enabled': False,
    'port': '',
    'baudrate': 9600,
    'protocol': 'modbus_4color',
    'custom_commands': {
        'light_on': '',
        'light_off': '',
        'buzzer_on': '',
        'buzzer_off': '',
    },
    'triggers': {
        'event1': {'name': '合格', 'enabled': True, 'color': 'green', 'effect': 'on', 'buzzer': False, 'duration': 2},
        'event2': {'name': 'NG', 'enabled': True, 'color': 'red', 'effect': 'fast', 'buzzer': True, 'duration': 5},
    },
    'idle_light': {
        'enabled': True,
        'color': 'blue',
    },
    'test_mode': False,
}


class AlarmManager:
    """单个报警器管理器（对应一个串口设备）"""

    def __init__(self, config: Optional[dict] = None):
        self.serial_port: Optional[serial.Serial] = None
        self.port_name: Optional[str] = None
        self.config = config if config else dict(DEFAULT_CHANNEL_CONFIG)
        self._alarm_thread: Optional[threading.Thread] = None
        self._alarm_stop_event = threading.Event()
        self._idle_light_active = False

    def connect(self, port: str, baudrate: int = 9600) -> dict:
        """连接串口，返回 {"ok": bool, "msg": str}"""
        try:
            if self.serial_port and self.serial_port.is_open:
                self.serial_port.close()

            self.serial_port = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1
            )
            self.port_name = port
            self.config['port'] = port
            self.config['baudrate'] = baudrate
            print(f"报警器已连接: {port} @ {baudrate}")
            return {"ok": True, "msg": f"已连接 {port}"}
        except PermissionError:
            hint = self._try_fix_permission(port)
            msg = f"串口权限不足: {port}。{hint}"
            print(f"连接报警器失败(权限): {msg}")
            return {"ok": False, "msg": msg}
        except serial.SerialException as e:
            err = str(e)
            if "FileNotFoundError" in err or "No such file" in err:
                msg = f"串口不存在: {port}（请检查USB是否插好、驱动是否安装）"
            elif "PermissionError" in err or "Access is denied" in err:
                hint = self._try_fix_permission(port)
                msg = f"串口权限不足: {port}。{hint}"
            else:
                msg = f"串口打开失败: {err}"
            print(f"连接报警器失败: {msg}")
            return {"ok": False, "msg": msg}
        except Exception as e:
            msg = f"连接异常: {e}"
            print(f"连接报警器失败: {msg}")
            return {"ok": False, "msg": msg}

    @staticmethod
    def _try_fix_permission(port: str) -> str:
        """Linux 上尝试自动修复串口权限"""
        import platform
        if platform.system() != "Linux":
            return "Windows 请检查：1)设备管理器是否有COM口 2)CH340驱动是否安装 3)端口是否被其他程序占用"
        try:
            import subprocess
            subprocess.run(["chmod", "666", port], timeout=3, check=True)
            return "已自动修复权限，请重试连接"
        except Exception:
            return "请执行: sudo usermod -aG dialout $USER 然后重启电脑，或执行 sudo chmod 666 " + port

    def disconnect(self):
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
        self.serial_port = None
        self.port_name = None

    def is_connected(self) -> bool:
        return self.serial_port is not None and self.serial_port.is_open

    def _send_command(self, command: bytes) -> bool:
        if not self.is_connected():
            return False
        try:
            self.serial_port.write(command)
            self.serial_port.flush()
            return True
        except Exception as e:
            print(f"发送命令失败: {e}")
            return False

    def _get_command(self, action: str) -> bytes:
        protocol = self.config.get('protocol', 'simple_ascii')

        if protocol == 'custom':
            custom = self.config.get('custom_commands', {})
            cmd_str = custom.get(action, '')
            if cmd_str:
                try:
                    if ' ' in cmd_str:
                        return bytes([int(x, 16) for x in cmd_str.split()])
                    else:
                        return cmd_str.encode()
                except:
                    return cmd_str.encode()
            return b''
        else:
            return PROTOCOLS.get(protocol, {}).get(action, b'')

    def light_on(self) -> bool:
        return self._send_command(self._get_command('light_on'))

    def light_off(self) -> bool:
        return self._send_command(self._get_command('light_off'))

    def buzzer_on(self) -> bool:
        return self._send_command(self._get_command('buzzer_on'))

    def buzzer_off(self) -> bool:
        return self._send_command(self._get_command('buzzer_off'))

    def all_on(self) -> bool:
        return self._send_command(self._get_command('all_on'))

    def all_off(self) -> bool:
        return self._send_command(self._get_command('all_off'))

    def trigger_alarm(self, event_type: str = 'event2'):
        if not self.config.get('enabled'):
            return

        trigger_config = self.config.get('triggers', {}).get(event_type, {})
        if not trigger_config.get('enabled'):
            return

        duration = trigger_config.get('duration', 3)
        color = trigger_config.get('color', 'red')
        effect = trigger_config.get('effect', 'on')
        use_buzzer = trigger_config.get('buzzer', False)
        protocol = self.config.get('protocol', 'modbus_4color')

        try:
            if protocol == 'modbus_4color':
                if color and color != 'none':
                    effect_map = {'on': 'on', 'slow': 'slow', 'fast': 'fast'}
                    effect_suffix = effect_map.get(effect, 'on')

                    if use_buzzer and color in ['red', 'green']:
                        if effect_suffix == 'on':
                            cmd = f'{color}_buzzer_on'
                        else:
                            cmd = f'{color}_buzzer_{effect_suffix}'
                    else:
                        cmd = f'{color}_{effect_suffix}'
                        if use_buzzer:
                            buzzer_cmd = f'buzzer_{effect_suffix}' if effect_suffix != 'on' else 'buzzer_on'
                            self._send_command(self._get_command(buzzer_cmd))

                    self._send_command(self._get_command(cmd))
                elif use_buzzer:
                    effect_suffix = {'on': 'on', 'slow': 'slow', 'fast': 'fast'}.get(effect, 'on')
                    buzzer_cmd = f'buzzer_{effect_suffix}' if effect_suffix != 'on' else 'buzzer_on'
                    self._send_command(self._get_command(buzzer_cmd))
            else:
                if color and color != 'none':
                    self.light_on()
                if use_buzzer:
                    self.buzzer_on()

            def delayed_off():
                time.sleep(duration)
                self._send_command(self._get_command('all_off'))
                if self._idle_light_active:
                    time.sleep(0.05)
                    self.restore_idle_light()

            threading.Thread(target=delayed_off, daemon=True).start()

        except Exception as e:
            print(f"报警执行失败: {e}")

    def stop_alarm(self):
        self._alarm_stop_event.set()
        if self._alarm_thread and self._alarm_thread.is_alive():
            self._alarm_thread.join(timeout=1)
        if self._idle_light_active:
            self.restore_idle_light()
        else:
            self.all_off()

    def start_idle_light(self):
        idle_cfg = self.config.get('idle_light', {})
        if not idle_cfg.get('enabled'):
            return
        if not self.config.get('enabled'):
            return
        color = idle_cfg.get('color', 'blue')
        cmd = self._get_command(f'{color}_on')
        if cmd:
            self._send_command(self._get_command('all_off'))
            time.sleep(0.05)
            self._send_command(cmd)
            self._idle_light_active = True
            print(f"[报警] 工作指示灯已亮: {color}")

    def stop_idle_light(self):
        self._idle_light_active = False
        self.all_off()
        print("[报警] 工作指示灯已关")

    def restore_idle_light(self):
        if not self._idle_light_active:
            return
        idle_cfg = self.config.get('idle_light', {})
        color = idle_cfg.get('color', 'blue')
        cmd = self._get_command(f'{color}_on')
        if cmd:
            self._send_command(cmd)


class AlarmRouter:
    """按通道路由的报警管理器集合"""

    def __init__(self):
        self.managers: dict[int, AlarmManager] = {}
        self._load_all()

    def _load_all(self):
        """从配置文件加载所有通道的报警管理器，并自动连接已启用的"""
        raw = self._read_file()
        if 'channels' in raw:
            for ch_str, ch_cfg in raw['channels'].items():
                ch_id = int(ch_str)
                merged = dict(DEFAULT_CHANNEL_CONFIG)
                merged.update(ch_cfg)
                self.managers[ch_id] = AlarmManager(config=merged)
        else:
            merged = dict(DEFAULT_CHANNEL_CONFIG)
            merged.update(raw)
            self.managers[0] = AlarmManager(config=merged)

        for ch_id, mgr in self.managers.items():
            if mgr.config.get('enabled') and mgr.config.get('port'):
                try:
                    result = mgr.connect(mgr.config['port'], mgr.config.get('baudrate', 9600))
                    if result["ok"]:
                        print(f"[报警] ch{ch_id} 自动连接成功: {mgr.config['port']}")
                    else:
                        print(f"[报警] ch{ch_id} 自动连接失败: {result['msg']}")
                except Exception as e:
                    print(f"[报警] ch{ch_id} 自动连接异常: {e}")

    def _read_file(self) -> dict:
        try:
            os.makedirs(os.path.dirname(ALARM_CONFIG_FILE), exist_ok=True)
            if os.path.exists(ALARM_CONFIG_FILE):
                with open(ALARM_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"加载报警配置失败: {e}")
        return {}

    def _save_all(self):
        channels_data = {}
        for ch_id, mgr in self.managers.items():
            channels_data[str(ch_id)] = mgr.config
        data = {'channels': channels_data}
        try:
            os.makedirs(os.path.dirname(ALARM_CONFIG_FILE), exist_ok=True)
            with open(ALARM_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存报警配置失败: {e}")

    def get(self, channel_id: int = 0) -> AlarmManager:
        if channel_id not in self.managers:
            self.managers[channel_id] = AlarmManager(config=dict(DEFAULT_CHANNEL_CONFIG))
        return self.managers[channel_id]

    def trigger_alarm(self, event_type: str, channel_id: int = 0):
        self.get(channel_id).trigger_alarm(event_type)

    def start_idle_light(self, channel_id: int = 0):
        self.get(channel_id).start_idle_light()

    def stop_idle_light(self, channel_id: int = 0):
        self.get(channel_id).stop_idle_light()

    def stop_alarm(self, channel_id: int = 0):
        self.get(channel_id).stop_alarm()

    def disconnect_all(self):
        for mgr in self.managers.values():
            mgr.disconnect()

    def on_channel_removed(self, channel_id: int):
        """工位被移除（降工位）时调用：停止报警、熄灭灯塔、释放串口、移除 manager。
        避免降工位后 ch1 的蜂鸣器/灯塔线程仍在运行，或串口被占用导致升回后无法重连。
        v2.7.2"""
        mgr = self.managers.pop(channel_id, None)
        if mgr is None:
            return
        try:
            mgr.stop_alarm()
        except Exception as e:
            print(f"[报警] ch{channel_id} stop_alarm 失败: {e}")
        try:
            mgr._idle_light_active = False
            mgr.all_off()
        except Exception as e:
            print(f"[报警] ch{channel_id} all_off 失败: {e}")
        try:
            mgr.disconnect()
        except Exception as e:
            print(f"[报警] ch{channel_id} disconnect 失败: {e}")
        print(f"[报警] ch{channel_id} 被移除，已停报警+熄灯+断串口", flush=True)

    @staticmethod
    def list_ports() -> List[dict]:
        ports = []
        for port in serial.tools.list_ports.comports():
            is_usb = (
                port.vid is not None or
                'ttyUSB' in port.device or
                'ttyACM' in port.device or
                'CH340' in (port.description or '') or
                'USB' in (port.description or '').upper()
            )
            if is_usb:
                ports.append({
                    'port': port.device,
                    'description': port.description,
                    'manufacturer': port.manufacturer or '未知',
                    'vid': port.vid,
                    'pid': port.pid,
                })
        return ports


# 全局路由实例 — 替代旧的 alarm_manager 单例
alarm_router = AlarmRouter()

# 向后兼容：旧代码 `from backend.api.alarm import alarm_manager` 仍能工作
alarm_manager = alarm_router.get(0)


# ========== API 路由 ==========

class ConnectRequest(BaseModel):
    port: str
    baudrate: int = 9600

class ConfigRequest(BaseModel):
    enabled: bool = False
    protocol: str = 'simple_ascii'
    custom_commands: Optional[dict] = None
    triggers: Optional[dict] = None
    idle_light: Optional[dict] = None
    test_mode: bool = False

class TestRequest(BaseModel):
    action: str


@router.get("/ports")
async def list_ports(channel: int = Query(0, description="工位通道")):
    """列出所有可用的串口设备"""
    ports = AlarmRouter.list_ports()
    mgr = alarm_router.get(channel)
    return {
        "success": True,
        "ports": ports,
        "current_port": mgr.port_name,
        "is_connected": mgr.is_connected(),
        "channel": channel,
    }


@router.post("/connect")
async def connect(req: ConnectRequest, channel: int = Query(0, description="工位通道")):
    """连接到报警器"""
    mgr = alarm_router.get(channel)
    result = mgr.connect(req.port, req.baudrate)
    if result["ok"]:
        alarm_router._save_all()
        return {"success": True, "message": f"工位 {channel} 已连接到 {req.port}"}
    else:
        raise HTTPException(status_code=500, detail=result["msg"])


@router.post("/disconnect")
async def disconnect(channel: int = Query(0, description="工位通道")):
    """断开报警器连接"""
    alarm_router.get(channel).disconnect()
    return {"success": True, "message": f"工位 {channel} 已断开连接"}


@router.get("/status")
async def get_status(channel: int = Query(-1, description="工位通道，-1 返回全部")):
    """获取报警器状态"""
    if channel >= 0:
        mgr = alarm_router.get(channel)
        return {
            "success": True,
            "channel": channel,
            "is_connected": mgr.is_connected(),
            "port": mgr.port_name,
            "config": mgr.config,
        }
    else:
        all_status = {}
        for ch_id, mgr in alarm_router.managers.items():
            all_status[ch_id] = {
                "is_connected": mgr.is_connected(),
                "port": mgr.port_name,
                "config": mgr.config,
            }
        return {"success": True, "channels": all_status}


@router.post("/config")
async def save_config(req: ConfigRequest, channel: int = Query(0, description="工位通道")):
    """保存报警器配置"""
    mgr = alarm_router.get(channel)
    mgr.config['enabled'] = req.enabled
    mgr.config['protocol'] = req.protocol
    mgr.config['test_mode'] = req.test_mode

    if req.custom_commands:
        mgr.config['custom_commands'] = req.custom_commands
    if req.triggers:
        mgr.config['triggers'] = req.triggers
    if req.idle_light:
        mgr.config['idle_light'] = req.idle_light

    alarm_router._save_all()
    return {"success": True, "message": f"工位 {channel} 配置已保存"}


@router.post("/test")
async def test_alarm(req: TestRequest, channel: int = Query(0, description="工位通道")):
    """测试报警器"""
    mgr = alarm_router.get(channel)
    if not mgr.is_connected():
        raise HTTPException(status_code=400, detail="报警器未连接")

    if not mgr.config.get('test_mode'):
        raise HTTPException(status_code=400, detail="请先开启测试模式")

    cmd = mgr._get_command(req.action)
    if cmd:
        success = mgr._send_command(cmd)
        return {"success": success, "action": req.action, "channel": channel}
    else:
        raise HTTPException(status_code=400, detail=f"无效的动作: {req.action}")


@router.post("/trigger/{event_type}")
async def trigger_alarm(event_type: str, channel: int = Query(0, description="工位通道")):
    """手动触发报警"""
    mgr = alarm_router.get(channel)
    triggers = mgr.config.get('triggers', {})
    if event_type not in triggers:
        raise HTTPException(status_code=400, detail=f"未配置的事件类型: {event_type}")

    mgr.trigger_alarm(event_type)
    return {"success": True, "message": f"工位 {channel} 已触发 {event_type} 报警"}


@router.post("/stop")
async def stop_alarm(channel: int = Query(0, description="工位通道")):
    """停止报警"""
    alarm_router.stop_alarm(channel)
    return {"success": True, "message": f"工位 {channel} 已停止报警"}


@router.post("/idle-light/start")
async def start_idle_light(channel: int = Query(0, description="工位通道")):
    """手动开启工作指示灯"""
    alarm_router.start_idle_light(channel)
    return {"success": True, "message": f"工位 {channel} 工作指示灯已开启"}


@router.post("/idle-light/stop")
async def stop_idle_light(channel: int = Query(0, description="工位通道")):
    """手动关闭工作指示灯"""
    alarm_router.stop_idle_light(channel)
    return {"success": True, "message": f"工位 {channel} 工作指示灯已关闭"}


@router.get("/protocols")
async def list_protocols():
    """列出支持的协议"""
    protocols = []
    for key, value in PROTOCOLS.items():
        protocols.append({
            'id': key,
            'name': value['name'],
        })
    return {"success": True, "protocols": protocols}
