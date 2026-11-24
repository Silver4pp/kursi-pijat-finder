import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, models
from .security import hash_password
from .services import slugify

logger = logging.getLogger("kpf")

BODY_AREAS = [
    ("leher_bahu", "Leher dan bahu"),
    ("punggung", "Punggung"),
    ("pinggang", "Pinggang"),
    ("panggul", "Panggul"),
    ("lengan", "Lengan"),
    ("betis", "Betis"),
    ("telapak_kaki", "Telapak kaki"),
]

ACCESS_GROUPS = [
    ("umum", "Pengunjung umum", False),
    ("penumpang_bertiket", "Penumpang bertiket", False),
    ("boarding_pass", "Penumpang dengan boarding pass", False),
    ("pelanggan_tamu", "Pelanggan atau tamu venue", False),
    ("member", "Member", True),
    ("pasien_pendamping", "Pasien dan pendamping", False),
    ("karyawan", "Karyawan", True),
    ("penghuni", "Penghuni", True),
    ("tamu_undangan", "Tamu undangan", True),
]


def seed_database(db: Session) -> None:
    """Create the reference data on an empty database.

    Demo accounts and the demo ratings that depend on them are opt-in
    (ALLOW_DEMO_ACCOUNTS=1) so a deployment can never inherit a known admin
    password just because APP_ENV was left unset.
    """
    if db.scalar(select(models.User)) is not None or db.scalar(select(models.Venue)) is not None:
        return

    areas = {}
    for number, (code, label) in enumerate(BODY_AREAS, start=1):
        area = models.BodyArea(code=code, label=label, sort_order=number)
        db.add(area)
        areas[code] = area

    groups = {}
    for code, label, private in ACCESS_GROUPS:
        group = models.AccessGroup(code=code, label=label, is_private=private)
        db.add(group)
        groups[code] = group

    admin = petugas = None
    if config.ALLOW_DEMO_ACCOUNTS:
        admin = models.User(
            name="Admin Demo",
            email="admin@example.com",
            password_hash=hash_password("Admin123!"),
            role="admin",
        )
        petugas = models.User(
            name="Petugas Demo",
            email="petugas@example.com",
            password_hash=hash_password("Petugas123!"),
            role="data_entry",
        )
        public_user = models.User(
            name="Pengguna Demo",
            email="user@example.com",
            password_hash=hash_password("User123!"),
            role="public",
        )
        db.add_all([admin, petugas, public_user])
    else:
        logger.info(
            "Akun demo tidak dibuat (ALLOW_DEMO_ACCOUNTS tidak diaktifkan). "
            "Buat admin pertama secara manual bila diperlukan."
        )

    brand_a = models.Brand(name="Merk Aruna (Contoh)", slug="merk-aruna-contoh")
    brand_b = models.Brand(name="Merk Nusantara (Contoh)", slug="merk-nusantara-contoh")
    operator = models.Operator(name="Relaks Demo")
    db.add_all([brand_a, brand_b, operator])
    db.flush()

    type_back = models.ChairType(
        brand_id=brand_a.id,
        name="Tipe Punggung Satu",
        slug="tipe-punggung-satu",
        notes="Data tipe contoh untuk pengujian lokal.",
    )
    type_full = models.ChairType(
        brand_id=brand_a.id,
        name="Tipe Seluruh Badan Dua",
        slug="tipe-seluruh-badan-dua",
        notes="Data tipe contoh untuk pengujian lokal.",
    )
    type_leg = models.ChairType(
        brand_id=brand_b.id,
        name="Tipe Punggung dan Kaki",
        slug="tipe-punggung-dan-kaki",
        notes="Data tipe contoh untuk pengujian lokal.",
    )
    db.add_all([type_back, type_full, type_leg])
    db.flush()

    feature_map = {
        type_back.id: {"punggung": ("didukung", "roller")},
        type_full.id: {
            "leher_bahu": ("didukung", "roller"),
            "punggung": ("didukung", "roller"),
            "pinggang": ("didukung", "roller"),
            "panggul": ("didukung", "kantung_udara"),
            "lengan": ("didukung", "kantung_udara"),
            "betis": ("didukung", "kantung_udara"),
            "telapak_kaki": ("didukung", "roller"),
        },
        type_leg.id: {
            "punggung": ("didukung", "roller"),
            "pinggang": ("didukung", "roller"),
            "betis": ("didukung", "kantung_udara"),
            "telapak_kaki": ("didukung", "roller"),
        },
    }
    for chair_type in [type_back, type_full, type_leg]:
        for code, area in areas.items():
            status, mechanism = feature_map.get(chair_type.id, {}).get(
                code, ("tidak_didukung", None)
            )
            db.add(
                models.ChairTypeBodyArea(
                    chair_type_id=chair_type.id,
                    body_area_id=area.id,
                    support_status=status,
                    mechanism=mechanism,
                    source_note="Contoh lokal, bukan spesifikasi produk nyata.",
                    confirmed_at=date(2026, 11, 1),
                )
            )

    venues = {}
    venue_rows = [
        (
            "stasiun",
            "Stasiun Demo Jakarta",
            "stasiun",
            "Jl. Contoh Stasiun No. 1",
            "Jakarta Pusat",
            "Gambir",
            -6.1767,
            106.8306,
            "Setiap hari 04.00-23.00 (contoh)",
        ),
        (
            "mall_selatan",
            "Mall Demo Selatan",
            "mall",
            "Jl. Contoh Selatan No. 2",
            "Jakarta Selatan",
            "Senayan",
            -6.2244,
            106.7996,
            "Setiap hari 10.00-22.00 (contoh)",
        ),
        (
            "mall_utara",
            "Mall Demo Utara",
            "mall",
            "Jl. Contoh Utara No. 3",
            "Jakarta Utara",
            "Kelapa Gading",
            -6.1745,
            106.9058,
            "Setiap hari 10.00-22.00 (contoh)",
        ),
        (
            "kantor",
            "Gedung Kantor Demo",
            "perkantoran",
            "Jl. Contoh Kantor No. 4",
            "Jakarta Pusat",
            "Tanah Abang",
            -6.2088,
            106.8175,
            "Jam kerja gedung (contoh)",
        ),
    ]
    for key, name, category, address, city, district, lat, lng, hours in venue_rows:
        venue = models.Venue(
            name=name,
            slug=slugify(name),
            category=category,
            address=address,
            admin_city=city,
            district=district,
            latitude=lat,
            longitude=lng,
            opening_hours=hours,
        )
        db.add(venue)
        venues[key] = venue
    db.flush()

    spot_rows = [
        (
            "gambir_utara",
            venues["stasiun"],
            "Area tunggu utara",
            "1",
            "Di dekat loket informasi contoh",
            "Pintu Utara",
            "Masuk melalui Pintu Utara, lalu berjalan ke area tunggu di dekat loket informasi.",
            ["penumpang_bertiket"],
            "Memerlukan tiket perjalanan.",
        ),
        (
            "gambir_selatan",
            venues["stasiun"],
            "Area makan selatan",
            "1",
            "Di samping deretan tempat makan contoh",
            "Pintu Selatan",
            "Masuk melalui Pintu Selatan, lalu menuju area makan.",
            ["umum", "penumpang_bertiket"],
            None,
        ),
        (
            "selatan_l2",
            venues["mall_selatan"],
            "Dekat eskalator timur",
            "2",
            "Di depan eskalator timur, di sebelah toko buku contoh",
            "Pintu Timur",
            "Naik eskalator timur ke lantai 2. Deretan kursi berada di depan eskalator.",
            ["umum"],
            None,
        ),
        (
            "utara_lg",
            venues["mall_utara"],
            "Dekat lift utama",
            "LG",
            "Di samping lift utama dan meja informasi contoh",
            "Pintu Utama",
            "Turun ke LG melalui lift utama. Titik kursi berada di samping meja informasi.",
            ["umum"],
            None,
        ),
        (
            "kantor_l5",
            venues["kantor"],
            "Ruang istirahat karyawan",
            "5",
            "Di samping pantry karyawan contoh",
            "Lobby karyawan",
            "Khusus pengguna gedung yang memiliki akses lantai 5.",
            ["karyawan"],
            "Memerlukan kartu akses karyawan.",
        ),
    ]

    spots = {}
    for key, venue, name, floor, landmark, entrance, directions, access, notes in spot_rows:
        spot = models.Spot(
            venue_id=venue.id,
            name=name,
            slug=slugify(name),
            floor=floor,
            zone_landmark=landmark,
            recommended_entrance=entrance,
            walking_directions=directions,
            access_notes=notes,
            existence_status="ada",
            last_verified_at=date(2026, 11, 1),
            verification_method="kunjungan",
        )
        spot.access_groups = [groups[code] for code in access]
        db.add(spot)
        spots[key] = spot
    db.flush()

    placement_rows = [
        ("gambir_utara", brand_a, type_back, 2, 10000, 10, "uang_tunai;qris"),
        ("gambir_selatan", brand_a, type_full, 3, 15000, 10, "uang_tunai;qris"),
        ("selatan_l2", brand_b, type_leg, 4, 20000, 15, "qris;e_money"),
        ("utara_lg", brand_a, type_full, None, None, None, ""),
        ("kantor_l5", brand_a, type_back, 1, None, None, ""),
    ]
    placements = []
    for spot_key, brand, chair_type, quantity, price, duration, payments in placement_rows:
        is_free = spot_key == "kantor_l5"
        placement = models.Placement(
            spot_id=spots[spot_key].id,
            brand_id=brand.id,
            chair_type_id=chair_type.id,
            operator_id=operator.id,
            quantity=quantity,
            quantity_basis="kunjungan" if quantity else None,
            cost_type="termasuk_fasilitas" if is_free else ("berbayar_per_sesi" if price else "belum_diketahui"),
            tariff_type="paket_tetap" if price else None,
            price_idr=price,
            duration_minutes=duration,
            price_is_starting_from=bool(price),
            payment_methods=payments,
            effective_from=date(2026, 11, 1),
        )
        db.add(placement)
        placements.append(placement)
    db.flush()

    for spot in spots.values():
        db.add(
            models.VerificationLog(
                spot_id=spot.id,
                checked_by_id=petugas.id if petugas else None,
                source_title="Data contoh lokal",
                checked_date=date(2026, 11, 1),
                check_method="kunjungan",
                confidence="kuat",
                fields_supported="keberadaan;lantai;patokan;akses",
                notes="Baris demonstrasi. Ganti dengan hasil pemeriksaan nyata sebelum dipakai publik.",
            )
        )

    if admin is not None:
        # Approved demonstration ratings: enough for the station back-only placement
        # and the same-type global fallback to be visible in local testing.
        rating_users = []
        for number in range(1, 7):
            user = models.User(
                name=f"Penilai Contoh {number}",
                email=f"penilai{number}@example.com",
                password_hash=hash_password("User123!"),
                role="public",
            )
            db.add(user)
            rating_users.append(user)
        db.flush()

        def add_rating(placement, user, scores):
            rating = models.Rating(placement_id=placement.id, user_id=user.id)
            db.add(rating)
            db.flush()
            version = models.RatingVersion(
                rating_id=rating.id,
                version_no=1,
                usage_date=date(2026, 11, 1),
                status="approved",
                reviewed_by_id=admin.id,
            )
            db.add(version)
            db.flush()
            rating.active_version_id = version.id
            for code, stars in scores.items():
                db.add(
                    models.RatingAreaScore(
                        rating_version_id=version.id, body_area_id=areas[code].id, stars=stars
                    )
                )

        back_scores = [4, 5, 4, 5, 4, 5]
        for user, stars in zip(rating_users, back_scores, strict=False):
            add_rating(placements[0], user, {"punggung": stars})

        full_scores = [
            {"punggung": 4, "betis": 3},
            {"punggung": 5, "betis": 4},
            {"punggung": 4, "betis": 4},
        ]
        for user, scores in zip(rating_users[:3], full_scores, strict=False):
            add_rating(placements[1], user, scores)

    db.commit()
