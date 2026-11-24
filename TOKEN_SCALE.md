# Skala token

Catatan tentang skala ukuran teks di `app/static/css/theme.css`. Sudah diterapkan;
dokumen ini menjelaskan alasannya supaya token lama tidak ditumbuhkan kembali.

## Sebelum

`theme.css` mendeklarasikan **38 token ukuran teks**. 33 di antaranya di rentang
**9,2 – 22,4 px**, dan 30 di antaranya di rentang 9,2 – 17,6 px saja. Selisih antar
token sering lebih kecil dari satu piksel:

| token | nilai | piksel | dipakai |
| --- | --- | --- | --- |
| `--text-7` | .7rem | 11,20 | 3 |
| `--text-715` | .715rem | 11,44 | 1 |
| `--text-72` | .72rem | 11,52 | 9 |
| `--text-74` | .74rem | 11,84 | 1 |
| `--text-75` | .75rem | 12,00 | 2 |
| `--text-76` | .76rem | 12,16 | 5 |

Empat token di atas berbeda 0,16 px satu sama lain. Selisih itu tidak terlihat di
layar, jadi token tersebut tidak menyampaikan keputusan desain apa pun, hanya menambah
nama yang harus dihafal. `--text-30px` tidak dipakai sama sekali.

## Sekarang: 8 token

| token | nilai | piksel | untuk |
| --- | --- | --- | --- |
| `--text-2xs` | .6875rem | 11 | label huruf kapital (kicker, eyebrow), wajib `text-transform: uppercase` + `letter-spacing` |
| `--text-xs` | .75rem | 12 | meta, caption, helper, teks di dalam badge |
| `--text-sm` | .875rem | 14 | teks sekunder, label formulir, item nav, isi kartu |
| `--text-base` | 1rem | 16 | badan teks, tombol |
| `--text-lg` | 1.125rem | 18 | judul kartu, `.lead`, legend formulir |
| `--text-xl` | `clamp(1.28rem, .8vw + 1.02rem, 1.6rem)` | 20,5 – 25,6 | judul seksi |
| `--text-2xl` | `clamp(1.6rem, 1.4vw + 1.15rem, 2.15rem)` | 25,6 – 34,4 | judul halaman |
| `--text-3xl` | `clamp(1.9rem, 1.7vw + 1.2rem, 2.8rem)` | 30,4 – 44,8 | hero |

Empat langkah tetap untuk teks, empat langkah fluid untuk display. Rasio antar langkah
teks: 12 → 14 → 16 → 18 → 20 → 24 → 30. Ini bukan skala modular 1,25 (12, 15, 18,75,
23,4) karena aplikasi ini padat data: 15 px dan 18,75 px tidak muat di tabel dan kartu.

Pemakaian setelah migrasi: 133 total (2xs 9, xs 36, sm 51, base 18, lg 8, xl 5, 2xl 4,
3xl 2). Tidak ada token yang dideklarasi tanpa dipakai.

## Catatan migrasi

Semua 33 token lama dipetakan ke langkah terdekat. 116 pemakaian berada di rentang
9,2 – 22,4 px; 77 di antaranya tidak bergeser lebih dari 0,5 px, dan tidak ada elemen
yang berubah lebih dari 1,8 px.

Tiga keputusan yang diambil saat migrasi:

1. **`--text-9375` (15 px) → `--text-base` (16 px).** Dipakai `.button` dan satu tombol
   lain. Turun ke 14 px akan mengecilkan tombol utama; naik ke 16 px membuatnya seukuran
   badan teks.
2. **`--text-md` (17 px) → `--text-base` (16 px).** Dipakai `.hero-lead`. Nilai 17 px
   adalah "hampir 16, hampir 18"; hierarki hero tetap terjaga lewat `--text-3xl` pada
   judulnya.
3. **`--text-lg` berubah dari `clamp(1.1rem, .35vw + 1rem, 1.25rem)` menjadi 18 px tetap.**
   Sebelumnya 17,6 – 20 px. Ini mengecilkan judul kartu sekitar 2 px di desktop dan
   menghilangkan langkah fluid yang tidak perlu: hanya ada satu pemakaian yang butuh
   ukuran di antara 16 dan 20. Kalau judul kartu terasa terlalu kecil, kembalikan
   `--text-lg` ke `clamp(1.1rem, .35vw + 1rem, 1.25rem)` dan biarkan sisanya.

Diverifikasi setelah migrasi: tidak ada *horizontal overflow* di 360, 390, 480, 640, 768,
1024, dan 1440 px pada halaman `/`, `/venue/...`, `/merk`, dan `/tentang-data`.

## Yang sengaja tidak diubah

- **Spacing** — `--space-1` sampai `--space-8` sudah 8 langkah dan konsisten dipakai.
  Satu-satunya yang mati adalah `--space-7`; hapus saat berikutnya menyentuh berkas itu.
- **Radius** — `--r-xs` sampai `--r-2xl` dan `--r-pill` sudah 7 langkah bersih
  (6, 8, 12, 16, 22, 28, 999). `--r-brand-mark` (11 px) dan `--r-brand-mark-sm` (10 px)
  berbeda 1 px dan tidak dipakai di CSS.
- **Bayangan** — `--shadow-1` sampai `--shadow-3` adalah ramp elevasi yang benar. Empat
  token bayangan khusus (`pin`, `pin-active`, `cluster`, `rail`) memang komponen tunggal.
- **Warna** — tidak diperiksa di dokumen ini.

## Patokan saat menulis CSS baru

Satu ukuran untuk satu peran. Kalau butuh ukuran yang belum ada, pertanyaannya bukan
"nilai berapa", tapi "peran apa". Kalau perannya sudah ada, pakai tokennya. Jangan
menambah token baru untuk selisih di bawah 1 px: selisih itu tidak terlihat, dan begitu
satu token "hampir sama" ditambahkan, delapan lainnya akan menyusul.
