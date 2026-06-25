"""逐帧产品计数器 —— 复刻 detect6.py 的自适应轨迹追踪算法（现场实测更准）。

与早期 demo（detect（视角1用）.py 的"进入→稳定→离开, 离开才计数"）不同,
detect6 的核心是「移动即计数 + 帧级硬锁」:

1. 锚动作框一出现就建跟踪, 记首次中心。
2. 跟踪中只要「首尾直线位移 >= move_threshold」且「连续 move_confirm_frames 帧
   都超阈值」(防抖动), 立刻计 1 件 —— 不等它离开。
3. 计数瞬间: 记录计数位置/时间 → 进入 force_lock_frames 帧「强制锁定期」
   (这段帧无视一切检测) → 销毁当前跟踪, 强制等下一个新产品。
4. 位置锁: 若与上次计数位置过近(< lock_spatial) 且时间过短(< lock_time), 跳过本次。
5. 锚框消失累计 lost_frame_thresh 帧才确认产品离开(抗间歇性漏检)。

为什么更准: 帧级硬锁对实时丢帧天然鲁棒 —— 计数后那段帧任何抖动/丢帧/重检都被
忽略, 不像"离开才计数"在产品离开瞬间容易被丢帧带偏(并件 → 漏计)。

坐标改造: detect6 用像素坐标 + 像素阈值 (MOVE=20px / LOCK_SPATIAL=25px @1728 宽),
本模块拿到的是主程序归一化坐标 (0-1), 故移动/位置阈值改用归一化值
(20/1728≈0.0116, 25/1728≈0.0145), 由配置覆盖。时间锁走真实时钟 (与 detect6 一致),
帧数类参数 (move_confirm / lost / force_lock) 按帧计数不依赖分辨率。
"""
from __future__ import annotations

import math

# ==================== 默认参数 (detect7(1) 原值, 阈值归一化 @1728 宽) ====================
DEFAULT_MOVE_THRESHOLD = 0.0116       # 移动判定阈值 (detect7(1) 20px / 1728)
DEFAULT_LOCK_SPATIAL = 0.0145         # 位置锁范围 (detect7(1) 25px / 1728)
DEFAULT_LOCK_TIME = 3.0               # 位置锁 / 计数冷却 (秒, detect7(1) LOCK_TIME)
DEFAULT_MOVE_CONFIRM_FRAMES = 3       # 连续 N 帧位移超阈值才确认移动 (detect7(1))
DEFAULT_LOST_FRAME_THRESH = 5         # 连续丢失 N 帧确认产品离开 (detect7(1))
DEFAULT_FORCE_LOCK_FRAMES = 40        # 计数后强制锁定帧数 (detect7(1) FORCE_LOCK_TOTAL)


def _center(det):
    """归一化检测框 (x,y=左上, w,h) → 中心点 (cx, cy)。"""
    return (det["x"] + det["w"] / 2.0, det["y"] + det["h"] / 2.0)


def _dist(a, b):
    """归一化坐标欧氏距离 (照搬 detect7(1) 像素欧氏度量, 阈值按 @1728 归一化)。"""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


