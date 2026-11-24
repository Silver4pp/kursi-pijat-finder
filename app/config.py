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


APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

# Demo accounts carry known passwords. They are created only when this flag is
# explicitly enabled, never because a deployment forgot to set APP_ENV.
ALLOW_DEMO_ACCOUNTS = _env_bool("ALLOW_DEMO_ACCOUNTS")

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

# Only trust X-Forwarded-For when the direct peer is a proxy we control;
# otherwise the header is user input and lets clients rotate rate-limit keys.
# Comma-separated list, e.g. TRUSTED_PROXIES="10.0.0.1,10.0.0.2".
TRUSTED_PROXIES = {ip.strip() for ip in os.getenv("TRUSTED_PROXIES", "").split(",") if ip.strip()}

# Staged proposal photos that were never reviewed are deleted after this many days.
STAGED_PHOTO_TTL_DAYS = _env_int("STAGED_PHOTO_TTL_DAYS", 30)

# Level for the application logger. Uvicorn configures only its own loggers, so the
# app has to install its own handler or logger.info() is dropped on the floor.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"

# Bind address for `python app/main.py`. Uvicorn started from the CLI takes its
# own --host/--port, so these only apply to the direct-entry path.
HOST = os.getenv("HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = _env_int("PORT", 8000)

_VALID_SAMESITE = {"lax", "strict", "none"}
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def validate_runtime_config() -> list[str]:
    """Return blocking configuration problems. An empty list means safe to start.

    Called once at startup so a misconfigured deployment fails loudly instead of
    running with insecure defaults.
    """
    problems: list[str] = []

    if SESSION_COOKIE_DAYS <= 0:
        problems.append("SESSION_COOKIE_DAYS must be greater than 0.")
    if RATE_LIMIT_WINDOW_MINUTES <= 0:
        problems.append("RATE_LIMIT_WINDOW_MINUTES must be greater than 0.")
    if any(value < 0 for value in (RATE_LIMIT_LOGIN, RATE_LIMIT_REGISTER, RATE_LIMIT_RATING)):
        problems.append("RATE_LIMIT_* values must not be negative.")
    if COOKIE_SAMESITE not in _VALID_SAMESITE:
        problems.append(
            f"COOKIE_SAMESITE must be one of {sorted(_VALID_SAMESITE)}, got {COOKIE_SAMESITE!r}."
        )
    if COOKIE_SAMESITE == "none" and not COOKIE_SECURE:
        problems.append(
            "COOKIE_SAMESITE=none requires COOKIE_SECURE=1; browsers reject the cookie otherwise."
        )
    if STAGED_PHOTO_TTL_DAYS <= 0:
        problems.append("STAGED_PHOTO_TTL_DAYS must be greater than 0.")
    if not 1 <= PORT <= 65535:
        problems.append(f"PORT must be between 1 and 65535, got {PORT}.")
    if LOG_LEVEL not in _VALID_LOG_LEVELS:
        problems.append(f"LOG_LEVEL must be one of {sorted(_VALID_LOG_LEVELS)}, got {LOG_LEVEL!r}.")

    if IS_PRODUCTION:
        if not COOKIE_SECURE:
            problems.append(
                "COOKIE_SECURE must be 1 in production: session cookies would otherwise "
                "travel over plain HTTP."
            )
        if not os.getenv("DATABASE_URL"):
            problems.append(
                "DATABASE_URL must be set in production; the default is a local SQLite file."
            )
        if not RATE_LIMIT_ENABLED:
            problems.append(
                "RATE_LIMIT_ENABLED must stay on in production (it is the brute-force guard)."
            )
        if ALLOW_DEMO_ACCOUNTS:
            problems.append(
                "ALLOW_DEMO_ACCOUNTS=1 seeds admin@example.com with a known password. "
                "Remove it before going live."
            )

    # A non-SQLite database strongly implies a real deployment. Refuse to run it
    # with development defaults unless production mode is declared explicitly.
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not IS_PRODUCTION and database_url and not database_url.startswith("sqlite"):
        problems.append(
            "APP_ENV is not 'production' but DATABASE_URL is not SQLite. "
            "Set APP_ENV=production so the production guardrails apply."
        )

    return problems
