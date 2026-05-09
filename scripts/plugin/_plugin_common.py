"""
插件工具集共享代码 — 集中实现关键算法

设计原则：
- 不依赖天骏后端代码（CLI 工具要能独立跑）
- 不依赖网络
- 关键算法实现一份, 工具脚本和后端 PluginManager 都引用本文件
- 单元测试入口: tests/plugin_system/test_plugin_common.py

参考: design/01 §四 (files_digest), design/02 §3 (RSA + HMAC), design/07 §四 (打包流程)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# ============================================================
# 常量（与 design/02 §四 一致）
# ============================================================

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "plugin-system" / "plugin.schema.json"
CUSTOMER_CODES_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "plugin-system" / "customer-codes.md"
)

CUSTOMER_CODE_REGEX = re.compile(r"^[a-z][a-z0-9-]{2,19}$")
PLUGIN_VERSION_REGEX = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.-]+)?$")
FILES_DIGEST_REGEX = re.compile(r"^sha256:[0-9a-f]{64}$")

DIGEST_EXCLUDE_NAMES = {
    "__pycache__",
    ".git",
    ".gitignore",
    ".DS_Store",
    "node_modules",
    ".vscode",
    ".idea",
    ".pytest_cache",
    ".mypy_cache",
    "Thumbs.db",
    ".tjvplugin",  # 自身
    "signature.bin",  # 签名时再加
}
DIGEST_EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".swp"}

# ============================================================
# 异常
# ============================================================

class PluginToolError(Exception):
    """工具脚本通用异常基类。"""

class ManifestValidationError(PluginToolError): pass
class FilesDigestError(PluginToolError): pass
class CryptoError(PluginToolError): pass

# ============================================================
# manifest 相关
# ============================================================

def load_schema() -> dict:
    """加载 plugin.schema.json。"""
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"plugin.schema.json 不存在: {SCHEMA_PATH}")
    with SCHEMA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def canonical_json(obj: dict) -> bytes:
    """生成符合 design/01 §四 的"规范化 JSON"字节序列。

    规则:
    - UTF-8
    - sort_keys=True
    - separators=(",", ":") 无空格
    - ensure_ascii=False (允许中文)
    - 末尾不带换行（与签名输入对齐）
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def validate_manifest(manifest: dict, *, schema: dict | None = None) -> None:
    """JSON Schema 校验, 失败抛 ManifestValidationError。

    需要 jsonschema 库；缺失时退化为最低字段 + customer_code 正则校验。
    """
    schema = schema or load_schema()
    try:
        from jsonschema import Draft202012Validator  # type: ignore

        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(manifest), key=lambda e: e.path)
        if errors:
            msgs = []
            for err in errors[:5]:
                path = "/".join(str(p) for p in err.absolute_path) or "<root>"
                msgs.append(f"  - {path}: {err.message}")
            raise ManifestValidationError(
                "manifest 校验失败 ({} 处错误, 显示前 5):\n{}".format(
                    len(errors), "\n".join(msgs)
                )
            )
    except ImportError:
        # 退化模式: 没装 jsonschema 也能跑基本检查
        for k in schema["required"]:
            if k not in manifest:
                raise ManifestValidationError(f"manifest 缺少必填字段: {k}")
        cc = manifest["customer_code"]
        if not CUSTOMER_CODE_REGEX.match(cc):
            raise ManifestValidationError(f"customer_code 不符合正则: {cc}")
        if not PLUGIN_VERSION_REGEX.match(manifest["plugin_version"]):
            raise ManifestValidationError(
                f"plugin_version 不符合 SemVer: {manifest['plugin_version']}"
            )


def check_customer_registered(customer_code: str) -> bool:
    """检查 customer_code 是否在 customer-codes.md 注册表里。

    实现说明: 简单做法 grep 反引号包围的 cc 字面是否出现; 不区分保留码 vs 真实客户码。
    详见 customer-codes.md §5。
    """
    if not CUSTOMER_CODES_PATH.exists():
        return False
    text = CUSTOMER_CODES_PATH.read_text(encoding="utf-8")
    return f"`{customer_code}`" in text

# ============================================================
# files_digest — 内容指纹
# ============================================================

