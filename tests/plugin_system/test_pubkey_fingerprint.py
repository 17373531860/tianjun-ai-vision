"""公钥 fingerprint = SHA256(SubjectPublicKeyInfo DER)[:16]."""
import pytest

from _plugin_common import calc_pubkey_fingerprint


def test_fingerprint_length(rsa_keypair):
    _, pub_pem = rsa_keypair
    fp = calc_pubkey_fingerprint(pub_pem)
    assert len(fp) == 16


def test_fingerprint_deterministic(rsa_keypair):
    _, pub_pem = rsa_keypair
    fp1 = calc_pubkey_fingerprint(pub_pem)
    fp2 = calc_pubkey_fingerprint(pub_pem)
    assert fp1 == fp2


def test_fingerprint_different_for_different_keys():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    fp_set = set()
    for _ in range(3):
        pri = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = pri.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        fp_set.add(calc_pubkey_fingerprint(pem))
    assert len(fp_set) == 3


def test_fingerprint_pem_whitespace_invariant(rsa_keypair):
    """PEM 多余空白行不应影响 fingerprint (DER 编码相同)."""
    _, pub_pem = rsa_keypair
    pub_with_extra = pub_pem + b"\n\n\n"
    fp1 = calc_pubkey_fingerprint(pub_pem)
    fp2 = calc_pubkey_fingerprint(pub_with_extra)
    assert fp1 == fp2
