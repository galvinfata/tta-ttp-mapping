# IV.3 Demonstration

> Catatan penulis: penomoran Gambar/Tabel bersifat sementara, sesuaikan dengan babmu. Bagian tangkapan layar (Gambar 4.x) diisi dengan screenshot saat sistem dijalankan.

---

Tahap demonstrasi bertujuan membuktikan bahwa seluruh komponen sistem dapat berjalan secara terintegrasi dalam memetakan TTP dari laporan CTI ke MITRE ATT&CK dan menghasilkan keluaran STIX 2.1. Pada bagian ini diuraikan prasyarat penjalanan sistem, cara penggunaan melalui dua mode antarmuka, serta beberapa skenario penggunaan yang merepresentasikan pemakaian sistem oleh pengguna.

## IV.3.1 Prasyarat dan Konfigurasi

Sebelum dijalankan, sistem memerlukan beberapa prasyarat. Pertama, server LLM lokal LM Studio harus aktif dan menyediakan endpoint yang kompatibel dengan OpenAI, dengan model `qwen/qwen3-4b` telah dimuat. Kedua, dataset laporan CTI (berformat `.json`, `.mjson`, atau `.pdf`) diletakkan pada direktori `data/tram_ii`, dan berkas basis pengetahuan MITRE ATT&CK Enterprise diletakkan pada direktori `data/mitre_cti`. Ketiga, parameter sistem dikonfigurasi melalui berkas `.env`, sebagaimana dicontohkan pada Tabel 4.x. Konfigurasi melalui variabel lingkungan ini memungkinkan perilaku sistem disesuaikan tanpa mengubah kode program.

**Tabel 4.x** Contoh Konfigurasi Sistem pada Berkas .env

| Parameter | Nilai contoh | Keterangan |
|---|---|---|
| LLM_PROVIDER | lmstudio | Penyedia model yang digunakan |
| LOCAL_LLM_BASE_URL | http://100.100.211.39:1234 | Alamat endpoint LM Studio |
| LOCAL_LLM_MODEL | qwen/qwen3-4b | Nama model yang dimuat |
| REVIEWER_ENABLE | false | Mengaktifkan Reviewer Agent (opsional) |
| ATTCK_SOURCE | data/mitre_cti/enterprise-attack.json | Sumber basis pengetahuan ATT&CK |

## IV.3.2 Cara Penggunaan

Sistem dapat digunakan melalui dua mode, yaitu mode baris perintah (CLI) untuk pemrosesan dan evaluasi secara batch, serta mode antarmuka web untuk analisis interaktif satu laporan.

### a. Mode Baris Perintah (CLI)

Pada mode ini, pengguna menjalankan sistem langsung dari terminal. Untuk pemrosesan cepat terhadap sebagian kecil laporan sebagai uji coba, sistem dijalankan melalui berkas utama:

```
python main.py
```

Perintah tersebut memuat dataset, menginisialisasi agen, memproses sejumlah laporan, lalu mencetak metrik evaluasi dan menyimpan hasilnya. Untuk memproses seluruh dataset sekaligus, digunakan skrip pipeline penuh:

```
python scripts/run_full_pipeline.py
```

Adapun untuk mengevaluasi berkas hasil yang sudah ada atau menjalankan evaluasi pada sejumlah laporan tertentu, digunakan skrip evaluasi:

```
python src/evaluation/evaluate_run.py
```

Keluaran dari mode CLI berupa berkas hasil prediksi dalam format JSON pada direktori `results/predictions`, serta metrik Precision, Recall, dan F1-Score yang ditampilkan pada terminal. Rekap evaluasi dalam bentuk berkas Excel dapat dihasilkan melalui skrip `build_excel_report.py`.

### b. Mode Antarmuka Web

Pada mode ini, sistem dijalankan sebagai layanan web menggunakan server uvicorn:

```
python -m uvicorn web.web_app:app --app-dir src --reload
```

