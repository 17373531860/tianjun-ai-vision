"""S6: 杀进程重启模拟"关软件再开", 验证摄像头自动恢复 (含前端竞态干扰轮)."""
import sys, time, os, signal, subprocess, requests
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "virtual_dual_station"))
from vhelp import api, check, summary

PY = "/Users/tianjun/miniconda3/envs/tianjun/bin/python"
REPO = "/Users/tianjun/Projects/tianjun-ai-vision"


def snapshot_size(ch):
    r = requests.get(f"http://localhost:8001/snapshot?channel={ch}", timeout=10)
    return len(r.content)


def backend_pid():
    out = subprocess.run(["pgrep", "-f", "uvicorn backend.main:app.*8001"],
                         capture_output=True, text=True).stdout.split()
    return [int(p) for p in out]


def wait_health(timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if requests.get("http://localhost:8001/api/v1/projects", timeout=2).ok:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def restart_backend(kill_hard=True):
    for p in backend_pid():
        os.kill(p, signal.SIGKILL if kill_hard else signal.SIGTERM)
    time.sleep(2)
    env = dict(os.environ, RUNTIME_MODE="test")
    f = open("/tmp/vtest_backend.log", "a")
    subprocess.Popen([PY, "-u", "-m", "uvicorn", "backend.main:app",
                      "--host", "0.0.0.0", "--port", "8001"],
                     stdout=f, stderr=subprocess.STDOUT, env=env, cwd=REPO,
                     start_new_session=True)
    return wait_health()


PLACEHOLDER_MAX = 12000

# 0) 落盘 ch0 源配置(和前端 Source 页保存一致) + 启动摄像头
r = api("PUT", "/workstations/channel-config", json={
    "channel_id": 0, "source_type": "camera", "device_index": 0,
    "resolution": "1280x720", "fps": 15,
    "auto_exposure": True, "exposure_value": -6.0})
check("S6.0 源配置落盘", r.status_code == 200, f"{r.status_code}")
api("POST", "/source/camera/start?channel=0",
    json={"device_index": 0, "width": 1280, "height": 720, "fps": 15,
          "auto_exposure": True, "exposure_value": -6.0})
time.sleep(3)
check("S6.0 杀前画面正常", snapshot_size(0) > PLACEHOLDER_MAX, f"{snapshot_size(0)}B")

# 1) 摄像头运行中 kill -9 (模拟强关软件/崩溃) → 重启 → 自动恢复
ok = restart_backend(kill_hard=True)
check("S6.1 强杀后重启, 后端健康", ok, "")
time.sleep(12)  # 给 auto_restore 首轮+重试时间
sz = snapshot_size(0)
if sz <= PLACEHOLDER_MAX:
    time.sleep(10)  # 收尾兜底轮 (首轮3s重试+5s兜底)
    sz = snapshot_size(0)
check("S6.1 重启后摄像头自动恢复出真实画面", sz > PLACEHOLDER_MAX, f"{sz}B")

# 2) 带前端竞态干扰的重启: 起来的瞬间就打 set_channel_count(模拟开机首屏)
ok = restart_backend(kill_hard=True)
check("S6.2 二次强杀重启, 后端健康", ok, "")
try:
    r = api("POST", "/workstations/mode", json={"channel_count": 2})
    print(f"  干扰: set_channel_count(2) -> {r.status_code}")
except Exception as e:
    print(f"  干扰调用异常(可接受): {e}")
time.sleep(14)
sz = snapshot_size(0)
if sz <= PLACEHOLDER_MAX:
    time.sleep(10)
    sz = snapshot_size(0)
check("S6.2 干扰下收尾兜底轮仍恢复画面", sz > PLACEHOLDER_MAX, f"{sz}B")

r = api("GET", "/source/detection/results?channel=0").json()
check("S6.3 恢复后通道状态一致(is_running+camera)",
      r.get("is_running") and r.get("source_type") == "camera",
      f"is_running={r.get('is_running')} type={r.get('source_type')}")

sys.exit(1 if summary() else 0)