def _iter_files_for_digest(root: Path) -> Iterable[Path]:
    """遍历 root 下所有要参与 digest 的文件 (排除 __pycache__ 等)。"""
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(root).parts
        if any(part in DIGEST_EXCLUDE_NAMES for part in rel_parts):
            continue
        if p.suffix in DIGEST_EXCLUDE_SUFFIXES:
            continue
        yield p


def calc_files_digest(plugin_dir: Path) -> str:
    """计算插件目录的 files_digest, 格式 'sha256:<64 hex>'.

    算法 (与 design/01 §四 一致):
    1. 遍历目录, 排除 DIGEST_EXCLUDE_NAMES + DIGEST_EXCLUDE_SUFFIXES + signature.bin
    2. 对每个文件, 用相对路径 (POSIX 分隔符)+ '\\0' + 内容字节 喂给 SHA256
    3. 文件之间用 '\\x1f' (RS) 分隔
    4. 文件按相对路径字典序排序后处理 (跨平台一致性)
    5. 输出 'sha256:' + 小写 hex
    """
    plugin_dir = plugin_dir.resolve()
    if not plugin_dir.is_dir():
        raise FilesDigestError(f"plugin_dir 不是目录: {plugin_dir}")

    files = list(_iter_files_for_digest(plugin_dir))
    if not files:
        raise FilesDigestError(f"plugin_dir 没有可计算的文件: {plugin_dir}")

    h = hashlib.sha256()
    for i, p in enumerate(files):
        rel = p.relative_to(plugin_dir).as_posix()
        if i > 0:
            h.update(b"\x1f")  # RS = 0x1f
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(64 * 1024), b""):
                h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def calc_files_digest_in_zip(zip_path: Path) -> str:
    """同 calc_files_digest, 但读 ZIP 内文件 (不用解压).

    用于 verify / install 阶段不必落地。
    排除规则同 calc_files_digest, 额外排除 'signature.bin' 自身。
    """
    if not zipfile.is_zipfile(zip_path):
        raise FilesDigestError(f"不是 ZIP: {zip_path}")

    h = hashlib.sha256()
    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(zf.namelist())
        kept = []
        for n in names:
            if n.endswith("/"):
                continue
            parts = Path(n).parts
            if any(part in DIGEST_EXCLUDE_NAMES for part in parts):
                continue
            if Path(n).suffix in DIGEST_EXCLUDE_SUFFIXES:
                continue
            kept.append(n)

        for i, n in enumerate(kept):
            if i > 0:
                h.update(b"\x1f")
            h.update(n.encode("utf-8"))
            h.update(b"\x00")
            with zf.open(n) as f:
                for chunk in iter(lambda: f.read(64 * 1024), b""):
                    h.update(chunk)
    return f"sha256:{h.hexdigest()}"

# ============================================================
# 公钥指纹
# ============================================================

def calc_pubkey_fingerprint(pub_pem: bytes) -> bytes:
    """公钥 fingerprint = SHA256(SubjectPublicKeyInfo DER)[:16].

    与 design/02 §3.4 / scripts/plugin/gen-master-keypair.py 一致。
    需要 cryptography。
    """
    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError as e:
        raise CryptoError("缺少 cryptography 库") from e
    pub = serialization.load_pem_public_key(pub_pem)
    der = pub.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).digest()[:16]

# ============================================================
# RSA 签名 / 验证
# ============================================================

def rsa_sign_pss(message: bytes, private_pem: bytes, password: bytes) -> bytes:
    """RSA-PSS-SHA256 签名 (与 design/02 §3.3 一致)。"""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as e:
        raise CryptoError("缺少 cryptography 库") from e

    pri = serialization.load_pem_private_key(private_pem, password=password)
    return pri.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=hashes.SHA256.digest_size,  # 32 byte
        ),
        hashes.SHA256(),
    )


def rsa_verify_pss(message: bytes, signature: bytes, public_pem: bytes) -> bool:
    """RSA-PSS-SHA256 验证, 不抛异常. 失败返回 False。"""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.exceptions import InvalidSignature
    except ImportError as e:
        raise CryptoError("缺少 cryptography 库") from e

    pub = serialization.load_pem_public_key(public_pem)
    try:
        pub.verify(
            signature,
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=hashes.SHA256.digest_size,
            ),
            hashes.SHA256(),
        )
        return True
    except InvalidSignature:
        return False

# ============================================================
# HMAC (customer_code 绑定)
# ============================================================

