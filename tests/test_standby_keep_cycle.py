"""v3.44.1 待机保留在制周期单测.

客户报障 (2026-07-23, 上银 SY3): 错盘拦截 → 人工确认(保留周期)成功后点「待机」,
在制周期 / 箱内台账 / SOP 已做步骤全部被清零 — 待机走了和「停止」一样的全清路径。

期望契约 (本文件锁定):
  A. standby(): 保留 current_cycle_steps / 混合台账 / 缺步挂起态 / 人工确认态,
     只清帧级缓存 (卡尔曼 / 推理帧 / 已确认检测)。
  B. stop_detection() / 清零 (reset_stats): 语义不变, 照旧全清。
"""
from __future__ import annotations

import os
os.environ.setdefault("BACKEND_SKIP_INIT", "1")

from backend.api.source import VideoSourceManager


def _make_mix_vsm():
    """真 VSM + custom(顺序) + 容器混合跟踪 (总数模式), 与 SY3 同构."""
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        "id": 99441,
        "name": "待机保留周期单测",
        "logic_mode": "custom",
        "pipeline_config": {
            "custom_based_on": "sequential",
            "custom_mixed_with": "tracking",
            "custom_sequence_order": [{"step_id": "s1"}, {"step_id": "s2"}],
            "custom_mix_container_enabled": True,
            "custom_mix_container_label": "托盘",
            "custom_mix_container_count_mode": "items_total",
            "custom_mix_container_items_total": 96,
        },
        "steps_config": [
            {"id": "s1", "label": "贴标", "enabled": True},
            {"id": "s2", "label": "封箱", "enabled": True},
            {"id": "s3", "label": "滑块", "enabled": True, "detect_role": "item",
             "expected_count": 24},
        ],
        "events_config": [], "counters_config": [], "data_config": {},
    })
    assert vsm._custom_mix is not None and vsm._custom_mix.mix_type == "tracking"
    return vsm


def _seed_mid_cycle_state(vsm):
    """人工造一个"箱做到一半"的运行时状态."""
    vsm.current_cycle_steps = ["贴标"]
    vsm.current_cycle_uuid = "test-uuid-1"
    vsm.step_last_seen["贴标"] = 123.0
    vsm.step_frame_confirmed["贴标"] = True
    # 混合台账: 直接往容器累加器塞一盘已记账
    cont = vsm._custom_mix._engine._container
    cont._done.append({"滑块": 24})
    # 人工确认定格态 (错盘拦截场景)
    vsm._pending_ack = True
    vsm._pending_ack_event_name = "包装防呆提示"
    vsm._pending_ack_reason = "第3盘数量不对 19/24"


def test_standby_keeps_cycle_and_ledger():
    vsm = _make_mix_vsm()
    _seed_mid_cycle_state(vsm)

    vsm.standby()

    # A. 在制周期 / 台账 / 定格态全部保留
    assert vsm.current_cycle_steps == ["贴标"], "待机不得清在制周期"
    assert vsm.current_cycle_uuid == "test-uuid-1"
    cont = vsm._custom_mix._engine._container
    assert cont.settled_item_total() == 24, "待机不得清箱内台账"
    assert vsm._pending_ack is True, "待机不得吞掉人工确认定格"
    # 帧级缓存照清
    assert len(vsm._kalman_filters) == 0
    with vsm._confirmed_detections_lock:
        assert vsm._confirmed_detections == []


def test_stop_detection_still_full_clear():
    vsm = _make_mix_vsm()
    _seed_mid_cycle_state(vsm)

    vsm.stop_detection()

    assert vsm.current_cycle_steps == [], "停止仍须全清在制周期"
    assert vsm._pending_ack is False, "停止仍须解除定格"
    assert vsm._pending_remediation is None


def test_standby_keeps_settle_hold():
    """缺步挂起 (settle hold) 属在制状态, 待机同样保留."""
    vsm = _make_mix_vsm()
    _seed_mid_cycle_state(vsm)
    vsm._settle_hold = {"missing": ["封箱"], "started_at": 1.0}

    vsm.standby()
    assert vsm._settle_hold is not None, "待机不得丢缺步挂起"

    vsm.stop_detection()
    assert vsm._settle_hold is None, "停止仍须清挂起"


def test_standby_holds_video_playback_and_resume_releases():
    """v3.44.2: 视频源待机必须冻结播放位置 (待机期间剧情不许被静默消耗),
    恢复推理 / 完整启动 / 停止 / 暂停都要解除冻结."""
    vsm = _make_mix_vsm()
    vsm.source_type = "video"

    vsm.standby()
    assert vsm._video_hold is True, "视频源待机须冻结播放"
    assert vsm._video_playback_held() is True

    # 纯语义验证: 跳过真模型加载/线程拉起, 只验证 resume 对冻结位的复位
    vsm.is_running = True
    vsm.model = object()
    vsm._start_inference_thread = lambda: None
    vsm._ensure_session_active = lambda: None
    vsm.start_session_recording = lambda: None
    vsm.resume_inference()
    assert vsm._video_hold is False, "恢复推理须解除播放冻结"
    assert vsm._video_playback_held() is False

    # 人工确认定格也冻结播放 (检测中)
    vsm.is_detecting = True
    vsm._pending_ack = True
    assert vsm._video_playback_held() is True, "确认定格中视频不许前进"
    vsm._pending_ack = False
    assert vsm._video_playback_held() is False

    # 相机源永不冻结 (现实世界暂停不了)
    vsm.source_type = "camera"
    vsm._video_hold = True
    vsm._pending_ack = True
    assert vsm._video_playback_held() is False, "相机源不适用播放冻结"


def test_stop_and_pause_release_video_hold():
    vsm = _make_mix_vsm()
    vsm.source_type = "video"
    vsm.standby()
    assert vsm._video_hold is True

    vsm.stop_detection()
    # stop_detection 不碰 _video_hold (视频流仍在跑), 但完整启动会复位 —
    # 这里锁定 pause (停止按钮的视频源路径) 的复位行为
    vsm._video_hold = True
    vsm.pause()
    assert vsm._video_hold is False, "暂停(停止按钮)须解除播放冻结"


def test_settled_ng_ack_clears_runtime_even_with_keep_cycle():
    """v3.44.4: 挂起超时/止损 NG 已落账, 确认释放必须清运行时 — 即使 NG 事件
    配了「确认后保留周期」。否则旧周期步骤赖着, 下一箱同名步骤被"单次接受"
    吞掉, 多箱并成一锅账 (上银 7-27 视频实测: 箱1 超时NG后箱2/3/4 全并账)."""
    vsm = _make_mix_vsm()
    _seed_mid_cycle_state(vsm)
    # 模拟 _finalize_settle_hold_ng 落账后留下的"确认后强制清"标记
    vsm._ack_clear_runtime_after = True

    vsm._ack_release_keep_cycle()

    assert vsm._pending_ack is False
    assert vsm.current_cycle_steps == [], "结算NG确认后旧周期步骤必须清空"
    assert vsm._ack_clear_runtime_after is False, "单次标记须被消费"


def test_mid_cycle_ack_keep_cycle_still_keeps():
    """零差异对照: 周期中途定格 (无强制清标记) 的保留周期语义不变."""
    vsm = _make_mix_vsm()
    _seed_mid_cycle_state(vsm)

    vsm._ack_release_keep_cycle()

    assert vsm._pending_ack is False
    assert vsm.current_cycle_steps == ["贴标"], "断点补做必须保留在制周期"
