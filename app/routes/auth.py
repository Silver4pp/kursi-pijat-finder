"""Authentication: login, registration, logout."""

import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import config, models
from ..db import get_db
from ..security import hash_password, new_token, session_expiry, token_hash, verify_password
from ..web import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    check_csrf,
    client_ip,
    get_current_user,
    login_limiter,
    redirect_with_flash,
    register_limiter,
    render,
    set_cookie,
)

router = APIRouter()

_dummy_hash: str | None = None


def _verify_dummy_password(password: str) -> None:
    """Equalise login timing between existing and missing accounts."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("timing-equaliser")
    verify_password(password, _dummy_hash)


@router.get("/masuk")
def login_page(request: Request, db: Session = Depends(get_db)):
    if get_current_user(request, db):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", {}, db)


@router.post("/masuk")
async def login(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    check_csrf(request, str(form.get("csrf_token") or ""))
    if config.RATE_LIMIT_ENABLED and not login_limiter.allow(f"login:{client_ip(request)}"):
        return render(
            request,
            "login.html",
            {"error": "Terlalu banyak percobaan masuk. Coba lagi dalam beberapa menit."},
            db,
            429,
        )
    email = str(form.get("email") or "").strip().lower()
    password = str(form.get("password") or "")
    # Registration stores lowercased emails, so a direct comparison keeps the
    # unique index usable.
    user = db.scalar(select(models.User).where(models.User.email == email))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        # Burn the same PBKDF2 work for unknown emails so response time does not
        # reveal which addresses are registered.
        _verify_dummy_password(password)
        return render(request, "login.html", {"error": "Email atau kata sandi tidak cocok."}, db, 400)
    raw = new_token()
    db.add(models.Session(user_id=user.id, token_hash=token_hash(raw), expires_at=session_expiry(config.SESSION_COOKIE_DAYS)))
    db.commit()
    response = redirect_with_flash("/", f"Selamat datang, {user.name}.")
    set_cookie(response, SESSION_COOKIE, raw, max_age=60 * 60 * 24 * config.SESSION_COOKIE_DAYS)
    # Rotate the CSRF token so any token issued before authentication expires.
    set_cookie(response, CSRF_COOKIE, new_token(), max_age=60 * 60 * 24 * config.SESSION_COOKIE_DAYS)
    return response


@router.get("/daftar")
def register_page(request: Request, db: Session = Depends(get_db)):
    return render(request, "register.html", {}, db)


@router.post("/daftar")
async def register(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    check_csrf(request, str(form.get("csrf_token") or ""))
    if config.RATE_LIMIT_ENABLED and not register_limiter.allow(f"register:{client_ip(request)}"):
        return render(
            request,
            "register.html",
            {"error": "Terlalu banyak pendaftaran dari perangkat ini. Coba lagi nanti."},
            db,
            429,
        )
    name = str(form.get("name") or "").strip()
    email = str(form.get("email") or "").strip().lower()
    password = str(form.get("password") or "")
    confirm = str(form.get("confirm") or "")
    if len(name) < 2 or "@" not in email or len(password) < 8:
        return render(
            request,
            "register.html",
            {"error": "Nama wajib diisi, email harus valid, dan kata sandi minimal 8 karakter."},
            db,
            400,
        )
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return render(
            request,
            "register.html",
            {"error": "Kata sandi harus mengandung huruf dan angka."},
            db,
            400,
        )
    if password != confirm:
        return render(request, "register.html", {"error": "Konfirmasi kata sandi tidak sama."}, db, 400)
    if db.scalar(select(models.User).where(models.User.email == email)):
        return render(request, "register.html", {"error": "Email sudah digunakan."}, db, 400)
    user = models.User(name=name, email=email, password_hash=hash_password(password), role="public")
    db.add(user)
    db.flush()
    raw = new_token()
    db.add(models.Session(user_id=user.id, token_hash=token_hash(raw), expires_at=session_expiry(config.SESSION_COOKIE_DAYS)))
    try:
        db.commit()
    except IntegrityError:
        # Two registrations raced the duplicate check; the unique index kept one.
        db.rollback()
        return render(request, "register.html", {"error": "Email sudah digunakan."}, db, 400)
    response = redirect_with_flash("/", "Akun berhasil dibuat.")
    set_cookie(response, SESSION_COOKIE, raw, max_age=60 * 60 * 24 * config.SESSION_COOKIE_DAYS)
    set_cookie(response, CSRF_COOKIE, new_token(), max_age=60 * 60 * 24 * config.SESSION_COOKIE_DAYS)
    return response


@router.post("/keluar")
async def logout(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    check_csrf(request, str(form.get("csrf_token") or ""))
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        session = db.scalar(select(models.Session).where(models.Session.token_hash == token_hash(raw)))
        if session:
            db.delete(session)
            db.commit()
    response = redirect_with_flash("/", "Anda sudah keluar.")
    response.delete_cookie(SESSION_COOKIE)
    return response
