# Kursi Pijat Finder

Aplikasi web untuk mencari lokasi kursi pijat di Jakarta. Pengguna dapat melihat gedung di peta, mencari titik kursi di dalamnya, memeriksa akses dan harga, serta memberi rating setelah menggunakan kursi.

**Data bawaan adalah data contoh, bukan data lokasi yang sudah diverifikasi.** Status “Masih Ada” menunjukkan hasil pemeriksaan terakhir, bukan apakah kursi sedang kosong.

## Fitur

- Pencarian berdasarkan lokasi atau merk, dengan filter akses dan pembayaran.
- Peta gedung dengan pengelompokan lokasi yang berdekatan, serta informasi lantai dan patokan setiap titik.
- Rating berdasarkan area pijat yang didukung oleh tipe kursi.
- Usulan data oleh petugas dan pemeriksaan oleh admin.
- Riwayat perubahan lokasi yang sudah disetujui.

## Teknologi

Python, FastAPI, Jinja2, SQLAlchemy, SQLite, dan Leaflet. Peta menggunakan tile OpenStreetMap dan memerlukan koneksi internet.

## Menjalankan aplikasi

Gunakan Python 3.11 atau yang lebih baru. Instalasi dan pengujian telah dijalankan dengan Python 3.15.2 di Linux.

Uvicorn dipasang tanpa paket tambahan `standard` karena aplikasi tidak membutuhkan PyYAML. Opsi `--reload` tetap tersedia untuk pengembangan.

Buka terminal di folder utama proyek, yaitu folder yang berisi `requirements.txt`. Nama dan lokasi folder proyek bebas; perintah di bawah menyimpan virtual environment di folder pengguna, bukan di dalam proyek. Aktivasi virtual environment tidak diperlukan karena perintah langsung memakai Python dari environment tersebut.

**Windows PowerShell (Python 3.15)**

```powershell
$venv = Join-Path $env:USERPROFILE '.virtualenvs\kursi-pijat-finder'
py -3.15 -m venv $venv
& "$venv\Scripts\python.exe" -m pip install -r requirements.txt
& "$venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

**macOS atau Linux (Python 3.15)**

```bash
VENV_DIR="$HOME/.virtualenvs/kursi-pijat-finder"
python3.15 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install -r requirements.txt
"$VENV_DIR/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Untuk menjalankan ulang, buka terminal di folder proyek dan gunakan kembali perintah Uvicorn di atas. Di terminal baru, tentukan kembali variabel `$venv` atau `VENV_DIR`.

Sebagai alternatif, `app/main.py` juga bisa dijalankan langsung dengan Python dari environment tersebut. Contoh di Windows PowerShell:

```powershell
& "$venv\Scripts\python.exe" .\app\main.py
```

Cara langsung menjalankan server tanpa `--reload`. Buka `http://127.0.0.1:8000`. Database dan data contoh dibuat otomatis saat aplikasi pertama kali dijalankan.

## Akun lokal

Akun berikut hanya untuk pengujian di komputer sendiri:

| Peran | Email | Kata sandi |
|---|---|---|
| Admin | `admin@example.com` | `Admin123!` |
| Petugas | `petugas@example.com` | `Petugas123!` |
| Pengguna | `user@example.com` | `User123!` |

Rating juga dapat diberikan tanpa akun. Token anonim disimpan di browser; menghapus cookie dapat menghilangkan akses ke rating anonim tersebut.

## Database dan berkas unggahan

Database berada di `data/app.db` dan foto tersimpan di `data/uploads/`. Keduanya tidak disertakan dalam repositori. Untuk mengulang data contoh, hentikan aplikasi lalu hapus `data/app.db`. Data yang sudah ditambahkan akan ikut terhapus.

Lokasi database dapat diatur melalui variabel lingkungan `DATABASE_URL`. Path SQLite relatif dihitung dari direktori tempat perintah dijalankan. Jika variabel tidak diatur, lokasi database mengikuti folder proyek.

## Pengujian

Jalankan dari folder utama proyek setelah environment disiapkan:

```powershell
# Windows PowerShell; tentukan kembali $venv jika membuka terminal baru
& "$venv\Scripts\python.exe" -m pytest -q
```

```bash
# macOS atau Linux; tentukan kembali VENV_DIR jika membuka terminal baru
"$VENV_DIR/bin/python" -m pytest -q
```

Pengujian menggunakan database sementara dan tidak mengubah database lokal aplikasi.

## Batasan

Aplikasi ini ditujukan untuk pengujian lokal, **belum siap dibuka ke publik**. Akun contoh dibuat otomatis dengan kata sandi yang diketahui; jangan gunakan akun tersebut untuk layanan publik. Sebelum menerapkan aplikasi secara daring, hapus mekanisme akun contoh, siapkan autentikasi dan konfigurasi produksi, HTTPS dan cookie aman, pembatasan laju, pencadangan, penyimpanan foto, serta tinjau keamanan dan kebijakan penyedia tile peta.

Formulir penggantian penempatan kursi dan impor data massal belum tersedia. Data contoh perlu diganti dengan data yang telah diperiksa sebelum digunakan sebagai informasi publik.

## Komponen pihak ketiga

Leaflet dan ikon Lucide disertakan bersama lisensinya. Lihat [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