class ProductCounter:
    """逐帧产品计数器 (复刻 detect6 移动即计数 + 帧硬锁算法)。

    用法: 每帧调 update(anchor_det, now, paused) — anchor_det 为本帧锚动作框
    (无则 None)。返回本帧是否新计了一件产品。
    """

    def __init__(self, move_threshold=DEFAULT_MOVE_THRESHOLD,
                 lock_spatial=DEFAULT_LOCK_SPATIAL, lock_time=DEFAULT_LOCK_TIME,
                 move_confirm_frames=DEFAULT_MOVE_CONFIRM_FRAMES,
                 lost_frame_thresh=DEFAULT_LOST_FRAME_THRESH,
                 force_lock_frames=DEFAULT_FORCE_LOCK_FRAMES):
        self.move_threshold = move_threshold
        self.lock_spatial = lock_spatial
        self.lock_time = lock_time
        self.move_confirm_frames = int(move_confirm_frames)
        self.lost_frame_thresh = int(lost_frame_thresh)
        self.force_lock_frames = int(force_lock_frames)
        self.reset()

    def reset(self):
        self.total = 0
        self._tracker = None          # {first, center, lost, counted, mc}
        self._last_count_center = None
        self._last_count_time = 0.0
        self._force_lock = 0

    def update(self, anchor_det, now, paused=False):
        # 强制锁定期: 计数后锁定 N 帧, 无视一切检测 (防漏检导致跟踪器重建+重复计数)
        if self._force_lock > 0:
            self._force_lock -= 1
            return False

        # 棉签满锁定: 冻结计数 (产品照过但不累加, 等换棉签解锁)
        if paused:
            return False

        center = _center(anchor_det) if anchor_det is not None else None

        if center is not None:
            if self._tracker is None:
                self._tracker = {"first": center, "center": center,
                                 "lost": 0, "counted": False, "mc": 0}
                return False
            self._tracker["center"] = center
            self._tracker["lost"] = 0
            if self._tracker["counted"]:
                return False
            disp = _dist(center, self._tracker["first"])
            if disp < self.move_threshold:
                self._tracker["mc"] = 0
                return False
            self._tracker["mc"] += 1
            if self._tracker["mc"] < self.move_confirm_frames:
                return False
            # 位置锁: 与上次计数过近且过短 → 跳过
            if self._last_count_center is not None \
                    and _dist(center, self._last_count_center) < self.lock_spatial \
                    and (now - self._last_count_time) < self.lock_time:
                return False
            # 计数 + 进入强制锁定 + 销毁跟踪
            self.total += 1
            self._last_count_center = center
            self._last_count_time = now
            self._force_lock = self.force_lock_frames
            self._tracker = None
            return True

        # 锚框消失: 累计丢失帧, 超阈值确认离开
        if self._tracker is not None:
            self._tracker["lost"] += 1
            if self._tracker["lost"] >= self.lost_frame_thresh:
                self._tracker = None
        return False


class SwabChangeWindow:
    """换棉签动作稳定性窗口 (照搬 demo SwabChangeProcessor + v1.1.0 防抖 gate)。

    用法: 每帧调 feed(has_change, now) — has_change 为本帧是否检出换棉签动作。
    返回是否触发一次"有效更换"。判定 = 稳定窗口内累计 >= stable_frames 帧
    + 本次连续动作段已持续 >= min_sustain_sec 秒 + 距上次触发超过 lock_time 去重。

    防抖背景 (v1.1.0): 真值统计显示换棉签框偶发 1~3 帧的瞬时误检 (位移仅几像素),
    旧"稳定2帧即解锁"会把这些当成换棉签多算。min_sustain_sec 要求动作段在物理
    时间上持续够久才算 — 用秒而非帧数, 现场任意帧率 (i5+3060 大图实时常掉到
    15~25fps) 都一致鲁棒。gap_sec 容忍动作中途短暂丢框 (否则一次动作被切成多段)。
    默认 min_sustain_sec=0.0 = 退化为旧行为 (向后兼容); 生产配置层给非零值启用。
    """

    def __init__(self, window_sec=0.35, lock_time=2.0, stable_frames=2,
                 min_sustain_sec=0.0, gap_sec=0.2):
        self.window_sec = window_sec
        self.lock_time = lock_time
        self.stable_frames = stable_frames
        self.min_sustain_sec = float(min_sustain_sec)
        self.gap_sec = float(gap_sec)
        self._win = []
        self._last_emit = 0.0
        self._seg_start = None   # 当前连续动作段起始时间戳
        self._last_seen = None   # 上一帧检出换棉签的时间戳

    def feed(self, has_change, now):
        if has_change:
            self._win.append(now)
            # 段追踪: 距上次检出超 gap_sec 视为新动作段
            if self._seg_start is None or \
                    (self._last_seen is not None and now - self._last_seen > self.gap_sec):
                self._seg_start = now
            self._last_seen = now
        self._win[:] = [t for t in self._win if now - t < self.window_sec]
        stable = len(self._win) >= self.stable_frames
        sustained = self._seg_start is not None and \
            (now - self._seg_start) >= self.min_sustain_sec
        if stable and sustained and now - self._last_emit > self.lock_time:
            self._last_emit = now
            return True
        return False

    def reset(self):
        self._win = []
        self._last_emit = 0.0
        self._seg_start = None
        self._last_seen = None


