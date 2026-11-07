import json
import math
import re
import unicodedata
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models

PAYMENT_LABELS = {
    "uang_tunai": "Uang tunai",
    "koin": "Koin",
    "qris": "QRIS",
    "e_money": "E-money",
    "kartu_token_venue": "Kartu/token venue",
    "aplikasi_operator": "Aplikasi operator",
    "gratis": "Gratis bagi yang berhak",
}

COST_LABELS = {
    "berbayar_per_sesi": "Berbayar per sesi",
    "gratis_berhak": "Gratis bagi yang berhak mengakses",
    "termasuk_fasilitas": "Termasuk fasilitas venue",
    "belum_diketahui": "Biaya belum diketahui",
}

METHOD_LABELS = {
    "riset_online": "riset online",
    "konfirmasi_resmi": "konfirmasi resmi",
    "kunjungan": "kunjungan lapangan",
}


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return slug or "lokasi"


def unique_slug(db: Session, model, base: str, **filters) -> str:
    slug = slugify(base)[:180]
    candidate = slug
    number = 2
    while True:
        stmt = select(model).where(model.slug == candidate)
        for key, value in filters.items():
            stmt = stmt.where(getattr(model, key) == value)
        if db.scalar(stmt) is None:
            return candidate
        candidate = f"{slug}-{number}"
        number += 1


def format_rupiah(value):
    if value is None:
        return None
    return "Rp" + f"{int(value):,}".replace(",", ".")


def format_decimal_id(value):
    if value is None:
        return "-"
    return f"{value:.1f}".replace(".", ",")


def haversine_km(lat1, lon1, lat2, lon2):
    radius = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def audit(db: Session, actor_id, entity_type, entity_id, action, details=None):
    db.add(
        models.AuditLog(
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            action=action,
            details_json=json.dumps(details or {}, ensure_ascii=False),
        )
    )


def _approved_scores(db: Session, placements):
    placement_ids = [p.id for p in placements]
    if not placement_ids:
        return []
    stmt = (
        select(models.RatingAreaScore, models.Rating)
        .join(models.RatingVersion, models.RatingVersion.id == models.RatingAreaScore.rating_version_id)
        .join(models.Rating, models.Rating.id == models.RatingVersion.rating_id)
        .where(
            models.Rating.placement_id.in_(placement_ids),
            models.RatingVersion.status == "approved",
            models.RatingVersion.id == models.Rating.active_version_id,
        )
    )
    return list(db.execute(stmt).all())


def summarize_scores(rows):
    grouped = defaultdict(list)
    composition = {}
    for score, rating in rows:
        grouped[score.body_area_id].append(score)
        data = composition.setdefault(
            score.body_area_id, {"account": 0, "anonymous": 0, "raters": set()}
        )
        key = (rating.id,)
        if key not in data["raters"]:
            data["raters"].add(key)
            if rating.user_id:
                data["account"] += 1
            else:
                data["anonymous"] += 1
    result = {}
    for area_id, scores in grouped.items():
        count = len(scores)
        result[area_id] = {
            "count": count,
            "average": (sum(s.stars for s in scores) / count) if count else None,
            "account_count": composition[area_id]["account"],
            "anonymous_count": composition[area_id]["anonymous"],
        }
    return result


def rating_summary_for_placement(db: Session, placement: models.Placement):
    """Return per-area location and same-type summaries following the 5-rater rule."""
    if not placement.chair_type:
        return []
    local = summarize_scores(_approved_scores(db, [placement]))
    type_placements = list(
        db.scalars(
            select(models.Placement).where(models.Placement.chair_type_id == placement.chair_type_id)
        ).all()
    )
    global_summary = summarize_scores(_approved_scores(db, type_placements))
    all_areas = {
        mapping.body_area_id: mapping
        for mapping in placement.chair_type.body_areas
    }
    ordered = sorted(
        all_areas.values(), key=lambda m: m.body_area.sort_order if m.body_area else 0
    )
    output = []
    empty = {"count": 0, "average": None, "account_count": 0, "anonymous_count": 0}
    for mapping in ordered:
        area_id = mapping.body_area_id
        local_data = local.get(area_id, empty)
        global_data = global_summary.get(area_id, empty)
        output.append(
            {
                "mapping": mapping,
                "area": mapping.body_area,
                "support_status": mapping.support_status,
                "local": local_data,
                "global": global_data,
                "show_local": local_data["count"] >= 5,
                "show_global": global_data["count"] >= 5,
            }
        )
    return output


