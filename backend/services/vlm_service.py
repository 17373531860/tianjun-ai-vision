# -*- coding: utf-8 -*-
"""本地多模态大模型 (VLM) 坐诊服务 (2026-09 全量批次)。

定位 (调研点 1-7 / 朝向 RFC 语义层): "站岗检测 + VLM 坐诊" —— 实时引擎
毫秒级快筛, VLM 只看关键帧回答"看懂"类问题 (这个人面向哪块面板? 这张
NG 图缺陷是什么? 门开了没?), 并产出人话审计文本进台账。

明确不进实时帧循环 (两段话原文: 又慢又难进现场 ONNX 契约, 不当工位
实时引擎) —— 全部按需调用, 默认关闭零开销。

接入形态: OpenAI 兼容 HTTP 端点 (chat/completions + base64 图)。
  工控机本地跑 Ollama / vLLM / LM Studio 均是此协议; 推荐模型
  Qwen2.5-VL-7B (已核查 Apache 2.0 可商用; 3B 版非商用、72B 版有
  1 亿 MAU 门槛条款, 选型钉死 7B)。云端点同协议也能接 (客户自担合规)。

配置: SystemConfig KV ``vlm_config`` (JSON):
  {"enabled": false, "endpoint": "http://127.0.0.1:11434/v1",
   "api_key": "", "model": "qwen2.5-vl:7b", "timeout_s": 60,
   "max_tokens": 512, "system_prompt": ""}
"""
from __future__ import annotations

import base64
import json
import threading
import time
from typing import Optional

import numpy as np

KV_KEY = "vlm_config"

DEFAULT_CONFIG = {
    "enabled": False,
    "endpoint": "http://127.0.0.1:11434/v1",
    "api_key": "",
    "model": "qwen2.5-vl:7b",
    "timeout_s": 60,
    "max_tokens": 512,
    "system_prompt": "你是工厂视觉检测系统的图像分析助手。用简体中文简明回答，"
                     "先给结论再给依据，不要编造画面里不存在的内容。",
}

_cache_lock = threading.Lock()
_cached: Optional[dict] = None
_cached_at: float = 0.0
_CACHE_TTL = 10.0


class VlmError(RuntimeError):
    """配置/调用错误, message 带处置指引。"""


def _read_kv() -> Optional[str]:
    try:
        from backend.db.database import SessionLocal
        from backend.models.models import SystemConfig
        db = SessionLocal()
        try:
            row = db.query(SystemConfig).filter(
                SystemConfig.key == KV_KEY).first()
            return (row.value or "").strip() if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[VLM] 读取配置失败, 回退默认(关闭): {e}")
        return None


def get_config(force: bool = False) -> dict:
    """当前配置 (KV 覆盖默认值, 带 TTL 缓存)。"""
    global _cached, _cached_at
    with _cache_lock:
        if not force and _cached is not None and (time.time() - _cached_at) < _CACHE_TTL:
            return dict(_cached)
    cfg = dict(DEFAULT_CONFIG)
    raw = _read_kv()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                for k in DEFAULT_CONFIG:
                    if k in data and data[k] is not None:
                        cfg[k] = data[k]
        except Exception as e:
            print(f"[VLM] 配置 JSON 解析失败, 用默认: {e}")
    with _cache_lock:
        _cached = dict(cfg)
        _cached_at = time.time()
    return cfg


def save_config(patch: dict) -> dict:
    """合并保存配置到 KV (只收白名单键), 返回生效配置。"""
    from backend.db.database import SessionLocal
    from backend.models.models import SystemConfig
    cfg = get_config(force=True)
    for k in DEFAULT_CONFIG:
        if k in patch and patch[k] is not None:
            cfg[k] = patch[k]
    cfg["enabled"] = bool(cfg.get("enabled"))
    cfg["timeout_s"] = max(5, min(600, int(cfg.get("timeout_s") or 60)))
    cfg["max_tokens"] = max(16, min(4096, int(cfg.get("max_tokens") or 512)))
    db = SessionLocal()
    try:
        row = db.query(SystemConfig).filter(SystemConfig.key == KV_KEY).first()
        val = json.dumps(cfg, ensure_ascii=False)
        if row:
            row.value = val
        else:
            db.add(SystemConfig(key=KV_KEY, value=val))
        db.commit()
    finally:
        db.close()
    global _cached, _cached_at
    with _cache_lock:
        _cached = dict(cfg)
        _cached_at = time.time()
    return cfg


