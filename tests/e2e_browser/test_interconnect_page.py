# -*- coding: utf-8 -*-
"""v3.47 训练平台互连 E2E: 互连设置页 + 模型仓库来源/训练分析展示.

覆盖:
- /#/interconnect 页面三块渲染 (连接配置 / 采样规则 / 运行状态)
- UI 改采样配置 → 保存 → 后端 /interconnect/config 落库双向验证
- 训练平台推送的模型在模型仓库显示「训练平台」来源标 + 训练分析弹窗
  (指标格 + ECharts 曲线 canvas)

约定: 互连配置测试前备份、测试后原样恢复; 推送的演示模型测试后删除。
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile

import pytest
import requests

from .conftest import E2E_PREFIX

ICN = "/api/v1/interconnect"


@pytest.fixture
def interconnect_backup(api_url):
    """备份互连配置, 测试结束原样恢复 (含 token, GET 不脱敏)。"""
    before = requests.get(f"{api_url}{ICN}/config", timeout=5).json()
    yield before
    requests.put(f"{api_url}{ICN}/config", json=before, timeout=5)


def _build_yvmodel(name: str, analysis: dict | None = None) -> bytes:
    artifact = b"e2e-fake-onnx" * 64
    art_path = "artifacts/model.onnx"
    manifest = {
        "contractVersion": "1.0.0",
        "packageId": uuid.uuid4().hex,
        "name": name,
        "version": "1.0.0",
        "createdAtUtc": "2026-08-07T00:00:00+00:00",
        "task": {"type": "detection"},
        "classes": [{"id": 0, "key": "a", "displayName": "缺陷A"}],
        "artifacts": [{
            "id": "onnx-main", "path": art_path, "format": "onnx",
            "precision": "fp32", "role": "primary",
            "size": len(artifact), "sha256": hashlib.sha256(artifact).hexdigest(),
        }],
        "provenance": {"producer": "yolovision"},
        "extensions": {"x-analysis": analysis} if analysis else {},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr(art_path, artifact)
    return buf.getvalue()


@pytest.fixture
def pushed_model(api_url, interconnect_backup):
    """启用互连并推一个带训练分析的演示模型, 测试后删除。"""
    token = uuid.uuid4().hex
    requests.put(f"{api_url}{ICN}/config",
                 json={"enabled": True, "token": token}, timeout=5)
    name = f"{E2E_PREFIX}yv_{uuid.uuid4().hex[:8]}"
    analysis = {
        "schema": "1.0",
        "metrics": {"map50": 0.9, "map50_95": 0.7, "precision": 0.92, "recall": 0.88},
        "curves": {"epochs": [1, 2, 3], "train_loss": [1.5, 0.8, 0.4],
                   "val_loss": [1.6, 0.9, 0.5], "map50": [0.3, 0.7, 0.9]},
        "training": {"trigger": "auto_retrain", "epochs": 3},
    }
    resp = requests.post(
        f"{api_url}{ICN}/models/push",
        headers={"X-Interconnect-Token": token},
        files={"package": (f"{name}.yvmodel", _build_yvmodel(name, analysis),
                            "application/zip")},
        timeout=15,
    )
    assert resp.status_code == 200, resp.text
    model_id = resp.json()["model_id"]
    yield {"id": model_id, "name": name}
    requests.delete(f"{api_url}/api/v1/models/{model_id}", timeout=5)


def test_互连页三块渲染(page, base_url):
    js_errors = []
    page.on("pageerror", lambda exc: js_errors.append(str(exc)))
    page.goto(f"{base_url}/#/interconnect", wait_until="domcontentloaded",
              timeout=15000)
    page.wait_for_selector("text=训练平台互连 (YoloVision)", timeout=15000)
    assert page.locator("text=连接配置").count() > 0
    assert page.locator("text=现场帧采样回传").count() > 0
    assert page.locator("text=运行状态").count() > 0
    # v1.1: 模型拉取分发卡 + 设备身份 + 检出闪断采样项
    assert page.locator("text=模型分发 — 定时拉取").count() > 0
    assert page.locator("text=设备 ID:").count() > 0
    assert page.locator("text=检出闪断采样").count() > 0
    fatal = [e for e in js_errors if "ResizeObserver" not in e]
    assert not fatal, f"互连页 JS 异常: {fatal}"


def test_采样配置UI保存落库(page, base_url, api_url, interconnect_backup):
    page.goto(f"{base_url}/#/interconnect", wait_until="domcontentloaded",
              timeout=15000)
    page.wait_for_selector("text=现场帧采样回传", timeout=15000)
    page.wait_for_timeout(800)  # 等 loadConfig 回填表单

    # 把「未检出帧采样」拨到与当前后端值相反的状态
    before = requests.get(f"{api_url}{ICN}/config", timeout=5).json()
    want = not before["sampling"]["no_detection_enabled"]
    row = page.locator("div.flex.items-center", has_text="未检出帧采样").first
    row.locator("span.el-switch__core").first.click()
    page.locator("button:has-text('保存配置')").click()
    page.wait_for_selector("text=互连配置已保存", timeout=8000)

    after = requests.get(f"{api_url}{ICN}/config", timeout=5).json()
    assert after["sampling"]["no_detection_enabled"] is want


def test_模型仓库来源标与训练分析弹窗(page, base_url, pushed_model):
    page.goto(f"{base_url}/#/model", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector(f"text={pushed_model['name']}", timeout=15000)

    card = page.locator(".grid > div", has_text=pushed_model["name"]).first
    assert card.locator(".el-tag:has-text('训练平台')").count() > 0

    card.locator("button:has-text('训练分析')").click()
    page.wait_for_selector(".el-dialog:has-text('训练分析')", timeout=8000)
    page.wait_for_timeout(1200)  # 等 ECharts 渲染
    dlg = page.locator(".el-dialog", has_text="训练分析").first
    assert dlg.locator("text=mAP@50").count() > 0
    assert dlg.locator("canvas").count() > 0
