# 07 — 打包工具链 + 客户分发流程

> 适用版本：基于 `feat/plugin-config` v0.1
> 本文目的：把"插件作者本地打包 → 主作者签名 → 客户工控机生效"全链路拆到**每条命令行可复制**的程度。
>
> 阅读前置：design 01（manifest）/ design 02（签名）/ design 03（DB） / design 06（加载器）。
>
> 配套：design 08（示例插件 + 客户文档）。

---

## 一、工具链总览

### 1.1 三组角色 / 三个工具

```
┌────────────────────────────────────────────────────────────────┐
│ 角色 1: 插件开发者 (在本地写代码)                              │
│  - 工具: pack-plugin.py                                        │
│  - 输出: plugins/{cc}/-uns.tjvplugin (未签名, 仅本地测试)       │
├────────────────────────────────────────────────────────────────┤
│ 角色 2: 主作者 (持私钥)                                        │
│  - 工具: sign-plugin.py                                        │
│  - 输入: -uns.tjvplugin                                        │
│  - 输出: {cc}-{version}.tjvplugin                              │
├────────────────────────────────────────────────────────────────┤
│ 角色 3: 客户工厂运维 (拿到 .tjvplugin)                         │
│  - 工具: 主程序 Settings 页上传 (或 CLI: install-plugin.py)    │
│  - 工具: verify-plugin.py (验签独立 CLI, 排错用)                │
└────────────────────────────────────────────────────────────────┘
```

### 1.2 工具清单（全在 `scripts/`）

| 脚本 | 角色 | 用途 |
|---|---|---|
| `scripts/pack-plugin.py` | 开发者 | 拉源码 → vite build → 计算 digest → 打 ZIP（**不签名**） |
| `scripts/sign-plugin.py` | 主作者 | 拿 ZIP → RSA 签 + HMAC + 写 signature.bin → 输出最终 ZIP |
| `scripts/verify-plugin.py` | 任何人 | 独立验签（不依赖主程序） |
| `scripts/install-plugin.py` | 运维 | CLI 替代 Settings 页（自动化场景） |
| `scripts/inject_public_key.py` | 主作者 | 应急轮换公钥（design 02 §九） |
| `scripts/dev-plugin.py` | 开发者 | 本地"开发模式"：跳过签名校验加载未签名插件 |

> 所有脚本基于 Python 3.10+，依赖 `cryptography` / `jsonschema` / `packaging`，不引入新 wheels。

### 1.3 .tjvplugin 文件格式（design 00 Q3=A）

```
acme-1.0.0.tjvplugin (本质是 ZIP)
├── plugin.json                 ← canonical (sort_keys, 无 BOM)
├── signature.bin               ← TJVP magic + 字节布局 (design 02 §6)
├── README.md                   ← 客户/运维文档
├── frontend/                   ← (可选, tier 1+ 有)
│   ├── theme.css
│   ├── i18n/zh-CN.json
│   ├── assets/...
│   └── dist/                   ← (tier 2+ 有, vite build 产物)
│       ├── entry.js
│       └── entry.css
├── backend/                    ← (tier 3 有)
│   ├── __init__.py             ← 导出 register_plugin
│   ├── routes.py
│   ├── adapters/...
│   ├── hooks.py
│   └── models.py
├── templates/                  ← (可选, 自定义导出模板)
│   └── acme-defect-report.docx
└── wheels/                     ← (可选, tier 3 自带 Python 依赖)
    └── paho_mqtt-1.6.1-py3-none-any.whl
```

**约束**：
- 文件名格式：`{customer_code}-{plugin_version}.tjvplugin`
- 总大小 ≤ 200 MB（默认）/ ≤ 500 MB（声明 `runtime.allow_large=true`）
- ZIP 用 `STORE`（不压缩）或 `DEFLATE` 都允许；客户端解压都能识别
- ZIP 注释字段不使用（避免某些格式工具篡改）

### 1.4 整体时序

```
开发者本地:
   src/ (源码)
   ↓ pack-plugin.py
   _build/{cc}/   (临时目录: 已 vite build, 未签)
   ↓
   {cc}-{ver}-uns.tjvplugin   (开发自测)

主作者机器:
   {cc}-{ver}-uns.tjvplugin
   ↓ sign-plugin.py --key plugin_master.pem
   {cc}-{ver}.tjvplugin       (最终交付)

GitHub Release / 私有渠道:
   {cc}-{ver}.tjvplugin

客户工控机:
   下载 .tjvplugin → Settings 上传 → POST /api/v1/plugins/install
   ↓
   后端解压到 plugins/{cc}/ → 写 plugins 表 (state=installed)
   ↓ Settings 页点 "激活"
   POST /api/v1/plugins/{id}/activate → state=active
   ↓ 提示重启
   重启 Electron → main.py PluginManager.startup → 加载
```

---

## 二、pack-plugin.py（开发者用）

### 2.1 用法

```bash
$ python scripts/pack-plugin.py plugins/acme/
[1/6] 校验 manifest...
[2/6] 校验源码命名空间...
[3/6] 构建前端 (vite build)...  → frontend/dist/entry.js (180 KB)
[4/6] 计算 files_digest...
[5/6] 写回 plugin.json (canonical)...
[6/6] 打 ZIP → dist/acme-1.0.0-uns.tjvplugin (256 KB)

✓ 已生成 (未签名). 交给主作者签名后才能在生产环境使用.
```

```bash
# 详细参数
$ python scripts/pack-plugin.py plugins/acme/ \
    --output dist/ \
    --skip-build \           # 跳过 vite build (已经 build 过)
    --strict                 # 严格模式: CSS 命名空间检查 / wheel 校验
```

### 2.2 完整实现