def rating_summary_for_type(db: Session, chair_type: models.ChairType):
    placements = list(
        db.scalars(select(models.Placement).where(models.Placement.chair_type_id == chair_type.id)).all()
    )
    summary = summarize_scores(_approved_scores(db, placements))
    empty = {"count": 0, "average": None, "account_count": 0, "anonymous_count": 0}
    output = []
    for mapping in sorted(
        chair_type.body_areas, key=lambda m: m.body_area.sort_order if m.body_area else 0
    ):
        output.append(
            {
                "mapping": mapping,
                "area": mapping.body_area,
                "support_status": mapping.support_status,
                "summary": summary.get(mapping.body_area_id, empty),
                "show": summary.get(mapping.body_area_id, empty)["count"] >= 5,
            }
        )
    return output


def get_or_create_named(db: Session, model, name, **extra):
    if not name or not str(name).strip():
        return None
    name = str(name).strip()
    existing = db.scalar(select(model).where(model.name == name))
    if existing:
        return existing
    values = {"name": name, **extra}
    if hasattr(model, "slug"):
        values["slug"] = unique_slug(db, model, name)
    obj = model(**values)
    db.add(obj)
    db.flush()
    return obj


def apply_create_location(db: Session, change_request: models.ChangeRequest, admin: models.User):
    payload = json.loads(change_request.payload_json)
    venue_data = payload.get("venue") or {}
    venue = None
    if venue_data.get("existing_venue_id"):
        venue = db.get(models.Venue, venue_data["existing_venue_id"])
    if venue is None:
        venue = models.Venue(
            name=venue_data["name"],
            slug=unique_slug(db, models.Venue, venue_data["name"]),
            category=venue_data["category"],
            address=venue_data["address"],
            admin_city=venue_data["admin_city"],
            district=venue_data.get("district") or None,
            latitude=float(venue_data["latitude"]),
            longitude=float(venue_data["longitude"]),
            opening_hours=venue_data.get("opening_hours") or None,
            is_published=True,
        )
        db.add(venue)
        db.flush()

    spot_data = payload.get("spot") or {}
    spot = models.Spot(
        venue_id=venue.id,
        name=spot_data["name"],
        slug=unique_slug(db, models.Spot, spot_data["name"], venue_id=venue.id),
        floor=spot_data["floor"],
        zone_landmark=spot_data["zone_landmark"],
        recommended_entrance=spot_data.get("recommended_entrance") or None,
        walking_directions=spot_data.get("walking_directions") or None,
        access_notes=spot_data.get("access_notes") or None,
        existence_status=spot_data.get("existence_status") or "ada",
        is_published=True,
        last_verified_at=parse_date(payload.get("verification", {}).get("checked_date")),
        verification_method=payload.get("verification", {}).get("check_method"),
    )
    access_codes = spot_data.get("access_groups") or ["umum"]
    spot.access_groups = list(
        db.scalars(select(models.AccessGroup).where(models.AccessGroup.code.in_(access_codes))).all()
    )
    db.add(spot)
    db.flush()

    placement_data = payload.get("placement") or {}
    brand = None
    chair_type = None
    if placement_data.get("brand_id"):
        brand = db.get(models.Brand, placement_data["brand_id"])
    elif placement_data.get("brand_name"):
        brand = get_or_create_named(db, models.Brand, placement_data["brand_name"])
    if placement_data.get("chair_type_id"):
        chair_type = db.get(models.ChairType, placement_data["chair_type_id"])
        if chair_type:
            brand = chair_type.brand
    elif brand and placement_data.get("chair_type_name"):
        type_name = str(placement_data["chair_type_name"]).strip()
        chair_type = db.scalar(
            select(models.ChairType).where(
                models.ChairType.brand_id == brand.id, models.ChairType.name == type_name
            )
        )
        if chair_type is None:
            chair_type = models.ChairType(
                brand_id=brand.id,
                name=type_name,
                slug=unique_slug(db, models.ChairType, type_name),
            )
            db.add(chair_type)
            db.flush()
            for area in db.scalars(select(models.BodyArea)).all():
                db.add(
                    models.ChairTypeBodyArea(
                        chair_type_id=chair_type.id,
                        body_area_id=area.id,
                        support_status="belum_dikonfirmasi",
                    )
                )
    operator = get_or_create_named(db, models.Operator, placement_data.get("operator_name"))

    placement = models.Placement(
        spot_id=spot.id,
        brand_id=brand.id if brand else None,
        chair_type_id=chair_type.id if chair_type else None,
        operator_id=operator.id if operator else None,
        quantity=int(placement_data["quantity"]) if placement_data.get("quantity") else None,
        quantity_basis=placement_data.get("quantity_basis") or None,
        cost_type=placement_data.get("cost_type") or "belum_diketahui",
        tariff_type=placement_data.get("tariff_type") or None,
        price_idr=int(placement_data["price_idr"]) if placement_data.get("price_idr") else None,
        duration_minutes=(
            int(placement_data["duration_minutes"]) if placement_data.get("duration_minutes") else None
        ),
        price_is_starting_from=bool(placement_data.get("price_is_starting_from")),
        payment_methods=";".join(placement_data.get("payment_methods") or []),
        effective_from=parse_date(payload.get("verification", {}).get("checked_date")) or date.today(),
        is_published=True,
    )
    db.add(placement)
    db.flush()

    verification = payload.get("verification") or {}
    db.add(
        models.VerificationLog(
            spot_id=spot.id,
            placement_id=placement.id,
            checked_by_id=change_request.submitter_id,
            source_title=verification.get("source_title") or None,
            source_url=verification.get("source_url") or None,
            source_type=verification.get("source_type") or None,
            source_published_date=parse_date(verification.get("source_published_date")),
            checked_date=parse_date(verification.get("checked_date")) or date.today(),
            check_method=verification.get("check_method") or "riset_online",
            confidence=verification.get("confidence") or "sedang",
            fields_supported=verification.get("fields_supported") or None,
            notes=verification.get("research_notes") or None,
        )
    )

    photo_data = payload.get("photo") or {}
    if change_request.staged_photo_name:
        db.add(
            models.Photo(
                spot_id=spot.id,
                storage_name=change_request.staged_photo_name,
                original_name=photo_data.get("original_name"),
                intended_use=photo_data.get("intended_use") or "bukti_internal",
                caption=photo_data.get("caption") or None,
                license_note=photo_data.get("license_note") or None,
                status="approved",
                uploaded_by_id=change_request.submitter_id,
            )
        )

    audit(db, admin.id, "spot", spot.id, "change_request_applied", {"change_request_id": change_request.id})
    return spot, placement


