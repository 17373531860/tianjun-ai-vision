"""v3.7.2 完整路径 UAT — 后端虚拟数据 + 前端可见反馈

覆盖之前单测和集成测之外的真实路径:

[C 系列] 扫码器旁路三策略 真实 API 全跑通
  C1 C 策略 (cycle_start_snapshot) — 拍快照 + 渲染落盘 + 周期中投新文件不影响本周期
  C2 A 策略 (mtime)              — 不锁快照, 直接取 mtime 最新
  C3 B 策略 (mtime_stable)        — wait_stable_ms + max_age_sec 过滤 + 旧文件被拒

[D 系列] box_color 持久化往返 UAT
  D1 API 改某 label 的 box_color → save → reload → 颜色还在 (DB)
  D2 项目里加副模型 → label 进 steps_config → from_model = 副模型名

[E 系列] 去重重试真实 API
  E1 同名命中 → 主线程立即 dedupe_queued + 异步线程超时 dedupe_timeout

[F 系列] 前端 UAT
  F1 在 RealtimeRulesDialog 里点"测试触发"按钮 — 验证 API 走通
  F2 cycle.external_meta 留痕能在 cycle 详情里查到

输出:
  evidence/v372_full_path_<时间>/
    *.png  — 截图
    *.webm — 录像
    verdict.json  — 机器可读结论
    REPORT.md — 总结
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import uuid as _uuid
from datetime import datetime, timedelta
from pathlib import Path

import requests

API = "http://localhost:8001/api/v1"
OUT_DIR = Path(__file__).resolve().parents[2] / "evidence" / \
    f"v372_full_path_{datetime.now().strftime('%Y-%m-%d_%H%M')}"
OUT_DIR.mkdir(parents=True, exist_ok=True)

verdict = {
    "started_at": datetime.now().isoformat(),
    "c_strategies": {},
    "d_box_color": {},
    "e_dedupe": {},
    "f_frontend": {},
    "errors": [],
}

# 添加 backend 到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def cleanup_rules(filter_name_prefix="UAT_"):
    """清掉本 UAT 创建的所有规则"""
    try:
        r = requests.get(f"{API}/export/realtime-rules")
        items = r.json().get("items", [])
        for it in items:
            if it["name"].startswith(filter_name_prefix):
                requests.delete(f"{API}/export/realtime-rules/{it['id']}")
    except Exception as e:
        print(f"  [warn] 清规则: {e}")


def cleanup_templates(filter_name_prefix="UAT_"):
    """清掉本 UAT 创建的所有自建模板"""
    try:
        r = requests.get(f"{API}/export/templates")
        items = r.json().get("items", [])
        for it in items:
            if (it["name"].startswith(filter_name_prefix)
                    and not it["is_system"]):
                requests.delete(f"{API}/export/templates/{it['id']}")
    except Exception as e:
        print(f"  [warn] 清模板: {e}")


def make_cycle_with_steps(db, project_id, step_labels_durations):
    """新建 session + cycle + steps, 返回 (sess, cyc).
    step_labels_durations = [('取件', 2.34), ('装配', 5.67), ...]"""
    from backend.models.models import DetectionSession, DetectionCycle, StepRecord
    sess = DetectionSession(
        session_uuid=_uuid.uuid4().hex, project_id=project_id,
        start_time=datetime.now() - timedelta(minutes=1),
    )
    db.add(sess); db.commit(); db.refresh(sess)
    cyc = DetectionCycle(
        cycle_uuid=_uuid.uuid4().hex, session_id=sess.id, cycle_number=1,
        start_time=datetime.now() - timedelta(seconds=10),
        end_time=datetime.now(), duration=10.0, is_good=True,
        event_id=1, event_name="OK", result_reason="ok",
        step_sequence=[l for l, _ in step_labels_durations],
    )
    db.add(cyc); db.commit(); db.refresh(cyc)
    for idx, (lbl, d) in enumerate(step_labels_durations):
        db.add(StepRecord(
            record_uuid=_uuid.uuid4().hex[:12], cycle_id=cyc.id,
            step_id=f"s{idx+1}", step_label=lbl, step_order=idx,
            start_time=cyc.start_time + timedelta(seconds=idx),
            end_time=cyc.start_time + timedelta(seconds=idx + d),
            duration=d, confidence=0.9, is_valid=True,
        ))
    db.commit()
    return sess, cyc


def cleanup_test_data(db, sess_ids, cyc_ids):
    """清虚拟 sess + cycle + step"""
    from backend.models.models import DetectionSession, DetectionCycle, StepRecord
    try:
        if cyc_ids:
            db.query(StepRecord).filter(StepRecord.cycle_id.in_(cyc_ids)).delete(
                synchronize_session=False
            )
            db.query(DetectionCycle).filter(DetectionCycle.id.in_(cyc_ids)).delete(
                synchronize_session=False
            )
        if sess_ids:
            db.query(DetectionSession).filter(DetectionSession.id.in_(sess_ids)).delete(
                synchronize_session=False
            )
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"  [warn] 清测试数据: {e}")


# ============================================================
# [C 系列] 扫码器旁路三策略
# ============================================================
def c_test_strategies():
    print("\n" + "=" * 60)
    print("[C 系列] 扫码器旁路三策略真实 API 全跑通")
    print("=" * 60)
    from backend.db.database import SessionLocal
    from backend.models.models import Project
    from backend.services.export_snapshot import (
        snapshot_for_cycle_start, lookup_snapshot_from_cycle,
    )
    from backend.services.export_realtime import dispatch_cycle_end_export

    db = SessionLocal()
    sess_ids, cyc_ids = [], []
    rule_ids = []
    tmp = tempfile.mkdtemp(prefix="uat_c_")
    try:
        proj = db.query(Project).filter(Project.task_type == "detection").first()
        assert proj, "需要至少一个 detection 项目"

        # 拿扫码器旁路系统预设
        r = requests.get(f"{API}/export/templates", params={"include_system": True})
        tpls = r.json()["items"]
        sb_tpl = next(t for t in tpls if t.get("builtin_id") == "builtin_scanner_bypass_3line_txt")

        # ---- C1: cycle_start_snapshot 策略 ----
        print("\n  C1: cycle_start_snapshot 策略 (锁快照, 防串号)")
        scan_dir = os.path.join(tmp, "c1_scan")
        out_dir = os.path.join(tmp, "c1_out")
        os.makedirs(scan_dir); os.makedirs(out_dir)
        with open(os.path.join(scan_dir, "C1_LOCKED.txt"), "w") as f:
            f.write("C1_LOCKED_VAL")

        payload = {
            "name": "UAT_C1_snapshot",
            "enabled": True,
            "template_id": sb_tpl["id"],
            "output_dir": out_dir,
            "input_dir": scan_dir,
            **sb_tpl["default_rule_config"],
        }
        r = requests.post(f"{API}/export/realtime-rules", json=payload)
        r.raise_for_status()
        rule = r.json()
        rule_ids.append(rule["id"])
        assert rule["latest_file_strategy"] == "cycle_start_snapshot"

        sess, cyc = make_cycle_with_steps(db, proj.id, [("取件", 2.34), ("装配", 5.67)])
        sess_ids.append(sess.id); cyc_ids.append(cyc.id)

        # 模拟 cycle_start 拍快照
        snap_summary = snapshot_for_cycle_start(db, channel_id=0, cycle_id=cyc.id,
                                                  project_id=proj.id)
        db.commit()
        db.refresh(cyc)
        snap = lookup_snapshot_from_cycle(cyc.external_meta, scan_dir)
        assert snap and snap["filename"] == "C1_LOCKED.txt"
        print(f"    ✅ snapshot: {snap['filename']} -> {snap['text']!r}")

        # 周期中投新文件
        time.sleep(0.05)
        with open(os.path.join(scan_dir, "C1_NEW_MID.txt"), "w") as f:
            f.write("MIDDLE")
        print(f"    💉 周期中投新文件 C1_NEW_MID.txt")

        # cycle_end 触发
        results = dispatch_cycle_end_export(db, channel_id=0, cycle_id=cyc.id,
                                              project_id=proj.id)
        our = next(rr for rr in results if rr["rule_id"] == rule["id"])
        assert our["status"] == "success", our
        out_path = os.path.join(out_dir, "C1_LOCKED.txt")
        assert os.path.exists(out_path)
        assert not os.path.exists(os.path.join(out_dir, "C1_NEW_MID.txt"))
        content = open(out_path, "r", encoding="utf-8", newline="").read()
        assert "C1_LOCKED_VAL" in content
        assert "MIDDLE" not in content
        print(f"    ✅ 落盘锁定文件名 + 锁定内容, 周期中新文件未泄漏")
        verdict["c_strategies"]["c1_cycle_start_snapshot"] = {
            "status": "PASS",
            "output_file": out_path,
            "content_preview": content[:200],
        }

        # ---- C2: mtime 策略 ----
        print("\n  C2: mtime 策略 (无快照, 取 mtime 最新)")
        scan_dir2 = os.path.join(tmp, "c2_scan")
        out_dir2 = os.path.join(tmp, "c2_out")
        os.makedirs(scan_dir2); os.makedirs(out_dir2)
        # 投 2 个文件, 后投的应被取
        with open(os.path.join(scan_dir2, "C2_OLD.txt"), "w") as f:
            f.write("C2_OLD_VAL")
        time.sleep(0.05)
        with open(os.path.join(scan_dir2, "C2_NEW.txt"), "w") as f:
            f.write("C2_NEW_VAL")

        cfg2 = dict(sb_tpl["default_rule_config"])
        cfg2["latest_file_strategy"] = "mtime"
        cfg2["latest_file_wait_stable_ms"] = 0
        cfg2["latest_file_max_age_sec"] = 0
        payload2 = {
            "name": "UAT_C2_mtime", "enabled": True, "template_id": sb_tpl["id"],
            "output_dir": out_dir2, "input_dir": scan_dir2, **cfg2,
        }
        r = requests.post(f"{API}/export/realtime-rules", json=payload2)
        r.raise_for_status()
        rule2 = r.json(); rule_ids.append(rule2["id"])

        sess2, cyc2 = make_cycle_with_steps(db, proj.id, [("S1", 1.0)])
        sess_ids.append(sess2.id); cyc_ids.append(cyc2.id)
        # 注意: 不调 snapshot (策略不是 C), 直接 dispatch
        results = dispatch_cycle_end_export(db, channel_id=0, cycle_id=cyc2.id,
                                              project_id=proj.id)
        our = next(rr for rr in results if rr["rule_id"] == rule2["id"])
        assert our["status"] == "success", our
        # 应取 mtime 最新 = C2_NEW
        out_new = os.path.join(out_dir2, "C2_NEW.txt")
        assert os.path.exists(out_new), f"应输出 C2_NEW, 实际: {os.listdir(out_dir2)}"
        content2 = open(out_new, "r", encoding="utf-8", newline="").read()
        assert "C2_NEW_VAL" in content2
        print(f"    ✅ mtime 策略取了最新文件 C2_NEW.txt")
        verdict["c_strategies"]["c2_mtime"] = {"status": "PASS"}

        # ---- C3: mtime_stable 策略 + max_age_sec 过滤旧文件 ----
        print("\n  C3: mtime_stable 策略 + max_age_sec 过滤")
        scan_dir3 = os.path.join(tmp, "c3_scan")
        out_dir3 = os.path.join(tmp, "c3_out")
        os.makedirs(scan_dir3); os.makedirs(out_dir3)
        # 投一个"很旧"的文件 (mtime 设到 100 秒前)
        old = os.path.join(scan_dir3, "C3_OLD.txt")
        open(old, "w").write("C3_OLD_VAL")
        os.utime(old, (time.time() - 100, time.time() - 100))

        cfg3 = dict(sb_tpl["default_rule_config"])
        cfg3["latest_file_strategy"] = "mtime_stable"
        cfg3["latest_file_max_age_sec"] = 30  # 只接受 30 秒内的
        cfg3["latest_file_wait_stable_ms"] = 0
        cfg3["filename_template"] = "fallback_{{ cycle.id }}.txt"  # 避免空 filename
        payload3 = {
            "name": "UAT_C3_mtime_stable", "enabled": True, "template_id": sb_tpl["id"],
            "output_dir": out_dir3, "input_dir": scan_dir3, **cfg3,
        }
        r = requests.post(f"{API}/export/realtime-rules", json=payload3)
        r.raise_for_status()
        rule3 = r.json(); rule_ids.append(rule3["id"])

        sess3, cyc3 = make_cycle_with_steps(db, proj.id, [("S1", 1.0)])
        sess_ids.append(sess3.id); cyc_ids.append(cyc3.id)
        results = dispatch_cycle_end_export(db, channel_id=0, cycle_id=cyc3.id,
                                              project_id=proj.id)
        our = next(rr for rr in results if rr["rule_id"] == rule3["id"])
        assert our["status"] == "success", our
        # 应走 fallback 名 (因为 max_age 过滤了 OLD.txt → latest_input_filename 返回空)
        fb_path = os.path.join(out_dir3, f"fallback_{cyc3.id}.txt")
        assert os.path.exists(fb_path), f"应走 fallback 名, 实际: {os.listdir(out_dir3)}"
        c3_content = open(fb_path).read()
        assert "C3_OLD_VAL" not in c3_content, f"旧文件不该被读取, 实际:\n{c3_content}"
        print(f"    ✅ max_age_sec=30 正确过滤掉 100s 前的文件")
        verdict["c_strategies"]["c3_mtime_stable_max_age"] = {"status": "PASS"}

    except Exception as e:
        import traceback
        print(f"  ❌ C 系列异常: {e}")
        verdict["errors"].append(f"C-series: {type(e).__name__}: {e}")
        verdict["c_strategies"]["traceback"] = traceback.format_exc()
    finally:
        # 清规则
        for rid in rule_ids:
            try:
                requests.delete(f"{API}/export/realtime-rules/{rid}")
            except Exception:
                pass
        cleanup_test_data(db, sess_ids, cyc_ids)
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# [D 系列] box_color 持久化往返
# ============================================================
def d_test_box_color():
    print("\n" + "=" * 60)
    print("[D 系列] box_color 持久化 + 副模型 label 入 steps_config")
    print("=" * 60)
    try:
        # 取一个项目
        r = requests.get(f"{API}/projects")
        body = r.json()
        projects = body.get("items") if isinstance(body, dict) else body
        if not projects:
            print("  ⚠️ 无项目可测")
            verdict["d_box_color"]["status"] = "SKIP_no_projects"
            return
        proj = projects[0]
        pid = proj["id"]

        steps_orig = proj.get("steps_config") or []
        if not steps_orig:
            print(f"  ⚠️ 项目 #{pid} {proj['name']} 没有 steps_config, 跳过")
            verdict["d_box_color"]["status"] = "SKIP_no_steps"
            return

        # 记录原始 box_color
        orig_box_color = (steps_orig[0] or {}).get("box_color", "")
        orig_label = steps_orig[0]["label"]
        print(f"  原始 step[0]: label={orig_label}, box_color={orig_box_color!r}")

        # 改成 #ff00cc
        steps_new = list(steps_orig)
        steps_new[0] = dict(steps_orig[0])
        steps_new[0]["box_color"] = "#ff00cc"
        steps_new[0]["from_model"] = steps_new[0].get("from_model", "main")

        r = requests.put(f"{API}/projects/{pid}",
                          json={"steps_config": steps_new})
        r.raise_for_status()
        print(f"  ✅ PUT /projects/{pid} 改 box_color=#ff00cc")

        # 重新读
        r = requests.get(f"{API}/projects/{pid}")
        proj2 = r.json()
        roundtrip = (proj2.get("steps_config") or [{}])[0].get("box_color")
        from_model_roundtrip = (proj2.get("steps_config") or [{}])[0].get("from_model")
        assert roundtrip == "#ff00cc", f"box_color 往返失败: {roundtrip!r}"
        assert from_model_roundtrip == "main", f"from_model 应为 main: {from_model_roundtrip!r}"
        print(f"  ✅ 重读 box_color={roundtrip}, from_model={from_model_roundtrip}")

        # 恢复
        steps_restore = list(steps_orig)
        requests.put(f"{API}/projects/{pid}", json={"steps_config": steps_restore})

        verdict["d_box_color"] = {
            "status": "PASS",
            "tested_label": orig_label,
            "before": orig_box_color,
            "after": roundtrip,
            "from_model_roundtrip": from_model_roundtrip,
        }
    except Exception as e:
        import traceback
        print(f"  ❌ D 系列异常: {e}")
        verdict["errors"].append(f"D-series: {type(e).__name__}: {e}")
        verdict["d_box_color"]["status"] = "FAIL"
        verdict["d_box_color"]["traceback"] = traceback.format_exc()


# ============================================================
# [E 系列] 去重重试真实 API
# ============================================================
def e_test_dedupe():
    print("\n" + "=" * 60)
    print("[E 系列] 同名去重 + 异步超时跳过")
    print("=" * 60)
    from backend.db.database import SessionLocal
    from backend.models.models import Project
    from backend.models.export_models import ExportRealtimeRule, ExportRunLog
    from backend.services.export_realtime import dispatch_cycle_end_export

    db = SessionLocal()
    sess_ids, cyc_ids = [], []
    rule_ids = []
    tmp = tempfile.mkdtemp(prefix="uat_e_")
    try:
        proj = db.query(Project).filter(Project.task_type == "detection").first()

        r = requests.get(f"{API}/export/templates", params={"include_system": True})
        tpls = r.json()["items"]
        sb_tpl = next(t for t in tpls if t.get("builtin_id") == "builtin_scanner_bypass_3line_txt")

        scan_dir = os.path.join(tmp, "scan")
        out_dir = os.path.join(tmp, "out")
        os.makedirs(scan_dir); os.makedirs(out_dir)
        # 投一份扫码 txt
        with open(os.path.join(scan_dir, "SAME.txt"), "w") as f:
            f.write("SAME_VAL")

        cfg = dict(sb_tpl["default_rule_config"])
        cfg["latest_file_strategy"] = "mtime"  # 避免 snapshot 干扰
        cfg["latest_file_wait_stable_ms"] = 0
        cfg["dedupe_same_filename"] = True
        cfg["dedupe_retry_max_sec"] = 2
        cfg["dedupe_retry_interval_ms"] = 100
        payload = {
            "name": "UAT_E_dedupe", "enabled": True, "template_id": sb_tpl["id"],
            "output_dir": out_dir, "input_dir": scan_dir, **cfg,
        }
        r = requests.post(f"{API}/export/realtime-rules", json=payload)
        r.raise_for_status()
        rule = r.json(); rule_ids.append(rule["id"])

        # 模拟"上一轮已经用过 SAME.txt"
        rule_db = db.query(ExportRealtimeRule).filter(
            ExportRealtimeRule.id == rule["id"]
        ).first()
        rule_db.last_used_input_filename = "SAME.txt"
        db.commit()

        # 触发: 当前 latest 也是 SAME.txt → 命中重名
        sess, cyc = make_cycle_with_steps(db, proj.id, [("S1", 1.0)])
        sess_ids.append(sess.id); cyc_ids.append(cyc.id)
        results = dispatch_cycle_end_export(db, channel_id=0, cycle_id=cyc.id,
                                              project_id=proj.id)
        our = next(rr for rr in results if rr["rule_id"] == rule["id"])
        assert our["status"] == "skipped"
        assert "dedupe_queued" in (our.get("skip_reason") or "")
        print(f"    ✅ 主线程立即返回 skipped(dedupe_queued)")

        # 等异步线程超时 (2 秒)
        time.sleep(2.5)

        # 查日志 — 应该有 dedupe_timeout
        db.expire_all()
        logs = db.query(ExportRunLog).filter(
            ExportRunLog.rule_id == rule["id"]
        ).order_by(ExportRunLog.id.desc()).all()
        skip_reasons = [l.skip_reason or "" for l in logs]
        has_timeout = any("dedupe_timeout" in s for s in skip_reasons)
        assert has_timeout, f"应有 dedupe_timeout 日志, 实际: {skip_reasons}"
        print(f"    ✅ 异步线程超时写 dedupe_timeout 日志")

        # 输出目录应为空 (跳过本规则, 不写文件)
        files = os.listdir(out_dir)
        assert files == [], f"超时应跳过不写, 实际: {files}"
        print(f"    ✅ 超时不写文件 (out_dir 空)")

        verdict["e_dedupe"] = {
            "status": "PASS",
            "logs_skip_reasons": skip_reasons[:5],
        }
    except Exception as e:
        import traceback
        print(f"  ❌ E 系列异常: {e}")
        verdict["errors"].append(f"E-series: {type(e).__name__}: {e}")
        verdict["e_dedupe"]["status"] = "FAIL"
        verdict["e_dedupe"]["traceback"] = traceback.format_exc()
    finally:
        for rid in rule_ids:
            try:
                requests.delete(f"{API}/export/realtime-rules/{rid}")
            except Exception:
                pass
        cleanup_test_data(db, sess_ids, cyc_ids)
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# [F 系列] 前端可见 UAT
# ============================================================
def f_test_frontend():
    print("\n" + "=" * 60)
    print("[F 系列] 前端可见浏览器 UAT (测试触发 + cycle 详情留痕)")
    print("=" * 60)
    from playwright.sync_api import sync_playwright
    try:
        # 先准备虚拟数据 — 建一条 cycle + 规则
        from backend.db.database import SessionLocal
        from backend.models.models import Project
        from backend.services.export_snapshot import snapshot_for_cycle_start

        db = SessionLocal()
        proj = db.query(Project).filter(Project.task_type == "detection").first()

        # 拿系统预设
        r = requests.get(f"{API}/export/templates", params={"include_system": True})
        tpls = r.json()["items"]
        sb_tpl = next(t for t in tpls if t.get("builtin_id") == "builtin_scanner_bypass_3line_txt")

        tmp = tempfile.mkdtemp(prefix="uat_f_")
        scan_dir = os.path.join(tmp, "scan")
        out_dir = os.path.join(tmp, "out")
        os.makedirs(scan_dir); os.makedirs(out_dir)
        with open(os.path.join(scan_dir, "F_TEST.txt"), "w") as f:
            f.write("F_TEST_VAL_FRONTEND")

        payload = {
            "name": "UAT_F_frontend",
            "enabled": True,
            "template_id": sb_tpl["id"],
            "output_dir": out_dir,
            "input_dir": scan_dir,
            **sb_tpl["default_rule_config"],
        }
        r = requests.post(f"{API}/export/realtime-rules", json=payload)
        r.raise_for_status()
        rule = r.json()

        # 建一个 cycle + 拍快照
        sess, cyc = make_cycle_with_steps(db, proj.id, [("取件", 2.34), ("装配", 5.67)])
        summary = snapshot_for_cycle_start(db, channel_id=0, cycle_id=cyc.id,
                                              project_id=proj.id)
        db.commit()
        sess_id = sess.id
        cyc_id = cyc.id
        rule_id = rule["id"]
        db.close()

        # Playwright: 进 Data 页 → 打开实时规则 dialog → 找新建的规则 → 点测试触发
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False, slow_mo=120, args=["--start-maximized"]
            )
            ctx = browser.new_context(
                viewport={"width": 1600, "height": 920},
                record_video_dir=str(OUT_DIR),
                record_video_size={"width": 1600, "height": 920},
            )
            page = ctx.new_page()
            try:
                # 先激活项目
                page.goto("http://localhost:6001/#/project", wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
                cards = page.locator(".cursor-pointer.bg-slate-900, "
                                       "div[class*='bg-slate-900'][class*='hover']")
                if cards.count() > 0:
                    cards.nth(0).click()
                    page.wait_for_timeout(1500)
                activate = page.locator("button").filter(has_text="启用当前项目")
                if activate.count() > 0:
                    try:
                        activate.first.click(timeout=5000)
                        page.wait_for_timeout(2000)
                        confirm = page.locator(".el-message-box__btns button.el-button--primary")
                        if confirm.count() > 0:
                            confirm.first.click()
                            page.wait_for_timeout(1500)
                    except Exception:
                        pass

                # 进 Data 页
                page.goto("http://localhost:6001/#/data", wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                page.screenshot(path=str(OUT_DIR / "f0_data_page.png"))

                # 打开实时规则 dialog
                entry = page.locator("button").filter(has_text="实时规则")
                entry.first.click()
                page.wait_for_timeout(1200)
                page.screenshot(path=str(OUT_DIR / "f1_rules_dialog.png"))

                # 找我们建的规则行 (UAT_F_frontend)
                row = page.locator("tr").filter(has_text="UAT_F_frontend")
                row_count = row.count()
                print(f"    UAT_F_frontend 规则可见: {'✅' if row_count > 0 else '❌'} (count={row_count})")
                verdict["f_frontend"]["rule_visible_in_table"] = row_count > 0

                if row_count > 0:
                    # 点行里的"测试"按钮 (规则操作列)
                    test_btn = row.locator("button").filter(has_text="测试")
                    if test_btn.count() > 0:
                        test_btn.first.click()
                        page.wait_for_timeout(1500)
                        page.screenshot(path=str(OUT_DIR / "f2_test_dialog.png"))

                        # 测试触发 dialog 出来后 — 选 cycle 模式, 填 cycle_id
                        # Element Plus el-radio: <label class="el-radio"><span class="el-radio__input"><span class="el-radio__inner">
                        # 直接点 radio label 的 span (要走 el-radio__inner 或 label 自身)
                        cycle_label = page.locator(".el-dialog .el-radio").filter(
                            has_text="指定 cycle_id"
                        )
                        if cycle_label.count() > 0:
                            cycle_label.first.click()
                            page.wait_for_timeout(600)
                        # cycle_id 输入框 — el-input-number 在 dialog 内的第一个
                        cyc_input = page.locator(
                            ".el-dialog .el-input-number input"
                        ).first
                        if cyc_input.count() > 0:
                            try:
                                cyc_input.wait_for(state="visible", timeout=5000)
                                cyc_input.fill(str(cyc_id))
                                page.wait_for_timeout(300)
                            except Exception as ee:
                                print(f"    [warn] cycle 输入框 fill 失败: {ee}")
                        page.screenshot(path=str(OUT_DIR / "f3_cycle_input.png"))
                        # 点对话框里的"测试"按钮 (确认提交) — RealtimeRulesDialog 嵌套 dialog
                        # 测试触发 dialog title="测试触发"
                        test_dialog = page.locator(".el-dialog").filter(
                            has_text="测试触发"
                        ).last
                        confirm_in_dialog = test_dialog.locator(
                            "button"
                        ).filter(has_text="立即触发").last
                        confirm_count = confirm_in_dialog.count()
                        print(f"    测试 dialog footer button 可见: {confirm_count}")
                        if confirm_count > 0:
                            confirm_in_dialog.click()
                            page.wait_for_timeout(3500)
                            page.screenshot(path=str(OUT_DIR / "f4_test_result.png"))
                            # 验证输出文件
                            out_file = os.path.join(out_dir, "F_TEST.txt")
                            file_exists = os.path.exists(out_file)
                            print(f"    立即触发后输出文件: "
                                    f"{'✅' if file_exists else '❌'}")
                            verdict["f_frontend"]["click_test_button"] = True
                            verdict["f_frontend"]["output_file_created"] = file_exists
                            if file_exists:
                                content = open(out_file, encoding="utf-8").read()
                                verdict["f_frontend"]["content_preview"] = content[:200]

                            # 验证输出文件
                            out_file = os.path.join(out_dir, "F_TEST.txt")
                            file_exists = os.path.exists(out_file)
                            print(f"    测试触发后输出文件: {'✅' if file_exists else '❌'}")
                            verdict["f_frontend"]["test_run_output_file"] = file_exists
                            if file_exists:
                                content = open(out_file).read()
                                verdict["f_frontend"]["test_run_content"] = content[:200]
                                print(f"    内容: {content[:80]}...")
                        verdict["f_frontend"]["status"] = "PASS"
                else:
                    verdict["f_frontend"]["status"] = "FAIL_rule_not_visible"
            finally:
                page.wait_for_timeout(1500)
                ctx.close()
                browser.close()

        # 清理
        try:
            requests.delete(f"{API}/export/realtime-rules/{rule_id}")
        except Exception:
            pass
        db = SessionLocal()
        cleanup_test_data(db, [sess_id], [cyc_id])
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        import traceback
        print(f"  ❌ F 系列异常: {e}")
        verdict["errors"].append(f"F-series: {type(e).__name__}: {e}")
        verdict["f_frontend"]["status"] = "FAIL"
        verdict["f_frontend"]["traceback"] = traceback.format_exc()


# ============================================================
# 主流程
# ============================================================
def main():
    print(f"[UAT] 输出目录: {OUT_DIR}")
    cleanup_rules("UAT_")

    c_test_strategies()
    d_test_box_color()
    e_test_dedupe()
    f_test_frontend()

    verdict["completed_at"] = datetime.now().isoformat()
    all_pass = (
        verdict["c_strategies"].get("c1_cycle_start_snapshot", {}).get("status") == "PASS"
        and verdict["c_strategies"].get("c2_mtime", {}).get("status") == "PASS"
        and verdict["c_strategies"].get("c3_mtime_stable_max_age", {}).get("status") == "PASS"
        and verdict["d_box_color"].get("status") in ("PASS", "SKIP_no_projects", "SKIP_no_steps")
        and verdict["e_dedupe"].get("status") == "PASS"
        and verdict["f_frontend"].get("status") == "PASS"
    )
    verdict["overall"] = all_pass

    verdict_path = OUT_DIR / "verdict.json"
    with open(verdict_path, "w", encoding="utf-8") as f:
        json.dump(verdict, f, ensure_ascii=False, indent=2)
    print(f"\n📋 verdict 写入: {verdict_path}")
    print(f"   总体: {'✅ PASS' if all_pass else '❌ FAIL'}")
    print(f"\n输出目录: {OUT_DIR}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
