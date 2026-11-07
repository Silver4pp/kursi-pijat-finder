import json
import secrets
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote

# Support both `python app/main.py` and importing the app through Uvicorn.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "app"

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import models, services
from .db import UPLOAD_DIR, Base, SessionLocal, engine, get_db
from .security import hash_password, new_token, session_expiry, token_hash, verify_password
from .seed import seed_database

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_database(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title="Kursi Pijat Finder Jakarta - Lokal",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
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
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
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
        subject.last_seen_at = datetime.now(timezone.utc)
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
    response.set_cookie(CSRF_COOKIE, csrf, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 14)
    if flash:
        response.delete_cookie(FLASH_COOKIE)
    return response


def redirect_with_flash(url: str, message: str, status_code: int = 303):
    response = RedirectResponse(url=url, status_code=status_code)
    response.set_cookie(FLASH_COOKIE, quote(message), max_age=30, httponly=True, samesite="lax")
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


def active_placements(spot: models.Spot):
    return [p for p in spot.placements if p.is_published and p.effective_to is None]


def serialize_placement(placement: models.Placement):
    return {
        "id": placement.id,
        "brand": placement.brand.name if placement.brand else "Merk belum terkonfirmasi",
        "type": placement.chair_type.name if placement.chair_type else "Tipe belum terkonfirmasi",
        "operator": placement.operator.name if placement.operator else None,
        "quantity": placement.quantity,
        "price": services.format_rupiah(placement.price_idr),
        "duration": placement.duration_minutes,
        "payments": [services.PAYMENT_LABELS.get(x, x) for x in placement.payment_codes],
        "can_rate": placement.can_rate,
        "cost_label": services.COST_LABELS.get(placement.cost_type, placement.cost_type),
    }


def serialize_spot(spot: models.Spot):
    return {
        "id": spot.id,
        "name": spot.name,
        "slug": spot.slug,
        "venue_slug": spot.venue.slug,
        "floor": spot.floor,
        "landmark": spot.zone_landmark,
        "status": spot.existence_status,
        "status_label": "Masih Ada" if spot.existence_status == "ada" else "Sudah Tidak Ada",
        "access": spot.access_labels,
        "last_verified": spot.last_verified_at.isoformat() if spot.last_verified_at else None,
        "placements": [serialize_placement(p) for p in active_placements(spot)],
    }


def filter_spots(db: Session, params: dict):
    spots = list(
        db.scalars(
            select(models.Spot)
            .join(models.Venue)
            .where(models.Spot.is_published.is_(True), models.Venue.is_published.is_(True))
            .options(
                selectinload(models.Spot.venue),
                selectinload(models.Spot.placements),
                selectinload(models.Spot.access_groups),
            )
        ).unique().all()
    )
    q = (params.get("q") or "").strip().lower()
    access_filter = params.get("access") or ""
    include_unknown_qty = params.get("include_unknown_qty") in {"1", "on", "true", True}
    min_qty_raw = params.get("min_qty")
    try:
        min_qty = int(min_qty_raw) if min_qty_raw not in (None, "") else None
    except ValueError:
        min_qty = None

    matched = []
    hidden_unknown_qty = 0
    for spot in spots:
        if spot.existence_status != "ada":
            continue
        placements = active_placements(spot)
        searchable = " ".join(
            filter(
                None,
                [
                    spot.venue.name,
                    spot.venue.address,
                    spot.venue.admin_city,
                    spot.venue.district or "",
                    spot.name,
                    spot.floor,
                    spot.zone_landmark,
                    " ".join((p.brand.name if p.brand else "") for p in placements),
                    " ".join((p.chair_type.name if p.chair_type else "") for p in placements),
                    " ".join((p.operator.name if p.operator else "") for p in placements),
                ],
            )
        ).lower()
        if q and q not in searchable:
            continue
        if params.get("city") and spot.venue.admin_city != params["city"]:
            continue
        if params.get("category") and spot.venue.category != params["category"]:
            continue
        if params.get("brand_id") and not any(p.brand_id == params["brand_id"] for p in placements):
            continue
        if access_filter and access_filter not in spot.access_codes:
            continue
        if params.get("payment") and not any(params["payment"] in p.payment_codes for p in placements):
            continue
        searched_venue_directly = bool(q and q in spot.venue.name.lower())
        if spot.is_private_only and not access_filter and not searched_venue_directly:
            continue
        if min_qty is not None:
            known = [p.quantity for p in placements if p.quantity is not None]
            if known and sum(known) >= min_qty:
                pass
            elif not known and include_unknown_qty:
                pass
            else:
                if not known:
                    hidden_unknown_qty += 1
                continue
        matched.append(spot)

    lat = params.get("lat")
    lng = params.get("lng")
    distances = {}
    if lat not in (None, "") and lng not in (None, ""):
        try:
            user_lat, user_lng = float(lat), float(lng)
            for spot in matched:
                distances[spot.venue_id] = services.haversine_km(
                    user_lat, user_lng, spot.venue.latitude, spot.venue.longitude
                )
            matched.sort(key=lambda s: distances.get(s.venue_id, 9999))
        except ValueError:
            distances = {}
    return matched, hidden_unknown_qty, distances


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    params = dict(request.query_params)
    spots, hidden_unknown, distances = filter_spots(db, params)
    venues_by_id = {}
    for spot in spots:
        item = venues_by_id.setdefault(
            spot.venue_id,
            {"venue": spot.venue, "spots": [], "distance": distances.get(spot.venue_id)},
        )
        item["spots"].append(spot)
    brands = list(db.scalars(select(models.Brand).order_by(models.Brand.name)).all())
    access_groups = list(db.scalars(select(models.AccessGroup).order_by(models.AccessGroup.label)).all())
    return render(
        request,
        "index.html",
        {
            "venue_items": list(venues_by_id.values()),
            "spots": spots,
            "brands": brands,
            "access_groups": access_groups,
            "params": params,
            "hidden_unknown_qty": hidden_unknown,
        },
        db,
    )


@app.get("/api/v1/venues")
def api_venues(request: Request, db: Session = Depends(get_db)):
    params = dict(request.query_params)
    spots, hidden_unknown, distances = filter_spots(db, params)
    venues = {}
    for spot in spots:
        venue = spot.venue
        entry = venues.setdefault(
            venue.id,
            {
                "id": venue.id,
                "name": venue.name,
                "slug": venue.slug,
                "category": venue.category,
                "category_label": CATEGORIES.get(venue.category, venue.category),
                "city": venue.admin_city,
                "lat": venue.latitude,
                "lng": venue.longitude,
                "spot_count": 0,
                "distance_km": distances.get(venue.id),
            },
        )
        entry["spot_count"] += 1
    return {"venues": list(venues.values()), "hidden_unknown_quantity_spots": hidden_unknown}


@app.get("/api/v1/venues/{venue_id}/spots")
def api_venue_spots(venue_id: str, request: Request, db: Session = Depends(get_db)):
    venue = db.get(models.Venue, venue_id)
    if not venue:
        raise HTTPException(404, "Venue tidak ditemukan")
    params = dict(request.query_params)
    spots, _, _ = filter_spots(db, params)
    venue_spots = [spot for spot in spots if spot.venue_id == venue.id]
    return {
        "venue": {"id": venue.id, "name": venue.name, "slug": venue.slug},
        "spots": [serialize_spot(spot) for spot in venue_spots],
    }


@app.get("/venue/{venue_slug}")
def venue_detail(venue_slug: str, request: Request, db: Session = Depends(get_db)):
    venue = db.scalar(select(models.Venue).where(models.Venue.slug == venue_slug))
    if not venue or not venue.is_published:
        raise HTTPException(404, "Venue tidak ditemukan")
    return render(request, "venue.html", {"venue": venue}, db)


@app.get("/venue/{venue_slug}/titik/{spot_slug}")
def spot_detail(venue_slug: str, spot_slug: str, request: Request, db: Session = Depends(get_db)):
    spot = db.scalar(
        select(models.Spot)
        .join(models.Venue)
        .where(models.Venue.slug == venue_slug, models.Spot.slug == spot_slug)
    )
    if not spot or not spot.is_published:
        raise HTTPException(404, "Titik tidak ditemukan")
    photos = [
        photo
        for photo in spot.photos
        if photo.status == "approved" and photo.intended_use in {"publik", "bukti_internal_dan_publik"}
    ]
    events = sorted(spot.public_events, key=lambda e: (e.effective_date, e.created_at), reverse=True)
    return render(
        request,
        "spot.html",
        {"spot": spot, "venue": spot.venue, "placements": active_placements(spot), "photos": photos, "events": events},
        db,
    )


@app.get("/photos/{photo_id}")
def serve_photo(photo_id: str, request: Request, db: Session = Depends(get_db)):
    photo = db.get(models.Photo, photo_id)
    if not photo:
        raise HTTPException(404, "Foto tidak ditemukan")
    user = get_current_user(request, db)
    public_ok = photo.status == "approved" and photo.intended_use in {
        "publik",
        "bukti_internal_dan_publik",
    }
    internal_ok = bool(user and user.can_input)
    if not public_ok and not internal_ok:
        raise HTTPException(403, "Foto ini tidak dapat dibuka publik")
    path = UPLOAD_DIR / photo.storage_name
    if not path.exists():
        raise HTTPException(404, "Berkas foto tidak ditemukan")
    return FileResponse(path)


@app.get("/merk")
def brands_page(request: Request, db: Session = Depends(get_db)):
    brands = list(db.scalars(select(models.Brand).order_by(models.Brand.name)).all())
    summaries = {}
    for brand in brands:
        summaries[brand.id] = {
            chair_type.id: services.rating_summary_for_type(db, chair_type)
            for chair_type in brand.chair_types
        }
    return render(request, "brands.html", {"brands": brands, "summaries": summaries}, db)


@app.get("/merk/{brand_slug}/{type_slug}")
def type_page(brand_slug: str, type_slug: str, request: Request, db: Session = Depends(get_db)):
    chair_type = db.scalar(
        select(models.ChairType)
        .join(models.Brand)
        .where(models.Brand.slug == brand_slug, models.ChairType.slug == type_slug)
    )
    if not chair_type:
        raise HTTPException(404, "Tipe kursi tidak ditemukan")
    placements = [
        p
        for p in chair_type.placements
        if p.is_published and p.effective_to is None and p.spot.is_published and p.spot.existence_status == "ada"
    ]
    visible_placements = [p for p in placements if not p.spot.is_private_only]
    return render(
        request,
        "type.html",
        {
            "chair_type": chair_type,
            "brand": chair_type.brand,
            "summary": services.rating_summary_for_type(db, chair_type),
            "placements": visible_placements,
            "private_count": len(placements) - len(visible_placements),
        },
        db,
    )


def find_rating_for_subject(db, placement_id, user, anon_subject):
    if user:
        return db.scalar(
            select(models.Rating).where(
                models.Rating.placement_id == placement_id, models.Rating.user_id == user.id
            )
        )
    if anon_subject:
        return db.scalar(
            select(models.Rating).where(
                models.Rating.placement_id == placement_id,
                models.Rating.anonymous_subject_id == anon_subject.id,
            )
        )
    return None


@app.get("/nilai/{placement_id}")
def rating_page(placement_id: str, request: Request, db: Session = Depends(get_db)):
    placement = db.get(models.Placement, placement_id)
    if not placement or not placement.is_published or not placement.spot.is_published:
        raise HTTPException(404, "Penempatan kursi tidak ditemukan")
    if not placement.can_rate:
        return render(
            request,
            "rating.html",
            {"placement": placement, "summary": [], "own_rating": None, "rating_unavailable": True},
            db,
        )
    user = get_current_user(request, db)
    anon_subject, _ = get_anon_subject(request, db, create=False)
    own_rating = find_rating_for_subject(db, placement.id, user, anon_subject)
    return render(
        request,
        "rating.html",
        {
            "placement": placement,
            "summary": services.rating_summary_for_placement(db, placement),
            "own_rating": own_rating,
            "rating_unavailable": False,
            "today": date.today(),
        },
        db,
    )


async def parse_rating_scores(request: Request, placement: models.Placement):
    form = await request.form()
    check_csrf(request, str(form.get("csrf_token") or ""))
    usage_raw = str(form.get("usage_date") or "")
    try:
        usage_date = date.fromisoformat(usage_raw)
    except ValueError as exc:
        raise HTTPException(400, "Tanggal pemakaian wajib diisi dengan benar") from exc
    if usage_date > date.today():
        raise HTTPException(400, "Tanggal pemakaian tidak boleh berada di masa depan")
    if placement.effective_from and usage_date < placement.effective_from:
        raise HTTPException(400, "Tanggal pemakaian berada sebelum penempatan ini tercatat berlaku")
    if placement.effective_to and usage_date > placement.effective_to:
        raise HTTPException(400, "Tanggal pemakaian berada setelah penempatan ini ditutup")
    supported = {m.body_area_id for m in placement.chair_type.supported_areas}
    scores = []
    for key, raw in form.items():
        if not key.startswith("score_") or raw in (None, ""):
            continue
        area_id = key.removeprefix("score_")
        if area_id not in supported:
            raise HTTPException(400, "Area yang dinilai tidak didukung oleh tipe kursi ini")
        try:
            stars = int(raw)
        except ValueError as exc:
            raise HTTPException(400, "Bintang harus berupa angka 1 sampai 5") from exc
        if stars < 1 or stars > 5:
            raise HTTPException(400, "Bintang harus berada antara 1 dan 5")
        scores.append((area_id, stars))
    if not scores:
        raise HTTPException(400, "Beri bintang untuk sedikitnya satu area yang Anda rasakan")
    return usage_date, scores


@app.post("/nilai/{placement_id}")
async def submit_rating(placement_id: str, request: Request, db: Session = Depends(get_db)):
    placement = db.get(models.Placement, placement_id)
    if not placement or not placement.can_rate:
        raise HTTPException(404, "Rating untuk penempatan ini belum dibuka")
    usage_date, scores = await parse_rating_scores(request, placement)
    user = get_current_user(request, db)
    anon_subject, raw_anon = (None, None)
    if not user:
        anon_subject, raw_anon = get_anon_subject(request, db, create=True)
    existing = find_rating_for_subject(db, placement.id, user, anon_subject)
    if existing:
        return redirect_with_flash(
            f"/nilai/{placement.id}", "Anda sudah mempunyai rating untuk kursi di lokasi ini. Gunakan fitur koreksi."
        )
    rating = models.Rating(
        placement_id=placement.id,
        user_id=user.id if user else None,
        anonymous_subject_id=anon_subject.id if anon_subject else None,
    )
    db.add(rating)
    db.flush()
    version = models.RatingVersion(rating_id=rating.id, version_no=1, usage_date=usage_date)
    db.add(version)
    db.flush()
    rating.pending_version_id = version.id
    for area_id, stars in scores:
        db.add(models.RatingAreaScore(rating_version_id=version.id, body_area_id=area_id, stars=stars))
    db.commit()
    response = redirect_with_flash(
        f"/nilai/{placement.id}", "Rating terkirim dan menunggu pemeriksaan admin sebelum dihitung."
    )
    if raw_anon:
        response.set_cookie(
            ANON_COOKIE, raw_anon, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 365
        )
    return response


@app.post("/ratings/{rating_id}/koreksi")
async def correct_rating(rating_id: str, request: Request, db: Session = Depends(get_db)):
    rating = db.get(models.Rating, rating_id)
    if not rating:
        raise HTTPException(404, "Rating tidak ditemukan")
    user = get_current_user(request, db)
    anon_subject, _ = get_anon_subject(request, db, create=False)
    owns = (user and rating.user_id == user.id) or (
        anon_subject and rating.anonymous_subject_id == anon_subject.id
    )
    if not owns:
        raise HTTPException(403, "Rating ini bukan milik Anda")
    placement = rating.placement
    usage_date, scores = await parse_rating_scores(request, placement)

    pending = rating.pending_version
    if pending and not rating.active_version:
        pending.usage_date = usage_date
        for old in list(pending.scores):
            db.delete(old)
        db.flush()
        for area_id, stars in scores:
            db.add(models.RatingAreaScore(rating_version_id=pending.id, body_area_id=area_id, stars=stars))
        db.commit()
        return redirect_with_flash(f"/nilai/{placement.id}", "Rating yang masih menunggu pemeriksaan telah diperbarui.")
    if pending:
        return redirect_with_flash(
            f"/nilai/{placement.id}", "Koreksi sebelumnya masih menunggu pemeriksaan."
        )
    next_no = max((v.version_no for v in rating.versions), default=0) + 1
    version = models.RatingVersion(rating_id=rating.id, version_no=next_no, usage_date=usage_date)
    db.add(version)
    db.flush()
    rating.pending_version_id = version.id
    for area_id, stars in scores:
        db.add(models.RatingAreaScore(rating_version_id=version.id, body_area_id=area_id, stars=stars))
    db.commit()
    return redirect_with_flash(f"/nilai/{placement.id}", "Koreksi terkirim. Rating lama tetap dihitung sampai koreksi disetujui.")


@app.get("/masuk")
def login_page(request: Request, db: Session = Depends(get_db)):
    if get_current_user(request, db):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", {}, db)


@app.post("/masuk")
async def login(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    check_csrf(request, str(form.get("csrf_token") or ""))
    email = str(form.get("email") or "").strip().lower()
    password = str(form.get("password") or "")
    user = db.scalar(select(models.User).where(func.lower(models.User.email) == email))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return render(request, "login.html", {"error": "Email atau kata sandi tidak cocok."}, db, 400)
    raw = new_token()
    db.add(models.Session(user_id=user.id, token_hash=token_hash(raw), expires_at=session_expiry()))
    db.commit()
    response = redirect_with_flash("/", f"Selamat datang, {user.name}.")
    response.set_cookie(SESSION_COOKIE, raw, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 14)
    return response


@app.get("/daftar")
def register_page(request: Request, db: Session = Depends(get_db)):
    return render(request, "register.html", {}, db)


@app.post("/daftar")
async def register(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    check_csrf(request, str(form.get("csrf_token") or ""))
    name = str(form.get("name") or "").strip()
    email = str(form.get("email") or "").strip().lower()
    password = str(form.get("password") or "")
    if len(name) < 2 or "@" not in email or len(password) < 8:
        return render(
            request,
            "register.html",
            {"error": "Nama wajib diisi, email harus valid, dan kata sandi minimal 8 karakter."},
            db,
            400,
        )
    if db.scalar(select(models.User).where(func.lower(models.User.email) == email)):
        return render(request, "register.html", {"error": "Email sudah digunakan."}, db, 400)
    user = models.User(name=name, email=email, password_hash=hash_password(password), role="public")
    db.add(user)
    db.flush()
    raw = new_token()
    db.add(models.Session(user_id=user.id, token_hash=token_hash(raw), expires_at=session_expiry()))
    db.commit()
    response = redirect_with_flash("/", "Akun berhasil dibuat.")
    response.set_cookie(SESSION_COOKIE, raw, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 14)
    return response


@app.post("/keluar")
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


@app.get("/akun/rating-saya")
def my_ratings(request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db)
    if response:
        return response
    ratings = list(
        db.scalars(
            select(models.Rating).where(models.Rating.user_id == user.id).order_by(models.Rating.updated_at.desc())
        ).all()
    )
    return render(request, "my_ratings.html", {"ratings": ratings}, db)


def save_staged_photo(upload: UploadFile | None):
    if not upload or not upload.filename:
        return None
    data = upload.file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "Foto tidak boleh lebih dari 5 MB")
    try:
        from io import BytesIO

        image = Image.open(BytesIO(data))
        image.verify()
        fmt = (image.format or "JPEG").lower()
    except Exception as exc:
        raise HTTPException(400, "Berkas yang diunggah harus berupa gambar") from exc
    ext = "jpg" if fmt in {"jpeg", "jpg"} else fmt
    if ext not in {"jpg", "png", "webp"}:
        raise HTTPException(400, "Format foto yang diterima adalah JPG, PNG, atau WebP")
    name = f"staged-{secrets.token_hex(16)}.{ext}"
    (UPLOAD_DIR / name).write_bytes(data)
    return name


@app.get("/internal")
def internal_dashboard(request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"data_entry", "admin"})
    if response:
        return response
    my_requests = list(
        db.scalars(
            select(models.ChangeRequest)
            .where(models.ChangeRequest.submitter_id == user.id)
            .order_by(models.ChangeRequest.created_at.desc())
        ).all()
    )
    spots = list(db.scalars(select(models.Spot).where(models.Spot.is_published.is_(True))).all())
    return render(request, "internal.html", {"my_requests": my_requests, "spots": spots}, db)


@app.get("/internal/usulan-baru")
def new_proposal(request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"data_entry", "admin"})
    if response:
        return response
    return render(
        request,
        "proposal_new.html",
        {
            "venues": list(db.scalars(select(models.Venue).order_by(models.Venue.name)).all()),
            "brands": list(db.scalars(select(models.Brand).order_by(models.Brand.name)).all()),
            "chair_types": list(db.scalars(select(models.ChairType).order_by(models.ChairType.name)).all()),
            "access_groups": list(db.scalars(select(models.AccessGroup)).all()),
            "today": date.today(),
        },
        db,
    )