def apply_spot_update(db: Session, change_request: models.ChangeRequest, admin: models.User, change_kind: str):
    payload = json.loads(change_request.payload_json)
    spot = change_request.target_spot
    if spot is None:
        raise ValueError("Target titik tidak ditemukan")
    if change_request.base_version and spot.version != change_request.base_version:
        raise ValueError("Data titik sudah berubah sejak usulan dibuat. Minta petugas mengajukan ulang.")
    changes = payload.get("changes") or {}
    old = {
        "floor": spot.floor,
        "zone_landmark": spot.zone_landmark,
        "recommended_entrance": spot.recommended_entrance,
        "walking_directions": spot.walking_directions,
        "access_notes": spot.access_notes,
        "existence_status": spot.existence_status,
        "access_groups": sorted(spot.access_codes),
    }
    for field in ["floor", "zone_landmark", "recommended_entrance", "walking_directions", "access_notes", "existence_status"]:
        if field in changes:
            setattr(spot, field, changes[field] or (None if field not in {"floor", "zone_landmark", "existence_status"} else getattr(spot, field)))
    if "access_groups" in changes:
        codes = changes["access_groups"] or ["umum"]
        spot.access_groups = list(
            db.scalars(select(models.AccessGroup).where(models.AccessGroup.code.in_(codes))).all()
        )
    if payload.get("checked_date"):
        spot.last_verified_at = parse_date(payload["checked_date"])
    if payload.get("check_method"):
        spot.verification_method = payload["check_method"]
    spot.version += 1

    db.add(
        models.VerificationLog(
            spot_id=spot.id,
            checked_by_id=change_request.submitter_id,
            source_title=payload.get("source_title") or None,
            source_url=payload.get("source_url") or None,
            source_type=payload.get("source_type") or None,
            checked_date=parse_date(payload.get("checked_date")) or date.today(),
            check_method=payload.get("check_method") or "riset_online",
            confidence=payload.get("confidence") or "sedang",
            fields_supported=payload.get("fields_supported") or None,
            notes=payload.get("notes") or None,
        )
    )
    if change_kind == "lapangan":
        changed_fields = [key for key in changes if str(old.get(key)) != str(changes.get(key))]
        label_map = {
            "floor": "lantai",
            "zone_landmark": "patokan",
            "recommended_entrance": "pintu masuk yang disarankan",
            "walking_directions": "petunjuk berjalan",
            "access_notes": "catatan akses",
            "existence_status": "status keberadaan",
            "access_groups": "kelompok akses",
        }
        changed_labels = ", ".join(label_map.get(x, x) for x in changed_fields) or "data titik"
        if changes.get("existence_status") == "tidak_ada":
            summary = "Kursi terakhir terkonfirmasi sudah tidak ada pada titik ini."
        elif changes.get("existence_status") == "ada":
            summary = "Kursi terkonfirmasi masih ada pada titik ini."
        else:
            summary = f"Perubahan nyata tercatat pada {changed_labels}."
        db.add(
            models.PublicChangeEvent(
                spot_id=spot.id,
                event_type="spot_update",
                summary=summary,
                effective_date=parse_date(payload.get("checked_date")) or date.today(),
                change_request_id=change_request.id,
            )
        )
    audit(
        db,
        admin.id,
        "spot",
        spot.id,
        "change_request_applied",
        {"change_request_id": change_request.id, "change_kind": change_kind, "before": old, "after": changes},
    )
    return spot
