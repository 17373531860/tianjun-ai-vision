# 02 — 签名机制：RSA + 客户码 HMAC

> 适用版本：基于 `feat/plugin-config` v0.1
> 本文目的：把"签名 + 客户码绑定"落到**字节级 + 可代码实现级别**——签名工具能照写 / 加载器能照抄 / 攻击者能列出能绕过和不能绕过的边界。
>
> 阅读前置：design 00 第八节（Q1=C / Q2=A 已敲定）、design 01 第三节（manifest 字段 + files_digest）。
>
> 配套：`design/03_database.md`（公钥落库）、`design/07_distribution.md`（签名工具）。

---

## 一、设计目标 + 攻击模型

### 1.1 三个核心目标

1. **真实性**：插件确实是天骏 AI 团队签的（不是别人伪造）
2. **完整性**：插件文件没被改（哪怕 1 字节）
3. **客户绑定**：客户 A 的插件不能拿给客户 B 用（即使客户 B 也是合法用户）

### 1.2 攻击模型（明确"防什么 / 不防什么"）

| 攻击场景 | 是否防 | 防御手段 |
|---|---|---|
| 攻击者直接改 `backend/__init__.py` 内容 | ✅ 防 | files_digest |
| 攻击者改 `plugin.json` 内容（含 customer_code） | ✅ 防 | RSA 签名 manifest |
| 攻击者拿到 ACME 插件想给 XYZ 用 | ✅ 防 | HMAC(customer_code, plugin_secret) |
| 攻击者改 license.dat 让 customerName=acme | ⚠️ 部分防 | license 已有 RSA 签名（design 02.7）|
| 攻击者反编译 Nuitka 拿到公钥 | ✅ 设计上无意义（公钥本就公开） | n/a |
| 攻击者反编译 Nuitka 拿到 plugin_secret（HMAC 密钥） | ❌ 不防 | 见 §1.3 |
| 攻击者拿主作者私钥 | ❌ 不防（私钥泄露=系统失守） | 仅靠物理保管，详见 §五 |
| 攻击者换主程序版本号绕过 | ❌ 不防（无意义，主程序也得签） | n/a |
| 客户自己写一个插件想加载 | ✅ 防（无私钥不能签） | RSA 签名 |
| 客户研究 manifest schema 自学打包 | ✅ 防 | 没私钥仍签不出 |

### 1.3 关于 plugin_secret（HMAC 密钥）

> 这是**全套设计中最微妙的一点**。

`plugin_secret` 是一个 HMAC 密钥，**主程序运行时需要它来验签**，所以它必须**藏在主程序里**。但它一旦泄露，攻击者就能伪造客户码绑定。

**结论：**
- HMAC **不是**抵御"高级攻击者反编译主程序"的手段
- HMAC 是抵御"中级攻击者拿到一份 ACME 插件想换 customer_code 的目录名 + manifest 后用"
- **真正的防伪靠 RSA 签名**（私钥永远不进主程序）
- **HMAC 是第二道锁**：攻击者必须同时
  - 反编译 Nuitka（拿 plugin_secret）
  - + 拿到主作者私钥（伪造 RSA 签名）
  - 才能完全绕过

**只要私钥不泄露，整套体系不被攻破。**

### 1.4 不在本文范围

- 加密插件代码（仅签名，不加密。Nuitka 会编译 backend，但 frontend 是明文）→ 见 §九（明文是已知妥协）
- 在线吊销列表（CRL）→ 当前不做（客户工控机离线）
- 时间戳服务（TSA）→ 当前不做

---

## 二、整体方案：RSA + 客户码 HMAC（Scheme C）

### 2.1 三层防护

```
┌──────────────────────────────────────────────────────────────┐
│  Layer 1: files_digest                                       │
│           SHA256(所有文件路径+内容)                          │
│           写到 plugin.json                                   │
│           作用: 防文件被改                                   │
├──────────────────────────────────────────────────────────────┤
│  Layer 2: RSA 数字签名                                       │
│           RSA-PSS-SHA256(plugin.json bytes)                  │
│           用主作者**私钥**签                                 │
│           主程序内置**公钥**验                               │
│           作用: 防伪造来源 + 防 plugin.json 被改             │
├──────────────────────────────────────────────────────────────┤
│  Layer 3: 客户码 HMAC                                        │
│           HMAC-SHA256(customer_code, plugin_secret)          │
│           plugin_secret 在主程序里                           │
│           作用: 防"换客户码 + 改目录名"复用插件              │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 验签短路逻辑

```
verify(plugin_dir):
    1. 读 plugin.json bytes (manifest_bytes)
    2. 读 signature.bin (sig_blob)
    3. 解析 sig_blob → (rsa_sig, customer_hmac, public_key_fingerprint, sig_version)
    4. 用 public_key_fingerprint 在公钥列表中找对应公钥
    5. RSA-PSS-SHA256.verify(public_key, manifest_bytes, rsa_sig)  ← 失败即 fail
    6. 解析 manifest → 拿 customer_code
    7. expected_hmac = HMAC-SHA256(customer_code, plugin_secret)
    8. constant_time_compare(customer_hmac, expected_hmac)        ← 失败即 fail
    9. files_digest 校验（design 01 §3.3）
    10. 全部通过 → OK
```

任意一步 fail 都拒绝加载，统一报 `PLUGIN_SIGNATURE_FAIL`（不暴露具体哪步失败，避免给攻击者反馈）。

---

## 三、files_digest 完整算法

### 3.1 算法（伪代码）

```python
import hashlib
import os
from pathlib import Path

