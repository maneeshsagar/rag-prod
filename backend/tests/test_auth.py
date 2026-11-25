"""Password hashing and login tokens."""

from app.core.auth import create_token, hash_password, read_token, verify_password


def test_password_hash_roundtrip():
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$")
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)


def test_token_roundtrip_and_tampering():
    token = create_token("user-1")
    assert read_token(token) == "user-1"
    payload, signature = token.split(".")
    assert read_token(f"{payload}.{'0' * len(signature)}") is None
    assert read_token("garbage") is None
