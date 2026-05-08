"""Step 8 端到端集成测试: 多模型完整数据流.

覆盖场景
========
  完整覆盖 8 步 feat/multi-model-roi-link 改造在 HTTP 层面的数据流:

  1. 创建项目 (无副模型, 老链路) → DB 落库 → 行为完全等价
  2. 项目带 pipeline_config.models[] (含副 slot) → DB 落库 → set_project_config
     → mi 配置就绪 (model 仍 None)
  3. POST /detection/start (老 payload) → mgr.load_model 老路径
  4. POST /detection/start (新 payload models[]) → release_all + load_into_slot
  5. GET /detection/results 透出 models[] 数组 (带 fps/latency/display_color)
  6. POST /gpu/set 多模型遍历重载

测试策略
========
  - 用 TestClient 走 FastAPI 真路由
  - mgr.load_model / mgr.load_model_into_slot 用 monkeypatch 替换为 stub
    (避免真的 load YOLO weights / GPU 资源)
  - 验证 HTTP 状态码 + response body + 后端 mgr/router 状态
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_load_methods(monkeypatch):
    """把 channel_manager 单例上的所有 channel 的 load_model/load_into_slot
    替换为 stub, 返回调用记录字典.
    """
    from backend.api.channel_manager import channel_manager

    calls = {
        'load_model': [],
        'load_model_into_slot': [],
        'release_all_models': 0,
        'release_all_models_for_channel': 0,
    }

    for cid, mgr in channel_manager.channels.items():
        def _make_load(c):
            def _load(path):
                calls['load_model'].append({'channel': c, 'path': path})
                # 模拟成功加载: mark mgr.model 非 None
                mgr_local = channel_manager.channels[c]
                mgr_local.model = MagicMock()
                mgr_local.model_path = path
                if hasattr(mgr_local, '_router'):
                    main = mgr_local._router.get('main')
                    if main is not None:
                        main.model = mgr_local.model
                        main.model_path = path
                return True
            return _load

        def _make_slot(c):
            def _slot(name, model_path, **kwargs):
                calls['load_model_into_slot'].append({
                    'channel': c, 'name': name, 'model_path': model_path, **kwargs
                })
                mgr_local = channel_manager.channels[c]
                mi = mgr_local._router.get(name)
                if mi is None:
                    from backend.api.source_inference_router import ModelInstance
                    mi = ModelInstance(name=name)
                    mgr_local._router.add_model(mi)
                mi.model = MagicMock()
                mi.model_path = model_path
                # 把 kwargs 写入 mi 状态 (模拟真 load_model_into_slot 的行为)
                if 'conf' in kwargs and kwargs['conf'] is not None:
                    mi.conf = kwargs['conf']
                if 'iou' in kwargs and kwargs['iou'] is not None:
                    mi.iou = kwargs['iou']
                if 'roi' in kwargs:
                    mi.roi = kwargs['roi']
                if 'schedule' in kwargs and kwargs['schedule']:
                    sch = kwargs['schedule']
                    if isinstance(sch, dict):
                        from backend.api.source_inference_router import Schedule
                        mi.schedule = Schedule(
                            type=sch.get('type', 'every_frame'),
                            n=int(sch.get('n', 1)),
                            events=list(sch.get('events') or []),
                        )
                if 'class_filter' in kwargs and kwargs['class_filter']:
                    mi.class_filter = set(kwargs['class_filter'])
                if 'priority' in kwargs and kwargs['priority'] is not None:
                    mi.priority = int(kwargs['priority'])
                if 'display_color' in kwargs and kwargs['display_color']:
                    mi.display_color = kwargs['display_color']
                if 'use_half' in kwargs and kwargs['use_half'] is not None:
                    mi.use_half = bool(kwargs['use_half'])
                if name == 'main':
                    mgr_local.model = mi.model
                    mgr_local.model_path = model_path
                return True
            return _slot

        def _make_release_all(c):
            def _release():
                calls['release_all_models'] += 1
                mgr_local = channel_manager.channels[c]
                for mi in mgr_local._router.models.values():
                    mi.model = None
                mgr_local.model = None
            return _release

        mgr.load_model = _make_load(cid)
        mgr.load_model_into_slot = _make_slot(cid)
        mgr.release_all_models = _make_release_all(cid)

    yield calls

    # 清理: 测试间释放模型引用
    for cid, mgr in channel_manager.channels.items():
        try:
            for mi in mgr._router.models.values():
                mi.model = None
            mgr.model = None
        except Exception:
            pass


@pytest.fixture
def stub_session(monkeypatch):
    """阻止 mgr.start_detection / start_session 真启动线程"""
    from backend.api.channel_manager import channel_manager
    for mgr in channel_manager.channels.values():
        mgr.start_detection = MagicMock()
        mgr.start_session = MagicMock(return_value=None)
        mgr.project_config = None
    yield


# ============================================================
# 场景 1: 老 payload (单模型) 行为完全等价
# ============================================================
def test_e2e_单模型_老payload_完全兼容(client, mock_load_methods, stub_session):
    resp = client.post(
        "/api/v1/source/detection/start?channel=0",
        json={"model_path": "/tmp/main.pt", "conf": 0.3, "iou": 0.5}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "success"

    # 老路径: 走 channel_manager.load_model_for_channel → mgr.load_model
    assert len(mock_load_methods['load_model']) == 1
    assert mock_load_methods['load_model'][0]['channel'] == 0
    assert mock_load_methods['load_model'][0]['path'] == '/tmp/main.pt'
    # 不应触发 release_all_models / load_model_into_slot
    assert mock_load_methods['release_all_models'] == 0
    assert len(mock_load_methods['load_model_into_slot']) == 0


def test_e2e_单模型_results_仍含_main_slot_快照(client):
    """单模型场景, /detection/results.models[] 仍至少有 main 一项"""
    resp = client.get("/api/v1/source/detection/results?channel=0")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert isinstance(data["models"], list)
    main_entry = next((m for m in data["models"] if m["name"] == "main"), None)
    assert main_entry is not None


# ============================================================
# 场景 2: 多模型 payload 走 slot 路径
# ============================================================
def test_e2e_多模型_payload_release_then_load_into_slot(
    client, mock_load_methods, stub_session
):
    payload = {
        "models": [
            {"name": "main", "model_path": "/tmp/main.pt",
             "conf": 0.3, "iou": 0.5, "display_color": "#10b981"},
            {"name": "tray", "model_path": "/tmp/tray.pt",
             "conf": 0.5, "iou": 0.5,
             "roi": [[0.7, 0.7], [1.0, 0.7], [1.0, 1.0], [0.7, 1.0]],
             "schedule": {"type": "every_n_frames", "n": 5},
             "class_filter": ["tray_normal", "tray_side"],
             "priority": 50, "display_color": "#f59e0b"},
        ]
    }
    resp = client.post("/api/v1/source/detection/start?channel=0", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success"

    # 多模型路径: 1 次 release_all_models + 2 次 load_model_into_slot
    assert mock_load_methods['release_all_models'] == 1
    assert len(mock_load_methods['load_model_into_slot']) == 2

    main_call = mock_load_methods['load_model_into_slot'][0]
    assert main_call['name'] == 'main'
    assert main_call['model_path'] == '/tmp/main.pt'
    assert main_call['conf'] == 0.3
    assert main_call['display_color'] == '#10b981'

    tray_call = mock_load_methods['load_model_into_slot'][1]
    assert tray_call['name'] == 'tray'
    assert tray_call['model_path'] == '/tmp/tray.pt'
    assert tray_call['conf'] == 0.5
    assert tray_call['roi'] == [[0.7, 0.7], [1.0, 0.7], [1.0, 1.0], [0.7, 1.0]]
    assert tray_call['schedule'] == {"type": "every_n_frames", "n": 5}
    assert tray_call['class_filter'] == ["tray_normal", "tray_side"]
    assert tray_call['priority'] == 50

    # 老路径不应触发
    assert len(mock_load_methods['load_model']) == 0


def test_e2e_多模型_results_含_全部_slot_快照(
    client, mock_load_methods, stub_session
):
    """多模型加载后, /detection/results.models[] 应包含所有 slot"""
    payload = {
        "models": [
            {"name": "main", "model_path": "/tmp/main.pt",
             "conf": 0.3, "display_color": "#10b981"},
            {"name": "tray", "model_path": "/tmp/tray.pt",
             "conf": 0.5, "display_color": "#f59e0b",
             "schedule": {"type": "every_n_frames", "n": 5}},
        ]
    }
    client.post("/api/v1/source/detection/start?channel=0", json=payload)

    resp = client.get("/api/v1/source/detection/results?channel=0")
    assert resp.status_code == 200
    data = resp.json()
    names = {m["name"] for m in data["models"]}
    assert "main" in names
    assert "tray" in names

    tray_entry = next(m for m in data["models"] if m["name"] == "tray")
    assert tray_entry["conf"] == 0.5
    assert tray_entry["display_color"] == "#f59e0b"
    assert tray_entry["model_loaded"] is True  # mock 设了 mi.model
    assert tray_entry["schedule"]["type"] == "every_n_frames"
    assert tray_entry["schedule"]["n"] == 5


# ============================================================
# 场景 3: set-project-config 解析 pipeline_config.models[]
# ============================================================
def test_e2e_set_project_config_解析_pipeline_models(
    client, mock_load_methods, stub_session
):
    """set-project-config 时不应触发 load_model (Step 6 契约: apply 不 load)"""
    project_config = {
        "project_id": 999,
        "name": "e2e_multi_test",
        "task_type": "detection",
        "logic_mode": "detection",
        "steps_config": [],
        "events_config": [],
        "counters_config": [],
        "data_config": {},
        "pipeline_config": {
            "settlement_mode": "first_step",
            "models": [
                {"name": "main", "display_color": "#10b981"},
                {"name": "tray", "conf": 0.5,
                 "schedule": {"type": "every_n_frames", "n": 5},
                 "priority": 50, "display_color": "#f59e0b"},
            ]
        }
    }
    resp = client.post(
        "/api/v1/source/detection/set-project?channel=0",
        json=project_config
    )
    assert resp.status_code == 200, resp.text

    # apply 阶段不应触发 load_model 或 load_into_slot
    assert mock_load_methods['release_all_models'] == 0
    assert len(mock_load_methods['load_model']) == 0
    assert len(mock_load_methods['load_model_into_slot']) == 0

    # 但 router 中 tray slot 应该存在 (空壳)
    from backend.api.channel_manager import channel_manager
    mgr = channel_manager.get(0)
    assert 'tray' in mgr._router.models
    tray_mi = mgr._router.get('tray')
    assert tray_mi.conf == 0.5
    assert tray_mi.priority == 50
    assert tray_mi.display_color == '#f59e0b'
    assert tray_mi.schedule.n == 5
    assert tray_mi.model is None, "apply 不应触发 load_model"


def test_e2e_set_project_config_切换项目_释放旧slot(
    client, mock_load_methods, stub_session
):
    """切换项目 (新配置不含旧 slot) 应释放旧副 slot, main 永保留"""
    # 第一次: 注入 tray + pose 副 slot
    client.post("/api/v1/source/detection/set-project?channel=0", json={
        "project_id": 1, "name": "p1",
        "task_type": "detection", "logic_mode": "detection",
        "steps_config": [], "events_config": [], "counters_config": [],
        "data_config": {},
        "pipeline_config": {
            "models": [
                {"name": "main"},
                {"name": "tray", "conf": 0.5},
                {"name": "pose", "conf": 0.4},
            ]
        }
    })
    from backend.api.channel_manager import channel_manager
    mgr = channel_manager.get(0)
    assert {'main', 'tray', 'pose'}.issubset(set(mgr._router.models.keys()))

    # 第二次: 切到只含 tray 的配置
    client.post("/api/v1/source/detection/set-project?channel=0", json={
        "project_id": 2, "name": "p2",
        "task_type": "detection", "logic_mode": "detection",
        "steps_config": [], "events_config": [], "counters_config": [],
        "data_config": {},
        "pipeline_config": {
            "models": [
                {"name": "main"},
                {"name": "tray", "conf": 0.6},
            ]
        }
    })
    # pose 应被释放, main + tray 保留
    assert 'pose' not in mgr._router.models
    assert 'main' in mgr._router.models
    assert 'tray' in mgr._router.models
    assert mgr._router.get('tray').conf == 0.6  # 新值


# ============================================================
# 场景 4: /gpu/set 多模型遍历重载
# ============================================================
def test_e2e_gpu_set_多模型_遍历重载(client, mock_load_methods, stub_session):
    """先加载多模型, 再 /gpu/set 切设备 → 应释放 + 重新 load_into_slot 所有"""
    # 先加载 main + tray
    client.post("/api/v1/source/detection/start?channel=0", json={
        "models": [
            {"name": "main", "model_path": "/tmp/main.pt"},
            {"name": "tray", "model_path": "/tmp/tray.pt", "conf": 0.5,
             "display_color": "#f59e0b"},
        ]
    })
    initial_releases = mock_load_methods['release_all_models']
    initial_slot_loads = len(mock_load_methods['load_model_into_slot'])

    # /gpu/set 切设备
    resp = client.post("/api/v1/source/gpu/set", json={"device": "cpu"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "success"
    assert "reloaded_models" in data
    assert set(data["reloaded_models"]) == {"main", "tray"}

    # release_all_models 应再调用 1 次 + load_into_slot 再调 2 次
    assert mock_load_methods['release_all_models'] == initial_releases + 1
    assert len(mock_load_methods['load_model_into_slot']) == initial_slot_loads + 2

    # 检查重载时 device='cpu' 透传
    last_two = mock_load_methods['load_model_into_slot'][-2:]
    assert all(c['device'] == 'cpu' for c in last_two)


def test_e2e_gpu_set_单模型_走老_load_model(
    client, mock_load_methods, stub_session
):
    """单模型场景 /gpu/set 走老 load_model 路径"""
    # 先用老 payload 加载单模型
    client.post("/api/v1/source/detection/start?channel=0", json={
        "model_path": "/tmp/main.pt", "conf": 0.3, "iou": 0.5
    })
    initial_loads = len(mock_load_methods['load_model'])
    initial_slot_loads = len(mock_load_methods['load_model_into_slot'])

    resp = client.post("/api/v1/source/gpu/set", json={"device": "cpu"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    # 应再调 load_model 一次, 不调 load_into_slot
    assert len(mock_load_methods['load_model']) == initial_loads + 1
    assert len(mock_load_methods['load_model_into_slot']) == initial_slot_loads


def test_e2e_gpu_set_无模型_仅写配置(client, mock_load_methods, stub_session):
    """没有模型加载时, /gpu/set 仅写 device 配置"""
    # 确保 main slot 的 model is None
    from backend.api.channel_manager import channel_manager
    mgr = channel_manager.get(0)
    for mi in mgr._router.models.values():
        mi.model = None
    mgr.model = None

    initial_releases = mock_load_methods['release_all_models']

    resp = client.post("/api/v1/source/gpu/set", json={"device": "cpu"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "下次加载模型时生效" in data["message"]
    assert mock_load_methods['release_all_models'] == initial_releases


# ============================================================
# 场景 5: 多模型加载部分失败 → 500
# ============================================================
# ============================================================
# 场景 6: 完整 Project 配置往返 (DB ↔ pipeline_config.models[])
# ============================================================
def test_e2e_project_配置往返_pipeline_models_持久化(client, clean_db):
    """创建 project 时带 pipeline_config.models, 查询 + 删除往返验证字段保留"""
    payload = {
        "name": "e2e_multi_model_project",
        "task_type": "detection",
        "logic_mode": "detection",
        "steps_config": [],
        "events_config": [],
        "counters_config": [],
        "alarm_config": {},
        "detection_config": {},
        "data_config": {},
        "pipeline_config": {
            "settlement_mode": "first_step",
            "models": [
                {"name": "main", "model_id": 1, "model_name": "main_model",
                 "display_color": "#10b981", "priority": 100},
                {"name": "tray", "model_id": 2, "model_name": "tray_model",
                 "conf": 0.5, "iou": 0.5,
                 "roi": [[0.7, 0.7], [1.0, 0.7], [1.0, 1.0], [0.7, 1.0]],
                 "schedule": {"type": "every_n_frames", "n": 5, "events": []},
                 "class_filter": ["tray_normal", "tray_side"],
                 "priority": 50, "display_color": "#f59e0b",
                 "use_half": False},
            ],
        },
    }
    resp = client.post("/api/v1/projects", json=payload)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    project_id = data["id"]

    try:
        # 查询: pipeline_config.models 应原样返回
        resp2 = client.get(f"/api/v1/projects/{project_id}")
        assert resp2.status_code == 200
        got = resp2.json()
        models_back = got["pipeline_config"]["models"]
        assert len(models_back) == 2
        names = {m["name"] for m in models_back}
        assert names == {"main", "tray"}

        tray = next(m for m in models_back if m["name"] == "tray")
        assert tray["conf"] == 0.5
        assert tray["roi"] == [[0.7, 0.7], [1.0, 0.7], [1.0, 1.0], [0.7, 1.0]]
        assert tray["schedule"]["n"] == 5
        assert tray["class_filter"] == ["tray_normal", "tray_side"]
        assert tray["display_color"] == "#f59e0b"
    finally:
        client.delete(f"/api/v1/projects/{project_id}")


def test_e2e_多模型_部分失败_返回500(client, monkeypatch, stub_session):
    """某个 spec load_model_into_slot 返回 False → HTTPException 500"""
    from backend.api.channel_manager import channel_manager

    mgr = channel_manager.get(0)

    def _slot_partial(name, model_path, **kwargs):
        if name == 'tray':
            return False  # tray 失败
        return True

    mgr.load_model_into_slot = _slot_partial
    mgr.release_all_models = MagicMock()
    mgr.start_detection = MagicMock()
    mgr.start_session = MagicMock(return_value=None)

    resp = client.post("/api/v1/source/detection/start?channel=0", json={
        "models": [
            {"name": "main", "model_path": "/tmp/main.pt"},
            {"name": "tray", "model_path": "/tmp/tray.pt"},
        ]
    })
    assert resp.status_code == 500
    assert 'tray' in resp.json()["detail"]