Antarmuka kemudian diakses melalui peramban pada alamat `http://127.0.0.1:8000`. Melalui antarmuka ini pengguna dapat mengunggah berkas laporan atau menempelkan teks laporan secara langsung, memantau status pemrosesan, meninjau hasil pemetaan taktik dan teknik, melakukan validasi (menerima atau menolak hasil), serta mengunduh laporan akhir dalam format PDF. Layanan ini menjalankan pemrosesan pada thread terpisah sehingga antarmuka tetap responsif meskipun proses pemetaan berlangsung lama. Tampilan antarmuka web ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Tampilan antarmuka web sistem

## IV.3.3 Skenario Penggunaan

Untuk menggambarkan pemanfaatan sistem secara konkret, berikut diuraikan tiga skenario penggunaan yang mewakili kebutuhan pengguna yang berbeda.

### Skenario 1: Evaluasi Batch oleh Peneliti

Skenario ini merepresentasikan pemakaian sistem untuk keperluan pengujian dan evaluasi menyeluruh. Peneliti menyiapkan seluruh dataset TRAM II pada direktori masukan, lalu menjalankan skrip pipeline penuh melalui mode CLI. Sistem memproses setiap laporan secara berurutan, menghasilkan prediksi teknik untuk seluruh laporan, lalu menghitung metrik kinerja secara keseluruhan. Hasil prediksi disimpan dalam berkas JSON dengan penanda waktu, dan rekap evaluasi disusun ke dalam berkas Excel berisi ringkasan metrik, hasil per laporan, taktik per laporan, serta distribusi taktik. Skenario ini menjadi dasar bagi analisis kinerja sistem pada tahap Evaluation.

### Skenario 2: Analisis Laporan Tunggal oleh Analis CTI

Skenario ini merepresentasikan pemakaian sistem secara interaktif oleh analis keamanan. Analis membuka antarmuka web, lalu mengunggah satu laporan CTI atau menempelkan teksnya. Sistem memproses laporan tersebut melalui pipeline yang sama dan menampilkan hasil pemetaan berupa daftar taktik dan teknik ATT&CK yang teridentifikasi. Analis kemudian meninjau hasil tersebut, menerima pemetaan yang dinilai tepat, dan menolak yang dinilai keliru. Berdasarkan hasil validasi, sistem membentuk bundle STIX 2.1 final dan menyusun laporan PDF yang memuat taktik, teknik, dan kalimat rujukan (evidence) dari teks laporan sebagai dasar setiap pemetaan. Laporan PDF tersebut kemudian dapat diunduh. Skenario ini menonjolkan peran sistem sebagai alat bantu analis, di mana keputusan akhir tetap berada pada manusia.

### Skenario 3: Pemrosesan Laporan Berformat PDF

Skenario ini merepresentasikan kondisi ketika laporan CTI tersedia dalam bentuk dokumen PDF, sebagaimana umum dijumpai pada publikasi threat intelligence. Pengguna cukup menempatkan berkas PDF pada direktori masukan atau mengunggahnya melalui antarmuka web. Sistem secara otomatis mengekstrak teks dari PDF dan mengonversinya ke format JSON yang konsisten dengan pipeline sebelum diproses. Dengan demikian, pengguna tidak perlu melakukan praproses manual, dan sistem dapat menerima masukan dari berbagai format laporan yang umum digunakan.

## IV.3.4 Hasil Demonstrasi

Berdasarkan ketiga skenario di atas, sistem terbukti dapat dijalankan secara terintegrasi dari masukan laporan hingga keluaran STIX 2.1, baik melalui mode baris perintah maupun antarmuka web. Sebagai ilustrasi alur kerja end-to-end, pada bagian berikut diambil salah satu laporan sebagai contoh, dan tahapan pemrosesannya mulai dari masukan hingga keluaran STIX diuraikan beserta perbandingan hasil pemetaan terhadap label acuan.

> (Lanjutkan dengan contoh end-to-end pada satu laporan, mis. "StopRansomware Royal Ransomware": Tabel alur pemrosesan, Tabel perbandingan prediksi vs ground truth, dan contoh objek STIX.)
