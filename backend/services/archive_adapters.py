# -*- coding: utf-8 -*-
"""归档目的地 adapter (v3.53 四期)。

内置: local_dir(本地/UNC) | ftp | sftp(需 paramiko) | s3(需 boto3) | http(multipart)
插件: dest_type = "plugin:<name>" → register_archive_adapter() 注册的回调
      (插件平台通过 PluginHost.register_archive_adapter 转投到这里)

统一契约:
    deliver(src_path, subdir, filename, cfg, throttle_kbps) -> 最终地址串
    - subdir: 日期子目录名 (空串 = 不分层)
    - 抛 PermanentDeliveryError = 配置类错误不重试; 其他异常 = 瞬态可重试
    - 本地类目的地负责 .tmp + rename 原子落位; 远端协议按各自最优实现
    - 重名策略仅 local_dir 完整支持 (rename/overwrite/skip);
      远端 adapter 为覆盖语义 (远端存在性探测各协议成本不一, UI 已注明)
"""
import os
import shutil
import time
from typing import Any, Callable, Dict, Optional


class PermanentDeliveryError(Exception):
    """配置/凭据/依赖类错误, 重试也不会好。"""


# ---------------------------------------------------------------------------
# 限速工具
# ---------------------------------------------------------------------------

_CHUNK = 256 * 1024


def _iter_file_throttled(fp, throttle_kbps: Optional[int]):
    """按 256KB 块读文件, 限速时在块间 sleep。"""
    budget = (throttle_kbps or 0) * 1024  # bytes per second
    t_window = time.time()
    sent_in_window = 0
    while True:
        chunk = fp.read(_CHUNK)
        if not chunk:
            return
        yield chunk
        if budget > 0:
            sent_in_window += len(chunk)
            if sent_in_window >= budget:
                elapsed = time.time() - t_window
                if elapsed < 1.0:
                    time.sleep(1.0 - elapsed)
                t_window = time.time()
                sent_in_window = 0


class _ThrottledReader:
    """给 ftplib/paramiko/requests 用的限速文件包装 (只实现 read)。"""

    def __init__(self, fp, throttle_kbps: Optional[int]):
        self._it = _iter_file_throttled(fp, throttle_kbps)
        self._buf = b""

    def read(self, size=-1):
        if size is None or size < 0:
            chunks = [self._buf] + list(self._it)
            self._buf = b""
            return b"".join(chunks)
        while len(self._buf) < size:
            try:
                self._buf += next(self._it)
            except StopIteration:
                break
        out, self._buf = self._buf[:size], self._buf[size:]
        return out


# ---------------------------------------------------------------------------
# 内置 adapter
# ---------------------------------------------------------------------------

def _deliver_local(src_path: str, subdir: str, filename: str,
                   cfg: Dict[str, Any], throttle_kbps: Optional[int]) -> str:
    """本地/已挂载网络盘。cfg: {"dest_dir": 绝对路径, "overwrite_policy": ...}
    唯一支持完整重名策略的 adapter; skip 时返回带 [skip] 前缀的地址。"""
    dest_dir = os.path.expanduser((cfg.get("dest_dir") or "").strip())
    if not dest_dir:
        raise PermanentDeliveryError("local_dir 缺少目标目录")
    if subdir:
        dest_dir = os.path.join(dest_dir, subdir)
    os.makedirs(dest_dir, exist_ok=True)

    policy = cfg.get("overwrite_policy") or "rename"
    dest = os.path.join(dest_dir, filename)
    if os.path.exists(dest):
        if policy == "skip":
            return "[skip]" + dest
        if policy == "rename":
            base, ext = os.path.splitext(filename)
            for i in range(1, 1000):
                cand = os.path.join(dest_dir, f"{base}_{i}{ext}")
                if not os.path.exists(cand):
                    dest = cand
                    break

    tmp = dest + f".tmp.{os.getpid()}"
    try:
        if throttle_kbps:
            with open(src_path, "rb") as fsrc, open(tmp, "wb") as fdst:
                for chunk in _iter_file_throttled(fsrc, throttle_kbps):
                    fdst.write(chunk)
            shutil.copystat(src_path, tmp)
        else:
            shutil.copy2(src_path, tmp)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return dest


