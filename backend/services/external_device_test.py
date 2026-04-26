"""外设连通性测试 mixin"""
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

class ExternalDeviceTestMixin:
    def _test_tcp(self, ip, port, timeout):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((ip, port))
            sock.close()
            return {"success": True, "message": f"TCP {ip}:{port} 连接成功"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def _test_modbus(self, ip, port, config, timeout):
        try:
            from pymodbus.client import ModbusTcpClient
            client = ModbusTcpClient(ip, port=port or 502, timeout=timeout)
            if client.connect():
                reg = config.get("register", 0)
                result = client.read_holding_registers(reg, 1, slave=config.get("unit_id", 1))
                client.close()
                if result.isError():
                    return {"success": False, "message": f"Modbus 连接成功但读寄存器失败: {result}"}
                return {"success": True, "message": f"Modbus {ip}:{port} 连接成功, 寄存器值={result.registers}"}
            return {"success": False, "message": "Modbus 连接失败"}
        except ImportError:
            return {"success": False, "message": "pymodbus 未安装"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def _test_serial(self, serial_port, baud, timeout):
        try:
            import serial
            ser = serial.Serial(port=serial_port, baudrate=baud, timeout=timeout)
            ser.close()
            return {"success": True, "message": f"串口 {serial_port} 打开成功 (baud={baud})"}
        except ImportError:
            return {"success": False, "message": "pyserial 未安装"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def _test_serial_modbus(self, serial_port, baud, config, protocol, timeout):
        """测试串口 Modbus ASCII 连接并尝试读取一次"""
        try:
            import serial
        except ImportError:
            return {"success": False, "message": "pyserial 未安装"}
        try:
            ser = serial.Serial(port=serial_port, baudrate=baud, timeout=timeout)
        except Exception as e:
            return {"success": False, "message": f"串口打开失败: {e}"}

        if protocol == "serial_continuous":
            time.sleep(2.0)
            data = ser.read(1024)
            ser.close()
            if data:
                text = data.decode("ascii", errors="ignore").strip()
                return {"success": True,
                        "message": f"连续接收模式收到数据: {text[:100]}"}
            return {"success": True,
                    "message": f"串口 {serial_port} 打开成功，但 2 秒内未收到数据（设备是否配置为连续发送？）"}

        slave_id = config.get("slave_id", 1)
        doc_reg = config.get("register", 41201)
        modbus_reg = doc_reg - 40001 if doc_reg >= 40001 else doc_reg
        count = config.get("count", 2)

        request = self._build_modbus_ascii_read(slave_id, modbus_reg, count)
        try:
            ser.reset_input_buffer()
            ser.write(request)
            time.sleep(0.3)
            response = ser.read(256)
            ser.close()
            if response:
                text = response.decode("ascii", errors="ignore").strip()
                registers = self._parse_modbus_ascii_response(text, slave_id)
                if registers is not None:
                    return {"success": True,
                            "message": f"Modbus ASCII 测试成功，寄存器值: {registers}"}
                return {"success": True,
                        "message": f"串口有响应但解析失败: {text[:60]}"}
            return {"success": True,
                    "message": f"串口 {serial_port} 打开成功，但 Modbus 无响应（检查从站地址和接线）"}
        except Exception as e:
            ser.close()
            return {"success": False, "message": f"通信失败: {e}"}

    def _test_http(self, config, timeout):
        try:
            import requests as req
            url = config.get("url", "")
            resp = req.get(url, timeout=timeout)
            return {"success": True, "message": f"HTTP {resp.status_code}, body length={len(resp.text)}"}
        except Exception as e:
            return {"success": False, "message": str(e)}
