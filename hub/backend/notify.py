"""通知出口 (M7 运维告警链路) — 节点离线超阈值 / 恢复通知外推。

调研裁决 (ThingsBoard Notification / 国产 DMP / 力控 SCADA 惯例):
- 通道三种全是 HTTP POST JSON, 实现薄: 通用 webhook / 钉钉群机器人
  (支持加签) / 企业微信群机器人。
- 规则一条就够 (4~10 台规模): 离线持续 ≥ N 分钟 → 告警; 恢复 → 恢复通知
  (仅当离线侧真的发过, 不然恢复消息是噪音)。
- 默认全关零骚扰; 发送失败只记 last_result 不重试排队 (值班窗口过了,
  迟到的离线短信比没有更糟 —— 墙上的置顶条才是权威)。
"""
import base64
import hashlib
import hmac
import time
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hub.backend.auth import require_perm
from hub.backend.db import get_db
from hub.backend.models import HubSetting

router = APIRouter()

SETTING_KEY = "notify"

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "offline_threshold_min": 5,   # 离线持续 ≥ N 分钟才外推 (0=立即)
    "notify_recover": True,       # 恢复时补一条 (仅当离线侧已通知)
    "channels": [],               # [{type: webhook|dingtalk|wecom, url, secret}]
    # M9 报警升级 (RFC §4.6): NG 未确认超 N 分钟 → 升级外推 (0=关);
    # 冷却窗内最多一条汇总, 防"每条 NG 一响"的通知疲劳
    "alarm_escalate_min": 0,
    "alarm_escalate_cooldown_min": 30,
}

_SEND_TIMEOUT = 5.0


# ============================================================
# 配置存取
# ============================================================

def get_config(db: Session) -> Dict[str, Any]:
    row = db.query(HubSetting).filter(HubSetting.key == SETTING_KEY).first()
    cfg = dict(DEFAULT_CONFIG)
    if row and isinstance(row.value, dict):
        cfg.update(row.value)
    return cfg


def save_config(db: Session, cfg: Dict[str, Any]) -> None:
    row = db.query(HubSetting).filter(HubSetting.key == SETTING_KEY).first()
    if row is None:
        row = HubSetting(key=SETTING_KEY)
        db.add(row)
    row.value = cfg
    row.updated_at = datetime.now()


# ============================================================
# 通道投递 (全部 HTTP POST JSON)
# ============================================================

def _dingtalk_url(url: str, secret: Optional[str]) -> str:
    """钉钉加签: timestamp + HMAC-SHA256(secret) 拼进 URL (官方算法)。"""
    if not secret:
        return url
    ts = str(round(time.time() * 1000))
    raw = f"{ts}\n{secret}".encode("utf-8")
    sign = urllib.parse.quote_plus(base64.b64encode(
        hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()))
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}timestamp={ts}&sign={sign}"


async def _send_one(channel: Dict[str, Any], title: str, text: str,
                    payload: Dict[str, Any]) -> Optional[str]:
    """向单通道投递。返回 None=成功, 否则错误描述 (调用方汇总)。"""
    ctype = channel.get("type")
    url = (channel.get("url") or "").strip()
    if not url:
        return "url 为空"
    try:
        async with httpx.AsyncClient(timeout=_SEND_TIMEOUT) as client:
            if ctype == "dingtalk":
                r = await client.post(
                    _dingtalk_url(url, channel.get("secret")),
                    json={"msgtype": "text",
                          "text": {"content": f"{title}\n{text}"}})
                body = r.json() if r.content else {}
                if r.status_code != 200 or body.get("errcode") not in (0, None):
                    return f"钉钉返回 {r.status_code}: {body}"
            elif ctype == "wecom":
                r = await client.post(
                    url, json={"msgtype": "text",
                               "text": {"content": f"{title}\n{text}"}})
                body = r.json() if r.content else {}
                if r.status_code != 200 or body.get("errcode") not in (0, None):
                    return f"企微返回 {r.status_code}: {body}"
            else:  # 通用 webhook: 结构化 JSON, 客户自己接
                r = await client.post(url, json={
                    "source": "tianjun-fleet-hub", "title": title,
                    "text": text, **payload})
                if r.status_code >= 400:
                    return f"HTTP {r.status_code}"
    except Exception as e:
        return f"{e.__class__.__name__}: {e}"
    return None


async def broadcast(cfg: Dict[str, Any], title: str, text: str,
                    payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """向全部通道投递, 返回逐通道结果 (后台调用忽略返回, 测试端点透出)。"""
    results = []
    for ch in cfg.get("channels") or []:
        err = await _send_one(ch, title, text, payload)
        results.append({"type": ch.get("type"), "url": ch.get("url"),
                        "ok": err is None, "error": err})
    return results


# ============================================================
# 端点 (admin 专属)
# ============================================================

class NotifyChannel(BaseModel):
    type: str                      # webhook / dingtalk / wecom
    url: str
    secret: Optional[str] = None   # 钉钉加签密钥 (可选)


class NotifyConfig(BaseModel):
    enabled: bool = False
    offline_threshold_min: int = 5
    notify_recover: bool = True
    channels: List[NotifyChannel] = []
    alarm_escalate_min: int = 0            # NG 未确认超 N 分钟升级 (0=关)
    alarm_escalate_cooldown_min: int = 30  # 升级通知冷却窗


@router.get("/notify/config", response_model=NotifyConfig,
            summary="通知出口配置",
            dependencies=[Depends(require_perm("node.manage"))])
def read_config(db: Session = Depends(get_db)):
    return get_config(db)


@router.put("/notify/config", response_model=NotifyConfig,
            summary="保存通知出口配置",
            dependencies=[Depends(require_perm("node.manage"))])
def write_config(payload: NotifyConfig, db: Session = Depends(get_db)):
    cfg = payload.model_dump()
    cfg["offline_threshold_min"] = max(0, int(cfg["offline_threshold_min"]))
    cfg["alarm_escalate_min"] = max(0, int(cfg["alarm_escalate_min"]))
    cfg["alarm_escalate_cooldown_min"] = max(
        1, int(cfg["alarm_escalate_cooldown_min"]))
    save_config(db, cfg)
    db.commit()
    return cfg


@router.post("/notify/test", summary="向全部通道发测试消息",
             dependencies=[Depends(require_perm("node.manage"))])
async def send_test(db: Session = Depends(get_db)):
    cfg = get_config(db)
    results = await broadcast(
        cfg, "【测试】集中管控枢纽通知通道",
        f"这是一条测试消息 ({datetime.now().strftime('%m-%d %H:%M:%S')})。"
        "收到即通道配置正确。",
        {"event": "test"})
    return {"channels": len(results), "results": results}