```python
# scripts/pack-plugin.py
"""插件打包工具（开发者用）

用法:
    python pack-plugin.py <plugin_dir> [--output OUTPUT] [--skip-build] [--strict]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import hashlib

# 共享代码 (与 sign-plugin.py 公用)
from _plugin_common import (
    canonical_json_dumps,
    calc_files_digest,
    JSONSCHEMA,
    validate_manifest,
    BuildError,
)


def main():
    parser = argparse.ArgumentParser(description="Pack TianJun Vision Plugin")
    parser.add_argument("plugin_dir", type=Path, help="插件源码目录")
    parser.add_argument("--output", type=Path, default=Path("dist"),
                        help="输出目录 (默认 dist/)")
    parser.add_argument("--skip-build", action="store_true",
                        help="跳过前端 vite build")
    parser.add_argument("--strict", action="store_true",
                        help="严格检查 CSS 命名空间 / wheel 完整性")
    args = parser.parse_args()

    plugin_dir = args.plugin_dir.resolve()
    if not plugin_dir.is_dir():
        die(f"目录不存在: {plugin_dir}")

    print(f"[*] 处理插件目录: {plugin_dir}")

    # 1. 校验 manifest
    print("[1/6] 校验 manifest...")
    manifest_path = plugin_dir / "plugin.json"
    if not manifest_path.exists():
        die("plugin.json 不存在")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_manifest(manifest)  # JSON Schema + 业务校验
    except BuildError as e:
        die(f"manifest 校验失败: {e}")

    cc = manifest["customer_code"]
    plugin_dir_name = plugin_dir.name
    if plugin_dir_name != cc:
        die(f"目录名 {plugin_dir_name} 必须与 customer_code {cc} 一致")

    # 2. 校验源码命名空间
    print("[2/6] 校验源码命名空间...")
    check_namespace(plugin_dir, manifest, strict=args.strict)

    # 3. 构建前端
    if not args.skip_build and (plugin_dir / "frontend").exists():
        print("[3/6] 构建前端 (vite build)...")
        build_frontend(plugin_dir / "frontend")
    else:
        print("[3/6] 跳过前端构建")

    # 4. 校验 wheels (tier 3 + requires.python_packages)
    if args.strict:
        check_wheels(plugin_dir, manifest)

    # 5. 计算 files_digest
    print("[4/6] 计算 files_digest...")
    digest = calc_files_digest(plugin_dir)
    print(f"      → {digest}")

    # 6. 写回 plugin.json (canonical, 含新 digest)
    print("[5/6] 写回 plugin.json (canonical)...")
    manifest["files_digest"] = digest
    manifest["created_at"] = manifest.get("created_at") or now_iso()
    # 注意: signed_at 暂留, 让 sign-plugin.py 填
    if "signed_at" not in manifest:
        manifest["signed_at"] = now_iso()  # placeholder
    if "signed_by" not in manifest:
        manifest["signed_by"] = "unsigned-dev-build"  # placeholder
    canonical = canonical_json_dumps(manifest)
    manifest_path.write_bytes(canonical)

    # 7. 打 ZIP
    print("[6/6] 打 ZIP...")
    args.output.mkdir(parents=True, exist_ok=True)
    out_name = f"{cc}-{manifest['plugin_version']}-uns.tjvplugin"
    out_path = args.output / out_name
    write_zip(plugin_dir, out_path)
    size_kb = out_path.stat().st_size // 1024
    print(f"\n✓ 已生成 (未签名): {out_path} ({size_kb} KB)")
    print(f"  交给主作者用 sign-plugin.py 签名后才能在生产环境使用")


def check_namespace(plugin_dir: Path, manifest: dict, strict: bool):
    cc = manifest["customer_code"]

    # 5.1 backend.tables 表名/类名前缀
    for t in (manifest.get("backend") or {}).get("tables", []):
        if not t["name"].startswith(f"p_{cc}_"):
            raise BuildError(f"表名 {t['name']} 必须以 p_{cc}_ 开头")
        if not t["class_name"].startswith("Plugin"):
            raise BuildError(f"类名 {t['class_name']} 必须以 Plugin 开头")

    # 5.2 backend.adapters name
    for a in (manifest.get("backend") or {}).get("adapters", []):
        if not a["name"].startswith(f"plugin-{cc}-"):
            raise BuildError(f"adapter name {a['name']} 必须以 plugin-{cc}- 开头")

    # 5.3 default_config keys
    for k in (manifest.get("default_config") or {}):
        if not k.startswith(f"plugin.{cc}."):
            raise BuildError(f"default_config key {k} 必须以 plugin.{cc}. 开头")

    # 5.4 frontend.stores id
    for s in ((manifest.get("frontend") or {}).get("stores") or []):
        if not s["id"].startswith(f"plugin-{cc}-"):
            raise BuildError(f"store id {s['id']} 必须以 plugin-{cc}- 开头")

    # 5.5 隐藏菜单不能动 reserved
    RESERVED = {"/monitor", "/settings", "/activation"}
    for p in ((manifest.get("frontend") or {}).get("hidden_menus") or []):
        if p in RESERVED:
            raise BuildError(f"hidden_menus 不能包含 {p} (reserved)")

    # 5.6 CSS 命名空间检查
    css_path = manifest.get("frontend", {}).get("theme", {}).get("css")
    if css_path:
        css_full = plugin_dir / css_path
        if css_full.exists():
            check_css_namespace(css_full, cc, strict)

    # 5.7 export.field_resolvers
    for f in ((manifest.get("export") or {}).get("field_resolvers") or []):
        if not f["field"].startswith(f"plugin.{cc}."):
            raise BuildError(f"field {f['field']} 必须以 plugin.{cc}. 开头")


def check_css_namespace(css_path: Path, cc: str, strict: bool):
    css = css_path.read_text(encoding="utf-8")
    DANGER = [
        (r"\*\s*\{", "全局 * 选择器"),
        (r"\bbody\s*\{", "全局 body 选择器 (用 :root)"),
        (r"\[class[\^*~|$]?='?\"?el-", "全局 [class*='el-'] 覆盖"),
    ]
    for pat, msg in DANGER:
        if re.search(pat, css):
            raise BuildError(f"CSS 危险选择器: {msg}")

    if strict:
        # 检查自家 class 是否符合命名规范
        for m in re.finditer(r"\.([a-zA-Z][a-zA-Z0-9_-]*)", css):
            cls = m.group(1)
            if cls.startswith(("el-", "tj-")):
                continue
            if not cls.startswith(f"plugin-{cc}-"):
                print(f"[WARN] CSS 类 .{cls} 建议加 plugin-{cc}- 前缀")


def check_wheels(plugin_dir: Path, manifest: dict):
    """验证 manifest.requires.python_packages 都有对应 wheel 或已被 main 提供"""
    pkgs = (manifest.get("requires") or {}).get("python_packages") or []
    if not pkgs:
        return
    wheels_dir = plugin_dir / "wheels"
    if not wheels_dir.exists():
        # 尝试 import (假设 main 已有这些库)
        for pkg in pkgs:
            name = pkg["name"]
            try:
                __import__(name.replace("-", "_"))
            except ImportError:
                raise BuildError(
                    f"requires.python_packages 声明 {name} 但既无 wheels/ 也无主程序提供"
                )
    else:
        present = {w.stem.split("-")[0].replace("_", "-").lower()
                   for w in wheels_dir.glob("*.whl")}
        missing = [p["name"] for p in pkgs if p["name"].lower() not in present]
        if missing:
            print(f"[WARN] wheels/ 中未找到 {missing}")


def build_frontend(frontend_dir: Path):
    """跑 vite build --config vite.lib.config.js"""
    config = frontend_dir / "vite.lib.config.js"
    if not config.exists():
        raise BuildError(f"前端缺 vite.lib.config.js")
    if not (frontend_dir / "node_modules").exists():
        print("      → 首次, 运行 npm install...")
        subprocess.run(["npm", "install"], cwd=frontend_dir, check=True)
    subprocess.run(
        ["npx", "vite", "build", "--config", str(config)],
        cwd=frontend_dir,
        check=True,
    )
    entry = frontend_dir / "dist" / "entry.js"
    if not entry.exists():
        raise BuildError("vite build 完成但找不到 dist/entry.js")
    print(f"      → {entry.relative_to(frontend_dir.parent)} ({entry.stat().st_size//1024} KB)")


def write_zip(plugin_dir: Path, out_path: Path):
    """打包到 ZIP, 排除噪声"""
    EXCLUDE_DIRS = {"__pycache__", ".git", ".vscode", "node_modules", "wheels-cache"}
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(plugin_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for f in files:
                if f.endswith((".pyc", ".log", ".tmp")):
                    continue
                full = Path(root) / f
                rel = full.relative_to(plugin_dir).as_posix()
                zf.write(full, arcname=rel)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def die(msg):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except BuildError as e:
        die(str(e))
```