def _encode_jpeg_b64(image_bgr: np.ndarray, max_side: int = 1280) -> str:
    import cv2
    h, w = image_bgr.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1.0:
        image_bgr = cv2.resize(image_bgr, (int(w * scale), int(h * scale)))
    ok, buf = cv2.imencode(".jpg", image_bgr, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise VlmError("图像编码失败")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def ask(image_bgr: np.ndarray, question: str,
        config: Optional[dict] = None) -> dict:
    """单图问答。返回 {"answer", "model", "latency_ms"}。

    默认关闭时抛 VlmError (调用方给用户明确提示), 不做任何网络请求。
    """
    import requests

    cfg = config or get_config()
    if not cfg.get("enabled"):
        raise VlmError("VLM 坐诊未启用: 请在 AI 能力试用页配置本地端点后开启 "
                       "(推荐 Ollama + Qwen2.5-VL-7B, Apache 2.0 可商用)")
    question = (question or "").strip()
    if not question:
        raise VlmError("问题不能为空")

    b64 = _encode_jpeg_b64(image_bgr)
    endpoint = (cfg.get("endpoint") or "").rstrip("/")
    if not endpoint:
        raise VlmError("VLM 端点未配置")
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    messages = []
    if cfg.get("system_prompt"):
        messages.append({"role": "system", "content": cfg["system_prompt"]})
    messages.append({
        "role": "user",
        "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": question},
        ],
    })
    payload = {
        "model": cfg.get("model") or "qwen2.5-vl:7b",
        "messages": messages,
        "max_tokens": int(cfg.get("max_tokens") or 512),
        "stream": False,
    }
    t0 = time.time()
    try:
        resp = requests.post(f"{endpoint}/chat/completions", json=payload,
                             headers=headers,
                             timeout=float(cfg.get("timeout_s") or 60))
    except Exception as e:
        raise VlmError(f"VLM 端点连接失败 ({endpoint}): {e}; "
                       f"请确认本地推理服务 (如 Ollama) 已启动") from e
    if resp.status_code != 200:
        raise VlmError(f"VLM 端点返回 {resp.status_code}: {resp.text[:300]}")
    try:
        data = resp.json()
        answer = data["choices"][0]["message"]["content"]
    except Exception as e:
        raise VlmError(f"VLM 响应解析失败: {e}") from e
    return {
        "answer": (answer or "").strip(),
        "model": data.get("model") or payload["model"],
        "latency_ms": int((time.time() - t0) * 1000),
    }


def probe(config: Optional[dict] = None) -> dict:
    """轻量探活: GET {endpoint}/models, 不发图。"""
    import requests
    cfg = config or get_config()
    endpoint = (cfg.get("endpoint") or "").rstrip("/")
    out = {"enabled": bool(cfg.get("enabled")), "endpoint": endpoint,
           "model": cfg.get("model"), "reachable": False, "detail": ""}
    if not endpoint:
        out["detail"] = "端点未配置"
        return out
    headers = {}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    try:
        resp = requests.get(f"{endpoint}/models", headers=headers, timeout=5)
        out["reachable"] = resp.status_code == 200
        out["detail"] = f"HTTP {resp.status_code}"
        if resp.status_code == 200:
            try:
                ids = [m.get("id") for m in resp.json().get("data", [])]
                out["available_models"] = ids[:20]
            except Exception:
                pass
    except Exception as e:
        out["detail"] = str(e)
    return out
