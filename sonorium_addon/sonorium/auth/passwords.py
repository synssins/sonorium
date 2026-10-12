"""
Password hashes: Argon2id (argon2-cffi, RFC 9106).

Why Argon2id: it is memory-hard (64 MiB per guess with the library defaults),
so guessing passwords from a stolen auth.json on a GPU is slow and costly;
it is OWASP's first choice and the winner of the Password Hashing
Competition. argon2-cffi installs from wheels on Debian (python:3.12-slim)
and Alpine (the add-on's base image), on amd64 and aarch64.

Hashing takes ~50-150 ms; the web routes run it in a worker thread.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # Argon2id, time cost 3, 64 MiB, 4 lanes
_dummy: str | None = None

MIN_LENGTH = 8
MAX_LENGTH = 256


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored: str | None, password: str) -> bool:
    """Whether password matches the stored hash. No hash: still spends the same time."""
    global _dummy
    if not stored:
        if _dummy is None:
            _dummy = _hasher.hash("sonorium-no-such-user")
        stored, password = _dummy, password + "\0x"
    try:
        return _hasher.verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored)
    except Exception:
        return False


def password_problem(password) -> str | None:
    """Why a new password can't be used, or None."""
    if not isinstance(password, str) or len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return "That password is too long."
    return None