### 2.3 _plugin_common.py（公共工具）

```python
# scripts/_plugin_common.py
"""被 pack / sign / verify 共享的工具函数"""
import hashlib
import json
from pathlib import Path

import jsonschema


class BuildError(Exception):
    pass


def canonical_json_dumps(obj: dict) -> bytes:
    """canonical: sort_keys / no extra whitespace / utf-8 / no BOM
    (与 design 02 §4.2 一致)
    """
    return json.dumps(
        obj,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def calc_files_digest(plugin_dir: Path) -> str:
    """同 design 02 §三完整算法"""
    EXCLUDE_DIRS = {"__pycache__", ".git", ".github", ".vscode", ".idea",
                    "node_modules", ".DS_Store", "wheels-cache"}
    EXCLUDE_FILES = {"plugin.json", "signature.bin", ".gitignore", ".gitkeep"}
    plugin_dir = plugin_dir.resolve()
    h = hashlib.sha256()

    files = []
    import os
    for root, dirs, fnames in os.walk(plugin_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fname in fnames:
            if fname in EXCLUDE_FILES:
                continue
            full = Path(root) / fname
            try:
                full = full.resolve()
            except OSError:
                continue
            rel = full.relative_to(plugin_dir).as_posix()
            files.append((rel, full))
    files.sort(key=lambda x: x[0])

    for rel, full in files:
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        try:
            with open(full, "rb") as fh:
                while True:
                    chunk = fh.read(8192)
                    if not chunk: break
                    h.update(chunk)
        except OSError:
            pass
        h.update(b"\x00")
    return f"sha256:{h.hexdigest()}"


# JSON Schema (从 design 01 §五取一份)
JSONSCHEMA = json.loads(
    (Path(__file__).parent.parent / "backend/core/plugin_schema.json").read_text()
)


def validate_manifest(manifest: dict) -> None:
    """JSON Schema + 业务校验"""
    try:
        jsonschema.validate(manifest, JSONSCHEMA)
    except jsonschema.ValidationError as e:
        raise BuildError(f"JSON Schema 校验失败: {e.message} at {list(e.absolute_path)}")

    # 业务: 三档与 backend/frontend 字段一致
    tier = manifest["tier"]
    if tier == 1 and "backend" in manifest:
        raise BuildError("tier=1 不允许有 backend")
    if tier == 3 and "backend" not in manifest:
        raise BuildError("tier=3 必须有 backend")
```

---

## 三、sign-plugin.py（主作者用）

### 3.1 用法

```bash
$ python scripts/sign-plugin.py dist/acme-1.0.0-uns.tjvplugin \
    --key ~/.tianjun-keys/plugin_master.pem \
    --signer "tianjun-ai-master-2026"
[1/6] 解压未签 ZIP → /tmp/sign-xxx/
[2/6] 重新校验 manifest...
[3/6] 重新计算 files_digest...
[4/6] 询问私钥密码...
Enter PEM pass phrase: ********
[5/6] RSA-PSS-SHA256 签名 (4096 bit)...
[6/6] 计算 customer_hmac (PLUGIN_SECRET via env)...
[7/6] 写 signature.bin (TJVP magic)...
[8/6] 重打 ZIP → dist/acme-1.0.0.tjvplugin

✓ 签名完成: dist/acme-1.0.0.tjvplugin (256 KB)
  customer_code: acme
  plugin_version: 1.0.0
  signed_by: tianjun-ai-master-2026
  signed_at: 2026-05-08T13:30:21Z
  pubkey_fingerprint: 1a2b3c4d5e6f7a8b
  files_digest: sha256:1a2b3c...
```

### 3.2 完整实现

