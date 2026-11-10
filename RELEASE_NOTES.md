Pembaruan Windows 10/11 64-bit: hentikan pengumuman sejak Siap Dikirim.

- Hanya pesanan instan berstatus Lunas/PAID yang diumumkan.
- Status READY_TO_SHIP/Siap Dikirim menghentikan suara, antrean, dan pengingat, seperti status Sedang Dikirim.
- Pesanan yang pertama ditemukan sudah Siap Dikirim tetap tidak berbunyi, termasuk setelah restart atau respons PAID lama.

- Pilih satu baris, lalu klik Salin nomor pesanan atau tekan Ctrl+C pada tabel.
- Clipboard hanya berisi nomor pesanan yang dipilih, siap ditempel ke pencarian Ginee.
- Pengujian EXE memeriksa pemilihan dua nomor yang berbeda dan penyalinan persis, termasuk angka nol di depan.

- Pengumuman pesanan instan dari marketplace yang terhubung ke Ginee, termasuk Shopee dan TikTok.
- Sebutan khusus untuk instan prioritas; suara wanita Indonesia sudah disertakan.
- Pengumuman dan pengingat berhenti setelah status SHIPPING tersinkron. Suara yang masih antre dilewati dan suara aktif dihentikan.
- Status pesanan terbuka diperiksa setiap siklus melalui API detail; status Sedang Dikirim terlihat di tabel aplikasi.
- Respons PAID lama tidak dapat mengaktifkan kembali pesanan yang sudah tercatat dikirim, termasuk setelah restart.
- Pembacaan ulang 15 menit serta log marketplace/status untuk membantu diagnosis sinkronisasi.
- Kunci API dan riwayat tidak disertakan dalam paket; isi key sendiri di setiap PC.

Jalankan EXE dan uji koneksi serta kedua jenis suara. Pemeriksaan otomatis memverifikasi paket Windows; koneksi akun Ginee dan speaker pengguna tetap perlu diperiksa di PC tujuan.

