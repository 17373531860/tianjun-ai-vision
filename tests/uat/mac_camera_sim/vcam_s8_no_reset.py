"""S8: 针对四连问的苛刻验证.

  Q1 双工位绑不同模型, 杀软件重启后各自加载各自的, 不串不重置
  Q2 停止再开始 x3, 每轮画面都要回来, 不黑屏不卡住
  Q3 停止后改参数再开始, 参数生效且绑定/模型不被重置
  Q4 以上每步之后都验一次快照非占位图 (黑屏=fail)
"""
import sys, time, os, signal, subprocess, requests, cv2, numpy as np
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "virtual_dual_station"))
from vhelp import api, check, summary

PY = "/Users/tianjun/miniconda3/envs/tianjun/bin/python"
REPO = "/Users/tianjun/Projects/tianjun-ai-vision"
B = "http://localhost:8001/api/v1"
LOG = "/tmp/vtest_backend.log"
PLACEHOLDER_MAX = 12000


def snap_info(ch):
    """返回 (bytes, w, h) — 解码快照真实尺寸, 黑屏占位图恒为 640x480/10KB."""
    raw = requests.get(f"http://localhost:8001/snapshot?channel={ch}", timeout=10).content
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2] if img is not None else (0, 0)
    return len(raw), w, h


def status(ch):
    return api("GET", f"/source/status?channel={ch}").json()


def bindings():
    cfg = api("GET", "/workstations/").json().get("source_configs", {})
    return ((cfg.get("0") or {}).get("project_id"),
            (cfg.get("1") or {}).get("project_id"))


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


# ---------- Q1 准备: 第二个不同模型给工位1 ----------
models = api("GET", "/models").json()
mlist = models.get("items", models) if isinstance(models, dict) else models
mid_b = None
for m in mlist:
    if m.get("name") == "__vt_best_b":
        mid_b = m["id"]
if mid_b is None:
    with open("/Users/tianjun/Projects/tianjun-sy8/tests/uat/assets/sy9_packing_best.pt", "rb") as f:
        r = requests.post(f"{B}/models/upload",
                          files={"file": ("sy9_packing_best.pt", f, "application/octet-stream")},
                          data={"name": "__vt_best_b", "framework": "PyTorch"},
                          timeout=300)
    check("Q1.0 模型B上传", r.status_code == 201, f"{r.status_code} {r.text[:100]}")
    mid_b = r.json()["id"]
print(f"  model_b_id={mid_b}")

r = api("PUT", "/projects/3", json={"default_model_id": mid_b, "model_format": "pt"})
check("Q1.0 项目3(工位1)换绑模型B", r.status_code == 200, f"{r.status_code}")
r = api("POST", "/projects/3/activate?channel=1")
check("Q1.0 ch1 重新激活项目3", r.status_code == 200, f"{r.status_code} {r.text[:80]}")
time.sleep(4)

# ---------- Q1: 杀软件重启, 各自加载各自模型 ----------
log_pos = os.path.getsize(LOG)
pids = subprocess.run(["pgrep", "-f", "uvicorn backend.main:app.*8001"],
                      capture_output=True, text=True).stdout.split()
for p in pids:
    os.kill(int(p), signal.SIGKILL)
time.sleep(2)
env = dict(os.environ, RUNTIME_MODE="test")
f = open(LOG, "a")
subprocess.Popen([PY, "-u", "-m", "uvicorn", "backend.main:app",
                  "--host", "0.0.0.0", "--port", "8001"],
                 stdout=f, stderr=subprocess.STDOUT, env=env, cwd=REPO,
                 start_new_session=True)
check("Q1.1 强杀重启健康", wait_health(), "")

deadline = time.time() + 90
while time.time() < deadline:
    s0, s1 = status(0), status(1)
    if all(s.get("is_running") and s.get("is_detecting") for s in (s0, s1)):
        break
    time.sleep(3)

with open(LOG, encoding="utf-8", errors="replace") as fh:
    fh.seek(log_pos)
    newlog = fh.read()
check("Q1.2 重启后 ch0 加载的是模型A(__vt_best)",
      "ch0 加载模型: __vt_best\n" in newlog or "ch0 加载模型: __vt_best" in
      "\n".join(l for l in newlog.splitlines() if "ch0 加载模型" in l and "__vt_best_b" not in l),
      [l for l in newlog.splitlines() if "加载模型" in l])
