"""customer_hmac = HMAC-SHA256(plugin_secret, customer_code|files_digest)."""
import pytest

from _plugin_common import (
    CryptoError,
    calc_customer_hmac,
    verify_customer_hmac,
)


def test_hmac_length(plugin_secret):
    h = calc_customer_hmac("acme", "sha256:" + "0" * 64, plugin_secret)
    assert len(h) == 32


def test_hmac_deterministic(plugin_secret):
    digest = "sha256:" + "f" * 64
    h1 = calc_customer_hmac("acme", digest, plugin_secret)
    h2 = calc_customer_hmac("acme", digest, plugin_secret)
    assert h1 == h2


def test_hmac_different_customer(plugin_secret):
    digest = "sha256:" + "0" * 64
    a = calc_customer_hmac("acme", digest, plugin_secret)
    b = calc_customer_hmac("acme-2", digest, plugin_secret)
    assert a != b


def test_hmac_different_digest(plugin_secret):
    a = calc_customer_hmac("acme", "sha256:" + "0" * 64, plugin_secret)
    b = calc_customer_hmac("acme", "sha256:" + "1" * 64, plugin_secret)
    assert a != b


def test_hmac_different_secret():
    secret_a = bytes(range(32))
    secret_b = bytes(reversed(range(32)))
    digest = "sha256:" + "0" * 64
    a = calc_customer_hmac("acme", digest, secret_a)
    b = calc_customer_hmac("acme", digest, secret_b)
    assert a != b


def test_hmac_secret_must_be_32_bytes():
    digest = "sha256:" + "0" * 64
    with pytest.raises(CryptoError):
        calc_customer_hmac("acme", digest, b"\x00" * 16)
    with pytest.raises(CryptoError):
        calc_customer_hmac("acme", digest, b"\x00" * 64)


def test_verify_roundtrip(plugin_secret):
    digest = "sha256:" + "0" * 64
    h = calc_customer_hmac("acme", digest, plugin_secret)
    assert verify_customer_hmac("acme", digest, plugin_secret, h)


def test_verify_rejects_tampered(plugin_secret):
    digest = "sha256:" + "0" * 64
    h = calc_customer_hmac("acme", digest, plugin_secret)
    tampered = bytes([h[0] ^ 1]) + h[1:]
    assert not verify_customer_hmac("acme", digest, plugin_secret, tampered)


def test_verify_rejects_wrong_customer(plugin_secret):
    digest = "sha256:" + "0" * 64
    h = calc_customer_hmac("acme", digest, plugin_secret)
    assert not verify_customer_hmac("hostile", digest, plugin_secret, h)
