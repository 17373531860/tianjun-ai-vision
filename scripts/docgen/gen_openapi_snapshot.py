#!/usr/bin/env python3
"""导出 OpenAPI 快照到 docs/dev/reference/api/openapi.json（P3 生成管线之二）。

用途（抄 Stripe：API 面变更进版本库、diff 即审计）：
  - 提交后 PR diff 直接可见"这次改动新增/删除/修改了哪些端点与字段"
  - 客户 IT/插件开发者拿这份文件即可离线对接，不必起后端看 /docs

用法：
    python scripts/docgen/gen_openapi_snapshot.py          # 生成/覆盖
    python scripts/docgen/gen_openapi_snapshot.py --check  # CI 校验是否过期
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# 同名 Pydantic 模型的组件名冲突消解依赖 set 迭代序 -> 受哈希随机化影响，
# 快照会每次不同。固定 PYTHONHASHSEED 后重新拉起自身，保证快照可复现。
if os.environ.get("PYTHONHASHSEED") != "0":
    env = dict(os.environ, PYTHONHASHSEED="0")
    # 快照只覆盖生产路由；pytest 环境会带 RUNTIME_MODE=test 挂上测试 shim 路由
    env.pop("RUNTIME_MODE", None)
    sys.exit(subprocess.call([sys.executable, __file__, *sys.argv[1:]], env=env))

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "dev" / "reference" / "api" / "openapi.json"

sys.path.insert(0, str(REPO))
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")
os.environ.setdefault("BACKEND_SKIP_INIT", "1")


def _canonicalize_schema_names(spec: dict) -> dict:
    """消除组件命名的跑批间抖动。

    仓库里存在多个同名 Pydantic 类（如串口版与 TCP 版的连接请求都叫一个名），
    FastAPI 用数字后缀消歧，但后缀分配顺序来自 set 迭代（内存地址序）——每次
    生成都可能互换。这里把同一"基名"的冲突组按定义内容排序后重新编号，
    并同步重写全部 $ref，让快照可复现。
    """
    import re

    schemas = spec.get("components", {}).get("schemas", {})

    def base_of(name: str) -> str:
        # FastAPI 对冲突模型有两种消歧形态：
        #   backend__api__alarm__ConnectRequest（模块前缀）/ ConnectRequest2（数字后缀）
        # 哪个模型拿到裸名是不确定的。统一按"类名"归组。
        cls = name.rsplit("__", 1)[-1]
        return re.match(r"^(.*?)(\d*)$", cls).group(1)

    groups: dict[str, list[str]] = {}
    for name in schemas:
        groups.setdefault(base_of(name), []).append(name)

    rename: dict[str, str] = {}
    for base, names in groups.items():
        if len(names) < 2:
            continue
        by_content = sorted(names, key=lambda n: json.dumps(schemas[n], sort_keys=True))
        for i, old in enumerate(by_content):
            rename[old] = base if i == 0 else f"{base}{i + 1}"

    if not rename:
        return spec

    def walk(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                old = ref.rsplit("/", 1)[1]
                if old in rename:
                    node["$ref"] = f"#/components/schemas/{rename[old]}"
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(spec)
    spec["components"]["schemas"] = {
        rename.get(k, k): v for k, v in schemas.items()
    }
    return spec


def build_spec() -> dict:
    from fastapi import FastAPI

    from backend.api.router_manifest import mount_all_routers

    app = FastAPI(title="TianJun AI Vision API", version="snapshot")
    mount_all_routers(app)
    return _canonicalize_schema_names(app.openapi())


def main() -> int:
    spec = build_spec()
    text = json.dumps(spec, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(f"[gen_openapi_snapshot] ❌ {OUT.relative_to(REPO)} 已过期，请重新生成。")
            return 1
        print("[gen_openapi_snapshot] ✅ 快照与路由一致。")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    n_paths = len(spec.get("paths", {}))
    print(f"[gen_openapi_snapshot] 已生成 {OUT.relative_to(REPO)}（{n_paths} 个路径）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
