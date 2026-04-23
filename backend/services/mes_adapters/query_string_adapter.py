"""
URL Query String 适配器

不管 method 是什么，都把 payload 展开成 URL query 参数发，请求体为空。
例：POST http://mes.local/report?order_no=WO-001&result=NG&missing_items=A,B

适用于客户 MES 要求"参数全放 URL 里"的场景；GET/POST 都支持。

配置项：
  url            接口地址（不用带 ?）
  method         POST / GET / PUT (默认 POST)
  array_join     列表字段的拼接分隔符, 默认 ","; 设为 "json" 则走 JSON 字符串
  dict_as_json   嵌套 dict 是否转成 JSON 字符串, 默认 True
  headers        自定义请求头
  auth           同其他适配器
"""
from __future__ import annotations
import json
import time
import requests
from typing import Any
from backend.services.mes_adapters.base import BaseAdapter


def _flatten_value(v: Any, array_join: str, dict_as_json: bool) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        if array_join == "json":
            return json.dumps(list(v), ensure_ascii=False)
        return array_join.join(_flatten_value(x, array_join, dict_as_json) for x in v)
    if isinstance(v, dict):
        if dict_as_json:
            return json.dumps(v, ensure_ascii=False)
        return str(v)
    return str(v)


class QueryStringAdapter(BaseAdapter):

    def send(self, payload: Any, config: dict) -> dict:
        url = config.get("url", "")
        method = config.get("method", "POST").upper()
        timeout = config.get("timeout", 30)
        headers = {**config.get("headers", {})}
        array_join = config.get("array_join", ",")
        dict_as_json = bool(config.get("dict_as_json", True))

        auth_config = config.get("auth")
        auth = None
        if auth_config and auth_config.get("type") == "basic":
            auth = (auth_config.get("username", ""), auth_config.get("password", ""))

        if isinstance(payload, dict):
            params = {k: _flatten_value(v, array_join, dict_as_json) for k, v in payload.items()}
        else:
            params = {"data": _flatten_value(payload, array_join, dict_as_json)}

        start = time.time()
        try:
            resp = requests.request(
                method, url,
                params=params,
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
            return {"status_code": 0, "body": None, "success": False,
                    "error": f"请求超时 ({timeout}s)",
                    "duration_ms": int((time.time() - start) * 1000)}
        except requests.ConnectionError as e:
            return {"status_code": 0, "body": None, "success": False,
                    "error": f"连接失败: {e}",
                    "duration_ms": int((time.time() - start) * 1000)}
        except Exception as e:
            return {"status_code": 0, "body": None, "success": False,
                    "error": str(e),
                    "duration_ms": int((time.time() - start) * 1000)}
