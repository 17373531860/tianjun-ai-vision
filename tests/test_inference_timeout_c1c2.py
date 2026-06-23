"""C1 推理超时重建池 + C2 紧急重置重载主模型 测试。

都走异常路径(推理卡死), 用 stub 验证:
  C1: 每次超时都 shutdown 推理池(下一帧拿新池), 不再等到 max 才重建。
  C2: 紧急 GPU 重置后会重载主模型(self.model_path)。
"""
import types


def _make_runner():
    from backend.api.source_detect_runners_mixin import DetectRunnersMixin
    obj = types.SimpleNamespace()
    obj._inference_timeout = 5
    obj._inference_timeout_count = 0
    obj._max_consecutive_timeouts = 3
    obj.shutdown_calls = 0
    obj.reset_calls = 0
    obj._shutdown_inference_executor = lambda: obj.__setattr__("shutdown_calls", obj.shutdown_calls + 1)
    obj._emergency_gpu_reset = lambda: obj.__setattr__("reset_calls", obj.reset_calls + 1)
    obj._handle_inference_timeout = types.MethodType(
        DetectRunnersMixin._handle_inference_timeout, obj)
    return obj


def test_c1_every_timeout_rebuilds_pool():
    obj = _make_runner()
    # 前两次(未到 max=3): 每次都重建池, 但不重置 GPU
    obj._handle_inference_timeout("main")
    obj._handle_inference_timeout("main")
    assert obj.shutdown_calls == 2, "C1: 每次超时都应重建推理池"
    assert obj.reset_calls == 0, "未到阈值不应重置 GPU"
    # 第三次到 max: 仍重建池 + 触发 GPU 重置 + 计数归零
    obj._handle_inference_timeout("main")
    assert obj.shutdown_calls == 3
    assert obj.reset_calls == 1
    assert obj._inference_timeout_count == 0


def test_c2_emergency_reset_reloads_main_model():
    from backend.api.source import VideoSourceManager  # noqa: F401  确保类可导入
    # 直接验证 _emergency_gpu_reset 重载逻辑: 用 stub 对象绑定该方法
    import types as _t
    from backend.api import source as src_mod

    obj = _t.SimpleNamespace()
    obj.model_path = "/fake/model.pt"
    obj._original_pt_path = None
    obj.loaded = []
    obj.load_model = lambda p, o=None: obj.loaded.append(p)
    obj._emergency_gpu_reset = _t.MethodType(
        src_mod.VideoSourceManager._emergency_gpu_reset, obj)

    obj._emergency_gpu_reset()
    assert obj.loaded == ["/fake/model.pt"], "C2: 紧急重置后应重载主模型"


def test_c2_no_model_path_no_reload():
    import types as _t
    from backend.api import source as src_mod
    obj = _t.SimpleNamespace()
    obj.model_path = None
    obj.loaded = []
    obj.load_model = lambda p, o=None: obj.loaded.append(p)
    obj._emergency_gpu_reset = _t.MethodType(
        src_mod.VideoSourceManager._emergency_gpu_reset, obj)
    obj._emergency_gpu_reset()
    assert obj.loaded == []  # 没有模型路径不重载, 不报错
