"""参数核验: DB 项目 3~7 的落库配置逐字段对照调参交付文档.

对照源: docs/tuning/LG工时_5工位_2026-08-06.json (工程师交付文档)
核验项: logic_mode / detection_steps / 每步 threshold / min_frames /
        disappear_delay / disappear_uninterruptible / min_duration / value_type
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = json.loads((ROOT / "docs/tuning/LG工时_5工位_2026-08-06.json").read_text())
conn = sqlite3.connect(ROOT / "backend/sql_app.db")

errors = []


def check(name, ok, detail=""):
    if not ok:
        errors.append(f"{name} :: {detail}")
        print(f"[FAIL] {name} :: {detail}")


for s in DOC["sets"]:
    pid, model = s["project_id"], s["model"]
    row = conn.execute(
        "SELECT logic_mode, steps_config, pipeline_config FROM projects WHERE id=?", (pid,)
    ).fetchone()
    if not row:
        check(f"P{pid}({model}) 存在", False, "项目不存在")
        continue
    logic_mode, steps_raw, pipe_raw = row
    steps = {st["label"]: st for st in json.loads(steps_raw)}
    pipe = json.loads(pipe_raw or "{}")

    check(f"P{pid}({model}) logic_mode", logic_mode == "detection", logic_mode)

    doc_required = set(s["final_config"]["detection_steps"])
    db_ids = pipe.get("detection_steps") or []
    id2label = {st["id"]: st["label"] for st in json.loads(steps_raw)}
    db_required = {id2label.get(i, f"?{i}") for i in db_ids}
    check(f"P{pid}({model}) detection_steps", db_required == doc_required,
          f"db={sorted(db_required)} doc={sorted(doc_required)}")

    for d in s["final_config"]["steps"]:
        label = d["label"]
        st = steps.get(label)
        if st is None:
            check(f"P{pid} 步骤[{label}]", False, "DB 缺该步骤")
            continue
        pairs = [
            ("threshold", st.get("threshold"), d["threshold"]),
            ("min_frames", st.get("min_frames"), d["min_frames"]),
            ("disappear_delay", st.get("disappear_delay"), d["disappear_delay"]),
            ("uninterruptible", bool(st.get("disappear_uninterruptible")), d["disappear_uninterruptible"]),
            ("min_duration", st.get("min_duration"), d.get("min_duration")),
            ("value_type", (st.get("plugin_data") or {}).get("lg-worktime", {}).get("value_type"),
             d["value_type"]),
        ]
        for pname, got, want in pairs:
            check(f"P{pid} [{label}].{pname}", got == want, f"db={got} doc={want}")

n_checked = sum(len(s["final_config"]["steps"]) * 6 + 2 for s in DOC["sets"])
print(f"\n共核验 {n_checked} 项," , "全部一致" if not errors else f"{len(errors)} 项不一致")
sys.exit(1 if errors else 0)