```python
# scripts/sign-plugin.py
"""主作者用: 签名一个未签名插件包"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import os
import struct
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
import shutil

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from _plugin_common import (
    canonical_json_dumps, calc_files_digest, validate_manifest, BuildError,
)


def main():
    parser = argparse.ArgumentParser(description="Sign TianJun Vision Plugin")
    parser.add_argument("input", type=Path, help="未签名 .tjvplugin")
    parser.add_argument("--key", type=Path, required=True, help="RSA 私钥 PEM")
    parser.add_argument("--signer", required=True, help='签名者标识 e.g. "tianjun-ai-master-2026"')
    parser.add_argument("--output", type=Path, default=None,
                        help="输出文件 (默认: 同名去 -uns)")
    parser.add_argument("--password", default=None,
                        help="(测试用) 私钥密码; 生产请用交互式输入")
    args = parser.parse_args()

    plugin_secret = os.environ.get("PLUGIN_SECRET_HEX")
    if not plugin_secret:
        die("环境变量 PLUGIN_SECRET_HEX 未设置")
    plugin_secret = bytes.fromhex(plugin_secret)
    if len(plugin_secret) != 32:
        die("PLUGIN_SECRET 必须是 32 字节 (64 hex 字符)")

    if not args.input.exists():
        die(f"文件不存在: {args.input}")

    print(f"[*] 输入: {args.input}")

    # 1. 解压
    with tempfile.TemporaryDirectory(prefix="sign-") as tmp:
        tmp = Path(tmp)
        print(f"[1/8] 解压到 {tmp}")
        with zipfile.ZipFile(args.input) as zf:
            zf.extractall(tmp)

        # 2. 重新校验 manifest
        print("[2/8] 重新校验 manifest...")
        manifest_path = tmp / "plugin.json"
        if not manifest_path.exists():
            die("缺 plugin.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        try:
            validate_manifest(manifest)
        except BuildError as e:
            die(str(e))

        cc = manifest["customer_code"]
        # 检查 customer_code 已注册 (design 00 Q5)
        check_customer_registered(cc)

        # 3. 重新计算 files_digest (防开发者打包后又改了文件)
        print("[3/8] 重新计算 files_digest...")
        actual_digest = calc_files_digest(tmp)
        print(f"      → {actual_digest}")
        if manifest.get("files_digest") != actual_digest:
            print(f"[WARN] manifest digest 与实际不一致, 已用实际值替代")
            manifest["files_digest"] = actual_digest

        # 4. 私钥密码 + 加载
        print("[4/8] 加载私钥...")
        pem = args.key.read_bytes()
        password = args.password
        if password is None:
            password = getpass.getpass("Enter PEM pass phrase: ")
        try:
            private_key = serialization.load_pem_private_key(
                pem, password=password.encode("utf-8") if password else None
            )
        except ValueError as e:
            die(f"私钥加载失败: {e} (密码错误?)")
        if not isinstance(private_key, rsa.RSAPrivateKey):
            die("私钥不是 RSA")
        if private_key.key_size != 4096:
            die(f"密钥长度必须 4096, 实际 {private_key.key_size}")

        # 5. 更新 signed_at / signed_by, 重写 manifest
        print("[5/8] 更新 signed_at / signed_by...")
        manifest["signed_at"] = now_iso()
        manifest["signed_by"] = args.signer
        manifest["created_at"] = manifest.get("created_at") or manifest["signed_at"]

        canonical = canonical_json_dumps(manifest)
        manifest_path.write_bytes(canonical)

        # 6. RSA 签名
        print("[6/8] RSA-PSS-SHA256 签名 (4096 bit)...")
        signature = private_key.sign(
            canonical,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=32,
            ),
            hashes.SHA256(),
        )
        assert len(signature) == 512

        # 7. customer_hmac
        print("[7/8] 计算 customer_hmac...")
        customer_hmac = hmac.new(
            key=plugin_secret, msg=cc.encode("utf-8"),
            digestmod=hashlib.sha256
        ).digest()

        # 公钥指纹
        pub_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        pub_der = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        pk_fingerprint = hashlib.sha256(pub_der).digest()[:8]

        sig_metadata = {
            "signed_at": manifest["signed_at"],
            "signed_by": manifest["signed_by"],
            "manifest_sha256": hashlib.sha256(canonical).hexdigest(),
        }

        write_signature_blob(
            output_path=tmp / "signature.bin",
            public_key_fingerprint=pk_fingerprint,
            customer_hmac=customer_hmac,
            rsa_signature=signature,
            sig_metadata=sig_metadata,
            gpu_required=(manifest.get("requires") or {}).get("gpu", False),
        )

        # 8. 重打 ZIP
        print("[8/8] 重打 ZIP...")
        out_path = args.output or args.input.parent / f"{cc}-{manifest['plugin_version']}.tjvplugin"
        write_zip_from_dir(tmp, out_path)

    size_kb = out_path.stat().st_size // 1024
    print(f"\n✓ 签名完成: {out_path} ({size_kb} KB)")
    print(f"  customer_code:      {cc}")
    print(f"  plugin_version:     {manifest['plugin_version']}")
    print(f"  signed_by:          {args.signer}")
    print(f"  signed_at:          {manifest['signed_at']}")
    print(f"  pubkey_fingerprint: {pk_fingerprint.hex()}")
    print(f"  files_digest:       {actual_digest}")


def write_signature_blob(output_path, public_key_fingerprint, customer_hmac,
                         rsa_signature, sig_metadata, gpu_required=False):
    """与 design 02 §6.3 完全一致"""
    flags = 0x0001 if gpu_required else 0
    meta_json = json.dumps(
        sig_metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(meta_json) > 460:
        raise ValueError(f"sig_metadata 太大: {len(meta_json)} > 460")

    buf = bytearray()
    buf.extend(b"TJVP")
    buf.append(0x01)  # format_version
    buf.append(0x00)  # reserved
    buf.extend(struct.pack(">H", flags))
    buf.extend(public_key_fingerprint)
    buf.extend(customer_hmac)
    buf.extend(struct.pack(">H", 512))
    buf.extend(rsa_signature)
    buf.extend(meta_json)
    buf.append(0x00)
    while len(buf) % 8:
        buf.append(0x00)
    output_path.write_bytes(bytes(buf))


def check_customer_registered(cc: str):
    """检查 docs/plugin-system/customer-codes.md 是否已注册"""
    reg_file = Path(__file__).parent.parent / "docs/plugin-system/customer-codes.md"
    if not reg_file.exists():
        print(f"[WARN] customer-codes.md 不存在, 跳过注册检查")
        return
    content = reg_file.read_text(encoding="utf-8")
    if f"`{cc}`" not in content and f"| {cc} |" not in content:
        print(f"[WARN] customer_code '{cc}' 未在 customer-codes.md 注册, 请先 PR 注册")
        # 不是 hard fail, 仅警告 (规模 ≤ 50 客户, 偶尔有内部测试码)


def write_zip_from_dir(src_dir: Path, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, _, files in os.walk(src_dir):
            for f in files:
                full = Path(root) / f
                rel = full.relative_to(src_dir).as_posix()
                zf.write(full, arcname=rel)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def die(msg):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
```

---

## 四、verify-plugin.py（独立验签）

不依赖主程序数据库，让任何人能验签——**用于排错 / 客户求助 / CI 校验**。

