"""Auth utilities — module A. This represents existing code in a repo."""

import hashlib
import hmac
import os


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Hash a password with a salt using SHA-256."""
    if salt is None:
        salt = os.urandom(32).hex()
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return hashed.hex(), salt


def verify_password(password: str, hashed: str, salt: str) -> bool:
    """Verify a password against a stored hash."""
    computed, _ = hash_password(password, salt)
    return hmac.compare_digest(computed, hashed)


def generate_token(length: int = 32) -> str:
    """Generate a random token for session management."""
    return os.urandom(length).hex()


def validate_email(email: str) -> bool:
    """Check if an email address is roughly valid."""
    import re
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


def sanitize_username(username: str) -> str:
    """Remove dangerous characters from a username."""
    import re
    return re.sub(r"[^a-zA-Z0-9_.-]", "", username)
