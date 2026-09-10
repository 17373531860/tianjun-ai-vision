# -*- coding: utf-8 -*-
"""包契约 1.1 E2E: 模型仓库试用标识.

覆盖:
- 契约 1.1 包 (x-trial=true + postprocess.mode=end_to_end) 经 /models/push 入库
- 模型仓库卡片显示「试用」标 (悬浮提示预期管理文案)
- 详情弹窗「来源」显示试用模型后缀

预置 source='preset' 徽标不进本 CI 用例 (需后端带 TIANJUN_PRESET_MODELS_DIR
启动做 seeding, CI 跑道不具备), 由单测 test_preset_seeding_* 守逻辑、
可见 UAT 守显示。
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


def _build_trial_yvmodel(name: str, task_type: str = "detection") -> bytes:
    artifact = b"e2e-trial-onnx" * 64
    art_path = "artifacts/model.onnx"
    manifest = {
        "contractVersion": "1.1",
        "packageId": uuid.uuid4().hex,
        "name": name,
        "version": "0.1.0",
        "createdAtUtc": "2026-09-01T00:00:00+00:00",
        "task": {"type": task_type},
        "classes": [{"id": 0, "key": "fire", "displayName": "火焰"}],
        "artifacts": [{
            "id": "onnx-main", "path": art_path, "format": "onnx",
            "precision": "fp32", "role": "primary",
            "size": len(artifact), "sha256": hashlib.sha256(artifact).hexdigest(),
        }],
        "postprocess": {"mode": "end_to_end"},
        "provenance": {"producer": "yolovision"},
        "extensions": {"x-trial": True},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr(art_path, artifact)
    return buf.getvalue()


@pytest.fixture
def pushed_trial_model(api_url):
    """备份互连配置 → 启用并推一个契约 1.1 试用包 → 测试后删模型恢复配置。"""
    before = requests.get(f"{api_url}{ICN}/config", timeout=5).json()
    token = uuid.uuid4().hex
    requests.put(f"{api_url}{ICN}/config",
                 json={"enabled": True, "token": token}, timeout=5)
    name = f"{E2E_PREFIX}trial_{uuid.uuid4().hex[:8]}"
    resp = requests.post(
        f"{api_url}{ICN}/models/push",
        headers={"X-Interconnect-Token": token},
        files={"package": (f"{name}.yvmodel", _build_trial_yvmodel(name),
                           "application/zip")},
        timeout=15,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert any("end_to_end" in w for w in body["warnings"])
    model_id = body["model_id"]
    yield {"id": model_id, "name": name}
    requests.delete(f"{api_url}/api/v1/models/{model_id}", timeout=5)
    requests.put(f"{api_url}{ICN}/config", json=before, timeout=5)


def test_试用模型卡片与详情标识(page, base_url, pushed_trial_model):
    page.goto(f"{base_url}/#/model", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector(f"text={pushed_trial_model['name']}", timeout=15000)

    card = page.locator(".grid > div", has_text=pushed_trial_model["name"]).first
    assert card.locator(".el-tag:has-text('试用')").count() > 0
    assert card.locator(".el-tag:has-text('训练平台')").count() > 0

    card.locator("button:has-text('详情')").click()
    page.wait_for_selector(".el-dialog", timeout=8000)
    dlg = page.locator(".el-dialog").first
    assert dlg.locator("text=YoloVision 训练平台（试用模型）").count() > 0
    page.screenshot(path="/tmp/tj_model_trial_badge.png", full_page=True)


@pytest.fixture
def pushed_ocr_model(api_url):
    """契约 1.1 新任务类型 (ocr): 入库存档路径的 UI 侧夹具。"""
    before = requests.get(f"{api_url}{ICN}/config", timeout=5).json()
    token = uuid.uuid4().hex
    requests.put(f"{api_url}{ICN}/config",
                 json={"enabled": True, "token": token}, timeout=5)
    name = f"{E2E_PREFIX}ocr_{uuid.uuid4().hex[:8]}"
    resp = requests.post(
        f"{api_url}{ICN}/models/push",
        headers={"X-Interconnect-Token": token},
        files={"package": (f"{name}.yvmodel",
                           _build_trial_yvmodel(name, task_type="ocr"),
                           "application/zip")},
        timeout=15,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert any("暂不支持推理" in w for w in body["warnings"])
    model_id = body["model_id"]
    yield {"id": model_id, "name": name}
    requests.delete(f"{api_url}/api/v1/models/{model_id}", timeout=5)
    requests.put(f"{api_url}{ICN}/config", json=before, timeout=5)


def test_运行时未支持任务类型显示存档标(page, base_url, pushed_ocr_model):
    """ocr 包照收入库, 模型卡片带「存档」标提示暂不支持推理。"""
    page.goto(f"{base_url}/#/model", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector(f"text={pushed_ocr_model['name']}", timeout=15000)
    card = page.locator(".grid > div", has_text=pushed_ocr_model["name"]).first
    assert card.locator(".el-tag:has-text('存档')").count() > 0
    page.screenshot(path="/tmp/tj_model_ocr_archived.png", full_page=True)
