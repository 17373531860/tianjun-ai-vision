"""
验证 v2.7.15 (C) 的 MJPEG generator 断开处理。

不需要真实摄像头。仿真思路：
  - 造一个最小化的 mgr 实例, 把 generate_mjpeg 的依赖(frame_lock, current_frame,
    _frame_seq, is_running, _encode_and_yield, frame_limit_enabled, target_stream_fps)
    全部打桩。
  - 场景 1: 正常迭代 3 帧后模拟客户端断开 (gen.close()), 期望:
      * GeneratorExit 被静默吞掉, 不冒到外面
      * finally 里 _mjpeg_active_streams 从 1 归 0
      * "连接关闭" 日志被打印
  - 场景 2: 连续开 3 个 generator, 再依次关闭, 期望活跃计数依次 1→2→3→2→1→0
  - 场景 3: yield 过程中 _encode_and_yield 抛异常, 期望 except Exception 分支接住,
    活跃计数清零, 不会泄露
"""
import os
import sys
import threading
import types
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.api import source as source_mod  # noqa: E402


def _make_fake_mgr():
    """构造只够 generate_mjpeg 跑起来的 mgr"""
    mgr = types.SimpleNamespace()
    mgr.frame_lock = threading.Lock()
    mgr.current_frame = b'fake_frame_bytes'
    mgr._frame_seq = 0
    mgr.is_running = True
    mgr.frame_limit_enabled = False
    mgr.target_stream_fps = 30
    mgr.channel_index = 0
    mgr._mjpeg_active_streams = 0

    # current_frame.copy() → 返回自身
    mgr.current_frame = mock.MagicMock()
    mgr.current_frame.copy.return_value = b'copied_frame'

    mgr._encode_and_yield = lambda frame: b'--frame\r\nContent-Type: image/jpeg\r\n\r\nJPEG\r\n'
    mgr.get_frame = lambda: None
    mgr._get_placeholder_frame = lambda: mgr.current_frame

    def tick():
        """让生成器感知到"新帧"到达"""
        mgr._frame_seq += 1

    mgr.tick = tick
    return mgr


# 绑定 generate_mjpeg 到仿真 mgr
_gen_impl = source_mod.VideoSourceManager.generate_mjpeg


def _bind(mgr):
    # channel_manager 依赖: import 时打桩
    fake_cm = types.SimpleNamespace(channel_count=1)
    with mock.patch.dict(sys.modules, {'backend.api.channel_manager': types.SimpleNamespace(channel_manager=fake_cm)}):
        return _gen_impl(mgr)


def test_disconnect_triggers_finally():
    mgr = _make_fake_mgr()
    gen = _bind(mgr)

    # 推 2 帧后模拟客户端断开
    for _ in range(2):
        mgr.tick()
        next(gen)

    assert mgr._mjpeg_active_streams == 1, f"预期活跃=1, 实际={mgr._mjpeg_active_streams}"
    # gen.close() 会向生成器 yield 点抛 GeneratorExit
    gen.close()

    assert mgr._mjpeg_active_streams == 0, f"断开后预期活跃=0, 实际={mgr._mjpeg_active_streams}"
    print("[PASS] test_disconnect_triggers_finally")


def test_multiple_streams_counter():
    mgr = _make_fake_mgr()
    gens = []
    for _ in range(3):
        g = _bind(mgr)
        # 推一帧让生成器走进去 (触发头部的计数 +1)
        mgr.tick()
        next(g)
        gens.append(g)

    assert mgr._mjpeg_active_streams == 3, f"预期活跃=3, 实际={mgr._mjpeg_active_streams}"

    for idx, g in enumerate(gens):
        g.close()
        expected = 3 - (idx + 1)
        assert mgr._mjpeg_active_streams == expected, \
            f"关闭第{idx+1}条后预期活跃={expected}, 实际={mgr._mjpeg_active_streams}"

    print("[PASS] test_multiple_streams_counter")


def test_encode_exception_cleans_up():
    mgr = _make_fake_mgr()

    def bad_encode(frame):
        raise RuntimeError("fake encode failure")

    mgr._encode_and_yield = bad_encode
    gen = _bind(mgr)
    mgr.tick()

    # 生成器遇到 RuntimeError 会走到 except Exception 分支, 然后 finally 清零
    # 对外表现为 StopIteration (生成器正常结束), 不应抛 RuntimeError
    try:
        next(gen)
        next(gen)  # 可能需要多推一次才触发
    except StopIteration:
        pass
    except RuntimeError as e:
        # 如果 except Exception 没接住, RuntimeError 会冒出来 — 这是 bug
        raise AssertionError(f"RuntimeError 未被 except Exception 接住: {e}")

    assert mgr._mjpeg_active_streams == 0, f"异常后预期活跃=0, 实际={mgr._mjpeg_active_streams}"
    print("[PASS] test_encode_exception_cleans_up")


if __name__ == '__main__':
    test_disconnect_triggers_finally()
    test_multiple_streams_counter()
    test_encode_exception_cleans_up()
    print("\n所有 MJPEG 断开测试通过")
