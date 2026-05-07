"""Step 2 单测：VSM 构造后 InferenceRouter 与默认 main ModelInstance 已就位。

验证点
======
  - VSM 构造完成后:
      * self._router 是 InferenceRouter 实例
      * self.models 是 OrderedDict, 默认含 'main' 一项 (priority=100)
      * self.models is self._router.models (引用共享, 不是 copy)
  - 老字段 100% 保持现状 (零回归):
      * self.model is None
      * self.model_path is None
      * self.conf_threshold == 0.25 / iou_threshold == 0.45
      * self.fps_inference == 0
      * self.use_half is False
      * self._is_native_pytorch is True
  - 多通道独立性:
      * 不同通道的 VSM 各自有独立的 router 和 main 实例
      * 一个通道改 main 不影响另一通道的 main
"""
from __future__ import annotations

from collections import OrderedDict

import pytest


def _new_vsm(channel_id: int = 0):
    """构造一个独立的 VSM 实例（直接 import, 不走 ChannelManager）"""
    from backend.api.source import VideoSourceManager
    return VideoSourceManager(channel_id=channel_id)


# ============================================================
# router 与 main 实例就位
# ============================================================
def test_VSM_构造完成后_router_已实例化():
    from backend.api.source_inference_router import InferenceRouter
    mgr = _new_vsm()
    assert isinstance(mgr._router, InferenceRouter)


def test_VSM_默认含_main_ModelInstance():
    from backend.api.source_inference_router import ModelInstance
    mgr = _new_vsm()
    assert "main" in mgr.models
    main = mgr.models["main"]
    assert isinstance(main, ModelInstance)
    assert main.name == "main"
    assert main.priority == 100
    assert main.model is None
    assert main.model_path is None


def test_VSM_models_是_router_models_引用_非_copy():
    """h.models 直接共享 router.models — 改 router 立即可见, 反之亦然"""
    from backend.api.source_inference_router import ModelInstance
    mgr = _new_vsm()
    assert mgr.models is mgr._router.models

    mgr._router.add_model(ModelInstance(name="tray", priority=50))
    assert "tray" in mgr.models  # h.models 立刻能看到

    # 反向：通过 h._router.main() 找回 main
    assert mgr._router.main() is mgr.models["main"]


def test_VSM_models_保持_OrderedDict_类型():
    mgr = _new_vsm()
    assert isinstance(mgr.models, OrderedDict)


# ============================================================
# 老字段零回归
# ============================================================
def test_老字段_model_是_None():
    mgr = _new_vsm()
    assert mgr.model is None


def test_老字段_model_path_是_None():
    mgr = _new_vsm()
    assert mgr.model_path is None


def test_老字段_model_task_默认_detect():
    mgr = _new_vsm()
    assert mgr.model_task == "detect"


def test_老字段_conf_iou_默认值():
    mgr = _new_vsm()
    assert mgr.conf_threshold == 0.25
    assert mgr.iou_threshold == 0.45


def test_老字段_fps_inference_为_0():
    mgr = _new_vsm()
    assert mgr.fps_inference == 0


def test_老字段_use_half_默认_False():
    mgr = _new_vsm()
    # 设备配置文件可能影响 use_half, 但默认未配置时应是 False
    # 测试环境用临时 DATA_DIR, 没有 device_config.json
    assert mgr.use_half is False


def test_老字段_is_native_pytorch_默认_True():
    mgr = _new_vsm()
    assert mgr._is_native_pytorch is True


def test_老字段_model_imgsz_默认_640():
    mgr = _new_vsm()
    assert mgr._model_imgsz == 640


def test_老字段_inference_exec_仍存在():
    """老的 InferenceExecutor 组件保持可用 (Step 5 才迁移)"""
    from backend.api.source_inference_executor import InferenceExecutor
    mgr = _new_vsm()
    assert isinstance(mgr.inference_exec, InferenceExecutor)


# ============================================================
# 多通道独立性
# ============================================================
def test_不同通道的_VSM_各自独立_router():
    mgr0 = _new_vsm(channel_id=0)
    mgr1 = _new_vsm(channel_id=1)

    # router 实例各自独立
    assert mgr0._router is not mgr1._router
    assert mgr0.models is not mgr1.models

    # main 实例各自独立
    main0 = mgr0.models["main"]
    main1 = mgr1.models["main"]
    assert main0 is not main1


def test_通道_main_改动不互相影响():
    from backend.api.source_inference_router import ModelInstance
    mgr0 = _new_vsm(channel_id=0)
    mgr1 = _new_vsm(channel_id=1)

    mgr0.models["main"].conf = 0.55
    mgr0.models["main"].roi = [[0.1, 0.1], [0.9, 0.9], [0.5, 0.5]]
    mgr0._router.add_model(ModelInstance(name="tray"))

    # ch1 完全不受影响
    assert mgr1.models["main"].conf == 0.25
    assert mgr1.models["main"].roi is None
    assert "tray" not in mgr1.models
    assert mgr1._router.main().conf == 0.25


# ============================================================
# router 完整生命周期 (与 InferenceLoopMixin / InferenceExecutor 不冲突)
# ============================================================
def test_VSM_构造_不会_启动任何线程():
    """Step 2 只挂 router 实例, 不应启动推理线程或采集线程"""
    mgr = _new_vsm()
    assert mgr._inference_thread is None
    assert mgr._inference_running is False
    assert mgr.is_running is False
    assert mgr.is_detecting is False


def test_VSM_反复构造不泄露():
    """构造 5 个 VSM 实例, 每个都有独立 router (无共享状态)"""
    mgrs = [_new_vsm(channel_id=i) for i in range(5)]
    for i, m in enumerate(mgrs):
        assert m.channel_id == i
        assert "main" in m.models
        assert m._router.main().name == "main"
    routers = {id(m._router) for m in mgrs}
    assert len(routers) == 5  # 全部独立


# ============================================================
# Step 2 不动 ChannelManager (留给 Step 7) — 但确认 ChannelManager 走默认路径
# 也能正常给每个通道挂 router
# ============================================================
def test_ChannelManager_默认通道_也有_router():
    """通过 ChannelManager 创建的通道 mgr 应该也带 router"""
    from backend.api.channel_manager import ChannelManager
    cm = ChannelManager()
    cm.set_channel_count(2)
    try:
        ch0 = cm.get(0)
        ch1 = cm.get(1)
        assert "main" in ch0.models
        assert "main" in ch1.models
        assert ch0._router is not ch1._router
        assert ch0.models["main"] is not ch1.models["main"]
    finally:
        cm.set_channel_count(1)  # 还原, 避免污染单例