def _deliver_ftp(src_path: str, subdir: str, filename: str,
                 cfg: Dict[str, Any], throttle_kbps: Optional[int]) -> str:
    """FTP。cfg: host/port/user/password/remote_dir/timeout/passive"""
    import ftplib
    host = (cfg.get("host") or "").strip()
    if not host:
        raise PermanentDeliveryError("ftp 缺少 host")
    port = int(cfg.get("port") or 21)
    ftp = ftplib.FTP()
    ftp.connect(host, port, timeout=float(cfg.get("timeout") or 15))
    try:
        ftp.login(cfg.get("user") or "anonymous", cfg.get("password") or "")
        ftp.set_pasv(bool(cfg.get("passive", True)))
        remote_dir = (cfg.get("remote_dir") or "/").rstrip("/")
        parts = [p for p in (remote_dir + ("/" + subdir if subdir else "")).split("/") if p]
        path = ""
        for p in parts:
            path += "/" + p
            try:
                ftp.mkd(path)
            except ftplib.error_perm:
                pass  # 已存在
        final_dir = path or "/"
        tmp_name = filename + ".part"
        with open(src_path, "rb") as f:
            ftp.storbinary(f"STOR {final_dir}/{tmp_name}",
                           _ThrottledReader(f, throttle_kbps), blocksize=_CHUNK)
        try:
            ftp.delete(f"{final_dir}/{filename}")
        except ftplib.error_perm:
            pass
        ftp.rename(f"{final_dir}/{tmp_name}", f"{final_dir}/{filename}")
        return f"ftp://{host}{final_dir}/{filename}"
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()


def _deliver_sftp(src_path: str, subdir: str, filename: str,
                  cfg: Dict[str, Any], throttle_kbps: Optional[int]) -> str:
    """SFTP。cfg: host/port/user/password/remote_dir/timeout。需 paramiko。"""
    try:
        import paramiko
    except ImportError:
        raise PermanentDeliveryError("sftp 需要 paramiko 库 (pip install paramiko)")
    host = (cfg.get("host") or "").strip()
    if not host:
        raise PermanentDeliveryError("sftp 缺少 host")
    transport = paramiko.Transport((host, int(cfg.get("port") or 22)))
    try:
        transport.connect(username=cfg.get("user") or "",
                          password=cfg.get("password") or "")
        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_dir = (cfg.get("remote_dir") or "/").rstrip("/")
        parts = [p for p in (remote_dir + ("/" + subdir if subdir else "")).split("/") if p]
        path = ""
        for p in parts:
            path += "/" + p
            try:
                sftp.stat(path)
            except FileNotFoundError:
                sftp.mkdir(path)
        final_dir = path or "/"
        tmp_remote = f"{final_dir}/{filename}.part"
        final_remote = f"{final_dir}/{filename}"
        if throttle_kbps:
            with open(src_path, "rb") as f, sftp.open(tmp_remote, "wb") as rf:
                for chunk in _iter_file_throttled(f, throttle_kbps):
                    rf.write(chunk)
        else:
            sftp.put(src_path, tmp_remote)
        try:
            sftp.remove(final_remote)
        except FileNotFoundError:
            pass
        sftp.rename(tmp_remote, final_remote)
        return f"sftp://{host}{final_remote}"
    finally:
        transport.close()


