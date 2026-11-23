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

from . import models, services  # noqa: F401  (kept so `python app/main.py` warms up imports)
from .db import Base, SessionLocal, engine, run_migrations
from .routes import admin, auth, internal, public, ratings
from .seed import seed_database

logger = logging.getLogger("kpf")

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    run_migrations()
    db = SessionLocal()
    try:
        seed_database(db)
        removed = services.purge_expired_auth_records(db)
        if removed:
            logger.info("Pembersihan awal menghapus %d sesi/subjek anonim kedaluwarsa", removed)
    finally:
        db.close()
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
    return response

app.include_router(public.router)
app.include_router(ratings.router)
app.include_router(auth.router)
app.include_router(internal.router)
app.include_router(admin.router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