EXCLUDE_DIRS = frozenset([
    "__pycache__", ".git", ".github", ".vscode", ".idea",
    "node_modules", ".DS_Store", "wheels-cache",
])

EXCLUDE_FILES = frozenset([
    "plugin.json", "signature.bin",  # ← manifest 自己 + 签名文件不参与 digest
    ".gitignore", ".gitkeep",
])


def calc_files_digest(plugin_dir: Path) -> str:
    """计算插件目录文件摘要

    返回值: "sha256:<64-hex>"

    跨平台一致性保证:
    - 路径用 / 分隔（Windows 的 \\ 转换）
    - 路径排序用字典序
    - 文件以二进制读取
    - 不依赖 mtime / atime / ctime / 文件权限
    """
    plugin_dir = plugin_dir.resolve()
    h = hashlib.sha256()

    files: list[tuple[str, Path]] = []
    for root, dirs, filenames in os.walk(plugin_dir):
        # 1. 排除噪声目录（in-place 修改 dirs 让 os.walk 不进入）
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

        for fname in filenames:
            if fname in EXCLUDE_FILES:
                continue
            full = Path(root) / fname
            # 2. 解符号链接（一次）
            try:
                full = full.resolve()
            except OSError:
                continue
            # 3. 计算相对路径，统一 / 分隔符
            rel = full.relative_to(plugin_dir).as_posix()
            files.append((rel, full))

    # 4. 路径字典序排序
    files.sort(key=lambda x: x[0])

    # 5. 顺序累加 hash
    for rel, full in files:
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")  # 路径分隔符
        try:
            with open(full, "rb") as fh:
                while True:
                    chunk = fh.read(8192)
                    if not chunk:
                        break
                    h.update(chunk)
        except OSError:
            # 读不到的文件视为空文件（跨平台一致性）
            pass
        h.update(b"\x00")  # 文件分隔符

    return f"sha256:{h.hexdigest()}"
```

### 3.2 corner case

| 情况 | 处理 |
|---|---|
| 空文件 | `路径 + \0 + \0` 仍计入 |
| 二进制文件 | 直接读字节，不解码 |
| 文件权限 0o600 | **不影响**digest（不读取权限位） |
| 文件 mtime 不同 | **不影响**digest |
| 文件路径含中文 | UTF-8 编码后参与 hash |
| 符号链接 | 解一次，计算被指向的真实文件 |
| 循环符号链接 | `Path.resolve()` 抛 OSError，跳过 |
| 隐藏文件（`.xxx`） | **包含**（除非在 EXCLUDE_FILES 内） |
| 大文件 | 分块读取，8192 byte/chunk |
| `.bytecode` / `.pyc` 文件 | **包含**（虽然 `__pycache__` 被排除，但散落的 `.pyc` 仍计入） |
| `__init__.py` 与 `__init__.pyi` | 都计入 |
| 一个文件出现两次（不同符号链接） | 因为 `resolve()` 后路径会一致，**只算一次** |

### 3.3 跨平台测试

签名工具在 Windows + Linux 必须算出一样的 digest：

```bash
# Linux
$ python -c "from sign_tool import calc_files_digest; print(calc_files_digest('plugins/acme'))"
sha256:1a2b3c...

# Windows (Git Bash)
> python -c "from sign_tool import calc_files_digest; print(calc_files_digest('plugins/acme'))"
sha256:1a2b3c...   # ← 必须一致
```

**不一致来源（必须修掉的）**：
- 行尾符号 `\r\n` vs `\n` → 我们**不归一化**，**警告客户用 .gitattributes 锁 LF**
- BOM → 我们不剥 BOM（让 `bytes` 完整传入）
- 文件系统大小写敏感差异（macOS 默认大小写不敏感）→ 不在我们支持范围（生产 Linux + Windows）

### 3.4 .gitattributes 推荐配置

打包时签名工具会**警告**客户在插件根目录放：

```gitattributes
# plugins/{customer_code}/.gitattributes
* text=auto eol=lf
*.docx binary
*.xlsx binary
*.pdf  binary
*.png  binary
*.jpg  binary
*.svg  text eol=lf
*.j2   text eol=lf
*.json text eol=lf
*.css  text eol=lf
*.js   text eol=lf
*.py   text eol=lf
```

---

## 四、RSA 数字签名

### 4.1 算法选型

| 项目 | 选型 | 理由 |
|---|---|---|
| **算法** | RSA-PSS-SHA256 | PSS 比 PKCS#1 v1.5 更安全；SHA256 与 license 算法一致 |
| **密钥长度** | 4096 bit | 4096 是当前商业项目常用强度，2048 太弱（2030 年前会被淘汰） |
| **库** | `cryptography` 3.x+（Python） | License 已用 `cryptography`，复用 |
| **盐长度** | 32 byte | 与 SHA256 输出长度一致（PSS 推荐） |
| **公钥指纹** | SHA256(DER public key)[:8] hex | 8 字节够区分多公钥；同 license 设计 |

### 4.2 签名内容

```
to_sign = canonical_manifest_bytes
       = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