```python
# scripts/verify-plugin.py
"""独立验签工具

用法:
    python verify-plugin.py acme-1.0.0.tjvplugin
"""
import argparse
import json
import struct
import sys
import tempfile
import zipfile
from pathlib import Path
import hashlib

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.exceptions import InvalidSignature

from _plugin_common import canonical_json_dumps, calc_files_digest


# 内置公钥列表 (与主程序一致, 见 design 02 §8.3)
BUILTIN_PUBLIC_KEYS = {
    "1a2b3c4d5e6f7a8b": b"""-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA...
-----END PUBLIC KEY-----""",
}


def main():
    parser = argparse.ArgumentParser(description="Verify TianJun Vision Plugin")
    parser.add_argument("input", type=Path)
    parser.add_argument("--customer-code", help="期望的 customer_code (可选)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="verify-") as tmp:
        tmp = Path(tmp)
        with zipfile.ZipFile(args.input) as zf:
            zf.extractall(tmp)

        result = verify_plugin_dir(tmp, args.customer_code, args.verbose)
        print_result(result)
        sys.exit(0 if result["ok"] else 1)


def verify_plugin_dir(plugin_dir: Path, expected_cc: str = None, verbose: bool = False):
    result = {"ok": False, "checks": []}

    def add(name, ok, detail=""):
        result["checks"].append({"name": name, "ok": ok, "detail": detail})

    # 1. 文件存在
    manifest_path = plugin_dir / "plugin.json"
    sig_path = plugin_dir / "signature.bin"
    if not manifest_path.exists():
        add("manifest 文件存在", False, "plugin.json 缺失")
        return result
    if not sig_path.exists():
        add("signature 文件存在", False, "signature.bin 缺失")
        return result

    add("manifest 文件存在", True)
    add("signature 文件存在", True)

    # 2. 解析 manifest
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except Exception as e:
        add("manifest 解析", False, str(e))
        return result
    add("manifest 解析", True, f"customer_code={manifest.get('customer_code')}")

    # 3. customer_code 检查
    cc = manifest.get("customer_code")
    if expected_cc and cc != expected_cc:
        add("customer_code 检查", False, f"期望 {expected_cc} 实际 {cc}")
        return result
    add("customer_code", True, cc)

    # 4. 解析 signature.bin
    try:
        sig_data = sig_path.read_bytes()
        sig = parse_signature_blob(sig_data)
    except Exception as e:
        add("signature.bin 格式", False, str(e))
        return result
    add("signature.bin 格式", True, f"format_version={sig['format_version']}")

    # 5. 公钥定位
    pk_pem = BUILTIN_PUBLIC_KEYS.get(sig["public_key_fingerprint"].hex())
    if pk_pem is None:
        add("公钥识别", False, f"未知指纹 {sig['public_key_fingerprint'].hex()}")
        return result
    add("公钥识别", True, sig["public_key_fingerprint"].hex())

    # 6. manifest_sha256 预筛
    actual_sha = hashlib.sha256(manifest_bytes).hexdigest()
    if actual_sha != sig["sig_metadata"].get("manifest_sha256"):
        add("manifest_sha256 预筛", False)
        return result
    add("manifest_sha256 预筛", True)

    # 7. RSA 验签
    try:
        public_key = serialization.load_pem_public_key(pk_pem)
        public_key.verify(
            sig["rsa_signature"],
            manifest_bytes,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=32,
            ),
            hashes.SHA256(),
        )
        add("RSA 验签", True)
    except InvalidSignature:
        add("RSA 验签", False, "签名不匹配")
        return result
    except Exception as e:
        add("RSA 验签", False, str(e))
        return result

    # 8. (可选) HMAC 验证 — 需要 PLUGIN_SECRET, 工具无, 跳过
    add("HMAC 验证", None, "(独立工具无 PLUGIN_SECRET, 跳过)")

    # 9. files_digest
    actual_digest = calc_files_digest(plugin_dir)
    if actual_digest != manifest.get("files_digest"):
        add("files_digest", False, f"期望 {manifest.get('files_digest')[:32]}... 实际 {actual_digest[:32]}...")
        return result
    add("files_digest", True)

    # 10. signed_at / signed_by 一致
    if (manifest.get("signed_at") != sig["sig_metadata"].get("signed_at")
            or manifest.get("signed_by") != sig["sig_metadata"].get("signed_by")):
        add("signed_at/signed_by 一致", False)
        return result
    add("signed_at/signed_by 一致", True)

    result["ok"] = True
    result["customer_code"] = cc
    result["plugin_version"] = manifest.get("plugin_version")
    result["signed_at"] = manifest.get("signed_at")
    result["signed_by"] = manifest.get("signed_by")
    result["files_digest"] = actual_digest
    return result


def parse_signature_blob(data: bytes):
    if data[0:4] != b"TJVP":
        raise ValueError(f"bad magic: {data[0:4]}")
    fmt_ver = data[4]
    if fmt_ver != 1:
        raise ValueError(f"unsupported format version: {fmt_ver}")
    return {
        "format_version": fmt_ver,
        "flags": struct.unpack(">H", data[6:8])[0],
        "public_key_fingerprint": data[8:16],
        "customer_hmac": data[16:48],
        "rsa_signature": data[50:562],
        "sig_metadata": json.loads(
            data[562:data.find(b"\x00", 562)].decode("utf-8")
        ),
    }


def print_result(result):
    print()
    for c in result["checks"]:
        if c["ok"] is True:
            mark = "✓"
        elif c["ok"] is False:
            mark = "✗"
        else:
            mark = "•"
        detail = f" — {c['detail']}" if c.get("detail") else ""
        print(f"  {mark} {c['name']}{detail}")
    print()
    if result["ok"]:
        print("✓ 签名验证通过")
        print(f"  customer_code:  {result['customer_code']}")
        print(f"  plugin_version: {result['plugin_version']}")
        print(f"  signed_by:      {result['signed_by']}")
        print(f"  signed_at:      {result['signed_at']}")
    else:
        print("✗ 签名验证失败")


if __name__ == "__main__":
    main()
```

输出示例：

```
$ python verify-plugin.py dist/acme-1.0.0.tjvplugin

  ✓ manifest 文件存在
  ✓ signature 文件存在
  ✓ manifest 解析 — customer_code=acme
  ✓ customer_code — acme
  ✓ signature.bin 格式 — format_version=1
  ✓ 公钥识别 — 1a2b3c4d5e6f7a8b
  ✓ manifest_sha256 预筛
  ✓ RSA 验签
  • HMAC 验证 — (独立工具无 PLUGIN_SECRET, 跳过)
  ✓ files_digest
  ✓ signed_at/signed_by 一致

✓ 签名验证通过
  customer_code:  acme
  plugin_version: 1.0.0
  signed_by:      tianjun-ai-master-2026
  signed_at:      2026-05-08T13:30:21Z
```

---

## 五、客户安装流程

### 5.1 用户视角

```
1. 主作者发邮件 / 私有渠道 → 客户拿到 acme-1.0.0.tjvplugin
2. 客户打开 Settings 页 → 插件管理
3. 点 "上传插件" → 选 .tjvplugin 文件 → 上传
4. 后端验签 → 解压到 plugins/acme/ → 写 plugins 表 (state=installed)
5. UI 刷新, 显示 "ACME MES 全栈插件 v2.0.0 [已安装, 未激活]"
6. 点 "激活" → 弹出确认对话框 ("此操作将激活插件并需要重启应用")
7. 用户确认 → POST activate → state=active
8. 弹出 "插件已激活, 立即重启?" → 确认 → window.electronAPI.relaunch()
9. 应用重启 → main.py PluginManager.startup → 加载 ACME 插件
10. UI 加载完成 → 看到 ACME 主题 / 报表菜单 / 自家 router 工作
```

### 5.2 上传 API