@app.post("/internal/usulan-baru")
async def create_proposal(request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"data_entry", "admin"})
    if response:
        return response
    form = await request.form()
    check_csrf(request, str(form.get("csrf_token") or ""))
    existing_venue_id = str(form.get("existing_venue_id") or "")
    venue_name = str(form.get("venue_name") or "").strip()
    spot_name = str(form.get("spot_name") or "").strip()
    floor = str(form.get("floor") or "").strip()
    landmark = str(form.get("zone_landmark") or "").strip()
    if not spot_name or not floor or not landmark:
        raise HTTPException(400, "Nama titik, lantai, dan patokan wajib diisi")
    if not existing_venue_id and not venue_name:
        raise HTTPException(400, "Pilih venue lama atau isi venue baru")

    def clean(key):
        return str(form.get(key) or "").strip()

    if existing_venue_id and not db.get(models.Venue, existing_venue_id):
        raise HTTPException(400, "Venue lama yang dipilih tidak ditemukan")
    if not existing_venue_id:
        if not clean("address") or not clean("admin_city") or not clean("latitude") or not clean("longitude"):
            raise HTTPException(400, "Venue baru wajib mempunyai alamat, kota, lintang, dan bujur")
        try:
            latitude = float(clean("latitude").replace(",", "."))
            longitude = float(clean("longitude").replace(",", "."))
        except ValueError as exc:
            raise HTTPException(400, "Lintang dan bujur harus berupa angka") from exc
        if not -7.0 <= latitude <= -5.0 or not 105.0 <= longitude <= 108.0:
            raise HTTPException(400, "Koordinat venue berada di luar rentang Jakarta yang diharapkan")
    for numeric_field in ["quantity", "price_idr", "duration_minutes"]:
        raw_value = clean(numeric_field)
        if raw_value:
            try:
                if int(raw_value) < 0:
                    raise ValueError
            except ValueError as exc:
                raise HTTPException(400, f"Kolom {numeric_field} harus berupa bilangan bulat yang valid") from exc

    payload = {
        "venue": {
            "existing_venue_id": existing_venue_id or None,
            "name": venue_name,
            "category": clean("venue_category") or "lainnya",
            "address": clean("address"),
            "admin_city": clean("admin_city"),
            "district": clean("district"),
            "latitude": clean("latitude"),
            "longitude": clean("longitude"),
            "opening_hours": clean("opening_hours"),
        },
        "spot": {
            "name": spot_name,
            "floor": floor,
            "zone_landmark": landmark,
            "recommended_entrance": clean("recommended_entrance"),
            "walking_directions": clean("walking_directions"),
            "access_notes": clean("access_notes"),
            "existence_status": clean("existence_status") or "ada",
            "access_groups": form.getlist("access_groups") or ["umum"],
        },
        "placement": {
            "brand_id": clean("brand_id") or None,
            "brand_name": clean("brand_name"),
            "chair_type_id": clean("chair_type_id") or None,
            "chair_type_name": clean("chair_type_name"),
            "operator_name": clean("operator_name"),
            "quantity": clean("quantity"),
            "quantity_basis": clean("quantity_basis"),
            "cost_type": clean("cost_type") or "belum_diketahui",
            "tariff_type": clean("tariff_type"),
            "price_idr": clean("price_idr"),
            "duration_minutes": clean("duration_minutes"),
            "price_is_starting_from": bool(form.get("price_is_starting_from")),
            "payment_methods": form.getlist("payment_methods"),
        },
        "verification": {
            "source_title": clean("source_title"),
            "source_url": clean("source_url"),
            "source_type": clean("source_type"),
            "source_published_date": clean("source_published_date"),
            "checked_date": clean("checked_date") or date.today().isoformat(),
            "check_method": clean("check_method") or "riset_online",
            "confidence": clean("confidence") or "sedang",
            "fields_supported": clean("fields_supported"),
            "research_notes": clean("research_notes"),
        },
        "photo": {
            "intended_use": clean("photo_intended_use") or "bukti_internal",
            "caption": clean("photo_caption"),
            "license_note": clean("photo_license_note"),
        },
    }
    photo_upload = form.get("photo")
    staged_name = None
    if hasattr(photo_upload, "filename") and photo_upload.filename:
        staged_name = save_staged_photo(photo_upload)  # type: ignore[arg-type]
        payload["photo"]["original_name"] = photo_upload.filename

    change_request = models.ChangeRequest(
        submitter_id=user.id,
        kind="create_location",
        payload_json=json.dumps(payload, ensure_ascii=False),
        staged_photo_name=staged_name,
        status="submitted",
    )
    db.add(change_request)
    db.commit()
    return redirect_with_flash("/internal", "Usulan lokasi terkirim dan menunggu pemeriksaan admin.")


