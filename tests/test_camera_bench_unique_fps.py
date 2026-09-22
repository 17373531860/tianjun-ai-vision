# -*- coding: utf-8 -*-
"""bench_unique_camera_fps 唯一帧率实测的行为契约 (v3.61)。

背景: Windows MSMF 自带帧率转换(FRC), 把 10fps 传感器流复制填充成
27-30fps, 老 _bench_fps 数 read() 返回率被骗, DSHOW/MSMF 选优误选
MSMF → 全链路指标"正常"但真实画面 10fps (2026-09 雷鸟现场)。
新实现只数内容不同的帧, 复制帧现形。
"""
import time

import numpy as np
import pytest

from backend.api.source_camera_start_mixin import bench_unique_camera_fps


class FakeCap:
    """假相机: 按 pattern 出帧。pattern 元素为 int 种子, 相同种子 = 复制帧。"""

    def __init__(self, pattern, read_delay=0.01, fail=False):
        self.pattern = pattern
        self.read_delay = read_delay
        self.fail = fail
        self.pos = 0

    def read(self):
        if self.fail:
            return False, None
        time.sleep(self.read_delay)
        seed = self.pattern[self.pos % len(self.pattern)]
        self.pos += 1
        rng = np.random.RandomState(seed)
        frame = rng.randint(0, 255, (48, 64, 3), dtype=np.uint8)
        return True, frame


def test_frc_duplicates_reported_as_low_fps():
    """MSMF FRC 场景: 每个真帧复制 3 份 → 唯一帧率应约为 read 率的 1/3。"""
    # 种子序列 1,1,1,2,2,2,3,3,3... 模拟每帧重复 3 次
    pattern = [i // 3 for i in range(30)]
    dup_fps = bench_unique_camera_fps(FakeCap(pattern), n=12)
    fresh_fps = bench_unique_camera_fps(FakeCap(list(range(30))), n=12)
    assert fresh_fps > 0
    ratio = dup_fps / fresh_fps
    # 12 帧里首帧必唯一, 理论比值 ~0.33~0.42, 放宽到 0.15~0.6 防计时抖动
    assert 0.15 < ratio < 0.6, f"复制帧未被识破: dup={dup_fps:.1f} fresh={fresh_fps:.1f}"


def test_real_camera_all_unique():
    """真实相机: 相邻帧带噪声全不同 → 唯一帧率 == read 率(不打折)。"""
    cap = FakeCap(list(range(30)))
    fps = bench_unique_camera_fps(cap, n=10)
    # 10ms/帧 → read 率 ~100fps, 唯一帧率应同量级
    assert fps > 50, f"真实相机被误判打折: {fps:.1f}"


def test_read_failure_returns_zero_like():
    """read 全失败 → 返回 0 唯一帧率, 不抛异常。"""
    fps = bench_unique_camera_fps(FakeCap([0], fail=True), n=5)
    assert fps == 0


def test_timeout_guard():
    """慢相机 read 卡顿 → timeout 内退出, 不阻塞启动流程。"""
    cap = FakeCap(list(range(30)), read_delay=0.3)
    t0 = time.time()
    bench_unique_camera_fps(cap, n=100, timeout=1.0)
    assert time.time() - t0 < 2.5, "timeout 守门失效"
