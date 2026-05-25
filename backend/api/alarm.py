"""
报警器管理 API
支持 USB 串口报警器的检测、配置和控制
支持多通道（每个工位独立串口设备）
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
import serial
import serial.tools.list_ports
import threading
import time
import json
import os
from backend.core.auth_deps import require_perm
from backend.core.config import DATA_DIR

router = APIRouter()

ALARM_CONFIG_FILE = os.path.join(DATA_DIR, 'alarm_config.json')

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
    """单个报警器管理器（对应一个串口设备）

    v2.7.3 新增「共享模式」：一个物理报警灯由多个工位共用。
    通过 `set_shared_mode()` 启用后，trigger_alarm / start_idle_light /
    stop_idle_light 都按 channel_id 维护内部状态，按优先级合成最终显示。
    """

    DEFAULT_PRIORITY_ORDER = ['ng', 'warn', 'ok', 'idle']
    DEFAULT_EVENT_PRIORITY_MAP = {
        'event1': 'ok',    # 默认 event1 = 合格 → ok 优先级
        'event2': 'ng',    # 默认 event2 = NG → ng 优先级
        'event3': 'warn',
        'event4': 'warn',
    }

    def __init__(self, config: Optional[dict] = None):
        self.serial_port: Optional[serial.Serial] = None
        self.port_name: Optional[str] = None
        self.config = config if config else dict(DEFAULT_CHANNEL_CONFIG)
        self._alarm_thread: Optional[threading.Thread] = None
        self._alarm_stop_event = threading.Event()
        self._idle_light_active = False

        # —— v2.7.3 共享模式状态 —— #
        # 服务的 channel id 集合（含 owner 自身）。len > 1 即共享模式
        self._shared_channels: set = set()
        # 每通道当前状态: {ch_id: {'event': str|None, 'category': str,
        #                          'expire_at': float, 'is_idle': bool}}
        self._channel_states: dict = {}
        # 优先级排序与事件→优先级类别映射
        self._priority_order: list = list(self.DEFAULT_PRIORITY_ORDER)
        self._event_priority_map: dict = dict(self.DEFAULT_EVENT_PRIORITY_MAP)
        # 当前已应用的视觉状态（避免重复发指令）
        self._current_visual = None
        # 状态/串口 重入锁
        self._state_lock = threading.RLock()

    # ===================== 共享模式入口 ===================== #

    def set_shared_mode(self, channels, priority_order=None, event_priority_map=None):
        """开启/更新共享模式。channels 包含所有共用此设备的工位 id（含 owner）。"""
        with self._state_lock:
            self._shared_channels = set(int(c) for c in channels)
            for ch in self._shared_channels:
                self._channel_states.setdefault(ch, {
                    'event': None, 'category': 'idle',
                    'expire_at': 0.0, 'is_idle': False,
                })
            if priority_order:
                self._priority_order = list(priority_order)
            if event_priority_map:
                self._event_priority_map = dict(event_priority_map)
            if not os.environ.get("BACKEND_SKIP_INIT"):
                print(f"[报警] 共享模式启用: 服务工位 {sorted(self._shared_channels)}, "
                      f"优先级={self._priority_order}", flush=True)

    def is_shared(self) -> bool:
        # 用同一把 RLock 保证 set 大小读取与共享模式启停不撕裂
        with self._state_lock:
            return len(self._shared_channels) > 1

    def get_shared_channels_snapshot(self):
        """对外暴露 _shared_channels 的快照，避免外部 sorted/iter 时被改"""
        with self._state_lock:
            return list(self._shared_channels)

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
                except Exception as _e:
                    print(f"[Alarm] 命令编码失败，按原文回退: {_e}", flush=True)
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

    def trigger_alarm(self, event_type: str = 'event2', channel_id: int = 0):
        """触发瞬时报警事件。

        - 非共享模式：与旧版完全一致，立即驱动灯，duration 后回到 idle。
        - 共享模式：按 channel 写入事件，按优先级合成；duration 到期重算。
        """
        if not self.config.get('enabled'):
            return
        trigger_config = self.config.get('triggers', {}).get(event_type, {})
        if not trigger_config.get('enabled'):
            return

        if self.is_shared():
            self._trigger_alarm_shared(event_type, trigger_config, channel_id)
        else:
            self._trigger_alarm_solo(event_type, trigger_config)

    def _trigger_alarm_solo(self, event_type: str, trigger_config: dict):
        """非共享模式：原 v2.7.2 行为，未做任何修改"""
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
                self._send_command(self._get_command('all_off'))
                if self._idle_light_active:
                    time.sleep(0.05)
                    self.restore_idle_light()

            # 用 threading.Timer 替代 Thread+sleep：内部走 Event.wait，可被 cancel，
            # 关停时也更可控（避免悬挂的 sleep 线程）
            t = threading.Timer(duration, delayed_off)
            t.daemon = True
            t.start()

        except Exception as e:
            print(f"报警执行失败: {e}")

    def _trigger_alarm_shared(self, event_type: str, trigger_config: dict, channel_id: int):
        """共享模式：写入通道事件，按优先级合成。"""
        duration = trigger_config.get('duration', 3)
        category = self._event_priority_map.get(event_type, 'ok')
        with self._state_lock:
            state = self._channel_states.setdefault(channel_id, {
                'event': None, 'category': 'idle', 'expire_at': 0.0, 'is_idle': False,
            })
            state['event'] = event_type
            state['category'] = category
            state['expire_at'] = time.time() + duration
            print(f"[报警·共享] ch{channel_id} 触发 {event_type}({category}), "
                  f"持续 {duration}s", flush=True)
            self._recompose_and_apply()

        def expire():
            with self._state_lock:
                cur = self._channel_states.get(channel_id)
                # 仅当还是这个事件时才清（避免覆盖了别的新事件）
                if cur and cur.get('event') == event_type:
                    cur['event'] = None
                    cur['category'] = 'idle'
                    cur['expire_at'] = 0.0
                    self._recompose_and_apply()

        t = threading.Timer(duration, expire)
        t.daemon = True
        t.start()

    def stop_alarm(self):
        self._alarm_stop_event.set()
        if self._alarm_thread and self._alarm_thread.is_alive():
            self._alarm_thread.join(timeout=1)
        if self.is_shared():
            # 共享模式：清掉所有通道的瞬时事件，重算
            with self._state_lock:
                for st in self._channel_states.values():
                    st['event'] = None
                    st['category'] = 'idle'
                    st['expire_at'] = 0.0
                self._recompose_and_apply()
            return
        if self._idle_light_active:
            self.restore_idle_light()
        else:
            self.all_off()

    def start_idle_light(self, channel_id: int = 0):
        # v2.7.3: 静默 return 都改为打印原因，方便现场排查为什么"开始检测但灯不亮"
        idle_cfg = self.config.get('idle_light', {})
        if not idle_cfg.get('enabled'):
            print("[报警] 工作指示灯未启用（idle_light.enabled=False）→ 不亮。"
                  "请到「报警配置」页底部勾选「启用空闲常亮」并保存。")
            return
        if not self.config.get('enabled'):
            print("[报警] 报警器未启用（enabled=False）→ 工作指示灯不亮。"
                  "请到「报警配置」页勾选「启用报警」并连接串口。")
            return
        if not self.is_connected():
            print(f"[报警] 串口未连接（port={self.config.get('port', '')}）→ 工作指示灯不亮。"
                  f"请到「报警配置」页点击「连接」按钮。")
            return
        color = idle_cfg.get('color', 'blue')
        cmd = self._get_command(f'{color}_on')
        if not cmd:
            print(f"[报警] 当前协议 {self.config.get('protocol')} 不支持颜色 '{color}' → "
                  f"灯不亮。请在「报警配置」改成支持该颜色的协议（如 modbus_4color），"
                  f"或换一个颜色。")
            return

        if self.is_shared():
            with self._state_lock:
                state = self._channel_states.setdefault(channel_id, {
                    'event': None, 'category': 'idle', 'expire_at': 0.0, 'is_idle': False,
                })
                state['is_idle'] = True
                self._idle_light_active = True
                print(f"[报警·共享] ch{channel_id} idle=True; 重算合成", flush=True)
                self._recompose_and_apply()
            return

        # 非共享模式：原行为
        if not self._send_command(self._get_command('all_off')):
            print("[报警] 发送 all_off 失败（串口写入异常）→ 工作指示灯不亮")
            return
        time.sleep(0.05)
        if self._send_command(cmd):
            self._idle_light_active = True
            print(f"[报警] 工作指示灯已亮: {color}")
        else:
            print(f"[报警] 发送 {color}_on 失败（串口写入异常）→ 工作指示灯不亮")

    def stop_idle_light(self, channel_id: int = 0):
        if self.is_shared():
            with self._state_lock:
                state = self._channel_states.setdefault(channel_id, {
                    'event': None, 'category': 'idle', 'expire_at': 0.0, 'is_idle': False,
                })
                state['is_idle'] = False
                any_idle = any(s.get('is_idle') for s in self._channel_states.values())
                if not any_idle:
                    self._idle_light_active = False
                print(f"[报警·共享] ch{channel_id} idle=False; "
                      f"还有 idle? {any_idle}; 重算合成", flush=True)
                self._recompose_and_apply()
            return
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

    # ===================== v2.7.3 共享模式合成 ===================== #

    def _recompose_and_apply(self):
        """根据所有通道当前状态合成最终视觉。须在持锁状态下调用。"""
        now = time.time()
        active_events = []
        any_idle = False
        for ch in self._shared_channels:
            st = self._channel_states.get(ch)
            if not st:
                continue
            if st.get('event') and st.get('expire_at', 0) > now:
                active_events.append((ch, st))
            if st.get('is_idle'):
                any_idle = True

        def prio_idx(category: str) -> int:
            try:
                return self._priority_order.index(category)
            except ValueError:
                return 999

        if active_events:
            active_events.sort(key=lambda x: prio_idx(x[1].get('category', 'idle')))
            owner_ch, owner_state = active_events[0]
            event_type = owner_state.get('event')
            target_key = ('event', owner_ch, event_type)
        elif any_idle and self._idle_light_active:
            target_key = ('idle', None, None)
        else:
            target_key = ('off', None, None)

        if target_key == self._current_visual:
            return  # 视觉无变化，避免重复发指令

        self._current_visual = target_key

        if target_key[0] == 'event':
            _tag, ch, event_type = target_key
            self._apply_event_visual(event_type)
            print(f"[报警·共享] 显示 ch{ch} 的 {event_type}", flush=True)
        elif target_key[0] == 'idle':
            self._apply_idle_visual()
            print("[报警·共享] 回退到 idle 灯", flush=True)
        else:
            self._send_command(self._get_command('all_off'))
            print("[报警·共享] 全灯熄灭", flush=True)

    def _apply_event_visual(self, event_type: str):
        """从 trigger_solo 抽出的"发命令"部分，用于共享模式立即应用。"""
        trigger_config = self.config.get('triggers', {}).get(event_type, {})
        color = trigger_config.get('color', 'red')
        effect = trigger_config.get('effect', 'on')
        use_buzzer = trigger_config.get('buzzer', False)
        protocol = self.config.get('protocol', 'modbus_4color')
        try:
            self._send_command(self._get_command('all_off'))
            time.sleep(0.05)
            if protocol == 'modbus_4color':
                if color and color != 'none':
                    effect_suffix = {'on': 'on', 'slow': 'slow', 'fast': 'fast'}.get(effect, 'on')
                    if use_buzzer and color in ['red', 'green']:
                        cmd = f'{color}_buzzer_on' if effect_suffix == 'on' else f'{color}_buzzer_{effect_suffix}'
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
        except Exception as e:
            print(f"[报警·共享] 应用事件视觉失败: {e}")

    def _apply_idle_visual(self):
        idle_cfg = self.config.get('idle_light', {})
        color = idle_cfg.get('color', 'blue')
        cmd = self._get_command(f'{color}_on')
        if not cmd:
            return
        try:
            self._send_command(self._get_command('all_off'))
            time.sleep(0.05)
            self._send_command(cmd)
        except Exception as e:
            print(f"[报警·共享] 应用 idle 视觉失败: {e}")


class AlarmRouter:
    """按通道路由的报警管理器集合

    v2.7.3 支持「共享模式」：在 owner channel 配置里写 `shared_with: [其他工位号]`，
    则 owner 的 AlarmManager 实例会被那些工位共用（多个 channel_id 指向同一 manager）。
    """

    def __init__(self):
        self.managers: dict = {}            # channel_id → AlarmManager（共享时多个 ch 指向同一实例）
        self._owner_for: dict = {}           # channel_id → owner channel_id（用于序列化去重）
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
                self._owner_for[ch_id] = ch_id
        else:
            merged = dict(DEFAULT_CHANNEL_CONFIG)
            merged.update(raw)
            self.managers[0] = AlarmManager(config=merged)
            self._owner_for[0] = 0

        # —— v2.7.3 处理 shared_with —— #
        # 一个 owner 配置里 shared_with: [1,2] 表示自己（owner）+ 工位 1,2 共用此设备
        for owner_ch, owner_mgr in list(self.managers.items()):
            shared_with = owner_mgr.config.get('shared_with') or []
            if not shared_with:
                continue
            all_chs = sorted(set([owner_ch] + [int(c) for c in shared_with]))
            owner_mgr.set_shared_mode(
                channels=all_chs,
                priority_order=owner_mgr.config.get('priority_order'),
                event_priority_map=owner_mgr.config.get('event_priority_map'),
            )
            for ch in all_chs:
                if ch == owner_ch:
                    continue
                # 如果该 ch 之前有自己的独立 manager 且占用了串口，先释放
                old = self.managers.get(ch)
                if old is not None and old is not owner_mgr:
                    try:
                        old.disconnect()
                    except Exception:
                        pass
                self.managers[ch] = owner_mgr
                self._owner_for[ch] = owner_ch
            if not os.environ.get("BACKEND_SKIP_INIT"):
                print(f"[报警] 共享组: owner=ch{owner_ch}, 服务={all_chs}", flush=True)

        # 自动连接（每个物理 manager 只连一次）
        # 测试环境（BACKEND_SKIP_INIT=1）跳过串口连接，避免日志刷屏 + 串口被占
        if os.environ.get("BACKEND_SKIP_INIT"):
            return
        connected_managers = set()
        for ch_id, mgr in self.managers.items():
            if id(mgr) in connected_managers:
                continue
            connected_managers.add(id(mgr))
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
        # v2.7.3: 共享 manager 只按 owner 序列化一次，避免重复
        channels_data = {}
        seen = set()
        for ch_id, mgr in self.managers.items():
            owner = self._owner_for.get(ch_id, ch_id)
            if id(mgr) in seen:
                continue
            seen.add(id(mgr))
            channels_data[str(owner)] = mgr.config
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
            self._owner_for[channel_id] = channel_id
        return self.managers[channel_id]

    def trigger_alarm(self, event_type: str, channel_id: int = 0):
        self.get(channel_id).trigger_alarm(event_type, channel_id=channel_id)

    def start_idle_light(self, channel_id: int = 0):
        self.get(channel_id).start_idle_light(channel_id=channel_id)

    def stop_idle_light(self, channel_id: int = 0):
        self.get(channel_id).stop_idle_light(channel_id=channel_id)

    def stop_alarm(self, channel_id: int = 0):
        self.get(channel_id).stop_alarm()

    def disconnect_all(self):
        # v2.7.3: 共享 manager 去重，避免重复 disconnect
        seen = set()
        for mgr in self.managers.values():
            if id(mgr) in seen:
                continue
            seen.add(id(mgr))
            mgr.disconnect()

    def reload_config_from_disk(self):
        """v2.7.3: 运行时重新加载配置（用于共享配置生效）。
        会先停止所有报警/熄灯/断串口，再重建 manager 集合。
        """
        seen = set()
        for mgr in list(self.managers.values()):
            if id(mgr) in seen:
                continue
            seen.add(id(mgr))
            try:
                mgr.stop_alarm()
            except Exception:
                pass
            try:
                mgr._idle_light_active = False
                mgr.all_off()
            except Exception:
                pass
            try:
                mgr.disconnect()
            except Exception:
                pass
        self.managers.clear()
        self._owner_for.clear()
        self._load_all()
        print("[报警] 配置已重新加载", flush=True)

    def on_channel_removed(self, channel_id: int):
        """工位被移除（降工位）时调用：停止报警、熄灭灯塔、释放串口、移除 manager。
        避免降工位后 ch1 的蜂鸣器/灯塔线程仍在运行，或串口被占用导致升回后无法重连。
        v2.7.2

        v2.7.3: 共享模式下，被移除的 ch 只是从 manager 的服务集合里摘除，
                串口/manager 保留给其他工位继续用；只有非共享或最后一个 ch 才真断。
        """
        mgr = self.managers.get(channel_id)
        if mgr is None:
            return

        # 共享模式：只把这个 ch 从服务集合摘掉，保留物理设备
        # 注意：判断和操作必须放在同一把锁里，否则中间可能被 set_shared_mode 改写
        with mgr._state_lock:
            in_shared = (len(mgr._shared_channels) > 1) and (channel_id in mgr._shared_channels)
            if in_shared:
                mgr._shared_channels.discard(channel_id)
                mgr._channel_states.pop(channel_id, None)
                try:
                    mgr._recompose_and_apply()
                except Exception as e:
                    print(f"[报警] ch{channel_id} 共享摘除后合成失败: {e}")
                remaining = sorted(mgr._shared_channels)
        if in_shared:
            self.managers.pop(channel_id, None)
            self._owner_for.pop(channel_id, None)
            print(f"[报警] ch{channel_id} 从共享组摘除，物理设备保留服务剩余工位 "
                  f"{remaining}", flush=True)
            return

        # 非共享：原行为
        self.managers.pop(channel_id, None)
        self._owner_for.pop(channel_id, None)
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
    # v2.7.3: 共享报警灯字段（仅 owner 通道写）
    shared_with: Optional[List[int]] = None      # 共享给哪些工位号（不含自己）
    priority_order: Optional[List[str]] = None   # ['ng','warn','ok','idle']
    event_priority_map: Optional[dict] = None    # {'event1':'ok','event2':'ng',...}

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


@router.post("/connect",
              dependencies=[Depends(require_perm("alarm.edit"))])
async def connect(req: ConnectRequest, channel: int = Query(0, description="工位通道")):
    """连接到报警器"""
    mgr = alarm_router.get(channel)
    result = mgr.connect(req.port, req.baudrate)
    if result["ok"]:
        alarm_router._save_all()
        return {"success": True, "message": f"工位 {channel} 已连接到 {req.port}"}
    else:
        raise HTTPException(status_code=500, detail=result["msg"])


@router.post("/disconnect",
              dependencies=[Depends(require_perm("alarm.edit"))])
async def disconnect(channel: int = Query(0, description="工位通道")):
    """断开报警器连接"""
    alarm_router.get(channel).disconnect()
    return {"success": True, "message": f"工位 {channel} 已断开连接"}


@router.get("/status")
async def get_status(channel: int = Query(-1, description="工位通道，-1 返回全部")):
    """获取报警器状态"""
    if channel >= 0:
        mgr = alarm_router.get(channel)
        owner_ch = alarm_router._owner_for.get(channel, channel)
        return {
            "success": True,
            "channel": channel,
            "is_connected": mgr.is_connected(),
            "port": mgr.port_name,
            "config": mgr.config,
            # v2.7.3: 共享状态信息
            "is_shared": mgr.is_shared(),
            "shared_channels": sorted(mgr.get_shared_channels_snapshot()) if mgr.is_shared() else [],
            "owner_channel": owner_ch,
            "is_owner": (owner_ch == channel),
        }
    else:
        all_status = {}
        for ch_id, mgr in alarm_router.managers.items():
            owner_ch = alarm_router._owner_for.get(ch_id, ch_id)
            all_status[ch_id] = {
                "is_connected": mgr.is_connected(),
                "port": mgr.port_name,
                "config": mgr.config,
                "is_shared": mgr.is_shared(),
                "shared_channels": sorted(mgr.get_shared_channels_snapshot()) if mgr.is_shared() else [],
                "owner_channel": owner_ch,
                "is_owner": (owner_ch == ch_id),
            }
        return {"success": True, "channels": all_status}


@router.post("/config",
              dependencies=[Depends(require_perm("alarm.edit"))])
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

    # v2.7.3: 共享配置写入
    sharing_changed = False
    if req.shared_with is not None:
        mgr.config['shared_with'] = list(req.shared_with)
        sharing_changed = True
    if req.priority_order is not None:
        mgr.config['priority_order'] = list(req.priority_order)
        sharing_changed = True
    if req.event_priority_map is not None:
        mgr.config['event_priority_map'] = dict(req.event_priority_map)
        sharing_changed = True

    alarm_router._save_all()

    # 共享配置变更后立刻生效（无需重启）
    reloaded = False
    if sharing_changed:
        try:
            alarm_router.reload_config_from_disk()
            reloaded = True
        except Exception as e:
            print(f"[报警] 共享配置生效失败: {e}")

    return {
        "success": True,
        "message": f"工位 {channel} 配置已保存" + ("（共享配置已重新加载生效）" if reloaded else ""),
        "reloaded": reloaded,
    }


@router.post("/reload",
              dependencies=[Depends(require_perm("alarm.edit"))])
async def reload_alarm_config():
    """v2.7.3: 重新加载报警配置（支持运行时切换共享/独立模式）"""
    try:
        alarm_router.reload_config_from_disk()
        return {"success": True, "message": "报警配置已重新加载"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test",
              dependencies=[Depends(require_perm("alarm.edit"))])
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


@router.post("/trigger/{event_type}",
              dependencies=[Depends(require_perm("alarm.edit"))])
async def trigger_alarm(event_type: str, channel: int = Query(0, description="工位通道")):
    """手动触发报警"""
    mgr = alarm_router.get(channel)
    triggers = mgr.config.get('triggers', {})
    if event_type not in triggers:
        raise HTTPException(status_code=400, detail=f"未配置的事件类型: {event_type}")

    mgr.trigger_alarm(event_type)
    return {"success": True, "message": f"工位 {channel} 已触发 {event_type} 报警"}


@router.post("/stop",
              dependencies=[Depends(require_perm("alarm.edit"))])
async def stop_alarm(channel: int = Query(0, description="工位通道")):
    """停止报警"""
    alarm_router.stop_alarm(channel)
    return {"success": True, "message": f"工位 {channel} 已停止报警"}


@router.post("/idle-light/start",
              dependencies=[Depends(require_perm("alarm.edit"))])
async def start_idle_light(channel: int = Query(0, description="工位通道")):
    """手动开启工作指示灯"""
    alarm_router.start_idle_light(channel)
    return {"success": True, "message": f"工位 {channel} 工作指示灯已开启"}


@router.post("/idle-light/stop",
              dependencies=[Depends(require_perm("alarm.edit"))])
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
