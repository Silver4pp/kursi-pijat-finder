"""Runtime configuration read from environment variables.

Keep this module dependency-free so it can be imported before the app.
"""

import os


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# Set COOKIE_SECURE=1 in production so session cookies are only sent over HTTPS.
# Defaults to off because local development and the test client run over HTTP.
COOKIE_SECURE = _env_bool("COOKIE_SECURE")
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax").strip().lower() or "lax"
SESSION_COOKIE_DAYS = _env_int("SESSION_COOKIE_DAYS", 14)

# Per-IP abuse guards for public form endpoints. Disable with RATE_LIMIT_ENABLED=0.
RATE_LIMIT_ENABLED = _env_bool("RATE_LIMIT_ENABLED", True)
RATE_LIMIT_LOGIN = _env_int("RATE_LIMIT_LOGIN", 10)  # attempts per window
RATE_LIMIT_REGISTER = _env_int("RATE_LIMIT_REGISTER", 5)
RATE_LIMIT_RATING = _env_int("RATE_LIMIT_RATING", 20)
RATE_LIMIT_WINDOW_MINUTES = _env_int("RATE_LIMIT_WINDOW_MINUTES", 15)
