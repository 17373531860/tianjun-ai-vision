"""
全面测试跟踪模式的遮挡容忍和消失确认功能。
用多种不同数值组合验证逻辑正确性。
"""
import sys, time
sys.path.insert(0, "/home/qianqian/桌面/word/tianjun副本/backend")

import numpy as np
from api.source import VideoSourceManager

def make_config(gone_confirm_frames=5, steps_lost=None, strategy="all_gone", expected=None):
    if steps_lost is None:
        steps_lost = [("螺丝", 1.0), ("垫片", 1.0)]
    if expected is None:
        expected = {s[0]: 1 for s in steps_lost}
    return {
        "id": 9999, "name": "TrackingTest", "task_type": "detection",
        "logic_mode": "tracking",
        "steps_config": [
            {"id": i+1, "label": lbl, "enabled": True, "displayLabel": lbl,
             "tracking_max_lost_seconds": lost_sec, "tracking_position_lock": False, "conf_threshold": 50}
            for i, (lbl, lost_sec) in enumerate(steps_lost)
        ],
        "pipeline_config": {
            "tracking_cycle_strategy": strategy,
            "tracking_gone_confirm_frames": gone_confirm_frames,
            "tracking_gone_threshold": 0,
            "counting_expected_items": expected,
            "tracking_check_order": False, "tracking_expected_order": [],
            "tracking_swap_detection": False, "tracking_appearance_match": False,
            "tracking_id_lock": False, "tracking_id_lock_frames": 15,
            "tracking_roi": {"enabled": False, "polygon": []},
        },
        "events_config": [], "counters_config": [],
    }

def setup_mgr(config, fps=10.0):
    mgr = VideoSourceManager()
    mgr.recording_enabled = False
    mgr.current_session_id = None
    mgr.set_project_config(config)
    mgr.fps_actual = fps
    return mgr

def det(label, track_id, x=0.3, y=0.3):
    return {"label": label, "confidence": 0.9, "x": x, "y": y, "w": 0.1, "h": 0.1, "track_id": track_id}

FRAME = np.zeros((480, 640, 3), dtype=np.uint8)
PASS, FAIL = 0, 0

def check(name, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"    ✓ {name}")
    else:
        FAIL += 1
        print(f"    ✗ {name}")
    return condition

# ==============================================================================
# 测试 1: gone_confirm_frames 不同数值 (1, 5, 15, 30, 50)
# ==============================================================================
def test_gone_confirm_values():
    print("\n" + "="*70)
    print("测试组 1: 不同 gone_confirm_frames 值 (1, 5, 15, 30, 50)")
    print("="*70)
    
    for gcf in [1, 5, 15, 30, 50]:
        config = make_config(gone_confirm_frames=gcf, steps_lost=[("螺丝", 0.5)])
        mgr = setup_mgr(config, fps=10.0)
        tol = int(0.5 * 10)  # 5 帧
        
        # 物体出现
        for _ in range(10):
            mgr._update_tracking_stats([det("螺丝", 100)], FRAME)
        mgr.cycle_start_time = time.time() - 20.0
        
        # 物体消失，逐帧跟踪结算时机
        settled_at = None
        total_empty = 0
        for i in range(tol + gcf + 10):
            was = mgr._tracking_cycle_active
            mgr._update_tracking_stats([], FRAME)
            total_empty += 1
            if was and not mgr._tracking_cycle_active:
                settled_at = total_empty
                break
        
        ok = settled_at is not None
        check(f"gcf={gcf:2d}: 结算于空帧第 {settled_at} 帧 (容忍{tol}+确认{gcf}={tol+gcf}帧)", ok)

# ==============================================================================
# 测试 2: 不同 FPS 下遮挡容忍帧数计算
# ==============================================================================
def test_different_fps():
    print("\n" + "="*70)
    print("测试组 2: 不同 FPS (10, 15, 25, 30) 下遮挡容忍帧数")
    print("="*70)
    
    for fps in [10, 15, 25, 30]:
        lost_sec = 2.0
        config = make_config(gone_confirm_frames=3, steps_lost=[("螺丝", lost_sec)])
        mgr = setup_mgr(config, fps=float(fps))
        expected_tol_frames = int(lost_sec * fps)
        
        # 物体出现
        for _ in range(10):
            mgr._update_tracking_stats([det("螺丝", 200)], FRAME)
        
        # 物体消失，检查在 expected_tol_frames-1 处还在
        for _ in range(expected_tol_frames - 1):
            mgr._update_tracking_stats([], FRAME)
        
        still_tracked = len(mgr._tracking_objects) == 1
        check(f"fps={fps:2d}, lost=2.0s: 消失 {expected_tol_frames-1} 帧后物体仍在", still_tracked)
        
        # 再 1 帧应该超时
        mgr._update_tracking_stats([], FRAME)
        removed = len(mgr._tracking_objects) == 0
        check(f"fps={fps:2d}, lost=2.0s: 消失 {expected_tol_frames} 帧后物体移除", removed)

