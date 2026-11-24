# Skala token

Usulan perapian token di `app/static/css/theme.css`. Dokumen ini adalah rekomendasi,
bukan perubahan yang sudah diterapkan.

## Yang sekarang ada

`theme.css` mendeklarasikan **38 token ukuran teks**. 33 di antaranya berada di rentang
**9,2 – 22,4 px**, dan 30 di antaranya berada di rentang 9,2 – 17,6 px saja.

Selisih antar token di rentang itu sering lebih kecil dari 1 piksel. Contoh nyata:

| token | nilai | piksel | dipakai |
| --- | --- | --- | --- |
| `--text-7` | .7rem | 11,20 | 3 |
| `--text-715` | .715rem | 11,44 | 1 |
| `--text-72` | .72rem | 11,52 | 9 |
| `--text-74` | .74rem | 11,84 | 1 |
| `--text-75` | .75rem | 12,00 | 2 |
| `--text-76` | .76rem | 12,16 | 5 |

Empat token di atas (72, 74, 75, 76) berbeda 0,16 px satu sama lain. Selisih itu tidak
terlihat di layar, jadi token-token tersebut tidak menyampaikan keputusan desain apa pun —
hanya menambah jumlah nama yang harus dihafal. `--text-30px` bahkan tidak dipakai sama
sekali.

Dampak praktisnya: setiap elemen baru memaksa penulis memilih di antara ~30 nilai yang
hampir sama, dan setiap pilihan membuat banyaknya token bertambah. Tidak ada satu pun token
yang menjadi "ukuran label" atau "ukuran teks bantu" yang resmi.

## Usulan: 9 token

Ramp display yang sekarang (`--text-lg` sampai `--text-3xl`) sudah bagus dan tetap dipakai.
Yang dirapikan hanya rentang teks kecil.

| token | nilai | piksel | untuk |
| --- | --- | --- | --- |
| `--text-2xs` | .6875rem | 11 | label huruf kapital (kicker/eyebrow), wajib `text-transform: uppercase` + `letter-spacing` |
| `--text-xs` | .75rem | 12 | meta, caption, helper, teks di dalam badge |
| `--text-sm` | .875rem | 14 | teks sekunder, label formulir, item nav, isi kartu |
| `--text-base` | 1rem | 16 | badan teks, tombol utama |
| `--text-lg` | 1.125rem | 18 | `hero-lead`, judul kartu besar |
| `--text-xl` | `clamp(1.28rem, .8vw + 1.02rem, 1.6rem)` | 20,5 – 25,6 | judul seksi |
| `--text-2xl` | `clamp(1.6rem, 1.4vw + 1.15rem, 2.15rem)` | 25,6 – 34,4 | judul halaman |
| `--text-3xl` | `clamp(1.9rem, 1.7vw + 1.2rem, 2.8rem)` | 30,4 – 44,8 | hero |

Catatan: `--text-lg` saat ini `clamp(1.1rem, .35vw + 1rem, 1.25rem)` (17,6 – 20 px). Kalau
dirubah menjadi 18 px tetap, `--text-lg` berhenti jadi token display dan jadi langkah teks
biasa. Itu memang maksudnya — 17,6 px adalah nilai "hampir 16, hampir 18" yang justru ingin
dihilangkan.

Rasio antar langkah: 12 → 14 → 16 → 18 → 20 → 24 → 30. Selisih 2 px di rentang kecil lalu
melebar ke atas. Ini bukan skala modular 1,25 (12, 15, 18,75, 23,4) karena aplikasi ini
padat data: 15 px dan 18,75 px tidak muat di tabel dan kartu.

## Pemetaan migrasi

Setiap token lama dipetakan ke langkah terdekat. Kolom "geser" adalah perubahan ukuran
sebenarnya di layar; nilai negatif berarti teks mengecil.

| token baru | token lama yang digantikan | pemakaian | geser terbesar |
| --- | --- | --- | --- |
| `--text-2xs` (11) | `575`, `66`, `68`, `7`, `715` | 9 | +1,80 px |
| `--text-xs` (12) | `72`, `74`, `75`, `76`, `xs`, `78`, `8` | 36 | −0,80 px |
| `--text-sm` (14) | `82`, `84`, `85`, `86`, `14px`, `sm`, `88`, `9`, `92`, `93` | 51 | −0,88 px |
| `--text-base` (16) | `9375`, `94`, `95`, `95em`, `98`, `base`, `102`, `105`, `md` | 18 | −1,00 px |
| `--text-lg` (18) | `11` | 1 | +0,40 px |
| `--text-xl` | `14` (1.4rem = 22,4 px) | 1 | −1,60 px |

