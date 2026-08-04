"""每日短信日报 (v3.46) 单测

覆盖:
  1. counter_daily 按日增量记账 (正向累计 / 非正忽略 / flush / 按工位过滤)
  2. sms_adapters 阿里云/腾讯云签名与请求组装 (mock requests, 不出网)
  3. /api/v1/sms-report 规则 CRUD + 服务商配置脱敏
  4. test-send 走 mock 适配器全链路 (聚合 → 发送 → SmsSendLog 落库)
  5. daily_report_before_send hook returnable 白名单契约
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import patch, MagicMock

import pytest


# ============================================================
# 1. counter_daily 记账
# ============================================================

class TestCounterDaily:

    def _clean(self):
        from backend.db.database import SessionLocal
        from backend.models.notify_models import CounterDailyStat
        from backend.services import counter_daily
        with counter_daily._lock:
            counter_daily._bucket.clear()
        db = SessionLocal()
        try:
            db.query(CounterDailyStat).delete()
            db.commit()
        finally:
            db.close()

    def test_positive_increment_accumulates(self):
        from backend.services import counter_daily
        self._clean()
        today = dt.date.today()

        counter_daily.record_increment(1, 0, "合格总数", 3)
        counter_daily.record_increment(1, 0, "合格总数", 2)
        counter_daily.flush_now()

        result = counter_daily.query_daily(today, project_id=1, channel_id=0)
        assert result.get("合格总数") == 5

    def test_non_positive_and_empty_ignored(self):
        from backend.services import counter_daily
        self._clean()
        today = dt.date.today()

        counter_daily.record_increment(1, 0, "计数A", 0)
        counter_daily.record_increment(1, 0, "计数A", -5)   # 清零/重置不扣减
        counter_daily.record_increment(1, 0, "", 3)          # 无名忽略
        counter_daily.record_increment(1, 0, "计数B", "abc")  # 非数值忽略
        counter_daily.flush_now()

        result = counter_daily.query_daily(today)
        assert "计数A" not in result
        assert "计数B" not in result

    def test_channel_filter_and_cross_channel_sum(self):
        from backend.services import counter_daily
        self._clean()
        today = dt.date.today()

        counter_daily.record_increment(1, 0, "产量", 10)
        counter_daily.record_increment(1, 1, "产量", 7)
        counter_daily.flush_now()

        assert counter_daily.query_daily(today, channel_id=0)["产量"] == 10
        assert counter_daily.query_daily(today, channel_id=1)["产量"] == 7
        # 跨工位求和 (汇总模式)
        assert counter_daily.query_daily(today)["产量"] == 17

    def test_flush_idempotent_after_drain(self):
        from backend.services import counter_daily
        self._clean()
        today = dt.date.today()

        counter_daily.record_increment(2, 0, "X", 4)
        counter_daily.flush_now()
        counter_daily.flush_now()  # 桶已空, 不应重复累计

        assert counter_daily.query_daily(today, project_id=2)["X"] == 4

    def test_record_for_host_extracts_context(self):
        from backend.services import counter_daily
        self._clean()
        today = dt.date.today()

        host = MagicMock()
        host.project_config = {"id": 9}
        host.channel_id = 2
        counter_daily.record_for_host(host, "焊点数", 6)
        counter_daily.flush_now()

        assert counter_daily.query_daily(today, project_id=9, channel_id=2)["焊点数"] == 6


# ============================================================
# 2. 适配器
# ============================================================

class TestSmsAdapters:

    ALIYUN_CFG = {
        "access_key_id": "AKID", "access_key_secret": "AKSECRET",
        "sign_name": "天军视觉", "template_code": "SMS_123",
    }
    TENCENT_CFG = {
        "access_key_id": "SID", "access_key_secret": "SKEY",
        "sign_name": "天军视觉", "template_code": "1234567",
        "sms_sdk_app_id": "1400000000",
    }

    def test_registry(self):
        from backend.services.sms_adapters import get_sms_adapter, list_providers
        assert set(list_providers()) >= {"aliyun", "tencent", "mock"}
        assert get_sms_adapter("aliyun").provider == "aliyun"
        with pytest.raises(ValueError):
            get_sms_adapter("nonexistent")

    def test_aliyun_request_shape(self):
        from backend.services.sms_adapters import get_sms_adapter
        adapter = get_sms_adapter("aliyun")

        with patch("backend.services.sms_adapters.aliyun_adapter.requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: {"Code": "OK", "BizId": "1"})
            result = adapter.send(["13800000000", "13900000000"],
                                  {"total": 120, "rate": "97.2"}, self.ALIYUN_CFG)

        assert result["success"] is True
        params = mock_get.call_args.kwargs["params"]
        assert params["Action"] == "SendSms"
        assert params["PhoneNumbers"] == "13800000000,13900000000"
        assert params["SignName"] == "天军视觉"
        assert params["TemplateCode"] == "SMS_123"
        assert "total" in params["TemplateParam"]
        assert params["Signature"]  # HMAC-SHA1 base64 已生成

    def test_aliyun_error_code_maps_to_failure(self):
        from backend.services.sms_adapters import get_sms_adapter
        adapter = get_sms_adapter("aliyun")
        with patch("backend.services.sms_adapters.aliyun_adapter.requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"Code": "isv.BUSINESS_LIMIT_CONTROL", "Message": "限流"})
            result = adapter.send(["13800000000"], {"a": 1}, self.ALIYUN_CFG)
        assert result["success"] is False
        assert "BUSINESS_LIMIT_CONTROL" in result["error"]

    def test_aliyun_missing_config(self):
        from backend.services.sms_adapters import get_sms_adapter
        adapter = get_sms_adapter("aliyun")
        result = adapter.send(["13800000000"], {}, {"sign_name": "x", "template_code": "y"})
        assert result["success"] is False
        assert "AccessKey" in result["error"]

    def test_tencent_request_shape(self):
        import json as _json
        from backend.services.sms_adapters import get_sms_adapter
        adapter = get_sms_adapter("tencent")

        with patch("backend.services.sms_adapters.tencent_adapter.requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"Response": {"SendStatusSet": [
                    {"Code": "Ok", "PhoneNumber": "+8613800000000"}]}})
            result = adapter.send(["13800000000"], {"total": 120}, self.TENCENT_CFG)

        assert result["success"] is True
        payload = _json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        assert payload["PhoneNumberSet"] == ["+8613800000000"]  # 自动加 +86
        assert payload["TemplateParamSet"] == ["120"]           # dict 顺序转位置参数
        headers = mock_post.call_args.kwargs["headers"]
        assert headers["X-TC-Action"] == "SendSms"
        assert headers["Authorization"].startswith("TC3-HMAC-SHA256 Credential=SID/")

    def test_tencent_partial_failure(self):
        from backend.services.sms_adapters import get_sms_adapter
        adapter = get_sms_adapter("tencent")
        with patch("backend.services.sms_adapters.tencent_adapter.requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"Response": {"SendStatusSet": [
                    {"Code": "Ok", "PhoneNumber": "+8613800000000"},
                    {"Code": "LimitExceeded.PhoneNumberDailyLimit",
                     "PhoneNumber": "+8613900000000", "Message": "超日限"}]}})
            result = adapter.send(["13800000000", "13900000000"], {"a": 1}, self.TENCENT_CFG)
        assert result["success"] is False
        assert "13900000000" in result["error"]

    def test_mock_adapter(self):
        from backend.services.sms_adapters import get_sms_adapter
        from backend.services.sms_adapters.mock_adapter import MockSmsAdapter
        adapter = get_sms_adapter("mock")
        result = adapter.send(["13800000000"], {"total": "1"}, {"template_code": "T"})
        assert result["success"] is True
        assert MockSmsAdapter.last_call["phone_numbers"] == ["13800000000"]


# ============================================================
# 2b. 自建 HTTP 中转适配器 (云审核过渡通道)
# ============================================================

class TestHttpRelayAdapter:

    CFG = {"relay_url": "https://relay.example.com/send", "relay_token": "TK",
           "content_template": "【天军视觉】${date}日报: 总数${total} 良率${rate}%"}

    def test_renders_content_and_posts(self):
        from backend.services.sms_adapters import get_sms_adapter
        adapter = get_sms_adapter("http_relay")
        with patch("backend.services.sms_adapters.http_relay_adapter.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200,
                                               json=lambda: {"success": True, "task_id": "t1"})
            result = adapter.send(["13800000000"],
                                  {"date": "08-03", "total": 120, "rate": "97.2"}, self.CFG)
        assert result["success"] is True
        payload = mock_post.call_args.kwargs["json"]
        assert payload["phones"] == ["13800000000"]
        assert payload["content"] == "【天军视觉】08-03日报: 总数120 良率97.2%"
        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer TK"

    def test_chinese_var_names_supported(self):
        from backend.services.sms_adapters.http_relay_adapter import HttpRelaySmsAdapter
        content = HttpRelaySmsAdapter._render_content(
            "今日${合格总数}件合格", {"合格总数": "88"})
        assert content == "今日88件合格"

    def test_no_template_falls_back_to_kv(self):
        from backend.services.sms_adapters.http_relay_adapter import HttpRelaySmsAdapter
        content = HttpRelaySmsAdapter._render_content("", {"total": "5", "ok": "4"})
        assert content == "total:5 ok:4"

    def test_missing_url_fails_fast(self):
        from backend.services.sms_adapters import get_sms_adapter
        result = get_sms_adapter("http_relay").send(["13800000000"], {"a": 1}, {})
        assert result["success"] is False
        assert "relay_url" in result["error"]

    def test_non_200_maps_to_failure(self):
        from backend.services.sms_adapters import get_sms_adapter
        adapter = get_sms_adapter("http_relay")
        with patch("backend.services.sms_adapters.http_relay_adapter.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=401,
                                               json=lambda: {"success": False, "error": "unauthorized"})
            result = adapter.send(["13800000000"], {"a": 1}, self.CFG)
        assert result["success"] is False
        assert "401" in result["error"]


class TestRelayServerScript:
    """scripts/sms_relay/relay_server.py 进程内冒烟 + 适配器真实回路 (不出网)"""

    @pytest.fixture()
    def relay(self):
        import importlib.util
        import os
        from http.server import ThreadingHTTPServer

        spec = importlib.util.spec_from_file_location(
            "relay_server",
            os.path.join(os.path.dirname(__file__), "..", "scripts", "sms_relay", "relay_server.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mod.TOKEN = "TESTTOKEN"
        mod._tasks.clear()
        mod._save = lambda: None  # 测试不落盘

        server = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
        import threading
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}", mod
        finally:
            server.shutdown()

    def test_send_pull_report_roundtrip(self, relay):
        import requests as rq
        url, mod = relay
        h = {"Authorization": "Bearer TESTTOKEN"}

        # 鉴权守门
        assert rq.post(f"{url}/send", json={}, timeout=5).status_code == 401

        r = rq.post(f"{url}/send", headers=h, timeout=5,
                    json={"phones": ["13800000000"], "content": "日报: 总数120"})
        assert r.json()["success"] is True
        task_id = r.json()["task_id"]

        # 手机端轮询取任务
        tasks = rq.get(f"{url}/pull?token=TESTTOKEN", timeout=5).json()["tasks"]
        assert len(tasks) == 1 and tasks[0]["id"] == task_id
        assert tasks[0]["content"] == "日报: 总数120"
        # 取走后不再重复下发
        assert rq.get(f"{url}/pull", headers=h, timeout=5).json()["tasks"] == []

        # 回报成功
        r = rq.post(f"{url}/report", headers=h, timeout=5,
                    json={"task_id": task_id, "success": True})
        assert r.json()["success"] is True
        listed = rq.get(f"{url}/tasks", headers=h, timeout=5).json()["tasks"]
        assert listed[-1]["status"] == "sent"

    def test_adapter_to_relay_full_loop(self, relay):
        """http_relay 适配器直接打到本地中转服务, 全链路无 mock"""
        from backend.services.sms_adapters import get_sms_adapter
        url, mod = relay
        result = get_sms_adapter("http_relay").send(
            ["13800000000"], {"total": "120"},
            {"relay_url": f"{url}/send", "relay_token": "TESTTOKEN",
             "content_template": "总数${total}"})
        assert result["success"] is True
        assert mod._tasks[-1]["content"] == "总数120"
        assert mod._tasks[-1]["phones"] == ["13800000000"]


# ============================================================
# 3. API: 规则 CRUD + 服务商配置
# ============================================================

class TestSmsReportAPI:

    def test_rule_crud_roundtrip(self, client):
        payload = {
            "name": "每日20点日报",
            "enabled": True,
            "cron_expression": "0 20 * * *",
            "data_window_type": "today",
            "group_by_channel": False,
            "metrics": ["stats.total_cycles", "stats.yield_rate", "counters_daily.合格总数"],
            "template_param_mapping": {"stats.total_cycles": "total"},
            "phone_numbers": ["13800000000"],
        }
        r = client.post("/api/v1/sms-report/rules", json=payload)
        assert r.status_code == 200, r.text
        rule = r.json()
        rid = rule["id"]
        assert rule["metrics"] == payload["metrics"]
        assert rule["phone_numbers"] == ["13800000000"]

        # GET 单条 + 列表
        assert client.get(f"/api/v1/sms-report/rules/{rid}").json()["name"] == "每日20点日报"
        rules = client.get("/api/v1/sms-report/rules").json()["rules"]
        assert any(x["id"] == rid for x in rules)

        # 更新
        r = client.put(f"/api/v1/sms-report/rules/{rid}",
                       json={"group_by_channel": True, "channel_ids": [0, 1]})
        assert r.json()["group_by_channel"] is True
        assert r.json()["channel_ids"] == [0, 1]

        # toggle
        assert client.post(f"/api/v1/sms-report/rules/{rid}/toggle").json()["enabled"] is False

        # 删除
        assert client.delete(f"/api/v1/sms-report/rules/{rid}").json()["deleted"] == rid
        assert client.get(f"/api/v1/sms-report/rules/{rid}").status_code == 404

    def test_invalid_cron_rejected(self, client):
        r = client.post("/api/v1/sms-report/rules", json={
            "name": "坏cron", "cron_expression": "not a cron",
        })
        assert r.status_code == 400

    def test_provider_config_masking(self, client):
        r = client.put("/api/v1/sms-report/provider-config", json={
            "provider": "aliyun",
            "config": {"access_key_id": "AK1", "access_key_secret": "RAWSECRET",
                       "sign_name": "签名", "template_code": "SMS_1"},
        })
        assert r.status_code == 200
        assert r.json()["config"]["access_key_secret"] == "******"  # 脱敏回显

        # 用脱敏占位再写 → 后端保留旧 SK 原文
        r = client.put("/api/v1/sms-report/provider-config", json={
            "provider": "aliyun",
            "config": {"access_key_id": "AK2", "access_key_secret": "******",
                       "sign_name": "签名", "template_code": "SMS_1"},
        })
        assert r.json()["config"]["access_key_id"] == "AK2"

        from backend.db.database import SessionLocal
        from backend.services.sms_report import get_provider_config
        db = SessionLocal()
        try:
            raw = get_provider_config(db)
            assert raw["config"]["access_key_secret"] == "RAWSECRET"
        finally:
            db.close()


# ============================================================
# 4. test-send 全链路 (mock 适配器)
# ============================================================

class TestSendPipeline:

    def test_test_send_with_mock_writes_log(self, client):
        from backend.services import counter_daily
        counter_daily.record_increment(0, 0, "合格总数", 8)

        r = client.post("/api/v1/sms-report/rules", json={
            "name": "试发链路", "enabled": True,
            "cron_expression": "0 20 * * *",
            "data_window_type": "today",
            "metrics": ["stats.total_cycles", "counters_daily.合格总数"],
            "template_param_mapping": {"stats.total_cycles": "total",
                                       "counters_daily.合格总数": "ok"},
            "phone_numbers": ["13800000000"],
        })
        rid = r.json()["id"]

        r = client.post(f"/api/v1/sms-report/rules/{rid}/test-send",
                        params={"use_mock": "true"})
        assert r.status_code == 200, r.text
        summary = r.json()
        assert summary["status"] == "success", summary
        assert summary["sent"] == 1

        # 变量按映射命名; 计数器当日增量 >= 本测试挂的 8 (session 级共库, 其他用例可能也加过)
        from backend.services.sms_adapters.mock_adapter import MockSmsAdapter
        params = MockSmsAdapter.last_call["template_params"]
        assert "total" in params
        assert int(params["ok"]) >= 8

        # 发送记录落库
        logs = client.get(f"/api/v1/sms-report/rules/{rid}/logs").json()["logs"]
        assert len(logs) == 1
        assert logs[0]["success"] is True
        assert logs[0]["provider"] == "mock"
        assert logs[0]["source_type"] == "manual_test"
        assert logs[0]["phone_numbers"] == ["13800000000"]

        client.delete(f"/api/v1/sms-report/rules/{rid}")

    def test_preview_no_send(self, client):
        r = client.post("/api/v1/sms-report/rules", json={
            "name": "预览", "enabled": False,
            "cron_expression": "0 8 * * *",
            "data_window_type": "today",
            "metrics": ["stats.total_cycles"],
            "phone_numbers": ["13800000000"],
        })
        rid = r.json()["id"]
        # SQLite 会复用被删规则的自增 id, 老规则的留痕日志可能挂在同 id 下 — 取增量对比
        logs_before = client.get(f"/api/v1/sms-report/rules/{rid}/logs").json()["total"]
        r = client.post(f"/api/v1/sms-report/rules/{rid}/preview")
        assert r.status_code == 200
        scopes = r.json()["scopes"]
        assert len(scopes) == 1
        assert "total_cycles" in scopes[0]["params"]
        # 预览不产生发送记录
        assert client.get(f"/api/v1/sms-report/rules/{rid}/logs").json()["total"] == logs_before
        client.delete(f"/api/v1/sms-report/rules/{rid}")

    def test_skipped_when_no_phones(self, client):
        r = client.post("/api/v1/sms-report/rules", json={
            "name": "无手机号", "enabled": True,
            "cron_expression": "0 8 * * *",
            "data_window_type": "today",
            "metrics": ["stats.total_cycles"],
        })
        rid = r.json()["id"]
        summary = client.post(f"/api/v1/sms-report/rules/{rid}/test-send",
                              params={"use_mock": "true"}).json()
        assert summary["status"] != "success"
        assert summary["sent"] == 0
        client.delete(f"/api/v1/sms-report/rules/{rid}")


# ============================================================
# 5. hook returnable 契约
# ============================================================

class TestDailyReportHookContract:

    def test_whitelist_registered(self):
        from backend.plugin_system.hook_dispatch import RETURNABLE_HOOK_FIELDS
        assert RETURNABLE_HOOK_FIELDS["daily_report_before_send"] == {
            "override_params", "override_phone_numbers", "skip_send",
        }

    def test_merge_filters_unknown_fields(self):
        from backend.plugin_system.hook_dispatch import _merge_handler_results
        merged = _merge_handler_results("daily_report_before_send", [
            {"override_params": {"total": "999"}, "unknown_field": 1},
            {"skip_send": True},
        ])
        assert merged == {"override_params": {"total": "999"}, "skip_send": True}

    def test_skip_send_respected_in_run(self, client):
        """handler 返回 skip_send=True → 该 scope 不发送"""
        r = client.post("/api/v1/sms-report/rules", json={
            "name": "hook跳过", "enabled": True,
            "cron_expression": "0 8 * * *",
            "data_window_type": "today",
            "metrics": ["stats.total_cycles"],
            "phone_numbers": ["13800000000"],
        })
        rid = r.json()["id"]

        with patch("backend.plugin_system.hook_dispatch.fire_plugin_hook",
                   return_value={"skip_send": True}):
            summary = client.post(f"/api/v1/sms-report/rules/{rid}/test-send",
                                  params={"use_mock": "true"}).json()
        assert summary["sent"] == 0
        assert summary["skipped"] >= 1
        client.delete(f"/api/v1/sms-report/rules/{rid}")
