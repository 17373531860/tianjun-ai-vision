"""
application/x-www-form-urlencoded 适配器

把 payload（dict）的每个字段平铺成一个 form 字段发出去，如：
    order_no=WO-001&result=NG&missing_items=A,B

区别于 form_data_adapter：后者把整个 payload 打成一个 JSON 字符串塞进一个固定字段（如 param），
本适配器是每个字段各占一个 form key，客户如果说"字段分开传、不要打包"就用这个。

配置项：
  url            接口地址
  method         POST / PUT / GET (默认 POST)
  array_join     列表字段的拼接分隔符, 默认 ","; 设为 "json" 则走 JSON 字符串
  dict_as_json   嵌套 dict 是否转成 JSON 字符串, 默认 True
  headers        自定义请求头（会合并）
  auth           同其他适配器 (none/basic/bearer/api_key/custom_header 在 gateway 层合并)
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


class FormUrlencodedAdapter(BaseAdapter):

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
            data = {k: _flatten_value(v, array_join, dict_as_json) for k, v in payload.items()}
        else:
            data = {"data": _flatten_value(payload, array_join, dict_as_json)}

        start = time.time()
        try:
            if method == "GET":
                resp = requests.get(url, params=data, headers=headers,
                                    auth=auth, timeout=timeout)
            else:
                resp = requests.request(
                    method, url,
                    data=data,
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
