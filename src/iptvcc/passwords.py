"""Password hashing (Argon2id) with a tunable, host-friendly profile."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# 32 MiB / 3 passes keeps a login attempt under ~50 ms on a 2-vCPU host while staying
# well above the OWASP minimum for Argon2id.
_hasher = PasswordHasher(
    time_cost=3, memory_cost=32 * 1024, parallelism=2, hash_len=32, salt_len=16
)

MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True
