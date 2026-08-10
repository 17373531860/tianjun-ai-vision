"""v3.19.x 自定义模式混合子状态机 (custom_mixed_with = 'per_item' | 'tracking').

架构原则 (重构后): **混合 = 复用独立模式的真引擎, 不发明第三套语义**。
  - 混合跟踪: 直接驱动宿主 TrackingMixin 的真机械 — max_recognized 后处理 /
    Phase1 位置锁 / Phase2 ID 匹配+re-ID / 抗闪烁 (ID Lock·Swap·外观) /
    失帧过期 / 动作计数 FSM / 堆叠 FSM / 清单重建, 全部调用独立跟踪模式的
    同一份代码。唯一被替换的是"周期主权": 砍掉四种周期结束策略
    (_tracking_check_settlement) 与自动开周期 (host._tracking_external_cycle
    守门), 周期始末完全听步骤侧 (基础模式) 的。
  - 混合逐件: 直接复用 PerItemMixin 的真个体状态机 (_PerItemStep) —
    个体锁定/跨帧位置匹配/目标⟶动作覆盖配对/持续帧确认/补锁定吸收/
    虚拟漏件判定全套原班逻辑。砍掉的同样只是周期主权: 稳定窗口开周期/
    收尾标签/完成即结算/双超时这些"何时开始何时结算"的判定全部听步骤侧。

与主状态机的三条契约 (违反任何一条都会污染现有模式):
  1. 物品标签由本组件独占消费 — 在 _update_step_stats 中被剥离,
     永远不进入步骤侧状态机 (cross_cycle / last_first / 同时组 / _process_single_step)。
     注: 若动作标签同时是步骤行 (既推动序列又覆盖个体), 该标签不剥离, 两边共享。
  2. 周期生命周期完全跟随步骤侧 — current_cycle_uuid 变化即重置统计,
     本组件永不开/关周期, 永不直接 _trigger_event。
  3. 结算永远由步骤侧触发 — 各结算点经 compose_settle_event 合成:
     步骤侧 OK + 物品侧 OK 才是 OK, 任一 NG 即 NG (原因合并)。

配置位 (原生词汇, 与独立模式同名同义):
  pipeline_config.custom_mixed_with : 'per_item' | 'tracking' | 缺省(现状, 零差异)
  steps_config[i].detect_role       : 'step'(默认) | 'item' — 物品行不参与序列/检测步骤

  混合跟踪的物品行直接使用独立跟踪模式的步骤字段 (真 loader 直接读):
    count_mode ('track'|'event') / event_required_count / event_gone_frames /
    event_min_visible_frames / stack_enabled / stack_reappear_seconds /
    stack_required_count / max_recognized / tracking_max_lost_seconds /
    tracking_position_lock / roi
  外加行级期望数量:
    steps_config[i].expected_count  : 跟踪计数行的周期期望个体数
    (兼容旧存储 steps_config[i].mix_item.expected_count)
  抗闪烁开关沿用 pipeline 原生字段: tracking_swap_detection /
    tracking_appearance_match / tracking_id_lock / tracking_id_lock_frames

  混合逐件的物品行直接使用独立逐件模式的步骤字段 (steps_config[i].per_item):
    item_label (str | list, OR 合并) / action_label / item_tracking_iou /
    coverage_iou / coverage_use_center / sustain_frames / expected_count
  项目级仅 pipeline_config.per_item.item_timeout_seconds 生效 (auto 模式
  个体超时清理); 稳定窗口/收尾标签/双超时/手动结算等周期字段在混合下无效。
"""
from __future__ import annotations

import time

from backend.api.source_roi import is_normalized_bbox_center_in_polygon

MIX_TYPES = ('per_item', 'tracking')


# ============================================================
# 混合跟踪 · 托盘容器累加器 (周期主权外移 — 不结算只记账)
# ============================================================


