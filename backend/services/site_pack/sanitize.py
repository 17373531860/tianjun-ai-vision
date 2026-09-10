# ==================== 分域序列化与机器绑定剥离 ====================
# 配方包只带"逻辑配置", 机器特定的东西一律不带或清空:
#   不带: License / 用户与 token / 检测历史 / 录像与归档 / archive_secret.key /
#         TensorRT .engine / 相机 index 与画质参数 / 集群 IP / 多屏 display_id
#   清空并默认停用: 扫码器 IP、MES 网关地址、PLC 连接参数、触发器串口、报警 COM 口
#
# 序列化统一规则: 去掉 id / 全部时间戳列, 其余按列名原样带走;
# 导入侧按稳定身份 (name / code / key / 项目名) upsert, 不动目标机历史数据。

from sqlalchemy import DateTime
from sqlalchemy import inspect as sa_inspect

# MES 连接 config JSON 里凡是命中这些键名的, 值一律清空 (适配器各有各的键)
NETWORK_KEYS = {
    "url", "base_url", "host", "ip", "port", "endpoint", "address",
    "server", "dsn", "database_url",
}
SERIAL_KEYS = {"port", "serial_port", "com", "com_port", "device_path"}


def row_to_dict(row) -> dict:
    """ORM 行 → 可移植 dict: 去 id 与全部 DateTime 列, JSON 列原样。"""
    out = {}
    for col in sa_inspect(row.__class__).columns:
        if col.primary_key or isinstance(col.type, DateTime):
            continue
        out[col.name] = getattr(row, col.name)
    return out


def apply_row_dict(row, data: dict) -> None:
    """把 dict 写回 ORM 行, 只写表里真实存在的列 (跨版本多余键静默忽略)。"""
    valid = {c.name for c in sa_inspect(row.__class__).columns}
    for key, value in data.items():
        if key in valid and not key.endswith("_id_ref"):
            setattr(row, key, value)


# ---------- 各分域剥离 ----------

def sanitize_scanner(data: dict) -> dict:
    data["ip"] = ""
    data["enabled"] = False
    return data


def sanitize_mes_connection(data: dict) -> dict:
    data["enabled"] = False
    data["pull_enabled"] = False
    config = data.get("config")
    if isinstance(config, dict):
        data["config"] = {
            k: ("" if k.lower() in NETWORK_KEYS else v)
            for k, v in config.items()
        }
    return data


def sanitize_plc(data: dict) -> dict:
    data["enabled"] = False
    params = data.get("conn_params")
    if isinstance(params, dict):
        data["conn_params"] = {
            k: ("" if k.lower() in (NETWORK_KEYS | SERIAL_KEYS) else v)
            for k, v in params.items()
        }
    return data


def sanitize_trigger(data: dict) -> dict:
    # 只有串口触发源带机器绑定; pixel_region/hid_key/定时的 params 是纯逻辑
    if data.get("type") == "serial":
        data["enabled"] = False
        params = data.get("params")
        if isinstance(params, dict):
            data["params"] = {
                k: ("" if k.lower() in SERIAL_KEYS else v)
                for k, v in params.items()
            }
    return data


def sanitize_alarm_config(data: dict) -> dict:
    """alarm_config.json: 清 COM 口并停用 (兼容单通道扁平/多通道 channels 两种形态)。"""
    def _strip(cfg):
        if not isinstance(cfg, dict):
            return
        if "port" in cfg:
            cfg["port"] = ""
            if cfg.get("enabled"):
                cfg["enabled"] = False
        for sub in cfg.values():
            if isinstance(sub, dict):
                _strip(sub)

    _strip(data)
    return data


def sanitize_sms_config(data: dict) -> dict:
    """sms_config.json: 模板/规则带走; AT 猫是插在本机的串口设备, 清口并停用。
    云通道 (HTTP/阿里/腾讯/WxPusher) 的凭据跨机可用, 原样带走 (包本身加密)。"""
    modem = data.get("at_modem")
    if isinstance(modem, dict):
        modem["port"] = ""
    if data.get("provider") == "at_modem" and data.get("enabled"):
        data["enabled"] = False
    return data


# 导入后待办 (按包里实际出现的分域取用, 拼进导入报告)
DOMAIN_TODOS = {
    "workstation": "到「工位与输入源」为每个工位重新选择相机/视频源",
    "models": "如项目使用 TensorRT 推理格式, 到「模型仓库」在本机重新转换引擎",
    "scanners": "扫码器 IP 已清空且已停用: 到「MES 管理 → 扫码器」填入本现场 IP 后启用",
    "mes_connections": "MES 网关地址已清空且已停用: 到「MES 管理 → 网关」填入地址并测试连接后启用",
    "plc": "PLC 连接参数已清空且已停用: 到「PLC 连接」填入 IP/机架槽位后启用",
    "triggers": "串口类触发源已清空串口号且已停用: 到「触发中心」重选串口后启用",
    "alarm": "报警器 COM 口已清空且已停用: 到「报警设置」重选串口后启用",
    "sms": "如使用 4G 短信猫: 到「短信通知」重选本机串口后启用",
}
