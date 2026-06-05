"""JLGW1 现场 NG 复现 + 根因定位 (金龙热水浸泡线).

真实证据来源:
  - 模型 bestGW1.pt 对现场视频逐帧推理 (conf 0.25/0.6 各跑一遍) 的标签时序:
      热水浸泡(多次) → 浸泡结束(37s) → 热水浸泡回潮(39.8s) → 浸泡结束(45.7s,重现)
      → 甩干(62-74s,末步) → 结尾浸泡结束误检(82s)
  - 开发库 JLGW1 项目真实配置:
      logic=custom, custom_based_on=sequential, settlement_mode=last_step
      序列 = 热水浸泡 → 浸泡结束 → 甩干 (末步=甩干)
      热水浸泡 strict_order=1 accept_once=0   ← 关键: 只开严格, 没开单次接受
      浸泡结束 strict_order=1 accept_once=0   ← 关键: 只开严格, 没开单次接受

根因: 守门拦截要求 严格顺序 AND 单次接受 同时为真. 现状 accept_once=0,
      守门不触发 → 浸泡结束第二次出现照样进周期 → 判"重复/回退" → NG.
      客户自述"开了严格+单次", 但库里单次接受全是 0 (没保存成功 / 只开了严格).

解法: 给 热水浸泡 + 浸泡结束 补上"单次接受", 守门即生效, 重现被拦, cycle 干净.

本测试 = 先红后绿: accept_once=0 复现 NG, accept_once=1 修复.
"""
from __future__ import annotations

import numpy as np

from backend.api.source import VideoSourceManager

_DUMMY_FRAME = np.zeros((10, 10, 3), dtype="uint8")
_SEQ = ["热水浸泡", "浸泡结束", "甩干"]  # JLGW1 序列 (step 3/4/5)


def _make_jlgw1(*, accept_once: bool):
    """还原 JLGW1 序列 + 末步结算; 关键两步开严格, 单次接受由参数控制.

    JLGW1 线上是 custom-based-on-sequential, 但守门/单次接受的判定路径
    (_process_single_step 里靠 is_seq_like) 对 sequential 与
    custom-based-on-sequential **完全一致**. 这里用 sequential 直喂单步处理,
    避开 custom 模式优先级入口的额外状态, 干净验证守门逻辑. 结论同样适用线上配置.
    """
    vsm = VideoSourceManager(channel_id=0)
    steps = [
        {"id": 1, "label": "热水浸泡", "enabled": True,
         "strict_order": True, "accept_once": accept_once},
        {"id": 2, "label": "浸泡结束", "enabled": True,
         "strict_order": True, "accept_once": accept_once},
        {"id": 3, "label": "甩干", "enabled": True},      # 末步, 不开保护
    ]
    vsm.set_project_config({
        "id": 5,
        "name": "JLGW1",
        "task_type": "detection",
        "logic_mode": "sequential",
        "steps_config": steps,
        "events_config": [
            {"id": 1, "name": "OK", "actions": [], "show_notification": False},
            {"id": 2, "name": "NG", "actions": [], "show_notification": False},
        ],
        "counters_config": [],
        "pipeline_config": {
            "sequence_order": [{"step_id": 1}, {"step_id": 2}, {"step_id": 3}],
            "settlement_mode": "last_step",
        },
    })
    return vsm


def _feed(vsm, label, t, enabled):
    vsm._step_raw_start.setdefault(label, t)
    vsm._process_single_step(
        label=label, current_time=t, enabled_labels=enabled,
        is_seq_like=True, should_update_screenshot=False,
        original_frame=_DUMMY_FRAME, det_info=None, just_confirmed_labels=set(),
    )


def _reentry(vsm, label, t, enabled):
    """某步消失被结算清理后重新出现 (老 step_last_seen 删掉 → 算新出现)."""
    vsm.step_last_seen.pop(label, None)
    _feed(vsm, label, t, enabled)


def _run_field_timeline(vsm):
    """真实视频时序 (压缩到工序主干): 浸泡结束在甩干前出现两次, 中间夹热水浸泡回潮."""
    en = set(_SEQ)
    t = 1000.0
    _feed(vsm, "热水浸泡", t, en)         # 8-35s 热水浸泡
    _feed(vsm, "浸泡结束", t + 5, en)     # 37s 浸泡结束(第1次)
    _reentry(vsm, "热水浸泡", t + 8, en)  # 39.8s 热水浸泡回潮
    _reentry(vsm, "浸泡结束", t + 13, en) # 45.7s 浸泡结束(重现!)
    _feed(vsm, "甩干", t + 25, en)        # 62s 甩干(末步)


def test_current_config_accept_once_off_reproduces_ng():
    """红: JLGW1 现状(单次接受=关) → 浸泡结束重现进周期, 标记回退 → 复现现场 NG."""
    vsm = _make_jlgw1(accept_once=False)
    _run_field_timeline(vsm)
    assert vsm.current_cycle_steps.count("浸泡结束") == 2, (
        f"现状应把浸泡结束重现记入周期(复现 NG), 实际={vsm.current_cycle_steps}"
    )
    assert vsm._cycle_regression is True, "重复出现应标记回退(NG)"


def test_enable_accept_once_fixes_ng():
    """绿: 给热水浸泡+浸泡结束补上单次接受 → 浸泡结束重现被守门拦, cycle 干净."""
    vsm = _make_jlgw1(accept_once=True)
    _run_field_timeline(vsm)
    assert vsm.current_cycle_steps == ["热水浸泡", "浸泡结束", "甩干"], (
        f"开单次接受后周期应收敛到期望序列, 实际={vsm.current_cycle_steps}"
    )
    assert vsm._cycle_regression is False, "重现被拦, 不应标记回退"
