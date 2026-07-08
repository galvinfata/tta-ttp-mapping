# Implementasi: Diagram Alir & Penjelasan Fungsi Utama per Modul

> Catatan penulis:
> - Setiap modul diwakili oleh satu fungsi utamanya saja agar pembahasan ringkas dan fokus.
> - Penomoran Gambar bersifat sementara; sesuaikan dengan urutan di babmu.
> - Diagram ditulis dalam format Mermaid; untuk laporan final, ekspor menjadi gambar lalu sisipkan sebagai "Gambar 4.x".
> - Saran penempatan: modul inti pipeline (poin 1–11) di sub-bab Implementasi Pipeline; modul keluaran & antarmuka (poin 12–15) di Lampiran atau bagian Demonstration.

---

## 1. Program Utama — `main.py` : `main()`

Fungsi `main` merupakan titik masuk sistem yang merangkai keseluruhan alur eksekusi. Setelah memastikan konfigurasi valid melalui `validate_setup`, fungsi ini memuat dataset laporan CTI dan basis pengetahuan ATT&CK, menginisialisasi agen, lalu memproses setiap laporan melalui `process_report`. Terdapat percabangan `REVIEWER_ENABLE` yang menegaskan bahwa Reviewer Agent bersifat opsional. Hasil pemrosesan kemudian dievaluasi, dicetak, dan disimpan. Alur fungsi ini ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Diagram alir fungsi main

```mermaid
flowchart TD
    A[Mulai] --> B[validate_setup]
    B --> C{valid?}
    C -- tidak --> Z[return]
    C -- ya --> D[load_tram_dataset, ambil 5 laporan]
    D --> E[load_attck_techniques & _tactics]
    E --> F[buat tactic & technique agent]
    F --> G{REVIEWER_ENABLE?}
    G -- ya --> G1[create_reviewer_agent]
    G -- tidak --> G2[reviewer = None]
    G1 --> H[loop tiap laporan: process_report]
    G2 --> H
    H --> I[evaluate_predictions]
    I --> J[cetak metrik & save_results]
    J --> Z2[return]
```

## 2. Pemuatan Dataset — `data_loader.py` : `load_tram_dataset()`

Fungsi `load_tram_dataset` membaca seluruh berkas dataset TRAM II dari sebuah direktori. Mula-mula seluruh berkas PDF dikonversi otomatis menjadi JSON, lalu setiap berkas `.json`/`.mjson` dibaca untuk diekstraksi teks laporan dan label teknik acuannya. Berkas dengan JSON tidak valid dilewati, dan hanya laporan yang memiliki teks yang dimasukkan ke daftar keluaran. Alur fungsi ini ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Diagram alir fungsi load_tram_dataset

```mermaid
flowchart TD
    A[Mulai] --> B[Konversi otomatis PDF ke JSON]
    B --> C[Kumpulkan berkas .json dan .mjson]
    C --> D[Untuk tiap berkas: baca JSON]
    D --> E{JSON valid?}
    E -- tidak --> D
    E -- ya --> F[Ekstrak teks & label teknik]
    F --> G{Teks ada?}
    G -- ya --> H[Tambahkan ke daftar laporan]
    G -- tidak --> D
    H --> D
    D --> I[Kembalikan daftar laporan]
```

## 3. Pemuatan Basis Pengetahuan — `attck_loader.py` : `load_attck_techniques()`

Fungsi `load_attck_techniques` membangun basis pengetahuan teknik ATT&CK dari berkas MITRE CTI. Fungsi ini menelusuri seluruh objek, menyaring hanya objek bertipe attack-pattern yang tidak usang, mengambil identitas teknik dan taktik (fase kill-chain) dari referensi eksternalnya, lalu menyusunnya menjadi sebuah dictionary. Apabila sebuah teknik muncul lebih dari sekali, datanya digabungkan. Alur fungsi ini ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Diagram alir fungsi load_attck_techniques

```mermaid
flowchart TD
    A[Mulai] --> B[Untuk tiap berkas ATT&CK: baca JSON]
    B --> C[Untuk tiap objek]
    C --> D{attack-pattern & tidak usang?}
    D -- tidak --> C
    D -- ya --> E[Ambil ID teknik & taktik]
    E --> F{ID teknik valid?}
    F -- tidak --> C
    F -- ya --> G{ID sudah ada?}
    G -- ya --> H[Gabungkan taktik, domain, deskripsi]
    G -- tidak --> I[Buat entri teknik baru]
    H --> C
    I --> C
    C --> J[Kembalikan dictionary teknik]
```