@app.get("/internal/titik/{spot_id}/usulkan")
def propose_spot_update(spot_id: str, request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"data_entry", "admin"})
    if response:
        return response
    spot = db.get(models.Spot, spot_id)
    if not spot:
        raise HTTPException(404, "Titik tidak ditemukan")
    return render(
        request,
        "proposal_update.html",
        {"spot": spot, "access_groups": list(db.scalars(select(models.AccessGroup)).all()), "today": date.today()},
        db,
    )


@app.post("/internal/titik/{spot_id}/usulkan")
async def create_spot_update(spot_id: str, request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"data_entry", "admin"})
    if response:
        return response
    spot = db.get(models.Spot, spot_id)
    if not spot:
        raise HTTPException(404, "Titik tidak ditemukan")
    form = await request.form()
    check_csrf(request, str(form.get("csrf_token") or ""))

    def clean(key):
        return str(form.get(key) or "").strip()

    payload = {
        "changes": {
            "floor": clean("floor"),
            "zone_landmark": clean("zone_landmark"),
            "recommended_entrance": clean("recommended_entrance"),
            "walking_directions": clean("walking_directions"),
            "access_notes": clean("access_notes"),
            "existence_status": clean("existence_status") or spot.existence_status,
            "access_groups": form.getlist("access_groups") or spot.access_codes,
        },
        "checked_date": clean("checked_date") or date.today().isoformat(),
        "check_method": clean("check_method") or "riset_online",
        "confidence": clean("confidence") or "sedang",
        "source_title": clean("source_title"),
        "source_url": clean("source_url"),
        "source_type": clean("source_type"),
        "fields_supported": clean("fields_supported"),
        "notes": clean("notes"),
    }
    change_request = models.ChangeRequest(
        submitter_id=user.id,
        kind="spot_update",
        target_spot_id=spot.id,
        base_version=spot.version,
        payload_json=json.dumps(payload, ensure_ascii=False),
        status="submitted",
    )
    db.add(change_request)
    db.commit()
    return redirect_with_flash("/internal", "Usulan perubahan titik terkirim dan menunggu pemeriksaan admin.")


