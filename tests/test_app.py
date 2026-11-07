import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_fd, _test_db_path = tempfile.mkstemp(prefix="kpf-test-", suffix=".db")
os.close(_fd)
os.unlink(_test_db_path)
os.environ["DATABASE_URL"] = f"sqlite:///{_test_db_path}"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import models  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed_database  # noqa: E402

Base.metadata.create_all(bind=engine)
with SessionLocal() as db:
    seed_database(db)


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
            assert 'href="/#cari"' in page.text
            assert 'href="/#explorer"' in page.text
            assert 'href="/merk"' in page.text
            assert 'href="/tentang-data"' in page.text
        assert 'href="/merk" aria-current="page"' in client.get("/merk").text

        login(client, "admin@example.com", "Admin123!")
        admin_page = client.get("/admin")
        assert admin_page.status_code == 200
        assert 'href="/admin" aria-current="page"' in admin_page.text
        assert 'action="/keluar"' in admin_page.text


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