# ==================== v1.1.0 假动作 / 操作员离开判定 ====================
DEFAULT_STILL_TIME = 2.0       # 框停留超此秒数判"静止" (detect7(1) SWAB_STILL_TIME)
DEFAULT_STILL_DISP = 0.0058    # 位移小于此(归一化)视为不动 (detect7(1) 10px / 1728)


class StillFakeActionDetector:
    """静止假动作判定器 (复刻 detect7(1) dirty_tracker 逻辑)。

    用途: 追踪某个动作框 (如视角1 "擦拭产品"框), 若框出现后停留超 still_time 秒
    但累计位移 < still_disp (归一化), 判定为"假擦拭/没真擦"命中一次告警;
    若位移够大 → 视为正常动作, 销毁追踪不再告警; 框消失累计 lost 帧后清理重置。

    与 demo 一致的一次性语义: 同一个框命中告警一次, 框消失重置后才会再判。
    """

    def __init__(self, still_time=DEFAULT_STILL_TIME, still_disp=DEFAULT_STILL_DISP,
                 lost_frame_thresh=DEFAULT_LOST_FRAME_THRESH):
        self.still_time = float(still_time)
        self.still_disp = float(still_disp)
        self.lost_frame_thresh = int(lost_frame_thresh)
        self.reset()

    def reset(self):
        self._tracker = None    # {first, birth, lost}
        self._warned = False

    def update(self, det, now):
        """每帧调一次。det 为本帧目标框 (归一化 dict) 或 None。返回本帧是否命中假动作。"""
        if det is not None:
            center = _center(det)
            if self._tracker is None:
                self._tracker = {"first": center, "birth": now, "lost": 0}
                self._warned = False
                return False
            self._tracker["lost"] = 0
            if self._warned:
                return False
            disp = _dist(center, self._tracker["first"])
            if disp >= self.still_disp:
                # 位移够大 = 正常动作, 销毁追踪 (下次框再来重新计时)
                self._tracker = None
                return False
            if now - self._tracker["birth"] > self.still_time:
                self._warned = True
                return True
            return False

        # 框消失: 累计丢失帧, 超阈值清理重置
        if self._tracker is not None:
            self._tracker["lost"] += 1
            if self._tracker["lost"] >= self.lost_frame_thresh:
                self._tracker = None
                self._warned = False
        return False


class AbsentCountdown:
    """操作员离开超时倒计时 (复刻 detect7(1) 周期性强制动作, 纯帧级、无后台线程)。

    用途: 视角2(换棉签通道) 长时间无任何检测目标 = 操作员离开了岗位。
    每帧 feed(present, now): present=本帧是否检测到操作员(任意目标)。
    检测到 → 重置截止时间戳; 持续无检测且超 timeout → 命中一次 (warned 去重),
    直到再次检测到人才解除。返回 (是否命中, 剩余秒) — 剩余秒供面板显示。
    """

    def __init__(self, timeout_sec=600.0):
        self.timeout_sec = float(timeout_sec)
        self.reset()

    def reset(self):
        self._deadline = 0.0
        self._warned = False

    def feed(self, present, now):
        if self._deadline <= 0.0:
            self._deadline = now + self.timeout_sec
        if present:
            self._deadline = now + self.timeout_sec
            self._warned = False
            return False, int(self.timeout_sec)
        remaining = max(0, int(self._deadline - now))
        if remaining <= 0 and not self._warned:
            self._warned = True
            return True, 0
        return False, remaining
