# Pengumuman Pesanan Instan — Ginee OMS

Aplikasi Windows untuk mengumumkan pesanan instan yang sudah lunas atau siap diproses di Ginee OMS, termasuk Shopee dan TikTok. Layanan instan prioritas mendapat pengumuman khusus.

## Unduh dan jalankan

Unduh **PengumumanPesananInstan.exe** dari [Releases](https://github.com/vidiciindonesia-crypto/ginee-instant-notifier/releases/latest). File ini untuk **Windows 10/11 64-bit**, sudah memuat Python, antarmuka, dan dua rekaman suara wanita Indonesia. PC penerima tidak perlu menginstal Python.

1. Jalankan EXE dan buka **Pengaturan API & layanan**.
2. Masukkan Access Key dan Secret Key Ginee di komputer tersebut, lalu simpan.
3. Klik **Mulai / uji koneksi**, **Tes instan biasa**, dan **Tes suara & pop-up**.
4. Minimalkan aplikasi untuk tetap memantau. Menutup aplikasi atau membiarkan PC sleep menghentikan pemantauan.

Untuk mengecek pesanan di Ginee, pilih satu baris pada tabel lalu klik **Salin nomor pesanan**, atau tekan **Ctrl+C** saat tabel aktif. Hanya nomor pesanan marketplace dari baris tersebut yang disalin, sehingga bisa langsung ditempel di pencarian Ginee.

Setelah diunduh, file EXE dapat disalin ke PC lain. Kunci Ginee diisi pada masing-masing PC.

## Perilaku

- Pengecekan otomatis, bawaan setiap 30 detik, mengikuti ketersediaan data API Ginee.
- Layanan yang mengandung `instant` atau `instan` diumumkan dari semua marketplace yang dikembalikan Ginee. Pola layanan dapat diubah di pengaturan.
- Layanan seperti `SPX Instant Prioritas` disebut sebagai prioritas.
- Setiap pesanan dicatat lokal untuk mencegah pengumuman berulang saat refresh/restart.
- Ketika status `SHIPPING` (Sedang Dikirim) tersinkron, pengumuman dan pengingat pesanan itu berhenti. Antrean suaranya dilewati; audio yang masih berjalan dihentikan. Pesanan tersebut tetap dibisukan setelah restart, termasuk bila ada respons `PAID` lama yang terlambat diterima.
- Status pesanan yang masih menunggu penanganan juga diperiksa langsung melalui API detail setiap siklus, sehingga penghentian suara tidak hanya bergantung pada daftar perubahan pesanan. Waktu pembaruan mengikuti interval sinkronisasi dan ketersediaan API Ginee (bawaan 30 detik).
- Timer packing lokal hanya untuk Shopee Instan Prioritas: `payAt + 15 menit`. TikTok tidak memakai aturan ini. Timer tidak menentukan apakah pengumuman boleh berbunyi.
- Pembacaan ulang 15 menit mengurangi risiko pembaruan API yang terlambat terlewat. Penundaan atau informasi logistik yang belum tersedia dari Ginee tetap dapat memengaruhi deteksi.
- Log menampilkan marketplace, status API, nomor pesanan, dan layanan tanpa data pembeli. Tombol **Salin log** membantu pemeriksaan pesanan yang belum diumumkan.
- Suara `id-ID-GadisNeural` sudah direkam ke MP3; pemutaran suara tidak memanggil layanan TTS saat pesanan masuk. Aplikasi tetap memerlukan internet untuk API Ginee.

## Data lokal dan kunci

Kunci API disimpan memakai Windows DPAPI untuk akun Windows yang memasukkannya. Pengaturan dan riwayat ada di `%LOCALAPPDATA%\GineeInstantNotifier`.

Kunci, pengaturan akun, dan riwayat pesanan tidak termasuk source atau EXE. Isi key pada setiap PC. Riwayat antarkomputer tidak disinkronkan; tiap PC dapat mengumumkan pesanan yang sama secara terpisah.

Endpoint baca yang digunakan:

- `POST /openapi/v3/oms/order/list`
- `POST /openapi/v3/oms/order/item/batch-get`

## Build Windows

GitHub Actions membangun EXE di Windows, menjalankan unit test dan pemeriksaan EXE, lalu membuat Release. Tidak ada key Ginee yang diperlukan untuk build.

Untuk build manual di Windows dengan Python 3.12 64-bit:

```powershell
python -m pip install -r requirements-build.txt
python -m unittest -v test_core test_audio
python build_exe.py
```

Hasilnya `dist\PengumumanPesananInstan.exe`.

Pemeriksaan paket:

```powershell
$p = Start-Process .\dist\PengumumanPesananInstan.exe -ArgumentList '--self-test --self-test-report runtime-check.json' -Wait -PassThru
Get-Content runtime-check.json
if ($p.ExitCode -ne 0) { throw 'Runtime check failed' }
```

Pemeriksaan ini menguji Tcl/Tk, SQLite, DPAPI, mutex Windows, SSL, dan keberadaan rekaman dalam EXE. Bunyi melalui perangkat speaker serta koneksi akun Ginee perlu diuji pada PC pengguna.

