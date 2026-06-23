"""C8 NVR 解码回调 stop 后竞态保护测试。

stop_preview 置死后, 仍在 native 线程上飞的 _on_real_data / _on_decode 回调
必须提前返回, 不再碰已释放的 play port / 不再写 frame。
"""
import threading


def _bare_session():
    """绕过 __init__(会加载 DLL), 只装回调需要的最小字段。"""
    from backend.hcnetsdk.wrapper import HCNetSession
    s = HCNetSession.__new__(HCNetSession)
    s._alive = False
    s._frame = None
    s._frame_lock = threading.Lock()
    s._frame_event = threading.Event()
    s._play_port = None
    # 若回调没提前返回会去调用这些 DLL 方法 → 触发异常, 反证未守门
    class _Boom:
        def __getattr__(self, name):
            raise AssertionError(f"stop 后回调不应调用 DLL: {name}")
    s._play_dll = _Boom()
    return s


def test_c8_real_data_callback_returns_after_stop():
    s = _bare_session()
    s._alive = False  # 模拟 stop_preview 已置死
    # 不应抛 AssertionError(即没碰 _play_dll), 应静默返回
    s._on_real_data(0, 1, None, 0, None)


def test_c8_decode_callback_returns_after_stop():
    s = _bare_session()
    s._alive = False

    # 哨兵: 一旦回调越过守门进入函数体, 读取 .contents 会把标志置 True
    reached = {"body": False}

    class _Probe:
        @property
        def contents(self):
            reached["body"] = True
            raise RuntimeError("stop")  # 进体后即中断, 但标志已记录

    s._on_decode(0, None, 0, _Probe(), None, None)
    assert reached["body"] is False, "stop 后解码回调应在守门处返回, 不进函数体"
    assert s._frame is None
    assert not s._frame_event.is_set()


def test_c8_alive_allows_processing_attempt():
    """alive=True 时不再被守门拦截(会进入处理, 这里用异常证明它越过了守门)。"""
    from backend.hcnetsdk.types import NET_DVR_SYSHEAD
    s = _bare_session()
    s._alive = True
    s._play_port = 0
    # alive + SYSHEAD 时 _on_real_data 会尝试调用 _play_dll → 触发 _Boom 断言,
    # 证明守门没有误拦在场流量(异常被回调内 try/except 吞掉, 这里改测不静默返回)。
    # 用计数器确认确实进入了 DLL 调用路径。
    called = {"n": 0}

    class _Counter:
        def __getattr__(self, name):
            called["n"] += 1
            raise RuntimeError("stub")
    s._play_dll = _Counter()
    s._on_real_data(0, NET_DVR_SYSHEAD, None, 0, None)
    assert called["n"] >= 1, "alive 时回调应进入处理(被守门误拦则为 0)"