class _ContainerAccumulator:
    """混合跟踪专用容器累加器: 物品按"当前主托盘"分组, 托盘进箱只记账。

    与独立容器模式 (_update_container_grouping) 的本质区别 — 周期主权归步骤侧:
      - 不调 _settle_box / _trigger_event / end_cycle (整箱结算听封箱步骤)
      - 托盘消失 (gone-confirm) 只 append 到本周期"已装托盘清单", 绝不开/关周期
      - 主托盘 = 未结算托盘里 first_seen 最早且仍在场的那个 (先进先出, 用户敲定)

    托盘身份独立轻量跟踪 (IoU 跨帧关联 + first_seen), 与宿主物品跟踪机械隔离,
    永不污染物品判定 / 步骤侧状态机。坐标全程归一化 [0,1]。
    """

    def __init__(self, container_label: str, item_expected: dict,
                 box_count: int, gone_frames: int, iou_match: float = 0.3,
                 count_mode: str = 'trays', item_target: int = 0,
                 confirm_by_frames: bool = True, confirm_by_action: bool = False,
                 action_label: str = '', confirm_combine: str = 'or',
                 action_min_frames: int = 3, action_gone_frames: int = 8,
                 action_cooldown_s: float = 2.0, per_tray_guard: bool = False,
                 peak_cap: int = 0, stable_min_frames: int = 0,
                 dedup_items: bool = True, dedup_trays: bool = False,
                 purge_empty_primary: bool = False,
                 yield_primary: bool = False,
                 unified_book_source: bool = False,
                 slot_check_label: str = '', slot_total: int = 0,
                 item_dedup_iou: float = 0.45, tray_dedup_iou: float = 0.0):
        self.container_label = container_label
        self.item_expected = {k: int(v) for k, v in (item_expected or {}).items()}
        # v3.44.4 每盘峰值封顶 (可配, 默认 0=关): 模型偶发重复框瞬时数出 25/26,
        # 峰值取存续期最大值会咬死这一帧 → 整箱 97/96 被误判"超出"NG (7-23 视频
        # 实测)。盘子物理槽位有上限, 配了就按此值封每盘每标签的峰值。
        self.peak_cap = max(0, int(peak_cap or 0))
        self.box_count = int(box_count or 0)
        self.gone_frames = max(1, int(gone_frames or 30))
        self.iou_match = float(iou_match)
        # 计数模式: 'trays' = 计托盘数 + 每盘门槛; 'items_total' = 累加进箱滑块总数, 整箱判目标
        self.count_mode = count_mode if count_mode in ('trays', 'items_total') else 'trays'
        self.item_target = int(item_target or 0)   # items_total 模式整箱滑块目标 (如 96)
        # 进箱确认方式: 消失满 N 帧 / 识别到"放托盘"动作 / 两者组合 (or|and)。
        # 默认仅"消失满帧"=老行为零差异; 配了动作标签且开了动作确认才进入动作判定路径。
        self.action_label = (action_label or '').strip()
        self.confirm_by_action = bool(confirm_by_action) and bool(self.action_label)
        # 至少留一个确认条件: 若动作确认没生效(没配标签), 强制回落到消失满帧, 避免永不进箱
        self.confirm_by_frames = bool(confirm_by_frames) or not self.confirm_by_action
        self.confirm_combine = 'and' if str(confirm_combine).lower() == 'and' else 'or'
        self.action_min_frames = max(1, int(action_min_frames or 1))   # 放托盘标签连续出现满此帧 = 动作成立(进行中)
        self.action_gone_frames = max(1, int(action_gone_frames or 1))  # 动作中标签消失满此帧 = 动作结束
        # v3.43.1 动作不应期 (秒, 0=关闭): 距上一次进箱记账不足此间隔的动作脉冲一律吸收 —
        # 治"放托盘标签中途断检超过消失帧, 一次动作被拆成两次、同一盘记两次账"
        # (客户机 TRT 推理下放托盘类置信度贴阈值抖动, 断检 8 帧≈0.26s 极易踩中)。
        # ⚠️ 纯时间窗: 现场若快节奏连放 (两盘间隔小于不应期) 需在 UI 把间隔调小。
        self.action_cooldown_s = max(0.0, float(action_cooldown_s or 0.0))
        # v3.44.1 每盘数量校验 (仅 items_total 模式): 进箱那一刻核这盘数量 ==
        # 每盘期望 (尾盘 = 距整箱目标的余数, "只算总数对不对得上"), 不对 → 这盘
        # 不记账 + 抛错盘警报 (宿主定格报警, 工人取出该盘确认后重装)。默认关。
        self.per_tray_guard = bool(per_tray_guard)
        # v3.44.5 "动作前稳定计数"快照记账 (可配, 默认 0=关 → 零差异):
        # 训练端证据 (2026-07-28 交付): 盘从堆上拿走后检测框无缝接上露出的下一盘,
        # 身份永不消失 → 靠"身份消失"驱动的记账在连放场景必然合并/漏账; 而
        # 拿起前暂放区计数连续数秒纹丝不动 (16 盘验证 10 全对 4 差±1)。
        # 打开后: 计数值连续同值满此帧数 = 该身份的"稳定计数"; 动作成立瞬间
        # 快照所有在位身份的稳定计数; 动作脉冲结算时主位有本次动作的新鲜快照
        # 即刻按快照记账 (不再等消失满帧), 记账后身份仍在场则原地清零重开新账
        # (下一盘继续用同一身份攒稳定值)。与工人速度解耦: 快慢只影响稳定段长短,
        # 不再依赖"两次动作之间标签必须断开/身份必须消失"。
        self.stable_min_frames = max(0, int(stable_min_frames or 0))
        # v3.46 五个可选开关 (默认值 = v3.45 线上行为, 关掉即回退, 不用打补丁):
        # - dedup_items: 滑块同标签高重叠去重 (v3.44 起线上一直开, 默认开)
        # - dedup_trays: 托盘框同帧高重叠去重 (新, 治影子身份, 默认关)
        # - purge_empty_primary: 主盘指针指着的空账身份离场满帧照样清 (默认关)
        # - yield_primary: 指针可让位给在位且账面更实的身份 (默认关)
        # - unified_book_source: 卡片三数 + 封箱凑数统一问"结账候选" (默认关)
        self.dedup_items = bool(dedup_items)
        self.dedup_trays = bool(dedup_trays)
        self.purge_empty_primary = bool(purge_empty_primary)
        self.yield_primary = bool(yield_primary)
        self.unified_book_source = bool(unified_book_source)
        # v3.46 槽位完整性门 (可配, 默认关 → 零差异): 盘的物理槽位数固定, 每个槽
        # 要么装着货要么空着 — 模型加训"空槽"类后, 一帧里 货数+空槽数 应恒等于
        # 槽位总数。不相等 = 这一帧没看全 (手挡住 / 盘半出画 / 重复框多数出来),
        # 该帧对该盘的观察一律不采信: 不抬峰值、不进稳定窗口。
        # 治的是"遮挡期的残数被记成峰值后咬死"与"重复框数出 25 顶峰值"两类老账。
        # 兜底: 另存一份不过门的峰值, 万一整盘全程没有一帧看全, 记账仍退回它,
        # 最坏情况与开关关闭时等价, 绝不会因为开了门就把盘漏账。
        self.slot_check_label = (slot_check_label or '').strip()
        self.slot_total = max(0, int(slot_total or 0))
        self.slot_check_on = bool(self.slot_check_label) and self.slot_total > 0
        # v3.46 两处"同一目标被画两个框"的去重阈值 (可配, 0 = 关闭该项去重):
        #   物品框 — v3.45 起硬编码常开 0.45, 现改为可配, 默认值保持 0.45 = 零差异
        #   托盘框 — 新增, 默认 0 关闭: 一个盘被吐两个框会多出一张影子工牌, 长期
        #            在位且带着跨盘旧数字, 主位一释放它就顶上去 (实测可复现)
        self.item_dedup_iou = max(0.0, float(item_dedup_iou or 0.0))
        self.tray_dedup_iou = max(0.0, float(tray_dedup_iou or 0.0))
        # 合流归一 (v3.47 五开关 × SY9 阈值可配, 同治一处去重, 两套配置面并存):
        # dedup_items=False 视为该项关(阈值归零); dedup_trays=True 但没配阈值
        # 时沿用老硬编码 0.45 — 两边客户的默认行为都零差异
        if not self.dedup_items:
            self.item_dedup_iou = 0.0
        if self.dedup_trays and self.tray_dedup_iou <= 0:
            self.tray_dedup_iou = 0.45
        self.reset()

    # 稳定值回看窗口 (秒): 稳定值取近 N 秒内有效窗口众数的最大值 — 免疫收尾
    # 伸手"骤降尾巴", 同时让上一盘进箱余像 (内袋盖住前箱内短暂可见) 自动过期。
    # 4s 由 7-27 视频取证定标: 盘的完整视角常只在账期早段 (紧凑连放时工人手
    # 悬停变长, 后段永远缺 1 个), 2.5s 会把它剪掉; 余像距下次动作 ≥4.9s 仍过期
    STABLE_LOOKBACK_S = 4.0

    def reset(self):
        self._trays = {}        # tid -> {bbox, first_seen, last_seen, gone, peak:{label:cnt}}
        self._seq = 0
        self._primary = None    # 当前主托盘 tid
        self._done = []         # [{label:peak}, ...] 本周期已装托盘
        self._cur_counts = {}   # 当前主托盘实时物品数 {label:cnt}
        # ---- 放托盘动作状态机 + 进箱确认标志 ----
        self._action_seen = 0          # 放托盘标签连续在场帧
        self._action_gone = 0          # 动作中标签连续消失帧
        self._action_in_progress = False   # 动作进行中 = 屏蔽窗口(锁主托盘, 旧主盘停止计数)
        self._action_started_ts = None     # 本次动作成立时刻 (v3.44.2 识别"动作期新生盘"用)
        self._action_done_pending = False  # 一次放托盘动作已完成、待与进箱判定配对
        self._primary_frames_ok = False    # 当前主托盘是否已"消失满帧"(AND 组合用标志位记忆)
        self._last_action_settle_ts = None  # 上一次动作确认进箱的时刻 (不应期基准; None=还没结过)
        self._settle_defer_frames = 0  # v3.44.3 动作结账"峰值就绪等待"已挂帧数
        self.wrong_tray_alert = None   # 错盘警报 (宿主每帧消费): {'index','count','expected'}
        self._tid_cur = {}      # v3.46 各在位身份的当帧计数 {tid: {label: cnt}} (同源展示用)
        # v3.46 槽位完整性门的本帧结果 (前端展示"这一帧看全了没"): None=未开门
        self._slot_view = None

    def _update_action_fsm(self, action_present: bool, current_time: float = 0.0):
        """放托盘动作状态机: 标签连续在场满 action_min_frames 帧 → 动作成立(进行中,
        开启屏蔽窗口); 进行中标签消失满 action_gone_frames 帧 → 动作结束(置 pending)。
        仅 confirm_by_action 时驱动; 否则全程 no-op (屏蔽窗口永不开, 老行为零差异)。

        不应期 (v3.43.1): 距上一次动作进箱不足 action_cooldown_s 的"动作结束"视为同一次
        动作被断检拆出的余波, 吸收掉不置 pending (只留调试日志), 杜绝一次动作记两次账。
        """
        if not self.confirm_by_action:
            return
        if action_present:
            self._action_seen += 1
            self._action_gone = 0
            if self._action_seen >= self.action_min_frames:
                if not self._action_in_progress:
                    self._action_started_ts = current_time
                    # v3.44.5 动作成立瞬间: 快照各身份的稳定计数 = 本次动作要
                    # 放的这盘"手接触前"的真实数量 (训练端验证方案)。
                    # 不要求身份仍在场 — 手先拿盘、动作标签滞后半秒是常态
                    # (7-27 箱2取证: 持真值 23 的身份此刻 gone=9), 新鲜度由
                    # 回看窗口重剪保证: 停更身份 (箱内余像) 的陈旧稳定值进不来
                    if self.stable_min_frames > 0:
                        for _t in self._trays.values():
                            _b = _t['bbox']
                            _ccx = _b['x'] + _b['w'] / 2.0
                            _ccy = _b['y'] + _b['h'] / 2.0
                            _snap = {}
                            for _lbl, _ml in (_t.get('modes') or {}).items():
                                _recent = [
                                    e[1] for e in _ml
                                    if e[0] >= current_time - self.STABLE_LOOKBACK_S
                                    and abs(e[2] - _ccx) <= _b['w'] * 0.5
                                    and abs(e[3] - _ccy) <= _b['h'] * 0.5]
                                if _recent:
                                    _snap[_lbl] = max(_recent)
                            if _snap:
                                _t['pre_action_stable'] = _snap
                                _t['pre_action_ts'] = current_time
                    try:
                        from backend.core import debug_center
                        if debug_center.is_on("backend.packaging"):
                            _ss = {tid: dict(t.get('pre_action_stable') or {})
                                   for tid, t in self._trays.items()
                                   if t.get('pre_action_ts') == current_time}
                            debug_center.dbg("backend.packaging", "放托盘动作成立",
                                             f"t={current_time:.2f} 稳定快照={_ss}")
                    except Exception:
                        pass
                self._action_in_progress = True
        else:
            if self._action_in_progress:
                self._action_gone += 1
                if self._action_gone >= self.action_gone_frames:
                    # 动作结束; 复位帧计数等待下一次动作
                    self._action_in_progress = False
                    self._action_seen = 0
                    self._action_gone = 0
                    in_cooldown = (
                        self.action_cooldown_s > 0
                        and self._last_action_settle_ts is not None
                        and (current_time - self._last_action_settle_ts)
                        < self.action_cooldown_s)
                    if not in_cooldown:
                        self._action_done_pending = True   # 待配对进箱
                    try:
                        from backend.core import debug_center
                        if debug_center.is_on("backend.packaging"):
                            if in_cooldown:
                                debug_center.dbg(
                                    "backend.packaging", "放托盘动作脉冲被不应期吸收",
                                    f"距上次进箱 {current_time - self._last_action_settle_ts:.2f}s"
                                    f" < 不应期 {self.action_cooldown_s:.2f}s, 判为同一次动作余波")
                            else:
                                debug_center.dbg("backend.packaging", "放托盘动作完成",
                                                 f"action_label={self.action_label}")
                    except Exception:
                        pass
            else:
                # 还没成立就消失 = 误检闪现, 不算一次动作
                self._action_seen = 0

    def _eff_peak(self, t: dict) -> dict:
        """这张工牌的有效账面: 过了槽位门的真峰值优先, 没有才退影子峰值。

        v3.46: 结算链上所有"这盘到底装没装货 / 装了多少"的判断都走这里 —
        开了槽位门以后真峰值可能还没等到一帧完整视角就要结账, 若那些判断只认
        真峰值, 真盘会被当成空框幽灵清掉、脉冲被烧, 整盘丢账 (7-30 视频实测
        4 盘只记住 2 盘)。
        """
        if t.get('peak'):
            return t['peak']
        if self.slot_check_on and t.get('peak_raw'):
            return t['peak_raw']
        return {}

    def _has_goods(self, t: dict) -> bool:
        """这张工牌名下到底有没有装过货。"""
        return bool(self._eff_peak(t))

    def _fresh_snapshot(self, t: dict) -> dict:
        """v3.44.5 返回该身份"本次动作"的稳定计数快照 (无/过期 → 空 dict)。"""
        if self.stable_min_frames <= 0 or self._action_started_ts is None:
            return {}
        if t.get('pre_action_ts') != self._action_started_ts:
            return {}
        return dict(t.get('pre_action_stable') or {})

    def _best_fresh_snapshot_tid(self):
        """v3.44.5 全场找"持有本次动作新鲜快照"的身份 (None=没有)。

        紧凑连放时主位常被在途幽灵 (手里的盘, 只看到 3-4 个) 抢走, 而动作成立
        瞬间真正核准过数量的备盘堆身份躺在非主位 — 脉冲记账必须认快照不认主位。
        多个持快照者取快照总数最大 (遮挡只会看少不会看多), 平手取最近在场。
        """
        best = None
        for tid, t in self._trays.items():
            snap = self._fresh_snapshot(t)
            if not snap:
                continue
            if (best is None
                    or sum(snap.values()) > sum(self._fresh_snapshot(self._trays[best]).values())
                    or (sum(snap.values()) == sum(self._fresh_snapshot(self._trays[best]).values())
                        and t['last_seen'] > self._trays[best]['last_seen'])):
                best = tid
        return best

    def _should_settle_primary(self) -> bool:
        """当前主托盘是否满足进箱条件 (按配置的确认方式组合)。"""
        fb, ab = self.confirm_by_frames, self.confirm_by_action
        if fb and ab:
            if self.confirm_combine == 'and':
                # v3.44.5 场上任一身份有本次动作的稳定快照 = "手接触前数量已
                # 核准", 动作本身就是离场证据, 不再苛求身份消失满帧 (连放场景
                # 下堆顶检测框无缝接上下一盘, 身份永不消失, 死等=合并/漏账)
                if self._action_done_pending \
                        and self._best_fresh_snapshot_tid() is not None:
                    return True
                return self._primary_frames_ok and self._action_done_pending
            return self._primary_frames_ok or self._action_done_pending
        if ab:
            return self._action_done_pending
        return self._primary_frames_ok

    def update(self, tray_dets: list, item_objs: list, current_time: float,
               action_present: bool = False, empty_slot_objs: list = None):
        from backend.api.source_per_item_mixin import _bbox_iou

        # 每帧重算; 无盘/未开门时前端不应看到上一帧残留
        self._slot_view = None

        # -1) 同标签高重叠去重: 模型对密排滑块会输出持续 1s+ 的重复框
        # (7-27 取证: 22 个滑块检出 25 个, 重复对 IoU 0.4-0.6, 窗口众数滤不掉
        # 持续性重复) → 计数前按 item_dedup_iou 去重, 置信度高者优先保留
        # (可关: dedup_items=False 或阈值配 0 都回退到 v3.44 之前的不去重行为,
        #  __init__ 已把两套配置面归一到 item_dedup_iou)
        if item_objs and len(item_objs) > 1 and self.item_dedup_iou > 0:
            _srt = sorted(item_objs,
                          key=lambda o: -(o.get('confidence') or 0.0))
            _kept = []
            for _o in _srt:
                _ob = _o.get('bbox') or {}
                _obt = (_ob.get('x', 0), _ob.get('y', 0),
                        _ob.get('w', 0), _ob.get('h', 0))
                _dup = False
                for _k, _kbt in _kept:
                    if (_k.get('class_name') == _o.get('class_name')
                            and _bbox_iou(_obt, _kbt) > self.item_dedup_iou):
                        _dup = True
                        break
                if not _dup:
                    _kept.append((_o, _obt))
            item_objs = [o for o, _ in _kept]

        # -1b) 空槽框同样去重 (同一个空槽被画两个框会把总数顶过槽位数, 反而
        # 让完整的一帧被门拦掉)
        empty_slot_objs = empty_slot_objs or []
        if len(empty_slot_objs) > 1 and self.item_dedup_iou > 0:
            _kept_e = []
            for _o in sorted(empty_slot_objs,
                             key=lambda o: -(o.get('confidence') or 0.0)):
                _ob = _o.get('bbox') or {}
                _obt = (_ob.get('x', 0), _ob.get('y', 0),
                        _ob.get('w', 0), _ob.get('h', 0))
                if any(_bbox_iou(_obt, _kbt) > self.item_dedup_iou
                       for _, _kbt in _kept_e):
                    continue
                _kept_e.append((_o, _obt))
            empty_slot_objs = [o for o, _ in _kept_e]

        # -1c) 托盘框同帧高重叠去重 (可配, 默认 0=关; dedup_trays=True 未配阈值
        # 时 __init__ 已归一到 0.45): 同一个盘被模型吐两个框 → 关联时一个吸附到
        # 既有身份、另一个新建身份, 一个物理盘挂两张工牌; 空账那张长期在位、带
        # 跨盘旧峰值, 主位一释放就顶上去, 或在救账候选里被捡走造成多记
        # (2026-07-30 探针实测复现)。置信度高者优先保留 (置信度已随检测流传进来,
        # 无 confidence 时全 0, sorted 稳定保序 = 先到者留)。
        if self.tray_dedup_iou > 0 and len(tray_dets) > 1:
            _tkept = []
            for _d in sorted(tray_dets,
                             key=lambda d: -(d.get('confidence') or 0.0)):
                _dbt = (_d.get('x', 0), _d.get('y', 0),
                        _d.get('w', 0), _d.get('h', 0))
                if any(_bbox_iou(_dbt, _kbt) > self.tray_dedup_iou
                       for _, _kbt in _tkept):
                    continue
                _tkept.append((_d, _dbt))
            if len(_tkept) < len(tray_dets):
                try:
                    from backend.core import debug_center
                    if debug_center.is_on("backend.packaging"):
                        debug_center.dbg(
                            "backend.packaging", "托盘框重复检出已去重",
                            f"{len(tray_dets)} → {len(_tkept)} 框 "
                            f"(IoU>{self.tray_dedup_iou:g}) t={current_time:.2f}")
                except Exception:
                    pass
            tray_dets = [d for d, _ in _tkept]

        # 0) 放托盘动作状态机 (仅 confirm_by_action 生效): 驱动屏蔽窗口 + 进箱脉冲
        self._update_action_fsm(action_present, current_time)

        # 1) 托盘检测框关联到已有托盘 (IoU 最高), 否则新建身份
        matched = set()
        for det in tray_dets:
            box = (det['x'], det['y'], det['w'], det['h'])
            best_tid, best_iou = None, self.iou_match
            for tid, t in self._trays.items():
                if tid in matched:
                    continue
                # v3.44.1 换盘围栏 (仅动作确认模式): 已离场超过消失确认帧数的托盘
                # 身份 = "被拿走、在途待记账"的旧盘, 不许再吸附新检测框 — 治取料位
                # 上层盘被拿走后、下层盘在同一位置露出被 IoU 关联到旧身份, 峰值只增
                # 不减把下层盘的 19 抹成上层盘的 24 (SY3 现场 2026-07-22)。
                # 旧盘身份保持可记账(它才是动作放进箱的那盘), 新露出的盘另立新身份。
                # v3.44.2 动作进行中围栏加速: 门槛降为动作消失帧 (与在途定格同门)。
                # 放盘动作期手部遮挡使旧盘断检 <20 帧就露新盘 (7-16 数据集实测 ~0.6s),
                # 围栏按 20 帧永远追不上 → 新盘被粘回旧身份、动作期计数修正落空。
                # 动作期的同位新框几乎必是新盘, 8 帧足够排除纯闪断。
                _fence_gate = (self.action_gone_frames if self._action_in_progress
                               else self.gone_frames)
                if self.confirm_by_action and t['gone'] >= _fence_gate:
                    continue
                tb = t['bbox']
                iou = _bbox_iou(box, (tb['x'], tb['y'], tb['w'], tb['h']))
                if iou >= best_iou:
                    best_iou, best_tid = iou, tid
            bbox = {'x': det['x'], 'y': det['y'], 'w': det['w'], 'h': det['h']}
            if best_tid is not None:
                t = self._trays[best_tid]
                t['bbox'] = bbox
                t['last_seen'] = current_time
                t['gone'] = 0
                matched.add(best_tid)
            else:
                self._seq += 1
                self._trays[self._seq] = {
                    'bbox': bbox, 'first_seen': current_time,
                    'last_seen': current_time, 'gone': 0, 'peak': {},
                    # v3.44.5 稳定计数跟踪 (stable_min_frames>0 时维护):
                    # hist = 滑窗计数史; stable = 账期内最大"稳定窗口众数";
                    # pre_action_* = 动作成立瞬间的稳定计数快照 (记账值)
                    'hist': {}, 'modes': {}, 'stable': {},
                    'pre_action_stable': {}, 'pre_action_ts': None,
                    # v3.46 影子峰值: 不过槽位门的老口径峰值, 仅作兜底
                    'peak_raw': {},
                }
                matched.add(self._seq)

        # 2) 本帧未出现的托盘累加消失帧
        for tid, t in self._trays.items():
            if tid not in matched:
                t['gone'] += 1

        # 2b) v3.46 第一件·空账主位销掉 (可选 purge_empty_primary, 默认关):
        #     老规则对主盘指针指着的身份有清理豁免 — 空账幽灵 (动作期计数冻结
        #     从没数到滑块 / 结账清零后被拿走 / 托盘框误检闪现) 占住指针后离场
        #     十秒也不清, 卡片大数字长期 0 而实时稳定 24 (2026-07-30 现场)。
        #     开了后: 指针指着的身份离场满消失确认帧、峰值空、且不持有本次动作
        #     的稳定快照 (账真的什么都没有) → 照样清掉、指针交出来 (step3 重选)。
        #     动作进行中 / 动作脉冲待配对时不清 — 那两段归结算逻辑管。
        if (self.purge_empty_primary and self._primary is not None
                and self._primary in self._trays
                and not self._action_in_progress
                and not self._action_done_pending):
            _pp = self._trays[self._primary]
            if (_pp['gone'] >= self.gone_frames and not _pp['peak']
                    and not self._fresh_snapshot(_pp)):
                del self._trays[self._primary]
                self._primary = None
                self._primary_frames_ok = False
                try:
                    from backend.core import debug_center
                    if debug_center.is_on("backend.packaging"):
                        debug_center.dbg("backend.packaging", "空账主位身份销掉",
                                         f"t={current_time:.2f} 指针交出重选")
                except Exception:
                    pass

        # 2c) v3.46 第二件·主盘指针让位 (可选 yield_primary, 默认关):
        #     老规则指针只在进箱结账那一刻才可能换指 — 同一物理盘断检超过围栏
        #     帧数被拆成新身份后, 旧身份带残数占指针, 后面每盘的大数字和封箱
        #     凑数都跟旧身份走 (取出重装/搬动/手挡都会触发)。开了后: 指针指着
        #     的身份已判定离场 (动作模式按动作消失帧, 与围栏同门), 且画面上有
        #     在位、账面严格更实的身份 → 指针让给它。动作进行中 / 脉冲待配对
        #     不让 (放盘瞬间锁主位 + 结算改配逻辑的既有保护不动); 严格大于
        #     才让 — 在途满盘(24)不会被下一盘的半账(12)抢走指针。
        if (self.yield_primary and self._primary is not None
                and self._primary in self._trays
                and not self._action_in_progress
                and not self._action_done_pending):
            _yg = (self.action_gone_frames if self.confirm_by_action
                   else self.gone_frames)
            _yp = self._trays[self._primary]
            if _yp['gone'] >= _yg:
                _p_sum = sum(_yp['peak'].values())
                _cands = [(sum(t['peak'].values()), -t['first_seen'], tid)
                          for tid, t in self._trays.items()
                          if tid != self._primary and t['gone'] == 0
                          and t['peak']]
                if _cands:
                    _c_sum, _, _ctid = max(_cands)
                    if _c_sum > _p_sum:
                        try:
                            from backend.core import debug_center
                            if debug_center.is_on("backend.packaging"):
                                debug_center.dbg(
                                    "backend.packaging", "主盘指针让位",
                                    f"{self._primary}(峰值和{_p_sum}, gone={_yp['gone']})"
                                    f" → {_ctid}(峰值和{_c_sum})")
                        except Exception:
                            pass
                        self._primary = _ctid
                        self._primary_frames_ok = False

        # 3) 主托盘选取: 当前主托盘只要还在累积器里 (没被 gone-confirm 移除) 就一直保持,
        #    抗瞬时漏检 / 托盘ID抖动 —— 漏检一两帧不切主、不清峰值, 等真正进箱 (step5
        #    移除) 才按 FIFO 重选下一盘。只有从未选出 / 主托盘已被移除时才重新挑。
        #    重选时优先在场 (gone==0) 的最早托盘; 若全在短暂遮挡也允许挑 gone 最小者顶上。
        # 动作进行中(屏蔽窗口)绝不重选主托盘 — 锁定当前盘, 杜绝放托盘期间被下一盘抢主。
        if (self._primary is None or self._primary not in self._trays) \
                and not self._action_in_progress:
            # v3.44.4 有峰值的真盘优先 (7-23 视频箱1取证): 箱内已放的盘会被持续
            # 检出, 生成"空峰值、出生早"的幽灵身份; 纯 FIFO 会让幽灵抢主位, 真正
            # 看满 24 个滑块的备盘观察者永远排不上, 配对链整个错位 (末盘丢账
            # 72/96 误NG)。峰值非空 = 真装着货的盘, 优先; 同档内仍按 FIFO。
            in_place = [(not self._has_goods(t), t['first_seen'], tid)
                        for tid, t in self._trays.items() if t['gone'] == 0]
            if in_place:
                self._primary = min(in_place)[2]
            elif self._trays:
                self._primary = min(self._trays.items(),
                                    key=lambda kv: (not self._has_goods(kv[1]),
                                                    kv[1]['gone'],
                                                    kv[1]['first_seen']))[0]
            else:
                self._primary = None

        # 4) 托盘内物品计数 (中心包含), 刷新 peak (峰值保持, 抗瞬时漏检)
        cur = {}
        # v3.44.1 在途定格 (仅动作确认模式): 主托盘真离场(超过动作消失帧的容忍) =
        # 已被拿走、在途待记账 — 峰值当即定格, 不再往它头上计数。否则下层盘在同一
        # 位置露出后, 其滑块中心仍落在旧盘 bbox 内, 峰值只增不减会把在途盘的 19
        # 顶成 24。短于容忍帧的检测闪断不触发定格 (峰值保持照旧)。
        primary_fenced = (
            self._primary is not None and self.confirm_by_action
            and self._trays[self._primary]['gone'] >= self.action_gone_frames)
        # v3.44.4 从"每帧只数一个目标"改为"所有在位身份各自按自框计数"(不分常态/动作态):
        # 7-23 视频箱1取证 — 工人连续放盘时动作标签全程不断, 老逻辑动作态只数"动作期
        # 新生的非主位", 备盘区真盘(主位)满24可见却全程没人计数, 拿起前峰值永远是空,
        # 末盘只能靠在途视角救账 (手挡着少1-2个 → 94/96 误NG)。按中心归属计数天然
        # 不串账 (各身份只数落在自己框内的滑块), 峰值各记各的; 唯一排除项是被在途
        # 定格冻结的旧主盘 (其 bbox 已停更, 同位新盘的滑块不许算到它头上)。
        count_tids = [tid for tid, t in self._trays.items()
                      if t['gone'] == 0
                      and not (tid == self._primary and primary_fenced)]
        # 前端展示口径保持旧语义: 常态看主位; 动作态看"动作成立后新生的最早在位身份"
        disp_tid = None
        if self.confirm_by_action and (self._action_in_progress or primary_fenced):
            born_after = self._action_started_ts if self._action_started_ts is not None else 0.0
            cands = [(self._trays[tid]['first_seen'], tid) for tid in count_tids
                     if tid != self._primary
                     and self._trays[tid]['first_seen'] >= born_after]
            if cands:
                disp_tid = min(cands)[1]
        elif self._primary in count_tids:
            disp_tid = self._primary
        primary_counts = {}
        self._tid_cur = {}   # v3.46 各身份当帧计数 (unified_book_source 展示用)
        for count_tid in count_tids:
            pb = self._trays[count_tid]['bbox']
            px1, py1 = pb['x'], pb['y']
            px2, py2 = px1 + pb['w'], py1 + pb['h']
            cnt = {}
            for obj in item_objs:
                lbl = obj.get('class_name', '')
                if not lbl:
                    continue
                # 盘计数模式: 只数配了每盘期望的物品; 总数模式: 整箱只看进箱总数,
                # 所有物品标签都计 (每盘期望可留空, 不再用 item_expected 当"是否计数"开关)
                if self.count_mode != 'items_total' and lbl not in self.item_expected:
                    continue
                b = obj.get('bbox') or {}
                cx = b.get('x', 0) + b.get('w', 0) / 2.0
                cy = b.get('y', 0) + b.get('h', 0) / 2.0
                if px1 <= cx <= px2 and py1 <= cy <= py2:
                    cnt[lbl] = cnt.get(lbl, 0) + 1
            self._tid_cur[count_tid] = dict(cnt)
            # v3.46 槽位完整性门: 货数 + 空槽数 != 槽位总数 → 这一帧没看全这盘,
            # 观察不采信 (先把不过门的影子峰值记下当兜底, 再跳过真峰值/稳定窗口)
            slot_ok = True
            if self.slot_check_on:
                n_empty = 0
                for _e in empty_slot_objs:
                    _eb = _e.get('bbox') or {}
                    _ecx = _eb.get('x', 0) + _eb.get('w', 0) / 2.0
                    _ecy = _eb.get('y', 0) + _eb.get('h', 0) / 2.0
                    if px1 <= _ecx <= px2 and py1 <= _ecy <= py2:
                        n_empty += 1
                n_items = sum(cnt.values())
                slot_ok = (n_items + n_empty) == self.slot_total
                if count_tid == disp_tid or (disp_tid is None
                                             and len(count_tids) == 1):
                    self._slot_view = {
                        'ok': slot_ok, 'items': n_items, 'empty': n_empty,
                        'total': self.slot_total,
                    }
                raw = self._trays[count_tid].setdefault('peak_raw', {})
                for lbl, c in cnt.items():
                    if self.peak_cap > 0 and c > self.peak_cap:
                        c = self.peak_cap
                    if c > raw.get(lbl, 0):
                        raw[lbl] = c
                if not slot_ok:
                    try:
                        from backend.core import debug_center
                        if debug_center.is_on("backend.packaging"):
                            debug_center.dbg(
                                "backend.packaging", "槽位不齐, 本帧不采信",
                                f"tid={count_tid} 货={n_items} 空槽={n_empty} "
                                f"应为 {self.slot_total} t={current_time:.1f}")
                    except Exception:
                        pass
                    if count_tid == disp_tid or (disp_tid is None
                                                 and len(count_tids) == 1):
                        primary_counts = cnt
                    continue

            peak = self._trays[count_tid]['peak']
            for lbl, c in cnt.items():
                # v3.44.4 每盘峰值封顶 (peak_cap, 默认关): 见 __init__ 注释
                if self.peak_cap > 0 and c > self.peak_cap:
                    c = self.peak_cap
                if c > peak.get(lbl, 0):
                    try:
                        from backend.core import debug_center
                        if debug_center.is_on("backend.packaging"):
                            debug_center.dbg(
                                "backend.packaging", "峰值抬升",
                                f"tid={count_tid}"
                                f"{'*P' if count_tid == self._primary else ''} "
                                f"{lbl}: {peak.get(lbl, 0)}→{c} t={current_time:.1f}")
                    except Exception:
                        pass
                    peak[lbl] = c
            # v3.44.5 稳定计数段跟踪 (开关见 __init__):
            # - 稳定段 = 最近 stable_min_frames 帧计数波动 ≤±2 时取窗口众数
            #   (7-27 帧级取证: 真实计数流天然 ±1 抖动 22,23,23,21,23…, "连续
            #   同值"永远凑不齐; 而偶发重复框把单帧数成 24/25, 取最大值会把
            #   23 的盘记成 24 — 众数两头都免疫);
            # - 稳定值 = 近 STABLE_LOOKBACK_S 秒内有效窗口众数的最大值:
            #   遮挡只会看少不会看多 (最大=最全视角, 免疫收尾伸手的"骤降尾巴"
            #   22→20), 重复框已被窗口众数滤掉; 回看有限期让"上一盘刚进箱、
            #   内袋盖住前箱内 24 短暂可见"这类早期余像自动过期, 不压真值 22;
            # - 动作进行中冻结更新 (盘被拿起后堆顶接上的下一盘会提前曝光,
            #   不许把下一盘的满值攒进本盘账期; 记账后账期重开再攒)。
            # 冻结解除条件放宽: 动作标签一缺席就恢复计数 (7-27 取证: 末盘真值
            # 视角常只有"标签消失后~脉冲结算前"的 0.5-1s, 紧凑连放下每帧都金贵,
            # 多冻 1 帧就凑不满稳定窗口)。标签闪断偶漏进的半空视角由窗口波动
            # 检查 (spread≤2) 拒绝; 脉冲判定 (action_gone_frames) 不受影响。
            _count_frozen = (self._action_in_progress
                             and self._action_gone < 1)
            if self.stable_min_frames > 0 and not _count_frozen:
                tr = self._trays[count_tid]
                hist = tr.setdefault('hist', {})
                modes = tr.setdefault('modes', {})
                st = tr.setdefault('stable', {})
                for lbl, c in cnt.items():
                    if self.peak_cap > 0 and c > self.peak_cap:
                        c = self.peak_cap
                    h = hist.setdefault(lbl, [])
                    h.append(c)
                    if len(h) > self.stable_min_frames:
                        h.pop(0)
                    if len(h) >= self.stable_min_frames \
                            and max(h) - min(h) <= 2:
                        from collections import Counter
                        mode = Counter(h).most_common(1)[0][0]
                        ml = modes.setdefault(lbl, [])
                        # 记录时带框中心: 身份 bbox 会漂移 (箱上→堆上), 快照时
                        # 只认与当前位置一致的记录, 时间维度分不开的用位置分
                        _b = tr['bbox']
                        ml.append((current_time, mode,
                                   _b['x'] + _b['w'] / 2.0,
                                   _b['y'] + _b['h'] / 2.0))
                        while ml and ml[0][0] < current_time - self.STABLE_LOOKBACK_S:
                            ml.pop(0)
                        st[lbl] = max(e[1] for e in ml)
                # 本帧没出现的标签窗口中断 (盘空/全遮挡)
                for lbl in list(hist.keys()):
                    if lbl not in cnt:
                        hist.pop(lbl, None)
            if count_tid == disp_tid or (disp_tid is None and len(count_tids) == 1):
                primary_counts = cnt
        cur = primary_counts
        self._cur_counts = cur

        # 5) 进箱判定 — 主托盘按"确认方式"决定进箱; 非主托盘只做幽灵清理。
        #    仅消失满帧(默认)时与改前等价: 主盘 gone>=N → frames_ok → 进箱。
        #    动作确认 / OR / AND 组合见 _should_settle_primary。
        if self._primary is not None:
            pt = self._trays[self._primary]
            if pt['gone'] >= self.gone_frames:
                self._primary_frames_ok = True
            if self._should_settle_primary() and not self._defer_settle_if_unready(pt):
                # v3.44.5 快照持有者优先: 紧凑连放时主位常被在途幽灵抢走 (手里
                # 的盘只看到 3-4 个, 7-27 实测被记成 4/1), 动作成立瞬间核准过
                # 数量的备盘堆身份反而躺在非主位 — 记账认快照不认主位。
                if (self.stable_min_frames > 0 and self.confirm_by_action
                        and self._action_done_pending):
                    _stid = self._best_fresh_snapshot_tid()
                    if _stid is not None and _stid != self._primary:
                        self._primary = _stid
                        pt = self._trays[_stid]
                        try:
                            from backend.core import debug_center
                            if debug_center.is_on("backend.packaging"):
                                debug_center.dbg(
                                    "backend.packaging", "主位改配快照持有者",
                                    f"tid={_stid} snap={self._fresh_snapshot(pt)}")
                        except Exception:
                            pass
                # v3.44.3 空峰值 + 动作脉冲触发 = 主位被幽灵身份占着 (动作期托盘
                # 误检框 / 被拿走清空的旧盘): 只清身份让位, 脉冲保留给正在显形的
                # 新盘 (5c 顶上后照常挂账等峰值结账), 不刷新不应期。修前这里会
                # 静默烧掉脉冲 → 连放末盘永远没进账 (7-23 视频 72/96 连锁崩)。
                # v3.44.4 末盘救账 (盘堆最后一盘, 7-23 视频箱1实测): 拿走盘堆最后
                # 一盘后主位常被"空峰值幽灵"占住, 真正看满 24 个滑块的那盘身份刚
                # 离场、躺在非主位等幽灵清理 — 老逻辑让位保留脉冲, 但后面再无新盘
                # 显形, 这盘的账就永远丢了。脉冲在手且主位空峰值时, 先在账里找
                # "已离场(≥动作消失帧)、有峰值、未记账"的真盘改配记账; 取最近
                # 离场者 (训练端反馈: 多候选取最大会捡走盘堆假消失轨迹)。
                if (not self._has_goods(pt) and self.confirm_by_action
                        and self._action_done_pending):
                    try:
                        from backend.core import debug_center
                        if debug_center.is_on("backend.packaging"):
                            _snap = [
                                (f"tid={_tid}{'*P' if _tid == self._primary else ''} "
                                 f"fs={_t['first_seen']:.1f} gone={_t['gone']} "
                                 f"peak={dict(_t['peak'])} "
                                 f"bbox=({_t['bbox']['x']:.0f},{_t['bbox']['y']:.0f},"
                                 f"{_t['bbox']['w']:.0f}x{_t['bbox']['h']:.0f})")
                                for _tid, _t in self._trays.items()]
                            debug_center.dbg(
                                "backend.packaging", "救账时身份池快照",
                                f"act_ts={self._action_started_ts} | " + " ; ".join(_snap))
                    except Exception:
                        pass
                    # v3.44.4 救账候选 = 与本次动作相关的有峰值身份: 动作窗口内
                    # 新生 (围栏拆出的移动盘/进箱可见拍), 或动作开始时还在场
                    # (刚被拿走的备盘)。时间窗天然排除盘堆陈旧假消失轨迹 (训练端
                    # 警告的风险)。多候选取峰值最大 — 同一物理盘的多段身份 (在途
                    # 视角手挡少 1-2 个 vs 进箱可见拍看全) 里遮挡只会少不会多,
                    # 取最大即取最全视角; 封顶 peak_cap 兜上限。
                    _best = None
                    _th = ((self._action_started_ts - 1.0)
                           if self._action_started_ts is not None else 0.0)
                    for _tid, _t in self._trays.items():
                        if _tid == self._primary or not self._has_goods(_t):
                            continue
                        if _t['first_seen'] < _th and _t['last_seen'] < _th:
                            continue  # 与本次动作无关的陈旧身份
                        _sum_t = sum(self._eff_peak(_t).values())
                        _sum_b = (sum(self._eff_peak(self._trays[_best]).values())
                                  if _best is not None else -1)
                        if (_best is None or _sum_t > _sum_b
                                or (_sum_t == _sum_b
                                    and _t['last_seen'] > self._trays[_best]['last_seen'])):
                            _best = _tid
                    if _best is not None:
                        del self._trays[self._primary]
                        self._primary = _best
                        pt = self._trays[_best]
                        try:
                            from backend.core import debug_center
                            if debug_center.is_on("backend.packaging"):
                                debug_center.dbg(
                                    "backend.packaging", "末盘救账改配离场真盘",
                                    f"peak={dict(pt['peak'])} gone={pt['gone']}")
                        except Exception:
                            pass
                keep_pending = (not self._has_goods(pt) and self.confirm_by_action
                                and self._action_done_pending)
                # v3.44.5 记账值: 本次动作的稳定快照 ("手接触前"核准数) 优先,
                # 没有快照才退回峰值 (老口径)
                _snap_val = self._fresh_snapshot(pt)
                # v3.46 兜底: 开了槽位门但整盘全程没有一帧看全 → 退回不过门的
                # 影子峰值, 最坏情况与门关闭时等价, 绝不因为开门而漏账
                book_val = _snap_val or self._eff_peak(pt)
                if book_val and self._reject_wrong_tray(book_val):
                    # v3.44.1 错盘拦截: 这盘数量不对 → 不记账, 身份照常清掉
                    # (盘已物理进箱, 等工人取出重装); 警报由宿主消费后报警定格。
                    pass
                elif book_val:
                    self._done.append(dict(book_val))
                    print(f"[MixContainer] 托盘进箱: {dict(book_val)}, "
                          f"已装 {len(self._done)}/{self.box_count or '?'}")
                    try:
                        from backend.core import debug_center
                        if debug_center.is_on("backend.packaging"):
                            debug_center.dbg(
                                "backend.packaging", "托盘进箱记账",
                                f"book={dict(book_val)} src={'快照' if _snap_val else '峰值'} "
                                f"done_trays={len(self._done)} "
                                f"item_target={self.item_target} "
                                f"by_frames={self._primary_frames_ok} "
                                f"by_action={self._action_done_pending}")
                    except Exception:
                        pass
                # v3.44.5 结账后身份处置: 快照模式下身份仍在场 (堆顶检测框已无缝
                # 接上露出的下一盘) → 原地清零重开新账, 保持主位不churn; 其余
                # (已离场/未开快照模式) 沿用删除让位。
                if (self.stable_min_frames > 0 and pt['gone'] == 0
                        and self._primary in self._trays):
                    pt['peak'] = {}
                    pt['peak_raw'] = {}
                    pt['hist'] = {}
                    # 重开新账时保留"本次动作开始之后"的观察 (计数在动作进行中
                    # 冻结, 动作开始后的众数全部来自动作结束后已露出的下一盘) —
                    # 紧凑连放时下一次动作 1s 内就来, 这段观察丢了就凑不齐窗口
                    _cut = pt.get('pre_action_ts') or current_time
                    pt['modes'] = {
                        lbl: [e for e in ml if e[0] > _cut]
                        for lbl, ml in (pt.get('modes') or {}).items()}
                    pt['modes'] = {k: v for k, v in pt['modes'].items() if v}
                    pt['stable'] = {}
                    pt['pre_action_stable'] = {}
                    pt['pre_action_ts'] = None
                    pt['first_seen'] = current_time
                else:
                    del self._trays[self._primary]
                    self._primary = None
                self._primary_frames_ok = False
                self._settle_defer_frames = 0
                if not keep_pending:
                    self._action_done_pending = False
                    # 开启动作确认时, 任何一次进箱都刷新不应期基准 — OR 组合下
                    # "消失满帧"先结的账, 紧随其后的动作余波同样不允许再记一笔
                    if self.confirm_by_action:
                        self._last_action_settle_ts = current_time
                else:
                    try:
                        from backend.core import debug_center
                        if debug_center.is_on("backend.packaging"):
                            _snap = {tid: {'gone': t0_['gone'],
                                           'peak': dict(t0_['peak']),
                                           'fs': round(t0_['first_seen'], 2),
                                           'ls': round(t0_['last_seen'], 2)}
                                     for tid, t0_ in self._trays.items()}
                            debug_center.dbg("backend.packaging", "空峰值让位保留脉冲",
                                             f"主位幽灵身份清除, 动作脉冲留给显形中的新盘; "
                                             f"primary={self._primary} 全身份={_snap}")
                    except Exception:
                        pass

        # 5b) 非主托盘幽灵清理: 消失满帧且从未累计滑块(peak 空)的杂框丢弃。
        #     v3.44.2 起动作期新生盘也可能带 peak (屏蔽窗口只冻旧主盘), 带峰值的
        #     非主身份若长期离场 (3 倍消失帧) 同样清 — 防陈旧幽灵日后被选主污账。
        for tid in list(self._trays.keys()):
            if tid == self._primary:
                continue
            gone = self._trays[tid]['gone']
            # v3.46 开了槽位门时, 用"含影子峰值"判有没有装货 — 真盘若一直没等到
            # 一帧完整视角, 真峰值还是空的, 不能把它当空框幽灵清掉
            if gone >= self.gone_frames and not self._has_goods(self._trays[tid]):
                del self._trays[tid]
            elif gone >= self.gone_frames * 3:
                del self._trays[tid]

        # 5c) 主托盘刚进箱被清空 → 把下一盘(FIFO 最早在场)顶上, 避免空窗一帧峰值闪 0。
        #     动作进行中(屏蔽窗口)绝不切主, 等动作结束本盘进箱后再让下一盘上位。
        if self._primary is None and not self._action_in_progress:
            # v3.44.4 与 step3 同步: 有峰值的真盘优先, 空峰值幽灵 (箱内已放盘的
            # 持续检出) 靠后, 防止幽灵抢主位错乱配对链
            in_place = [(not self._has_goods(t), t['first_seen'], tid)
                        for tid, t in self._trays.items() if t['gone'] == 0]
            if in_place:
                self._primary = min(in_place)[2]

        # 5d) 动作脉冲无主托盘可配对(空动作 / 盘尚未上位) → 给一段宽限等新盘露出,
        #     到期仍无盘才丢弃 (v3.44.3 前是当帧即丢 — 连放场景新盘常在脉冲结束后
        #     零点几秒才被检出, 即丢会把最后一盘的账烧掉)。
        if self._primary is None and self._action_done_pending and not self._action_in_progress:
            self._settle_defer_frames += 1
            if self._settle_defer_frames > self.gone_frames * 3:
                self._action_done_pending = False
                self._settle_defer_frames = 0
                try:
                    from backend.core import debug_center
                    if debug_center.is_on("backend.packaging"):
                        debug_center.dbg("backend.packaging", "空动作脉冲宽限到期丢弃",
                                         f"t={current_time:.2f} 无托盘可配对")
                except Exception:
                    pass

    def _defer_settle_if_unready(self, pt: dict) -> bool:
        """v3.44.3 动作结账"峰值就绪等待" (True = 本帧不结账, 继续等).

        连放场景 (工人 ~3s 一盘) 的最后一盘: 放盘动作脉冲结束那一刻, 新盘滑块常被
        手/身体挡着尚未计入峰值 (7-23 视频实测峰值在脉冲结束后 ~1s 才爬完)。改前
        逻辑用空峰值直接消费脉冲并删身份 → 这盘永远没进账, 数量门静默拦收尾步骤,
        周期结不了, 下一箱的账全灌进来 (168/96 连锁崩)。

        仅动作确认脉冲需要等 (消失满帧结账 = 盘已物理离场, 峰值必已定格):
          - 峰值已到本盘期望 → 立即结 (常态连放, 零延迟);
          - 下一个动作已开始 → 立即结 (保持脉冲-账一一配对);
          - 峰值非空但没到期望 → 等满整窗再按现值结 (真少装盘交给错盘拦截/裁决,
            只是晚 ~4.5s 报警; 不做"稳定即结"早退, 末几件检测常断续闪入);
          - 挂满 3 倍消失确认帧仍空峰值 → 清幽灵身份让位 (脉冲保留, 真无盘由
            5d 宽限最终丢弃);
          - 其余 → 继续挂账 (不删身份, 不烧脉冲)。
        """
        if not (self.confirm_by_action and self._action_done_pending):
            return False
        if self._action_in_progress:
            self._settle_defer_frames = 0
            return False
        # v3.44.5 场上任一身份有本次动作的稳定快照 → 账在动作前就核准了,
        # 立即结不等 (结算块会把主位改配给快照持有者)
        if self._best_fresh_snapshot_tid() is not None:
            self._settle_defer_frames = 0
            return False
        if pt['gone'] >= self.gone_frames and self._has_goods(pt):
            return False  # 有峰值且盘已物理离场, 账已定格 → 不等
        peak_total = sum(self._eff_peak(pt).values())
        per_tray = sum(self.item_expected.values())
        booked = sum(sum(t.values()) for t in self._done)
        remaining = self.item_target - booked
        expected_this = (min(per_tray, remaining)
                         if per_tray > 0 and remaining > 0 else 0)
        best_cand = 0
        if peak_total > 0:
            # 峰值非空: 只有"每盘校验开 + 期望已知 + 还没爬到期望"才值得等
            # (给迟到峰值机会, 免得半爬的 21/24 触发错盘误报); 其余按老行为立即结。
            # 注意不做"峰值稳定即结"早退 — 7-23 视频实测末几个滑块的检测断续闪入,
            # 21 可以停稳 1 秒以上才跳 24, 等满整窗才结 (真少装只是晚 ~4.5s 报警)。
            if not (self.per_tray_guard and expected_this > 0
                    and peak_total < expected_this):
                self._settle_defer_frames = 0
                return False
        else:
            # v3.44.4 空峰值幽灵主位挂账等真账 (7-23 视频箱1取证): 末盘的真账
            # (进箱可见拍) 常在脉冲结束后 0.5~1.5s 才显形, 立即结只能救到在途
            # 视角 (手挡着少 1-2 个)。有救账候选爬满本盘期望 → 立即结 (结算分支
            # 的救账改配它); 没爬满等满窗, 到期只要有峰值候选照样结给救账。
            for _tid, _t in self._trays.items():
                if _tid == self._primary or not self._has_goods(_t):
                    continue
                best_cand = max(best_cand, sum(self._eff_peak(_t).values()))
            if expected_this > 0 and best_cand >= expected_this:
                self._settle_defer_frames = 0
                return False
        self._settle_defer_frames += 1
        if self._settle_defer_frames > self.gone_frames * 3:
            self._settle_defer_frames = 0
            if not pt['peak']:
                if best_cand > 0:
                    return False  # 有峰值候选 → 按现值结账, 交给救账改配
                # 到期主位仍空峰值且无候选 (幽灵占位/从未显形): 清身份让位、
                # 脉冲保留, 显形中的新盘顶上后重新挂账; 真无盘由 5d 宽限最终丢弃
                del self._trays[self._primary]
                self._primary = None
                self._primary_frames_ok = False
                return True
            return False  # 有峰值但没到期望 → 按现值结账 (少装由 guard/裁决处置)
        try:
            from backend.core import debug_center
            if debug_center.is_on("backend.packaging") and self._settle_defer_frames == 1:
                debug_center.dbg("backend.packaging", "动作结账挂起等峰值",
                                 f"peak={dict(pt['peak'])} expected_this={expected_this}")
        except Exception:
            pass
        return True

    def _reject_wrong_tray(self, peak: dict) -> bool:
        """v3.44.1 每盘数量校验 (True = 错盘, 这盘不记账并抛警报).

        仅 items_total 模式 + per_tray_guard 开 + 配了每盘期望时生效:
          本盘期望 = min(每盘期望总数, 整箱目标 - 已进箱总数) — 尾盘自动按
          "补齐总数的余数"核 (用户敲定: 尾盘只算总数对不对得上, 不卡每盘 24)。
          进箱已满 (余数<=0) 不核 — 那属"多装/下一箱"形态, 归结算裁决管。
        """
        if not (self.per_tray_guard and self.count_mode == 'items_total'):
            return False
        per_tray = sum(self.item_expected.values())
        if per_tray <= 0 or self.item_target <= 0:
            return False
        booked = sum(sum(t.values()) for t in self._done)
        remaining = self.item_target - booked
        if remaining <= 0:
            return False
        expected_this = min(per_tray, remaining)
        count_this = sum(peak.values())
        if count_this == expected_this:
            return False
        self.wrong_tray_alert = {
            'index': len(self._done) + 1,
            'count': count_this,
            'expected': expected_this,
        }
        print(f"[MixContainer] 错盘拦截: 第{len(self._done) + 1}盘数量不对 "
              f"{count_this}/{expected_this}, 不记账 (已进箱 {booked}/{self.item_target})")
        return True

    def consume_wrong_tray_alert(self):
        """宿主每帧消费错盘警报 (取走即清, 无警报返回 None)。"""
        alert, self.wrong_tray_alert = self.wrong_tray_alert, None
        return alert

    def _book_candidate_tid(self):
        """v3.46 第三件·"结账此刻真正会被选中的身份" (与 update step5 的改配
        顺序同步: 本次动作的快照持有者 > 主盘指针)。展示三数与封箱凑数统一问它,
        杜绝"大数字问指针、实时问别的盘"的两身份数字混排。"""
        if self.stable_min_frames > 0 and self.confirm_by_action:
            _stid = self._best_fresh_snapshot_tid()
            if _stid is not None:
                return _stid
        return self._primary

    def _fold_tid(self):
        """封箱凑数/展示取值的身份: unified_book_source 开 → 结账候选;
        关 → 主盘指针 (老口径零差异)。"""
        if self.unified_book_source:
            cand = self._book_candidate_tid()
            if cand is not None and cand in self._trays:
                return cand
        return self._primary

    def _primary_foldable(self) -> bool:
        """结算折算守门: 在位主托盘可否折进本箱裁决。

        v3.44.4: 动作确认模式下, 在位主盘只有"真在途" (动作脉冲待配对 / 已消失
        满帧) 才允许折进本箱 — 否则它是"下一箱已备好未动的盘" (上银 7-27 视频:
        封箱时点位上摆着下一箱首盘 peak=24, 折进来把 90/96 不足抹成 114/96
        超出, NG 语义整个反了)。非动作确认模式保持老行为 (无脉冲概念, 零差异)。
        """
        if self._primary is None:
            return False
        if not self.confirm_by_action:
            return True
        # v3.44.5 快照模式下记账随脉冲即时落定, 封箱时末盘早已入账 — 在位
        # 主盘只有"脉冲在途" (记账真在飞行中) 才许折算。放开 frames_ok 腿会
        # 把桌角待用盘碎片 (peak=4, 永驻在位) 折进裁决, 94/96 不足被抹成
        # 98/96 超出 (7-27 UAT 箱3)。
        if self.stable_min_frames > 0:
            return bool(self._action_done_pending)
        return bool(self._action_done_pending or self._primary_frames_ok)

    def _trays_for_verdict(self):
        """封箱裁决用的托盘全集: 已装清单 + 当前主托盘 (最后一盘可能还没 gone-confirm)。"""
        trays = list(self._done)
        if self._primary_foldable():
            peak = self._trays.get(self._fold_tid(), {}).get('peak')
            if peak:
                trays.append(dict(peak))
        return trays

    def verdict(self, display_map: dict):
        dm = display_map or {}
        reasons = []
        # 总数模式: 不卡盘数/每盘, 只判进箱滑块总数是否正好等于整箱目标。
        # 关键: 进箱总数 = 已 gone-confirm 进箱的各盘 (self._done)。
        # 当前在位主托盘"是否计入本箱"取决于本箱有没有装够:
        #   - 还没装够 (done_total < target): 末盘可能刚进箱还没确认, 把在位这盘补进来凑数;
        #   - 已经装够 (done_total >= target): 在位的是"下一箱"的第一盘, 绝不计入本箱,
        #     否则会把下一盘也算进来误判超出 (治现场"4盘96+下一盘已上桌→120 NG")。
        if self.count_mode == 'items_total':
            # 整箱只看进箱总数 = 各盘峰值跨所有物品标签求和 (与每盘期望 item_expected 无关)。
            target = self.item_target
            done_total = sum(sum(t.values()) for t in self._done)
            total = done_total
            if target > 0 and done_total < target and self._primary_foldable():
                cur_peak = self._trays.get(self._fold_tid(), {}).get('peak', {}) or {}
                total += sum(cur_peak.values())
            if target > 0 and total != target:
                rel = '不足' if total < target else '超出'
                reasons.append(f'箱内总数{rel} {total}/{target}')
            return (not reasons), reasons
        # 盘计数模式 (现状): 盘数够 + 每盘达每盘门槛
        trays = self._trays_for_verdict()
        if self.box_count > 0 and len(trays) < self.box_count:
            reasons.append(f'托盘数不足 {len(trays)}/{self.box_count} 盘')
        for idx, tray in enumerate(trays, 1):
            for lbl, exp in self.item_expected.items():
                got = tray.get(lbl, 0)
                if got < exp:
                    reasons.append(f'第{idx}盘 {dm.get(lbl, lbl)} 不足 {got}/{exp}')
        return (not reasons), reasons

    def set_item_target(self, target: int):
        """外部 (包装结算协调器) 反向设"当前箱滑块目标" — 普通箱=每箱数 / 尾箱=余数。

        让 verdict 始终用当前箱的真实目标判合格 (尾箱不再被按整箱数误判 NG)。
        仅 items_total 模式有意义; trays 模式调了也无副作用。
        """
        self.item_target = max(0, int(target or 0))
        try:
            from backend.core import debug_center
            if debug_center.is_on("backend.packaging"):
                debug_center.dbg("backend.packaging", "容器累加器设箱目标",
                                 f"item_target={self.item_target} mode={self.count_mode}")
        except Exception:
            pass

    def settled_item_total(self) -> int:
        """本周期已进箱滑块总数 (跨标签求和), 口径与 verdict 完全一致。

        供包装结算协调器在周期结算时读取 (= 这一箱实际进了多少滑块)。
        与 verdict 同源: 已 gone-confirm 进箱各盘峰值之和; 若本箱还没装够,
        把在位主托盘 (可能末盘还没确认) 也补进来凑数。
        """
        # 跨所有物品标签求和 (与 verdict 同源, 不依赖每盘期望 item_expected)
        done_total = sum(sum(t.values()) for t in self._done)
        total = done_total
        if self.item_target <= 0 or done_total < self.item_target:
            cur_peak = {}
            if self._primary_foldable():
                cur_peak = self._trays.get(self._fold_tid(), {}).get('peak', {}) or {}
            total += sum(cur_peak.values())
        return total

    def booked_item_total(self) -> int:
        """仅"已确认进箱"各盘峰值之和 — 不含在位主托盘的凑数。

        v3.44 收尾防呆数量门专用口径: 备盘区摆着一整盘没进箱时, verdict 口径
        (settled_item_total) 会把它凑进总数, 数量门被"看起来已满"骗过 —
        上银现场视频二 (漏装第四盘就放油嘴包) 实测放行漏报。收尾动作问的是
        "箱里真装够了吗", 只能认已记账的。
        """
        return sum(sum(t.values()) for t in self._done)

    def pending_booking_peak_total(self) -> int:
        """在途主盘峰值 (动作已成立/帧数已达标、记账仍在延迟窗内的那盘)。

        v3.44.3 数量门竞态补丁: 第4盘放入后记账走峰值就绪等待 (最多 ~4.5s),
        紧跟着的放油嘴包脉冲只有 ~0.5s — 若门只认已记账数会把这一步误拦掉
        (7-23 视频 UAT + 客户现场"检测到但不计数"同款)。该盘已物理进箱、
        峰值真实可见, 数量门应把它计入"箱内已有"; 真少装 (19/24) 凑不满照拦。
        """
        if self._primary is None:
            return 0
        if not (self._action_done_pending or self._primary_frames_ok):
            return 0
        peak = self._trays.get(self._fold_tid(), {}).get('peak', {}) or {}
        return sum(peak.values())

    def to_state(self, display_map: dict):
        dm = display_map or {}
        cur = self._cur_counts or {}
        # 实时卡 = 当帧真实检测数 (检到几个显示几个, 没检到就掉, 不 hold);
        # 当前主托盘的峰值仅作参考下发, 真正"记峰值"发生在进箱那一刻 (见 update step5)。
        peak = {}
        if self._primary is not None:
            peak = self._trays.get(self._primary, {}).get('peak', {}) or {}
        # v3.46 第三件·三数同源 (可选 unified_book_source, 默认关):
        # 老口径的实时/峰值/预计进箱可能来自两张不同工牌 (峰值问指针, 实时在
        # 指针漏检帧临时问画面上唯一在位的盘) → 屏幕出现 8 与 23 并排的矛盾。
        # 开了后三个数统一问"结账候选" (与封箱凑数 _fold_tid 同一张工牌)。
        _src_tid = None
        if self.unified_book_source:
            _src_tid = self._fold_tid()
            if _src_tid is not None and _src_tid in self._trays:
                cur = self._tid_cur.get(_src_tid, {}) or {}
                peak = self._trays[_src_tid].get('peak', {}) or {}
        # 展示标签集: 盘计数模式沿用每盘期望清单 (零差异);
        # 总数模式期望可留空, 改取实际计到的物品标签 (实时/峰值/已进箱)
        disp_labels = list(self.item_expected.keys())
        if self.count_mode == 'items_total':
            for lbl in list(cur.keys()) + list(peak.keys()):
                if lbl and lbl not in disp_labels:
                    disp_labels.append(lbl)
            for t in self._done:
                for lbl in t.keys():
                    if lbl and lbl not in disp_labels:
                        disp_labels.append(lbl)
        # v3.44.6 预计进箱值: 与记账取值规则严格同源 (稳定快照优先, 无稳定值退
        # 峰值, 见 update step5 的 book_val = 快照 or peak) — 开了"动作前稳定
        # 计数"后记账不再等于峰值, 操作员在卡片上要能提前看到"这盘会记几个"。
        stable = {}
        _stable_tid = (_src_tid if (self.unified_book_source
                                    and _src_tid is not None)
                       else self._primary)
        if self.stable_min_frames > 0 and _stable_tid is not None:
            stable = self._trays.get(_stable_tid, {}).get('stable', {}) or {}
        cur_items = [{
            'label': lbl,
            'display_name': dm.get(lbl, lbl),
            'current_count': cur.get(lbl, 0),     # 当帧实时数 (展示主数字)
            'peak_count': peak.get(lbl, 0),       # 当前托盘在位峰值 (参考)
            'book_preview': stable.get(lbl) or peak.get(lbl, 0),  # 预计进箱记的值
            'expected_per_tray': self.item_expected.get(lbl, 0),
        } for lbl in disp_labels]
        state = {
            'enabled': True,
            'count_mode': self.count_mode,
            'container_label': self.container_label,
            'container_display': dm.get(self.container_label, self.container_label),
            'box_count': self.box_count,
            'trays_done': len(self._done),
            'current_tray_items': cur_items,
            'done_detail': self._done,
        }
        if self.count_mode == 'items_total':
            # 已进箱滑块总数 (按 label 累加各盘峰值) + 整箱目标
            totals = {}
            for t in self._done:
                for lbl, c in t.items():
                    totals[lbl] = totals.get(lbl, 0) + c
            state['item_total_done'] = totals
            state['item_target'] = self.item_target
        # v3.46 槽位完整性: 让操作员一眼看出"这一帧算不算数" (门没开则不下发)
        if self.slot_check_on and self._slot_view is not None:
            state['slot_view'] = dict(self._slot_view)
        return state