```python
# backend/api/plugins.py
import zipfile
import shutil
from fastapi import UploadFile, File, HTTPException, Depends
from fastapi.responses import JSONResponse


@router.post("/install", response_model=dict)
def install_plugin(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传 .tjvplugin 安装

    流程:
    1. 大小限制 (200 MB)
    2. 临时保存到 tmp
    3. 调 PluginVerifier.verify (design 02 §七)
    4. 解压到 plugins/{cc}/ (覆盖)
    5. 写 plugins 表 (state=installed)
    6. 返回 {success, customer_code, plugin_version, message}

    错误统一报 HTTP 400 + 错误代码字段
    """
    if not file.filename.endswith(".tjvplugin"):
        raise HTTPException(400, "文件后缀必须为 .tjvplugin")

    # 限制大小: 一次性 read 容易 OOM, 用 stream
    plugins_dir = Path(settings.PLUGINS_DIR)
    plugins_dir.mkdir(parents=True, exist_ok=True)

    # 临时存
    with tempfile.NamedTemporaryFile(suffix=".tjvplugin", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        size = 0
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > 200 * 1024 * 1024:
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(413, "文件超过 200 MB")
            tmp.write(chunk)

    try:
        # 解压到临时目录
        with tempfile.TemporaryDirectory(prefix="install-") as extract_tmp:
            extract_tmp = Path(extract_tmp)
            try:
                with zipfile.ZipFile(tmp_path) as zf:
                    # 安全检查: 防 zip slip
                    for member in zf.namelist():
                        if member.startswith("/") or ".." in member:
                            raise HTTPException(400, "ZIP 包含危险路径")
                    zf.extractall(extract_tmp)
            except zipfile.BadZipFile:
                raise HTTPException(400, "文件不是有效的 ZIP")

            # 找 plugin.json
            manifest_path = extract_tmp / "plugin.json"
            if not manifest_path.exists():
                raise HTTPException(400, "ZIP 缺 plugin.json")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            cc = manifest.get("customer_code")
            if not cc:
                raise HTTPException(400, "manifest 缺 customer_code")

            # ⚠️ 关键: 验签
            license_payload = load_active_license_payload()
            verifier = PluginVerifier(public_keys=load_builtin_public_keys())
            ok, reason = verifier.verify(extract_tmp, license_payload)
            if not ok:
                raise HTTPException(400, f"PLUGIN_SIGNATURE_FAIL ({reason})")

            # 移到正式位置
            target = plugins_dir / cc
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(extract_tmp, target)

            # 写 plugins 表
            existing = db.query(PluginInstall).filter_by(customer_code=cc).first()
            new_state = "installed"
            if existing:
                # 升级路径: 老插件如果是 active, 升级后保持 active 等待重启
                new_state = "active" if existing.state == "active" else "installed"
                existing.plugin_version = manifest["plugin_version"]
                existing.manifest_json = manifest
                existing.manifest_sha256 = compute_manifest_sha(manifest_path)
                existing.signature_status = "ok"
                existing.state = new_state
                existing.error_msg = None
                existing.error_code = None
                existing.install_path = str(target.resolve())
            else:
                db.add(PluginInstall(
                    customer_code=cc,
                    name=manifest.get("name"),
                    plugin_version=manifest["plugin_version"],
                    tier=manifest["tier"],
                    manifest_json=manifest,
                    manifest_sha256=compute_manifest_sha(manifest_path),
                    signature_status="ok",
                    signed_by=manifest.get("signed_by"),
                    signed_at=manifest.get("signed_at"),
                    install_path=str(target.resolve()),
                    state="installed",
                ))
            db.commit()

            # 写 audit
            db.add(PluginAuditLog(
                customer_code=cc,
                plugin_version=manifest["plugin_version"],
                event_type="install",
                event_detail={"size_bytes": size, "state": new_state},
            ))
            db.commit()

            return {
                "success": True,
                "customer_code": cc,
                "plugin_version": manifest["plugin_version"],
                "name": manifest.get("name"),
                "tier": manifest["tier"],
                "state": new_state,
                "message": (
                    "已安装, 待激活" if new_state == "installed"
                    else "已升级, 重启后生效"
                ),
            }
    finally:
        tmp_path.unlink(missing_ok=True)
```

### 5.3 激活 API

```python
@router.post("/{plugin_id}/activate")
def activate_plugin(plugin_id: int, db: Session = Depends(get_db)):
    p = db.query(PluginInstall).get(plugin_id)
    if not p:
        raise HTTPException(404)
    if p.state == "quarantined":
        raise HTTPException(400, "插件已隔离, 请先解除")
    if p.signature_status != "ok":
        raise HTTPException(400, "签名状态异常")

    # 单插件激活: 把所有其他 active 插件置为 disabled
    others = db.query(PluginInstall).filter(
        PluginInstall.id != plugin_id, PluginInstall.state == "active"
    ).all()
    for o in others:
        o.state = "disabled"

    p.state = "active"
    p.activated_at = datetime.utcnow()
    db.add(PluginAuditLog(
        customer_code=p.customer_code,
        event_type="activate",
        event_detail={"deactivated_others": [o.customer_code for o in others]},
    ))
    db.commit()

    return {"success": True, "needs_restart": True,
            "message": "已激活, 需要重启应用"}
```

### 5.4 CLI 替代（运维场景）

```bash
# scripts/install-plugin.py
$ python install-plugin.py acme-1.0.0.tjvplugin --activate --restart
[1/4] 上传 acme-1.0.0.tjvplugin (256 KB)
[2/4] 验签 OK (signed_by=tianjun-ai-master-2026)
[3/4] 已安装到 plugins/acme/, state=installed
[4/4] 激活...
✓ 已激活. 5 秒后重启应用...
```

```python
# scripts/install-plugin.py
"""CLI 安装插件 (替代 Settings 页, 自动化场景用)"""
import argparse
import requests
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--api", default="http://localhost:8001")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()

    print(f"[1/4] 上传 {args.file.name} ({args.file.stat().st_size//1024} KB)")
    with open(args.file, "rb") as f:
        resp = requests.post(
            f"{args.api}/api/v1/plugins/install",
            files={"file": (args.file.name, f, "application/octet-stream")},
            timeout=60,
        )
    if resp.status_code != 200:
        print(f"[ERROR] {resp.status_code}: {resp.text}")
        return 1
    data = resp.json()
    print(f"[2/4] 验签 OK")
    print(f"[3/4] 已安装到 plugins/{data['customer_code']}/, state={data['state']}")

    if args.activate:
        print(f"[4/4] 激活...")
        # 拿 plugin id
        list_resp = requests.get(f"{args.api}/api/v1/plugins/")
        plugin_id = next(
            p["id"] for p in list_resp.json()
            if p["customer_code"] == data["customer_code"]
        )
        act_resp = requests.post(f"{args.api}/api/v1/plugins/{plugin_id}/activate")
        if act_resp.status_code != 200:
            print(f"[ERROR] {act_resp.status_code}: {act_resp.text}")
            return 1
        print(f"✓ 已激活")

    if args.restart:
        # IPC 不可用, 让用户/脚本自行处理
        print("提醒: 请手动重启应用")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
```

