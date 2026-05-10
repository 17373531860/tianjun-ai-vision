"""插件签名生命周期 BDD step 实现。

这些场景刻意通过 subprocess 调用 scripts/plugin/*.py，模拟主作者/工厂 IT 的真实 CLI 使用方式。
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when


scenarios("../features/plugin_signing.feature")
scenarios("../features/plugin_cli.feature")


PASSWORD = "test-password-123!"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run(args, *, input_text: str | None = None, env: dict | None = None):
    return subprocess.run(
        [sys.executable, *map(str, args)],
        cwd=_repo_root(),
        text=True,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, **(env or {})},
        timeout=30,
    )


def _write_test_keys(tmp_path: Path, ctx: dict) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key_dir = tmp_path / "keys"
    key_dir.mkdir()

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


def _write_wrong_public_key(tmp_path: Path) -> Path:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pri = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = pri.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    path = tmp_path / "wrong_pub.pem"
    path.write_bytes(pub_pem)
    return path


def _write_plugin(tmp_path: Path, *, missing_customer_code: bool = False) -> Path:
    plugin_dir = tmp_path / ("bad-plugin" if missing_customer_code else "good-plugin")
    (plugin_dir / "frontend" / "assets").mkdir(parents=True)
    (plugin_dir / "frontend" / "theme.css").write_text(":root { --tj-primary: #38bdf8; }\n", encoding="utf-8")
    (plugin_dir / "frontend" / "assets" / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"></svg>\n',
        encoding="utf-8",
    )

    manifest = {
        "manifest_version": 1,
        "name": "BDD Plugin",
        "customer_code": "internal-test",
        "plugin_version": "1.0.0",
        "description": "BDD lifecycle plugin",
        "author": "tianjun-ai-master",
        "tier": 1,
        "capabilities": ["theme.css", "theme.logo"],
        "main_version_min": "3.7.0",
        "created_at": "2026-05-10T00:00:00+00:00",
        "signed_at": "1970-01-01T00:00:00+00:00",
        "signed_by": "unsigned-dev-build",
        "files_digest": "sha256:" + "0" * 64,
        "frontend": {
            "theme": {
                "css": "frontend/theme.css",
                "logo": "frontend/assets/logo.svg",
            }
        },
    }
    if missing_customer_code:
        del manifest["customer_code"]

    (plugin_dir / "plugin.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    return plugin_dir


def _pack(ctx: dict) -> subprocess.CompletedProcess:
    out_dir = ctx["tmp_path"] / "packed"
    out_dir.mkdir(exist_ok=True)
    result = _run([
        "scripts/plugin/pack-plugin.py",
        "--src",
        ctx["plugin_dir"],
        "--out",
        out_dir,
        "--skip-build",
    ])
    ctx["pack_result"] = result
    packages = sorted(out_dir.glob("*.tjvplugin"))
    if packages:
        ctx["unsigned_package"] = packages[-1]
    return result


def _sign(ctx: dict) -> subprocess.CompletedProcess:
    out_dir = ctx["tmp_path"] / "signed"
    out_dir.mkdir(exist_ok=True)
    result = _run([
        "scripts/plugin/sign-plugin.py",
        "--in",
        ctx["unsigned_package"],
        "--out",
        out_dir,
        "--key",
        ctx["private_key"],
        "--secret",
        ctx["secret"],
        "--signed-by",
        "bdd-test-master",
    ], input_text=PASSWORD + "\n")
    ctx["sign_result"] = result
    packages = sorted(out_dir.glob("*.tjvplugin"))
    if packages:
        ctx["signed_package"] = packages[-1]
    return result


def _verify(ctx: dict, pub_key: Path | None = None) -> subprocess.CompletedProcess:
    result = _run([
        "scripts/plugin/verify-plugin.py",
        "--in",
        ctx["signed_package"],
        "--pub",
        pub_key or ctx["public_key"],
    ])
    ctx["verify_result"] = result
    return result


def _install(ctx: dict, *, secret: Path | None = None, data_dir: Path | None = None, dry_run: bool = False):
    args = [
        "scripts/plugin/install-plugin.py",
        "--in",
        ctx["signed_package"],
        "--pub",
        ctx["public_key"],
        "--secret",
        secret or ctx["secret"],
        "--data-dir",
        data_dir or ctx["install_dir"],
        "--activate",
    ]
    if dry_run:
        args.append("--dry-run")
    result = _run(args)
    ctx["install_result"] = result
    return result


def _build_signed_package(tmp_path: Path, ctx: dict) -> None:
    ctx["tmp_path"] = tmp_path
    ctx["plugin_dir"] = _write_plugin(tmp_path)
    _write_test_keys(tmp_path, ctx)
    ctx["install_dir"] = tmp_path / "install-data"
    pack_result = _pack(ctx)
    assert pack_result.returncode == 0, pack_result.stderr + pack_result.stdout
    sign_result = _sign(ctx)
    assert sign_result.returncode == 0, sign_result.stderr + sign_result.stdout


def _rewrite_zip(src: Path, dst: Path, replacements: dict[str, bytes]) -> None:
    with zipfile.ZipFile(src) as zf_in, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf_out:
        for info in zf_in.infolist():
            data = replacements.get(info.filename)
            if data is None:
                data = zf_in.read(info.filename)
            zf_out.writestr(info, data)


@given("我有一个最小 Tier1 插件目录")
def given_minimal_plugin(tmp_path, ctx):
    ctx["tmp_path"] = tmp_path
    ctx["plugin_dir"] = _write_plugin(tmp_path)
    ctx["install_dir"] = tmp_path / "install-data"


@given("我准备好测试签名密钥和客户密钥")
def given_test_keys(tmp_path, ctx):
    _write_test_keys(tmp_path, ctx)


@given("我有一个已经签名的插件包")
def given_signed_package(tmp_path, ctx):
    _build_signed_package(tmp_path, ctx)


@given("我有一个缺少客户码的插件目录")
def given_bad_manifest_plugin(tmp_path, ctx):
    ctx["tmp_path"] = tmp_path
    ctx["plugin_dir"] = _write_plugin(tmp_path, missing_customer_code=True)


@when("我用 CLI 完成打包、签名、校验、安装")
def when_full_cli_lifecycle(ctx):
    assert _pack(ctx).returncode == 0, ctx["pack_result"].stderr + ctx["pack_result"].stdout
    assert _sign(ctx).returncode == 0, ctx["sign_result"].stderr + ctx["sign_result"].stdout
    assert _verify(ctx).returncode == 0, ctx["verify_result"].stderr + ctx["verify_result"].stdout
    _install(ctx)


@when("我篡改插件包内的业务文件")
def when_tamper_file(ctx):
    tampered = ctx["tmp_path"] / "tampered-file.tjvplugin"
    _rewrite_zip(
        ctx["signed_package"],
        tampered,
        {"frontend/theme.css": b":root { --tj-primary: #000; }\n"},
    )
    ctx["signed_package"] = tampered


@when("我替换插件包内的签名文件")
def when_replace_signature(ctx):
    tampered = ctx["tmp_path"] / "tampered-signature.tjvplugin"
    _rewrite_zip(ctx["signed_package"], tampered, {"signature.bin": b"not-a-valid-signature"})
    ctx["signed_package"] = tampered


@when("我运行插件校验")
def when_verify(ctx):
    _verify(ctx)


@when("我使用另一把公钥运行插件校验")
def when_verify_wrong_pubkey(tmp_path, ctx):
    _verify(ctx, _write_wrong_public_key(tmp_path))


@when("我运行插件打包")
def when_pack(ctx):
    _pack(ctx)


@when("我用错误客户密钥安装插件")
def when_install_wrong_secret(tmp_path, ctx):
    wrong_secret = tmp_path / "wrong_SECRET.txt"
    wrong_secret.write_text(base64.b64encode(bytes(reversed(range(32)))).decode("ascii") + "\n", encoding="utf-8")
    _install(ctx, secret=wrong_secret)


@then("插件安装应成功")
def then_install_success(ctx):
    result = ctx["install_result"]
    assert result.returncode == 0, result.stderr + result.stdout


@then("插件目录应落盘")
def then_installed_dir_exists(ctx):
    assert (ctx["install_dir"] / "plugins" / "internal-test" / "plugin.json").is_file()


@then("插件校验应失败")
def then_verify_failed(ctx):
    result = ctx["verify_result"]
    assert result.returncode != 0, result.stdout + result.stderr


@then("插件打包应失败")
def then_pack_failed(ctx):
    result = ctx["pack_result"]
    assert result.returncode != 0, result.stdout + result.stderr
    assert "customer_code" in (result.stdout + result.stderr)


@then("插件安装应失败")
def then_install_failed(ctx):
    result = ctx["install_result"]
    assert result.returncode != 0, result.stdout + result.stderr
    assert "HMAC" in (result.stdout + result.stderr)


# ============================================================
# CLI 用户体验 feature
# ============================================================

@when("我不带参数运行所有插件 CLI")
def when_run_all_cli_without_args(ctx):
    scripts = [
        "pack-plugin.py",
        "sign-plugin.py",
        "verify-plugin.py",
        "install-plugin.py",
        "inject-public-key.py",
        "dev-plugin.py",
    ]
    ctx["cli_results"] = {
        script: _run([f"scripts/plugin/{script}"])
        for script in scripts
    }


@then("每个 CLI 都应显示 usage 用法")
def then_all_cli_show_usage(ctx):
    for script, result in ctx["cli_results"].items():
        text = result.stdout + result.stderr
        assert result.returncode != 0, script
        assert "usage:" in text.lower(), f"{script} 未显示 usage: {text[:300]}"


@when("我使用不存在的公钥路径运行插件校验")
def when_verify_missing_pubkey(ctx):
    result = _run([
        "scripts/plugin/verify-plugin.py",
        "--in",
        ctx["signed_package"],
        "--pub",
        ctx["tmp_path"] / "missing_pub.pem",
    ])
    ctx["cli_result"] = result


@then("CLI 应提示公钥不存在")
def then_missing_pubkey_message(ctx):
    result = ctx["cli_result"]
    text = result.stdout + result.stderr
    assert result.returncode != 0
    assert "公钥不存在" in text


@when("我安装插件到只读目录")
def when_install_readonly_dir(ctx):
    readonly = ctx["tmp_path"] / "readonly-data"
    readonly.mkdir()
    readonly.chmod(0o500)
    try:
        result = _install(ctx, data_dir=readonly)
    finally:
        readonly.chmod(0o700)
    ctx["cli_result"] = result


@then("CLI 应提示安装落盘失败")
def then_install_readonly_message(ctx):
    result = ctx["cli_result"]
    text = result.stdout + result.stderr
    assert result.returncode != 0
    assert "安装落盘失败" in text


@when("我以 dry-run 模式安装插件")
def when_install_dry_run(ctx):
    _install(ctx, dry_run=True)


@then("CLI 应提示 dry-run 成功")
def then_dry_run_success(ctx):
    result = ctx["install_result"]
    text = result.stdout + result.stderr
    assert result.returncode == 0, text
    assert "dry-run" in text


@then("插件目录不应落盘")
def then_plugin_dir_not_written(ctx):
    assert not (ctx["install_dir"] / "plugins" / "internal-test").exists()
