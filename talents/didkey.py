"""Minimal did:key (Ed25519) and base58btc, so callouts verify with nothing but this file."""
from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_MC = b"\xed\x01"


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def b58decode(s: str) -> bytes:
    n = 0
    for c in s:
        n = n * 58 + _B58.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + raw


def did_of(pub: bytes) -> str:
    return "did:key:z" + b58encode(_MC + pub)


def pub_of(did: str) -> bytes:
    if not did.startswith("did:key:z"):
        raise ValueError("not a did:key")
    raw = b58decode(did[len("did:key:z"):])
    if raw[:2] != _MC or len(raw) != 34:
        raise ValueError("not an Ed25519 did:key")
    return raw[2:]


def new_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def key_from_seed(seed: bytes) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(seed)


def did_of_key(k: Ed25519PrivateKey) -> str:
    return did_of(k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def verify(did: str, sig: bytes, msg: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(pub_of(did)).verify(sig, msg)
        return True
    except Exception:  # noqa: BLE001
        return False
