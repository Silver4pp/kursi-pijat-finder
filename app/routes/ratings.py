"""Rating submission, correction, deletion, and personal rating history."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import config, models, services
from ..db import get_db
from ..web import (
    ANON_COOKIE,
    check_csrf,
    client_ip,
    get_anon_subject,
    get_current_user,
    rating_limiter,
    redirect_with_flash,
    render,
    require_user,
    set_cookie,
)

router = APIRouter()


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


def render_rating_page(request: Request, db: Session, placement, *, error=None, submitted=None):
    user = get_current_user(request, db)
    anon_subject, _ = get_anon_subject(request, db, create=False)
    own_rating = find_rating_for_subject(db, placement.id, user, anon_subject)
    submitted_scores = {}
    if submitted is not None:
        for key, value in submitted.items():
            if key.startswith("score_") and str(value) in {"1", "2", "3", "4", "5"}:
                submitted_scores[key.removeprefix("score_")] = int(value)
    return render(
        request,
        "rating.html",
        {
            "placement": placement,
            "summary": services.rating_summary_for_placement(db, placement),
            "own_rating": own_rating,
            "rating_unavailable": False,
            "today": date.today(),
            "rating_error": error,
            "submitted": submitted,
            "submitted_scores": submitted_scores,
        },
        db,
        status_code=400 if error else 200,
    )


@router.get("/nilai/{placement_id}")
def rating_page(placement_id: str, request: Request, db: Session = Depends(get_db)):
    placement = db.get(models.Placement, placement_id)
    if (
        not placement
        or not placement.is_published
        or not placement.spot.is_published
        or not placement.spot.venue.is_published
    ):
        raise HTTPException(404, "Penempatan kursi tidak ditemukan")
    if not placement.can_rate:
        return render(
            request,
            "rating.html",
            {"placement": placement, "summary": [], "own_rating": None, "rating_unavailable": True},
            db,
        )
    return render_rating_page(request, db, placement)


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


@router.post("/nilai/{placement_id}")
async def submit_rating(placement_id: str, request: Request, db: Session = Depends(get_db)):
    placement = db.get(models.Placement, placement_id)
    if (
        not placement
        or not placement.is_published
        or not placement.spot.is_published
        or not placement.spot.venue.is_published
        or not placement.can_rate
    ):
        raise HTTPException(404, "Rating untuk penempatan ini belum dibuka")
    if config.RATE_LIMIT_ENABLED and not rating_limiter.allow(f"rating:{client_ip(request)}"):
        return render_rating_page(
            request, db, placement, error="Terlalu banyak pengiriman rating. Coba lagi nanti."
        )
    try:
        usage_date, scores = await parse_rating_scores(request, placement)
    except HTTPException as exc:
        if exc.status_code != 400:
            raise
        return render_rating_page(
            request, db, placement, error=exc.detail, submitted=dict(await request.form())
        )
    user = get_current_user(request, db)
    anon_subject, raw_anon = (None, None)
    if not user:
        anon_subject, raw_anon = get_anon_subject(request, db, create=True)
    existing = find_rating_for_subject(db, placement.id, user, anon_subject)
    if existing:
        return redirect_with_flash(
            f"/nilai/{placement.id}",
            "Anda sudah mempunyai rating untuk kursi di lokasi ini. Gunakan fitur koreksi.",
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
        db.add(
            models.RatingAreaScore(rating_version_id=version.id, body_area_id=area_id, stars=stars)
        )
    try:
        db.commit()
    except IntegrityError:
        # Two concurrent submissions raced the duplicate check; the unique
        # constraint kept only one, so treat this as a duplicate rating.
        db.rollback()
        return redirect_with_flash(
            f"/nilai/{placement.id}",
            "Anda sudah mempunyai rating untuk kursi di lokasi ini. Gunakan fitur koreksi.",
        )
    response = redirect_with_flash(
        f"/nilai/{placement.id}", "Rating terkirim dan menunggu pemeriksaan admin sebelum dihitung."
    )
    if raw_anon:
        set_cookie(response, ANON_COOKIE, raw_anon, max_age=60 * 60 * 24 * 365)
    return response


@router.post("/ratings/{rating_id}/koreksi")
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
    if (
        not placement.is_published
        or not placement.spot.is_published
        or not placement.spot.venue.is_published
    ):
        raise HTTPException(404, "Rating untuk penempatan ini belum dibuka")
    try:
        usage_date, scores = await parse_rating_scores(request, placement)
    except HTTPException as exc:
        if exc.status_code != 400:
            raise
        return render_rating_page(
            request, db, placement, error=exc.detail, submitted=dict(await request.form())
        )

    pending = rating.pending_version
    if pending and not rating.active_version:
        pending.usage_date = usage_date
        for old in list(pending.scores):
            db.delete(old)
        db.flush()
        for area_id, stars in scores:
            db.add(
                models.RatingAreaScore(
                    rating_version_id=pending.id, body_area_id=area_id, stars=stars
                )
            )
        db.commit()
        return redirect_with_flash(
            f"/nilai/{placement.id}", "Rating yang masih menunggu pemeriksaan telah diperbarui."
        )
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
        db.add(
            models.RatingAreaScore(rating_version_id=version.id, body_area_id=area_id, stars=stars)
        )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return redirect_with_flash(
            f"/nilai/{placement.id}", "Koreksi sebelumnya masih menunggu pemeriksaan."
        )
    return redirect_with_flash(
        f"/nilai/{placement.id}",
        "Koreksi terkirim. Rating lama tetap dihitung sampai koreksi disetujui.",
    )


@router.post("/ratings/{rating_id}/hapus")
async def delete_rating(rating_id: str, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    check_csrf(request, str(form.get("csrf_token") or ""))
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
    placement_id = rating.placement_id
    services.audit(
        db,
        user.id if user else None,
        "rating",
        rating.id,
        "rating_deleted",
        {"placement_id": placement_id},
    )
    db.delete(rating)
    db.commit()
    return redirect_with_flash(
        f"/nilai/{placement_id}", "Rating Anda dihapus dan tidak lagi dihitung."
    )


@router.get("/akun/rating-saya")
def my_ratings(request: Request, db: Session = Depends(get_db)):
    user, response = require_user(request, db)
    if response:
        return response
    ratings = list(
        db.scalars(
            select(models.Rating)
            .where(models.Rating.user_id == user.id)
            .order_by(models.Rating.updated_at.desc())
        ).all()
    )
    return render(request, "my_ratings.html", {"ratings": ratings}, db)
