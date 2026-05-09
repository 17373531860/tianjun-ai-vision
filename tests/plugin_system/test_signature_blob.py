"""signature.bin 序列化/反序列化必须 roundtrip 一致。"""
import pytest

from _plugin_common import (
    SIG_MAGIC,
    SIG_VERSION,
    CryptoError,
    SignatureBlob,
    parse_signature_blob,
    write_signature_blob,
)


def _make_blob(rsa_sig=b"X" * 256, meta=b'{"alg":"x"}'):
    return SignatureBlob(
        magic=SIG_MAGIC,
        version=SIG_VERSION,
        public_key_fingerprint=bytes(range(16)),
        customer_hmac=bytes(range(32)),
        rsa_sig_len=len(rsa_sig),
        rsa_signature=rsa_sig,
        sig_metadata_len=len(meta),
        sig_metadata=meta,
    )


def test_roundtrip():
    blob = _make_blob()
    data = write_signature_blob(blob)
    parsed = parse_signature_blob(data)
    assert parsed.magic == SIG_MAGIC
    assert parsed.version == SIG_VERSION
    assert parsed.public_key_fingerprint == bytes(range(16))
    assert parsed.customer_hmac == bytes(range(32))
    assert parsed.rsa_signature == b"X" * 256
    assert parsed.sig_metadata == b'{"alg":"x"}'


def test_layout_byte_count():
    """signature.bin 大小 = 4 magic + 1 ver + 16 fp + 32 hmac + 2 rsa_len + N rsa + 2 meta_len + M meta."""
    blob = _make_blob(rsa_sig=b"X" * 512, meta=b"y" * 100)
    data = write_signature_blob(blob)
    expected = 4 + 1 + 16 + 32 + 2 + 512 + 2 + 100
    assert len(data) == expected


def test_bad_magic_raises():
    blob = _make_blob()
    blob.magic = b"XXXX"
    with pytest.raises(CryptoError):
        write_signature_blob(blob)


def test_parse_truncated_raises():
    blob = _make_blob()
    data = write_signature_blob(blob)
    with pytest.raises(CryptoError):
        parse_signature_blob(data[:30])


def test_parse_wrong_magic_raises():
    blob = _make_blob()
    data = bytearray(write_signature_blob(blob))
    data[0] = ord("Z")
    with pytest.raises(CryptoError):
        parse_signature_blob(bytes(data))


def test_parse_unsupported_version_raises():
    blob = _make_blob()
    data = bytearray(write_signature_blob(blob))
    data[4] = 99  # version
    with pytest.raises(CryptoError):
        parse_signature_blob(bytes(data))


def test_fingerprint_must_be_16_byte():
    blob = _make_blob()
    blob.public_key_fingerprint = b"X" * 15
    with pytest.raises(CryptoError):
        write_signature_blob(blob)


def test_hmac_must_be_32_byte():
    blob = _make_blob()
    blob.customer_hmac = b"X" * 30
    with pytest.raises(CryptoError):
        write_signature_blob(blob)


def test_empty_metadata_ok():
    blob = _make_blob(meta=b"")
    data = write_signature_blob(blob)
    parsed = parse_signature_blob(data)
    assert parsed.sig_metadata == b""
    assert parsed.sig_metadata_len == 0