---

## 六、CI 集成（GitHub Actions）

### 6.1 插件作者的 CI（.github/workflows/build-plugin.yml）

```yaml
# plugins/acme/.github/workflows/build-plugin.yml
name: Build ACME Plugin

on:
  push:
    branches: [main]
    tags: ['v*']
  pull_request:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with: { python-version: '3.10' }

      - uses: actions/setup-node@v4
        with: { node-version: '20' }

      - name: Install Python deps
        run: pip install jsonschema cryptography packaging

      - name: Pack plugin
        run: |
          python scripts/pack-plugin.py plugins/acme --output dist/ --strict

      - uses: actions/upload-artifact@v4
        with:
          name: acme-unsigned
          path: dist/*-uns.tjvplugin
```

### 6.2 主作者签名 CI（受保护分支 / 仅手动触发）

```yaml
# .github/workflows/sign-plugin.yml
name: Sign Plugin

on:
  workflow_dispatch:
    inputs:
      artifact_url:
        description: 'Unsigned plugin artifact URL'
        required: true

jobs:
  sign:
    runs-on: ubuntu-latest
    environment: production-signing  # 受保护环境, 仅特定 reviewer 可触发
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with: { python-version: '3.10' }

      - run: pip install cryptography jsonschema

      - name: Download unsigned artifact
        run: |
          curl -L "${{ inputs.artifact_url }}" -o input.tjvplugin

      - name: Sign
        env:
          PLUGIN_SECRET_HEX: ${{ secrets.PLUGIN_SECRET_HEX }}
          KEY_PASSWORD: ${{ secrets.PLUGIN_KEY_PASSWORD }}
        run: |
          # secrets.PLUGIN_PRIVATE_KEY 是 base64 PEM
          echo "${{ secrets.PLUGIN_PRIVATE_KEY }}" | base64 -d > /tmp/key.pem
          chmod 600 /tmp/key.pem
          python scripts/sign-plugin.py input.tjvplugin \
              --key /tmp/key.pem \
              --signer "tianjun-ai-master-2026" \
              --password "$KEY_PASSWORD"
          rm -f /tmp/key.pem  # 立即销毁

      - uses: actions/upload-artifact@v4
        with:
          name: acme-signed
          path: '*.tjvplugin'
          if-no-files-found: error
```

> ⚠️ **风险**：私钥进 GitHub Secrets 后，任何 admin 都可以读出。
> **缓解**：
> - 用 GitHub Environment 受保护，需要 reviewer 批准才能跑
> - 仅打 release 时跑，不在 PR 触发
> - 私钥仍是**主备份在主作者本地**——CI 私钥应是次密钥（备用）
> - 主作者本地仍然能签（应急）

### 6.3 验签 CI（每次 release 强制）

```yaml
# .github/workflows/verify-plugin.yml
name: Verify Plugin Release

on:
  release:
    types: [published]

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install cryptography jsonschema
      - name: Download release asset
        run: |
          gh release download ${{ github.event.release.tag_name }} \
              --pattern '*.tjvplugin'
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      - run: |
          for f in *.tjvplugin; do
              python scripts/verify-plugin.py "$f"
          done
```

---

## 七、应急公钥轮换工具（design 02 §九）

```python
# scripts/inject_public_key.py
"""紧急轮换 / 增加公钥到主程序代码

用法:
    python inject_public_key.py new_pubkey.pem \
        --signed-by "tianjun-ai-master-2027" \
        --valid-from 2027-01-01 \
        --valid-to 2031-01-01 \
        --revoke-old      # 可选, 把旧公钥的 valid_to 改为今天
"""
import argparse
import ast
import hashlib
import re
from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives import serialization


PUBLIC_KEYS_FILE = Path(__file__).parent.parent / "backend/core/plugin_public_keys.py"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pubkey_file", type=Path)
    parser.add_argument("--signed-by", required=True)
    parser.add_argument("--valid-from", required=True)
    parser.add_argument("--valid-to", required=True)
    parser.add_argument("--revoke-old", action="store_true")
    parser.add_argument("--description", default="")
    args = parser.parse_args()

    pem = args.pubkey_file.read_bytes()
    pk = serialization.load_pem_public_key(pem)
    der = pk.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    fp_hex = hashlib.sha256(der).hexdigest()[:16]

    # 修改 plugin_public_keys.py
    src = PUBLIC_KEYS_FILE.read_text()
    new_entry = f'''    "{fp_hex}": {{
        "pem": b"""{pem.decode("ascii")}""",
        "signed_by": "{args.signed_by}",
        "valid_from": "{args.valid_from}",
        "valid_to": "{args.valid_to}",
        "description": "{args.description}",
    }},
'''
    # 注入
    src = src.replace(
        "PLUGIN_PUBLIC_KEYS: dict[str, dict] = {",
        f"PLUGIN_PUBLIC_KEYS: dict[str, dict] = {{\n{new_entry}",
        1,
    )

    # revoke 旧的: 把旧条目的 valid_to 改为今天
    if args.revoke_old:
        today = date.today().isoformat()
        src = re.sub(
            r'"valid_to":\s*"\d{4}-\d{2}-\d{2}"',
            lambda m: f'"valid_to": "{today}"' if m.group() != f'"valid_to": "{args.valid_to}"' else m.group(),
            src,
        )

    PUBLIC_KEYS_FILE.write_text(src)
    print(f"✓ 已注入公钥指纹 {fp_hex}")
    print(f"  请 git add + commit + 触发 hotfix 打包")
```

---

## 八、开发模式（unsigned 加载）

开发者本地调试时不可能每次都签——提供 **dev mode**：

```python
# scripts/dev-plugin.py
"""开发模式: 跳过签名校验加载本地插件 (仅 DEBUG_MODE=1 主程序生效)

用法:
    DEBUG_MODE=1 python scripts/dev-plugin.py plugins/acme/
"""
```

实现：

```python
# backend/core/plugin_manager.py 内
def _do_load(self, db, plugin_install):
    if os.environ.get("DEBUG_MODE") == "1" and os.environ.get("PLUGIN_DEV_MODE") == "1":
        logger.warning(f"[Plugin] DEV MODE: 跳过 {plugin_install.customer_code} 验签")
        # 忽略 signature_status, 直接加载
        ...
    else:
        if plugin_install.signature_status != "ok":
            self._mark_failed(...)
            return
        ...
```

