"""文档防腐回归（P0/P3，2026-07 开发文档体系）。

六个子进程各跑一个防腐脚本：
  - ci/check_doc_paths        : docs/dev + skills + AGENTS.md 引用的仓库路径必须真实存在
  - ci/check_doc_links        : docs/dev 内相对链接必须可达
  - ci/check_doc_endpoints    : 基线法端点文档门禁（新端点缺 summary/response_model/docstring 即红）
  - docgen/gen_db_schema --check        : 表参考文档与 ORM 一致
  - docgen/gen_openapi_snapshot --check : OpenAPI 快照与路由一致
  - docgen/gen_config_dict --check      : 配置字典与代码一致

修法指引见各脚本输出与 docs/dev/conventions/ 三份军规；
生成物过期时重跑对应 gen_*.py（不带 --check）再提交。
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"


def _run(rel: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / rel), *args],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=300,
    )


@pytest.mark.parametrize(
    "rel,args",
    [
        ("ci/check_doc_paths.py", ()),
        ("ci/check_doc_links.py", ()),
        ("ci/check_doc_endpoints.py", ()),
        ("docgen/gen_db_schema.py", ("--check",)),
        ("docgen/gen_openapi_snapshot.py", ("--check",)),
        ("docgen/gen_config_dict.py", ("--check",)),
    ],
    ids=["paths", "links", "endpoints", "db-schema", "openapi", "config-dict"],
)
def test_doc_ci(rel, args):
    proc = _run(rel, *args)
    assert proc.returncode == 0, f"{rel} 失败:\n{proc.stdout}\n{proc.stderr}"
