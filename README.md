# aim-input-repo

## Login ke EPrints

Script awal ini melakukan login ke Repository Universitas Hasanuddin dan
membuka halaman **Items**. Kredensial dibaca dari `.env` dan tidak disimpan di
kode.

### Persiapan

1. Buat virtual environment dan pasang dependency:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   playwright install chromium
   ```

   Firewall repositori EPrints memblokir Chromium headless (halaman "Web Page
   Blocked", User-Agent `HeadlessChrome`), sehingga langkah yang menyentuh
   EPrints harus memakai `--headed`. Di VPS tanpa desktop, jalankan browser
   terlihat di dalam Xvfb:

   ```bash
   sudo apt install -y xvfb
   xvfb-run -a python eprint_login.py --headed
   ```

2. Salin `.env.example` menjadi `.env`, lalu isi:

   ```dotenv
   EPRINT_USERNAME=username_anda
   EPRINT_PASSWORD=password_anda
   ```

### Menjalankan

Login EPrints harus memakai browser terlihat (`--headed`); mode headless
ditolak oleh firewall repositori. Di VPS tanpa desktop, awali dengan
`xvfb-run -a`:

```bash
python eprint_login.py --headed
```

Tambahkan `--keep-open` untuk membiarkan browser tetap terbuka saat debugging:

```bash
python eprint_login.py --headed --keep-open
```

### Mencari nama mahasiswa dari NIM

Setelah menambahkan `RTA_EMAIL` dan `RTA_PASSWORD` ke `.env`, jalankan:

```bash
python eprint_login.py --lookup-nim H011191003
```

Perintah ini login ke RTA, mencari NIM, membuka halaman Detail, dan mencetak
nama lengkap mahasiswa. RTA tidak memblokir mode headless, jadi `--headed`
tidak diperlukan. Tahap upload EPrints belum dijalankan oleh perintah ini.

### Menyiapkan satu item pertama

Untuk uji coba satu file PDF pertama berdasarkan urutan nama file:

```bash
python eprint_login.py --prepare-first September --headed
```

Perintah ini mengambil NIM dari nama file, mengambil nama dan judul dari RTA,
membuat item bertipe thesis, mengunggah PDF, mengatur **Visible to** menjadi
**Repository staff only**, mengatur bahasa menjadi **Indonesian**, lalu mengisi
judul, abstrak, jenis thesis, gelar, dan creator dari data RTA. Perintah
berhenti sebelum submit/publikasi akhir.

Data pembimbing, division, dan publication detail juga diisi dari RTA:
kontributor diisi dengan nama dan NIDN/NIDK, division dicocokkan ke pilihan
repositori, status publication menjadi **Published**, date type menjadi
**Publication**, institusi menjadi **UNIVERSITAS HASANUDDIN**, dan department
menggunakan nilai **Program Studi Format Eprint**.

Untuk program Matematika, subjects **Q Science (General)** dan **QA
Mathematics** ditambahkan. Pada UI batch, tanggal publication diisi manual
berdasarkan halaman pengesahan PDF. Setelah subjects ditambahkan dan Next ditekan, script berhenti
sebelum submit/publikasi akhir.

Sebelum membuat item baru, script memeriksa judul pada halaman Items. Jika
judul dari RTA sudah ada, file dilewati dan tidak di-upload. Untuk memproses
beberapa file terurut berdasarkan nama, gunakan `--count`:

```bash
python eprint_login.py --prepare-first September --count 5 --headed --deposit
```

Hilangkan `--deposit` jika ingin berhenti setelah menyiapkan draft. `--deposit`
harus ditambahkan secara eksplisit untuk mengirim item ke repositori. Setiap
file diproses pada pasangan tab yang ditutup setelah selesai; jika satu file
gagal, file berikutnya tetap dicoba dan kegagalannya dicetak ke terminal.

### UI batch lokal

UI Flask menyimpan daftar kandidat dan statusnya di SQLite lokal. UI tidak
menyimpan kredensial; isi `EPRINT_USERNAME`, `EPRINT_PASSWORD`, `RTA_EMAIL`,
dan `RTA_PASSWORD` di `.env` seperti di atas. Jalankan:

```bash
python app.py
```

Buat atau perbarui sesi login EPrints tersimpan satu kali. Perintah ini
menggunakan `EPRINT_USERNAME` dan `EPRINT_PASSWORD` dari `.env`, membuka
browser terlihat, lalu login dan menyimpan cookie secara otomatis:

```bash
python eprint_login.py --login --headed
```

Setelah halaman **Manage deposits** tampil, browser akan ditutup otomatis dan
cookie login tetap tersimpan di `.eprints-browser-profile` selama 24 jam; setelah
itu jalankan perintah ini lagi. Cookie sesi EPrints bersifat sementara sehingga
script memberinya masa berlaku agar bertahan di profil. Folder profil berisi
sesi login aktif, diabaikan oleh git (`.gitignore`), dan tidak boleh di-commit.
Jika browser ingin dibiarkan terbuka untuk pemeriksaan, gunakan:

```bash
python eprint_login.py --login --headed --keep-open
```

Dengan opsi tersebut, tekan Enter setelah halaman **Manage deposits** tampil
untuk menutup browser.

Di VPS tanpa desktop, `--login` dan tombol **Process** tetap membutuhkan
browser terlihat, jadi awali perintahnya dengan `xvfb-run -a`:

```bash
xvfb-run -a python eprint_login.py --login
xvfb-run -a python app.py
```

Jika Chromium langsung tertutup saat membuka profil (`TargetClosedError`),
biasanya profil lama hasil clone sudah korup. Hapus folder
`.eprints-browser-profile`, lalu jalankan `--login` lagi untuk membuat profil
baru.

Sebelum menekan tombol **Process** di UI, pastikan browser login EPrints sudah
ditutup. UI memakai profil browser tersimpan yang sama, sehingga browser yang
masih terbuka akan mengunci profil dan proses tidak dapat dimulai.

Setelah login berhasil, buka `http://127.0.0.1:5000`, pilih nama folder bulan
dan jumlah PDF baru.
Kandidat yang sudah tercatat di database tidak dihitung lagi, termasuk yang
sudah selesai, sehingga `Count 4` menambahkan empat file baru. Kandidat PDF
diurutkan berdasarkan nama, lalu NIM, judul, dan nama diambil dari RTA.
Gunakan tautan preview untuk memeriksa berkas dan isi tanggal persetujuan
per berkas. Klik **Save approval dates** untuk menyimpan persetujuan, lalu
Setelah tanggal disimpan, setiap baris yang siap akan memiliki tombol
**Process** sendiri. Klik tombol pada baris yang diinginkan untuk memproses
tepat satu file; browser popup akan terbuka seperti proses terminal. Tidak ada
file lain yang ikut diproses. Pastikan sesi login tersimpan sudah dibuat dengan
`python eprint_login.py --login`; proses memakai profil tersebut.
Jika proses terputus atau sebuah file gagal setelah disetujui, klik tombol yang
sama untuk melanjutkan file tersebut; status `approved`, `processing`, dan
`failed` dengan tanggal persetujuan akan dicoba kembali. Kandidat scan yang
belum disetujui tidak ikut diproses. Sebelum membuat item, sistem tetap
memeriksa judul yang sudah ada sehingga deposit yang sudah sempat berhasil
tidak dibuat ulang.
Proses memakai satu instance browser headless dan tab sementara per file, lalu
selalu menutupnya setelah scan atau batch selesai. Setiap file memiliki batas
waktu sendiri; jika RTA bermasalah untuk satu file, file berikutnya tetap
dicoba. Kandidat yang sudah selesai atau judulnya sudah ada dilewati. Database
default bernama
`eprints_ui.sqlite3` dan dapat diganti dengan `EPRINTS_UI_DATABASE`.
