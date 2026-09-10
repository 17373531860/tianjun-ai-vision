# ==================== .tjvsite 文件格式与加解密 ====================
# 二进制布局 (v1):
#   [0:8)   magic  b"TJVSITE1"
#   [8]     schema version (当前 1)
#   [9]     flags  bit0 = 设置了用户密码
#   [10:26) salt   16 字节随机
#   [26:38) base nonce 12 字节随机 (每块 nonce = 前 8 字节 + 4 字节块计数器)
#   之后为若干块: [4 字节大端密文长度][AES-256-GCM 密文]
#
# 密钥推导:
#   无密码: HKDF-SHA256(产品密钥, salt) —— 防"改后缀当 zip 打开"式窥探/篡改
#   有密码: 先 PBKDF2(密码) 得 32 字节, 再与产品密钥拼接过 HKDF —— 防文件外流
#
# 产品密钥只在后端二进制里 (CI Nuitka 编译), 前端/Electron 不接触明文包。

import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC = b"TJVSITE1"
SCHEMA_VERSION = 1
FLAG_PASSWORD = 0x01
HEADER_LEN = 8 + 1 + 1 + 16 + 12
# 分块加密: 大包 (带几百 MB 模型) 不整包进内存
CHUNK_SIZE = 8 * 1024 * 1024
PBKDF2_ITERATIONS = 200_000

# 产品内置密钥 (v1 固定; 换钥需升 schema version 并保留旧版解密路径)
_PRODUCT_KEY = bytes.fromhex(
    "7a3f9c1e5b8d2046e1f7a9c30b6d84f2196c5e7d0a4b83f6c2e91d75a8043bce"
)


class SitePackError(Exception):
    """包格式/解密错误, 消息可直接展示给用户。"""


class SitePackPasswordRequired(SitePackError):
    """包设置了密码但未提供。"""


class SitePackBadPassword(SitePackError):
    """密码错误或文件被篡改/损坏 (GCM 校验失败, 二者无法区分)。"""


def _derive_key(salt: bytes, password: str = None) -> bytes:
    material = _PRODUCT_KEY
    if password:
        pw_key = PBKDF2HMAC(
            algorithm=hashes.SHA256(), length=32, salt=salt,
            iterations=PBKDF2_ITERATIONS,
        ).derive(password.encode("utf-8"))
        material = _PRODUCT_KEY + pw_key
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=salt,
        info=b"tjvsite-payload-v1",
    ).derive(material)


def _chunk_nonce(base_nonce: bytes, counter: int) -> bytes:
    return base_nonce[:8] + struct.pack(">I", counter)


def encrypt_file(src_path: str, dst_path: str, password: str = None) -> None:
    """把明文 payload (内层 zip) 加密写成 .tjvsite。"""
    salt = os.urandom(16)
    base_nonce = os.urandom(12)
    key = _derive_key(salt, password)
    aead = AESGCM(key)
    flags = FLAG_PASSWORD if password else 0

    with open(src_path, "rb") as src, open(dst_path, "wb") as dst:
        dst.write(MAGIC)
        dst.write(bytes([SCHEMA_VERSION, flags]))
        dst.write(salt)
        dst.write(base_nonce)
        counter = 0
        while True:
            chunk = src.read(CHUNK_SIZE)
            if not chunk:
                break
            ct = aead.encrypt(_chunk_nonce(base_nonce, counter), chunk, MAGIC)
            dst.write(struct.pack(">I", len(ct)))
            dst.write(ct)
            counter += 1


def probe_header(path: str) -> dict:
    """只读文件头, 不解密。用于导入 UI 先判断要不要弹密码框。"""
    with open(path, "rb") as f:
        header = f.read(HEADER_LEN)
    if len(header) < HEADER_LEN or header[:8] != MAGIC:
        raise SitePackError("不是有效的现场配方包 (.tjvsite) 文件")
    version = header[8]
    if version > SCHEMA_VERSION:
        raise SitePackError(
            f"配方包格式版本 {version} 高于本软件支持的 {SCHEMA_VERSION}, 请升级软件后再导入")
    return {"schema": version,
            "needs_password": bool(header[9] & FLAG_PASSWORD)}


def decrypt_file(src_path: str, dst_path: str, password: str = None) -> dict:
    """解密 .tjvsite 到明文 payload 文件, 返回 probe 信息。"""
    info = probe_header(src_path)
    if info["needs_password"] and not password:
        raise SitePackPasswordRequired("该配方包设置了密码, 请输入密码后再导入")

    with open(src_path, "rb") as src:
        header = src.read(HEADER_LEN)
        salt = header[10:26]
        base_nonce = header[26:38]
        key = _derive_key(salt, password if info["needs_password"] else None)
        aead = AESGCM(key)

        with open(dst_path, "wb") as dst:
            counter = 0
            while True:
                len_raw = src.read(4)
                if not len_raw:
                    break
                if len(len_raw) != 4:
                    raise SitePackError("配方包不完整 (文件被截断)")
                (ct_len,) = struct.unpack(">I", len_raw)
                ct = src.read(ct_len)
                if len(ct) != ct_len:
                    raise SitePackError("配方包不完整 (文件被截断)")
                try:
                    dst.write(aead.decrypt(
                        _chunk_nonce(base_nonce, counter), ct, MAGIC))
                except InvalidTag:
                    if info["needs_password"]:
                        raise SitePackBadPassword("密码错误, 或文件已损坏/被篡改")
                    raise SitePackBadPassword("文件已损坏或被篡改, 无法导入")
                counter += 1
    return info
