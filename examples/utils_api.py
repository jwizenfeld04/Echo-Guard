"""API utilities — module B. Represents code that an AI agent wrote separately."""

import hashlib
import os
import re


def create_password_hash(pwd: str, salt_value: str | None = None) -> tuple[str, str]:
    """Create a hashed version of the password with PBKDF2."""
    if salt_value is None:
        salt_value = os.urandom(32).hex()
    result = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt_value.encode(), 100000)
    return result.hex(), salt_value


def check_password(pwd: str, stored_hash: str, salt_value: str) -> bool:
    """Check if a password matches the stored hash."""
    computed, _ = create_password_hash(pwd, salt_value)
    return computed == stored_hash


def make_random_token(size: int = 32) -> str:
    """Create a random hex token."""
    return os.urandom(size).hex()


def is_valid_email(addr: str) -> bool:
    """Validate an email address format."""
    email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(email_regex, addr))


def clean_username(name: str) -> str:
    """Strip invalid characters from username string."""
    return re.sub(r"[^a-zA-Z0-9_.-]", "", name)


def format_api_response(data: dict, status: int = 200) -> dict:
    """Format a standard API response envelope."""
    return {
        "status": status,
        "data": data,
        "ok": 200 <= status < 300,
    }
