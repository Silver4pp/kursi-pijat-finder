import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Support both `python app/main.py` and importing the app through Uvicorn.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "app"

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config, models, services  # noqa: F401  (kept so `python app/main.py` warms up imports)
from .db import Base, SessionLocal, engine, ensure_data_dirs, run_migrations
from .routes import admin, auth, internal, public, ratings
from .seed import seed_database

logger = logging.getLogger("kpf")

BASE_DIR = Path(__file__).resolve().parent

# Pages that render account or moderation data must never be cached.
NO_STORE_PREFIXES = ("/admin", "/internal", "/akun", "/masuk", "/daftar")


def configure_logging() -> None:
    """Install a handler for the app's own logger.

    Uvicorn only configures its own loggers. Without this, logger.info() in the
    app is dropped (root defaults to WARNING) and logger.exception() prints a bare
    traceback with no timestamp or level.
    """
    if logging.getLogger().handlers:
        return  # A server (gunicorn, a test runner) already owns logging.
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Validate first: an invalid LOG_LEVEL would otherwise crash inside basicConfig
    # before the guard could report it in a readable list.
    problems = config.validate_runtime_config()
    if problems:
        raise RuntimeError("Konfigurasi tidak valid:\n- " + "\n- ".join(problems))
    configure_logging()

    ensure_data_dirs()
    Base.metadata.create_all(bind=engine)
    run_migrations()
    db = SessionLocal()
    try:
        seed_database(db)
        removed = services.purge_expired_auth_records(db)
        if removed:
            logger.info("Pembersihan awal menghapus %d sesi/subjek anonim kedaluwarsa", removed)
        stale_photos = services.purge_stale_staged_photos(db, config.STAGED_PHOTO_TTL_DAYS)
        if stale_photos:
            logger.info(
                "Pembersihan awal menghapus %d foto usulan yang tidak diproses", stale_photos
            )
    finally:
        db.close()

    if config.IS_PRODUCTION and not config.TRUSTED_PROXIES:
        logger.warning(
            "TRUSTED_PROXIES kosong: bila aplikasi berada di belakang reverse proxy, "
            "semua pengunjung berbagi satu bucket rate limit."
        )
    yield


app = FastAPI(
    title="Kursi Pijat Finder Jakarta - Lokal",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.middleware("http")
async def security_headers(request, call_next):
    """Baseline hardening headers for every response."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(self), camera=(), microphone=()"
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; "
        "style-src 'self'; script-src 'self'; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    if request.url.path.startswith(NO_STORE_PREFIXES):
        response.headers.setdefault("Cache-Control", "no-store, private")
    return response


app.include_router(public.router)
app.include_router(ratings.router)
app.include_router(auth.router)
app.include_router(internal.router)
app.include_router(admin.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=config.HOST,
        port=config.PORT,
        log_level=config.LOG_LEVEL.lower(),
    )
