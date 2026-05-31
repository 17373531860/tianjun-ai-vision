"""可见浏览器 UAT — v1.1.2 福建金龙双工位插件视觉验证.

前提:
  - 后端 :8001 已跑, 组合 A 配置已就绪 (ch0=GW1 + ch1=GW2 各自独立运行中)
  - 前端 :5173 已跑, 插件 v1.1.2 已 active

任务:
  1. 进 Monitor 双工位视图
  2. 拍连续 3 张截图 (启动后 3s / 15s / 30s, 看真实推理填进 UI)
  3. 拍局部截图: 统计区 / SOP 流程串 / 步骤统计表 / 控制按钮
  4. 操作按钮验证 (清零 / 待机 / 停止 / 开始)
"""
import time
import requests
from pathlib import Path
from playwright.sync_api import sync_playwright


BASE = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8001/api/v1"
OUT = Path("/tmp/uat_v112_screenshots")
OUT.mkdir(parents=True, exist_ok=True)

GW1_VIDEO = "/tmp/tianjun_wfc_real_uat/uploads/videos/gw1.mp4"
GW2_VIDEO = "/tmp/tianjun_wfc_real_uat/uploads/videos/gw2.mp4"
GW1_MODEL = "/tmp/tianjun_wfc_real_uat/uploads/models/bestGW1.pt"
GW2_MODEL = "/tmp/tianjun_wfc_real_uat/uploads/models/bestGW2.pt"


def find_project_id(name_prefix):
    """根据名字前缀找最新的 project_id"""
    r = requests.get(f"{API}/projects", timeout=5)
    items = r.json() if isinstance(r.json(), list) else (r.json().get("items") or [])
    matched = [p for p in items if (p.get("name") or "").startswith(name_prefix)]
    matched.sort(key=lambda p: p["id"], reverse=True)
    return matched[0]["id"] if matched else None


def setup_for_visual():
    """关键: 让浏览器进 Monitor 时能看到 GW1+GW2 而不是被 QG 覆盖.
       策略: activate jinlong-GW1 (ch0 自动 sync GW1) + 持久化绑定 ch1.project_id=GW2.id
            → ch1 因为 bound_to_other 不被 sync, 保持 GW2.
    """
    gw1_pid = find_project_id("jinlong-GW1-1780157721")  # 用最新一组
    gw2_pid = find_project_id("jinlong-GW2-1780157721")
    print(f"  GW1 project_id={gw1_pid}, GW2 project_id={gw2_pid}")
    if not gw1_pid or not gw2_pid:
        print("[!!] 找不到 jinlong-GW1/GW2 项目, 请先跑 dual_independent UAT")
        return False

    # 先停掉旧的检测和视频
    for ch in (0, 1):
        try:
            requests.post(f"{API}/source/detection/stop?channel={ch}", timeout=5)
            requests.post(f"{API}/source/video/stop?channel={ch}", timeout=5)
        except Exception:
            pass
    time.sleep(1)

    # 持久化绑定 ch1 → GW2
    r = requests.put(f"{API}/workstations/channel-config",
                     json={"channel_id": 1, "project_id": gw2_pid,
                           "source_type": "video", "file_path": GW2_VIDEO,
                           "model_id": gw2_pid},
                     timeout=10)
    print(f"  ch1 持久化绑定 GW2: status={r.status_code}")

    # 持久化绑定 ch0 → GW1
    r = requests.put(f"{API}/workstations/channel-config",
                     json={"channel_id": 0, "project_id": gw1_pid,
                           "source_type": "video", "file_path": GW1_VIDEO,
                           "model_id": gw1_pid},
                     timeout=10)
    print(f"  ch0 持久化绑定 GW1: status={r.status_code}")

    # activate GW1 (这会触发: 全局 sync ch0 → GW1; ch1 因 bound_to_other 跳过, 保持自己的 project)
    r = requests.post(f"{API}/projects/{gw1_pid}/activate", timeout=15)
    print(f"  activate GW1 (全局): status={r.status_code}")
    time.sleep(1)

    # ch1 因为 sync 被跳过, 需要手动 set-project + 启动
    # 拿到 GW2 项目数据
    r = requests.get(f"{API}/projects/{gw2_pid}", timeout=5)
    gw2_proj = r.json() if r.status_code == 200 else {}
    proj_cfg = {
        "project_id": gw2_pid,
        "name": gw2_proj.get("name", "jinlong-GW2"),
        "task_type": "detection",
        "logic_mode": "sequential",
        "pipeline_config": gw2_proj.get("pipeline_config") or {"logic_mode": "sequential", "settlement_mode": "first_step"},
        "steps_config": gw2_proj.get("steps_config") or [],
        "events_config": gw2_proj.get("events_config") or [],
    }
    r = requests.post(f"{API}/source/detection/set-project?channel=1", json=proj_cfg, timeout=10)
    print(f"  ch1 set-project GW2: status={r.status_code}")

    # 启动视频 + 检测 (1.5x 慢速给 Playwright 截图时间)
    for ch, vid in [(0, GW1_VIDEO), (1, GW2_VIDEO)]:
        r = requests.post(f"{API}/source/video/start?channel={ch}",
                          json={"file_path": vid, "speed": 1.5}, timeout=15)
        print(f"  ch{ch} 启动视频: status={r.status_code}")

    for ch, m, tag in [(0, GW1_MODEL, "GW1"), (1, GW2_MODEL, "GW2")]:
        r = requests.post(f"{API}/source/detection/start?channel={ch}",
                          json={"model_path": m, "conf": 0.5, "iou": 0.45,
                                "session_name": f"jinlong-{tag}-visual"},
                          timeout=30)
        print(f"  ch{ch} 启动检测 {tag}: status={r.status_code}")
    return True


