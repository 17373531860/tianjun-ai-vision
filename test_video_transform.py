"""测试 VideoSourceManager 的画面变换功能（旋转 + 镜像 + per_channel 持久化）"""
import os
import sys
import json
import tempfile
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.api.source import VideoSourceManager


def make_frame():
    """构造一个有方向性的测试帧：左上白、右上灰、左下黑、右下亮灰"""
    f = np.zeros((4, 4, 3), dtype=np.uint8)
    f[:2, :2] = (255, 255, 255)
    f[:2, 2:] = (128, 128, 128)
    f[2:, :2] = (0, 0, 0)
    f[2:, 2:] = (200, 200, 200)
    return f


def _new_mgr(channel_id=99):
    m = VideoSourceManager(channel_id=channel_id)
    m.video_rotation = 0
    m.video_flip_h = False
    m.video_flip_v = False
    return m


def test_rotation_0():
    mgr = _new_mgr()
    f = make_frame()
    out = mgr._apply_frame_transform(f.copy())
    assert np.array_equal(out, f), "0° 不应改变"


def test_rotation_90():
    mgr = _new_mgr()
    mgr.video_rotation = 90
    f = make_frame()
    out = mgr._apply_frame_transform(f.copy())
    assert out.shape == f.shape, "正方形旋转90°形状不变"
    # ROTATE_90_CLOCKWISE：原左上 → 右上，右下 → 左下
    assert tuple(out[0, -1]) == tuple(f[0, 0]), "顺时针90°：左上→右上"
    assert tuple(out[-1, 0]) == tuple(f[-1, -1]), "顺时针90°：右下→左下"


def test_rotation_180():
    mgr = _new_mgr()
    mgr.video_rotation = 180
    f = make_frame()
    out = mgr._apply_frame_transform(f.copy())
    assert tuple(out[-1, -1]) == tuple(f[0, 0]), "左上→右下"
    assert tuple(out[0, 0]) == tuple(f[-1, -1]), "右下→左上"


def test_rotation_270():
    mgr = _new_mgr()
    mgr.video_rotation = 270
    f = make_frame()
    out = mgr._apply_frame_transform(f.copy())
    # ROTATE_90_COUNTERCLOCKWISE：原左上 → 左下
    assert tuple(out[-1, 0]) == tuple(f[0, 0]), "270°：左上→左下"


def test_flip_h():
    mgr = _new_mgr()
    mgr.video_flip_h = True
    f = make_frame()
    out = mgr._apply_frame_transform(f.copy())
    assert tuple(out[0, 0]) == tuple(f[0, -1]), "左右翻转：左上↔右上"
    assert tuple(out[-1, -1]) == tuple(f[-1, 0]), "左右翻转：右下↔左下"


def test_flip_v():
    mgr = _new_mgr()
    mgr.video_flip_v = True
    f = make_frame()
    out = mgr._apply_frame_transform(f.copy())
    assert tuple(out[0, 0]) == tuple(f[-1, 0]), "上下翻转：左上↔左下"


def test_flip_hv():
    mgr = _new_mgr()
    mgr.video_flip_h = True
    mgr.video_flip_v = True
    f = make_frame()
    out = mgr._apply_frame_transform(f.copy())
    assert tuple(out[0, 0]) == tuple(f[-1, -1])


def test_rotation_plus_flip():
    """旋转 + 镜像组合：先转 90°，再左右翻转"""
    mgr = _new_mgr()
    mgr.video_rotation = 90
    mgr.video_flip_h = True
    f = make_frame()
    out = mgr._apply_frame_transform(f.copy())
    assert out.shape == f.shape


def test_none_frame():
    mgr = _new_mgr()
    mgr.video_rotation = 90
    assert mgr._apply_frame_transform(None) is None


def test_invalid_rotation_fallback():
    mgr = _new_mgr()
    mgr.video_rotation = 45
    f = make_frame()
    out = mgr._apply_frame_transform(f.copy())
    assert np.array_equal(out, f), "非法旋转角度应被视为 0°（不变）"


def test_per_channel_save_load():
    """保存两个通道各自独立的 transform，互不覆盖"""
    with tempfile.TemporaryDirectory() as td:
        cfg = os.path.join(td, 'device_config.json')

        mgr0 = VideoSourceManager(channel_id=0)
        mgr0.CONFIG_FILE = cfg
        mgr0.video_rotation = 90
        mgr0.video_flip_h = True
        mgr0.video_flip_v = False
        mgr0._save_device_config()

        mgr1 = VideoSourceManager(channel_id=1)
        mgr1.CONFIG_FILE = cfg
        mgr1.video_rotation = 180
        mgr1.video_flip_h = False
        mgr1.video_flip_v = True
        mgr1._save_device_config()

        # 通过原始文件校验 per_channel 两条都在
        with open(cfg, 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert '0' in data['per_channel'] and '1' in data['per_channel']
        assert data['per_channel']['0']['rotation'] == 90
        assert data['per_channel']['1']['rotation'] == 180

        # 重新构造 mgr 并加载，应该读到各自值
        mgr0b = VideoSourceManager(channel_id=0)
        mgr0b.CONFIG_FILE = cfg
        mgr0b._load_device_config()
        assert mgr0b.video_rotation == 90
        assert mgr0b.video_flip_h is True
        assert mgr0b.video_flip_v is False

        mgr1b = VideoSourceManager(channel_id=1)
        mgr1b.CONFIG_FILE = cfg
        mgr1b._load_device_config()
        assert mgr1b.video_rotation == 180
        assert mgr1b.video_flip_h is False
        assert mgr1b.video_flip_v is True


if __name__ == '__main__':
    tests = [
        test_rotation_0, test_rotation_90, test_rotation_180, test_rotation_270,
        test_flip_h, test_flip_v, test_flip_hv,
        test_rotation_plus_flip, test_none_frame, test_invalid_rotation_fallback,
        test_per_channel_save_load,
    ]
    fail = 0
    for t in tests:
        try:
            t()
            print(f'PASS  {t.__name__}')
        except Exception as e:
            fail += 1
            print(f'FAIL  {t.__name__}: {type(e).__name__}: {e}')
    print(f'\n{len(tests) - fail}/{len(tests)} passed')
    sys.exit(0 if fail == 0 else 1)
