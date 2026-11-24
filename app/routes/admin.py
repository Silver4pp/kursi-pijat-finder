"""Admin routes: moderation queues, decisions, and master data."""

import csv
import io
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import models, services
from ..db import get_db
from ..web import (
    CATEGORIES,
    CITIES,
    CHANGE_KIND_LABELS,
    check_csrf,
    discard_staged_photo,
    paginate,
    redirect_with_flash,
    render,
    require_user,
)

logger = logging.getLogger("kpf")


def _csv_safe(value):
    """Neutralise spreadsheet formulas in user-provided text."""
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + value
    return value


def _csv_row(cells):
    return [_csv_safe(cell) for cell in cells]

router = APIRouter()


def _pager_prefix(request: Request, page_key: str) -> str:
    """Build a pager URL that keeps the sibling queue's page param."""
    rest = {key: value for key, value in request.query_params.items() if key != page_key}
    query = urlencode(rest)
    return f"/admin?{query}&{page_key}=" if query else f"/admin?{page_key}="


@router.get("/admin")
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
    loc_page, loc_pager = paginate(location_queue, request.query_params.get("loc_page"), 10)
    rat_page, rat_pager = paginate(rating_queue, request.query_params.get("rat_page"), 10)
    return render(
        request,
        "admin.html",
        {
            "location_queue": loc_page,
            "rating_queue": rat_page,
            "loc_pager": loc_pager,
            "rat_pager": rat_pager,
            "loc_prefix": _pager_prefix(request, "loc_page"),
            "rat_prefix": _pager_prefix(request, "rat_page"),
            "change_kind_labels": CHANGE_KIND_LABELS,
            "spot_count": db.scalar(select(func.count(models.Spot.id))) or 0,
            "venue_count": db.scalar(select(func.count(models.Venue.id))) or 0,
        },
        db,
    )


@router.get("/admin/usulan/{change_request_id}")
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


@router.post("/admin/usulan/{change_request_id}/keputusan")
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
        discard_staged_photo(item)
        services.audit(db, user.id, "change_request", item.id, "change_request_returned", {"note": note or None})
        db.commit()
        return redirect_with_flash("/admin", "Usulan dikembalikan kepada petugas.")
    if action == "reject":
        item.status = "rejected"
        discard_staged_photo(item)
        services.audit(db, user.id, "change_request", item.id, "change_request_rejected", {"note": note or None})
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
    except Exception:
        db.rollback()
        logger.exception("Gagal menerapkan usulan %s (kind=%s)", item.id, item.kind)
        return redirect_with_flash(
            f"/admin/usulan/{item.id}",
            "Usulan belum dapat diterapkan karena datanya tidak valid. Pemeriksaan tidak tersimpan.",
        )
    return redirect_with_flash("/admin", "Usulan disetujui dan data publik diperbarui.")


@router.get("/admin/venue/{venue_id}/edit")
def admin_venue_edit(venue_id: str, request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"admin"})
    if response:
        return response
    venue = db.get(models.Venue, venue_id)
    if not venue:
        raise HTTPException(404, "Venue tidak ditemukan")
    return render(
        request,
        "admin_venue_edit.html",
        {"venue": venue},
        db,
    )