# ==============================================================================
# 测试 3: 不同容忍时间 (0.1s, 0.5s, 2s, 5s, 10s, 15s)
# ==============================================================================
def test_different_tolerance():
    print("\n" + "="*70)
    print("测试组 3: 不同遮挡容忍时间 (0.1s, 0.5s, 2s, 5s, 10s, 15s)")
    print("="*70)
    
    fps = 10.0
    for lost_sec in [0.1, 0.5, 2.0, 5.0, 10.0, 15.0]:
        config = make_config(gone_confirm_frames=3, steps_lost=[("螺丝", lost_sec)])
        mgr = setup_mgr(config, fps=fps)
        expected_frames = int(lost_sec * fps)
        
        for _ in range(10):
            mgr._update_tracking_stats([det("螺丝", 300)], FRAME)
        
        # 消失 expected-1 帧
        for _ in range(max(expected_frames - 1, 0)):
            mgr._update_tracking_stats([], FRAME)
        
        if expected_frames > 1:
            still_in = len(mgr._tracking_objects) == 1
            check(f"lost={lost_sec:5.1f}s ({expected_frames:3d}帧): 消失{expected_frames-1:3d}帧后仍在", still_in)
        
        # 再消失到刚好超时
        remaining = max(1, expected_frames) - max(expected_frames - 1, 0)
        for _ in range(remaining):
            mgr._update_tracking_stats([], FRAME)
        
        gone = len(mgr._tracking_objects) == 0
        check(f"lost={lost_sec:5.1f}s ({expected_frames:3d}帧): 消失{expected_frames:3d}帧后移除", gone)

# ==============================================================================
# 测试 4: 多类别不同容忍 + 结算联动
# ==============================================================================
def test_multi_class_settlement():
    print("\n" + "="*70)
    print("测试组 4: 多类别不同容忍 + 消失确认联动结算")
    print("="*70)
    
    fps = 10.0
    gcf = 5
    steps = [("螺丝", 1.0), ("垫片", 3.0), ("弹簧", 5.0)]
    config = make_config(gone_confirm_frames=gcf, steps_lost=steps)
    mgr = setup_mgr(config, fps=fps)
    
    # 三个物体同时出现
    for _ in range(10):
        mgr._update_tracking_stats([
            det("螺丝", 400, x=0.1), det("垫片", 401, x=0.4), det("弹簧", 402, x=0.7)
        ], FRAME)
    mgr.cycle_start_time = time.time() - 30.0
    
    # 三个同时消失
    # 螺丝: 10帧后超时, 垫片: 30帧后超时, 弹簧: 50帧后超时
    
    # 10 帧后
    for _ in range(10):
        mgr._update_tracking_stats([], FRAME)
    remaining = sorted([o['class_name'] for o in mgr._tracking_objects.values()])
    check(f"10帧后: 螺丝移除, 剩余={remaining}", remaining == ['垫片', '弹簧'])
    check(f"10帧后: gone_frames=0 (仍有活跃物体)", mgr._tracking_gone_frames == 0)
    
    # 30 帧后
    for _ in range(20):
        mgr._update_tracking_stats([], FRAME)
    remaining2 = sorted([o['class_name'] for o in mgr._tracking_objects.values()])
    check(f"30帧后: 垫片移除, 剩余={remaining2}", remaining2 == ['弹簧'])
    check(f"30帧后: gone_frames=0 (弹簧还在)", mgr._tracking_gone_frames == 0)
    
    # 50 帧后 (再 20 帧)
    for _ in range(20):
        mgr._update_tracking_stats([], FRAME)
    remaining3 = [o['class_name'] for o in mgr._tracking_objects.values()]
    check(f"50帧后: 弹簧移除, 剩余={remaining3}", len(remaining3) == 0)
    check(f"50帧后: gone_frames>0 (消失确认开始)", mgr._tracking_gone_frames > 0)
    
    # 再等 gcf 帧结算
    for _ in range(gcf + 2):
        mgr._update_tracking_stats([], FRAME)
    check(f"50+{gcf}帧后: 周期已结算", not mgr._tracking_cycle_active)