Dari 116 pemakaian di rentang itu, 77 tidak bergeser lebih dari 0,5 px. Sisanya bergeser
0,56 – 1,8 px, dan hanya dua token yang dipakai satu kali yang bergeser lebih dari 1 px.
Tidak ada elemen yang berubah lebih dari 1,8 px.

Tiga keputusan yang perlu disetujui sebelum migrasi:

1. **`--text-9375` (15 px) → 16 px, bukan 14 px.** Dipakai di `.button` dan satu tombol lain.
   Turun ke 14 px akan mengecilkan tombol utama; naik ke 16 px membuatnya seukuran badan teks.
   Pilihan kedua lebih lazim dan tidak mengubah hierarki.
2. **`--text-md` (17 px) → 16 px.** Dipakai `.hero-lead` saja. 17 px adalah nilai "hampir 16,
   hampir 18". Kalau `hero-lead` memang harus lebih besar dari badan teks, naikkan ke
   `--text-lg` (18 px), jangan ke 16 px.
3. **`--text-14px` (14 px) → `--text-sm`.** Dipakai angka di dalam `.marker-cluster`. Sudah 14 px,
   tidak bergeser.

## Cara menerapkan

Jangan hapus blok lama dulu. Tambahkan token baru di atasnya, ganti pemakaian, lalu hapus
blok lama setelah jumlah pemakaiannya nol.

```python
import re
from pathlib import Path

p = Path("app/static/css/theme.css")
s = p.read_text()

MAP = {
    "--text-575": "--text-2xs", "--text-66": "--text-2xs", "--text-68": "--text-2xs",
    "--text-7": "--text-2xs", "--text-715": "--text-2xs",
    "--text-72": "--text-xs", "--text-74": "--text-xs", "--text-75": "--text-xs",
    "--text-76": "--text-xs", "--text-xs": "--text-xs", "--text-78": "--text-xs",
    "--text-8": "--text-xs",
    "--text-82": "--text-sm", "--text-84": "--text-sm", "--text-85": "--text-sm",
    "--text-86": "--text-sm", "--text-14px": "--text-sm", "--text-sm": "--text-sm",
    "--text-88": "--text-sm", "--text-9": "--text-sm", "--text-92": "--text-sm",
    "--text-93": "--text-sm",
    "--text-9375": "--text-base", "--text-94": "--text-base", "--text-95": "--text-base",
    "--text-95em": "--text-base", "--text-98": "--text-base", "--text-base": "--text-base",
    "--text-102": "--text-base", "--text-105": "--text-base", "--text-md": "--text-base",
    "--text-11": "--text-lg", "--text-14": "--text-xl",
}

# Urutan penting: token dengan nama yang menjadi awalan token lain harus diganti duluan.
for old in sorted(MAP, key=len, reverse=True):
    s = s.replace(f"var({old})", f"var({MAP[old]})")

p.write_text(s)
```

Setelah itu deklarasi lama dihapus satu per satu. Jangan menghapus `--text-2xs` sampai
`--text-3xl`, dan jangan menghapus `--text-sm`/`--text-base` — namanya sama dengan nama baru,
jadi pemakaiannya sudah benar sejak awal.

Terakhir, naikkan cache buster `theme.css` di `app/templates/base.html`.

## Yang tidak perlu diubah

- **Spacing** — `--space-1` sampai `--space-8` sudah 8 langkah dan konsisten dipakai
  (4 / 38 / 30 / 41 / 26 / 3 / 0 / 1). Satu-satunya yang mati adalah `--space-7`; hapus saja.
- **Radius** — `--r-xs` sampai `--r-2xl` dan `--r-pill` sudah 7 langkah yang bersih
  (6, 8, 12, 16, 22, 28, 999). `--r-brand-mark` (11 px) dan `--r-brand-mark-sm` (10 px)
  berbeda 1 px dan tidak dipakai di CSS; hapus atau ganti salah satunya dengan `--r-sm`.
- **Bayangan** — `--shadow-1` sampai `--shadow-3` adalah ramp elevasi yang benar. Empat
  token bayangan khusus (`pin`, `pin-active`, `cluster`, `rail`) memang komponen tunggal,
  biarkan.
- **Warna** — tidak diperiksa di dokumen ini.

## Patokan saat menulis CSS baru

Satu ukuran untuk satu peran. Kalau butuh ukuran yang belum ada, pertanyaannya bukan
"nilai berapa", tapi "peran apa". Kalau perannya sudah ada, pakai tokennya.
