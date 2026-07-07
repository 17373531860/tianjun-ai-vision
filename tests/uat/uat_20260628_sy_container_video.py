"""可见浏览器 UAT: SY 真模型 + 合格视频跑容器累加器 (进箱/计数表现)。

接管 ch0: 测试视频 → set-project 推 SY 配置(默认仅消失满帧, 零差异) → 加载 SY .pt
真推理。轮询 /detection/results 记录容器状态时间线(trays_done/item_total_done),
同时开 Monitor 浏览器周期截图。前端 6001, 后端 8001。
"""
import time
import json
import requests
from playwright.sync_api import sync_playwright

BE = "http://localhost:8001/api/v1"
FE = "http://localhost:6001"
OUT = "tests/uat"
VIDEO_DIR = "/tmp/uat_video"
PID = 10
VIDEO = "/home/qianqian/文档/xwechat_files/wxid_9j6tgdyqgpon22_030a/temp/RWTemp/2026-06/9fb58869a2e3d1fa047812185dd93ec8/f0e84dd6035cd5ab0ec840ad706d6b4c.mp4"
MODEL = "/home/qianqian/桌面/word/tianjun-main/backend/uploads/models/f5d4e734cd504416aeefce4765c768a9_best6.26.pt"
SPEED = 2.0
CH = 0


def api(method, path, **kw):
    r = getattr(requests, method)(f"{BE}{path}", timeout=20, **kw)
    return r


def main():
    # ── 拉 SY 完整配置 ──
    p = api("get", f"/projects/{PID}").json()
    setproj = {
        "project_id": PID,
        "name": p["name"],
        "task_type": p.get("task_type", "detection"),
        "logic_mode": p.get("logic_mode", "custom"),
        "steps_config": p.get("steps_config") or [],
        "pipeline_config": p.get("pipeline_config") or {},
        "events_config": p.get("events_config") or [],
        "counters_config": p.get("counters_config") or [],
        "data_config": p.get("data_config") or {},
    }
    pc = setproj["pipeline_config"]
    print("[cfg] mix=%s container=%s count_mode=%s target=%s by_frames=%s by_action=%s gone=%s" % (
        pc.get("custom_mixed_with"), pc.get("custom_mix_container_label"),
        pc.get("custom_mix_container_count_mode"), pc.get("custom_mix_container_item_target"),
        pc.get("custom_mix_container_confirm_by_frames"),
        pc.get("custom_mix_container_confirm_by_action"),
        pc.get("custom_mix_container_gone_frames")))

    # ── 接管 ch0: 视频源 → set-project → 启动检测 ──
    print("[1] video/start:", api("post", f"/source/video/start?channel={CH}",
          json={"file_path": VIDEO, "speed": SPEED}).status_code)
    print("[2] set-project:", api("post", f"/source/detection/set-project?channel={CH}",
          json=setproj).status_code)
    r3 = api("post", f"/source/detection/start?channel={CH}",
             json={"model_path": MODEL, "conf": 0.25, "iou": 0.45,
                   "session_name": "uat_container_video"})
    print("[3] detection/start:", r3.status_code, r3.text[:200])

    timeline = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=60)
        ctx = browser.new_context(viewport={"width": 1680, "height": 950},
                                  record_video_dir=VIDEO_DIR)
        page = ctx.new_page()
        page.goto(f"{FE}/#/monitor", wait_until="domcontentloaded")
        time.sleep(4)

        t0 = time.time()
        shot_i = 0
        last_shot = 0
        while time.time() - t0 < 160:
            try:
                d = api("get", f"/source/detection/results?channel={CH}").json()
            except Exception as e:
                print("poll err", str(e)[:100]); time.sleep(3); continue
            cm = d.get("custom_mix_state") or {}
            cont = cm.get("container") or {}
            counters = d.get("counters") or {}
            n_det = len(d.get("detections") or [])
            cur_items = cont.get("current_tray_items") or []
            cur_sum = sum(i.get("current_count", 0) for i in cur_items)
            item_total = cont.get("item_total_done") or {}
            tot = sum(item_total.values()) if isinstance(item_total, dict) else 0
            rec = {
                "t": round(time.time() - t0, 1),
                "detecting": d.get("is_detecting"),
                "n_det": n_det,
                "trays_done": cont.get("trays_done"),
                "item_total_sum": tot,
                "item_target": cont.get("item_target"),
                "cur_tray_sum": cur_sum,
                "counters": {k: counters.get(k) for k in ("合格总数", "不良总数", "总产量")},
            }
            timeline.append(rec)
            print(f"  t={rec['t']:>5}s det={rec['detecting']} dets={n_det:>2} "
                  f"trays_done={rec['trays_done']} 已进箱滑块={tot}/{rec['item_target']} "
                  f"当前盘={cur_sum} 计数={rec['counters']}")
            # 每 ~20s 截一图; 检测停了就收尾
            if time.time() - last_shot > 20:
                shot_i += 1
                try:
                    page.screenshot(path=f"{OUT}/sy_video_{shot_i:02d}.png")
                    print(f"   shot -> {OUT}/sy_video_{shot_i:02d}.png")
                except Exception:
                    pass
                last_shot = time.time()
            if rec["t"] > 8 and not d.get("is_detecting"):
                print("  [检测已结束]")
                break
            time.sleep(3)

        page.screenshot(path=f"{OUT}/sy_video_final.png")
        print(f"  final shot -> {OUT}/sy_video_final.png")
        time.sleep(1)
        ctx.close()
        browser.close()

    with open(f"{OUT}/_sy_video_timeline.json", "w") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)
    if timeline:
        last = timeline[-1]
        peak_trays = max((r["trays_done"] or 0) for r in timeline)
        peak_tot = max((r["item_total_sum"] or 0) for r in timeline)
        print("\n==================== 汇总 ====================")
        print(f"  采样 {len(timeline)} 次")
        print(f"  峰值已进箱托盘数 trays_done={peak_trays}")
        print(f"  峰值已进箱滑块总数={peak_tot} (整箱目标={last['item_target']})")
        print(f"  末计数器={last['counters']}")
        moved = any((r['trays_done'] or 0) > 0 or (r['item_total_sum'] or 0) > 0 for r in timeline)
        print(f"  进箱/计数发生变化: {'YES' if moved else 'NO'}")


if __name__ == "__main__":
    main()
