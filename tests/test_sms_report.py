"""每日短信日报 (v3.46) 单测

覆盖:
  1. counter_daily 按日增量记账 (正向累计 / 非正忽略 / flush / 按工位过滤)
  2. 统一通道层 sms_providers 的 aliyun/tencent 签名与请求组装 (注入 request_func, 不出网)
  3. /api/v1/sms-report 规则 CRUD + 通道信息只读端点
  4. test-send 走 mock 通道全链路 (聚合 → 发送 → SmsSendLog 落库)
  5. daily_report_before_send hook returnable 白名单契约
  6. 中转服务脚本 + generic_http 通道对接中转的替代路径 (http_relay 适配器已退役)

通道选择/凭据走共享 sms_config.json (与 NG 短信通知同一份), 本文件不再测 SystemConfig 服务商配置。
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
# 2. 统一通道层: aliyun / tencent / mock Provider (SmsProvider 契约)
# ============================================================

def _cloud_config(**overrides):
    """带云通道凭据的 SmsServiceConfig (统一配置对象, 不出网)。"""
    from backend.services.sms_service import SmsServiceConfig
    defaults = dict(
        aliyun_access_key_id="AKID", aliyun_access_key_secret="AKSECRET",
        aliyun_sign_name="天军视觉", aliyun_template_code="SMS_123",
        tencent_secret_id="SID", tencent_secret_key="SKEY",
        tencent_sdk_app_id="1400000000", tencent_sign_name="天军视觉",
        tencent_template_id="1234567",
        retries=0,
    )
    defaults.update(overrides)
    return SmsServiceConfig(**defaults)


def _send_kwargs(params):
    return dict(message_id="m1", event_name="每日数据日报",
                raw_message="", context={"template_params": params})


class TestSmsProviders:

    def test_factory_registry(self):
        from backend.services.sms_providers import create_provider, PROVIDER_LABELS
        cfg = _cloud_config()
        assert set(PROVIDER_LABELS) == {
            "at_modem", "generic_http", "wxpusher", "aliyun", "tencent"}
        assert create_provider(cfg, provider_name="aliyun").name == "aliyun"
        assert create_provider(cfg, provider_name="mock").name == "mock"
        with pytest.raises(ValueError):
            create_provider(cfg, provider_name="nonexistent")
        with pytest.raises(ValueError):
            # at_modem 必须注入 modem_factory
            create_provider(cfg, provider_name="at_modem")

    def test_aliyun_request_shape(self):
        from backend.services.sms_providers import create_provider
        request = MagicMock(return_value=MagicMock(
            status_code=200, json=lambda: {"Code": "OK", "BizId": "1"}))
        provider = create_provider(_cloud_config(), provider_name="aliyun",
                                   request_func=request)

        batch = provider.send(["13800000000", "13900000000"], "",
                              **_send_kwargs({"total": 120, "rate": "97.2"}))

        assert batch.success is True
        assert [r.phone for r in batch.results] == ["13800000000", "13900000000"]
        params = request.call_args.kwargs["params"]
        assert params["Action"] == "SendSms"
        assert params["PhoneNumbers"] == "13800000000,13900000000"
        assert params["SignName"] == "天军视觉"
        assert params["TemplateCode"] == "SMS_123"
        assert "total" in params["TemplateParam"]
        assert params["Signature"]  # HMAC-SHA1 base64 已生成

    def test_aliyun_template_code_override_via_context(self):
        from backend.services.sms_providers import create_provider
        request = MagicMock(return_value=MagicMock(
            status_code=200, json=lambda: {"Code": "OK"}))
        provider = create_provider(_cloud_config(), provider_name="aliyun",
                                   request_func=request)
        provider.send(["13800000000"], "", message_id="m", event_name="e",
                      raw_message="", context={"template_params": {"a": "1"},
                                               "template_code": "SMS_999"})
        assert request.call_args.kwargs["params"]["TemplateCode"] == "SMS_999"

    def test_aliyun_ng_summary_fallback_params(self):
        """NG 汇总路径不传 template_params → 回退 time_range/ok_count/ng_count"""
        import json as _json
        from backend.services.sms_providers import create_provider
        request = MagicMock(return_value=MagicMock(
            status_code=200, json=lambda: {"Code": "OK"}))
        provider = create_provider(_cloud_config(), provider_name="aliyun",
                                   request_func=request)
        provider.send(["13800000000"], "msg", message_id="m", event_name="NG 汇总",
                      raw_message="msg",
                      context={"time_range": "08-04 08:00~20:00",
                               "ok_count": 90, "ng_count": 3})
        tpl = _json.loads(request.call_args.kwargs["params"]["TemplateParam"])
        assert tpl == {"time_range": "08-04 08:00~20:00",
                       "ok_count": "90", "ng_count": "3"}

    def test_aliyun_error_code_maps_to_failure(self):
        from backend.services.sms_providers import create_provider
        request = MagicMock(return_value=MagicMock(
            status_code=200,
            json=lambda: {"Code": "isv.SMS_SIGNATURE_ILLEGAL", "Message": "签名不合法"}))
        provider = create_provider(_cloud_config(), provider_name="aliyun",
                                   request_func=request)
        batch = provider.send(["13800000000"], "", **_send_kwargs({"a": 1}))
        assert batch.success is False
        assert "SMS_SIGNATURE_ILLEGAL" in batch.results[0].error_code
        assert request.call_count == 1  # 签名/参数错不重试

    def test_aliyun_missing_template_fails_fast(self):
        from backend.services.sms_providers import create_provider
        provider = create_provider(_cloud_config(aliyun_template_code=""),
                                   provider_name="aliyun",
                                   request_func=MagicMock())
        batch = provider.send(["13800000000"], "", **_send_kwargs({}))
        assert batch.success is False
        assert batch.results[0].error_code == "ALIYUN_NO_TEMPLATE"

    def test_tencent_request_shape(self):
        import json as _json
        from backend.services.sms_providers import create_provider
        request = MagicMock(return_value=MagicMock(
            status_code=200,
            json=lambda: {"Response": {"SendStatusSet": [
                {"Code": "Ok", "PhoneNumber": "+8613800000000"}]}}))
        provider = create_provider(_cloud_config(), provider_name="tencent",
                                   request_func=request)

        batch = provider.send(["13800000000"], "", **_send_kwargs({"total": 120}))

        assert batch.success is True
        payload = _json.loads(request.call_args.kwargs["data"].decode("utf-8"))
        assert payload["PhoneNumberSet"] == ["+8613800000000"]  # 自动加 +86
        assert payload["TemplateParamSet"] == ["120"]           # dict 顺序转位置参数
        headers = request.call_args.kwargs["headers"]
        assert headers["X-TC-Action"] == "SendSms"
        assert headers["Authorization"].startswith("TC3-HMAC-SHA256 Credential=SID/")

    def test_tencent_partial_failure_per_phone(self):
        from backend.services.sms_providers import create_provider
        request = MagicMock(return_value=MagicMock(
            status_code=200,
            json=lambda: {"Response": {"SendStatusSet": [
                {"Code": "Ok", "PhoneNumber": "+8613800000000"},
                {"Code": "LimitExceeded.PhoneNumberDailyLimit",
                 "PhoneNumber": "+8613900000000", "Message": "超日限"}]}}))
        provider = create_provider(_cloud_config(), provider_name="tencent",
                                   request_func=request)
        batch = provider.send(["13800000000", "13900000000"], "",
                              **_send_kwargs({"a": 1}))
        assert batch.success is False
        by_phone = {r.phone: r for r in batch.results}
        assert by_phone["13800000000"].success is True   # 逐号拆分成败
        assert by_phone["13900000000"].success is False
        assert "LimitExceeded" in by_phone["13900000000"].error_code

    def test_mock_provider_records_last_send(self):
        from backend.services.sms_providers import create_provider
        from backend.services.sms_providers.mock_provider import MockProvider
        provider = create_provider(_cloud_config(), provider_name="mock")
        batch = provider.send(["13800000000"], "正文",
                              **_send_kwargs({"total": "1"}))
        assert batch.success is True
        assert MockProvider.last_send["recipients"] == ["13800000000"]
        assert MockProvider.last_send["message"] == "正文"


# ============================================================
# 2b. 正文渲染 (内容式通道用)
# ============================================================

class TestRenderContent:

    def test_chinese_var_names_supported(self):
        from backend.services.sms_report import render_content
        assert render_content("今日${合格总数}件合格", {"合格总数": "88"}) == "今日88件合格"

    def test_no_template_falls_back_to_kv(self):
        from backend.services.sms_report import render_content
        assert render_content("", {"total": "5", "ok": "4"}) == "total:5 ok:4"
        assert render_content(None, {"a": "1"}) == "a:1"

    def test_unknown_var_renders_empty(self):
        from backend.services.sms_report import render_content
        assert render_content("值=${不存在}", {}) == "值="


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

    def test_generic_http_provider_to_relay_full_loop(self, relay):
        """统一层 generic_http 通道直接打到本地中转服务, 全链路无 mock。

        这是 http_relay 适配器退役后的替代路径: 共享配置选 generic_http,
        api_url 指向中转 /send, field_mapping 把 message/phone_numbers
        映射成中转服务的 content/phones。
        """
        from backend.services.sms_providers import create_provider
        from backend.services.sms_service import SmsServiceConfig
        url, mod = relay

        cfg = SmsServiceConfig(
            provider="generic_http",
            api_url=f"{url}/send",
            token="TESTTOKEN",
            field_mapping={"phone_numbers": "phones", "message": "content"},
            retries=0,
        )
        provider = create_provider(cfg)
        batch = provider.send(
            ["13800000000"], "总数120", message_id="m1",
            event_name="每日数据日报", raw_message="总数120", context={})
        assert batch.success is True
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

    def test_providers_endpoint_readonly_channel_info(self, client):
        """通道信息只读端点: 清单来自统一层, 生效通道来自共享 sms_config.json。"""
        r = client.get("/api/v1/sms-report/providers")
        assert r.status_code == 200
        data = r.json()
        names = {p["name"] for p in data["providers"]}
        assert names == {"at_modem", "generic_http", "wxpusher", "aliyun", "tencent"}
        assert data["active_provider"] in names
        assert isinstance(data["template_based"], bool)

    def test_rule_content_template_persists(self, client):
        r = client.post("/api/v1/sms-report/rules", json={
            "name": "正文模板规则", "cron_expression": "0 20 * * *",
            "metrics": ["stats.total_cycles"],
            "template_param_mapping": {"stats.total_cycles": "total"},
            "phone_numbers": ["13800000000"],
            "content_template": "【天军视觉】总数${total}",
        })
        assert r.status_code == 200, r.text
        rid = r.json()["id"]
        assert r.json()["content_template"] == "【天军视觉】总数${total}"
        r = client.put(f"/api/v1/sms-report/rules/{rid}",
                       json={"content_template": "总${total}"})
        assert r.json()["content_template"] == "总${total}"
        client.delete(f"/api/v1/sms-report/rules/{rid}")


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
            "content_template": "总数${total} 合格${ok}",
        })
        rid = r.json()["id"]

        r = client.post(f"/api/v1/sms-report/rules/{rid}/test-send",
                        params={"use_mock": "true"})
        assert r.status_code == 200, r.text
        summary = r.json()
        assert summary["status"] == "success", summary
        assert summary["sent"] == 1

        # 变量按映射命名; 计数器当日增量 >= 本测试挂的 8 (session 级共库, 其他用例可能也加过)
        from backend.services.sms_providers.mock_provider import MockProvider
        params = MockProvider.last_send["template_params"]
        assert "total" in params
        assert int(params["ok"]) >= 8
        # 正文模板在本端渲染 (内容式通道语义)
        assert MockProvider.last_send["message"].startswith("总数")
        assert f"合格{params['ok']}" in MockProvider.last_send["message"]

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