check("Q1.2 重启后 ch1 加载的是模型B(__vt_best_b)",
      any("ch1 加载模型: __vt_best_b" in l for l in newlog.splitlines()),
      [l for l in newlog.splitlines() if "ch1" in l and "模型" in l][:3])
p0, p1 = bindings()
check("Q1.3 重启后绑定不重置 ch0->2 ch1->3", p0 == 2 and p1 == 3, f"{p0},{p1}")
for ch in (0, 1):
    s = status(ch)
    sz, w, h = snap_info(ch)
    check(f"Q1.4 ch{ch} 重启后检测中+真画面", s.get("is_detecting") and sz > PLACEHOLDER_MAX,
          f"det={s.get('is_detecting')} snap={sz}B {w}x{h}")

# ---------- Q2: 停止再开始 x3, 画面每轮都要回来 ----------
for i in range(3):
    api("POST", "/source/camera/stop?channel=0")
    time.sleep(1)
    sz, w, h = snap_info(0)
    check(f"Q2.{i+1}a 停止后快照退回占位图(明确停了, 而不是卡死帧)", sz <= PLACEHOLDER_MAX,
          f"{sz}B")
    r = api("POST", "/source/camera/start?channel=0",
            json={"device_index": 0, "width": 1280, "height": 720, "fps": 15,
                  "auto_exposure": True, "exposure_value": -6.0})
    time.sleep(3)
    sz, w, h = snap_info(0)
    check(f"Q2.{i+1}b 再开始画面回来(不黑屏不卡住)", r.status_code == 200 and
          sz > PLACEHOLDER_MAX, f"{r.status_code} snap={sz}B {w}x{h}")
p0, p1 = bindings()
check("Q2.4 三轮开停后绑定不重置", p0 == 2 and p1 == 3, f"{p0},{p1}")
# 前端真实姿势: 开始检测总是带项目模型路径 (Monitor startDetectionForChannel)
_ms = api("GET", "/models").json()
_ml = _ms.get("items", _ms) if isinstance(_ms, dict) else _ms
MODEL_A_PATH = next(m["file_path"] for m in _ml if m["name"] == "__vt_best")
r = api("POST", "/source/detection/start?channel=0",
        json={"model_path": MODEL_A_PATH})
time.sleep(4)
check("Q2.5 开停后检测能再开(前端姿势带model_path)", r.status_code == 200 and
      status(0).get("is_detecting"), f"{r.status_code} {r.text[:80]}")

# ---------- Q3: 停止后改参数再开始 ----------
api("POST", "/source/camera/stop?channel=0")
time.sleep(1)
r = api("POST", "/source/camera/start?channel=0",
        json={"device_index": 0, "width": 640, "height": 480, "fps": 10,
              "auto_exposure": False, "exposure_value": -5.0})
time.sleep(3)
s = status(0)
sz, w, h = snap_info(0)
check("Q3.1 改参数后画面出来(不黑屏)", r.status_code == 200 and sz > PLACEHOLDER_MAX,
      f"{r.status_code} snap={sz}B")
check("Q3.2 新参数真生效(640x480)", s.get("width") == 640 and s.get("height") == 480
      and w == 640 and h == 480, f"status={s.get('width')}x{s.get('height')} snap={w}x{h}")
p0, p1 = bindings()
check("Q3.3 改参数不重置绑定", p0 == 2 and p1 == 3, f"{p0},{p1}")
r = api("POST", "/source/detection/start?channel=0",
        json={"model_path": MODEL_A_PATH})
time.sleep(4)
s = status(0)
check("Q3.4 改参数后检测能开(前端姿势)", r.status_code == 200 and
      s.get("is_detecting") is True, f"{r.status_code} {r.text[:80]}")
check("Q3.5 检测开后模型在位", s.get("model_loaded") is True,
      f"{s.get('model_loaded')}")
# ch1 全程不该被殃及
s1 = status(1)
sz1, _, _ = snap_info(1)
check("Q3.6 ch1 全程不被 ch0 的折腾殃及", s1.get("is_running") and
      s1.get("is_detecting") and sz1 > PLACEHOLDER_MAX,
      f"run={s1.get('is_running')} det={s1.get('is_detecting')} snap={sz1}B")

# 恢复 ch0 参数
api("POST", "/source/camera/stop?channel=0"); time.sleep(1)
api("POST", "/source/camera/start?channel=0",
    json={"device_index": 0, "width": 1280, "height": 720, "fps": 15,
          "auto_exposure": True, "exposure_value": -6.0})

sys.exit(1 if summary() else 0)
