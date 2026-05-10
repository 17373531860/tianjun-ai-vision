from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

from pytest_bdd import given, parsers, scenarios, then, when


scenarios("../features/plugin_install_api.feature")

PASSWORD = "test-password-123!"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run(args, *, input_text: str | None = None):
    return subprocess.run(
        [sys.executable, *map(str, args)],
        cwd=_repo_root(),
        text=True,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=os.environ.copy(),
        timeout=30,
    )


def _write_test_keys(tmp_path: Path, ctx: dict) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key_dir = tmp_path / "keys"
    key_dir.mkdir(exist_ok=True)

    pri = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pri_pem = pri.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(PASSWORD.encode("utf-8")),
    )
    pub_pem = pri.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_key = key_dir / "plugin_master_pri.pem"
    public_key = key_dir / "plugin_master_pub.pem"
    secret = key_dir / "PLUGIN_SECRET.txt"
    private_key.write_bytes(pri_pem)
    public_key.write_bytes(pub_pem)
    secret.write_text(base64.b64encode(bytes(range(32))).decode("ascii") + "\n", encoding="utf-8")

    ctx["private_key"] = private_key
    ctx["public_key"] = public_key
    ctx["secret"] = secret
    os.environ["PLUGIN_PUBLIC_KEY_PATH"] = str(public_key)
    os.environ["PLUGIN_SECRET_FILE"] = str(secret)


def _write_plugin(tmp_path: Path, customer_code: str) -> Path:
    plugin_dir = tmp_path / f"plugin-{customer_code}"
    (plugin_dir / "frontend" / "assets").mkdir(parents=True, exist_ok=True)
    (plugin_dir / "frontend" / "theme.css").write_text(":root { --tj-primary: #38bdf8; }\n", encoding="utf-8")
    (plugin_dir / "frontend" / "assets" / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"></svg>\n',
        encoding="utf-8",
    )
    manifest = {
        "manifest_version": 1,
        "name": f"API Plugin {customer_code}",
        "customer_code": customer_code,
        "plugin_version": "1.0.0",
        "description": "API lifecycle plugin",
        "author": "tianjun-ai-master",
        "tier": 1,
        "capabilities": ["theme.css", "theme.logo"],
        "main_version_min": "3.7.0",
        "created_at": "2026-05-10T00:00:00+00:00",
        "signed_at": "1970-01-01T00:00:00+00:00",
        "signed_by": "unsigned-dev-build",
        "files_digest": "sha256:" + "0" * 64,
        "frontend": {"theme": {"css": "frontend/theme.css", "logo": "frontend/assets/logo.svg"}},
    }
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return plugin_dir


def _signed_package(tmp_path: Path, ctx: dict, customer_code: str) -> Path:
    plugin_dir = _write_plugin(tmp_path, customer_code)
    unsigned_dir = tmp_path / f"unsigned-{customer_code}"
    signed_dir = tmp_path / f"signed-{customer_code}"
    pack = _run([
        "scripts/plugin/pack-plugin.py",
        "--src",
        plugin_dir,
        "--out",
        unsigned_dir,
        "--skip-build",
    ])
    assert pack.returncode == 0, pack.stdout + pack.stderr
    unsigned = next(unsigned_dir.glob("*.tjvplugin"))
    sign = _run([
        "scripts/plugin/sign-plugin.py",
        "--in",
        unsigned,
        "--out",
        signed_dir,
        "--key",
        ctx["private_key"],
        "--secret",
        ctx["secret"],
        "--signed-by",
        "bdd-api",
    ], input_text=PASSWORD + "\n")
    assert sign.returncode == 0, sign.stdout + sign.stderr
    return next(signed_dir.glob(f"*{customer_code}.tjvplugin"))


def _install(client, package: Path):
    with package.open("rb") as f:
        return client.post(
            "/api/v1/plugins/install",
            files={"file": (package.name, f, "application/zip")},
        )


@given("后端插件测试密钥已配置")
def configure_plugin_keys(tmp_path, ctx, client):
    _write_test_keys(tmp_path, ctx)
    client.put("/api/v1/system/license-cache", json={"customer": ""})
    current = client.get("/api/v1/plugins")
    if current.status_code == 200:
        for item in current.json().get("items", []):
            client.delete(f"/api/v1/plugins/{item['customer_code']}")


@given(parsers.parse('当前 License 客户码为 "{customer_code}"'))
def set_license_customer(client, customer_code):
    res = client.put("/api/v1/system/license-cache", json={"customer": customer_code})
    assert res.status_code == 200


@given(parsers.parse('我准备了客户码为 "{customer_code}" 的已签名插件包'))
def prepare_signed_package(tmp_path, ctx, customer_code):
    ctx["package"] = _signed_package(tmp_path, ctx, customer_code)


@given("我准备了一个非法插件包")
def prepare_bad_package(tmp_path, ctx):
    bad = tmp_path / "bad.tjvplugin"
    bad.write_text("not a zip", encoding="utf-8")
    ctx["package"] = bad


@given(parsers.parse('我已通过 API 安装客户码为 "{customer_code}" 的插件'))
def installed_plugin(tmp_path, ctx, client, customer_code):
    ctx["package"] = _signed_package(tmp_path, ctx, customer_code)
    res = _install(client, ctx["package"])
    assert res.status_code == 200, res.text


@when("我通过 API 上传安装该插件包")
def upload_package(ctx, client):
    ctx["response"] = _install(client, ctx["package"])


@when(parsers.parse('我激活客户码为 "{customer_code}" 的插件'))
@given(parsers.parse('我激活客户码为 "{customer_code}" 的插件'))
def activate_plugin_api(ctx, client, customer_code):
    ctx["response"] = client.post(f"/api/v1/plugins/{customer_code}/activate")


@when(parsers.parse('我停用客户码为 "{customer_code}" 的插件'))
def deactivate_plugin_api(ctx, client, customer_code):
    ctx["response"] = client.post(f"/api/v1/plugins/{customer_code}/deactivate")


@when(parsers.parse('我卸载客户码为 "{customer_code}" 的插件'))
def delete_plugin_api(ctx, client, customer_code):
    ctx["response"] = client.delete(f"/api/v1/plugins/{customer_code}")


@then("插件安装 API 应返回成功")
def install_success(ctx):
    assert ctx["response"].status_code == 200, ctx["response"].text


@then(parsers.parse('插件安装 API 应返回错误码 "{code}"'))
def install_error_code(ctx, code):
    assert ctx["response"].status_code >= 400
    assert ctx["response"].json()["detail"]["code"] == code


@then(parsers.parse('插件列表应包含客户码 "{customer_code}"'))
def list_contains(client, customer_code):
    res = client.get("/api/v1/plugins")
    assert res.status_code == 200
    assert any(item["customer_code"] == customer_code for item in res.json()["items"])


@then(parsers.parse('插件列表不应包含客户码 "{customer_code}"'))
def list_not_contains(client, customer_code):
    res = client.get("/api/v1/plugins")
    assert res.status_code == 200
    assert all(item["customer_code"] != customer_code for item in res.json()["items"])


@then(parsers.parse('插件列表的 active 客户码应为 "{customer_code}"'))
def active_customer(client, customer_code):
    res = client.get("/api/v1/plugins")
    assert res.status_code == 200
    assert res.json()["active_customer_code"] == customer_code


@then("插件列表不应有 active 客户码")
def no_active_customer(client):
    res = client.get("/api/v1/plugins")
    assert res.status_code == 200
    assert res.json()["active_customer_code"] is None