# ============================================================
# 混合跟踪: 真跟踪引擎 (周期主权外移)
# ============================================================


class _TrackingMixEngine:
    """驱动宿主真跟踪机械的薄编排层。

    宿主 (VideoSourceManager) 的 MRO 已含 TrackingMixin / ChecklistMixin,
    所有状态字典 (_tracking_objects / _event_* / _stack_* ...) 在自定义模式下
    本就闲置 — 本引擎按帧调用真方法, 把作用域裁剪到物品行标签。
    不做的事 (周期主权归步骤侧):
      - 不调 _tracking_check_settlement (四种周期结束策略)
      - 不调 _update_container_grouping / _scan_d_update (容器=周期策略, 混合下无意义)
      - 自动开周期由 host._tracking_external_cycle 在真机械内守门跳过
    """

    def __init__(self, item_cfgs: list, container_cfg: dict = None):
        self.items = {}
        for cfg in item_cfgs:
            self.items[cfg['label']] = cfg
        self.item_labels = frozenset(self.items.keys())
        # 托盘容器累加器 (可选): 配了容器标签才启用, 否则 None = 走原满盘门/计数路径 (零差异)
        self._container = None
        if container_cfg and container_cfg.get('label'):
            self._container = _ContainerAccumulator(
                container_label=container_cfg['label'],
                item_expected=container_cfg.get('item_expected', {}),
                box_count=container_cfg.get('box_count', 0),
                gone_frames=container_cfg.get('gone_frames', 30),
                iou_match=container_cfg.get('iou_match', 0.3),
                count_mode=container_cfg.get('count_mode', 'trays'),
                item_target=container_cfg.get('item_target', 0),
                confirm_by_frames=container_cfg.get('confirm_by_frames', True),
                confirm_by_action=container_cfg.get('confirm_by_action', False),
                action_label=container_cfg.get('action_label', ''),
                confirm_combine=container_cfg.get('confirm_combine', 'or'),
                action_min_frames=container_cfg.get('action_min_frames', 3),
                action_gone_frames=container_cfg.get('action_gone_frames', 8),
                action_cooldown_s=container_cfg.get('action_cooldown_s', 2.0),
                per_tray_guard=container_cfg.get('per_tray_guard', False),
                peak_cap=container_cfg.get('peak_cap', 0),
                stable_min_frames=container_cfg.get('stable_min_frames', 0),
                dedup_items=container_cfg.get('dedup_items', True),
                dedup_trays=container_cfg.get('dedup_trays', False),
                purge_empty_primary=container_cfg.get('purge_empty_primary', False),
                yield_primary=container_cfg.get('yield_primary', False),
                unified_book_source=container_cfg.get('unified_book_source', False),
                slot_check_label=container_cfg.get('slot_check_label', ''),
                slot_total=container_cfg.get('slot_total', 0),
                item_dedup_iou=container_cfg.get('item_dedup_iou', 0.45),
                tray_dedup_iou=container_cfg.get('tray_dedup_iou', 0.0),
            )
        # 静态期望清单 (verdict 用, 不依赖喂帧): 与真 loader 的注入规则一致 —
        # event 行 → event_required_count; 堆叠行 → stack_required_count;
        # 普通跟踪计数行 → expected_count (0 = 只展示不判定)
        self.expected_items = {}
        for label, cfg in self.items.items():
            mode = cfg.get('count_mode') or 'track'
            if mode == 'event':
                self.expected_items[label] = max(1, int(cfg.get('event_required_count') or 1))
            elif cfg.get('stack_enabled'):
                self.expected_items[label] = max(2, int(cfg.get('stack_required_count') or 2))
            else:
                exp = int(cfg.get('expected_count') or 0)
                if exp > 0:
                    self.expected_items[label] = exp

    # ---- 周期重置: 只清物品侧运行时状态, 步骤侧/容器/周期字段一概不碰 ----
    _HOST_DICT_ATTRS = (
        '_tracking_objects', '_tracking_class_counters', '_tracking_display_map',
        '_tracking_lost_frames', '_tracking_letter_map', '_tracking_item_checklist',
        '_tracking_recently_lost', '_tracking_transferred_ids',
        '_tracking_prev_positions', '_tracking_appearance',
        '_tracking_stable_frames', '_tracking_locked_ids',
        '_tracking_registered_positions',
        '_event_counters', '_event_state', '_event_visible_frames',
        '_event_gone_frames_count', '_event_first_seen', '_event_last_seen',
        '_stack_state', '_stack_counters', '_stack_disappeared_at',
        '_stack_visible_frames',
        '_stack_sat_frames', '_stack_latched', '_stack_phase_peak',
        '_stack_partials',
    )

    def reset_host_state(self, host):
        for attr in self._HOST_DICT_ATTRS:
            d = getattr(host, attr, None)
            if isinstance(d, dict):
                d.clear()
        host._tracking_letter_idx = 0
        host._tracking_order_seq = 0
        if self._container is not None:
            self._container.reset()

    def feed(self, host, detections: list, current_time: float, original_frame=None):
        # 幂等声明: 周期主权在外部 — 真机械内的自动开周期一律跳过
        host._tracking_external_cycle = True

        # 物品标签过滤 + 步骤置信度阈值守门 (与步骤侧同一套语义);
        # 物品流的逐行 ROI 守门交给真机械内部的 _det_passes_roi_for_label
        # (in_roi 标志), 容器/动作标签不进跟踪机械 → 在本入口过 ROI
        # (v3.44.4: 此前记账链完全不吃 ROI, 备盘堆的托盘/滑块只能靠主盘
        # 归属兜底 — 画了 ROI 也拦不住身份漂移, 上银 7-23 视频超计同源).
        conf_map = getattr(host, 'step_conf_thresholds', None) or {}
        poly_map = getattr(host, 'step_roi_polygons', None) or {}

        def _in_roi(det, label):
            poly = poly_map.get(label)
            if poly and len(poly) >= 3:
                return is_normalized_bbox_center_in_polygon(det, poly)
            return True

        dets = []
        tray_dets = []  # 容器累加器用: 托盘检测框 (与物品流隔离, 不进跟踪机械)
        container_label = self._container.container_label if self._container else None
        action_label = getattr(self._container, 'action_label', '') if self._container else ''
        action_present = False  # 本帧"放托盘"动作标签是否在场 → 驱动动作状态机
        # v3.46 空槽标签: 与动作标签同级的旁路信号 — 不进物品流、不进跟踪机械、
        # 不计入任何记账总数, 只喂给槽位完整性门判断"这一帧看全了没有"
        slot_label = getattr(self._container, 'slot_check_label', '') if self._container else ''
        empty_slot_objs = []
        for det in detections or []:
            label = det.get('label', '')
            if slot_label and label == slot_label:
                threshold = conf_map.get(label)
                if ((threshold is None or det.get('confidence', 0) >= threshold)
                        and _in_roi(det, label)):
                    empty_slot_objs.append({
                        'bbox': {
                            'x': float(det.get('x', 0)), 'y': float(det.get('y', 0)),
                            'w': float(det.get('w', 0)), 'h': float(det.get('h', 0)),
                        },
                        'confidence': det.get('confidence', 0),
                    })
                continue
            if container_label and label == container_label:
                threshold = conf_map.get(label)
                if threshold is not None and det.get('confidence', 0) < threshold:
                    continue
                if not _in_roi(det, label):
                    continue
                tray_dets.append({
                    'x': float(det.get('x', 0)), 'y': float(det.get('y', 0)),
                    'w': float(det.get('w', 0)), 'h': float(det.get('h', 0)),
                    # v3.46 托盘置信度进记账链: 重复框去重时置信度高者保留
                    'confidence': float(det.get('confidence', 0) or 0.0),
                })
                continue
            if action_label and label == action_label:
                # 放托盘动作标签: 仅作"本帧在场"信号, 不进物品流/跟踪机械
                threshold = conf_map.get(label)
                if ((threshold is None or det.get('confidence', 0) >= threshold)
                        and _in_roi(det, label)):
                    action_present = True
                continue
            if label not in self.item_labels:
                continue
            threshold = conf_map.get(label)
            if threshold is not None and det.get('confidence', 0) < threshold:
                continue
            dets.append(det)

        # 1) 真 loader 解析 steps_config (会把 event/stack 期望注入 expected),
        #    随后作用域裁剪到物品行 — 步骤行的任何残留配置不进入跟踪机械
        expected = dict(self.expected_items)
        cfg = host._tracking_load_step_config(expected)
        for key in ('per_class_lost_sec', 'per_class_position_lock',
                    'event_steps', 'stack_steps', 'max_recognized_per_label'):
            cfg[key] = {k: v for k, v in cfg[key].items() if k in self.item_labels}
        expected = {k: v for k, v in expected.items() if k in self.item_labels}

        pcfg = (host.project_config or {}).get('pipeline_config', {}) or {}
        swap_detection = pcfg.get('tracking_swap_detection', False)
        appearance_match = pcfg.get('tracking_appearance_match', False)
        id_lock = pcfg.get('tracking_id_lock', False)
        id_lock_frames = pcfg.get('tracking_id_lock_frames', 15)

        # 2) 最大识别数后处理 (改本帧 detections 的 track_id, 与独立模式一致)
        host._tracking_apply_max_recognized(dets, cfg['max_recognized_per_label'])

        # 3) 帧检测分类 (无 trigger 标签 — 周期策略已被砍掉)
        frame_detections, _trigger_visible, event_labels_seen = \
            host._tracking_collect_frame_dets(dets, '', 'all_gone', cfg['event_steps'])

        seen_track_ids = set()
        pos_lock_assigned_dids = set()

        # 4) Phase 1: 位置锁匹配
        pos_lock_handled_tids = host._tracking_phase1_position_lock(
            frame_detections, cfg['per_class_position_lock'], current_time,
            'all_gone', seen_track_ids, pos_lock_assigned_dids)

        # 5) Phase 2: track_id 匹配 (id_lock / recently_lost re-ID / 新分配)
        host._tracking_phase2_id_match(
            frame_detections, pos_lock_handled_tids, cfg['per_class_position_lock'],
            id_lock, id_lock_frames, appearance_match, cfg['max_lost_sec'],
            current_time, 'all_gone', seen_track_ids, pos_lock_assigned_dids,
            expected, original_frame)

        # 6) 抗闪烁: ID Lock + Swap Detection + Appearance Match
        host._tracking_apply_anti_flicker(
            seen_track_ids, cfg['per_class_position_lock'],
            id_lock, id_lock_frames, swap_detection, appearance_match, original_frame)

        # 7) 失帧累加 + 过期清理
        host._tracking_increment_lost_expire(
            seen_track_ids, cfg['per_class_lost_sec'], cfg['max_lost_sec'], current_time)

        # 8) 动作计数 FSM
        host._tracking_run_event_fsm(cfg['event_steps'], event_labels_seen, current_time)

        # 9) 堆叠 FSM
        host._tracking_run_stack_fsm(cfg['stack_steps'], dets, current_time)

        # 10) 物品截图 (限频 1Hz, 给前端清单卡片)
        if original_frame is not None:
            host._tracking_capture_screenshots(seen_track_ids, original_frame)

        # 11) 清单重建 (前端"物品清点"展示直接复用)
        host._rebuild_checklist(expected)

        # 12) 托盘容器累加: 物品按主托盘分组 + 托盘进箱记账 (不碰周期主权)
        #     用「当前帧原始检测框」计数 (= 同帧同时出现的滑块数), 取在位峰值;
        #     不用 _tracking_objects — 唯一ID跟踪在 24 个密集小目标上会塌缩成
        #     个位数 (ByteTrack 只保住几个稳定 ID), 与"同时最多那帧的数量"不是一回事。
        if self._container is not None:
            # v3.44.4: 物品喂容器记账前过 ROI — 跟踪机械里 ROI 只影响 in_roi
            # 计数口径, 记账链此前拿的是未过滤 dets, 备盘堆滑块会污染箱账.
            # 只滤记账支流, 不动跟踪机械输入 (身份保持行为零差异).
            item_dets_for_container = [{
                'class_name': d.get('label', ''),
                'bbox': {
                    'x': float(d.get('x', 0)), 'y': float(d.get('y', 0)),
                    'w': float(d.get('w', 0)), 'h': float(d.get('h', 0)),
                },
            } for d in dets if _in_roi(d, d.get('label', ''))]
            self._container.update(tray_dets, item_dets_for_container, current_time,
                                   action_present=action_present,
                                   empty_slot_objs=empty_slot_objs)
            # v3.44.1 错盘警报消费: 数量不对的盘刚被拒账 → 借收尾防呆提示事件
            # 报警 (事件配了「需人工确认」则整线定格, 工人取出错盘、确认后重装;
            # 配「确认后保留周期」可断点续做)。异常隔离, 绝不打断检测热路径。
            alert = self._container.consume_wrong_tray_alert()
            if alert is not None and host is not None:
                try:
                    host._fire_closing_guard_alarm(
                        f"收尾防呆: 第{alert['index']}盘数量不对 "
                        f"{alert['count']}/{alert['expected']} — 该盘未记账, "
                        f"请取出该盘, 确认后重新装",
                        event_id=getattr(host, '_settle_hold_event_id', None))
                except Exception as _wt_e:
                    print(f"[MixContainer] 错盘报警失败 (隔离): {_wt_e}")

    # ---- 合并计数: 与独立模式 _rebuild_checklist 同一公式 ----
    @staticmethod
    def _merged_counters(host):
        merged = {}
        track_counters = getattr(host, '_tracking_class_counters', {}) or {}
        stack_counters = getattr(host, '_stack_counters', {}) or {}
        event_counters = getattr(host, '_event_counters', {}) or {}
        for label in set(track_counters) | set(stack_counters) | set(event_counters):
            merged[label] = (max(track_counters.get(label, 0), stack_counters.get(label, 0))
                             + event_counters.get(label, 0))
        return merged

    def verdict(self, host):
        if host is None:
            return True, []
        display_map = getattr(host, 'step_display_names', {}) or {}
        # 容器模式: 裁决改为"每托盘是否数满 + 整箱托盘数是否够", 不卡全局累计总数
        if self._container is not None:
            return self._container.verdict(display_map)
        merged = self._merged_counters(host)
        reasons = []
        for label, exp in self.expected_items.items():
            cfg = self.items.get(label, {})
            actual = merged.get(label, 0)
            display = display_map.get(label, cfg.get('display', label))
            # 满盘门模式: 只验"每盘是否数满", 任一盘短即 NG, 不卡累计总数
            if cfg.get('stack_enabled') and cfg.get('stack_gate_only'):
                partials = self._stack_partials_with_live(host, label)
                if partials:
                    detail = ', '.join(f"{p['peak']}/{p['required']}" for p in partials)
                    reasons.append(f'[{display}] 有未数满的盘: {detail}')
                continue
            if actual < exp:
                msg = f'[{display}] 数量不足 {actual}/{exp}'
                # 堆叠批层模式: 附上"哪批没数够"的可解释明细
                partials = self._stack_partials_with_live(host, label)
                if partials:
                    detail = ', '.join(f"{p['peak']}/{p['required']}" for p in partials)
                    msg += f' (不完整批次: {detail})'
                reasons.append(msg)
            elif actual > exp:
                reasons.append(f'[{display}] 数量超出期望 ({actual} > {exp})')
        return (not reasons), reasons

    def set_container_item_target(self, target):
        """供包装结算反向设当前箱滑块目标 (无容器时 no-op)。"""
        if self._container is not None:
            self._container.set_item_target(target)

    def container_settled_item_total(self):
        """本周期已进箱滑块总数 (无容器时 None)。"""
        if self._container is not None:
            return self._container.settled_item_total()
        return None

    def container_item_target(self):
        """当前箱滑块目标 (无容器/未设目标时 None)。收尾防呆数量门用。"""
        if self._container is not None:
            t = getattr(self._container, 'item_target', None)
            return int(t) if t else None
        return None

    def container_booked_item_total(self):
        """仅已确认进箱的滑块总数 (不含在位托盘凑数; 无容器时 None)。数量门口径。"""
        if self._container is not None:
            return self._container.booked_item_total()
        return None

    def container_pending_peak_total(self):
        """在途主盘峰值 (记账在延迟窗内的那盘; 无容器时 None)。数量门竞态补丁。"""
        if self._container is not None:
            return self._container.pending_booking_peak_total()
        return None

    @staticmethod
    def _stack_partials_with_live(host, label):
        """已落账的不完整批次 + 当前在场/刚离场但还没闩锁的批次 (结算时最后一批
        不达标还没等到"下一批开始"落账, 这里折进去保证可解释性完整)。

        委托给宿主的 _stack_collect_partials — 与独立模式满盘门同一口径单点维护。"""
        if host is None:
            return []
        return host._stack_collect_partials(label)

    def to_state(self, host):
        merged = self._merged_counters(host) if host is not None else {}
        display_map = (getattr(host, 'step_display_names', {}) or {}) if host is not None else {}
        items = []
        for label, cfg in self.items.items():
            mode = cfg.get('count_mode') or 'track'
            role = 'event' if mode == 'event' else ('stack' if cfg.get('stack_enabled') else 'track')
            row = {
                'label': label,
                'display_name': display_map.get(label, cfg.get('display', label)),
                'role': role,
                'expected_count': self.expected_items.get(label, 0),
                'seen_count': merged.get(label, 0),
            }
            if role == 'stack' and host is not None:
                # 批层模式: 前端可显示 "N 批未达标 (峰值/要求)"
                row['partials'] = self._stack_partials_with_live(host, label)
            items.append(row)
        checklist = {}
        if host is not None:
            checklist = dict(getattr(host, '_tracking_item_checklist', {}) or {})
        state = {'mix_type': 'tracking', 'items': items, 'checklist': checklist}
        if self._container is not None:
            state['container'] = self._container.to_state(display_map)
        return state


