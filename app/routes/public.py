"""Public routes: search, venue/spot pages, photos, brands, and JSON API."""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from .. import models, services
from ..db import get_db
from ..uploads import upload_path
from ..web import CATEGORIES, get_current_user, paginate, render

router = APIRouter()


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


@router.get("/robots.txt")
def robots_txt():
    return PlainTextResponse(
        "User-agent: *\nDisallow: /internal\nDisallow: /admin\nDisallow: /nilai\n"
    )


@router.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    params = dict(request.query_params)
    result = services.filter_spots(db, params)
    spots = result.spots
    venues_by_id = {}
    for spot in spots:
        item = venues_by_id.setdefault(
            spot.venue_id,
            {"venue": spot.venue, "spots": [], "distance": result.distance_for(spot)},
        )
        item["spots"].append(spot)
    brands = list(db.scalars(select(models.Brand).order_by(models.Brand.name)).all())
    access_groups = list(db.scalars(select(models.AccessGroup).order_by(models.AccessGroup.label)).all())
    venue_list = list(venues_by_id.values())
    page_items, pager = paginate(venue_list, params.get("page"), 10)
    base_query = {k: v for k, v in params.items() if k != "page"}
    pager_prefix = "/?" + (urlencode(base_query) + "&" if base_query else "") + "page="
    return render(
        request,
        "index.html",
        {
            "venue_items": page_items,
            "pager": pager,
            "pager_prefix": pager_prefix,
            "spots": spots,
            "brands": brands,
            "access_groups": access_groups,
            "params": params,
            "hidden_unknown_qty": result.hidden_unknown_qty,
        },
        db,
    )


@router.get("/api/v1/venues")
def api_venues(request: Request, db: Session = Depends(get_db)):
    params = dict(request.query_params)
    result = services.filter_spots(db, params)
    venues = {}
    for spot in result.spots:
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
                "distance_km": result.distances.get(venue.id),
            },
        )
        entry["spot_count"] += 1
    return {"venues": list(venues.values()), "hidden_unknown_quantity_spots": result.hidden_unknown_qty}


@router.get("/api/v1/venues/{venue_id}/spots")
def api_venue_spots(venue_id: str, request: Request, db: Session = Depends(get_db)):
    venue = db.get(models.Venue, venue_id)
    if not venue or not venue.is_published:
        raise HTTPException(404, "Venue tidak ditemukan")
    params = dict(request.query_params)
    result = services.filter_spots(db, params)
    venue_spots = [spot for spot in result.spots if spot.venue_id == venue.id]
    return {
        "venue": {"id": venue.id, "name": venue.name, "slug": venue.slug},
        "spots": [services.serialize_spot(spot) for spot in venue_spots],
    }


@router.get("/api/v1/suggest")
def api_suggest(q: str = "", db: Session = Depends(get_db)):
    """Lightweight autocomplete for the home search box."""
    term = (q or "").strip()
    if len(term) < 2:
        return {"venues": [], "spots": [], "brands": []}
    venues = list(
        db.scalars(
            select(models.Venue)
            .where(
                models.Venue.is_published.is_(True),
                models.Venue.name.contains(term, autoescape=True),
            )
            .order_by(models.Venue.name)
            .limit(5)
        ).all()
    )
    spots = list(
        db.scalars(
            select(models.Spot)
            .join(models.Venue)
            .where(
                models.Spot.is_published.is_(True),
                models.Venue.is_published.is_(True),
                models.Spot.existence_status == "ada",
                models.Spot.name.contains(term, autoescape=True),
            )
            .order_by(models.Venue.name, models.Spot.floor, models.Spot.name)
            .options(
                selectinload(models.Spot.venue),
                selectinload(models.Spot.access_groups),
            )
            .limit(20)
        ).all()
    )
    # Fetch a wider window, then drop private-only facilities and keep the best five.
    spots = [spot for spot in spots if services.is_publicly_visible(spot)][:5]
    brands = list(
        db.scalars(
            select(models.Brand)
            .where(models.Brand.name.contains(term, autoescape=True))
            .order_by(models.Brand.name)
            .limit(3)
        ).all()
    )
    return {
        "venues": [
            {
                "slug": venue.slug,
                "name": venue.name,
                "city": venue.admin_city,
                "category_label": CATEGORIES.get(venue.category, venue.category),
            }
            for venue in venues
        ],
        "spots": [
            {
                "venue_slug": spot.venue.slug,
                "spot_slug": spot.slug,
                "name": spot.name,
                "venue_name": spot.venue.name,
                "floor": spot.floor,
            }
            for spot in spots
        ],
        "brands": [{"name": brand.name, "slug": brand.slug} for brand in brands],
    }


@router.get("/venue/{venue_slug}")
def venue_detail(venue_slug: str, request: Request, db: Session = Depends(get_db)):
    venue = db.scalar(select(models.Venue).where(models.Venue.slug == venue_slug))
    if not venue or not venue.is_published:
        raise HTTPException(404, "Venue tidak ditemukan")
    spots = [
        spot
        for spot in venue.spots
        if spot.is_published and services.is_publicly_visible(spot)
    ]
    return render(request, "venue.html", {"venue": venue, "spots": spots}, db)


@router.get("/venue/{venue_slug}/titik/{spot_slug}")
def spot_detail(venue_slug: str, spot_slug: str, request: Request, db: Session = Depends(get_db)):
    spot = db.scalar(
        select(models.Spot)
        .join(models.Venue)
        .where(models.Venue.slug == venue_slug, models.Spot.slug == spot_slug)
    )
    if not spot or not spot.is_published or not spot.venue.is_published:
        raise HTTPException(404, "Titik tidak ditemukan")
    if not services.is_publicly_visible(spot):
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
        {"spot": spot, "venue": spot.venue, "placements": services.active_placements(spot), "photos": photos, "events": events},
        db,
    )


@router.get("/photos/{photo_id}")
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
    path = upload_path(photo.storage_name)
    if not path.exists():
        raise HTTPException(404, "Berkas foto tidak ditemukan")
    return FileResponse(
        path,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=86400",
        },
    )


@router.get("/merk")
def brands_page(request: Request, db: Session = Depends(get_db)):
    brands = list(db.scalars(select(models.Brand).order_by(models.Brand.name)).all())
    summaries = {}
    for brand in brands:
        summaries[brand.id] = {
            chair_type.id: services.rating_summary_for_type(db, chair_type)
            for chair_type in brand.chair_types
        }
    return render(request, "brands.html", {"brands": brands, "summaries": summaries}, db)


@router.get("/merk/{brand_slug}/{type_slug}")
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
        if p.is_published
        and p.effective_to is None
        and p.spot.is_published
        and p.spot.venue.is_published
        and p.spot.existence_status == "ada"
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


@router.get("/tentang-data")
def about_data(request: Request, db: Session = Depends(get_db)):
    return render(request, "about.html", {}, db)