@app.get("/admin")
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"admin"})
    if response:
        return response
    location_queue = list(
        db.scalars(
            select(models.ChangeRequest)
            .where(models.ChangeRequest.status == "submitted")
            .order_by(models.ChangeRequest.created_at)
        ).all()
    )
    rating_queue = list(
        db.scalars(
            select(models.RatingVersion)
            .where(models.RatingVersion.status == "pending")
            .order_by(models.RatingVersion.created_at)
        ).all()
    )
    return render(
        request,
        "admin.html",
        {
            "location_queue": location_queue,
            "rating_queue": rating_queue,
            "change_kind_labels": CHANGE_KIND_LABELS,
            "spot_count": db.scalar(select(func.count(models.Spot.id))) or 0,
            "venue_count": db.scalar(select(func.count(models.Venue.id))) or 0,
        },
        db,
    )


@app.get("/admin/usulan/{change_request_id}")
def admin_change_detail(change_request_id: str, request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"admin"})
    if response:
        return response
    item = db.get(models.ChangeRequest, change_request_id)
    if not item:
        raise HTTPException(404, "Usulan tidak ditemukan")
    payload = json.loads(item.payload_json)
    return render(
        request,
        "admin_change.html",
        {"item": item, "payload": payload, "payload_pretty": json.dumps(payload, indent=2, ensure_ascii=False)},
        db,
    )