```

**关键点**：
- `sort_keys=True`：跨平台 / 跨语言一致
- `ensure_ascii=False`：保留中文 UTF-8 字节（不转 `\uXXXX`）
- `separators=(",", ":")`：紧凑格式（无多余空格 / 换行）
- 编码 `utf-8`（不带 BOM）

**陷阱**：客户写 plugin.json 时格式可能很随意（缩进 4 空格、含注释），**签名工具会先 reformat 成 canonical 格式再签 + 写回**。客户拿到的 plugin.json 文件就是 canonical 的。

### 4.3 签名生成

```python
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def sign_manifest(manifest_bytes: bytes, private_key_pem: bytes,
                  password: bytes) -> bytes:
    """RSA-PSS-SHA256 签名

    Args:
        manifest_bytes: canonical manifest JSON bytes
        private_key_pem: PEM-encoded RSA private key (encrypted)
        password: 私钥保护密码

    Returns:
        512 bytes (4096-bit RSA 签名)
    """
    pk = serialization.load_pem_private_key(
        private_key_pem, password=password
    )
    if not isinstance(pk, rsa.RSAPrivateKey):
        raise ValueError("Not an RSA private key")
    if pk.key_size != 4096:
        raise ValueError(f"RSA key size must be 4096, got {pk.key_size}")

    signature = pk.sign(
        manifest_bytes,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=32,
        ),
        hashes.SHA256(),
    )
    assert len(signature) == 512, f"Expected 512 bytes, got {len(signature)}"
    return signature
```

### 4.4 验签

```python
def verify_manifest_signature(manifest_bytes: bytes, signature: bytes,
                               public_key_pem: bytes) -> bool:
    """RSA-PSS-SHA256 验签"""
    from cryptography.exceptions import InvalidSignature

    pk = serialization.load_pem_public_key(public_key_pem)
    try:
        pk.verify(
            signature,
            manifest_bytes,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=32,
            ),
            hashes.SHA256(),
        )
        return True
    except InvalidSignature:
        return False
```

### 4.5 公钥指纹算法

```python
def public_key_fingerprint(public_key_pem: bytes) -> bytes:
    """计算公钥指纹（用于多公钥定位）

    Returns:
        8 bytes
    """
    pk = serialization.load_pem_public_key(public_key_pem)
    der = pk.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).digest()[:8]
```

---

## 五、客户码 HMAC

### 5.1 算法

```python
import hmac
import hashlib


PLUGIN_SECRET = b"<32-byte secret embedded in main program>"  # 见 §六


