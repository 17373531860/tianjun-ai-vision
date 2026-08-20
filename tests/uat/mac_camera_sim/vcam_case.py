"""哈金森一拖三摄像头问题仿真 (Mac 内置摄像头, v3.51.1 代码).

场景对照:
  S1 正常打开: 摄像头启动→真实画面(非占位图)
  S2 外部进程占用时打开: 明确报错或成功(mac 可共享), 后端不崩
  S3 双通道抢同一物理摄像头(哈金森"被占用"主场景): 行为可预期、报错清晰、不崩
  S4 运行中调参重开(哈金森"调参后另一个坏"): 生命周期锁保护, 不串不崩
  S5 快速开停循环+并发stop(捷昌0xC0000374同族): 后端存活
  S6 杀进程重启(模拟关开软件): 自动恢复把摄像头拉起来
"""
import sys, time, subprocess, os, signal
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "virtual_dual_station"))
from vhelp import api, check, summary

B = "http://localhost:8001/api/v1"


def snapshot_size(ch):
    import requests
    r = requests.get(f"http://localhost:8001/snapshot?channel={ch}", timeout=10)
    return len(r.content)


def det(ch):
    return api("GET", f"/source/detection/results?channel={ch}").json()


def cam_start(ch, idx=0, **kw):
    body = {"device_index": idx, "width": 1280, "height": 720, "fps": 30,
            "auto_exposure": True, "exposure_value": -6.0}
    body.update(kw)
    return api("POST", f"/source/camera/start?channel={ch}", json=body)


def cam_stop(ch):
    return api("POST", f"/source/camera/stop?channel={ch}")


PLACEHOLDER_MAX = 12000  # 640x480 黑底占位 jpg 约 6-8KB; 真实 720p 画面远大于此

# ---------- S1 正常打开 ----------
r = cam_start(0)
check("S1 摄像头启动接口成功", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
time.sleep(3)
st = det(0)
sz = snapshot_size(0)
check("S1 通道在跑且有真实画面", st.get("is_running") and sz > PLACEHOLDER_MAX,
      f"is_running={st.get('is_running')} snapshot={sz}B")

# ---------- S4 运行中调参重开 (哈金森: 调参后摄像头轮着坏) ----------
for i, (w, h, ae) in enumerate([(640, 480, False), (1280, 720, True)]):
    r = cam_start(0, width=w, height=h, auto_exposure=ae, exposure_value=-5.0)
    check(f"S4.{i+1} 运行中改参数重开({w}x{h},AE={ae})", r.status_code == 200,
          f"{r.status_code} {r.text[:120]}")
    time.sleep(2.5)
    sz = snapshot_size(0)
    check(f"S4.{i+1} 重开后画面恢复", sz > PLACEHOLDER_MAX, f"snapshot={sz}B")

# ---------- S3 双通道抢同一物理摄像头 ----------
r = cam_start(1, idx=0)
time.sleep(3)
st0, st1 = det(0), det(1)
sz0 = snapshot_size(0)
ok_defined = (r.status_code == 200) or (r.status_code == 500 and "占用" in r.text)
check("S3 第二通道抢同一摄像头: 行为可预期(成功共享或明确报占用)", ok_defined,
      f"ch1_resp={r.status_code} {r.text[:100]}")
check("S3 ch0 不被 ch1 拖死(仍有画面)", st0.get("is_running") and sz0 > PLACEHOLDER_MAX,
      f"is_running={st0.get('is_running')} snapshot={sz0}B")
cam_stop(1)
time.sleep(1)
sz0b = snapshot_size(0)
check("S3 ch1 停止后 ch0 摄像头不被误释放", sz0b > PLACEHOLDER_MAX,
      f"snapshot={sz0b}B")

# ---------- S2 外部进程占用 ----------
holder = subprocess.Popen(
    ["/Users/tianjun/miniconda3/envs/tianjun/bin/python", "-c",
     "import cv2,time; c=cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION); "
     "print('holder opened', c.isOpened(), flush=True); time.sleep(20)"],
    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
time.sleep(4)
cam_stop(0); time.sleep(1)
r = cam_start(0)
time.sleep(2.5)
sz = snapshot_size(0)
occupied_msg = r.status_code == 500 and ("占用" in r.text or "无法打开" in r.text)
check("S2 外部占用下打开: 成功共享或报错含'占用'指引", r.status_code == 200 or occupied_msg,
      f"{r.status_code} {r.text[:120]}")
holder.send_signal(signal.SIGKILL)
if r.status_code != 200:
    time.sleep(1)
    r2 = cam_start(0)
    check("S2 占用者退出后重开成功", r2.status_code == 200, f"{r2.status_code}")
    time.sleep(2)

# ---------- S5 快速开停循环 (并发释放竞态) ----------
alive = True
for i in range(6):
    cam_stop(0)
    r = cam_start(0)
    if r.status_code != 200:
        time.sleep(1.5)
        r = cam_start(0)  # mac 释放有延迟, 一次退避重试
    if r.status_code != 200:
        alive = False
        print(f"  循环{i}: {r.status_code} {r.text[:80]}")
        break
    time.sleep(0.8)
try:
    hp = api("GET", "/projects").status_code == 200
except Exception:
    hp = False
check("S5 快速开停×6 后端存活不崩", alive and hp, f"alive={alive} health={hp}")
time.sleep(2)
check("S5 循环后画面正常", snapshot_size(0) > PLACEHOLDER_MAX, f"{snapshot_size(0)}B")

print("\n(S6 杀进程重启场景由外层脚本驱动)")
sys.exit(1 if summary() else 0)