@app.post("/admin/usulan/{change_request_id}/keputusan")
async def decide_change(change_request_id: str, request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"admin"})
    if response:
        return response
    item = db.get(models.ChangeRequest, change_request_id)
    if not item or item.status != "submitted":
        raise HTTPException(404, "Usulan yang menunggu pemeriksaan tidak ditemukan")
    if item.submitter_id == user.id:
        return redirect_with_flash(f"/admin/usulan/{item.id}", "Admin tidak dapat memeriksa usulannya sendiri.")
    form = await request.form()
    check_csrf(request, str(form.get("csrf_token") or ""))
    action = str(form.get("action") or "")
    note = str(form.get("review_note") or "").strip()
    change_kind = str(form.get("change_kind") or "koreksi")
    if action in {"return", "reject"} and not note:
        return redirect_with_flash(
            f"/admin/usulan/{item.id}",
            "Catatan pemeriksaan wajib diisi bila usulan dikembalikan atau ditolak.",
        )
    item.reviewer_id = user.id
    item.review_note = note or None
    item.reviewed_at = datetime.now(timezone.utc)
    if action == "return":
        item.status = "returned"
        db.commit()
        return redirect_with_flash("/admin", "Usulan dikembalikan kepada petugas.")
    if action == "reject":
        item.status = "rejected"
        db.commit()
        return redirect_with_flash("/admin", "Usulan ditolak.")
    if action != "approve":
        raise HTTPException(400, "Keputusan tidak dikenali")
    try:
        if item.kind == "create_location":
            spot, _placement = services.apply_create_location(db, item, user)
            item.applied_spot_id = spot.id
            item.change_kind = "lapangan"
        elif item.kind == "spot_update":
            if change_kind not in {"lapangan", "koreksi"}:
                raise ValueError("Jenis perubahan wajib dipilih")
            spot = services.apply_spot_update(db, item, user, change_kind)
            item.applied_spot_id = spot.id
            item.change_kind = change_kind
        else:
            raise ValueError("Jenis usulan belum didukung")
        item.status = "approved"
        db.commit()
    except Exception as exc:
        db.rollback()
        return redirect_with_flash(f"/admin/usulan/{item.id}", f"Usulan belum dapat diterapkan: {exc}")
    return redirect_with_flash("/admin", "Usulan disetujui dan data publik diperbarui.")