# ============================================================
# 混合逐件: 真逐件引擎 (个体锁定 + 覆盖配对, 周期主权外移)
# ============================================================


class _PerItemMixEngine:
    """驱动真逐件个体状态机 (_PerItemStep) 的薄编排层。

    复用 (与独立逐件模式同一份代码):
      - _PerItemStep: 个体表/跨帧位置匹配 (IoU)/目标⟶动作覆盖配对/
        持续帧确认/覆盖单调性/虚拟漏件 (expected_count)/完成判定
      - PerItemMixin._per_item_absorb_new_items: 固定数量模式的补锁定吸收
      - PerItemMixin._collect_item_boxes: 多标签 OR 合并
    不做的事 (周期主权归步骤侧):
      - 不跑稳定窗口开周期 / 收尾标签 / 完成即结算 / 双超时 / 手动结算
      - 个体吸收语义: expected_count>0 行走"补锁定"路径 (整周期吸收新位置,
        到期望数封顶 — 抗误检幻影个体); auto 行走 dynamic 路径 (随见随建 +
        item_timeout 清理), 与独立模式的两种哲学一一对应。
    """

    def __init__(self, item_cfgs: list, item_timeout_seconds: float):
        # 延迟导入避免环 (per_item mixin 不依赖本模块)
        from backend.api.source_per_item_mixin import _PerItemStep
        self.steps = []
        for raw in item_cfgs:
            self.steps.append(_PerItemStep(raw))
        self.item_timeout_seconds = max(0.0, float(item_timeout_seconds or 0.0))
        # 引擎监听的标签全集 (行标签 + 个体标签 + 动作标签)
        watch = set()
        for s in self.steps:
            if s.step_label:
                watch.add(s.step_label)
            watch.update(s.item_label)
            if s.action_label:
                watch.add(s.action_label)
        self.watch_labels = frozenset(watch)
        self._frame_id = 0
        self.last_ng_detail = None

    def reset_host_state(self, host):
        for step in self.steps:
            step.reset_for_new_cycle()

    def feed(self, host, detections: list, current_time: float, original_frame=None):
        from backend.api.source_per_item_mixin import PerItemMixin
        self._frame_id += 1

        # 标签过滤 + 步骤置信度阈值 + 逐行 ROI (与步骤侧同一套守门语义)
        conf_map = getattr(host, 'step_conf_thresholds', None) or {}
        poly_map = getattr(host, 'step_roi_polygons', None) or {}
        boxes_by_label = {}
        for det in detections or []:
            label = det.get('label', '')
            if label not in self.watch_labels:
                continue
            threshold = conf_map.get(label)
            if threshold is not None and det.get('confidence', 0) < threshold:
                continue
            poly = poly_map.get(label)
            if poly and len(poly) >= 3 and not is_normalized_bbox_center_in_polygon(det, poly):
                continue
            bbox = (
                float(det.get('x', 0)), float(det.get('y', 0)),
                float(det.get('w', 0)), float(det.get('h', 0)),
            )
            if bbox[2] <= 0 or bbox[3] <= 0:
                continue
            boxes_by_label.setdefault(label, []).append(bbox)

        for step in self.steps:
            item_boxes = PerItemMixin._collect_item_boxes(boxes_by_label, step.item_label)
            fixed_count = step.expected_count > 0
            # 无 item box 也调用，以便逐帧清除 associated；方法签名保持兼容，
            # custom_mix 暂不启用跨步骤整板位移估算。
            step.update_item_positions(
                item_boxes, self._frame_id, current_time,
                lock_count_on_start=fixed_count)
            if item_boxes:
                # 固定数量: 只更新已有个体位置, 新位置走补锁定吸收 (封顶 expected)
                # auto: dynamic 随见随建
                if fixed_count and len(step.items) < step.expected_count:
                    PerItemMixin._per_item_absorb_new_items(
                        step, item_boxes, self._frame_id, current_time)
            if not fixed_count:
                step.cleanup_stale_items(
                    current_time, self.item_timeout_seconds,
                    lock_count_on_start=False)
            action_boxes = boxes_by_label.get(step.action_label, [])
            step.apply_coverage(action_boxes, self._frame_id, current_time)
            if not step.completed and step.check_completion():
                step.completed = True
                print(f"[CustomMix] 逐件步骤 [{step.display_label}] 完成 "
                      f"({step.covered_count()}/{len(step.items)})")

    def verdict(self, host):
        reasons = []
        ng_details = []
        for step in self.steps:
            # 与独立模式结算同语义: 读粘性完成标志 (feed 中翻转, 永不回滚);
            # 兜底再查一次完成判定 (结算与最后一帧之间的竞态)
            done = step.completed or step.check_completion()
            if done:
                continue
            missing = [iid for iid, st in step.items.items() if not st.covered]
            total = len(step.items)
            if step.expected_count > 0 and total < step.expected_count:
                reasons.append(
                    f'[{step.display_label}] 未完成({step.covered_count()}/{total}, '
                    f'期望{step.expected_count}件)')
            else:
                reasons.append(
                    f'[{step.display_label}] 未完成({step.covered_count()}/{total})')
            ng_details.append({
                'step_label': step.step_label,
                'display_label': step.display_label,
                'covered_count': step.covered_count(),
                'total': total,
                'expected_count': step.expected_count,
                'missing_item_ids': missing,
            })
        if reasons:
            self.last_ng_detail = {
                'reason_summary': '; '.join(reasons),
                'settled_at': time.time(),
                'missing_total': sum(len(d['missing_item_ids']) for d in ng_details),
                'steps_failed': ng_details,
            }
        else:
            self.last_ng_detail = None
        return (not reasons), reasons

    def to_state(self, host):
        steps_state = [s.to_state_dict(strict_display=True) for s in self.steps]
        items = []
        for s, st in zip(self.steps, steps_state):
            items.append({
                'label': s.step_label,
                'display_name': s.display_label,
                'role': 'pair',
                'expected_count': s.expected_count,
                'covered_count': st['covered_count'],
                'total': st['total'],
                'completed': st['completed'],
            })
        return {
            'mix_type': 'per_item',
            'items': items,
            'steps': steps_state,
            'last_ng_detail': self.last_ng_detail,
        }


