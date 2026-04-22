"""
模拟客户 MES 接收端 + 用 tianjun 真实 adapter 端到端测试

覆盖的 5 种场景 (多端口并行 mock server, 同一进程内线程起):
  8801  form-data + 无鉴权          (基础场景)
  8802  form-data + Basic Auth       (老式客户 MES)
  8803  form-data + Bearer Token     (现代云 MES)
  8804  form-data + API Key Header   (SaaS 风格)
  8805  REST JSON                    (对照, 客户用 JSON 而非 form-data)

每个 mock server 会:
  - 校验鉴权头
  - 回显收到的 form-data / JSON body
  - 返回 200 {"code": 0, "msg": "ok"} 表示成功
  - 请求内容 print 到终端, 肉眼可见地验证 tianjun 发对了

测试端:
  - 直接调用 tianjun 的 mes_adapters / mes_gateway, 模拟 box_complete 推送
  - 不依赖 DB, 不需要启 tianjun 后端

运行:
  cd /path/to/tianjun && python3 tools/test_mes_gateway.py
"""
from __future__ import annotations
import base64
import json
import os
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

# 让 python 找到 backend 包
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from backend.services.mes_adapters import get_adapter  # noqa: E402
from backend.services.mes_gateway import MESGateway    # noqa: E402


# ==================== Mock 客户 MES Server ====================

class MockMESHandler(BaseHTTPRequestHandler):
    """单端口 mock, 按端口决定校验策略."""

    # 端口配置: {port: {auth_type, expected_*}}
    PORT_CONFIG: dict = {}
    # 每个端口收到的请求会推入这里, 供测试端断言
    RECEIVED: dict = {}

    def log_message(self, format, *args):
        # 屏蔽默认访问日志, 避免刷屏
        pass

    def do_POST(self):
        port = self.server.server_address[1]
        cfg = self.PORT_CONFIG.get(port, {})
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")

        # ---- 鉴权校验 ----
        auth_result = self._check_auth(cfg)
        if not auth_result["ok"]:
            self._respond(401, {"code": 401, "msg": auth_result["msg"]})
            return

        # ---- body 解析 ----
        parsed = self._parse_body(raw, content_type)

        # ---- 记录到 RECEIVED ----
        entry = {
            "port": port,
            "path": self.path,
            "method": "POST",
            "content_type": content_type,
            "auth_header": self.headers.get("Authorization") or "",
            "api_key_header": self.headers.get("X-API-Key") or "",
            "custom_headers": {
                k: v for k, v in self.headers.items()
                if k.lower().startswith("x-") and k.lower() != "x-api-key"
            },
            "raw_body": raw.decode("utf-8", errors="replace"),
            "parsed": parsed,
        }
        self.RECEIVED.setdefault(port, []).append(entry)

        # ---- 打印 ----
        scenario = cfg.get("scenario", f"port-{port}")
        print(f"\n{'='*70}")
        print(f"  收到请求 @ 端口 {port}  【{scenario}】")
        print(f"{'='*70}")
        print(f"  Authorization : {entry['auth_header'] or '(none)'}")
        print(f"  X-API-Key     : {entry['api_key_header'] or '(none)'}")
        if entry["custom_headers"]:
            print(f"  自定义 headers: {entry['custom_headers']}")
        print(f"  Content-Type  : {content_type}")
        print(f"  业务数据 (已解出 param):")
        print(json.dumps(parsed.get("payload"), indent=4, ensure_ascii=False))

        # ---- 响应 ----
        self._respond(200, {"code": 0, "msg": "ok"})

    def _check_auth(self, cfg: dict) -> dict:
        atype = cfg.get("auth_type", "none")
        if atype == "none":
            return {"ok": True}
        if atype == "basic":
            hdr = self.headers.get("Authorization", "")
            if not hdr.startswith("Basic "):
                return {"ok": False, "msg": "missing Basic auth"}
            raw = base64.b64decode(hdr[6:]).decode("utf-8", errors="replace")
            user, _, pw = raw.partition(":")
            if user != cfg.get("expected_username") or pw != cfg.get("expected_password"):
                return {"ok": False, "msg": "wrong username/password"}
            return {"ok": True}
        if atype == "bearer":
            hdr = self.headers.get("Authorization", "")
            if not hdr.startswith("Bearer "):
                return {"ok": False, "msg": "missing Bearer token"}
            if hdr[7:] != cfg.get("expected_token"):
                return {"ok": False, "msg": "wrong token"}
            return {"ok": True}
        if atype == "api_key":
            key_hdr = cfg.get("expected_header", "X-API-Key")
            if self.headers.get(key_hdr) != cfg.get("expected_value"):
                return {"ok": False, "msg": f"missing/wrong {key_hdr}"}
            return {"ok": True}
        return {"ok": True}

    def _parse_body(self, raw: bytes, content_type: str) -> dict:
        """解析 body, 取出 param JSON 或原始 JSON"""
        if "application/json" in content_type:
            try:
                return {"payload": json.loads(raw.decode("utf-8"))}
            except Exception as e:
                return {"error": f"JSON 解析失败: {e}", "raw": raw.decode("utf-8", errors="replace")}
        if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            # form-urlencoded: param={...}
            try:
                form = parse_qs(raw.decode("utf-8"))
                param = (form.get("param") or form.get("data") or form.get("json") or [""])[0]
                if param:
                    try:
                        return {"payload": json.loads(param), "form_key": _detect_key(form)}
                    except Exception:
                        return {"payload": param, "form_key": _detect_key(form)}
                return {"raw_form": form}
            except Exception as e:
                return {"error": f"form 解析失败: {e}"}
        return {"raw": raw.decode("utf-8", errors="replace")}

    def _respond(self, status: int, body: dict):
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _detect_key(form: dict) -> str:
    for k in ("param", "data", "json", "content"):
        if k in form:
            return k
    return list(form.keys())[0] if form else ""