def calc_customer_hmac(customer_code: str, files_digest: str, plugin_secret: bytes) -> bytes:
    """HMAC-SHA256(plugin_secret, customer_code + '|' + files_digest), 32 byte.

    与 design/02 §3.5 一致。
    """
    if len(plugin_secret) != 32:
        raise CryptoError(f"plugin_secret 必须 32 byte, 当前 {len(plugin_secret)}")
    msg = f"{customer_code}|{files_digest}".encode("utf-8")
    return hmac.new(plugin_secret, msg, hashlib.sha256).digest()


def verify_customer_hmac(
    customer_code: str,
    files_digest: str,
    plugin_secret: bytes,
    expected: bytes,
) -> bool:
    """常量时间比较 HMAC. 用 hmac.compare_digest 防 timing attack。"""
    actual = calc_customer_hmac(customer_code, files_digest, plugin_secret)
    return hmac.compare_digest(actual, expected)

# ============================================================
# signature.bin 二进制布局 (与 design/02 §四 一致)
# ============================================================

SIG_MAGIC = b"TJVP"
SIG_VERSION = 1


@dataclass
class SignatureBlob:
    magic: bytes  # 4 byte 'TJVP'
    version: int  # 1 byte
    public_key_fingerprint: bytes  # 16 byte
    customer_hmac: bytes  # 32 byte
    rsa_sig_len: int  # 2 byte big-endian
    rsa_signature: bytes  # 256 ~ 1024 byte
    sig_metadata_len: int  # 2 byte big-endian
    sig_metadata: bytes  # 0 ~ 4096 byte JSON


def write_signature_blob(blob: SignatureBlob) -> bytes:
    """序列化 signature.bin。"""
    if blob.magic != SIG_MAGIC:
        raise CryptoError(f"magic 错: {blob.magic!r}")
    if blob.version != SIG_VERSION:
        raise CryptoError(f"version 错: {blob.version}")
    if len(blob.public_key_fingerprint) != 16:
        raise CryptoError("fingerprint 必须 16 byte")
    if len(blob.customer_hmac) != 32:
        raise CryptoError("customer_hmac 必须 32 byte")

    out = bytearray()
    out += blob.magic
    out += bytes([blob.version])
    out += blob.public_key_fingerprint
    out += blob.customer_hmac
    out += blob.rsa_sig_len.to_bytes(2, "big")
    out += blob.rsa_signature
    out += blob.sig_metadata_len.to_bytes(2, "big")
    out += blob.sig_metadata
    return bytes(out)


def parse_signature_blob(data: bytes) -> SignatureBlob:
    """反序列化 signature.bin, 失败抛 CryptoError。"""
    if len(data) < 4 + 1 + 16 + 32 + 2:
        raise CryptoError(f"signature.bin 太短: {len(data)} byte")

    pos = 0
    magic = data[pos:pos + 4]; pos += 4
    if magic != SIG_MAGIC:
        raise CryptoError(f"signature.bin magic 错: {magic!r}")

    version = data[pos]; pos += 1
    if version != SIG_VERSION:
        raise CryptoError(f"signature.bin version 不支持: {version}")

    fp = data[pos:pos + 16]; pos += 16
    cust_hmac = data[pos:pos + 32]; pos += 32

    rsa_len = int.from_bytes(data[pos:pos + 2], "big"); pos += 2
    if pos + rsa_len > len(data):
        raise CryptoError("signature.bin RSA 段越界")
    rsa_sig = data[pos:pos + rsa_len]; pos += rsa_len

    if pos + 2 > len(data):
        raise CryptoError("signature.bin metadata 长度段缺失")
    meta_len = int.from_bytes(data[pos:pos + 2], "big"); pos += 2
    if pos + meta_len > len(data):
        raise CryptoError("signature.bin metadata 段越界")
    meta = data[pos:pos + meta_len]; pos += meta_len

    return SignatureBlob(
        magic=magic,
        version=version,
        public_key_fingerprint=fp,
        customer_hmac=cust_hmac,
        rsa_sig_len=rsa_len,
        rsa_signature=rsa_sig,
        sig_metadata_len=meta_len,
        sig_metadata=meta,
    )

# ============================================================
# 简易 logger
# ============================================================

def info(msg: str) -> None:
    print(f"[INFO] {msg}")


def warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def err(msg: str) -> None:
    print(f"[FAIL] {msg}")
