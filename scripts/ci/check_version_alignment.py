#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发版前版本对齐自检：四处版本号一致性（v3.31 第四期新增）。

检查项:
  1. electron/package.json 的 version（tag 构建校验依赖它, 是"机器真相"）
  2. AGENTS.md 第一节「当前线上版本」声明
  3. docs/changelog/ 下最大版本号的 changelog 文件
  4. git tag 最新 v* 标签（发版 tag 尚未打时只告警不报错）

用法（update-release skill 第 1 步 version bump 后、打 tag 前跑）:
    python scripts/ci/check_version_alignment.py            # 检查一致性
    python scripts/ci/check_version_alignment.py --expect v3.32.0   # 顺带核对目标版本

前三处不一致 → 退出码 1（阻断发版）；tag 落后 → 仅告警（tag 在发版最后一步才打）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def parse_ver(s: str) -> tuple:
    """'v3.31.0' / '3.31.0' -> (3, 31, 0)，解析失败返回空元组。"""
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", s.strip())
    return tuple(int(x) for x in m.groups()) if m else ()


def ver_package_json() -> str:
    with open(os.path.join(ROOT, "electron", "package.json"), encoding="utf-8") as f:
        return json.load(f)["version"]


def ver_agents_md() -> str:
    with open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8") as f:
        for line in f:
            if "当前线上版本" in line:
                m = re.search(r"v(\d+\.\d+\.\d+)", line)
                if m:
                    return m.group(1)
    return ""


def ver_changelog() -> str:
    d = os.path.join(ROOT, "docs", "changelog")
    vers = []
    for name in os.listdir(d):
        m = re.match(r"v(\d+\.\d+\.\d+)_", name)
        if m:
            vers.append(m.group(1))
    return max(set(vers), key=parse_ver) if vers else ""


def ver_git_tag() -> str:
    try:
        out = subprocess.run(["git", "tag", "--list", "v*"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
        tags = [t for t in out.split() if parse_ver(t)]
        return max(tags, key=parse_ver).lstrip("v") if tags else ""
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", help="期望版本号, 如 v3.32.0（可选）")
    args = ap.parse_args()

    pkg = ver_package_json()
    agents = ver_agents_md()
    chlog = ver_changelog()
    tag = ver_git_tag()

    print(f"electron/package.json : {pkg or '<未找到>'}")
    print(f"AGENTS.md 当前线上版本 : {agents or '<未找到>'}")
    print(f"docs/changelog 最新    : {chlog or '<未找到>'}")
    print(f"git tag 最新 v*        : {tag or '<未找到>'}")

    errors = []
    # ── 硬校验：package.json / AGENTS / changelog 三处必须一致 ──
    trio = {"package.json": pkg, "AGENTS.md": agents, "changelog": chlog}
    missing = [k for k, v in trio.items() if not v]
    if missing:
        errors.append(f"版本号缺失: {', '.join(missing)}")
    elif len({pkg, agents, chlog}) != 1:
        errors.append(f"三处版本不一致: package.json={pkg} AGENTS={agents} changelog={chlog}")

    if args.expect:
        exp = args.expect.lstrip("v")
        for k, v in trio.items():
            if v and v != exp:
                errors.append(f"{k}={v} != 期望 {exp}")

    # ── 软校验：tag 允许落后（发版最后才打），领先则是异常 ──
    if tag and pkg and parse_ver(tag) > parse_ver(pkg):
        errors.append(f"git tag v{tag} 领先 package.json {pkg}（tag 打错或忘了 bump）")
    elif tag and pkg and parse_ver(tag) < parse_ver(pkg):
        print(f"⚠️  tag v{tag} 落后 {pkg}（若正在发版属正常, 打 tag 前的状态）")

    if errors:
        print("\n❌ 版本对齐检查失败:")
        for e in errors:
            print(f"   - {e}")
        return 1
    print("\n✅ 版本对齐检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
