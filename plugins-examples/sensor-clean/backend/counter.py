"""逐帧产品计数器 —— 实现 detect9.py 的自适应轨迹追踪业务语义。

与早期 demo（detect（视角1用）.py 的"进入→稳定→离开, 离开才计数"）不同,
detect9 的核心是「移动确认即计数 + 强制锁」:

1. 锚动作框一出现就建跟踪, 记首次中心。
2. 跟踪中只要「首尾直线位移 >= move_threshold」且「连续 move_confirm_frames 帧
   都超阈值」(防抖动), 立刻计 1 件 —— 不等它离开。
3. 计数瞬间: 记录计数位置/时间 → 进入 force_lock_frames 帧「强制锁定期」
   (这段帧无视一切检测) → 销毁当前跟踪, 强制等下一个新产品。
4. 位置锁: 若与上次计数位置过近(< lock_spatial) 且时间过短(< lock_time), 跳过本次。
5. 锚框消失累计 lost_frame_thresh 帧才确认产品离开(抗间歇性漏检)。

为什么更准: 帧级硬锁对实时丢帧天然鲁棒 —— 计数后那段帧任何抖动/丢帧/重检都被
忽略, 不像"离开才计数"在产品离开瞬间容易被丢帧带偏(并件 → 漏计)。

坐标改造: detect9 在 960px 显示宽度上使用 MOVE=20px / LOCK_SPATIAL=25px；
本模块接收主程序归一化坐标 (0-1)，生产配置因此使用 20/960、25/960。
类构造器保留旧版兼容回退值，插件 preset 会显式覆盖为客户真值标定参数；
离场和强锁优先走视频时间轴，避免实时推理丢帧改变计数语义。
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


def point_in_polygon(pt, polygon):
    """射线法: 归一化点是否在归一化多边形内 (含边界近似)。

    polygon: [[x,y],...] 至少 3 点; 不合法视为「不限制」返回 True —
    与主程序 ROI 过滤 (source_roi.is_normalized_bbox_center_in_polygon) 语义
    一致, 但纯 Python 实现, 插件不依赖主程序内部模块 / cv2。
    """
    if not polygon or len(polygon) < 3:
        return True
    try:
        px, py = float(pt[0]), float(pt[1])
        n = len(polygon)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = float(polygon[i][0]), float(polygon[i][1])
            xj, yj = float(polygon[j][0]), float(polygon[j][1])
            if (yi > py) != (yj > py):
                x_cross = (xj - xi) * (py - yi) / (yj - yi) + xi
                if px < x_cross:
                    inside = not inside
            j = i
        return inside
    except Exception:
        return True   # 脏数据视为不限制, 绝不因 ROI 配错拦停计数链路


def det_in_roi(det, polygon):
    """检测框中心点是否落在 ROI 多边形内 (无 ROI = 不限制)。"""
    if not polygon or len(polygon) < 3:
        return True
    return point_in_polygon(_center(det), polygon)


class ProductCounter:
    """逐帧产品计数器（detect9 移动确认计数 + 客户真值时间锁）。

    用法: 每帧调 update(anchor_det, now, paused) — anchor_det 为本帧锚动作框
    (无则 None)。返回本帧是否新计了一件产品。
    """

    def __init__(self, move_threshold=DEFAULT_MOVE_THRESHOLD,
                 lock_spatial=DEFAULT_LOCK_SPATIAL, lock_time=DEFAULT_LOCK_TIME,
                 move_confirm_frames=DEFAULT_MOVE_CONFIRM_FRAMES,
                 lost_frame_thresh=DEFAULT_LOST_FRAME_THRESH,
                 force_lock_frames=DEFAULT_FORCE_LOCK_FRAMES,
                 dist_y_weight=1.0, lost_gone_sec=0.0, force_lock_sec=0.0):
        self.move_threshold = move_threshold
        self.lock_spatial = lock_spatial
        self.lock_time = lock_time
        self.move_confirm_frames = int(move_confirm_frames)
        self.lost_frame_thresh = int(lost_frame_thresh)
        self.force_lock_frames = int(force_lock_frames)
        # v1.4.0 时间制阈值 (>0 启用, 替代对应帧数制; 0=沿用帧数制老行为)。
        # 动机: demo 逐帧同步处理不丢帧, "N 帧"即"N/25 秒"; 主程序实时推理丢帧
        # (30fps 源 23fps 推理), 同样的真实离场时间覆盖的处理帧更少, 帧数制
        # 会让跟踪/许可存活过久 → 小幅度视频多计。时间制跨帧率语义一致:
        # lost_gone_sec = demo 5帧/25fps = 0.2s; force_lock_sec = 40帧/25 = 1.6s。
        self.lost_gone_sec = float(lost_gone_sec or 0.0)
        self.force_lock_sec = float(force_lock_sec or 0.0)
        # v1.4.0 纵向位移权重: demo 在像素域算欧氏距离, 纵向像素 = dy_norm*高,
        # 折算到归一化域等价于纵向乘 (高/宽)。等权(1.0)会把纵向位移放大 →
        # 真值回放多计 (1728x1080 视频 demo=38 件, 等权=40, 加权 0.625=38 全对齐)。
        # 默认 1.0 保持 v1.2.0 零差异, 现场按源宽高比配 (preset 给 0.625)。
        self.dist_y_weight = float(dist_y_weight)
        self.reset()

    def _wdist(self, a, b):
        dx = a[0] - b[0]
        dy = (a[1] - b[1]) * self.dist_y_weight
        return math.sqrt(dx * dx + dy * dy)

    def reset(self):
        self.total = 0
        self._tracker = None          # {first, center, lost, counted, mc, last_seen}
        self._last_count_center = None
        self._last_count_time = 0.0
        self._force_lock = 0
        self._force_lock_until = 0.0
        self.tracker_gone = False
        self._prev_ts = None      # 上一处理帧时间 (估计帧间隔用)
        self._ema_dt = None       # 处理帧间隔 EMA (回溯离场判定的单帧余量)

    def update(self, anchor_det, now, paused=False, allow_count=True):
        """每帧调一次。allow_count=False 时跟踪照常但整个计数块跳过
        (detect9(1) 双类别许可语义: 许可未解锁, 位移/确认帧都不推进)。
        计数后调用方可读 tracker_gone 判断本帧跟踪是否因丢失被销毁。
        """
        self.tracker_gone = False
        # 处理帧间隔 EMA: 回溯离场判定要扣掉"重现那一帧本身"的间隔,
        # 否则缺席 k 帧会被算成 k+1 帧, 比逐帧缺席判定严一帧 → 误杀合法跟踪
        if self._prev_ts is not None:
            dt = now - self._prev_ts
            if 0.0 < dt < 0.5:
                self._ema_dt = dt if self._ema_dt is None \
                    else self._ema_dt * 0.9 + dt * 0.1
        self._prev_ts = now
        # 强制锁定期: 计数后锁定一段, 无视一切检测 (防漏检导致跟踪器重建+重复计数)
        # 时间制优先 (丢帧鲁棒), 未启用时按帧数制
        if self.force_lock_sec > 0:
            if now < self._force_lock_until:
                return False
        elif self._force_lock > 0:
            self._force_lock -= 1
            return False

        # 棉签满锁定: 冻结计数 (产品照过但不累加, 等换棉签解锁)
        if paused:
            return False

        center = _center(anchor_det) if anchor_det is not None else None

        if center is not None:
            # 时间制补丁: 锚重现但距上次在场已超离场时长 → 缺席期整段被实时
            # 丢帧吞掉(缺席帧一帧都没被处理到), 帧数制/缺席帧判定都无感。
            # 回溯判定为"已离场又回来": 销毁重建跟踪(first 重置)并重置许可。
            # 扣一帧间隔余量: gap 含"重现帧自身"的间隔, 真实缺席 = gap - dt。
            if self._tracker is not None and self.lost_gone_sec > 0:
                gap = now - self._tracker.get("last_seen", now)
                if gap - (self._ema_dt or 0.0) >= self.lost_gone_sec:
                    self._tracker = None
                    self.tracker_gone = True
            if self._tracker is None:
                self._tracker = {"first": center, "center": center,
                                 "lost": 0, "counted": False, "mc": 0,
                                 "last_seen": now}
                return False
            self._tracker["center"] = center
            self._tracker["lost"] = 0
            self._tracker["last_seen"] = now
            if self._tracker["counted"]:
                return False
            if not allow_count:
                return False
            disp = self._wdist(center, self._tracker["first"])
            if disp < self.move_threshold:
                self._tracker["mc"] = 0
                return False
            self._tracker["mc"] += 1
            if self._tracker["mc"] < self.move_confirm_frames:
                return False
            # 位置锁: 与上次计数过近且过短 → 跳过
            if self._last_count_center is not None \
                    and self._wdist(center, self._last_count_center) < self.lock_spatial \
                    and (now - self._last_count_time) < self.lock_time:
                return False
            # 计数 + 进入强制锁定 + 销毁跟踪
            self.total += 1
            self._last_count_center = center
            self._last_count_time = now
            self._force_lock = self.force_lock_frames
            self._force_lock_until = now + self.force_lock_sec
            self._tracker = None
            return True

        # 锚框消失: 确认离开 → 销毁跟踪。时间制优先 (真实缺席时长, 丢帧鲁棒),
        # 未启用时按连续丢失帧数制
        if self._tracker is not None:
            self._tracker["lost"] += 1
            if self.lost_gone_sec > 0:
                gone = (now - self._tracker.get("last_seen", now)) >= self.lost_gone_sec
            else:
                gone = self._tracker["lost"] >= self.lost_frame_thresh
            if gone:
                self._tracker = None
                self.tracker_gone = True   # detect9(1): 跟踪销毁需重置双类别许可
        return False


class SwabChangeWindow:
    """把换棉签检测框聚合成一次性的有效动作段。

    用法: 每帧调 feed(has_change, now) — has_change 为本帧是否检出换棉签动作。
    返回是否触发一次"有效更换"。判定 = 当前帧仍检出 + 稳定窗口内累计
    >= stable_frames 帧 + 本动作段持续 >= min_sustain_sec 秒。

    v1.4.3 动作段状态机保证一个连续动作最多命中一次；必须连续缺框超过 gap_sec
    才重新武装。lock_time 只防相邻独立动作的边界抖动，不再承担整段去重。
    min_sustain_sec 用真实秒数过滤瞬时误检，避免处理帧率变化改变物理语义。
    类默认 min_sustain_sec=0 保留直接调用兼容性，生产配置使用真值标定值。
    """

    def __init__(self, window_sec=0.35, lock_time=2.0, stable_frames=2,
                 min_sustain_sec=0.0, gap_sec=0.2):
        self.window_sec = window_sec
        self.lock_time = lock_time
        self.stable_frames = stable_frames
        self.min_sustain_sec = float(min_sustain_sec)
        self.gap_sec = float(gap_sec)
        self._win = []
        self._last_emit = None
        self._seg_start = None   # 当前连续动作段起始时间戳
        self._last_seen = None   # 上一帧检出换棉签的时间戳
        self._segment_emitted = False

    def feed(self, has_change, now):
        """接收一帧检测状态，命中有效新动作时返回 True。

        Context: 在 detection_frame 帧循环线程内调用；本对象按通道独占且不持锁；
                 不阻塞、不做 I/O，异常由外层插件 hook 隔离。
        """
        if has_change:
            is_new_segment = self._seg_start is None or self._last_seen is None \
                or now - self._last_seen > self.gap_sec
            if is_new_segment:
                self._win = []
                self._seg_start = now
                self._segment_emitted = False
            self._win.append(now)
            self._last_seen = now
        elif self._last_seen is not None and now - self._last_seen > self.gap_sec:
            # 已真实离场：清掉旧窗口并重新武装；短时丢框仍属于同一动作段。
            self._win = []
            self._seg_start = None
            self._last_seen = None
            self._segment_emitted = False
        self._win[:] = [t for t in self._win if now - t < self.window_sec]
        stable = len(self._win) >= self.stable_frames
        sustained = self._seg_start is not None and \
            (now - self._seg_start) >= self.min_sustain_sec
        cooled = self._last_emit is None or now - self._last_emit > self.lock_time
        if has_change and not self._segment_emitted and stable and sustained and cooled:
            self._last_emit = now
            self._segment_emitted = True
            return True
        return False

    def reset(self):
        self._win = []
        self._last_emit = None
        self._seg_start = None
        self._last_seen = None
        self._segment_emitted = False


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