@app.post("/admin/rating/{version_id}/keputusan")
async def decide_rating(version_id: str, request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"admin"})
    if response:
        return response
    version = db.get(models.RatingVersion, version_id)
    if not version or version.status != "pending":
        raise HTTPException(404, "Rating yang menunggu pemeriksaan tidak ditemukan")
    rating = version.rating
    if rating.user_id == user.id:
        return redirect_with_flash("/admin", "Admin tidak dapat memeriksa ratingnya sendiri.")
    form = await request.form()
    check_csrf(request, str(form.get("csrf_token") or ""))
    action = str(form.get("action") or "")
    review_note = str(form.get("review_note") or "").strip()
    if action == "reject" and not review_note:
        return redirect_with_flash("/admin", "Catatan pemeriksaan wajib diisi bila rating ditolak.")
    version.reviewed_by_id = user.id
    version.review_note = review_note or None
    version.reviewed_at = datetime.now(timezone.utc)
    if action == "approve":
        if rating.active_version:
            rating.active_version.status = "superseded"
        version.status = "approved"
        rating.active_version_id = version.id
        rating.pending_version_id = None
        message = "Rating disetujui dan mulai dihitung."
    elif action == "reject":
        version.status = "rejected"
        rating.pending_version_id = None
        message = "Rating ditolak dan tidak dihitung."
    else:
        raise HTTPException(400, "Keputusan tidak dikenali")
    rating.updated_at = datetime.now(timezone.utc)
    services.audit(db, user.id, "rating", rating.id, f"rating_{version.status}", {"version_id": version.id})
    db.commit()
    return redirect_with_flash("/admin", message)


