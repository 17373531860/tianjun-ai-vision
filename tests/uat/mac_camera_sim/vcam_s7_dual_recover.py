"""S7: 捷昌双工位完整自动恢复 — 模型+视频源+检测中, 直接杀软件重开.

链路: 上传真模型 → 两项目各绑模型 → ch0/ch1 各绑各项目 + 相机源落盘
     → 双工位开检测 → kill -9 → 重启 → 三件套(源/模型/检测)全自动回来且不串.
"""
import sys, time, os, signal, subprocess, requests
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "virtual_dual_station"))
from vhelp import api, check, summary

PY = "/Users/tianjun/miniconda3/envs/tianjun/bin/python"
REPO = "/Users/tianjun/Projects/tianjun-ai-vision"
B = "http://localhost:8001/api/v1"
PLACEHOLDER_MAX = 12000


def snap(ch):
    return len(requests.get(f"http://localhost:8001/snapshot?channel={ch}", timeout=10).content)


def det(ch):
    return api("GET", f"/source/detection/results?channel={ch}").json()


def wait_health(timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if requests.get(f"{B}/projects", timeout=2).ok:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


# ---------- 准备: 模型 ----------
models = api("GET", "/models").json()
mlist = models.get("items", models) if isinstance(models, dict) else models
mid = None
for m in mlist:
    if m.get("name") == "__vt_best":
        mid = m["id"]
if mid is None:
    with open("/Users/tianjun/Downloads/best.pt", "rb") as f:
        r = requests.post(f"{B}/models/upload",
                          files={"file": ("best.pt", f, "application/octet-stream")},
                          data={"name": "__vt_best", "framework": "PyTorch"},
                          timeout=300)
    check("S7.0 模型上传", r.status_code == 201, f"{r.status_code} {r.text[:120]}")
    mid = r.json()["id"]
print(f"  model_id={mid}")

# 两个虚拟项目各绑模型 (default_model_id)
for pid, tag in ((2, "工位0"), (3, "工位1")):
    r = api("PUT", f"/projects/{pid}", json={"default_model_id": mid,
                                             "model_format": "pt"})
    check(f"S7.0 项目{pid}({tag})绑模型", r.status_code == 200,
          f"{r.status_code} {r.text[:100]}")

# ---------- 准备: 双工位源配置落盘 + 项目绑定 ----------
for ch, pid in ((0, 2), (1, 3)):
    r = api("PUT", "/workstations/channel-config", json={
        "channel_id": ch, "source_type": "camera", "device_index": 0,
        "resolution": "1280x720", "fps": 15, "auto_exposure": True,
        "exposure_value": -6.0, "project_id": pid, "was_detecting": True})
    check(f"S7.1 ch{ch} 源+项目绑定落盘", r.status_code == 200, f"{r.status_code}")

# 各工位激活各自项目 (加载模型)
for ch, pid in ((0, 2), (1, 3)):
    r = api("POST", f"/projects/{pid}/activate?channel={ch}")
    check(f"S7.1 ch{ch} 激活项目{pid}", r.status_code == 200,
          f"{r.status_code} {r.text[:100]}")

# 开相机 + 开检测
for ch in (0, 1):
    api("POST", f"/source/camera/start?channel={ch}",
        json={"device_index": 0, "width": 1280, "height": 720, "fps": 15,
              "auto_exposure": True, "exposure_value": -6.0})
time.sleep(3)
for ch in (0, 1):
    r = api("POST", f"/source/detection/start?channel={ch}", json={})
    check(f"S7.2 ch{ch} 开始检测", r.status_code == 200, f"{r.status_code} {r.text[:100]}")
time.sleep(6)
for ch in (0, 1):
    st = det(ch)
    check(f"S7.2 ch{ch} 杀前: 源在跑+检测中+真画面",
          st.get("is_running") and st.get("is_detecting") and snap(ch) > PLACEHOLDER_MAX,
          f"run={st.get('is_running')} det={st.get('is_detecting')} snap={snap(ch)}B")

# ---------- 杀软件 ----------
pids = subprocess.run(["pgrep", "-f", "uvicorn backend.main:app.*8001"],
                      capture_output=True, text=True).stdout.split()
for p in pids:
    os.kill(int(p), signal.SIGKILL)
print(f"  已 kill -9 后端 {pids}")
time.sleep(2)

env = dict(os.environ, RUNTIME_MODE="test")
f = open("/tmp/vtest_backend.log", "a")
f.write("\n===== S7 RESTART (dual recover) =====\n")
subprocess.Popen([PY, "-u", "-m", "uvicorn", "backend.main:app",
                  "--host", "0.0.0.0", "--port", "8001"],
                 stdout=f, stderr=subprocess.STDOUT, env=env, cwd=REPO,
                 start_new_session=True)
check("S7.3 重启后端健康", wait_health(), "")

# ---------- 等自动恢复 (源恢复+模型加载+检测自动开始, 含收尾兜底轮) ----------
deadline = time.time() + 90
state = {}
while time.time() < deadline:
    state = {ch: det(ch) for ch in (0, 1)}
    if all(s.get("is_running") and s.get("is_detecting") for s in state.values()):
        break
    time.sleep(3)

for ch in (0, 1):
    s = state.get(ch, {})
    check(f"S7.4 ch{ch} 重启后视频源自动恢复", s.get("is_running") and
          s.get("source_type") == "camera",
          f"run={s.get('is_running')} type={s.get('source_type')}")
    check(f"S7.4 ch{ch} 重启后检测自动开始", bool(s.get("is_detecting")),
          f"det={s.get('is_detecting')}")
    sz = snap(ch)
    check(f"S7.4 ch{ch} 重启后真实画面", sz > PLACEHOLDER_MAX, f"{sz}B")

# ---------- 项目/模型不串: 各工位仍绑各自项目 ----------
srcs = api("GET", "/workstations/").json()
cfg = srcs.get("source_configs", {})
p0 = (cfg.get("0") or {}).get("project_id")
p1 = (cfg.get("1") or {}).get("project_id")
check("S7.5 ch0 仍绑项目2(大件), ch1 仍绑项目3(小件), 不串",
      p0 == 2 and p1 == 3, f"ch0->{p0} ch1->{p1}")

sys.exit(1 if summary() else 0)
