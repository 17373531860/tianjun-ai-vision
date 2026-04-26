"""
REST/JSON 适配器

发送 JSON 格式的 HTTP 请求到外部 MES。
支持 GET/POST/PUT, 可配置 headers 和 auth。
"""
import time
import requests
from typing import Any
from backend.services.mes_adapters.base import BaseAdapter


class RESTAdapter(BaseAdapter):

    def send(self, payload: Any, config: dict) -> dict:
        url = config.get("url", "")
        method = config.get("method", "POST").upper()
        headers = {**config.get("headers", {})}
        timeout = config.get("timeout", 30)

        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

        auth = self._build_auth(config.get("auth"))

        start = time.time()
        try:
            resp = requests.request(
                method, url,
                json=payload if method in ("POST", "PUT", "PATCH") else None,
                params=payload if method == "GET" else None,
                headers=headers,
                auth=auth,
                timeout=timeout,
            )
            elapsed = int((time.time() - start) * 1000)
            try:
                body = resp.json()
            except Exception:
                body = resp.text

            return {
                "status_code": resp.status_code,
                "body": body,
                "success": True,
                "error": None,
                "duration_ms": elapsed,
            }
        except requests.Timeout:
            return {
                "status_code": 0, "body": None, "success": False,
                "error": f"请求超时 ({timeout}s)",
                "duration_ms": int((time.time() - start) * 1000),
            }
        except requests.ConnectionError as e:
            return {
                "status_code": 0, "body": None, "success": False,
                "error": f"连接失败: {e}",
                "duration_ms": int((time.time() - start) * 1000),
            }
        except Exception as e:
            return {
                "status_code": 0, "body": None, "success": False,
                "error": str(e),
                "duration_ms": int((time.time() - start) * 1000),
            }

    def _build_auth(self, auth_config):
        if not auth_config or auth_config.get("type") == "none":
            return None
        if auth_config.get("type") == "basic":
            return (auth_config.get("username", ""), auth_config.get("password", ""))
        if auth_config.get("type") == "bearer":
            return None  # bearer token handled in headers
        return None
