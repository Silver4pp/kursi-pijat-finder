import json
import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

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


def purge_expired_auth_records(db: Session) -> int:
    """Delete expired login sessions and anonymous subjects idle for a year.

    Anonymous subjects that still own ratings are kept so moderation history
    stays intact. Returns the number of removed records.
    """
    now = datetime.now(timezone.utc)
    removed = 0

    for session in db.scalars(select(models.Session)).all():
        expires = session.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < now:
            db.delete(session)
            removed += 1

    cutoff = now - timedelta(days=365)
    for subject in db.scalars(select(models.AnonymousSubject)).all():
        last_seen = subject.last_seen_at
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        if last_seen < cutoff and not subject.ratings:
            db.delete(subject)
            removed += 1

    db.commit()
    return removed


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


def _public_type_placements(db: Session, chair_type_id) -> list[models.Placement]:
    """Placements of one chair type that public pages are allowed to show."""
    return list(
        db.scalars(
            select(models.Placement)
            .join(models.Spot)
            .join(models.Venue)
            .where(
                models.Placement.chair_type_id == chair_type_id,
                models.Placement.is_published.is_(True),
                models.Placement.effective_to.is_(None),
                models.Spot.is_published.is_(True),
                models.Spot.existence_status == "ada",
                models.Venue.is_published.is_(True),
            )
        ).all()
    )


def rating_summary_for_placement(db: Session, placement: models.Placement):
    """Return per-area location and same-type summaries following the 5-rater rule."""
    if not placement.chair_type:
        return []
    local = summarize_scores(_approved_scores(db, [placement]))
    type_placements = _public_type_placements(db, placement.chair_type_id)
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
    placements = _public_type_placements(db, chair_type.id)
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


def _coordinate(value, field: str) -> float:
    """Parse a coordinate that may use a decimal comma."""
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Koordinat {field} tidak valid") from exc


def _resolve_access_groups(db: Session, codes) -> list[models.AccessGroup]:
    """Map access codes to groups and reject codes that no longer exist."""
    wanted = [str(code) for code in codes if code]
    groups = list(
        db.scalars(select(models.AccessGroup).where(models.AccessGroup.code.in_(wanted))).all()
    )
    if not groups:
        raise ValueError("Kelompok akses tidak dikenali: " + ", ".join(wanted))
    return groups


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
            latitude=_coordinate(venue_data["latitude"], "lintang"),
            longitude=_coordinate(venue_data["longitude"], "bujur"),
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
    spot.access_groups = _resolve_access_groups(db, access_codes)
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
        spot.access_groups = _resolve_access_groups(db, codes)
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


def active_placements(spot: models.Spot):
    return [p for p in spot.placements if p.is_published and p.effective_to is None]


def serialize_placement(placement: models.Placement):
    return {
        "id": placement.id,
        "brand": placement.brand.name if placement.brand else "Merk belum terkonfirmasi",
        "type": placement.chair_type.name if placement.chair_type else "Tipe belum terkonfirmasi",
        "operator": placement.operator.name if placement.operator else None,
        "quantity": placement.quantity,
        "price": format_rupiah(placement.price_idr),
        "duration": placement.duration_minutes,
        "payments": [PAYMENT_LABELS.get(x, x) for x in placement.payment_codes],
        "can_rate": placement.can_rate,
        "cost_label": COST_LABELS.get(placement.cost_type, placement.cost_type),
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


@dataclass
class SearchResult:
    spots: list[models.Spot]
    distances: dict[str, float] = field(default_factory=dict)
    hidden_unknown_qty: int = 0

    def distance_for(self, spot: models.Spot):
        return self.distances.get(spot.venue_id)


def filter_spots(db: Session, params: dict) -> SearchResult:
    # City, category, and existence filters run in SQL; the remaining filters
    # operate on placement payloads and stay in Python for exact semantics.
    stmt = (
        select(models.Spot)
        .join(models.Venue)
        .where(
            models.Spot.is_published.is_(True),
            models.Venue.is_published.is_(True),
            models.Spot.existence_status == "ada",
        )
        .options(
            selectinload(models.Spot.venue),
            selectinload(models.Spot.placements),
            selectinload(models.Spot.access_groups),
        )
    )
    if params.get("city"):
        stmt = stmt.where(models.Venue.admin_city == params["city"])
    if params.get("category"):
        stmt = stmt.where(models.Venue.category == params["category"])
    spots = list(db.scalars(stmt).unique().all())

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
        matching_placements = [
            p for p in placements
            if (not params.get("brand_id") or p.brand_id == params["brand_id"])
            and (not params.get("payment") or params["payment"] in p.payment_codes)
        ]
        if (params.get("brand_id") or params.get("payment")) and not matching_placements:
            continue
        if access_filter and access_filter not in spot.access_codes:
            continue
        searched_venue_directly = bool(q and q in spot.venue.name.lower())
        if spot.is_private_only and not access_filter and not searched_venue_directly:
            continue
        if min_qty is not None:
            known = [p.quantity for p in matching_placements if p.quantity is not None]
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
                distances[spot.venue_id] = haversine_km(
                    user_lat, user_lng, spot.venue.latitude, spot.venue.longitude
                )
            matched.sort(key=lambda s: distances.get(s.venue_id, 9999))
        except ValueError:
            distances = {}
    return SearchResult(spots=matched, distances=distances, hidden_unknown_qty=hidden_unknown_qty)
