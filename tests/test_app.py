import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, models, services
from app.db import SessionLocal
from app.main import app


def test_main_can_be_run_as_a_script_from_another_directory(tmp_path):
    script = Path(__file__).resolve().parents[1] / "app" / "main.py"
    code = (
        "import runpy, sys; from unittest.mock import patch; "
        "exec('with patch(\\\"uvicorn.run\\\") as run:\\n"
        " runpy.run_path(sys.argv[1], run_name=\\\"__main__\\\")\\n"
        " assert run.call_count == 1')"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'direct.db'}"},
    )
    assert result.returncode == 0, result.stderr


def csrf(client: TestClient) -> str:
    return client.cookies.get("kpf_csrf") or ""


def login(client: TestClient, email: str, password: str):
    client.get("/masuk")
    response = client.post(
        "/masuk",
        data={"csrf_token": csrf(client), "email": email, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response


def placement_id_by_spot(spot_slug: str):
    with SessionLocal() as db:
        return db.scalar(
            select(models.Placement.id)
            .join(models.Spot)
            .where(models.Spot.slug == spot_slug)
        )


def body_area_id(code: str):
    with SessionLocal() as db:
        return db.scalar(select(models.BodyArea.id).where(models.BodyArea.code == code))


def test_home_includes_polished_explorer_controls():
    with TestClient(app) as client:
        page = client.get("/").text
        assert 'class="hero home-hero"' in page
        assert 'class="filter-details"' in page
        assert 'class="search-line"' in page
        assert 'data-view-target="map"' in page
        assert 'data-view-target="list"' in page
        assert 'id="map-loading"' in page
        assert 'Leaflet.markercluster/leaflet.markercluster.js' in page
        assert 'Lingkaran: jumlah gedung' in page
        assert 'id="search-status"' in page
        assert 'class="social-sidebar"' in page
        assert 'id="explorer" data-view="list"' in page
        assert 'Terapkan filter' in page
        assert 'bukan kursi yang sedang kosong' in page
        assert 'data-view-target="list" aria-pressed="true"' in page


def test_shared_navigation_on_public_and_admin_pages():
    with TestClient(app) as client:
        for path in ("/", "/merk", "/tentang-data", "/masuk", "/daftar", "/venue/stasiun-demo-jakarta"):
            page = client.get(path)
            assert page.status_code == 200
            assert 'class="social-sidebar"' in page.text
            assert 'href="/#cari"' not in page.text
            assert 'href="/#explorer"' not in page.text
            assert 'href="/merk"' in page.text
            assert 'href="/tentang-data"' in page.text
        assert 'href="/merk" aria-current="page"' in client.get("/merk").text

        login(client, "admin@example.com", "Admin123!")
        admin_page = client.get("/admin")
        assert admin_page.status_code == 200
        assert 'href="/admin" aria-current="page"' in admin_page.text
        assert 'action="/keluar"' in admin_page.text


def test_brand_directory_keeps_rating_counts_and_links():
    with TestClient(app) as client:
        page = client.get("/merk")
        assert page.status_code == 200
        assert "type-rating-list" in page.text
        assert "4,5" in page.text
        assert "6 penilai" in page.text
        assert "penilai minimum" in page.text
        assert "/merk/merk-aruna-contoh/tipe-punggung-satu" in page.text
        assert "5 penilai yang disetujui" in page.text


def test_private_facility_hidden_until_direct_search_or_access_filter():
    with TestClient(app) as client:
        default_names = [v["name"] for v in client.get("/api/v1/venues").json()["venues"]]
        assert "Gedung Kantor Demo" not in default_names

        direct_names = [
            v["name"]
            for v in client.get("/api/v1/venues", params={"q": "Gedung Kantor Demo"}).json()["venues"]
        ]
        assert "Gedung Kantor Demo" in direct_names

        access_names = [
            v["name"]
            for v in client.get("/api/v1/venues", params={"access": "karyawan"}).json()["venues"]
        ]
        assert "Gedung Kantor Demo" in access_names


def test_rating_threshold_and_unsupported_areas():
    with TestClient(app) as client:
        back_only = client.get(f"/nilai/{placement_id_by_spot('area-tunggu-utara')}").text
        assert "4,5" in back_only
        assert "berdasarkan 6 penilai" in back_only
        assert back_only.count("Tidak ada fitur") == 6
        assert len(re.findall(r'name="score_', back_only)) == 6  # five stars plus an explicit unrated choice

        full_body = client.get(f"/nilai/{placement_id_by_spot('area-makan-selatan')}").text
        assert "Lokasi ini mempunyai 3 penilai" in full_body


def test_anonymous_rating_moderation_and_duplicate_prevention():
    placement_id = placement_id_by_spot("area-makan-selatan")
    with TestClient(app) as anonymous:
        page = anonymous.get(f"/nilai/{placement_id}")
        assert "score_" in page.text
        payload = {
            "csrf_token": csrf(anonymous),
            "usage_date": "2026-11-02",
            f"score_{body_area_id('punggung')}": "5",
        }
        submitted = anonymous.post(
            f"/nilai/{placement_id}", data=payload, follow_redirects=True
        )
        assert "menunggu pemeriksaan admin" in submitted.text
        assert "Lokasi ini mempunyai 3 penilai" in anonymous.get(f"/nilai/{placement_id}").text

        duplicate = anonymous.post(
            f"/nilai/{placement_id}", data=payload, follow_redirects=True
        )
        assert "sudah mempunyai rating" in duplicate.text

    with TestClient(app) as admin:
        login(admin, "admin@example.com", "Admin123!")
        queue = admin.get("/admin").text
        version_id = re.search(r"/admin/rating/([^/]+)/keputusan", queue).group(1)
        approved = admin.post(
            f"/admin/rating/{version_id}/keputusan",
            data={"csrf_token": csrf(admin), "action": "approve", "review_note": "Lolos uji"},
            follow_redirects=True,
        )
        assert "mulai dihitung" in approved.text

    with TestClient(app) as public:
        page = public.get(f"/nilai/{placement_id}").text
        assert "Lokasi ini mempunyai 4 penilai" in page


def test_spot_update_requires_admin_approval_before_public_change():
    with SessionLocal() as db:
        spot = db.scalar(select(models.Spot).where(models.Spot.slug == "area-tunggu-utara"))
        spot_id = spot.id
        original_landmark = spot.zone_landmark

    new_landmark = "Di dekat papan informasi keberangkatan contoh"
    with TestClient(app) as petugas:
        login(petugas, "petugas@example.com", "Petugas123!")
        form_page = petugas.get(f"/internal/titik/{spot_id}/usulkan")
        assert form_page.status_code == 200
        response = petugas.post(
            f"/internal/titik/{spot_id}/usulkan",
            data={
                "csrf_token": csrf(petugas),
                "floor": "1",
                "zone_landmark": new_landmark,
                "recommended_entrance": "Pintu Utara",
                "walking_directions": "Masuk dari Pintu Utara dan menuju papan informasi.",
                "access_notes": "Memerlukan tiket perjalanan.",
                "existence_status": "ada",
                "access_groups": ["penumpang_bertiket"],
                "checked_date": "2026-11-03",
                "check_method": "kunjungan",
                "confidence": "kuat",
                "source_title": "Kunjungan pengujian lokal",
                "source_url": "",
                "source_type": "kunjungan",
                "fields_supported": "patokan;keberadaan",
                "notes": "Perubahan patokan pada pengujian.",
            },
            follow_redirects=True,
        )
        assert "menunggu pemeriksaan admin" in response.text

    with SessionLocal() as db:
        assert db.get(models.Spot, spot_id).zone_landmark == original_landmark
        change_id = db.scalar(
            select(models.ChangeRequest)
            .where(models.ChangeRequest.kind == "spot_update")
            .order_by(models.ChangeRequest.created_at.desc())
        ).id

    with TestClient(app) as admin:
        login(admin, "admin@example.com", "Admin123!")
        detail = admin.get(f"/admin/usulan/{change_id}")
        assert new_landmark in detail.text
        response = admin.post(
            f"/admin/usulan/{change_id}/keputusan",
            data={
                "csrf_token": csrf(admin),
                "action": "approve",
                "change_kind": "lapangan",
                "review_note": "Sesuai bukti pengujian.",
            },
            follow_redirects=True,
        )
        assert "data publik diperbarui" in response.text

    with SessionLocal() as db:
        spot = db.get(models.Spot, spot_id)
        assert spot.zone_landmark == new_landmark
        assert spot.public_events


def test_create_location_proposal_and_admin_publication():
    with SessionLocal() as db:
        chair_type = db.scalar(
            select(models.ChairType).where(models.ChairType.name == "Tipe Punggung Satu")
        )
        brand_id = chair_type.brand_id
        chair_type_id = chair_type.id

    with TestClient(app) as petugas:
        login(petugas, "petugas@example.com", "Petugas123!")
        petugas.get("/internal/usulan-baru")
        response = petugas.post(
            "/internal/usulan-baru",
            data={
                "csrf_token": csrf(petugas),
                "existing_venue_id": "",
                "venue_name": "Terminal Demo Timur",
                "venue_category": "lainnya",
                "address": "Jl. Contoh Timur No. 9",
                "admin_city": "Jakarta Timur",
                "district": "Cakung (Contoh)",
                "latitude": "-6.2100",
                "longitude": "106.9000",
                "opening_hours": "Setiap hari 05.00-22.00 (contoh)",
                "spot_name": "Ruang tunggu utama",
                "floor": "1",
                "zone_landmark": "Di dekat loket contoh",
                "recommended_entrance": "Pintu Utama",
                "walking_directions": "Masuk dan berjalan ke ruang tunggu.",
                "access_notes": "",
                "existence_status": "ada",
                "access_groups": ["umum"],
                "brand_id": brand_id,
                "brand_name": "",
                "chair_type_id": chair_type_id,
                "chair_type_name": "",
                "operator_name": "Relaks Demo",
                "quantity": "2",
                "quantity_basis": "kunjungan",
                "cost_type": "berbayar_per_sesi",
                "tariff_type": "paket_tetap",
                "price_idr": "12000",
                "duration_minutes": "10",
                "payment_methods": ["qris"],
                "source_title": "Pengujian lokal",
                "source_type": "kunjungan",
                "source_url": "",
                "source_published_date": "",
                "checked_date": "2026-11-03",
                "check_method": "kunjungan",
                "confidence": "kuat",
                "fields_supported": "keberadaan;lantai;patokan",
                "research_notes": "Data pengujian.",
                "photo_intended_use": "bukti_internal",
                "photo_caption": "",
                "photo_license_note": "",
            },
            follow_redirects=True,
        )
        assert "menunggu pemeriksaan admin" in response.text

    with TestClient(app) as public:
        names = [v["name"] for v in public.get("/api/v1/venues").json()["venues"]]
        assert "Terminal Demo Timur" not in names

    with SessionLocal() as db:
        change_id = db.scalar(
            select(models.ChangeRequest)
            .where(models.ChangeRequest.kind == "create_location")
            .order_by(models.ChangeRequest.created_at.desc())
        ).id

    with TestClient(app) as admin:
        login(admin, "admin@example.com", "Admin123!")
        response = admin.post(
            f"/admin/usulan/{change_id}/keputusan",
            data={
                "csrf_token": csrf(admin),
                "action": "approve",
                "review_note": "Disetujui untuk pengujian lokal.",
            },
            follow_redirects=True,
        )
        assert "data publik diperbarui" in response.text

    with TestClient(app) as public:
        names = [v["name"] for v in public.get("/api/v1/venues").json()["venues"]]
        assert "Terminal Demo Timur" in names


def approve_latest_rating_for_user(admin: TestClient, email: str):
    with SessionLocal() as db:
        version_id = db.scalar(
            select(models.RatingVersion.id)
            .join(models.Rating)
            .join(models.User, models.User.id == models.Rating.user_id)
            .where(models.User.email == email, models.RatingVersion.status == "pending")
            .order_by(models.RatingVersion.created_at.desc())
        )
    response = admin.post(
        f"/admin/rating/{version_id}/keputusan",
        data={"csrf_token": csrf(admin), "action": "approve", "review_note": "Lolos uji"},
        follow_redirects=True,
    )
    assert "mulai dihitung" in response.text


def test_rating_correction_replaces_score_without_adding_a_rater():
    placement_id = placement_id_by_spot("area-tunggu-utara")
    punggung_id = body_area_id("punggung")

    with TestClient(app) as user:
        login(user, "user@example.com", "User123!")
        user.get(f"/nilai/{placement_id}")
        submitted = user.post(
            f"/nilai/{placement_id}",
            data={
                "csrf_token": csrf(user),
                "usage_date": "2026-11-02",
                f"score_{punggung_id}": "3",
            },
            follow_redirects=True,
        )
        assert "menunggu pemeriksaan admin" in submitted.text

    with TestClient(app) as admin:
        login(admin, "admin@example.com", "Admin123!")
        approve_latest_rating_for_user(admin, "user@example.com")

    with TestClient(app) as public:
        page = public.get(f"/nilai/{placement_id}").text
        assert "4,3" in page
        assert "berdasarkan 7 penilai" in page

    with TestClient(app) as user:
        login(user, "user@example.com", "User123!")
        page = user.get(f"/nilai/{placement_id}").text
        rating_id = re.search(r"/ratings/([^/]+)/koreksi", page).group(1)
        corrected = user.post(
            f"/ratings/{rating_id}/koreksi",
            data={
                "csrf_token": csrf(user),
                "usage_date": "2026-11-02",
                f"score_{punggung_id}": "5",
            },
            follow_redirects=True,
        )
        assert "Rating lama tetap dihitung" in corrected.text

    with TestClient(app) as public:
        assert "4,3" in public.get(f"/nilai/{placement_id}").text

    with TestClient(app) as admin:
        login(admin, "admin@example.com", "Admin123!")
        approve_latest_rating_for_user(admin, "user@example.com")

    with TestClient(app) as public:
        page = public.get(f"/nilai/{placement_id}").text
        assert "4,6" in page
        assert "berdasarkan 7 penilai" in page


def test_search_keeps_nearby_coordinates_when_filters_are_submitted():
    with TestClient(app) as client:
        page = client.get("/", params={"lat": "-6.200000", "lng": "106.820000", "q": "mall"})
        assert page.status_code == 200
        assert 'name="lat" value="-6.200000"' in page.text
        assert 'name="lng" value="106.820000"' in page.text
        assert "Diurutkan dari lokasi terdekat" in page.text
        assert "1 aktif" in page.text


def test_invalid_rating_and_correction_return_to_form_and_preserve_values():
    placement_id = placement_id_by_spot("area-tunggu-utara")
    area_id = body_area_id("punggung")
    with TestClient(app) as client:
        client.get(f"/nilai/{placement_id}")
        payload = {"csrf_token": csrf(client), "usage_date": "2026-11-02"}
        invalid = client.post(f"/nilai/{placement_id}", data=payload)
        assert invalid.status_code == 400
        assert "Beri bintang untuk sedikitnya satu area" in invalid.text
        assert 'value="2026-11-02"' in invalid.text
        assert "rating-form" in invalid.text
        assert "application/json" not in invalid.headers["content-type"]

        invalid_date = client.post(f"/nilai/{placement_id}", data={**payload, "usage_date": "2026-10-01", f"score_{area_id}": "4"})
        assert invalid_date.status_code == 400
        assert "sebelum penempatan ini" in invalid_date.text
        assert re.search(rf'id="new-{area_id}-4"[^>]*checked', invalid_date.text)

        submitted = client.post(f"/nilai/{placement_id}", data={**payload, f"score_{area_id}": "4"}, follow_redirects=True)
        rating_id = re.search(r"/ratings/([^/]+)/koreksi", submitted.text).group(1)
        correction = client.post(f"/ratings/{rating_id}/koreksi", data=payload)
        assert correction.status_code == 400
        assert "Beri bintang untuk sedikitnya satu area" in correction.text
        assert 'value="2026-11-02"' in correction.text
        assert re.search(rf'id="correct-{area_id}-0"[^>]*checked', correction.text)

        rejected_csrf = client.post(f"/nilai/{placement_id}", data={**payload, "csrf_token": "wrong"})
        assert rejected_csrf.status_code == 403


def test_hidden_placements_cannot_receive_public_ratings():
    placement_id = placement_id_by_spot("area-tunggu-utara")
    with SessionLocal() as db:
        placement = db.get(models.Placement, placement_id)
        placement.is_published = False
        db.commit()
    try:
        with TestClient(app) as client:
            client.get("/")
            assert client.get(f"/nilai/{placement_id}").status_code == 404
            response = client.post(f"/nilai/{placement_id}", data={"csrf_token": csrf(client), "usage_date": "2026-11-02", f"score_{body_area_id('punggung')}": "5"})
            assert response.status_code == 404
    finally:
        with SessionLocal() as db:
            db.get(models.Placement, placement_id).is_published = True
            db.commit()


def test_combined_placement_filters_match_the_same_chairs():
    with SessionLocal() as db:
        spot = db.scalar(select(models.Spot).where(models.Spot.slug == "area-tunggu-utara"))
        other_type = db.scalar(select(models.ChairType).where(models.ChairType.name == "Tipe Punggung dan Kaki"))
        first_placement = spot.placements[0]
        venue_id = spot.venue_id
        original_brand_id = first_placement.brand_id
        other_brand_id = other_type.brand_id
        extra = models.Placement(spot_id=spot.id, brand_id=other_type.brand_id, chair_type_id=other_type.id,
                                 quantity=5, payment_methods="e_money", cost_type="berbayar_per_sesi")
        db.add(extra)
        db.commit()
        extra_id = extra.id
    try:
        with TestClient(app) as client:
            def slugs(**params):
                result = client.get(f"/api/v1/venues/{venue_id}/spots", params=params).json()
                return [spot["slug"] for spot in result["spots"]]

            assert "area-tunggu-utara" not in slugs(brand_id=other_brand_id, payment="qris")
            assert "area-tunggu-utara" not in slugs(brand_id=original_brand_id, payment="qris", min_qty="4")
            assert "area-tunggu-utara" in slugs(brand_id=other_brand_id, payment="e_money", min_qty="5")
    finally:
        with SessionLocal() as db:
            db.delete(db.get(models.Placement, extra_id))
            db.commit()


def test_health_and_robots():
    with TestClient(app) as client:
        assert client.get("/health").json()["database"] == "ok"
        robots = client.get("/robots.txt")
        assert robots.status_code == 200
        assert "Disallow: /admin" in robots.text


def test_login_rate_limit_blocks_repeated_attempts(monkeypatch):
    monkeypatch.setattr("app.config.RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr("app.web.login_limiter.max_events", 0)
    with TestClient(app) as client:
        client.get("/masuk")
        blocked = client.post(
            "/masuk",
            data={"csrf_token": csrf(client), "email": "admin@example.com", "password": "Admin123!"},
        )
        assert blocked.status_code == 429
        assert "Terlalu banyak percobaan masuk" in blocked.text


def test_register_rejects_mismatched_or_weak_password():
    with TestClient(app) as client:
        client.get("/daftar")
        base = {"csrf_token": csrf(client), "name": "Penguji Daftar", "email": "penguji@example.com"}
        mismatch = client.post("/daftar", data={**base, "password": "Sandal99", "confirm": "Sandal98"})
        assert mismatch.status_code == 400
        assert "Konfirmasi kata sandi tidak sama" in mismatch.text

        weak = client.post("/daftar", data={**base, "password": "sandalyangpanjang", "confirm": "sandalyangpanjang"})
        assert weak.status_code == 400
        assert "huruf dan angka" in weak.text

        ok = client.post("/daftar", data={**base, "password": "Sandal99", "confirm": "Sandal99"}, follow_redirects=False)
        assert ok.status_code == 303


def test_unpublished_venue_hides_spot_detail_and_type_listing():
    with SessionLocal() as db:
        venue = db.scalar(select(models.Venue).where(models.Venue.slug == "mall-demo-selatan"))
        venue.is_published = False
        db.commit()
    try:
        with TestClient(app) as client:
            assert client.get("/venue/mall-demo-selatan").status_code == 404
            assert client.get("/venue/mall-demo-selatan/titik/dekat-eskalator-timur").status_code == 404
            names = [v["name"] for v in client.get("/api/v1/venues").json()["venues"]]
            assert "Mall Demo Selatan" not in names
            type_page = client.get("/merk/merk-aruna-contoh/tipe-seluruh-badan-dua")
            assert type_page.status_code == 200
            assert "Mall Demo Selatan" not in type_page.text
    finally:
        with SessionLocal() as db:
            venue = db.scalar(select(models.Venue).where(models.Venue.slug == "mall-demo-selatan"))
            venue.is_published = True
            db.commit()


def test_create_proposal_rejects_unknown_existence_status():
    with SessionLocal() as db:
        venue_id = db.scalar(select(models.Venue.id).where(models.Venue.slug == "mall-demo-selatan"))
    with TestClient(app) as petugas:
        login(petugas, "petugas@example.com", "Petugas123!")
        petugas.get("/internal/usulan-baru")
        response = petugas.post(
            "/internal/usulan-baru",
            data={
                "csrf_token": csrf(petugas),
                "existing_venue_id": venue_id,
                "venue_name": "",
                "spot_name": "Titik uji status",
                "floor": "1",
                "zone_landmark": "Di dekat uji status",
                "existence_status": "entahlah",
            },
        )
        assert response.status_code == 400
        assert "Status keberadaan tidak dikenali" in response.text


def test_rejected_proposal_discards_staged_photo():
    from io import BytesIO

    from PIL import Image as PILImage

    from app.db import UPLOAD_DIR

    buffer = BytesIO()
    PILImage.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    photo_bytes = buffer.getvalue()

    with TestClient(app) as petugas:
        login(petugas, "petugas@example.com", "Petugas123!")
        petugas.get("/internal/usulan-baru")
        response = petugas.post(
            "/internal/usulan-baru",
            data={
                "csrf_token": csrf(petugas),
                "existing_venue_id": "",
                "venue_name": "Venue Uji Foto",
                "venue_category": "lainnya",
                "address": "Jl. Uji Foto No. 1",
                "admin_city": "Jakarta Pusat",
                "latitude": "-6.1900",
                "longitude": "106.8300",
                "spot_name": "Titik Uji Foto",
                "floor": "1",
                "zone_landmark": "Di dekat loket uji",
                "photo_intended_use": "bukti_internal",
            },
            files={"photo": ("contoh.png", photo_bytes, "image/png")},
            follow_redirects=True,
        )
        assert "menunggu pemeriksaan admin" in response.text

    with SessionLocal() as db:
        change_id = db.scalar(
            select(models.ChangeRequest)
            .where(models.ChangeRequest.kind == "create_location")
            .order_by(models.ChangeRequest.created_at.desc())
        ).id
        staged_name = db.get(models.ChangeRequest, change_id).staged_photo_name
        assert staged_name and (UPLOAD_DIR / staged_name).exists()

    with TestClient(app) as admin:
        login(admin, "admin@example.com", "Admin123!")
        rejected = admin.post(
            f"/admin/usulan/{change_id}/keputusan",
            data={"csrf_token": csrf(admin), "action": "reject", "review_note": "Data kurang jelas."},
            follow_redirects=True,
        )
        assert "ditolak" in rejected.text

    with SessionLocal() as db:
        item = db.get(models.ChangeRequest, change_id)
        assert item.staged_photo_name is None
        assert not (UPLOAD_DIR / staged_name).exists()


def test_owner_can_delete_their_own_rating():
    placement_id = placement_id_by_spot("area-makan-selatan")
    punggung_id = body_area_id("punggung")
    with TestClient(app) as user:
        login(user, "user@example.com", "User123!")
        user.get(f"/nilai/{placement_id}")
        submitted = user.post(
            f"/nilai/{placement_id}",
            data={"csrf_token": csrf(user), "usage_date": "2026-11-02", f"score_{punggung_id}": "4"},
            follow_redirects=True,
        )
        assert "menunggu pemeriksaan admin" in submitted.text
        rating_id = re.search(r"/ratings/([^/]+)/hapus", submitted.text).group(1)

        with SessionLocal() as db:
            assert db.get(models.Rating, rating_id) is not None

        deleted = user.post(
            f"/ratings/{rating_id}/hapus",
            data={"csrf_token": csrf(user)},
            follow_redirects=True,
        )
        assert "Rating Anda dihapus" in deleted.text

    with SessionLocal() as db:
        assert db.get(models.Rating, rating_id) is None


def _create_venue_with_spot(db, index: int):
    """Seed one pagination venue/spot; safe to call from several tests."""
    venue = db.scalar(
        select(models.Venue).where(models.Venue.slug == f"paginasi-test-mall-{index}")
    )
    if venue is None:
        venue = models.Venue(
            name=f"Paginasi Test Mall {index}",
            slug=f"paginasi-test-mall-{index}",
            category="mall",
            address="Jl. Uji Paginasi 1",
            admin_city="Jakarta Pusat",
            latitude=-6.2,
            longitude=106.8,
            is_published=True,
        )
        db.add(venue)
        db.flush()
    if (
        db.scalar(select(models.Spot).where(models.Spot.slug == f"paginasi-spot-{index}"))
        is None
    ):
        db.add(
            models.Spot(
                venue_id=venue.id,
                name=f"Lantai 1 dekat pintu {index}",
                slug=f"paginasi-spot-{index}",
                floor="1",
                zone_landmark="Dekat pintu utara",
                existence_status="ada",
                is_published=True,
            )
        )


def test_home_pagination_spans_two_pages():
    with SessionLocal() as db:
        for number in range(11):
            _create_venue_with_spot(db, number)
        db.commit()

    with TestClient(app) as client:
        page1 = client.get("/").text
        assert "class=\"pager\"" in page1
        assert "Berikutnya" in page1
        assert "Halaman 1 dari 2" in page1

        page2 = client.get("/?page=2").text
        assert "Sebelumnya" in page2
        # Paginated page 2 must not repeat page-1 venues.
        assert "Paginasi Test Mall 0" not in page2
        assert "Paginasi Test Mall 10" in page2

        # Out-of-range and non-numeric page values fall back safely.
        assert "Sebelumnya" in client.get("/?page=99").text
        assert "Halaman 1 dari 2" in client.get("/?page=abc").text


def test_admin_queue_pagination():
    with TestClient(app) as client:
        login(client, "admin@example.com", "Admin123!")

    with SessionLocal() as db:
        admin = db.scalar(select(models.User).where(models.User.email == "admin@example.com"))
        for _ in range(11):
            db.add(
                models.ChangeRequest(
                    submitter_id=admin.id,
                    kind="create_location",
                    payload_json="{}",
                    status="submitted",
                )
            )
        placement = db.scalar(
            select(models.Placement)
            .where(
                models.Placement.is_published.is_(True),
                models.Placement.brand_id.is_not(None),
                models.Placement.chair_type_id.is_not(None),
            )
            .order_by(models.Placement.id)
        )
        pending = len(
            db.scalars(
                select(models.RatingVersion).where(models.RatingVersion.status == "pending")
            ).all()
        )
        for i in range(max(0, 11 - pending)):
            subject = models.AnonymousSubject(token_hash=f"pager-subject-{i}")
            db.add(subject)
            db.flush()
            rating = models.Rating(placement_id=placement.id, anonymous_subject_id=subject.id)
            db.add(rating)
            db.flush()
            db.add(
                models.RatingVersion(
                    rating_id=rating.id,
                    version_no=1,
                    usage_date=models.utcnow().date(),
                    status="pending",
                )
            )
        db.commit()

    with TestClient(app) as client:
        login(client, "admin@example.com", "Admin123!")
        page1 = client.get("/admin").text
        assert "Halaman 1 dari 2" in page1
        assert "/admin?loc_page=2" in page1

        page2 = client.get("/admin?loc_page=2").text
        assert "Sebelumnya" in page2
        assert "/admin?loc_page=1" in page2

        # Moving one queue must not reset the other queue's page.
        both = client.get("/admin?loc_page=1&rat_page=2").text
        loc_next = re.search(r'href="([^"]*loc_page=2[^"]*)"', both)
        assert loc_next and "rat_page=2" in loc_next.group(1)
        rat_prev = re.search(r'href="([^"]*rat_page=1[^"]*)"', both)
        assert rat_prev and "loc_page=1" in rat_prev.group(1)


def test_admin_can_edit_venue_and_others_cannot():
    with SessionLocal() as db:
        for number in range(11):
            _create_venue_with_spot(db, number)
        db.commit()

    with SessionLocal() as db:
        venue = db.scalar(select(models.Venue).where(models.Venue.slug == "paginasi-test-mall-0"))
        venue_id = venue.id

    # Anonymous visitors are sent to the login page.
    with TestClient(app) as client:
        resp = client.get(f"/admin/venue/{venue_id}/edit", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/masuk"

    # Public accounts lack the admin role.
    with TestClient(app) as client:
        login(client, "user@example.com", "User123!")
        resp = client.get(f"/admin/venue/{venue_id}/edit", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

    with TestClient(app) as client:
        login(client, "admin@example.com", "Admin123!")
        page = client.get(f"/admin/venue/{venue_id}/edit")
        assert "Edit gedung" in page.text

        saved = client.post(
            f"/admin/venue/{venue_id}/edit",
            data={
                "csrf_token": csrf(client),
                "name": "Paginasi Mall Renamed",
                "category": "mall",
                "admin_city": "Jakarta Pusat",
                "address": "Jl. Uji Paginasi 1",
                "district": "Tanah Abang",
                "latitude": "-6.22",
                "longitude": "106,81",
                "opening_hours": "10:00-21:00",
                "is_published": "1",
            },
            follow_redirects=True,
        )
        assert "Data gedung diperbarui" in saved.text

        with SessionLocal() as db:
            venue = db.get(models.Venue, venue_id)
            assert venue.name == "Paginasi Mall Renamed"
            assert venue.district == "Tanah Abang"
            assert venue.longitude == 106.81
            assert venue.is_published

        # Invalid coordinates keep the old values and show the flash message.
        invalid = client.post(
            f"/admin/venue/{venue_id}/edit",
            data={
                "csrf_token": csrf(client),
                "name": "Paginasi Mall Renamed",
                "category": "mall",
                "admin_city": "Jakarta Pusat",
                "address": "Jl. Uji Paginasi 1",
                "latitude": "abc",
                "longitude": "106.81",
            },
            follow_redirects=True,
        )
        assert "harus berupa angka" in invalid.text


def test_admin_csv_export():
    with TestClient(app) as client:
        resp = client.get("/admin/export/spots.csv", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/masuk"

    with TestClient(app) as client:
        login(client, "user@example.com", "User123!")
        resp = client.get("/admin/export/spots.csv", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

    with TestClient(app) as client:
        login(client, "admin@example.com", "Admin123!")
        resp = client.get("/admin/export/spots.csv")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "attachment" in resp.headers["content-disposition"]
        body = resp.content.decode("utf-8-sig")
        header = body.splitlines()[0]
        for column in ["venue_name", "spot_name", "brand", "quantity", "price_idr"]:
            assert column in header
        # At least one placement row beyond the header.
        assert len(body.strip().splitlines()) > 1


def test_suggest_api():
    with SessionLocal() as db:
        for number in range(11):
            _create_venue_with_spot(db, number)
        db.commit()

    with TestClient(app) as client:
        # Too short queries return empty buckets.
        empty = client.get("/api/v1/suggest", params={"q": "a"})
        assert empty.status_code == 200
        assert empty.json() == {"venues": [], "spots": [], "brands": []}

        data = client.get("/api/v1/suggest", params={"q": "Paginasi"}).json()
        assert any(v["name"].startswith("Paginasi") for v in data["venues"])
        assert all("slug" in v and "city" in v for v in data["venues"])

        spots = client.get("/api/v1/suggest", params={"q": "pintu"}).json()
        assert any(s["name"].startswith("Lantai 1 dekat pintu") for s in spots["spots"])
        assert all("venue_slug" in s and "spot_slug" in s for s in spots["spots"])

        # Wildcard characters in the term are matched literally.
        wildcard = client.get("/api/v1/suggest", params={"q": "%"}).json()
        assert wildcard["venues"] == []

def test_create_location_normalises_comma_coordinates():
    with SessionLocal() as db:
        admin = db.scalar(select(models.User).where(models.User.email == "admin@example.com"))
        payload = {
            "venue": {
                "name": "Venue Koordinat Koma",
                "category": "mall",
                "address": "Jl. Uji Koma 1",
                "admin_city": "Jakarta Pusat",
                "latitude": "-6,21",
                "longitude": "106,83",
            },
            "spot": {
                "name": "Titik Koordinat Koma",
                "floor": "1",
                "zone_landmark": "Dekat eskalator",
                "existence_status": "ada",
                "access_groups": ["umum"],
            },
            "placement": {},
            "verification": {"checked_date": "2026-11-01"},
            "photo": {},
        }
        change = models.ChangeRequest(
            submitter_id=admin.id,
            kind="create_location",
            payload_json=json.dumps(payload),
            status="submitted",
        )
        db.add(change)
        db.commit()

        spot, _ = services.apply_create_location(db, change, admin)
        db.commit()

        assert spot.venue.latitude == -6.21
        assert spot.venue.longitude == 106.83
        assert spot.access_codes == ["umum"]


def test_create_location_rejects_unknown_access_groups():
    with SessionLocal() as db:
        admin = db.scalar(select(models.User).where(models.User.email == "admin@example.com"))
        payload = {
            "venue": {
                "name": "Venue Kode Akses Palsu",
                "category": "mall",
                "address": "Jl. Uji Kode 1",
                "admin_city": "Jakarta Pusat",
                "latitude": "-6.2",
                "longitude": "106.8",
            },
            "spot": {
                "name": "Titik Kode Akses Palsu",
                "floor": "1",
                "zone_landmark": "Dekat pintu",
                "existence_status": "ada",
                "access_groups": ["kode_palsu"],
            },
            "placement": {},
            "verification": {"checked_date": "2026-11-01"},
            "photo": {},
        }
        change = models.ChangeRequest(
            submitter_id=admin.id,
            kind="create_location",
            payload_json=json.dumps(payload),
            status="submitted",
        )
        db.add(change)
        db.commit()

        try:
            services.apply_create_location(db, change, admin)
        except ValueError as exc:
            db.rollback()
            assert "Kelompok akses tidak dikenali" in str(exc)
        else:
            raise AssertionError("unknown access groups must be rejected")


def test_type_rating_summary_ignores_placements_in_hidden_venues():
    with SessionLocal() as db:
        chair_type = db.scalar(
            select(models.ChairType).where(models.ChairType.slug == "tipe-punggung-satu")
        )
        area = db.scalar(select(models.BodyArea).where(models.BodyArea.code == "punggung"))
        before = {
            row["area"].code: row["summary"]["count"]
            for row in services.rating_summary_for_type(db, chair_type)
        }

        venue = models.Venue(
            name="Venue Tersembunyi Rating",
            slug="venue-tersembunyi-rating",
            category="mall",
            address="Jl. Tersembunyi",
            admin_city="Jakarta Pusat",
            latitude=-6.21,
            longitude=106.82,
            is_published=False,
        )
        db.add(venue)
        db.flush()
        spot = models.Spot(
            venue_id=venue.id,
            name="Titik Tersembunyi",
            slug="titik-tersembunyi-rating",
            floor="2",
            zone_landmark="x",
            existence_status="ada",
            is_published=True,
        )
        db.add(spot)
        db.flush()
        placement = models.Placement(
            spot_id=spot.id,
            brand_id=chair_type.brand_id,
            chair_type_id=chair_type.id,
            is_published=True,
            effective_from=models.utcnow().date(),
            cost_type="berbayar_per_sesi",
        )
        db.add(placement)
        db.flush()
        subject = models.AnonymousSubject(token_hash="hidden-summary-subject")
        db.add(subject)
        db.flush()
        rating = models.Rating(placement_id=placement.id, anonymous_subject_id=subject.id)
        db.add(rating)
        db.flush()
        version = models.RatingVersion(
            rating_id=rating.id,
            version_no=1,
            usage_date=models.utcnow().date(),
            status="approved",
        )
        db.add(version)
        db.flush()
        rating.active_version_id = version.id
        db.add(
            models.RatingAreaScore(
                rating_version_id=version.id, body_area_id=area.id, stars=5
            )
        )
        db.commit()

        after = {
            row["area"].code: row["summary"]["count"]
            for row in services.rating_summary_for_type(db, chair_type)
        }
        assert after == before


def test_venue_spots_api_hides_unpublished_venue():
    with SessionLocal() as db:
        venue = db.scalar(
            select(models.Venue).where(models.Venue.slug == "venue-tersembunyi-api")
        )
        if venue is None:
            venue = models.Venue(
                name="Venue Tersembunyi API",
                slug="venue-tersembunyi-api",
                category="mall",
                address="Jl. Tersembunyi API",
                admin_city="Jakarta Pusat",
                latitude=-6.2,
                longitude=106.8,
                is_published=False,
            )
            db.add(venue)
            db.commit()
        venue_id = venue.id

    with TestClient(app) as client:
        response = client.get(f"/api/v1/venues/{venue_id}/spots")
        assert response.status_code == 404


def test_delete_rating_requires_csrf_token():
    with SessionLocal() as db:
        user = db.scalar(select(models.User).where(models.User.email == "user@example.com"))
        rated = set(
            db.scalars(
                select(models.Rating.placement_id).where(models.Rating.user_id == user.id)
            ).all()
        )
        placement = next(
            p
            for p in db.scalars(
                select(models.Placement).where(models.Placement.is_published.is_(True))
            ).all()
            if p.id not in rated
        )
        rating = models.Rating(placement_id=placement.id, user_id=user.id)
        db.add(rating)
        db.commit()
        rating_id = rating.id

    with TestClient(app) as client:
        login(client, "user@example.com", "User123!")

        rejected = client.post(f"/ratings/{rating_id}/hapus", data={}, follow_redirects=False)
        assert rejected.status_code == 403
        with SessionLocal() as db:
            assert db.get(models.Rating, rating_id) is not None

        deleted = client.post(
            f"/ratings/{rating_id}/hapus",
            data={"csrf_token": csrf(client)},
            follow_redirects=False,
        )
        assert deleted.status_code == 303
        with SessionLocal() as db:
            assert db.get(models.Rating, rating_id) is None


def test_csv_export_neutralises_formula_cells():
    with SessionLocal() as db:
        brand = db.scalar(select(models.Brand))
        chair_type = db.scalar(select(models.ChairType))
        venue = models.Venue(
            name='=HYPERLINK("http://example.invalid/x","klik")',
            slug="csv-formula-venue",
            category="mall",
            address="Jl. Uji CSV",
            admin_city="Jakarta Pusat",
            latitude=-6.2,
            longitude=106.8,
            is_published=False,
        )
        db.add(venue)
        db.flush()
        spot = models.Spot(
            venue_id=venue.id,
            name="+1+1",
            slug="csv-formula-spot",
            floor="1",
            zone_landmark="x",
            existence_status="ada",
            is_published=True,
        )
        db.add(spot)
        db.flush()
        db.add(
            models.Placement(
                spot_id=spot.id,
                brand_id=brand.id,
                chair_type_id=chair_type.id,
                is_published=True,
                effective_from=models.utcnow().date(),
                cost_type="berbayar_per_sesi",
                quantity=2,
            )
        )
        db.commit()

    with TestClient(app) as client:
        login(client, "admin@example.com", "Admin123!")
        body = client.get("/admin/export/spots.csv").content.decode("utf-8-sig")

    formula_rows = [line for line in body.splitlines() if "HYPERLINK" in line]
    prefix = '"' + "'" + "=HYPERLINK"
    assert formula_rows and formula_rows[0].startswith(prefix)
    assert any("'+1+1" in line for line in body.splitlines())


def test_forwarded_header_is_ignored_unless_peer_is_trusted(monkeypatch):
    from starlette.requests import Request

    from app import web

    def make_request(host: str, forwarded: str) -> Request:
        return Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/",
                "raw_path": b"/",
                "query_string": b"",
                "headers": [(b"x-forwarded-for", forwarded.encode())],
                "client": (host, 12345),
                "server": ("testserver", 80),
            }
        )

    monkeypatch.setattr(config, "TRUSTED_PROXIES", set())
    request = make_request("198.51.100.9", "203.0.113.1, 203.0.113.2")
    assert web.client_ip(request) == "198.51.100.9"

    monkeypatch.setattr(config, "TRUSTED_PROXIES", {"198.51.100.9"})
    assert web.client_ip(request) == "203.0.113.1"


def test_home_pagination_handles_zero_and_negative_pages():
    with SessionLocal() as db:
        for number in range(11):
            _create_venue_with_spot(db, number)
        db.commit()

    with TestClient(app) as client:
        for page in ("0", "-1"):
            response = client.get("/", params={"page": page})
            assert response.status_code == 200
            assert "Halaman 1 dari" in response.text


def test_private_spot_is_not_exposed_on_public_pages():
    with TestClient(app) as client:
        detail = client.get(
            "/venue/gedung-kantor-demo/titik/ruang-istirahat-karyawan"
        )
        assert detail.status_code == 404

        venue_page = client.get("/venue/gedung-kantor-demo")
        assert venue_page.status_code == 200
        assert "Ruang istirahat karyawan" not in venue_page.text

        suggest = client.get("/api/v1/suggest", params={"q": "istirahat"}).json()
        assert suggest["spots"] == []


def test_runtime_config_blocks_insecure_production(monkeypatch):
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    monkeypatch.setattr(config, "COOKIE_SECURE", False)
    monkeypatch.setattr(config, "ALLOW_DEMO_ACCOUNTS", True)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    problems = config.validate_runtime_config()
    assert any("COOKIE_SECURE" in problem for problem in problems)
    assert any("DATABASE_URL" in problem for problem in problems)
    assert any("ALLOW_DEMO_ACCOUNTS" in problem for problem in problems)

    monkeypatch.setattr(config, "COOKIE_SECURE", True)
    monkeypatch.setattr(config, "ALLOW_DEMO_ACCOUNTS", False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/app")
    assert config.validate_runtime_config() == []


def test_runtime_config_rejects_sameSite_none_without_secure_cookie(monkeypatch):
    monkeypatch.setattr(config, "COOKIE_SAMESITE", "none")
    monkeypatch.setattr(config, "COOKIE_SECURE", False)
    assert any("SameSite=none" in problem or "COOKIE_SAMESITE=none" in problem
               for problem in config.validate_runtime_config())


def test_upload_path_rejects_unsafe_names():
    from fastapi import HTTPException

    from app.uploads import is_safe_storage_name, upload_path

    assert is_safe_storage_name("staged-" + "a" * 32 + ".jpg")
    assert not is_safe_storage_name("../etc/passwd")
    assert not is_safe_storage_name("staged-../../etc/passwd")
    assert not is_safe_storage_name("")
    assert not is_safe_storage_name(None)

    with pytest.raises(HTTPException) as excinfo:
        upload_path("../../etc/passwd")
    assert excinfo.value.status_code == 404


def test_sensitive_pages_are_not_cached():
    with TestClient(app) as client:
        login(client, "admin@example.com", "Admin123!")
        assert client.get("/admin").headers["cache-control"] == "no-store, private"
        assert client.get("/akun/rating-saya").headers["cache-control"] == "no-store, private"


def test_stale_staged_photos_are_purged():
    from datetime import timedelta

    from app.db import UPLOAD_DIR

    with SessionLocal() as db:
        submitter = db.scalar(select(models.User).where(models.User.role == "data_entry"))
        item = models.ChangeRequest(
            submitter_id=submitter.id,
            kind="create_location",
            payload_json="{}",
            staged_photo_name="staged-" + "b" * 32 + ".jpg",
            status="submitted",
        )
        item.created_at = datetime.now(UTC) - timedelta(days=60)
        db.add(item)
        db.commit()
        item_id = item.id
    (UPLOAD_DIR / ("staged-" + "b" * 32 + ".jpg")).write_bytes(b"demo")

    with SessionLocal() as db:
        assert services.purge_stale_staged_photos(db, 30) == 1
        assert db.get(models.ChangeRequest, item_id).staged_photo_name is None
    assert not (UPLOAD_DIR / ("staged-" + "b" * 32 + ".jpg")).exists()