# ==============================================================================
# 测试 5: 消失确认中途物体反复出现消失
# ==============================================================================
def test_flicker_during_confirm():
    print("\n" + "="*70)
    print("测试组 5: 消失确认中途物体反复出现消失 (抗抖动)")
    print("="*70)
    
    fps = 10.0
    gcf = 20
    config = make_config(gone_confirm_frames=gcf, steps_lost=[("螺丝", 0.5)])
    mgr = setup_mgr(config, fps=fps)
    tol = int(0.5 * fps)  # 5帧
    
    # 物体出现
    for _ in range(10):
        mgr._update_tracking_stats([det("螺丝", 500)], FRAME)
    mgr.cycle_start_time = time.time() - 20.0
    
    # 物体消失超过容忍
    for _ in range(tol + 1):
        mgr._update_tracking_stats([], FRAME)
    
    # 消失确认开始，计几帧
    for _ in range(8):
        mgr._update_tracking_stats([], FRAME)
    gf_mid = mgr._tracking_gone_frames
    check(f"消失确认进行中: gone_frames={gf_mid} > 5", gf_mid > 5)
    
    # 物体短暂重现 1 帧
    mgr._update_tracking_stats([det("螺丝", 600)], FRAME)
    gf_reset = mgr._tracking_gone_frames
    check(f"物体重现: gone_frames 重置为 {gf_reset}", gf_reset == 0)
    check(f"周期仍然活跃", mgr._tracking_cycle_active)
    
    # 物体又消失，重新进入容忍 + 确认
    for _ in range(tol + 1):
        mgr._update_tracking_stats([], FRAME)
    
    gf_restart = mgr._tracking_gone_frames
    check(f"物体再次消失+超容忍: gone_frames={gf_restart} > 0", gf_restart > 0)
    
    # 跑完消失确认
    for _ in range(gcf + 5):
        mgr._update_tracking_stats([], FRAME)
    check(f"最终结算完成", not mgr._tracking_cycle_active)

# ==============================================================================
# 测试 6: roi_exit 策略的消失确认
# ==============================================================================
def test_roi_exit_strategy():
    print("\n" + "="*70)
    print("测试组 6: roi_exit 策略消失确认")
    print("="*70)
    
    gcf = 10
    config = make_config(gone_confirm_frames=gcf, steps_lost=[("螺丝", 0.5)], strategy="roi_exit")
    mgr = setup_mgr(config, fps=10.0)
    tol = 5
    
    for _ in range(10):
        mgr._update_tracking_stats([det("螺丝", 700)], FRAME)
    mgr.cycle_start_time = time.time() - 20.0
    
    for _ in range(tol + 1):
        mgr._update_tracking_stats([], FRAME)
    
    settled = False
    for i in range(gcf + 5):
        was = mgr._tracking_cycle_active
        mgr._update_tracking_stats([], FRAME)
        if was and not mgr._tracking_cycle_active:
            settled = True
            check(f"roi_exit gcf={gcf}: 结算于第 {i+1} 帧", True)
            break
    
    if not settled:
        check(f"roi_exit gcf={gcf}: 应触发结算", False)

# ==============================================================================
# 测试 7: 边界情况 — gone_confirm_frames=1
# ==============================================================================
def test_edge_gcf_1():
    print("\n" + "="*70)
    print("测试组 7: 边界情况 gone_confirm_frames=1 (立即结算)")
    print("="*70)
    
    config = make_config(gone_confirm_frames=1, steps_lost=[("螺丝", 0.3)])
    mgr = setup_mgr(config, fps=10.0)
    tol = 3  # 0.3s * 10fps
    
    for _ in range(10):
        mgr._update_tracking_stats([det("螺丝", 800)], FRAME)
    mgr.cycle_start_time = time.time() - 20.0
    
    # 消失直到结算
    settled_at = None
    for i in range(tol + 5):
        was = mgr._tracking_cycle_active
        mgr._update_tracking_stats([], FRAME)
        if was and not mgr._tracking_cycle_active:
            settled_at = i + 1
            break
    
    check(f"gcf=1: 容忍{tol}帧后立即结算 (实际第{settled_at}帧)", settled_at is not None and settled_at <= tol + 2)

# ==============================================================================
# 测试 8: 极端大数值 gone_confirm=200, lost=20s
# ==============================================================================
def test_large_values():
    print("\n" + "="*70)
    print("测试组 8: 大数值 gone_confirm=200, lost_seconds=20s, fps=30")
    print("="*70)
    
    fps = 30.0
    gcf = 200
    lost = 20.0
    config = make_config(gone_confirm_frames=gcf, steps_lost=[("螺丝", lost)])
    mgr = setup_mgr(config, fps=fps)
    expected_tol = int(lost * fps)  # 600 帧
    
    for _ in range(10):
        mgr._update_tracking_stats([det("螺丝", 900)], FRAME)
    mgr.cycle_start_time = time.time() - 100.0
    
    # 快速消失
    for _ in range(expected_tol):
        mgr._update_tracking_stats([], FRAME)
    
    removed = len(mgr._tracking_objects) == 0
    check(f"20s容忍@30fps ({expected_tol}帧): 物体正确移除", removed)
    
    # 消失确认
    for _ in range(gcf):
        mgr._update_tracking_stats([], FRAME)
    
    check(f"200帧消失确认后: 周期已结算", not mgr._tracking_cycle_active)

# ==============================================================================

if __name__ == "__main__":
    test_gone_confirm_values()
    test_different_fps()
    test_different_tolerance()
    test_multi_class_settlement()
    test_flicker_during_confirm()
    test_roi_exit_strategy()
    test_edge_gcf_1()
    test_large_values()
    
    print("\n" + "="*70)
    print(f"最终结果: {PASS} 通过, {FAIL} 失败")
    print("="*70)
    
    if FAIL > 0:
        print("✗✗ 存在失败的测试!")
        sys.exit(1)
    else:
        print("✓✓ 全部测试通过!")
        sys.exit(0)