## 4. Identifikasi Taktik — `tactic_agent.py` : `identify_tactics()`

Fungsi `identify_tactics` memetakan teks laporan ke taktik ATT&CK dari daftar tertutup 14 taktik Enterprise. Permintaan ke model menggunakan structured output JSON, dengan mode penalaran model dinonaktifkan agar keluaran tetap dihasilkan. Apabila penguraian JSON gagal, digunakan mekanisme cadangan berbasis ekspresi reguler. Setiap pemanggilan dibungkus retry berjenjang dengan exponential backoff dan dukungan model cadangan. Alur fungsi ini ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Diagram alir fungsi identify_tactics

```mermaid
flowchart TD
    A[Mulai] --> B[Susun prompt daftar 14 taktik]
    B --> C{Ada umpan balik reviewer?}
    C -- ya --> C1[Sisipkan umpan balik, naikkan temperature]
    C -- tidak --> D[Percobaan: model utama lalu cadangan]
    C1 --> D
    D --> E[Panggil LLM dengan structured output JSON]
    E --> F{Keluaran kosong?}
    F -- ya --> G[Retry backoff / pindah model]
    G --> D
    F -- tidak --> H[Uraikan JSON]
    H -- gagal --> I[Cadangan regex pola TA####]
    H -- berhasil --> J[Validasi ke daftar taktik resmi]
    I --> J
    J --> K[Kembalikan daftar tactic ID valid]
```

## 5. Ekstraksi Teknik — `technique_agent.py` : `extract_techniques()`

Fungsi `extract_techniques` merupakan tahap paling kompleks karena ruang teknik ATT&CK sangat besar. Untuk mengatasinya, sistem terlebih dahulu mempersempit kandidat melalui retrieval TF-IDF (50 teknik teratas), lalu memecah laporan panjang menjadi beberapa potongan dengan tumpang tindih agar tidak ada teknik yang hilang. Model hanya boleh memilih dari daftar kandidat, dan hasil tiap potongan digabungkan tanpa duplikasi. Alur fungsi ini ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Diagram alir fungsi extract_techniques

```mermaid
flowchart TD
    A[Mulai] --> B[Retrieval kandidat: 50 teknik teratas TF-IDF]
    B --> C{Kandidat kosong?}
    C -- ya --> C1[Kembalikan daftar kosong]
    C -- tidak --> D[Format daftar kandidat sesuai batas konteks]
    D --> E[Pecah laporan jadi maksimal 4 potongan]
    E --> F[Untuk tiap potongan: panggil LLM dari kandidat]
    F --> G[Uraikan, validasi ke basis pengetahuan, retry]
    G --> H[Gabungkan hasil tanpa duplikat]
    H --> I{Masih ada potongan?}
    I -- ya --> F
    I -- tidak --> J[Kembalikan daftar teknik]
```

## 6. Review Konsistensi — `reviewer_agent.py` : `review_tactics_and_techniques()`

Fungsi `review_tactics_and_techniques` menilai apakah taktik dan teknik yang diperoleh konsisten dengan teks laporan. Fungsi ini menyusun ringkasan taktik dan teknik, mengirimkannya ke model bersama kutipan laporan, lalu mengembalikan penilaian berupa status valid beserta umpan balik. Sama seperti agen lain, terdapat mekanisme cadangan penguraian dan retry. Alur fungsi ini ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Diagram alir fungsi review_tactics_and_techniques

```mermaid
flowchart TD
    A[Mulai] --> B[Susun ringkasan taktik & teknik]
    B --> C[Susun prompt reviewer]
    C --> D[Percobaan: model utama lalu cadangan]
    D --> E[Panggil LLM]
    E --> F{Keluaran kosong?}
    F -- ya --> G[Retry backoff / pindah model]
    G --> D
    F -- tidak --> H[Uraikan objek JSON]
    H -- gagal --> I[Cadangan: deteksi valid/invalid dari teks]
    H -- berhasil --> J[Kembalikan status valid & umpan balik]
    I --> J
```

## 7. Orkestrasi Pipeline — `orchestrator.py` : `process_report()`

Fungsi `process_report` memproses satu laporan melalui keseluruhan pipeline LangGraph. Fungsi ini menyiapkan state awal, lalu menjalankan graf yang merangkai node `input_report`, `tactic_extraction`, `technique_extraction`, `review`, dan `post_process`, termasuk percabangan revisi setelah node review. Keluarannya berupa teknik prediksi, taktik teridentifikasi, dan bundle STIX. Alur fungsi ini ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Diagram alir fungsi process_report

