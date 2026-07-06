#!/usr/bin/env python3
"""端点文档门禁（防腐三件套之三，基线法，参照微软 CS1591 编译告警思路）。

对 router_manifest 挂载的全部主程序路由检查"端点文档军规"四件套中可静态判定的三件：
  1. summary 非空
  2. 有响应体承诺（response_model 或 status_code=204 或 response_class）
  3. docstring 非空

基线规则（docs/dev/conventions/端点文档军规.md 第四节）：
  - 存量欠账固化在 doc_endpoint_baseline.json：不报错，但基线只许缩不许涨
  - 新端点/被改动端点不在基线 -> 缺文档直接 CI 红
  - 基线里某端点已合规 -> 提示可从基线删除（用 --prune 自动删）

用法：
  python scripts/ci/check_doc_endpoints.py            # 检查
  python scripts/ci/check_doc_endpoints.py --rebaseline  # 首次生成/重建基线（需主作者授权）
  python scripts/ci/check_doc_endpoints.py --prune       # 把已合规端点从基线里删掉

依赖：需在 tianjun conda 环境运行（会导入全部路由模块，含 cv2 依赖链）。
退出码：0 = 通过；1 = 新欠账或基线膨胀。
"""

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).with_name("doc_endpoint_baseline.json")

sys.path.insert(0, str(REPO))

# 与 backend/main.py 一致：cv2 导入前的环境铁律（AGENTS.md 不变量 #2/#10）
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")
os.environ.setdefault("BACKEND_SKIP_INIT", "1")
# 门禁只覆盖生产路由；从 pytest 环境继承 RUNTIME_MODE=test 会挂上测试专用 shim 路由
os.environ.pop("RUNTIME_MODE", None)


def collect_offenses():
    """挂载全部路由并返回 {端点键: [缺失项...]}。"""
    from fastapi import FastAPI
    from fastapi.routing import APIRoute

    from backend.api.router_manifest import mount_all_routers

    app = FastAPI()
    mount_all_routers(app)

    offenses: dict[str, list[str]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = ",".join(sorted(m for m in route.methods if m != "HEAD"))
        key = f"{methods} {route.path}"
        missing = []
        if not (route.summary and route.summary.strip()):
            missing.append("summary")
        # response_class 默认是 Default(JSONResponse) 包装（DefaultPlaceholder），
        # 端点显式声明 response_class 时才是裸 class —— 用类名判断避免版本差异
        has_body_promise = (
            route.response_model is not None
            or route.status_code == 204
            or type(route.response_class).__name__ != "DefaultPlaceholder"
        )
        if not has_body_promise:
            missing.append("response_model")
        doc = (route.endpoint.__doc__ or "").strip()
        if not doc:
            missing.append("docstring")
        if missing:
            offenses[key] = missing
    return offenses


def main() -> int:
    offenses = collect_offenses()

    if "--rebaseline" in sys.argv:
        BASELINE.write_text(
            json.dumps(offenses, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )
        print(f"[check_doc_endpoints] 基线已写入 {BASELINE.name}：{len(offenses)} 个存量欠账端点。")
        return 0

    baseline: dict[str, list[str]] = {}
    if BASELINE.exists():
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    new_offenses = {k: v for k, v in offenses.items() if k not in baseline}
    # 基线内端点新增缺失项也算涨账
    grown = {
        k: sorted(set(v) - set(baseline[k]))
        for k, v in offenses.items()
        if k in baseline and set(v) - set(baseline[k])
    }
    cleared = [k for k in baseline if k not in offenses]

    if "--prune" in sys.argv and cleared:
        for k in cleared:
            baseline.pop(k)
        BASELINE.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )
        print(f"[check_doc_endpoints] 已从基线清除 {len(cleared)} 个已合规端点。")

    ok = True
    if new_offenses:
        ok = False
        print(f"[check_doc_endpoints] ❌ {len(new_offenses)} 个基线外端点缺文档（新端点必须四件套齐全）：")
        for k, v in sorted(new_offenses.items()):
            print(f"  {k}: 缺 {'/'.join(v)}")
    if grown:
        ok = False
        print(f"[check_doc_endpoints] ❌ {len(grown)} 个基线内端点欠账变多（只许还不许借）：")
        for k, v in sorted(grown.items()):
            print(f"  {k}: 新缺 {'/'.join(v)}")
    if ok:
        n_total = len(offenses)
        print(
            f"[check_doc_endpoints] ✅ 无新欠账。存量基线 {len(baseline)} 项，"
            f"当前实际欠账 {n_total} 项"
            + (f"（{len(cleared)} 项已还清，可运行 --prune 收账）。" if cleared else "。")
        )
        return 0
    print("修法见 docs/dev/conventions/端点文档军规.md。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
