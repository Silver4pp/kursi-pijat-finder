"""Shared web-layer helpers: constants, cookies, CSRF, flash, rendering, uploads."""

import logging
import secrets
from datetime import UTC, datetime
from io import BytesIO
from math import ceil
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, models, services
from .security import SlidingWindowLimiter, new_token, token_hash
from .uploads import upload_path

BASE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

SESSION_COOKIE = "kpf_session"
CSRF_COOKIE = "kpf_csrf"
ANON_COOKIE = "kpf_anon"
FLASH_COOKIE = "kpf_flash"

CITIES = ["Jakarta Pusat", "Jakarta Selatan", "Jakarta Barat", "Jakarta Timur", "Jakarta Utara"]
CATEGORIES = {
    "mall": "Mall",
    "stasiun": "Stasiun",
    "bandara": "Bandara",
    "rumah_sakit": "Rumah sakit",
    "perkantoran": "Perkantoran",
    "apartemen": "Apartemen",
    "hotel": "Hotel",
    "kebugaran": "Kebugaran",
    "lainnya": "Lainnya",
}
CHANGE_KIND_LABELS = {"create_location": "Lokasi baru", "spot_update": "Perubahan titik"}
REQUEST_STATUS_LABELS = {
    "submitted": "Menunggu pemeriksaan",
    "returned": "Dikembalikan",
    "rejected": "Ditolak",
    "approved": "Disetujui",
}
EXISTENCE_STATUSES = {"ada", "tidak_ada"}

MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_PHOTO_EDGE = 2560

login_limiter = SlidingWindowLimiter(config.RATE_LIMIT_LOGIN, config.RATE_LIMIT_WINDOW_MINUTES * 60)
register_limiter = SlidingWindowLimiter(config.RATE_LIMIT_REGISTER, config.RATE_LIMIT_WINDOW_MINUTES * 60)
rating_limiter = SlidingWindowLimiter(config.RATE_LIMIT_RATING, config.RATE_LIMIT_WINDOW_MINUTES * 60)


def set_cookie(response, key: str, value: str, *, max_age: int | None = None, httponly: bool = True):
    response.set_cookie(
        key,
        value,
        max_age=max_age,
        httponly=httponly,
        samesite=config.COOKIE_SAMESITE,
        secure=config.COOKIE_SECURE,
    )


def client_ip(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    if host in config.TRUSTED_PROXIES:
        hops = [
            hop.strip()
            for hop in request.headers.get("x-forwarded-for", "").split(",")
            if hop.strip()
        ]
        if hops:
            return hops[0]
    return host


def get_current_user(request: Request, db: Session):
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    session = db.scalar(
        select(models.Session).where(models.Session.token_hash == token_hash(raw))
    )
    if not session:
        return None
    expires = session.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires < datetime.now(UTC):
        db.delete(session)
        db.commit()
        return None
    if not session.user.is_active:
        return None
    return session.user


def get_anon_subject(request: Request, db: Session, create: bool = False):
    raw = request.cookies.get(ANON_COOKIE)
    subject = None
    if raw:
        subject = db.scalar(
            select(models.AnonymousSubject).where(
                models.AnonymousSubject.token_hash == token_hash(raw)
            )
        )
    if subject:
        # Only touch last_seen_at on paths that commit afterwards; on plain GET
        # renders the change would be discarded when the session closes.
        if create:
            subject.last_seen_at = datetime.now(UTC)
        return subject, None
    if not create:
        return None, None
    raw = new_token()
    subject = models.AnonymousSubject(token_hash=token_hash(raw))
    db.add(subject)
    db.flush()
    return subject, raw


def render(request: Request, template: str, context: dict, db: Session, status_code: int = 200):
    csrf = request.cookies.get(CSRF_COOKIE) or new_token()
    flash = request.cookies.get(FLASH_COOKIE)
    full_context = {
        "current_user": get_current_user(request, db),
        "csrf_token": csrf,
        "flash": unquote(flash) if flash else None,
        "payment_labels": services.PAYMENT_LABELS,
        "cost_labels": services.COST_LABELS,
        "method_labels": services.METHOD_LABELS,
        "categories": CATEGORIES,
        "cities": CITIES,
        "format_rupiah": services.format_rupiah,
        "format_decimal": services.format_decimal_id,
    }
    full_context.update(context)
    response = templates.TemplateResponse(
        request=request, name=template, context=full_context, status_code=status_code
    )
    set_cookie(response, CSRF_COOKIE, csrf, max_age=60 * 60 * 24 * 14)
    if flash:
        response.delete_cookie(FLASH_COOKIE)
    return response


def redirect_with_flash(url: str, message: str, status_code: int = 303):
    response = RedirectResponse(url=url, status_code=status_code)
    set_cookie(response, FLASH_COOKIE, quote(message), max_age=30)
    return response


def check_csrf(request: Request, supplied: str | None):
    expected = request.cookies.get(CSRF_COOKIE)
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="Token formulir tidak valid. Muat ulang halaman.")