def _deliver_s3(src_path: str, subdir: str, filename: str,
                cfg: Dict[str, Any], throttle_kbps: Optional[int]) -> str:
    """S3/MinIO。cfg: endpoint_url(可空=AWS)/bucket/prefix/access_key/secret_key/region。
    需 boto3。"""
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError:
        raise PermanentDeliveryError("s3 需要 boto3 库 (pip install boto3)")
    bucket = (cfg.get("bucket") or "").strip()
    if not bucket:
        raise PermanentDeliveryError("s3 缺少 bucket")
    client = boto3.client(
        "s3",
        endpoint_url=(cfg.get("endpoint_url") or None),
        aws_access_key_id=cfg.get("access_key") or None,
        aws_secret_access_key=cfg.get("secret_key") or None,
        region_name=cfg.get("region") or None,
        config=BotoConfig(connect_timeout=15, read_timeout=60,
                          retries={"max_attempts": 1}),
    )
    prefix = (cfg.get("prefix") or "").strip("/")
    key = "/".join(x for x in (prefix, subdir, filename) if x)
    with open(src_path, "rb") as f:
        body = _ThrottledReader(f, throttle_kbps) if throttle_kbps else f
        client.put_object(Bucket=bucket, Key=key, Body=body.read()
                          if throttle_kbps else body)
    host = cfg.get("endpoint_url") or "s3"
    return f"{host.rstrip('/')}/{bucket}/{key}"


def _deliver_http(src_path: str, subdir: str, filename: str,
                  cfg: Dict[str, Any], throttle_kbps: Optional[int]) -> str:
    """HTTP multipart POST。cfg: url/field_name/token(加 Authorization Bearer)/
    headers(dict)/timeout。服务端 2xx 即成功; 响应体前 200 字拼进地址串留痕。"""
    import requests as _rq
    url = (cfg.get("url") or "").strip()
    if not url:
        raise PermanentDeliveryError("http 缺少 url")
    headers = dict(cfg.get("headers") or {})
    if cfg.get("token"):
        headers.setdefault("Authorization", f"Bearer {cfg['token']}")
    field = cfg.get("field_name") or "file"
    data = {"subdir": subdir, "filename": filename}
    with open(src_path, "rb") as f:
        fp = _ThrottledReader(f, throttle_kbps) if throttle_kbps else f
        resp = _rq.post(url, headers=headers, data=data,
                        files={field: (filename, fp, "application/octet-stream")},
                        timeout=float(cfg.get("timeout") or 60))
    if resp.status_code >= 400:
        # 4xx 视为配置/鉴权类永久失败, 5xx 瞬态重试
        msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
        if resp.status_code < 500:
            raise PermanentDeliveryError(msg)
        raise RuntimeError(msg)
    return f"{url} → {filename}"


# ---------------------------------------------------------------------------
# 注册表 (内置 + 插件)
# ---------------------------------------------------------------------------

DeliverFn = Callable[[str, str, str, Dict[str, Any], Optional[int]], str]

_BUILTIN: Dict[str, DeliverFn] = {
    "local_dir": _deliver_local,
    "ftp": _deliver_ftp,
    "sftp": _deliver_sftp,
    "s3": _deliver_s3,
    "http": _deliver_http,
}

_plugin_adapters: Dict[str, DeliverFn] = {}


def register_archive_adapter(name: str, deliver_fn: DeliverFn):
    """插件注册自定义归档 adapter。规则里 dest_type 填 "plugin:<name>"。
    deliver_fn(src_path, subdir, filename, cfg, throttle_kbps) -> 地址串。"""
    if not name or not callable(deliver_fn):
        raise ValueError("register_archive_adapter: name/deliver_fn 非法")
    _plugin_adapters[str(name)] = deliver_fn
    print(f"[VideoArchive] 插件 adapter 已注册: {name}")


def unregister_archive_adapter(name: str):
    _plugin_adapters.pop(str(name), None)


def list_adapter_types():
    return list(_BUILTIN.keys()) + [f"plugin:{n}" for n in _plugin_adapters]


def get_adapter(dest_type: str) -> DeliverFn:
    dt = (dest_type or "local_dir").strip()
    if dt in _BUILTIN:
        return _BUILTIN[dt]
    if dt.startswith("plugin:"):
        fn = _plugin_adapters.get(dt[len("plugin:"):])
        if fn:
            return fn
        raise PermanentDeliveryError(
            f"插件 adapter 未注册: {dt} (插件未激活或未调用 register_archive_adapter)")
    raise PermanentDeliveryError(f"未知目的地类型: {dt}")