@app.get("/admin/master-tipe")
def admin_types(request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"admin"})
    if response:
        return response
    chair_types = list(db.scalars(select(models.ChairType).order_by(models.ChairType.name)).all())
    return render(request, "admin_types.html", {"chair_types": chair_types}, db)


@app.get("/admin/master-tipe/{chair_type_id}")
def admin_type_edit(chair_type_id: str, request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"admin"})
    if response:
        return response
    chair_type = db.get(models.ChairType, chair_type_id)
    if not chair_type:
        raise HTTPException(404, "Tipe tidak ditemukan")
    return render(request, "admin_type_edit.html", {"chair_type": chair_type, "today": date.today()}, db)


@app.post("/admin/master-tipe/{chair_type_id}")
async def admin_type_save(chair_type_id: str, request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"admin"})
    if response:
        return response
    chair_type = db.get(models.ChairType, chair_type_id)
    if not chair_type:
        raise HTTPException(404, "Tipe tidak ditemukan")
    form = await request.form()
    check_csrf(request, str(form.get("csrf_token") or ""))
    for mapping in chair_type.body_areas:
        status = str(form.get(f"status_{mapping.id}") or mapping.support_status)
        if status not in {"didukung", "tidak_didukung", "belum_dikonfirmasi"}:
            raise HTTPException(400, "Status dukungan area tidak valid")
        mapping.support_status = status
        mapping.mechanism = str(form.get(f"mechanism_{mapping.id}") or "").strip() or None
        mapping.source_note = str(form.get(f"source_{mapping.id}") or "").strip() or None
        mapping.confirmed_at = date.today() if status != "belum_dikonfirmasi" else None
    services.audit(db, user.id, "chair_type", chair_type.id, "feature_map_updated", {})
    db.commit()
    return redirect_with_flash("/admin/master-tipe", "Peta fitur tipe kursi diperbarui.")


@app.get("/tentang-data")
def about_data(request: Request, db: Session = Depends(get_db)):
    return render(request, "about.html", {}, db)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