def require_user(request: Request, db: Session, roles=None):
    user = get_current_user(request, db)
    if user is None:
        return None, redirect_with_flash("/masuk", "Silakan masuk terlebih dahulu.")
    if roles and user.role not in roles:
        return None, redirect_with_flash("/", "Akun Anda tidak mempunyai hak untuk membuka halaman itu.")
    return user, None


def save_staged_photo(upload: UploadFile | None):
    if not upload or not upload.filename:
        return None
    if upload.size is not None and upload.size > MAX_PHOTO_BYTES:
        raise HTTPException(400, "Foto tidak boleh lebih dari 5 MB")
    data = upload.file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(400, "Foto tidak boleh lebih dari 5 MB")
    try:
        image = Image.open(BytesIO(data))
        # Refuse decompression bombs before Pillow decodes the whole image.
        if image.width * image.height > 50_000_000:
            raise HTTPException(400, "Dimensi foto terlalu besar")
        image.load()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, "Berkas yang diunggah harus berupa gambar") from exc
    fmt = (image.format or "").upper()
    if fmt not in {"JPEG", "PNG", "WEBP"}:
        raise HTTPException(400, "Format foto yang diterima adalah JPG, PNG, atau WebP")
    # Re-encode so EXIF (which can contain GPS data) and any embedded payload
    # are stripped, and bound the stored size by downscaling very large photos.
    if max(image.width, image.height) > MAX_PHOTO_EDGE:
        image.thumbnail((MAX_PHOTO_EDGE, MAX_PHOTO_EDGE))
    try:
        out = BytesIO()
        if fmt == "JPEG":
            image.convert("RGB").save(out, format="JPEG", quality=85, optimize=True)
            ext = "jpg"
        else:
            image.save(out, format=fmt)
            ext = fmt.lower()
    except Exception as exc:
        raise HTTPException(400, "Foto tidak dapat diproses") from exc
    name = f"staged-{secrets.token_hex(16)}.{ext}"
    upload_path(name).write_bytes(out.getvalue())
    return name


def discard_staged_photo(change_request: models.ChangeRequest):
    """Remove the staged upload of a change request that will not be applied."""
    name = change_request.staged_photo_name
    if not name:
        return
    try:
        upload_path(name).unlink(missing_ok=True)
    except HTTPException:
        # An unsafe name never pointed at a real upload; drop the reference.
        pass
    except OSError:
        logging.getLogger("kpf").warning("Gagal menghapus foto staged %s", name, exc_info=True)
    change_request.staged_photo_name = None


def paginate(items: list, page_param, per_page: int = 10):
    """Slice an already-filtered list. Returns (page_items, pager_context)."""
    try:
        page = max(1, int(page_param))
    except (TypeError, ValueError):
        page = 1
    total = len(items)
    pages = max(1, ceil(total / per_page))
    page = min(page, pages)
    start = (page - 1) * per_page
    return items[start : start + per_page], {
        "page": page,
        "pages": pages,
        "total": total,
        "per_page": per_page,
    }