def calc_customer_hmac(customer_code: str) -> bytes:
    """计算客户码 HMAC

    Args:
        customer_code: 客户码（小写英数+连字符）

    Returns:
        32 bytes
    """
    if not customer_code:
        raise ValueError("customer_code is empty")
    return hmac.new(
        key=PLUGIN_SECRET,
        msg=customer_code.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()


def verify_customer_hmac(customer_code: str, expected_hmac: bytes) -> bool:
    """常量时间对比"""
    return hmac.compare_digest(
        calc_customer_hmac(customer_code), expected_hmac
    )
```

### 5.2 PLUGIN_SECRET 的生成与分发

> ⚠️ 这是 §1.3 提到的"中级攻击者门槛"——非高安全防御。

```python
# 一次性生成（在主作者本地）
import secrets
PLUGIN_SECRET = secrets.token_bytes(32)
print(PLUGIN_SECRET.hex())
# → 写到 backend/core/plugin_secret.py（GitIgnore），然后 main.py import
```

**保管**：
- `backend/core/plugin_secret.py` 加入 `.gitignore`（**绝不进 git**）
- 同时存放在主作者的密码管理器（如 1Password）
- CI 通过环境变量 `PLUGIN_SECRET_HEX` 注入

```python
# backend/core/plugin_secret.py（生产）
import os

_hex = os.environ.get("PLUGIN_SECRET_HEX")
if not _hex:
    # 开发环境兜底：固定值（仅 DEBUG 时用）
    if os.environ.get("DEBUG_MODE") == "1":
        _hex = "00" * 32
    else:
        raise RuntimeError("PLUGIN_SECRET_HEX env not set")

PLUGIN_SECRET: bytes = bytes.fromhex(_hex)
assert len(PLUGIN_SECRET) == 32
```

**轮换策略**：
- 默认**不轮换**（一旦换，所有已签插件失效）
- 若怀疑泄露 → 必须**全部插件重新签名**（详见 §九）

### 5.3 这一步加了什么 + 不加什么

| 攻击 | 不加 HMAC（仅 RSA） | 加了 HMAC |
|---|---|---|
| 改 plugin.json 内容 | RSA 拦 | RSA 拦 |
| 改文件内容 | files_digest 拦 | files_digest 拦 |
| 改目录名 acme → xyz | RSA 拦（manifest 内有 customer_code） | RSA 拦 |
| **改 license.dat 的 customerName** | **不拦**（license RSA 是另一对密钥） | **HMAC 拦**（plugin 的 customer_code 与 license 不一致 → manifest 验证 §七 阶段 4.2 拦） |
| 反编译主程序拿 PLUGIN_SECRET，再伪造一份新 manifest 的 HMAC | RSA 仍拦（没私钥签不出新 manifest） | RSA 仍拦 |

> 实际上 HMAC 主要是防"客户运维拿 ACME 插件想给 XYZ 用"——他们不会反编译 Nuitka，但能改 license.dat。
> 如果客户运维有反编译能力，那他们直接关掉验签函数也能跑（任何客户端验签都防不住）。

---

## 六、signature.bin 二进制格式

### 6.1 字节布局

```
┌─────────────────────────────────────────────────────────────┐
│ Offset  Size  Field                Value                    │
├─────────────────────────────────────────────────────────────┤
│ 0x00    4     magic                "TJVP"  (TianJun Vision  │
│                                            Plugin)         │
│ 0x04    1     format_version        0x01                    │
│ 0x05    1     reserved              0x00                    │
│ 0x06    2     flags (uint16 BE)     0x0000                  │
│                                     bit 0: GPU required     │
│                                     bit 1: experimental     │
│                                     bit 2-15: reserved      │
│ 0x08    8     public_key_fingerprint                        │
│                                     (SHA256(DER pubkey)[:8])│
│ 0x10    32    customer_hmac        HMAC-SHA256(cc, secret)  │
│ 0x30    2     rsa_sig_len (uint16 BE)  0x0200 = 512         │
│ 0x32    512   rsa_signature                                 │
│ 0x232   ...   sig_metadata_json (变长，以 \0 结尾)          │
│              {"signed_at":"...","signed_by":"...",          │
│               "manifest_sha256":"..."}                      │
│ ...     ...   padding (0x00) 直到总长度对齐 8 字节          │
└─────────────────────────────────────────────────────────────┘

最小总长度: 0x232 + 1 (\0) = 563 字节
最大总长度: 1024 字节（sig_metadata_json ≤ 460 字节）
```

### 6.2 解析代码

```python
import struct
import json
from dataclasses import dataclass


@dataclass
class SignatureBlob:
    format_version: int
    flags: int
    public_key_fingerprint: bytes  # 8 bytes
    customer_hmac: bytes           # 32 bytes
    rsa_signature: bytes           # 512 bytes
    sig_metadata: dict             # parsed JSON

    @property
    def gpu_required(self) -> bool:
        return bool(self.flags & 0x0001)

    @property
    def experimental(self) -> bool:
        return bool(self.flags & 0x0002)


def parse_signature_blob(data: bytes) -> SignatureBlob:
    """解析 signature.bin

    Raises:
        SignatureFormatError: 格式不对
    """
    if len(data) < 563:
        raise SignatureFormatError(f"too short: {len(data)}")
    if len(data) > 1024:
        raise SignatureFormatError(f"too long: {len(data)}")

    # Magic
    if data[0:4] != b"TJVP":
        raise SignatureFormatError(f"bad magic: {data[0:4]!r}")

    fmt_ver = data[4]
    if fmt_ver != 0x01:
        raise SignatureFormatError(f"unsupported format version: {fmt_ver}")

    reserved = data[5]
    flags = struct.unpack(">H", data[6:8])[0]
    pk_fp = data[8:16]
    customer_hmac = data[16:48]
    rsa_sig_len = struct.unpack(">H", data[48:50])[0]
    if rsa_sig_len != 512:
        raise SignatureFormatError(f"unexpected rsa_sig_len: {rsa_sig_len}")

    rsa_sig = data[50:562]

    # 找 sig_metadata 边界（首个 \0）
    meta_start = 562
    meta_end = data.find(b"\x00", meta_start)
    if meta_end < 0:
        raise SignatureFormatError("metadata not terminated")
    meta_json = data[meta_start:meta_end].decode("utf-8")
    try:
        meta = json.loads(meta_json)
    except json.JSONDecodeError as e:
        raise SignatureFormatError(f"bad metadata json: {e}")

    return SignatureBlob(
        format_version=fmt_ver,
        flags=flags,
        public_key_fingerprint=pk_fp,
        customer_hmac=customer_hmac,
        rsa_signature=rsa_sig,
        sig_metadata=meta,
    )
```

### 6.3 写入代码

```python
def write_signature_blob(
    output_path: Path,
    public_key_fingerprint: bytes,
    customer_hmac: bytes,
    rsa_signature: bytes,
    sig_metadata: dict,
    gpu_required: bool = False,
) -> None:
    """写 signature.bin

    sig_metadata 必须包含:
        signed_at, signed_by, manifest_sha256
    """
    assert len(public_key_fingerprint) == 8
    assert len(customer_hmac) == 32
    assert len(rsa_signature) == 512

    flags = 0
    if gpu_required:
        flags |= 0x0001

    meta_json = json.dumps(
        sig_metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(meta_json) > 460:
        raise ValueError(f"sig_metadata too large: {len(meta_json)}")

    buf = bytearray()
    buf.extend(b"TJVP")
    buf.append(0x01)              # format_version
    buf.append(0x00)              # reserved
    buf.extend(struct.pack(">H", flags))
    buf.extend(public_key_fingerprint)
    buf.extend(customer_hmac)
    buf.extend(struct.pack(">H", 512))  # rsa_sig_len
    buf.extend(rsa_signature)
    buf.extend(meta_json)
    buf.append(0x00)              # \0 terminator

    # 8 字节对齐 padding
    while len(buf) % 8 != 0:
        buf.append(0x00)

    output_path.write_bytes(bytes(buf))
```

### 6.4 sig_metadata 字段

`sig_metadata` 是 JSON，三个必填字段：

```json
{
  "signed_at": "2026-05-08T05:30:00Z",
  "signed_by": "tianjun-ai-master-2026",
  "manifest_sha256": "1a2b3c..."
}
```

| 字段 | 含义 |
|---|---|
| `signed_at` | 签名时间戳（与 plugin.json 内 signed_at 必须一致） |
| `signed_by` | 签名者（与 plugin.json 内 signed_by 必须一致） |
| `manifest_sha256` | manifest_bytes 的 SHA256（冗余校验，加快预筛） |

> **冗余设计**：`signed_at` / `signed_by` 在 manifest 和 sig_metadata 各存一份。验签时**必须严格相等**——不一致 = 篡改。

---

## 七、验签完整流程（加载器视角）

### 7.1 完整流程

```python
class PluginVerifier:
    """加载器调用此类完成验签"""

    def __init__(self, public_keys: dict[bytes, bytes]):
        """
        Args:
            public_keys: {fingerprint(8 bytes): public_key_pem(bytes)}
        """
        self.public_keys = public_keys

    def verify(self, plugin_dir: Path,
               license_payload: dict) -> tuple[bool, str]:
        """完整验签流程

        Returns:
            (success, reason)
            如果 success=False，reason 是错误代码（不暴露细节）
        """
        # ============ 阶段 0: 文件存在 ============
        manifest_path = plugin_dir / "plugin.json"
        sig_path = plugin_dir / "signature.bin"

        if not manifest_path.exists():
            return False, "MANIFEST_NOT_FOUND"
        if not sig_path.exists():
            return False, "SIGNATURE_NOT_FOUND"

        # ============ 阶段 1: 读 + 解析 signature ============
        try:
            sig_blob_data = sig_path.read_bytes()
            sig_blob = parse_signature_blob(sig_blob_data)
        except SignatureFormatError:
            return False, "PLUGIN_SIGNATURE_FAIL"

        # ============ 阶段 2: 公钥定位 ============
        public_key_pem = self.public_keys.get(sig_blob.public_key_fingerprint)
        if public_key_pem is None:
            # 既可能是公钥被吊销, 也可能是攻击者用别的私钥
            return False, "PLUGIN_SIGNATURE_FAIL"

        # ============ 阶段 3: 读 manifest bytes ============
        try:
            manifest_bytes = manifest_path.read_bytes()
        except OSError:
            return False, "PLUGIN_SIGNATURE_FAIL"

        # 长度限制
        if len(manifest_bytes) > 256 * 1024:
            return False, "MANIFEST_TOO_LARGE"

        # ============ 阶段 4: 预筛 manifest_sha256 ============
        actual_manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        if actual_manifest_sha != sig_blob.sig_metadata.get("manifest_sha256"):
            return False, "PLUGIN_SIGNATURE_FAIL"

        # ============ 阶段 5: RSA 验签（最贵的一步，放后面）============
        try:
            ok = verify_manifest_signature(
                manifest_bytes, sig_blob.rsa_signature, public_key_pem
            )
        except Exception:
            return False, "PLUGIN_SIGNATURE_FAIL"
        if not ok:
            return False, "PLUGIN_SIGNATURE_FAIL"

        # ============ 阶段 6: 解析 manifest ============
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False, "PLUGIN_SIGNATURE_FAIL"

        # ============ 阶段 7: customer_hmac 校验 ============
        customer_code = manifest.get("customer_code")
        if not customer_code or not isinstance(customer_code, str):
            return False, "PLUGIN_SIGNATURE_FAIL"

        if not verify_customer_hmac(customer_code, sig_blob.customer_hmac):
            return False, "PLUGIN_SIGNATURE_FAIL"

        # ============ 阶段 8: license 一致性 ============
        if license_payload.get("customerName") != customer_code:
            return False, "PLUGIN_LICENSE_MISMATCH"

        # ============ 阶段 9: signed_at / signed_by 冗余校验 ============
        if (manifest.get("signed_at") != sig_blob.sig_metadata.get("signed_at")
                or manifest.get("signed_by") != sig_blob.sig_metadata.get("signed_by")):
            return False, "PLUGIN_SIGNATURE_FAIL"

        # ============ 阶段 10: files_digest 校验 ============
        actual_digest = calc_files_digest(plugin_dir)
        if actual_digest != manifest.get("files_digest"):
            return False, "PLUGIN_FILES_DIGEST_MISMATCH"

        return True, "OK"
```

### 7.2 性能优化（按代价排序）

加载阶段总耗时 ≤ 100 ms（4096 RSA 验签是最贵的一步）。

| 阶段 | 耗时 | 优先级 |
|---|---|---|
| 0~2 文件 IO | ~1 ms | 优先做 |
| 3~4 manifest_sha256 预筛 | ~1 ms | 早失败 |
| 5 RSA 验签 | ~30~80 ms | 慢，但必须做 |
| 6 JSON 解析 | ~1 ms | 在 RSA 后做（防止恶意 JSON 炸 parser）|
| 7 HMAC 校验 | ~0.1 ms | 快 |
| 8 license 比对 | ~0 ms | 快 |
| 9 冗余字段比对 | ~0 ms | 快 |
| 10 files_digest 计算 | ~10~500 ms（取决插件大小）| 最贵 |

**优化点**：
- 阶段 4 预筛先抓 99% 的篡改（不用算 RSA）
- 阶段 10 files_digest 在 RSA 通过后再算（攻击者改了文件 + 改 plugin.json 的 files_digest 字段，会被 RSA 拦下）

### 7.3 安全注意

- **常量时间比对**：HMAC 比对必须用 `hmac.compare_digest`（不要 `==`）
- **错误信息不泄露**：所有签名相关 fail 统一报 `PLUGIN_SIGNATURE_FAIL`，不告诉攻击者具体哪步
- **日志写完整原因**：但**只在 backend log 里**写，不返回给前端 / 不暴露到 license 文件

```python
# 加载器内部
logger.error("插件 %s 验签失败 [%s]", plugin_dir.name, reason_detail)
# 返回给前端
return {"success": False, "error": "PLUGIN_SIGNATURE_FAIL"}
```

---

## 八、密钥管理（design 00 Q2=A，主作者私人保管）

### 8.1 私钥生成（一次性）

```bash
# 主作者本地，离线机器
mkdir -p ~/.tianjun-plugin-keys/
cd ~/.tianjun-plugin-keys/

# 1. 生成 4096 位 RSA 私钥（带密码保护）
openssl genrsa -aes-256-cbc -out plugin_master.pem 4096
# Enter PEM pass phrase: <强密码>

# 2. 导出公钥
openssl rsa -in plugin_master.pem -pubout -out plugin_master.pub

# 3. 计算指纹
python3 -c "
from cryptography.hazmat.primitives import serialization
import hashlib
data = open('plugin_master.pub', 'rb').read()
pk = serialization.load_pem_public_key(data)
der = pk.public_bytes(
    encoding=serialization.Encoding.DER,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
)
print('Fingerprint:', hashlib.sha256(der).hexdigest()[:16])
"
```

### 8.2 保管要求（design 00 Q2=A）

| 介质 | 数量 | 用途 |
|---|---|---|
| 加密 USB 1（主） | 1 | 日常打包用 |
| 加密 USB 2（备份） | 1 | 离线异地存放 |
| 1Password / Bitwarden | 1 | 私钥密码 + 私钥导出文本 |
| 公司保险柜（书面 QR） | 1 | 长期备份 |

**绝不**：
- 进 git（不论 public / private 仓库）
- 进 CI 环境变量
- 进任何主程序代码（包括 Nuitka 编译产物）
- 进电子邮件 / IM
- 拷贝到非加密磁盘

### 8.3 公钥分发（进主程序）

```python
# backend/core/plugin_public_keys.py
"""
内置插件公钥列表
- 通过 git 维护
- 与主程序一起编译进 Nuitka
- 公开（任何人都可以拿，只能验签不能签）
"""

PLUGIN_PUBLIC_KEYS: dict[str, dict] = {
    # SHA256(DER pubkey)[:8] hex -> {pem, signed_by, valid_from, valid_to}
    "1a2b3c4d5e6f7a8b": {
        "pem": b"""-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA...
-----END PUBLIC KEY-----""",
        "signed_by": "tianjun-ai-master-2026",
        "valid_from": "2026-01-01",
        "valid_to": "2030-01-01",  # 4 年有效期
        "description": "TianJun AI 主签名密钥（2026~2029）",
    },
    # 第二把: 备用公钥（应急切换用，先内置但还没启用）
    # ...
}


def get_public_key_by_fingerprint(fp: bytes) -> bytes | None:
    fp_hex = fp.hex()
    entry = PLUGIN_PUBLIC_KEYS.get(fp_hex)
    if entry is None:
        return None
    # 校验有效期
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).date().isoformat()
    if not (entry["valid_from"] <= now <= entry["valid_to"]):
        return None
    return entry["pem"]
```

### 8.4 公钥轮换（不同于密钥泄露）

正常轮换（4 年一次）：

```
T-180 天: 生成新密钥对（v2025 → v2030）
T-90  天: 主程序代码加新公钥（同时保留旧公钥）
T-0   天: 切换签名工具用新私钥
T+90  天: 所有新插件用新私钥签
T+365 天: 旧插件全部重签
T+730 天: 主程序代码移除旧公钥
```

**关键**：在过渡期，主程序**同时支持新老两个公钥**——通过 fingerprint 区分。

---

## 九、私钥泄露应急 SOP

### 9.1 触发信号

- 主作者发现 USB 丢失 / 被盗
- 1Password 账户异常
- 发现野外有"伪天骏插件"流通
- 主作者离职 / 设备处置

### 9.2 SOP 步骤

```
0  小时: 确认泄露 → 主作者 + 团队负责人 + 老板三方签字 → 启动应急
2  小时: 生成新密钥对 (master_v2)
4  小时: 主程序代码 push 紧急 hotfix:
        - 加 master_v2 公钥
        - 把 master_v1 公钥的 valid_to 改为今天
24 小时: hotfix 推送给所有客户工控机
        - 用 hotfix 通道（不重新打包安装包）
1  周  : 用 master_v2 重签所有现有插件
        - 通知客户更新插件
1  月  : 删除 master_v1 公钥（彻底失效）
```

### 9.3 一键应急脚本

```bash
# scripts/emergency-rotate-key.sh
#!/usr/bin/env bash
set -euxo pipefail

# 1. 生成新密钥
openssl genrsa -aes-256-cbc -out plugin_master_v2.pem 4096
openssl rsa -in plugin_master_v2.pem -pubout -out plugin_master_v2.pub

# 2. 自动改 plugin_public_keys.py
python scripts/inject_public_key.py plugin_master_v2.pub --revoke-old

# 3. 自动重签所有 plugins/* 目录
for dir in plugins/*/; do
    python scripts/sign-plugin.py "$dir" --key plugin_master_v2.pem
done

# 4. 触发 hotfix 打包
python scripts/build-hotfix.py --reason "key-rotation"
```

### 9.4 客户侧影响

- 老插件**立即失效**（错误代码 `PLUGIN_SIGNATURE_FAIL`）
- 新插件需要主程序 hotfix 后才能加载
- **不影响主程序本身运行**（只是插件停了）
- 客户在 Settings 看到的提示："插件验签失败，请联系厂商升级"

---

## 十、攻击场景演练

### 10.1 场景 A：客户运维改 plugin.json 内 customer_code

```
原: "customer_code": "acme"
改: "customer_code": "xyz"  ← 想偷给 xyz 用

步骤:
1. RSA 验签：失败（manifest_bytes 变了，签名对不上）
2. 加载终止 → PLUGIN_SIGNATURE_FAIL ✅ 拦下
```

### 10.2 场景 B：客户运维改 license.dat 让 customerName=acme

```
原 license: customerName=xyz
改 license: customerName=acme  ← 想用 acme 插件

步骤:
1. license 自己的 RSA 签名 → 失败（license 也是签的）✅ 拦下

如果客户绕过 license 验证（修改主程序）:
2. 那么 plugin 加载流程 §阶段 8 license_payload 比对仍然过
3. 但 plugin 仍能加载 ❌ 这种情况防不住

→ 这种情况下客户已经控制了主程序代码，任何客户端验签都防不住
→ 我们的边界: 不防客户改主程序源码
```

### 10.3 场景 C：客户拿 ACME 插件，改目录名 acme → xyz

```
步骤:
1. 加载器扫描 plugins/xyz/
2. 读 plugin.json，customer_code 仍是 "acme"（攻击者忘改）
3. 阶段 4.1 customer_code 与目录名不一致 → 拒绝 ✅ 拦下

如果攻击者也改 plugin.json 的 customer_code:
4. RSA 验签失败 → PLUGIN_SIGNATURE_FAIL ✅ 拦下
```

### 10.4 场景 D：客户反编译主程序拿 PLUGIN_SECRET，伪造 HMAC

```
攻击者:
1. 反编译 Nuitka，找到 PLUGIN_SECRET 字节
2. 自己写一份 plugin.json (cc=xyz)
3. 算 HMAC(xyz, PLUGIN_SECRET)
4. 但 RSA 签名要私钥，没有 → 写不出 signature.bin
5. 加载失败 ✅ 仍拦下（RSA 是底线）
```

### 10.5 场景 E：私钥泄露 + PLUGIN_SECRET 泄露（双重失守）

```
攻击者完全可以伪造合法插件:
1. 用泄露的私钥签
2. 用泄露的 secret 算 HMAC
3. 拼一份 plugin 给 ACME 用

防御:
→ 应急 SOP（§九） rotate keys
→ 客户主程序 hotfix 后, 旧伪造插件失效
```

### 10.6 场景 F：MITM 攻击 GitHub Release

```
攻击者:
1. 拦截客户下载 acme.tjvplugin
2. 替换为伪造插件

防御:
1. HTTPS（GitHub 自带）+ ETag
2. 即使下载到伪造文件, 客户工控机加载仍 RSA 验签失败 ✅ 拦下
```

---

## 十一、签名工具与加载器的 API 契约

### 11.1 签名工具（design 07 详细）

```python
# scripts/sign-plugin.py
"""
打包 + 签名插件目录

Usage:
    python sign-plugin.py plugins/acme/ \
        --key ~/.tianjun-plugin-keys/plugin_master.pem \
        --signer "tianjun-ai-master-2026" \
        --output dist/acme-1.0.0.tjvplugin

流程:
    1. 读取 plugin.json
    2. JSON Schema 校验
    3. canonical reformat plugin.json
    4. 计算 files_digest, 写回 plugin.json
    5. canonical 序列化 → manifest_bytes
    6. 询问私钥密码 (getpass)
    7. RSA 签名
    8. 计算 customer_hmac (PLUGIN_SECRET 通过环境变量传入)
    9. 写 signature.bin
    10. ZIP 打包成 .tjvplugin（design 00 Q3=A）
    11. 输出指纹用于审计
"""
```

### 11.2 加载器（design 06 详细）

```python
# backend/core/plugin_manager.py
class PluginManager:
    def __init__(self, plugins_dir: Path, license_payload: dict):
        self.plugins_dir = plugins_dir
        self.license_payload = license_payload
        self.verifier = PluginVerifier(public_keys=load_builtin_public_keys())

    def load_active_plugin(self) -> Plugin | None:
        """加载唯一活动插件（design 00 第二节: 单插件激活）"""
        # 找到 plugins/{customer_code}/ 目录
        # 验签
        # 加载档位资源
        ...
```

---

## 十二、测试策略（design 06 验收点）

### 12.1 单元测试（pytest）

```python
# backend/tests/test_signature.py

def test_files_digest_consistent_cross_platform(tmp_path):
    """同样内容必须算出同样 digest"""
    # 略

def test_files_digest_excludes_plugin_json(tmp_path):
    """plugin.json 自己不参与 digest"""
    # 略

def test_files_digest_excludes_signature_bin(tmp_path):
    """signature.bin 不参与 digest"""
    # 略

def test_files_digest_path_separator_normalized(tmp_path):
    """Windows \\ 转 / 后 digest 与 Linux 一致"""
    # 略

def test_rsa_sign_verify_roundtrip():
    """sign + verify 闭环"""

def test_rsa_verify_fails_on_modified_manifest():
    """改一字节就 fail"""

def test_customer_hmac_constant_time():
    """compare_digest 是常量时间"""

def test_signature_blob_parse_invalid_magic():
    """魔数错就抛 SignatureFormatError"""

def test_full_verify_happy_path(tmp_path, signed_plugin):
    """完整流程通过"""

def test_full_verify_modified_file(tmp_path, signed_plugin):
    """改 backend/__init__.py 就 PLUGIN_FILES_DIGEST_MISMATCH"""

def test_full_verify_modified_manifest(tmp_path, signed_plugin):
    """改 plugin.json 就 PLUGIN_SIGNATURE_FAIL"""

def test_full_verify_wrong_customer_code(tmp_path, signed_plugin):
    """改目录名就 MANIFEST_CUSTOMER_CODE_DIR_MISMATCH"""

def test_full_verify_unknown_public_key(tmp_path, signed_plugin):
    """换公钥指纹就 PLUGIN_SIGNATURE_FAIL"""

def test_full_verify_expired_public_key(tmp_path, signed_plugin):
    """公钥过期就 PLUGIN_SIGNATURE_FAIL"""

def test_full_verify_license_mismatch(tmp_path, signed_plugin):
    """license customerName 不一致 PLUGIN_LICENSE_MISMATCH"""
```

### 12.2 集成测试

- 真实生成 RSA 密钥 → 真实 sign → 真实 verify
- 跨 OS：Linux 签 → Windows 验 / Windows 签 → Linux 验

### 12.3 渗透测试用例（手工）

| 用例 | 期望 |
|---|---|
| 删 signature.bin | `SIGNATURE_NOT_FOUND` |
| signature.bin 全 0 | `PLUGIN_SIGNATURE_FAIL` |
| signature.bin 头 4 字节改 "AAAA" | `PLUGIN_SIGNATURE_FAIL` |
| signature.bin 中 RSA 部分改 1 字节 | `PLUGIN_SIGNATURE_FAIL` |
| signature.bin 中 HMAC 部分改 1 字节 | `PLUGIN_SIGNATURE_FAIL` |
| 把 acme 插件目录复制成 xyz/ + 改 manifest cc=xyz | `PLUGIN_SIGNATURE_FAIL` |
| 把 acme 插件 zip 解压重新 zip（顺序变） | OK（zip 顺序无关 digest） |
| 中文文件名 | OK |
| 空文件 | OK（参与 digest） |
| 50 MB 二进制资源 | OK（≤ 1 秒算 digest） |

### 12.4 性能基准

| 场景 | 期望 |
|---|---|
| 1 MB 插件验签 | ≤ 100 ms |
| 50 MB 插件验签 | ≤ 600 ms |
| 200 MB 插件验签 | ≤ 2 s |

---

## 十三、与 license 系统的边界澄清

> 主程序已有 license 系统（`electron/src/main/license-manager.js`），同样用 RSA。**它和插件签名是两套不同的密钥对**。

| | License 系统 | 插件签名系统 |
|---|---|---|
| 私钥保管 | 公司另一团队 | 主作者一人 |
| 公钥位置 | electron 主进程 / Nuitka 后端 | Nuitka 后端 plugin_public_keys.py |
| 算法 | RSA-SHA256 PKCS#1 v1.5 | RSA-PSS-SHA256 |
| 用途 | 客户授权（machineId 绑定） | 插件防伪 + 客户码绑定 |
| 失效后果 | 整个主程序退出 | 插件不加载，主程序继续 |

**两个系统在 §阶段 8 唯一的交点**：plugin 的 `customer_code` 必须 == license 的 `customerName`。

> 未来若 license 系统迁移到 RSA-PSS，可以共用密钥对——但现阶段保持独立。

---

## 十四、开放点（不阻塞实现，留作 review）

| 编号 | 议题 | 建议 |
|---|---|---|
| **S1** | sig_metadata 是否要塞 plugin_version | 当前不塞，因为 manifest 内已经有 |
| **S2** | 是否要支持多签（multi-signer） | 当前不做（单一主作者） |
| **S3** | 是否要支持时间戳服务 (RFC3161) | 当前不做（客户离线） |
| **S4** | 是否要支持插件链（plugin depends on plugin） | 当前不做（design 00 单插件激活） |
| **S5** | 公钥列表是否要支持运行时增量加载 | 当前不做（每次 hotfix 主程序更新） |
| **S6** | 是否要给客户工控机分配独立 secret（每机一个） | 当前不做（HMAC 全机器共享 secret） |
| **S7** | 是否要把 sig_metadata 也签进 RSA | **建议加**：把 sig_metadata 拼到 manifest_bytes 后一起签，下版本可加（v2 manifest） |

---

## 十五、本文决策摘要（供下游引用）

| 决策点 | 值 |
|---|---|
| RSA 算法 | RSA-PSS-SHA256, MGF1, salt=32 |
| RSA 密钥长度 | 4096 bit |
| HMAC 算法 | HMAC-SHA256 |
| HMAC 密钥长度 | 32 bytes |
| 签名内容 | canonical manifest bytes（sort_keys=True, ensure_ascii=False, separators=,:） |
| signature.bin 格式 | TJVP 魔数 + 字节级布局（详 §六） |
| 公钥指纹算法 | SHA256(DER pubkey)[:8] |
| 公钥分发方式 | 内置在主程序代码（编译进 Nuitka） |
| 公钥有效期 | 4 年 |
| 私钥保管 | 主作者私人 USB×2 + 1Password + 保险柜 |
| PLUGIN_SECRET 注入 | 环境变量 `PLUGIN_SECRET_HEX` 或 plugin_secret.py（gitignore） |
| 验签错误统一 | `PLUGIN_SIGNATURE_FAIL`（不细分） |
| files_digest 算法 | SHA256, 排除 plugin.json + signature.bin + 噪声目录 |
| 单元测试覆盖 | 14 个核心 case |
| 渗透测试用例 | 10 个手工 case |

---

**本文最后更新**：2026-05-08
**事实校验**：基于 design 00 Q1=C / Q2=A，对齐 license 现有 cryptography 库 + Nuitka 编译约束
**下一文档**：design/03_database.md（plugins 表 / plugin_state 表 / PG-SQLite 双跑）
