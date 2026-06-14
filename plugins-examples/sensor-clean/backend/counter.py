"""逐帧产品计数器 —— 照搬 demo (detect（视角1用）.py) 的 ProductCounter 算法。

与主程序的"步骤/周期结算"不同, 本模块复刻 demo 的逐帧生命周期跟踪:
锚动作框「进入→稳定→离开」= 处理完 1 件, 在它离开时计数一次;
移动阈值 + 空间锁 + 时间锁防止同一件被重复计数。

坐标改造: demo 用像素坐标 + 像素阈值 (MOVE=20px / LOCK_SPATIAL=35px @ 640 宽),
本模块拿到的是主程序归一化坐标 (0-1), 故阈值改用归一化值
(20/640≈0.031, 35/640≈0.055), 由配置覆盖。时间锁仍走真实时钟 (与 demo 一致)。
"""
from __future__ import annotations

import math

# ==================== 默认参数 (归一化, 对齐 demo @640 宽) ====================
DEFAULT_MOVE_THRESHOLD = 0.03      # 产品移动判定阈值 (demo 20px / 640)
DEFAULT_LOCK_SPATIAL = 0.055       # 位置锁范围 (demo 35px / 640)
DEFAULT_LOCK_TIME = 2.0            # 位置锁 / 计数冷却时间 (秒, 同 demo)
DEFAULT_ENTER_FRAMES = 2           # 进入确认帧数 (同 demo)
DEFAULT_LEAVE_FRAMES = 3           # 离开确认帧数 (同 demo)
# 短暂消失容忍: 锚框消失但在 N 帧内重现, 视为仍在画面 (不进入"离开"判定),
# 用于抵抗主程序实时推理丢帧把一件产品的生命周期切碎成多次计数 (demo 离线
# 逐帧不丢帧, 故默认 0 = 完全复刻 demo; 实时管线丢帧场景调大此值压重复计数)。
DEFAULT_DISAPPEAR_TOLERANCE = 0


def _center(det):
    """归一化检测框 (x,y=左上, w,h) → 中心点 (cx, cy)。"""
    return (det["x"] + det["w"] / 2.0, det["y"] + det["h"] / 2.0)


def _dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


class ProductTracker:
    """单个产品的完整生命周期 (照搬 demo ProductTracker, 中心点归一化)。"""

    STATUS_ENTERING = 0
    STATUS_STABLE = 1
    STATUS_LEAVING = 2
    STATUS_LEFT = 3

    def __init__(self, center, timestamp, enter_frames, leave_frames):
        self.center = center
        self.last_center = center
        self.trajectory = [center]
        self.timestamp = timestamp
        self.status = self.STATUS_ENTERING
        self.enter_frames = 1
        self.leave_frames = 0
        self._enter_need = enter_frames
        self._leave_need = leave_frames

    def update(self, center, timestamp):
        self.center = center
        self.last_center = center
        self.trajectory.append(center)
        self.timestamp = timestamp
        if self.status == self.STATUS_ENTERING:
            self.enter_frames += 1
            if self.enter_frames >= self._enter_need:
                self.status = self.STATUS_STABLE

    def mark_leaving(self):
        if self.status == self.STATUS_STABLE:
            self.status = self.STATUS_LEAVING
            self.leave_frames = 1

    def update_leaving(self):
        if self.status == self.STATUS_LEAVING:
            self.leave_frames += 1
            if self.leave_frames >= self._leave_need:
                self.status = self.STATUS_LEFT

    def total_movement(self):
        if len(self.trajectory) < 2:
            return 0.0
        total = 0.0
        for i in range(1, len(self.trajectory)):
            total += _dist(self.trajectory[i], self.trajectory[i - 1])
        return total


class ProductCounter:
    """逐帧产品计数器 (照搬 demo ProductCounter)。

    用法: 每帧调 update(anchor_det, now, paused) — anchor_det 为本帧锚动作框
    (无则 None)。返回本帧是否新计了一件产品。
    """

    def __init__(self, move_threshold=DEFAULT_MOVE_THRESHOLD,
                 lock_spatial=DEFAULT_LOCK_SPATIAL, lock_time=DEFAULT_LOCK_TIME,
                 enter_frames=DEFAULT_ENTER_FRAMES, leave_frames=DEFAULT_LEAVE_FRAMES,
                 disappear_tolerance=DEFAULT_DISAPPEAR_TOLERANCE):
        self.move_threshold = move_threshold
        self.lock_spatial = lock_spatial
        self.lock_time = lock_time
        self.enter_frames = enter_frames
        self.leave_frames = leave_frames
        self.disappear_tolerance = disappear_tolerance
        self.reset()

    def reset(self):
        self.total = 0
        self.current = None
        self.has_counted = False
        self.last_count_time = 0.0
        self.lock_center = None
        self.lock_timestamp = 0.0
        self._miss = 0

    def _can_count(self, center, now):
        if now - self.last_count_time < self.lock_time:
            return False
        if self.lock_center is not None:
            if _dist(center, self.lock_center) < self.lock_spatial and \
               (now - self.lock_timestamp) < self.lock_time:
                return False
        return True

    def _commit(self, center, now):
        self.total += 1
        self.last_count_time = now
        self.lock_center = center
        self.lock_timestamp = now

    def update(self, anchor_det, now, paused=False):
        if anchor_det is not None:
            self._miss = 0
            center = _center(anchor_det)
            if self.current is None:
                self.current = ProductTracker(center, now, self.enter_frames, self.leave_frames)
                self.has_counted = False
            else:
                self.current.update(center, now)
            return False

        # 锚动作消失
        if self.current is None:
            return False

        # 短暂消失容忍: N 帧内重现视为仍在画面, 不进入离开判定 (抗实时丢帧切碎生命周期)
        if self.current.status == ProductTracker.STATUS_STABLE:
            self._miss += 1
            if self._miss <= self.disappear_tolerance:
                return False

        # 还没稳定就消失 → 噪声丢弃
        if self.current.status == ProductTracker.STATUS_ENTERING:
            self.current = None
            self.has_counted = False
            return False

        self.current.mark_leaving()
        self.current.update_leaving()
        if self.current.status != ProductTracker.STATUS_LEFT:
            return False

        # 完整离开: 满足移动 + 防重复 + 未暂停才计数
        counted = False
        center = self.current.last_center
        if (not self.has_counted and not paused
                and self.current.total_movement() >= self.move_threshold
                and self._can_count(center, now)):
            self._commit(center, now)
            counted = True
        self.current = None
        self.has_counted = False
        return counted


class SwabChangeWindow:
    """换棉签动作稳定性窗口 (照搬 demo SwabChangeProcessor 的判定逻辑)。

    用法: 每帧调 feed(has_change, now) — has_change 为本帧是否检出换棉签动作。
    返回是否触发一次"有效更换" (稳定 >= 2 帧 + 距上次触发超过去重时间)。
    """

    def __init__(self, window_sec=0.35, lock_time=2.0, stable_frames=2):
        self.window_sec = window_sec
        self.lock_time = lock_time
        self.stable_frames = stable_frames
        self._win = []
        self._last_emit = 0.0

    def feed(self, has_change, now):
        if has_change:
            self._win.append(now)
        self._win[:] = [t for t in self._win if now - t < self.window_sec]
        stable = len(self._win) >= self.stable_frames
        if stable and now - self._last_emit > self.lock_time:
            self._last_emit = now
            return True
        return False

    def reset(self):
        self._win = []
        self._last_emit = 0.0