> ⚠️ Nuitka 生产 build 时**强制移除** DEBUG_MODE 分支（nuitka --remove-debug-symbols / 编译期断言），防止客户工控机误开。

---

## 九、版本升级流程

### 9.1 老插件 → 新插件（同 customer_code）

```
v1.0.0 已激活
  ↓ 客户拿到 v1.1.0 (.tjvplugin)
  ↓ Settings 上传
后端处理 (§5.2 install_plugin):
  - 验签 OK
  - 检测到 customer_code 已存在 (PluginInstall)
  - state 保持: 老的是 active → 新的也是 active (待重启)
  - 替换 plugins/{cc}/ 目录内容
  - 更新 plugin_version / manifest_json / manifest_sha256
  - 写 audit_log (event=install, plugin_version=1.1.0)
  ↓ 提示重启
  ↓ 重启
PluginManager.startup:
  - 扫到新版本的 plugin.json
  - 重新跑加载流程
  - default_config 应用算法 (design 03 §6.2):
    - 仅写入"当前 SystemConfig 中没有"的 key
    - 客户已修改的值保留
```

### 9.2 降级（罕见）

降级允许，但**主程序会警告**：

```
[Plugin] acme 版本回退: 2.0.0 → 1.5.0 (是否确认?)
```

降级时：
- 保留 plugins 表行（不删）
- DB 表的列**不会自动 DROP**（design 03 §5.3 由插件自己处理 migrate）
- 旧版本如果用不到新加列，**仍能正常工作**（SQLAlchemy 的额外列是 nullable）

### 9.3 跨主程序版本兼容

```
主程序 v3.7 + 插件依赖 main_version_min=3.6.0   → 兼容 ✓
主程序 v3.8 + 插件依赖 main_version_max=3.7.x   → 警告 (允许但不保证)
主程序 v4.0 + 插件依赖 manifest_version=1, max=3.x → 拒绝
```

---

## 十、目录结构（最终一览）

### 10.1 主程序仓库

```
tianjun-plugin/
├── backend/
│   ├── core/
│   │   ├── plugin_manager.py            ← design 06 §3
│   │   ├── plugin_registry.py           ← design 06 §4.3
│   │   ├── plugin_host.py               ← design 06 §五
│   │   ├── plugin_verifier.py           ← design 02 §七
│   │   ├── plugin_migrate.py            ← design 03 §四
│   │   ├── plugin_public_keys.py        ← design 02 §8.3
│   │   ├── plugin_secret.py             ← gitignore!
│   │   └── plugin_schema.json           ← design 01 §五
│   ├── models/plugin_models.py          ← design 03 §十
│   ├── api/plugins.py                   ← §5.2 install / activate / list 等
│   └── ...
├── frontend/
│   ├── src/plugin/
│   │   ├── themeLoader.js               ← design 04 §3.1.4
│   │   ├── uiLoader.js                  ← design 05 §4.5
│   │   ├── registry.js                  ← design 05 §3.2
│   │   ├── host.js                      ← design 05 §3.3
│   │   └── wrapView.js                  ← design 05 §5.6
│   ├── vite.vendor.config.js            ← design 05 §4.4.1
│   └── ...
├── scripts/
│   ├── _plugin_common.py                ← §2.3
│   ├── pack-plugin.py                   ← §二
│   ├── sign-plugin.py                   ← §三
│   ├── verify-plugin.py                 ← §四
│   ├── install-plugin.py                ← §5.4
│   ├── inject_public_key.py             ← §七
│   └── dev-plugin.py                    ← §八
├── docs/plugin-system/
│   ├── customer-codes.md                ← design 00 Q5: 客户码注册表
│   ├── inventory/                       ← 已写
│   └── design/                          ← 本系列文档
└── plugins/                             ← 客户插件落地目录 (.gitignore)
    └── default/                         ← 内置示例 (开发期)
```

### 10.2 客户工控机

```
%APPDATA%\TianjunVision\
├── data/
│   ├── sql_app.db
│   ├── plugins/                         ← PluginManager 扫描这里
│   │   └── acme/                        ← 上传的插件解压
│   └── ...
└── License.dat
```

---

## 十一、安全清单

| 风险 | 防御 |
|---|---|
| ZIP slip（路径逃逸） | 检查 `..` / 绝对路径 |
| ZIP bomb（解压炸弹） | 大小限制 200 MB + 解压时检查每个文件大小 |
| 私钥进 git | gitignore + pre-commit hook 检查 |
| PLUGIN_SECRET 进 git | 同上 |
| 未签名插件被误装 | install API 强制验签，未通过直接 400 |
| 签了但 customer_code 与 license 不一致 | install API + 加载时双重校验 |
| 装一个插件导致 active 多个 | activate API 自动 disable 其他 |
| 版本递减绕过修复 | 插件 plugin_version 比对（警告，不阻塞） |
| 路径 traversal 通过 assets 端点读 secret | design 04 §4.2 7 重防御 |
| 插件包内含恶意 wheels | 由签名链信任主作者审计 |

---

## 十二、本文决策摘要

| 决策点 | 值 |
|---|---|
| .tjvplugin 格式 | ZIP（design 00 Q3=A） |
| 文件名 | `{customer_code}-{plugin_version}.tjvplugin` |
| 大小上限 | 200 MB（默认）/ 500 MB（声明 allow_large） |
| 工具数 | 6 个（pack / sign / verify / install / inject_key / dev） |
| pack-plugin.py 作用 | 校验 + vite build + digest + 打 ZIP（**不签**） |
| sign-plugin.py 私钥来源 | `--key` 参数 + 交互式密码（生产）/ `--password`（CI） |
| sign-plugin.py PLUGIN_SECRET 来源 | 环境变量 PLUGIN_SECRET_HEX |
| verify-plugin.py 范围 | 独立验签，HMAC 步骤跳过（无 secret） |
| 客户安装 | Settings 上传（GUI）或 install-plugin.py（CLI） |
| 安装 API 验签 | 强制（失败 → 400） |
| 升级保留 active 状态 | 是（重启后生效新版本） |
| 降级允许 | 是（警告） |
| dev mode | 仅 DEBUG_MODE=1 + PLUGIN_DEV_MODE=1，Nuitka build 移除 |
| CI 私钥保管 | GitHub Environment 受保护（备用），主备份在主作者本地 |
| 公钥轮换工具 | inject_public_key.py + git commit + hotfix |

---

**本文最后更新**：2026-05-08
**事实校验**：基于 design 02（签名）/ 03（DB）/ 06（加载器）/ 04+05（前端）的接口约束
**下一文档**：design/08_examples.md（示例插件 + 用户文档）