```mermaid
flowchart TD
    A[Mulai] --> B[Susun state awal pipeline]
    B --> C[input_report]
    C --> D[tactic_extraction]
    D --> E[technique_extraction]
    E --> F[review]
    F --> G{valid atau iterasi maksimum?}
    G -- tidak --> D
    G -- ya --> H[post_process: rekonsiliasi, validasi, STIX]
    H --> I[Kembalikan hasil pemetaan & bundle STIX]
```

## 8. Rekonsiliasi — `reconciler.py` : `reconcile_results()`

Fungsi `reconcile_results` menyaring teknik mentah agar konsisten dengan taktik yang teridentifikasi, dengan memetakan identitas taktik ke nama fase kill-chain. Terdapat dua pengaman penting: apabila tidak ada taktik teridentifikasi, seluruh teknik valid dipertahankan; dan apabila penyaringan justru menghapus seluruh teknik valid, hasil tidak dikosongkan agar recall tidak hilang. Alur fungsi ini ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Diagram alir fungsi reconcile_results

```mermaid
flowchart TD
    A[Mulai] --> B{Daftar teknik kosong?}
    B -- ya --> B1[Kembalikan kosong]
    B -- tidak --> C[Petakan tactic ID ke fase kill-chain]
    C --> D[Ambil teknik yang ada di basis pengetahuan]
    D --> E{Ada taktik teridentifikasi?}
    E -- tidak --> F[Pengaman 1: pertahankan semua teknik valid]
    E -- ya --> G[Pertahankan teknik yang fasenya konsisten]
    G --> H{Hasil penyaringan kosong?}
    H -- ya --> I[Pengaman 2: pertahankan semua teknik valid]
    H -- tidak --> J[Buang duplikat, pertahankan urutan]
    F --> J
    I --> J
    J --> K[Kembalikan daftar teknik final]
```

## 9. Validasi — `validator.py` : `validate_techniques()`

Fungsi `validate_techniques` memverifikasi setiap teknik hasil rekonsiliasi terhadap basis pengetahuan ATT&CK. Teknik dipisahkan menjadi valid (terdapat di basis pengetahuan) dan tidak valid, dan hanya teknik valid yang diteruskan ke tahap berikutnya. Alur fungsi ini ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Diagram alir fungsi validate_techniques

```mermaid
flowchart TD
    A[Mulai] --> B[Untuk tiap teknik]
    B --> C{Ada di basis pengetahuan?}
    C -- ya --> D[Masukkan ke daftar valid]
    C -- tidak --> E[Masukkan ke daftar tidak valid]
    D --> B
    E --> B
    B --> F[Kembalikan teknik valid & tidak valid]
```

## 10. Pembentukan STIX — `stix_builder.py` : `build_stix_bundle()`

Fungsi `build_stix_bundle` mengonversi daftar teknik final menjadi keluaran STIX 2.1. Setiap teknik diubah menjadi objek AttackPattern lengkap dengan referensi eksternal ke laman MITRE ATT&CK dan fase kill-chain-nya, lalu seluruh objek dirangkai menjadi satu bundle. Alur fungsi ini ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Diagram alir fungsi build_stix_bundle

```mermaid
flowchart TD
    A[Mulai] --> B[Untuk tiap teknik]
    B --> C{Ada di basis pengetahuan?}
    C -- tidak --> B
    C -- ya --> D[Buat objek AttackPattern + referensi & fase]
    D --> B
    B --> E{Ada objek terbentuk?}
    E -- tidak --> F[Kembalikan bundle kosong]
    E -- ya --> G[Rangkai jadi satu bundle STIX 2.1]
    G --> H[Kembalikan bundle]
```

## 11. Evaluasi — `evaluator.py` : `evaluate_predictions()`

Fungsi `evaluate_predictions` menghitung metrik kinerja sistem dengan membandingkan teknik prediksi terhadap label acuan. Perhitungan dilakukan dalam dua mode: exact (identitas persis termasuk sub-teknik) dan base-technique (dinormalisasi ke teknik induk) agar ketidakcocokan granularitas sub-teknik tidak menghukum hasil secara berlebihan. Alur fungsi ini ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Diagram alir fungsi evaluate_predictions

