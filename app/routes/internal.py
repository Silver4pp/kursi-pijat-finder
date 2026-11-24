"""Internal workspace for data-entry staff: dashboards and change proposals."""

import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..db import get_db
from ..web import (
    EXISTENCE_STATUSES,
    REQUEST_STATUS_LABELS,
    check_csrf,
    redirect_with_flash,
    render,
    require_user,
    save_staged_photo,
)

router = APIRouter()


@router.get("/internal")
def internal_dashboard(request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db, roles={"data_entry", "admin"})
    if response:
        return response
    status_filter = request.query_params.get("status") or ""
    stmt = select(models.ChangeRequest).where(models.ChangeRequest.submitter_id == user.id)
    if status_filter in REQUEST_STATUS_LABELS:
        stmt = stmt.where(models.ChangeRequest.status == status_filter)
    my_requests = list(db.scalars(stmt.order_by(models.ChangeRequest.created_at.desc())).all())
    status_counts = dict(
        db.execute(
            select(models.ChangeRequest.status, func.count())
            .where(models.ChangeRequest.submitter_id == user.id)
            .group_by(models.ChangeRequest.status)
        ).all()
    )
    spots = list(db.scalars(select(models.Spot).where(models.Spot.is_published.is_(True))).all())
    return render(
        request,
        "internal.html",
        {
            "my_requests": my_requests,
            "spots": spots,
            "status_labels": REQUEST_STATUS_LABELS,
            "status_counts": status_counts,
            "total_requests": sum(status_counts.values()),
            "status_filter": status_filter,
        },
        db,
    )


@router.get("/internal/usulan-baru")
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
            "chair_types": list(
                db.scalars(select(models.ChairType).order_by(models.ChairType.name)).all()
            ),
            "access_groups": list(db.scalars(select(models.AccessGroup)).all()),
            "today": date.today(),
        },
        db,
    )


@router.post("/internal/usulan-baru")
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
        if (
            not clean("address")
            or not clean("admin_city")
            or not clean("latitude")
            or not clean("longitude")
        ):
            raise HTTPException(400, "Venue baru wajib mempunyai alamat, kota, lintang, dan bujur")
        try:
            latitude = float(clean("latitude").replace(",", "."))
            longitude = float(clean("longitude").replace(",", "."))
        except ValueError as exc:
            raise HTTPException(400, "Lintang dan bujur harus berupa angka") from exc
        if not -7.0 <= latitude <= -5.0 or not 105.0 <= longitude <= 108.0:
            raise HTTPException(
                400, "Koordinat venue berada di luar rentang Jakarta yang diharapkan"
            )
    for numeric_field in ["quantity", "price_idr", "duration_minutes"]:
        raw_value = clean(numeric_field)
        if raw_value:
            try:
                if int(raw_value) < 0:
                    raise ValueError
            except ValueError as exc:
                raise HTTPException(
                    400, f"Kolom {numeric_field} harus berupa bilangan bulat yang valid"
                ) from exc

    existence = clean("existence_status") or "ada"
    if existence not in EXISTENCE_STATUSES:
        raise HTTPException(400, "Status keberadaan tidak dikenali")

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
            "existence_status": existence,
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
    return redirect_with_flash(
        "/internal", "Usulan lokasi terkirim dan menunggu pemeriksaan admin."
    )


@router.get("/internal/titik/{spot_id}/usulkan")
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
        {
            "spot": spot,
            "access_groups": list(db.scalars(select(models.AccessGroup)).all()),
            "today": date.today(),
        },
        db,
    )


@router.post("/internal/titik/{spot_id}/usulkan")
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

    existence = clean("existence_status") or spot.existence_status
    if existence not in EXISTENCE_STATUSES:
        raise HTTPException(400, "Status keberadaan tidak dikenali")

    payload = {
        "changes": {
            "floor": clean("floor"),
            "zone_landmark": clean("zone_landmark"),
            "recommended_entrance": clean("recommended_entrance"),
            "walking_directions": clean("walking_directions"),
            "access_notes": clean("access_notes"),
            "existence_status": existence,
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
    return redirect_with_flash(
        "/internal", "Usulan perubahan titik terkirim dan menunggu pemeriksaan admin."
    )