class CustomMixMachine:
    """混合子状态机外壳: 引擎分发 + 周期跟随重置 + 裁决快照。"""

    def __init__(self, mix_type: str, item_cfgs: list, *,
                 item_timeout_seconds: float = 3.0, step_labels=(),
                 extra_item_labels=(), container_cfg=None):
        self.mix_type = mix_type
        self._cycle_token = '__init__'
        self._host = None
        if mix_type == 'tracking':
            self._engine = _TrackingMixEngine(item_cfgs, container_cfg)
            # 跟踪混合: 物品行标签全部由本组件独占消费
            self.item_labels = self._engine.item_labels
        else:
            self._engine = _PerItemMixEngine(item_cfgs, item_timeout_seconds)
            # 逐件混合: 个体/动作标签被独占消费, 但与步骤行同名的标签除外
            # (动作标签可同时推动序列 — 两边共享, 不剥离)。
            # extra_item_labels: 配置不完整被跳过的物品行标签 — 仍要剥离,
            # 不允许半配置的物品流进步骤侧状态机。
            self.item_labels = frozenset(
                (self._engine.watch_labels | set(extra_item_labels or ()))
                - set(step_labels or ()))

    @property
    def strip_labels(self):
        """从步骤侧状态机剥离的标签集 = 物品标签 + 容器标签 + (启用动作确认时)动作标签。

        容器标签 (如"托盘") 由容器累加器独占消费, **不应**再作为普通检测步骤参与
        "出现→消失→完成"判定 —— 否则模型在换盘/遮挡窗口偶发丢检, 步骤机会每帧刷
        "步骤完成"+ 标签频闪 (容器累加器本身有消失确认帧/IoU 峰值保持, 稳得多)。
        动作确认标签同理 (仅 confirm_by_action 生效时)。
        注意: 仅剥离"步骤侧"视图; 容器/动作标签仍正常检测/画框/喂累加器 (feed 在剥离前)。
        """
        labels = set(self.item_labels)
        eng = self._engine
        cont = getattr(eng, '_container', None)
        if cont is not None:
            if getattr(cont, 'container_label', ''):
                labels.add(cont.container_label)
            if getattr(cont, 'confirm_by_action', False) and getattr(cont, 'action_label', ''):
                labels.add(cont.action_label)
        return frozenset(labels)

    def reset(self):
        if self._host is not None:
            self._engine.reset_host_state(self._host)

    def feed(self, host, detections: list, current_time: float, original_frame=None):
        """每帧喂入 (在 _update_step_stats 剥离物品标签前调用)。"""
        self._host = host
        # 周期跟随: 步骤侧开了新周期 (uuid 变化) → 清零本周期物品统计
        token = getattr(host, 'current_cycle_uuid', None)
        if token != self._cycle_token:
            self.reset()
            self._cycle_token = token
        self._engine.feed(host, detections, current_time, original_frame)

    def verdict(self):
        """合成裁决: 所有物品都 OK 才 OK, NG 原因合并。"""
        return self._engine.verdict(self._host)

    def set_container_item_target(self, target):
        """包装结算反向设当前箱滑块目标 (透传跟踪引擎; 非容器/逐件混合 no-op)。"""
        fn = getattr(self._engine, 'set_container_item_target', None)
        if fn is not None:
            fn(target)

    def container_settled_item_total(self):
        """本周期已进箱滑块总数 (供包装结算累加; 非容器混合返回 None)。"""
        fn = getattr(self._engine, 'container_settled_item_total', None)
        return fn() if fn is not None else None

    def container_item_target(self):
        """当前箱滑块目标 (收尾防呆数量门用; 非容器混合返回 None)。"""
        fn = getattr(self._engine, 'container_item_target', None)
        return fn() if fn is not None else None

    def container_booked_item_total(self):
        """仅已确认进箱的滑块总数 (数量门口径; 非容器混合返回 None)。"""
        fn = getattr(self._engine, 'container_booked_item_total', None)
        return fn() if fn is not None else None

    def container_pending_peak_total(self):
        """在途主盘峰值 (数量门竞态补丁; 非容器混合返回 None)。"""
        fn = getattr(self._engine, 'container_pending_peak_total', None)
        return fn() if fn is not None else None

    def to_state(self):
        state = self._engine.to_state(self._host)
        # 步骤侧周期是否进行中 (前端面板"周期中/等待"显示用; 周期主权在步骤侧)
        state['cycle_active'] = bool(
            self._host is not None and getattr(self._host, 'current_cycle_uuid', None))
        return state