```mermaid
flowchart TD
    A[Mulai] --> B[Ambil ground truth & prediksi]
    B --> C[Hitung skor mode exact]
    C --> D[Normalisasi ke base-technique]
    D --> E[Hitung skor mode base]
    E --> F[Kembalikan metrik exact, base, total]
```

---

## Modul Keluaran & Antarmuka (disarankan ke Lampiran / Demonstration)

## 12. Penelusuran Bukti — `evidence.py` : `build_evidence_map()`

Fungsi `build_evidence_map` mencari kalimat rujukan (evidence) untuk tiap taktik dan teknik yang terpetakan menggunakan TF-IDF, tanpa pemanggilan LLM tambahan. Hasilnya dipakai untuk menampilkan dasar setiap pemetaan pada laporan PDF. Alur fungsi ini ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Diagram alir fungsi build_evidence_map

```mermaid
flowchart TD
    A[Mulai] --> B[Pecah laporan menjadi kalimat]
    B --> C{Ada kalimat?}
    C -- tidak --> C1[Kembalikan peta kosong]
    C -- ya --> D[Bangun TF-IDF kalimat]
    D --> E[Teknik: cari kalimat paling mirip nama+deskripsi]
    E --> F[Taktik: cocokkan nama, jika gagal pakai kalimat teknik se-fase]
    F --> G[Kembalikan peta bukti taktik & teknik]
```

## 13. Laporan PDF — `report_builder.py` : `build_pdf_report()`

Fungsi `build_pdf_report` menyusun dokumen PDF hasil pemetaan, berisi metadata laporan, tabel taktik dan teknik beserta kalimat rujukannya, serta ringkasan STIX. Alur fungsi ini ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Diagram alir fungsi build_pdf_report

```mermaid
flowchart TD
    A[Mulai] --> B[Siapkan dokumen & gaya]
    B --> C[Tulis judul & tabel metadata]
    C --> D{Ada taktik?}
    D -- ya --> D1[Tabel taktik + bukti]
    D -- tidak --> D2[Teks 'tidak ada taktik']
    D1 --> E{Ada teknik?}
    D2 --> E
    E -- ya --> E1[Tabel teknik + bukti]
    E -- tidak --> E2[Teks 'tidak ada teknik']
    E1 --> F[Bangun PDF & kembalikan bytes]
    E2 --> F
```

## 14. Antarmuka Web — `web_app.py` : `process()` dan `_run_job()`

Endpoint `process` menerima unggahan laporan, membuat sebuah job, lalu menjalankannya pada thread terpisah melalui `_run_job` agar tidak menahan server selama proses panjang. Pengguna kemudian memantau status, mengambil hasil, memvalidasinya, dan mengunduh laporan PDF. Inti pemetaan tetap dilakukan oleh `process_report` yang sama dengan pipeline. Alur ringkas ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Diagram alir layanan pemrosesan web (process & _run_job)

```mermaid
flowchart TD
    A[Mulai: unggah laporan] --> B[Baca & validasi masukan]
    B --> C[Buat job, status: queued]
    C --> D[Jalankan _run_job di thread terpisah]
    D --> E[process_report]
    E --> F[Susun hasil taktik & teknik]
    F --> G[Status: done]
    E -. error .-> H[Status: error]
    C --> I[Kembalikan job_id ke pengguna]
```

## 15. Laporan Excel — `build_excel_report.py` : `main()`

Fungsi `main` pada modul ini menghasilkan laporan evaluasi dalam berkas Excel dari berkas hasil prediksi. Workbook yang dibentuk berisi empat lembar: ringkasan metrik, hasil per laporan, taktik per laporan, dan distribusi taktik. Alur fungsi ini ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Diagram alir fungsi main (build_excel_report)

```mermaid
flowchart TD
    A[Mulai] --> B[Tentukan berkas hasil & keluaran]
    B --> C[Muat hasil prediksi & basis pengetahuan]
    C --> D[Bangun 4 lembar: ringkasan, per laporan, taktik, distribusi]
    D --> E[Simpan workbook]
    E --> F[Cetak ringkasan metrik]
```

---

## Catatan modul pendukung lain
Modul `pdf_to_json_converter.py` (konversi PDF ke JSON sebagai alat bantu), serta `evaluate_run.py` dan `run_full_pipeline.py` (skrip alternatif untuk menjalankan dan mengevaluasi pipeline pada seluruh dataset) memiliki alur yang serupa dengan `main()` dan tidak perlu digambarkan terpisah pada badan laporan. Apabila diperlukan, diagram alirnya dapat disertakan pada Lampiran.