@router.post("/admin/venue/{venue_id}/edit")
async def admin_venue_save(venue_id: str, request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"admin"})
    if response:
        return response
    venue = db.get(models.Venue, venue_id)
    if not venue:
        raise HTTPException(404, "Venue tidak ditemukan")
    form = await request.form()
    check_csrf(request, str(form.get("csrf_token") or ""))

    def clean(key):
        return str(form.get(key) or "").strip()

    name = clean("name")
    category = clean("category")
    address = clean("address")
    admin_city = clean("admin_city")
    if not name or not address or not admin_city:
        return redirect_with_flash(
            f"/admin/venue/{venue.id}/edit",
            "Nama, alamat, dan kota wajib diisi.",
        )
    if category not in CATEGORIES:
        return redirect_with_flash(f"/admin/venue/{venue.id}/edit", "Kategori tidak dikenali.")
    if admin_city not in CITIES:
        return redirect_with_flash(f"/admin/venue/{venue.id}/edit", "Kota tidak dikenali.")
    try:
        latitude = float(clean("latitude").replace(",", "."))
        longitude = float(clean("longitude").replace(",", "."))
    except ValueError:
        return redirect_with_flash(f"/admin/venue/{venue.id}/edit", "Lintang dan bujur harus berupa angka.")
    if not -7.0 <= latitude <= -5.0 or not 105.0 <= longitude <= 108.0:
        return redirect_with_flash(
            f"/admin/venue/{venue.id}/edit",
            "Koordinat venue berada di luar rentang Jakarta yang diharapkan.",
        )

    old_name = venue.name
    venue.name = name
    venue.category = category
    venue.address = address
    venue.admin_city = admin_city
    venue.district = clean("district") or None
    venue.latitude = latitude
    venue.longitude = longitude
    venue.opening_hours = clean("opening_hours") or None
    venue.is_published = bool(form.get("is_published"))
    services.audit(
        db,
        user.id,
        "venue",
        venue.id,
        "venue_updated",
        {"name": name, "renamed": old_name != name, "is_published": venue.is_published},
    )
    db.commit()
    return redirect_with_flash(f"/admin/venue/{venue.id}/edit", "Data gedung diperbarui.")


@router.post("/admin/rating/{version_id}/keputusan")
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


@router.get("/admin/export/spots.csv")
def admin_export_spots_csv(request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"admin"})
    if response:
        return response
    spots = list(
        db.scalars(
            select(models.Spot)
            .join(models.Venue)
            .options(
                selectinload(models.Spot.venue),
                selectinload(models.Spot.placements),
                selectinload(models.Spot.access_groups),
            )
            .order_by(models.Venue.name, models.Spot.floor, models.Spot.name)
        ).unique().all()
    )
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "venue_name",
            "venue_city",
            "venue_category",
            "venue_district",
            "venue_lat",
            "venue_lng",
            "spot_name",
            "spot_slug",
            "floor",
            "landmark",
            "existence_status",
            "access_codes",
            "brand",
            "chair_type",
            "operator",
            "quantity",
            "cost_type",
            "price_idr",
            "duration_minutes",
            "payment_methods",
            "effective_from",
        ]
    )
    for spot in spots:
        venue = spot.venue
        for placement in spot.placements:
            if not placement.is_published or placement.effective_to is not None:
                continue
            writer.writerow(_csv_row(
                [
                    venue.name,
                    venue.admin_city,
                    venue.category,
                    venue.district or "",
                    venue.latitude,
                    venue.longitude,
                    spot.name,
                    spot.slug,
                    spot.floor,
                    spot.zone_landmark or "",
                    spot.existence_status,
                    ";".join(sorted(spot.access_codes)),
                    placement.brand.name if placement.brand else "",
                    placement.chair_type.name if placement.chair_type else "",
                    placement.operator.name if placement.operator else "",
                    placement.quantity if placement.quantity is not None else "",
                    placement.cost_type,
                    placement.price_idr if placement.price_idr is not None else "",
                    placement.duration_minutes if placement.duration_minutes is not None else "",
                    ";".join(placement.payment_codes),
                    placement.effective_from.isoformat() if placement.effective_from else "",
                ]
            ))
    filename = f"spots-{datetime.now(timezone.utc).date().isoformat()}.csv"
    return Response(
        content=buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/admin/master-tipe")
def admin_types(request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"admin"})
    if response:
        return response
    chair_types = list(db.scalars(select(models.ChairType).order_by(models.ChairType.name)).all())
    return render(request, "admin_types.html", {"chair_types": chair_types}, db)


@router.get("/admin/master-tipe/{chair_type_id}")
def admin_type_edit(chair_type_id: str, request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"admin"})
    if response:
        return response
    chair_type = db.get(models.ChairType, chair_type_id)
    if not chair_type:
        raise HTTPException(404, "Tipe tidak ditemukan")
    return render(request, "admin_type_edit.html", {"chair_type": chair_type, "today": datetime.now().date()}, db)


@router.post("/admin/master-tipe/{chair_type_id}")
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
        mapping.confirmed_at = datetime.now().date() if status != "belum_dikonfirmasi" else None
    services.audit(db, user.id, "chair_type", chair_type.id, "feature_map_updated", {})
    db.commit()
    return redirect_with_flash("/admin/master-tipe", "Peta fitur tipe kursi diperbarui.")