def build_custom_mix(config: dict):
    """从项目配置构建混合子状态机; 非 custom / 未混合 / 无物品行 → None (零差异)。"""
    if not config or config.get('logic_mode') != 'custom':
        return None
    pipeline = config.get('pipeline_config', {}) or {}
    mix_type = pipeline.get('custom_mixed_with')
    if mix_type not in MIX_TYPES:
        return None
    item_cfgs = []
    step_labels = set()
    skipped_item_labels = set()
    for step in config.get('steps_config', []) or []:
        if not step.get('enabled', True):
            continue
        label = step.get('label')
        if step.get('detect_role') != 'item':
            if label:
                step_labels.add(label)
            continue
        if not label:
            continue
        if mix_type == 'tracking':
            # 原生跟踪词汇: 行为字段 (count_mode/event_*/stack_*/max_recognized/...)
            # 由真 loader 直接从 steps_config 读, 这里只收 "身份 + 期望数量"
            expected = step.get('expected_count')
            if expected is None:
                expected = (step.get('mix_item') or {}).get('expected_count', 0)  # 旧存储兜底
            item_cfgs.append({
                'label': label,
                'display': step.get('displayLabel') or step.get('display_name') or label,
                'count_mode': step.get('count_mode', 'track'),
                'stack_enabled': bool(step.get('stack_enabled')),
                'stack_gate_only': bool(step.get('stack_gate_only', False)),
                'expected_count': expected,
                'event_required_count': step.get('event_required_count', 1),
                'stack_required_count': step.get('stack_required_count', 2),
            })
        else:
            # 原生逐件词汇: 物品行就是一条 "目标⟶动作" 配对, 字段与独立模式
            # steps_config[i].per_item 完全同名同义, 直接交给真 _PerItemStep 解析
            per = step.get('per_item') or {}
            if not per.get('item_label') or not per.get('action_label'):
                print(f"[CustomMix] 物品行 [{label}] 缺 per_item.item_label/action_label, 跳过")
                skipped_item_labels.add(label)
                continue
            item_cfgs.append(step)
    if not item_cfgs:
        print(f"[CustomMix] custom_mixed_with={mix_type} 但没有任何启用的物品行, 混合不生效")
        return None
    item_timeout = float(((pipeline.get('per_item') or {}).get('item_timeout_seconds', 3.0)) or 0.0)

    # 托盘容器累加器配置 (仅 tracking 混合 + 配了容器标签才启用; 否则 None = 零差异)
    container_cfg = None
    if mix_type == 'tracking':
        clabel = (pipeline.get('custom_mix_container_label') or '').strip()
        if clabel:
            item_expected = {}
            for ic in item_cfgs:
                lbl = ic.get('label')
                exp = int(ic.get('expected_count') or 0)
                if lbl and lbl != clabel and exp > 0:
                    item_expected[lbl] = exp
            count_mode = pipeline.get('custom_mix_container_count_mode', 'trays')
            # 进箱确认方式 (默认仅"消失满帧"= 老行为零差异)
            confirm_by_frames = bool(pipeline.get('custom_mix_container_confirm_by_frames', True))
            confirm_by_action = bool(pipeline.get('custom_mix_container_confirm_by_action', False))
            action_label = (pipeline.get('custom_mix_container_action_label') or '').strip()
            confirm_combine = pipeline.get('custom_mix_container_confirm_combine', 'or')
            # 放托盘动作门槛: 复用"放托盘"步骤自身配置 (最短出现帧 min_frames + 消失确认帧),
            # 落实"动作要走步骤那套门槛"; 缺省给宽松默认避免漏配卡死。
            action_min_frames, action_gone_frames = 3, 8
            if action_label:
                for s in config.get('steps_config', []) or []:
                    if s.get('label') == action_label:
                        action_min_frames = int(s.get('min_frames') or 3)
                        action_gone_frames = int(
                            s.get('tracking_gone_confirm_frames')
                            or s.get('event_gone_frames') or 8)
                        break
            # v3.43.1 动作门槛支持在"进箱确认方式"里直接配 (此前借用的步骤字段在
            # 自定义混合模式下无 UI 入口, 用户想调调不到)。配了才覆盖, 否则回落步骤字段。
            _pl_min = pipeline.get('custom_mix_container_action_min_frames')
            if _pl_min is not None and int(_pl_min or 0) > 0:
                action_min_frames = int(_pl_min)
            _pl_gone = pipeline.get('custom_mix_container_action_gone_frames')
            if _pl_gone is not None and int(_pl_gone or 0) > 0:
                action_gone_frames = int(_pl_gone)
            # 动作不应期 (秒): 缺省 2.0; 显式配 0 = 关闭
            _pl_cd = pipeline.get('custom_mix_container_action_cooldown_s')
            action_cooldown_s = 2.0 if _pl_cd is None else max(0.0, float(_pl_cd or 0.0))
            container_cfg = {
                'label': clabel,
                'item_expected': item_expected,
                'box_count': int(pipeline.get('custom_mix_container_box_count', 0) or 0),
                'gone_frames': int(pipeline.get('custom_mix_container_gone_frames', 30) or 30),
                'iou_match': float(pipeline.get('custom_mix_container_iou_match', 0.3) or 0.3),
                'count_mode': count_mode,
                'item_target': int(pipeline.get('custom_mix_container_item_target', 0) or 0),
                'confirm_by_frames': confirm_by_frames,
                'confirm_by_action': confirm_by_action,
                'action_label': action_label,
                'confirm_combine': confirm_combine,
                'action_min_frames': action_min_frames,
                'action_gone_frames': action_gone_frames,
                'action_cooldown_s': action_cooldown_s,
                # v3.44.1 每盘数量校验 (错盘当场拦截, 默认关): 每盘期望取物品行
                # 的期望数量 (item_expected), 尾盘按整箱余数核
                'per_tray_guard': bool(pipeline.get(
                    'custom_mix_container_per_tray_guard', False)),
                # v3.44.4 每盘峰值封顶 (0=关): 治模型偶发重复框把峰值咬到 25/26
                'peak_cap': int(pipeline.get(
                    'custom_mix_container_peak_cap', 0) or 0),
                # v3.44.5 "动作前稳定计数"快照记账 (0=关): 连放场景堆顶检测框
                # 无缝接上下一盘身份永不消失, 改按动作成立瞬间的稳定计数入账
                'stable_min_frames': int(pipeline.get(
                    'custom_mix_container_stable_min_frames', 0) or 0),
                # v3.46 五个可选开关 (默认 = v3.45 线上行为, 详见累加器 __init__):
                'dedup_items': bool(pipeline.get(
                    'custom_mix_container_dedup_items', True)),
                'dedup_trays': bool(pipeline.get(
                    'custom_mix_container_dedup_trays', False)),
                'purge_empty_primary': bool(pipeline.get(
                    'custom_mix_container_purge_empty_primary', False)),
                'yield_primary': bool(pipeline.get(
                    'custom_mix_container_yield_primary', False)),
                'unified_book_source': bool(pipeline.get(
                    'custom_mix_container_unified_book_source', False)),
                # v3.46 槽位完整性门 (空槽标签 + 槽位总数都配齐才开门):
                # 货数+空槽数 == 槽位总数 的帧才抬峰值/进稳定窗口, 治遮挡残数与重复框
                'slot_check_label': (
                    str(pipeline.get('custom_mix_container_slot_check_label') or '')
                    .strip()),
                'slot_total': int(pipeline.get(
                    'custom_mix_container_slot_total', 0) or 0),
                # v3.46 两处重复框去重阈值 (0=关该项)。物品框缺省 0.45 = 保持
                # v3.45 起的既有行为; 托盘框缺省 0 = 不动老项目
                'item_dedup_iou': (
                    0.45 if pipeline.get('custom_mix_container_item_dedup_iou') is None
                    else max(0.0, float(
                        pipeline.get('custom_mix_container_item_dedup_iou') or 0.0))),
                'tray_dedup_iou': max(0.0, float(
                    pipeline.get('custom_mix_container_tray_dedup_iou', 0) or 0)),
            }
            confirm_desc = []
            if confirm_by_frames:
                confirm_desc.append(f"消失满{container_cfg['gone_frames']}帧")
            if confirm_by_action and action_label:
                confirm_desc.append(
                    f"放托盘动作[{action_label}](出现≥{action_min_frames}帧/"
                    f"消失≥{action_gone_frames}帧/不应期{action_cooldown_s:g}s)")
            confirm_str = f" 进箱确认={('+' + confirm_combine.upper() + '+').join(confirm_desc) if len(confirm_desc) > 1 else (confirm_desc[0] if confirm_desc else '消失满帧')}"
            _slot_lbl = container_cfg.get('slot_check_label') or ''
            _slot_n = int(container_cfg.get('slot_total') or 0)
            slot_str = (f" 槽位门=开[{_slot_lbl}]×{_slot_n}"
                        if _slot_lbl and _slot_n > 0 else " 槽位门=关")
            _idi = container_cfg.get('item_dedup_iou') or 0.0
            _tdi = container_cfg.get('tray_dedup_iou') or 0.0
            slot_str += (f" 物品框去重={f'IoU>{_idi:g}' if _idi > 0 else '关'}"
                         f" 托盘框去重={f'IoU>{_tdi:g}' if _tdi > 0 else '关'}")
            if count_mode == 'items_total':
                print(f"[CustomMix] 托盘容器累加器[总数模式]: 容器={clabel} "
                      f"整箱滑块目标={container_cfg['item_target']}"
                      f"{confirm_str}{slot_str}")
            else:
                print(f"[CustomMix] 托盘容器累加器[盘计数]: 容器={clabel} 每盘期望={item_expected} "
                      f"每箱={container_cfg['box_count']}盘{confirm_str}{slot_str}")

    machine = CustomMixMachine(mix_type, item_cfgs,
                               item_timeout_seconds=item_timeout,
                               step_labels=step_labels,
                               extra_item_labels=skipped_item_labels,
                               container_cfg=container_cfg)
    print(f"[CustomMix] 混合子状态机就绪: mix={mix_type} 物品={sorted(machine.item_labels)}")
    return machine


