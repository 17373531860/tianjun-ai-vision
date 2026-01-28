"""
报警器管理 API
支持 USB 串口报警器的检测、配置和控制
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import serial
import serial.tools.list_ports
import threading
import time
import json
import os

router = APIRouter()

# 报警器配置文件路径
ALARM_CONFIG_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'alarm_config.json')

# 常见的串口命令协议（用于尝试不同的报警器）
PROTOCOLS = {
    'modbus_4color': {
        'name': 'MODBUS四色指示灯（推荐）',
        # 红灯
        'red_on': bytes([0x01, 0x05, 0x00, 0x01, 0x01, 0x00, 0x9D, 0x9A]),
        'red_off': bytes([0x01, 0x05, 0x00, 0x01, 0x00, 0x00, 0x9C, 0x0A]),
        'red_slow': bytes([0x01, 0x05, 0x00, 0x01, 0x02, 0x00, 0x9D, 0x6A]),  # 慢闪
        'red_fast': bytes([0x01, 0x05, 0x00, 0x01, 0x03, 0x00, 0x9C, 0xFA]),  # 快闪
        # 绿灯
        'green_on': bytes([0x01, 0x05, 0x00, 0x02, 0x01, 0x00, 0x6D, 0x9A]),
        'green_off': bytes([0x01, 0x05, 0x00, 0x02, 0x00, 0x00, 0x6C, 0x0A]),
        'green_slow': bytes([0x01, 0x05, 0x00, 0x02, 0x02, 0x00, 0x6D, 0x6A]),
        'green_fast': bytes([0x01, 0x05, 0x00, 0x02, 0x03, 0x00, 0x6C, 0xFA]),
        # 蓝灯
        'blue_on': bytes([0x01, 0x05, 0x00, 0x03, 0x01, 0x00, 0x3C, 0x5A]),
        'blue_off': bytes([0x01, 0x05, 0x00, 0x03, 0x00, 0x00, 0x3D, 0xCA]),
        # 黄灯
        'yellow_on': bytes([0x01, 0x05, 0x00, 0x04, 0x01, 0x00, 0x8D, 0x9B]),
        'yellow_off': bytes([0x01, 0x05, 0x00, 0x04, 0x00, 0x00, 0x8C, 0x0B]),
        # 蜂鸣器
        'buzzer_on': bytes([0x01, 0x05, 0x00, 0x05, 0x01, 0x00, 0xDC, 0x5B]),
        'buzzer_off': bytes([0x01, 0x05, 0x00, 0x05, 0x00, 0x00, 0xDD, 0xCB]),
        'buzzer_slow': bytes([0x01, 0x05, 0x00, 0x05, 0x02, 0x00, 0xDC, 0xAB]),
        'buzzer_fast': bytes([0x01, 0x05, 0x00, 0x05, 0x03, 0x00, 0xDD, 0x3B]),
        # 红灯+蜂鸣器（报警用）
        'red_buzzer_on': bytes([0x01, 0x05, 0x00, 0x06, 0x01, 0x00, 0x2C, 0x5B]),
        'red_buzzer_off': bytes([0x01, 0x05, 0x00, 0x06, 0x00, 0x00, 0x2D, 0xCB]),
        'red_buzzer_fast': bytes([0x01, 0x05, 0x00, 0x06, 0x03, 0x00, 0x2D, 0x3B]),
        # 绿灯+蜂鸣器
        'green_buzzer_on': bytes([0x01, 0x05, 0x00, 0x07, 0x01, 0x00, 0x7D, 0x9B]),
        'green_buzzer_off': bytes([0x01, 0x05, 0x00, 0x07, 0x00, 0x00, 0x7C, 0x0B]),
        # 全部关闭
        'all_off': bytes([0x01, 0x05, 0x00, 0x0A, 0x00, 0x00, 0xED, 0xC8]),
        # 兼容旧接口
        'light_on': bytes([0x01, 0x05, 0x00, 0x01, 0x01, 0x00, 0x9D, 0x9A]),  # 红灯亮
        'light_off': bytes([0x01, 0x05, 0x00, 0x0A, 0x00, 0x00, 0xED, 0xC8]),  # 全关
        'all_on': bytes([0x01, 0x05, 0x00, 0x06, 0x01, 0x00, 0x2C, 0x5B]),  # 红灯+蜂鸣
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


class AlarmManager:
    """报警器管理器"""
    
    def __init__(self):
        self.serial_port: Optional[serial.Serial] = None
        self.port_name: Optional[str] = None
        self.config = self._load_config()
        self._alarm_thread: Optional[threading.Thread] = None
        self._alarm_stop_event = threading.Event()
        
    def _load_config(self) -> dict:
        """加载报警器配置"""
        default_config = {
            'enabled': False,
            'port': '',
            'baudrate': 9600,
            'protocol': 'modbus_4color',  # 默认使用 MODBUS 四色指示灯
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
            'test_mode': False,  # 测试模式：开启后测试按钮才能使用
        }
        
        try:
            os.makedirs(os.path.dirname(ALARM_CONFIG_FILE), exist_ok=True)
            if os.path.exists(ALARM_CONFIG_FILE):
                with open(ALARM_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    saved_config = json.load(f)
                    default_config.update(saved_config)
        except Exception as e:
            print(f"加载报警配置失败: {e}")
        
        return default_config
    
    def _save_config(self):
        """保存报警器配置"""
        try:
            os.makedirs(os.path.dirname(ALARM_CONFIG_FILE), exist_ok=True)
            with open(ALARM_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存报警配置失败: {e}")
    
    def list_ports(self) -> List[dict]:
        """列出所有可用的串口设备（只显示真正的USB设备）"""
        ports = []
        for port in serial.tools.list_ports.comports():
            # 过滤：只显示USB串口设备（有VID/PID或者是ttyUSB/ttyACM）
            is_usb = (
                port.vid is not None or  # 有USB VID
                'ttyUSB' in port.device or  # USB转串口
                'ttyACM' in port.device or  # USB CDC设备
                'CH340' in (port.description or '') or  # CH340芯片
                'USB' in (port.description or '').upper()  # 描述包含USB
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
    
    def connect(self, port: str, baudrate: int = 9600) -> bool:
        """连接到串口设备"""
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
            self._save_config()
            print(f"报警器已连接: {port} @ {baudrate}")
            return True
        except Exception as e:
            print(f"连接报警器失败: {e}")
            return False
    
    def disconnect(self):
        """断开连接"""
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
        self.serial_port = None
        self.port_name = None
    
    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self.serial_port is not None and self.serial_port.is_open
    
    def _send_command(self, command: bytes) -> bool:
        """发送命令到报警器"""
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
        """获取指定动作的命令"""
        protocol = self.config.get('protocol', 'simple_ascii')
        
        if protocol == 'custom':
            custom = self.config.get('custom_commands', {})
            cmd_str = custom.get(action, '')
            if cmd_str:
                # 支持十六进制格式如 "A0 01 01 A2"
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
        """开灯"""
        return self._send_command(self._get_command('light_on'))
    
    def light_off(self) -> bool:
        """关灯"""
        return self._send_command(self._get_command('light_off'))
    
    def buzzer_on(self) -> bool:
        """开蜂鸣器"""
        return self._send_command(self._get_command('buzzer_on'))
    
    def buzzer_off(self) -> bool:
        """关蜂鸣器"""
        return self._send_command(self._get_command('buzzer_off'))
    
    def all_on(self) -> bool:
        """全部打开"""
        return self._send_command(self._get_command('all_on'))
    
    def all_off(self) -> bool:
        """全部关闭"""
        return self._send_command(self._get_command('all_off'))
    
    def trigger_alarm(self, event_type: str = 'event2'):
        """触发报警（根据配置）"""
        if not self.config.get('enabled'):
            return
        
        trigger_config = self.config.get('triggers', {}).get(event_type, {})
        if not trigger_config.get('enabled'):
            return
        
        duration = trigger_config.get('duration', 3)
        color = trigger_config.get('color', 'red')  # red, green, blue, yellow, none
        effect = trigger_config.get('effect', 'on')  # on, slow, fast
        use_buzzer = trigger_config.get('buzzer', False)
        protocol = self.config.get('protocol', 'modbus_4color')
        
        # 直接执行报警
        try:
            if protocol == 'modbus_4color':
                # 构建命令名称：{color}_{effect} 或 {color}_buzzer_{effect}
                if color and color != 'none':
                    # 效果映射
                    effect_map = {'on': 'on', 'slow': 'slow', 'fast': 'fast'}
                    effect_suffix = effect_map.get(effect, 'on')
                    
                    if use_buzzer and color in ['red', 'green']:
                        # 红灯/绿灯+蜂鸣器组合命令
                        if effect_suffix == 'on':
                            cmd = f'{color}_buzzer_on'
                        else:
                            cmd = f'{color}_buzzer_{effect_suffix}'
                    else:
                        # 单独灯光命令
                        cmd = f'{color}_{effect_suffix}'
                        # 如果需要蜂鸣，单独发送蜂鸣命令
                        if use_buzzer:
                            buzzer_cmd = f'buzzer_{effect_suffix}' if effect_suffix != 'on' else 'buzzer_on'
                            self._send_command(self._get_command(buzzer_cmd))
                    
                    self._send_command(self._get_command(cmd))
                elif use_buzzer:
                    # 只有蜂鸣，没有灯
                    effect_suffix = {'on': 'on', 'slow': 'slow', 'fast': 'fast'}.get(effect, 'on')
                    buzzer_cmd = f'buzzer_{effect_suffix}' if effect_suffix != 'on' else 'buzzer_on'
                    self._send_command(self._get_command(buzzer_cmd))
            else:
                # 其他协议：使用旧逻辑
                if color and color != 'none':
                    self.light_on()
                if use_buzzer:
                    self.buzzer_on()
            
            # 后台线程等待后关闭
            def delayed_off():
                import time
                time.sleep(duration)
                self._send_command(self._get_command('all_off'))
            
            threading.Thread(target=delayed_off, daemon=True).start()
            
        except Exception as e:
            print(f"报警执行失败: {e}")
    
    def stop_alarm(self):
        """停止报警"""
        self._alarm_stop_event.set()
        if self._alarm_thread and self._alarm_thread.is_alive():
            self._alarm_thread.join(timeout=1)
        self.all_off()


# 全局报警管理器实例
alarm_manager = AlarmManager()


# ========== API 路由 ==========

class ConnectRequest(BaseModel):
    port: str
    baudrate: int = 9600

class ConfigRequest(BaseModel):
    enabled: bool = False
    protocol: str = 'simple_ascii'
    custom_commands: Optional[dict] = None
    triggers: Optional[dict] = None
    test_mode: bool = False

class TestRequest(BaseModel):
    action: str  # light_on, light_off, buzzer_on, buzzer_off, all_on, all_off


@router.get("/ports")
async def list_ports():
    """列出所有可用的串口设备"""
    ports = alarm_manager.list_ports()
    return {
        "success": True,
        "ports": ports,
        "current_port": alarm_manager.port_name,
        "is_connected": alarm_manager.is_connected()
    }


@router.post("/connect")
async def connect(req: ConnectRequest):
    """连接到报警器"""
    success = alarm_manager.connect(req.port, req.baudrate)
    if success:
        return {"success": True, "message": f"已连接到 {req.port}"}
    else:
        raise HTTPException(status_code=500, detail="连接失败，请检查设备")


@router.post("/disconnect")
async def disconnect():
    """断开报警器连接"""
    alarm_manager.disconnect()
    return {"success": True, "message": "已断开连接"}


@router.get("/status")
async def get_status():
    """获取报警器状态"""
    return {
        "success": True,
        "is_connected": alarm_manager.is_connected(),
        "port": alarm_manager.port_name,
        "config": alarm_manager.config
    }


@router.post("/config")
async def save_config(req: ConfigRequest):
    """保存报警器配置"""
    alarm_manager.config['enabled'] = req.enabled
    alarm_manager.config['protocol'] = req.protocol
    alarm_manager.config['test_mode'] = req.test_mode
    
    if req.custom_commands:
        alarm_manager.config['custom_commands'] = req.custom_commands
    if req.triggers:
        alarm_manager.config['triggers'] = req.triggers
    
    alarm_manager._save_config()
    return {"success": True, "message": "配置已保存"}


@router.post("/test")
async def test_alarm(req: TestRequest):
    """测试报警器"""
    if not alarm_manager.is_connected():
        raise HTTPException(status_code=400, detail="报警器未连接")
    
    # 检查测试模式是否开启
    if not alarm_manager.config.get('test_mode'):
        raise HTTPException(status_code=400, detail="请先开启测试模式")
    
    # 直接通过协议获取命令并发送
    cmd = alarm_manager._get_command(req.action)
    if cmd:
        success = alarm_manager._send_command(cmd)
        return {"success": success, "action": req.action}
    else:
        raise HTTPException(status_code=400, detail=f"无效的动作: {req.action}")


@router.post("/trigger/{event_type}")
async def trigger_alarm(event_type: str):
    """手动触发报警"""
    # 检查事件类型是否在配置中
    triggers = alarm_manager.config.get('triggers', {})
    if event_type not in triggers:
        raise HTTPException(status_code=400, detail=f"未配置的事件类型: {event_type}")
    
    alarm_manager.trigger_alarm(event_type)
    return {"success": True, "message": f"已触发 {event_type} 报警"}


@router.post("/stop")
async def stop_alarm():
    """停止报警"""
    alarm_manager.stop_alarm()
    return {"success": True, "message": "已停止报警"}


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
