"""Loopback-only Tianjun commercial-license lease support."""
from __future__ import annotations

import base64
import datetime as dt
import ipaddress
import json
import os
import threading
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA7pzkErjVtIpWJ3GWqRKi
nPqsg8bG1MRjdHvYux1XLBWCUak1LakrU/WxrDKkyvhuKyA+y7GlT37VwvIpUB1v
An48IdKqk6mp5iPEzBeHx1+68VC8pSrtmFlOK7bRQq9BCmeAWOOcWpbSPJ0xX+kS
50v9v/Pxpv2EhciO0fz/uVuIp9LxlIUF8ho7auDLteGVT1tI8IAGPTQX7ouAYsE/
YWmtpmjetmSWKiscb0uUV74KFBPZyGk2y5a6JX4ELOI+KeWUP4rWcudFo2fNZG3n
hhckBIeqxc1xg3gPONve4C9SibDPvyWt3ULKSrBEFa+JLEbtAsHEhd/PrusjPuMb
mwIDAQAB
-----END PUBLIC KEY-----"""

_NONCE_TTL = dt.timedelta(minutes=5)
_REQUEST_WINDOW = dt.timedelta(minutes=2)
_nonce_lock = threading.Lock()
_seen_nonces: dict[str, dt.datetime] = {}


def _nonce_ledger_path() -> Path:
    root = Path(
        os.environ.get("TIANJUN_DATA_DIR")
        or (Path.home() / ".tianjun-ai-vision")
    )
    return root / "interconnect" / "license-lease-nonces.json"


def _load_nonce_ledger() -> dict[str, dt.datetime]:
    path = _nonce_ledger_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {
            key: dt.datetime.fromisoformat(value)
            for key, value in raw.items()
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _save_nonce_ledger(values: dict[str, dt.datetime]) -> None:
    path = _nonce_ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            {key: value.isoformat() for key, value in values.items()},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)


class LeaseError(ValueError):
    pass


def require_loopback(host: str | None) -> None:
    try:
        address = ipaddress.ip_address((host or "").split("%", 1)[0])
    except ValueError as exc:
        raise LeaseError("license lease requires a loopback client") from exc
    if not address.is_loopback:
        raise LeaseError("license lease requires a loopback client")


def validate_and_consume_nonce(
    nonce: str,
    requested_at: dt.datetime,
    now: dt.datetime | None = None,
) -> None:
    now = now or dt.datetime.now(dt.timezone.utc)
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=dt.timezone.utc)
    requested_at = requested_at.astimezone(dt.timezone.utc)
    if abs(now - requested_at) > _REQUEST_WINDOW:
        raise LeaseError("license lease request is outside the allowed time window")
    try:
        raw = base64.urlsafe_b64decode(nonce + "=" * (-len(nonce) % 4))
    except (ValueError, TypeError) as exc:
        raise LeaseError("license lease nonce is invalid") from exc
    if len(raw) < 24:
        raise LeaseError("license lease nonce is too short")
    with _nonce_lock:
        _seen_nonces.update(_load_nonce_ledger())
        expired = [
            key for key, observed in _seen_nonces.items()
            if now - observed > _NONCE_TTL
        ]
        for key in expired:
            _seen_nonces.pop(key, None)
        if nonce in _seen_nonces:
            raise LeaseError("license lease nonce was already used")
        _seen_nonces[nonce] = now
        _save_nonce_ledger(_seen_nonces)


def read_verified_envelope() -> tuple[str, dict]:
    path_value = (os.environ.get("TIANJUN_LICENSE_PATH") or "").strip()
    machine_id = (os.environ.get("TIANJUN_MACHINE_ID") or "").strip()
    if not path_value or not machine_id:
        raise LeaseError("desktop license context is unavailable")
    path = Path(path_value).resolve()
    if not path.is_file():
        raise LeaseError("Tianjun license file was not found")
    raw = path.read_text(encoding="utf-8")
    try:
        envelope = json.loads(raw)
        data = envelope["data"]
        signature = base64.b64decode(envelope["signature"], validate=True)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LeaseError("Tianjun license envelope is invalid") from exc
    if not isinstance(data, str):
        raise LeaseError("Tianjun license data must be the original JSON string")
    try:
        key = serialization.load_pem_public_key(PUBLIC_KEY)
        key.verify(signature, data.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    except Exception as exc:  # cryptography exposes several backend-specific subclasses
        raise LeaseError("Tianjun license signature is invalid") from exc
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise LeaseError("Tianjun license payload is invalid") from exc
    if payload.get("machineId") != machine_id:
        raise LeaseError("Tianjun license does not match this machine")
    if "version" in payload and payload.get("version") != 1:
        raise LeaseError("Tianjun license version is unsupported")
    if payload.get("product") not in (None, "", "tianjun-ai-vision"):
        raise LeaseError("Tianjun license product is invalid")
    expires = payload.get("expiresAt")
    if expires:
        try:
            parsed = dt.datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
        except ValueError as exc:
            raise LeaseError("Tianjun license expiration is invalid") from exc
        if parsed.astimezone(dt.timezone.utc) <= dt.datetime.now(dt.timezone.utc):
            raise LeaseError("Tianjun license is expired")
    return raw, payload


def reset_nonce_cache_for_tests() -> None:
    with _nonce_lock:
        _seen_nonces.clear()
        try:
            _nonce_ledger_path().unlink()
        except FileNotFoundError:
            pass