def compose_settle_event(host, event_id, reason):
    """步骤侧结算事件 × 物品侧裁决合成 (所有 custom 结算点统一经此函数)。

    规则 (用户敲定: 结算一定由步骤驱动, 两边都 OK 才 OK):
      - 未启用混合 → 原样透传 (零差异)
      - 步骤侧 OK(1) + 物品侧 NG → 降级为 NG(2), 原因合并
      - 步骤侧 NG(2) + 物品侧 NG → 仍 NG, 原因追加物品侧
      - 自定义事件 (id 非 1/2) → 不改判 (好坏语义无法推断), 仅透传
    每次合成即视为一次周期结算, 物品统计随之清零 (新周期 uuid 变化时也会兜底重置)。
    """
    mix = getattr(host, '_custom_mix', None)
    if mix is None:
        return event_id, reason
    # v3.44: 步骤侧单独判定缓存给包装结算 (合成后的整体 NG 分不清"缺步骤"还是
    # "仅数量不足", 而少装挂起等补做只该在步骤齐、仅数量不足时触发).
    host._last_settle_steps_ok = (event_id == 1)
    try:
        ok, mix_reasons = mix.verdict()
        # 在 reset 前抓本周期进箱滑块总数, 缓存给包装结算协调器 (reset 后就归零).
        # 仅容器混合有值; 其它模式 None — on_cycle_settled 只在 sliders 口径读它.
        try:
            host._last_container_item_total = mix.container_settled_item_total()
            # 缓存本周期已检出步骤集 (尾箱塞工单 gate 探测用; end_cycle 后 current_cycle_steps 会清)
            host._last_cycle_steps = list(getattr(host, 'current_cycle_steps', []) or [])
            # v3.42.1: 序列外步骤旁路账本随周期轮转 (放工单等序列外检测步骤
            # 不进 current_cycle_steps, gate 探测靠这本账; 只留最近两代防陈旧误放行)
            host._last_oos_steps_seen = set(getattr(host, '_oos_steps_seen', None) or set())
            host._oos_steps_seen = set()
            try:
                from backend.core import debug_center
                if debug_center.is_on("backend.packaging"):
                    cont = getattr(mix, '_container', None)
                    tgt = getattr(cont, 'item_target', None) if cont else None
                    debug_center.dbg(
                        "backend.packaging", "封箱周期进箱滑块总数",
                        f"ch={getattr(host, 'channel_id', '-')} total={host._last_container_item_total} "
                        f"target={tgt} steps={host._last_cycle_steps}")
            except Exception:
                pass
        except Exception:
            host._last_container_item_total = None
        mix.reset()
    except Exception as e:
        print(f"[CustomMix] 裁决合成失败, 按步骤侧原判放行: {e}")
        return event_id, reason
    if ok or event_id not in (1, 2):
        return event_id, reason
    merged = '；'.join(mix_reasons)
    new_reason = f'{reason}；物品校验未通过: {merged}' if reason else f'物品校验未通过: {merged}'
    if event_id == 1:
        print(f"[CustomMix] 步骤侧 OK 但物品校验 NG → 降级为 NG: {merged}")
    return 2, new_reason
