"""Step 7 单测: ChannelManager 全局 warmup_lock + load_model_for_channel 双签名.

测试范围
========
  - 全局 warmup_lock:
      * ch0 init 后 router.warmup_lock 是 cm._global_warmup_lock 同一引用
      * set_channel_count(2) 后 ch1.router.warmup_lock 也是同一引用
      * set_channel_count(4) 增到 4 通道, 全部共享同一锁
      * 反复降级再升级 (1→2→1→2) 锁不会被换掉
  - load_model_for_channel 双签名:
      * 老调用 (ch, path, device) → mgr.load_model 被调
      * 新调用 (ch, path, device, name='main', conf=0.3) → mgr.load_model_into_slot 被调
      * 新调用 (ch, path, device, name='tray', roi=..., schedule=...) → 透传 kwargs
      * 不存在 channel → False
  - load_shared_model 双签名同上
  - release_all_models_for_channel:
      * 转发到 mgr.release_all_models
      * 不存在 channel → False
      * 老 mgr 不支持 release_all_models 时尝试 _release_model 兜底
  - 跨通道并发 warmup 串行性 (用 stub model 验证锁串行)

测试策略
========
  - 用真 ChannelManager 实例 (隔离的全局, 测试间不串扰)
  - mgr.load_model / mgr.load_model_into_slot / mgr.release_all_models 用
    monkeypatch 替换为可观察 stub
  - 不真的 load weights / GPU warmup
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fresh_cm(monkeypatch):
    """构造独立的 ChannelManager 实例, 不污染模块级 channel_manager"""
    from backend.api.channel_manager import ChannelManager
    cm = ChannelManager()
    return cm


@pytest.fixture
def cm_with_2_channels(fresh_cm):
    fresh_cm.set_channel_count(2)
    return fresh_cm


# ============================================================
# 全局 warmup_lock 共享
# ============================================================
def test_ch0_init_后_router_warmup_lock_是全局锁(fresh_cm):
    ch0 = fresh_cm.channels[0]
    assert ch0._router.warmup_lock is fresh_cm._global_warmup_lock


def test_set_channel_count_2_新通道也共享全局锁(cm_with_2_channels):
    ch0 = cm_with_2_channels.channels[0]
    ch1 = cm_with_2_channels.channels[1]
    assert ch0._router.warmup_lock is cm_with_2_channels._global_warmup_lock
    assert ch1._router.warmup_lock is cm_with_2_channels._global_warmup_lock
    assert ch0._router.warmup_lock is ch1._router.warmup_lock


def test_set_channel_count_4_全部共享(fresh_cm):
    fresh_cm.set_channel_count(4)
    refs = [fresh_cm.channels[c]._router.warmup_lock for c in range(4)]
    # 全部是同一对象
    for ref in refs[1:]:
        assert ref is refs[0]
    assert refs[0] is fresh_cm._global_warmup_lock


def test_反复升降_全局锁_本身_不变(fresh_cm):
    original_lock = fresh_cm._global_warmup_lock
    fresh_cm.set_channel_count(2)
    fresh_cm.set_channel_count(1)
    fresh_cm.set_channel_count(2)

    # cm 上的全局锁 reference 不变
    assert fresh_cm._global_warmup_lock is original_lock
    # 现存通道仍然指向它
    for cid in fresh_cm.channels:
        assert fresh_cm.channels[cid]._router.warmup_lock is original_lock


def test_install_global_warmup_lock_幂等(fresh_cm):
    """重复调用不会换锁"""
    ch0 = fresh_cm.channels[0]
    lock_before = ch0._router.warmup_lock
    fresh_cm._install_global_warmup_lock(ch0)
    fresh_cm._install_global_warmup_lock(ch0)
    assert ch0._router.warmup_lock is lock_before


# ============================================================
# load_model_for_channel 双签名
# ============================================================
def test_load_model_for_channel_老签名_走_load_model(fresh_cm):
    mgr = fresh_cm.channels[0]
    mgr.load_model = MagicMock(return_value=True)
    mgr.load_model_into_slot = MagicMock(return_value=True)

    ok = fresh_cm.load_model_for_channel(0, '/tmp/main.pt', device='cpu')
    assert ok is True
    mgr.load_model.assert_called_once_with('/tmp/main.pt')
    mgr.load_model_into_slot.assert_not_called()
    assert mgr.device == 'cpu'


def test_load_model_for_channel_老签名_默认_device_auto(fresh_cm):
    """device 不传 → resolve 'auto' (cpu/cuda 由环境决定)"""
    mgr = fresh_cm.channels[0]
    mgr.load_model = MagicMock(return_value=True)

    fresh_cm.load_model_for_channel(0, '/tmp/m.pt')
    mgr.load_model.assert_called_once_with('/tmp/m.pt')
    # device resolve 后写入 mgr (cpu 或 cuda:0, 但不应是 'auto')
    assert mgr.device != 'auto'


def test_load_model_for_channel_新签名_main_走_slot(fresh_cm):
    mgr = fresh_cm.channels[0]
    mgr.load_model = MagicMock(return_value=True)
    mgr.load_model_into_slot = MagicMock(return_value=True)

    ok = fresh_cm.load_model_for_channel(
        0, '/tmp/main.pt', device='cpu',
        name='main', conf=0.3, iou=0.5, display_color='#10b981',
    )
    assert ok is True
    mgr.load_model.assert_not_called()
    mgr.load_model_into_slot.assert_called_once()
    call = mgr.load_model_into_slot.call_args
    assert call.kwargs['name'] == 'main'
    assert call.kwargs['model_path'] == '/tmp/main.pt'
    assert call.kwargs['device'] == 'cpu'
    assert call.kwargs['conf'] == 0.3
    assert call.kwargs['iou'] == 0.5
    assert call.kwargs['display_color'] == '#10b981'


def test_load_model_for_channel_新签名_副_slot_透传_所有_kwargs(fresh_cm):
    mgr = fresh_cm.channels[0]
    mgr.load_model_into_slot = MagicMock(return_value=True)

    ok = fresh_cm.load_model_for_channel(
        0, '/tmp/tray.pt', device='cuda:0',
        name='tray', conf=0.5, iou=0.6,
        roi=[[0.7, 0.7], [1.0, 1.0]],
        schedule={'type': 'every_n_frames', 'n': 5},
        class_filter=['tray_normal', 'tray_side'],
        priority=50,
        display_color='#f59e0b',
        use_half=True,
        original_pt_path='/tmp/tray_src.pt',
    )
    assert ok is True
    call_kwargs = mgr.load_model_into_slot.call_args.kwargs
    assert call_kwargs['name'] == 'tray'
    assert call_kwargs['conf'] == 0.5
    assert call_kwargs['roi'] == [[0.7, 0.7], [1.0, 1.0]]
    assert call_kwargs['schedule'] == {'type': 'every_n_frames', 'n': 5}
    assert call_kwargs['class_filter'] == ['tray_normal', 'tray_side']
    assert call_kwargs['priority'] == 50
    assert call_kwargs['use_half'] is True
    assert call_kwargs['original_pt_path'] == '/tmp/tray_src.pt'


def test_load_model_for_channel_不存在_channel_返回_False(fresh_cm):
    assert fresh_cm.load_model_for_channel(99, '/tmp/m.pt') is False
    assert fresh_cm.load_model_for_channel(99, '/tmp/m.pt', name='main') is False


def test_load_model_for_channel_load_失败_返回_False(fresh_cm):
    mgr = fresh_cm.channels[0]
    mgr.load_model = MagicMock(return_value=False)
    assert fresh_cm.load_model_for_channel(0, '/tmp/m.pt') is False


def test_load_model_for_channel_新签名_TypeError_返回_False(fresh_cm):
    """如果 mgr.load_model_into_slot 不接受某个 kwarg, 应记录并返回 False"""
    mgr = fresh_cm.channels[0]
    mgr.load_model_into_slot = MagicMock(side_effect=TypeError("unexpected kwarg"))
    ok = fresh_cm.load_model_for_channel(
        0, '/tmp/m.pt', name='main', not_a_real_kwarg='x'
    )
    assert ok is False


def test_load_model_for_channel_老_mgr_无_load_into_slot_回退到_load_model(fresh_cm):
    """模拟老 VSM 没有 load_model_into_slot 时, 应回退到 mgr.load_model.

    用 fake mgr (无 load_model_into_slot 属性) 替换 channels[0] 验证.
    """
    fake_mgr = MagicMock(spec=['load_model', 'device', 'channel_id', '_router'])
    fake_mgr.channel_id = 0
    fake_mgr.load_model = MagicMock(return_value=True)
    fake_mgr.device = 'cpu'
    # 保证 hasattr(fake_mgr, 'load_model_into_slot') 返回 False
    fresh_cm.channels[0] = fake_mgr

    ok = fresh_cm.load_model_for_channel(
        0, '/tmp/m.pt', device='cpu', name='main', conf=0.3
    )
    assert ok is True
    fake_mgr.load_model.assert_called_once_with('/tmp/m.pt')


# ============================================================
# load_shared_model 双签名
# ============================================================
def test_load_shared_model_老签名_所有通道_走_load_model(cm_with_2_channels):
    for cid in (0, 1):
        cm_with_2_channels.channels[cid].load_model = MagicMock(return_value=True)
        cm_with_2_channels.channels[cid].load_model_into_slot = MagicMock(
            return_value=True)

    ok = cm_with_2_channels.load_shared_model('/tmp/main.pt', device='cpu')
    assert ok is True
    for cid in (0, 1):
        cm_with_2_channels.channels[cid].load_model.assert_called_once_with(
            '/tmp/main.pt')
        cm_with_2_channels.channels[cid].load_model_into_slot.assert_not_called()


def test_load_shared_model_新签名_所有通道_走_slot(cm_with_2_channels):
    for cid in (0, 1):
        cm_with_2_channels.channels[cid].load_model_into_slot = MagicMock(
            return_value=True)

    ok = cm_with_2_channels.load_shared_model(
        '/tmp/tray.pt', device='cuda:0',
        name='tray', conf=0.5,
        roi=[[0.7, 0.7], [1.0, 1.0]],
        priority=50,
    )
    assert ok is True
    for cid in (0, 1):
        call = cm_with_2_channels.channels[cid].load_model_into_slot.call_args
        assert call.kwargs['name'] == 'tray'
        assert call.kwargs['conf'] == 0.5
        assert call.kwargs['priority'] == 50


def test_load_shared_model_部分_channel_失败_返回_False(cm_with_2_channels):
    cm_with_2_channels.channels[0].load_model = MagicMock(return_value=True)
    cm_with_2_channels.channels[1].load_model = MagicMock(return_value=False)

    ok = cm_with_2_channels.load_shared_model('/tmp/m.pt')
    assert ok is False


# ============================================================
# release_all_models_for_channel
# ============================================================
def test_release_all_models_for_channel_转发_mgr方法(fresh_cm):
    mgr = fresh_cm.channels[0]
    mgr.release_all_models = MagicMock()

    ok = fresh_cm.release_all_models_for_channel(0)
    assert ok is True
    mgr.release_all_models.assert_called_once()


def test_release_all_models_for_channel_不存在_channel(fresh_cm):
    assert fresh_cm.release_all_models_for_channel(99) is False


def test_release_all_models_for_channel_失败_返回_False(fresh_cm):
    mgr = fresh_cm.channels[0]
    mgr.release_all_models = MagicMock(side_effect=RuntimeError("boom"))
    assert fresh_cm.release_all_models_for_channel(0) is False


# ============================================================
# 跨通道并发 warmup 串行性 (集成测试)
# ============================================================
def test_全局锁_保证跨通道_warmup_串行(cm_with_2_channels):
    """模拟两个通道同时 warmup, 验证 _global_warmup_lock 串行化执行.

    用法: 把 mgr.load_model 替换为 stub, stub 内部:
      1. 用 router.warmup_lock 包住一段 sleep(50ms) 模拟 GPU warmup
      2. 记录进入/退出锁的时间戳
    然后两个通道并发调用, 验证两段执行没有时间重叠.
    """
    timeline = []  # [(channel_id, 'enter'|'exit', timestamp)]
    timeline_lock = threading.Lock()

    def make_stub(cid):
        def _stub_load(path):
            mgr = cm_with_2_channels.channels[cid]
            warmup_lock = mgr._router.warmup_lock
            with warmup_lock:
                with timeline_lock:
                    timeline.append((cid, 'enter', time.time()))
                time.sleep(0.05)  # 模拟 GPU warmup 50ms
                with timeline_lock:
                    timeline.append((cid, 'exit', time.time()))
            return True
        return _stub_load

    cm_with_2_channels.channels[0].load_model = make_stub(0)
    cm_with_2_channels.channels[1].load_model = make_stub(1)

    t0 = threading.Thread(target=lambda: cm_with_2_channels.load_model_for_channel(
        0, '/tmp/m0.pt', device='cpu'))
    t1 = threading.Thread(target=lambda: cm_with_2_channels.load_model_for_channel(
        1, '/tmp/m1.pt', device='cpu'))
    t0.start(); t1.start()
    t0.join(); t1.join()

    # timeline 必须是 enter,exit,enter,exit 的形式 (没有交叉)
    assert len(timeline) == 4
    # 取出每对 enter/exit
    pairs = []
    for evt in timeline:
        if evt[1] == 'enter':
            pairs.append({'cid': evt[0], 'enter': evt[2], 'exit': None})
        else:
            pairs[-1]['exit'] = evt[2]
            assert pairs[-1]['cid'] == evt[0], "enter/exit 必须配对同一 channel"
    assert len(pairs) == 2
    # 两段不重叠: 第一段的 exit <= 第二段的 enter
    pairs.sort(key=lambda p: p['enter'])
    assert pairs[0]['exit'] <= pairs[1]['enter'] + 1e-6, \
        "两个通道的 warmup 必须串行执行, 不允许时间重叠"


def test_单通道场景_全局锁_仍生效(fresh_cm):
    """单通道时锁存在但不会引起阻塞 (锁拿一次释放一次)"""
    mgr = fresh_cm.channels[0]
    enter_count = [0]

    def _stub_load(path):
        with mgr._router.warmup_lock:
            enter_count[0] += 1
        return True

    mgr.load_model = _stub_load
    fresh_cm.load_model_for_channel(0, '/tmp/m.pt')
    fresh_cm.load_model_for_channel(0, '/tmp/m.pt')
    assert enter_count[0] == 2


# ============================================================
# 边界 / 兼容
# ============================================================
def test_load_model_for_channel_设备解析_auto_to_real(fresh_cm):
    """auto 应解析成真实设备 (依 torch 环境: cuda:0 / mps / cpu)"""
    mgr = fresh_cm.channels[0]
    mgr.load_model = MagicMock(return_value=True)
    fresh_cm.load_model_for_channel(0, '/tmp/m.pt', device='auto')
    assert mgr.device in ('cpu', 'cuda:0', 'mps')


# ============================================================
# v3.50.2 channel-config 保存不得抹掉别段 key (project_id 绑定)
# 背景: Source 页保存输入源时项目下拉为空 → project_id=null + merge=False
# 整体覆盖 → 工位绑定被抹 → 全局激活失去"绑定其它项目的通道不动"保护
# → 跨工位串项目/串模型 (2026-08-14 捷昌 B 工位现场实录)
# ============================================================
def _call_save_channel_config(monkeypatch, body, old_cfg):
    from backend.api import channel_manager as cm_mod
    saved = {}
    monkeypatch.setattr(cm_mod.channel_manager, 'get_channel_sources',
                        lambda: {str(body.get('channel_id', 0)): dict(old_cfg)})
    monkeypatch.setattr(cm_mod.channel_manager, 'save_channel_source',
                        lambda ch, cfg, merge=False: saved.update(
                            {'ch': ch, 'cfg': cfg, 'merge': merge}))
    cm_mod.save_channel_config(dict(body))
    return saved


def test_channel_config_保存_project_id_为_null_继承旧绑定(monkeypatch):
    saved = _call_save_channel_config(
        monkeypatch,
        body={'channel_id': 0, 'source_type': 'camera', 'device_index': 1,
              'project_id': None},
        old_cfg={'source_type': 'camera', 'project_id': 7,
                 'was_detecting': True})
    assert saved['cfg']['project_id'] == 7, "旧绑定不得被 null 抹掉"
    assert saved['cfg']['was_detecting'] is True, "was_detecting 同样继承"
    assert saved['merge'] is False, "源配置段整体重写语义保留"


def test_channel_config_保存_显式指定_project_id_不被旧值覆盖(monkeypatch):
    saved = _call_save_channel_config(
        monkeypatch,
        body={'channel_id': 1, 'source_type': 'camera', 'project_id': 2},
        old_cfg={'project_id': 7})
    assert saved['cfg']['project_id'] == 2, "显式换绑要生效"


def test_channel_config_保存_旧配置无绑定_不凭空造(monkeypatch):
    saved = _call_save_channel_config(
        monkeypatch,
        body={'channel_id': 0, 'source_type': 'camera'},
        old_cfg={'source_type': 'video'})
    assert 'project_id' not in saved['cfg'] or saved['cfg']['project_id'] is None
