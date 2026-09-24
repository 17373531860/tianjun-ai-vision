"""Fleet Hub 配置（RFC 15 §11.1）。

设计约束:
  - 枢纽是独立服务, **禁止 import 主程序 backend.***（会拖起 cv2/torch 依赖链,
    且枢纽部署机没有这些库）。与边缘的一切交互走 HTTP (edge_client)。
  - 数据目录 HUB_DATA_DIR 环境变量可指, 默认 hub/data/（DB + Fernet 密钥落这里）。
"""
import os
from pathlib import Path

HUB_API_PREFIX = "/api/v1"

# 枢纽支持的边缘 API 契约号上限 (边缘 handshake.identity.api_contract 高于此值 →
# 拒绝纳管并提示升级枢纽; 禁止 if-version 散落业务代码, 门槛只在纳管处判一次)
SUPPORTED_EDGE_CONTRACT = 1

# 轮询节奏默认值 (RFC 15 §10.2, 全部可配)
HEALTH_INTERVAL_S = float(os.environ.get("HUB_HEALTH_INTERVAL", "2"))
PROFILE_INTERVAL_S = float(os.environ.get("HUB_PROFILE_INTERVAL", "60"))
OFFLINE_AFTER_FAILURES = 3          # 连续失败 N 次判离线
MAX_BACKOFF_S = 30.0                # 离线后指数退避上限
EDGE_TIMEOUT_S = float(os.environ.get("HUB_EDGE_TIMEOUT", "5"))

# 操作锁 TTL (RFC 15 §10.1): 持锁人每次写操作自动续期; 过期即视同无锁
LOCK_TTL_S = float(os.environ.get("HUB_LOCK_TTL", "120"))

# 事件通道 (P0-10)
EVENT_PULL_INTERVAL_S = float(os.environ.get("HUB_EVENT_INTERVAL", "4"))  # 事件拉取节奏 (心跳的低频档)
EVENT_KEEP_MAX = int(os.environ.get("HUB_EVENT_KEEP_MAX", "10000"))       # hub_events 保留上限

# 数据中心统计通道 (M5): 全量周期拉取 + 小时桶聚合 + 保留策略
CYCLE_PULL_INTERVAL_S = float(os.environ.get("HUB_CYCLE_INTERVAL", "5"))   # 周期拉取节奏
CYCLE_PULL_PAGE = int(os.environ.get("HUB_CYCLE_PAGE", "500"))             # 单次拉取页大小
CYCLE_KEEP_DAYS = int(os.environ.get("HUB_CYCLE_KEEP_DAYS", "90"))         # 明细保留天数
ROLLUP_INTERVAL_S = float(os.environ.get("HUB_ROLLUP_INTERVAL", "20"))     # 脏桶重算节奏
ROLLUP_KEEP_DAYS = int(os.environ.get("HUB_ROLLUP_KEEP_DAYS", "730"))      # 小时桶保留天数
RETENTION_INTERVAL_S = float(os.environ.get("HUB_RETENTION_INTERVAL", "3600"))  # 过期清理节奏
NOTIFY_CHECK_INTERVAL_S = float(os.environ.get("HUB_NOTIFY_INTERVAL", "15"))    # M7 通知巡检节奏


def get_data_dir() -> Path:
    """数据目录懒解析 (测试用 HUB_DATA_DIR 指临时目录, 必须调用期读 env)"""
    raw = os.environ.get("HUB_DATA_DIR")
    d = Path(raw) if raw else Path(__file__).resolve().parents[1] / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d
