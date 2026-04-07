"""
Form-Data 适配器

将 payload 序列化为 JSON 字符串后放入 form-data 的指定 key 中发送。
适用于客户 MES 要求 form-data 传参的场景 (如: param={"macno":"...", "data":{...}})。
"""
import json
import time
import requests
from typing import Any
from backend.services.mes_adapters.base import BaseAdapter


class FormDataAdapter(BaseAdapter):

    def send(self, payload: Any, config: dict) -> dict:
        url = config.get("url", "")
        form_key = config.get("form_key", "param")
        timeout = config.get("timeout", 30)
        headers = {**config.get("headers", {})}
        auth_config = config.get("auth")

        auth = None
        if auth_config and auth_config.get("type") == "basic":
            auth = (auth_config.get("username", ""), auth_config.get("password", ""))

        payload_str = json.dumps(payload, ensure_ascii=False)
        form_data = {form_key: payload_str}

        start = time.time()
        try:
            resp = requests.post(
                url, data=form_data,
                headers=headers, auth=auth, timeout=timeout,
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