def main():
    print("[VISUAL UAT] 配置: GW1 = active project, ch1 绑 GW2 (持久化) ...")
    if not setup_for_visual():
        return
    time.sleep(3)  # 让视频先吐数据

    # 确认通道现在的 project_config 是 GW1/GW2
    for ch in (0, 1):
        r = requests.get(f"{API}/source/detection/results?channel={ch}", timeout=5)
        d = r.json() if r.status_code == 200 else {}
        pc = d.get("project_config") or {}
        print(f"  [verify] ch{ch}: project_name={pc.get('project_name')} steps={len(pc.get('steps_config') or [])} det={d.get('is_detecting')} fps={d.get('fps')}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()

        page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)  # 等 Vue + 插件 mount

        # 1) 切到双工位视图 (如果还是单工位)
        # 直接看 fjjl 插件容器
        page.screenshot(path=str(OUT / "00_init.png"), full_page=True)
        print(f"[01] 初始截图 -> {OUT / '00_init.png'}")

        # 2) 检查插件容器存在
        plugin_root = page.locator(".fjjl-grid")
        cnt = plugin_root.count()
        print(f"[02] .fjjl-grid 容器数量: {cnt}")

        if cnt == 0:
            print("[!!] 插件未渲染, 可能不在双工位 monitor.layout.body 槽位下")
            print("     当前 url:", page.url)
            # 尝试直接到 monitor 视图
            page.locator("text=监控").first.click(timeout=5000)
            page.wait_for_timeout(2000)
            page.screenshot(path=str(OUT / "01_after_click_monitor.png"), full_page=True)
            cnt = plugin_root.count()
            print(f"[03] 点击监控后 .fjjl-grid 数量: {cnt}")

        # 3) 等推理出数据再拍
        for wait_sec, name in [(5, "t5"), (15, "t15"), (30, "t30")]:
            page.wait_for_timeout(wait_sec * 1000 if name == "t5" else 10000)
            page.screenshot(path=str(OUT / f"10_full_{name}.png"), full_page=True)
            print(f"[04] {name} 全图 -> {OUT / f'10_full_{name}.png'}")

        # 4) 局部截图 (插件容器整体)
        if plugin_root.count() > 0:
            plugin_root.first.screenshot(path=str(OUT / "20_plugin_grid.png"))
            print(f"[05] 插件容器 -> {OUT / '20_plugin_grid.png'}")

        # 5) 局部: 统计区 ch0
        stats_box = page.locator(".fjjl-stats-grid").first
        if stats_box.count() > 0:
            stats_box.screenshot(path=str(OUT / "21_stats_ch0.png"))
            print(f"[06] 统计区 ch0 -> {OUT / '21_stats_ch0.png'}")

        # 6) 局部: SOP 流程串
        sop = page.locator(".fjjl-sop-cell")
        if sop.count() > 0:
            sop.first.screenshot(path=str(OUT / "22_sop_strip.png"))
            print(f"[07] SOP 流程 -> {OUT / '22_sop_strip.png'}")

        # 7) 局部: 步骤统计表 + 按钮
        bottom = page.locator(".fjjl-bottom-cell")
        if bottom.count() > 0:
            bottom.first.screenshot(path=str(OUT / "23_bottom_table.png"))
            print(f"[08] 步骤表 + 按钮 -> {OUT / '23_bottom_table.png'}")

        # 8) 抓表格行数, 验证 GW1 (7) + GW2 (2) = 9 行
        rows = page.locator(".fjjl-stepstats-table tbody tr").count()
        print(f"[09] 步骤表行数 = {rows} (期望 9)")

        # 9) 点 "清零" 按钮看是否生效
        clear_btn = page.locator(".fjjl-btn:has-text('清零')")
        if clear_btn.count() > 0:
            print(f"[10] 找到清零按钮, 点击")
            try:
                clear_btn.first.click(timeout=3000)
                page.wait_for_timeout(2000)
                # 可能弹确认框
                ok_btn = page.locator("button:has-text('确认'), button:has-text('确定'), .el-message-box button.el-button--primary").first
                if ok_btn.count() > 0:
                    ok_btn.click(timeout=2000)
                    print("[11] 确认弹框已点")
                page.screenshot(path=str(OUT / "30_after_clear.png"), full_page=True)
            except Exception as e:
                print(f"[!!] 清零失败: {e}")

        # 10) 抓 ECharts canvas 是否实际渲染
        canvases = page.locator(".fjjl-echart-box canvas").count()
        print(f"[12] ECharts canvas 数量 = {canvases} (期望 >= 2: 每工位 1 个饼 + 1 个仪表盘)")

        # 11) 各项数据真实性 (顶部统计数字)
        numbers = page.locator(".fjjl-stat-num").all_inner_texts()
        print(f"[13] 顶部统计数字 = {numbers}")

        # 12) 项目名
        proj_labels = page.locator(".fjjl-station-label, .fjjl-station-name").all_inner_texts()
        print(f"[14] 工位标签 = {proj_labels}")

        # 输出截图清单
        print(f"\n[完成] 截图目录: {OUT}")
        for f in sorted(OUT.glob("*.png")):
            sz = f.stat().st_size
            print(f"  {f.name}  ({sz//1024} KB)")

        browser.close()


if __name__ == "__main__":
    main()