def _probe_port(port: int) -> bool:
    """检测端口是否空闲, 占用 → False"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def start_mock_servers():
    """后台启 5 个 mock server, 返回 servers 列表.

    自动检测端口占用, 若有冲突会打印清单并 sys.exit(2).
    """
    scenarios = {
        8801: {"scenario": "form-data · 无鉴权", "auth_type": "none"},
        8802: {"scenario": "form-data · Basic Auth", "auth_type": "basic",
               "expected_username": "tianjun", "expected_password": "secret123"},
        8803: {"scenario": "form-data · Bearer Token", "auth_type": "bearer",
               "expected_token": "eyJhbGciOi.FAKE.TOKEN"},
        8804: {"scenario": "form-data · API Key Header", "auth_type": "api_key",
               "expected_header": "X-API-Key", "expected_value": "mes-api-key-xyz"},
        8805: {"scenario": "REST JSON · 无鉴权", "auth_type": "none"},
    }

    occupied = [p for p in scenarios if not _probe_port(p)]
    if occupied:
        print("\n[ERROR] 以下端口被占用, 无法启动 mock server:")
        for p in occupied:
            print(f"  - {p} ({scenarios[p]['scenario']})")
        print("\n请先释放这些端口, 或用 lsof -i:<端口> 查占用进程.\n")
        sys.exit(2)

    MockMESHandler.PORT_CONFIG = scenarios
    servers = []
    for port in scenarios:
        srv = ThreadingHTTPServer(("127.0.0.1", port), MockMESHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True, name=f"mock-mes-{port}")
        t.start()
        servers.append(srv)
    return servers, scenarios


# ==================== tianjun 推送端 ====================

def build_aggregated_ng() -> dict:
    """模拟 cluster_collector._check_and_dispatch 生成的 NG 箱 aggregated"""
    return {
        "box_serial": "BOX-20260423-001",
        "overall_result": "NG", "result": "NG",
        "total_stations": 2, "completed_stations": 2,
        "order_no": "WO-ORD-001",
        "workpiece_id": "SN-123457",
        "ng_items": ["螺丝A", "垫片B"],
        "stations": [
            {"station_id": "A", "is_good": True, "ng_steps": []},
            {"station_id": "B", "is_good": False,
             "ng_steps": [{"label": "螺丝A"}, {"label": "垫片B"}]},
        ],
        "timestamp": "2026-04-23T10:30:00",
    }


def build_aggregated_ok() -> dict:
    return {
        "box_serial": "BOX-20260423-002",
        "overall_result": "OK", "result": "OK",
        "total_stations": 2, "completed_stations": 2,
        "order_no": "WO-ORD-001",
        "workpiece_id": "SN-123456",
        "ng_items": [],
        "stations": [
            {"station_id": "A", "is_good": True, "ng_steps": []},
            {"station_id": "B", "is_good": True, "ng_steps": []},
        ],
        "timestamp": "2026-04-23T10:31:00",
    }


def push_once(name: str, adapter_type: str, config: dict, aggregated: dict) -> dict:
    """模拟 _send_to_connection 的核心路径, 返回 {payload, response}"""
    # 1. push_on_result 过滤 (复刻 _send_to_connection 里的逻辑)
    push_on = config.get("push_on_result")
    if push_on:
        r = (aggregated.get("overall_result") or aggregated.get("result") or "").upper()
        if r and r not in [str(x).upper() for x in push_on]:
            print(f"  ⚠ [{name}] result={r} 被 push_on_result={push_on} 过滤, 不推送")
            return {"skipped": True}

    # 2. label_mapping
    full_ctx = dict(aggregated)
    lm = config.get("label_mapping") or {}
    if lm and isinstance(full_ctx.get("ng_items"), list):
        full_ctx["ng_items_mapped"] = [lm.get(x, x) for x in full_ctx["ng_items"]]
        if config.get("label_mapping_mode", "replace") == "replace":
            full_ctx["ng_items"] = full_ctx["ng_items_mapped"]

    # 3. 鉴权合并 → headers
    effective = MESGateway._apply_auth_to_headers(config)

    # 4. adapter send
    adapter = get_adapter(adapter_type)
    payload = adapter.build_payload(full_ctx, effective)
    result = adapter.send(payload, effective)
    ok = adapter.check_response(result, effective)

    return {"payload": payload, "response": result, "adapter_success": ok}


def run_tests():
    base_url = "http://127.0.0.1"

    ng = build_aggregated_ng()
    ok = build_aggregated_ok()

    scenarios = [
        {
            "name": "场景1  form-data 无鉴权 + 4字段",
            "adapter": "form-data",
            "aggregated": ng,
            "config": {
                "url": f"{base_url}:8801/api/inspection",
                "form_key": "param",
                "auth": {"type": "none"},
                "template": {
                    "order_no": "{order_no}",
                    "workpiece_id": "{workpiece_id}",
                    "result": "{overall_result}",
                    "missing_items": {"_array_source": "ng_items", "_item_template": "{item}"},
                },
            },
            "expect_port": 8801,
            "expect_http": 200,
        },
        {
            "name": "场景2  form-data Basic Auth",
            "adapter": "form-data",
            "aggregated": ng,
            "config": {
                "url": f"{base_url}:8802/api/inspection",
                "form_key": "param",
                "auth": {"type": "basic", "username": "tianjun", "password": "secret123"},
                "template": {
                    "order_no": "{order_no}",
                    "workpiece_id": "{workpiece_id}",
                    "result": "{overall_result}",
                    "missing_items": {"_array_source": "ng_items", "_item_template": "{item}"},
                },
            },
            "expect_port": 8802,
            "expect_http": 200,
        },
        {
            "name": "场景3  form-data Bearer Token + 物料映射",
            "adapter": "form-data",
            "aggregated": ng,
            "config": {
                "url": f"{base_url}:8803/api/inspection",
                "form_key": "param",
                "auth": {"type": "bearer", "token": "eyJhbGciOi.FAKE.TOKEN"},
                "label_mapping": {"螺丝A": "PART-001", "垫片B": "MAT-SC-B02"},
                "label_mapping_mode": "replace",
                "template": {
                    "order_no": "{order_no}",
                    "workpiece_id": "{workpiece_id}",
                    "result": "{overall_result}",
                    "missing_items": {"_array_source": "ng_items", "_item_template": "{item}"},
                },
            },
            "expect_port": 8803,
            "expect_http": 200,
            "expect_missing_items": ["PART-001", "MAT-SC-B02"],
        },
        {
            "name": "场景4  form-data API Key Header + 自定义 form_key",
            "adapter": "form-data",
            "aggregated": ng,
            "config": {
                "url": f"{base_url}:8804/api/inspection",
                "form_key": "data",  # 客户用 data 不是 param
                "auth": {"type": "api_key", "header": "X-API-Key", "value": "mes-api-key-xyz"},
                "template": {
                    "order_no": "{order_no}",
                    "workpiece_id": "{workpiece_id}",
                    "result": "{overall_result}",
                    "missing_items": {"_array_source": "ng_items", "_item_template": "{item}"},
                },
            },
            "expect_port": 8804,
            "expect_http": 200,
            "expect_form_key": "data",
        },
        {
            "name": "场景5  REST JSON (对照) 客户用 JSON 而非 form",
            "adapter": "rest",
            "aggregated": ng,
            "config": {
                "url": f"{base_url}:8805/api/inspection",
                "method": "POST",
                "auth": {"type": "none"},
                "template": {
                    "order_no": "{order_no}",
                    "workpiece_id": "{workpiece_id}",
                    "result": "{overall_result}",
                    "missing_items": {"_array_source": "ng_items", "_item_template": "{item}"},
                },
            },
            "expect_port": 8805,
            "expect_http": 200,
        },
        {
            "name": "场景6  push_on_result=[NG] 对 OK 箱应跳过",
            "adapter": "form-data",
            "aggregated": ok,  # OK 箱
            "config": {
                "url": f"{base_url}:8801/api/inspection",
                "form_key": "param",
                "auth": {"type": "none"},
                "push_on_result": ["NG"],
                "template": {"result": "{overall_result}"},
            },
            "expect_skipped": True,
        },
        {
            "name": "场景7  鉴权错误 → HTTP 401 (负面测试)",
            "adapter": "form-data",
            "aggregated": ng,
            "config": {
                "url": f"{base_url}:8802/api/inspection",
                "form_key": "param",
                "auth": {"type": "basic", "username": "wrong", "password": "wrong"},
                "template": {"result": "{overall_result}"},
            },
            "expect_http": 401,
        },
    ]

    print("\n" + "="*70)
    print(" " * 18 + "开始端到端测试 (7 场景)")
    print("="*70)

    results = []
    for s in scenarios:
        print(f"\n▶  {s['name']}")
        try:
            out = push_once(s["name"], s["adapter"], s["config"], s["aggregated"])
        except Exception as e:
            print(f"  ✗ 异常: {e}")
            traceback.print_exc()
            results.append((s["name"], False, str(e)))
            continue

        verdict, reason = _verify(s, out)
        mark = "✓" if verdict else "✗"
        print(f"  {mark} {reason}")
        results.append((s["name"], verdict, reason))
        time.sleep(0.2)  # 让 mock server 的 print 完整输出再进下一场景

    # ---- 总结 ----
    print("\n" + "="*70)
    print(" " * 22 + "测试结果汇总")
    print("="*70)
    for name, ok, reason in results:
        print(f"  {'✓' if ok else '✗'}  {name}")
        if not ok:
            print(f"        → {reason}")
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n  {passed}/{total} 通过")
    return passed == total


def _verify(scenario: dict, out: dict) -> tuple[bool, str]:
    # 跳过场景
    if scenario.get("expect_skipped"):
        if out.get("skipped"):
            return True, "按预期被 push_on_result 过滤"
        return False, "应被过滤但实际推送了"

    if out.get("skipped"):
        return False, "被意外过滤"

    resp = out.get("response") or {}
    status = resp.get("status_code")
    expect_http = scenario.get("expect_http", 200)
    if status != expect_http:
        return False, f"HTTP {status} ≠ 预期 {expect_http}, err={resp.get('error')}"

    if expect_http >= 400:
        return True, f"HTTP {status} 按预期失败"

    # 正常场景: 校验 mock server 收到的数据
    port = scenario.get("expect_port")
    received = MockMESHandler.RECEIVED.get(port, [])
    if not received:
        return False, f"mock:{port} 未收到任何请求"
    last = received[-1]
    parsed = last.get("parsed") or {}
    payload = parsed.get("payload")
    if not isinstance(payload, dict):
        return False, f"mock 端收到的不是 dict: {payload}"

    # 核对 4 字段存在
    for f in ("order_no", "workpiece_id", "result"):
        if f not in payload:
            return False, f"payload 缺字段 {f}: {payload}"

    # 物料映射
    if "expect_missing_items" in scenario:
        if payload.get("missing_items") != scenario["expect_missing_items"]:
            return False, (f"missing_items={payload.get('missing_items')} "
                           f"≠ 期望 {scenario['expect_missing_items']}")

    # form_key
    if "expect_form_key" in scenario:
        if parsed.get("form_key") != scenario["expect_form_key"]:
            return False, f"form_key={parsed.get('form_key')} ≠ 期望 {scenario['expect_form_key']}"

    return True, f"HTTP 200, payload OK: {list(payload.keys())}"


# ==================== 走后端 test API 路径的验证 ====================
# 这部分**不起 FastAPI**, 而是直接 import test_connection 函数,
# 用 SessionLocal 临时创建 MESConnection 记录, 调用 test_connection(),
# 走完整 DB → label_mapping → _apply_auth_to_headers → adapter.send 链路,
# 再删除临时记录. 这验证的是前端点"测试"按钮时的真实行为.


def run_api_tests():
    from backend.db.database import SessionLocal  # noqa: WPS433
    from backend.models.mes_models import MESConnection  # noqa: WPS433
    from backend.api.mes_gateway import test_connection, TestPayload  # noqa: WPS433

    ng = build_aggregated_ng()
    api_scenarios = [
        {
            "name": "API场景1  /test + box_complete + Bearer + 映射",
            "adapter": "form-data",
            "event_type": "box_complete",
            "config": {
                "url": "http://127.0.0.1:8803/api/inspection",
                "form_key": "param",
                "auth": {"type": "bearer", "token": "eyJhbGciOi.FAKE.TOKEN"},
                "label_mapping": {"螺丝A": "PART-001", "垫片B": "MAT-SC-B02"},
                "label_mapping_mode": "replace",
                "template": {
                    "order_no": "{order_no}",
                    "workpiece_id": "{workpiece_id}",
                    "result": "{overall_result}",
                    "missing_items": {"_array_source": "ng_items", "_item_template": "{item}"},
                },
            },
            "expect_port": 8803,
            "expect_missing_items": ["PART-001", "MAT-SC-B02"],
        },
        {
            "name": "API场景2  /test + cycle_end + Basic",
            "adapter": "form-data",
            "event_type": "cycle_end",
            "config": {
                "url": "http://127.0.0.1:8802/api/inspection",
                "form_key": "param",
                "auth": {"type": "basic", "username": "tianjun", "password": "secret123"},
                "template": {
                    "order_no": "{order.order_no}",
                    "workpiece_id": "{workpiece.serial_no}",
                    "result": "{cycle.result}",
                    "duration": "{cycle.duration}",
                },
            },
            "expect_port": 8802,
        },
        {
            "name": "API场景3  /test 不受 push_on_result 过滤 (配 push_on_result=[NG] 仍必须发出)",
            "adapter": "form-data",
            "event_type": "box_complete",
            "config": {
                "url": "http://127.0.0.1:8801/api/inspection",
                "form_key": "param",
                "auth": {"type": "none"},
                # 这个设置在真实推送时, OK 箱会被过滤掉;
                # 但 /test 端点**故意**绕过 push_on_result, 保证点"测试"一定能看到结果
                "push_on_result": ["NG"],
                "template": {"result": "{overall_result}"},
            },
            "expect_port": 8801,
            # /test 用的内置 context 是 NG 箱, 所以实际 result 就是 NG, 验证请求确实发出即可
            "expect_result_value": "NG",
        },
    ]

    db = SessionLocal()
    results = []
    try:
        for s in api_scenarios:
            print(f"\n▶  {s['name']}")
            conn = MESConnection(
                name=f"mock-{s['event_type']}-{int(time.time() * 1000)}",
                adapter_type=s["adapter"],
                enabled=False,
                config=s["config"],
                push_events=[s["event_type"]],
                retry_count=0,
                retry_interval_sec=5,
            )
            db.add(conn)
            db.commit()
            cid = conn.id
            try:
                payload = TestPayload(event_type=s["event_type"])
                resp = test_connection(cid, payload)
                port = s["expect_port"]
                received = MockMESHandler.RECEIVED.get(port, [])
                if not received:
                    raise AssertionError(f"mock:{port} 未收到请求")
                last = received[-1]
                body = (last.get("parsed") or {}).get("payload") or {}
                if "expect_missing_items" in s:
                    if body.get("missing_items") != s["expect_missing_items"]:
                        raise AssertionError(
                            f"missing_items={body.get('missing_items')} "
                            f"≠ {s['expect_missing_items']}"
                        )
                if "expect_result_value" in s:
                    if body.get("result") != s["expect_result_value"]:
                        raise AssertionError(
                            f"result={body.get('result')} "
                            f"≠ {s['expect_result_value']}"
                        )
                if resp.get("status_code") != 200:
                    raise AssertionError(f"HTTP {resp.get('status_code')}, err={resp.get('error')}")
                print(f"  ✓ event={s['event_type']} HTTP={resp['status_code']} "
                      f"payload_keys={list(body.keys())}")
                results.append((s["name"], True, "ok"))
            except Exception as e:
                print(f"  ✗ {e}")
                results.append((s["name"], False, str(e)))
                traceback.print_exc()
            finally:
                db.delete(db.merge(conn))
                db.commit()
            time.sleep(0.2)
    finally:
        db.close()

    return results


# ==================== main ====================

def main():
    servers, scenarios = start_mock_servers()
    print("\n" + "="*70)
    print(" " * 18 + "模拟客户 MES 已启动 (5 个端口)")
    print("="*70)
    for port, cfg in scenarios.items():
        print(f"  127.0.0.1:{port}  →  {cfg['scenario']}  [auth={cfg['auth_type']}]")

    time.sleep(0.3)  # 让 server 就绪
    success1 = run_tests()

    print("\n" + "=" * 70)
    print(" " * 14 + "开始后端 /test API 端到端测试 (3 场景)")
    print("=" * 70)
    api_results = run_api_tests()
    print("\n" + "=" * 70)
    print(" " * 20 + "API 路径测试汇总")
    print("=" * 70)
    for name, ok, reason in api_results:
        print(f"  {'✓' if ok else '✗'}  {name}")
        if not ok:
            print(f"        → {reason}")
    api_passed = sum(1 for _, ok, _ in api_results if ok)
    print(f"\n  API 路径: {api_passed}/{len(api_results)} 通过")

    time.sleep(0.5)
    for srv in servers:
        srv.shutdown()

    sys.exit(0 if success1 and api_passed == len(api_results) else 1)


if __name__ == "__main__":
    main()
