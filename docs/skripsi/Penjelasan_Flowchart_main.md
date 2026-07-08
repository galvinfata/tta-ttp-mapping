# Penjelasan Flowchart Program Utama (main.py)

> Catatan: penomoran Gambar bersifat sementara, sesuaikan dengan urutan di babmu. Bagian ini cocok diletakkan di awal sub-bab Implementasi (sebagai gambaran alur program utama) sebelum pembahasan tiap node pipeline.

---

Program utama diimplementasikan pada berkas `main.py` yang berperan sebagai titik masuk (entry point) sistem. Berkas ini terdiri atas dua fungsi, yaitu `validate_setup` yang memvalidasi kelengkapan konfigurasi awal dan `main` yang mengatur keseluruhan alur eksekusi mulai dari pemuatan data hingga penyimpanan hasil.

## Validasi Konfigurasi Awal (validate_setup)

Sebelum pipeline dijalankan, sistem terlebih dahulu memastikan bahwa seluruh prasyarat lingkungan telah terpenuhi melalui fungsi `validate_setup`. Diagram alir fungsi ini ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Diagram alir fungsi validate_setup

Sebagaimana ditunjukkan pada Gambar 4.x, fungsi ini diawali dengan membaca variabel lingkungan `LLM_PROVIDER`, kemudian melakukan tiga pemeriksaan secara berurutan. Pemeriksaan pertama memastikan ketersediaan berkas dataset laporan CTI pada direktori `data/tram_ii`. Pemeriksaan kedua memastikan ketersediaan berkas basis pengetahuan MITRE ATT&CK pada direktori `data/mitre_cti`. Pemeriksaan ketiga memastikan kelengkapan konfigurasi server LLM lokal, yaitu apabila penyedia yang dipilih adalah LM Studio namun alamat `BASE_URL` belum diisi.

Setiap pemeriksaan yang gagal akan menetapkan status `ok` menjadi salah (False) dan mencetak pesan yang menjelaskan kekurangan tersebut. Penting untuk dicatat bahwa kegagalan pada satu pemeriksaan tidak langsung menghentikan fungsi, melainkan proses tetap dilanjutkan ke pemeriksaan berikutnya. Dengan demikian, seluruh masalah konfigurasi yang ada dapat dilaporkan sekaligus dalam satu kali eksekusi sehingga memudahkan pengguna memperbaikinya. Pada akhir fungsi, apabila status `ok` bernilai salah, sistem mencetak pesan bahwa setup belum lengkap, lalu fungsi mengembalikan status `ok` tersebut. Mekanisme ini memastikan sumber daya berat seperti model dan basis pengetahuan hanya dimuat ketika lingkungan benar-benar siap.

## Alur Eksekusi Utama (main)

Setelah validasi konfigurasi, keseluruhan proses dikendalikan oleh fungsi `main` yang menjadi pengatur alur eksekusi sistem. Diagram alir fungsi ini ditunjukkan pada Gambar 4.x.

**Gambar 4.x** Diagram alir fungsi main

Sebagaimana terlihat pada Gambar 4.x, fungsi `main` diawali dengan memanggil `validate_setup`. Apabila konfigurasi dinyatakan tidak valid, program langsung berhenti agar tidak melanjutkan proses pada lingkungan yang belum siap. Apabila valid, sistem memuat dataset laporan CTI melalui `load_tram_dataset` dan mengambil lima laporan pertama sebagai subset pengujian. Selanjutnya sistem memuat basis pengetahuan ATT&CK, baik daftar teknik maupun taktik, lalu menginisialisasi Tactic Agent dan Technique Agent.

Setelah agen utama siap, terdapat percabangan berdasarkan variabel lingkungan `REVIEWER_ENABLE` yang menentukan apakah Reviewer Agent diaktifkan. Apabila aktif, sistem membuat Reviewer Agent; apabila tidak, nilai reviewer ditetapkan kosong (None). Percabangan ini menegaskan bahwa Reviewer Agent merupakan komponen opsional. Selanjutnya, setiap laporan pada subset diproses secara berulang melalui fungsi `process_report`, yang menjalankan keseluruhan pipeline pemetaan TTP dan menghasilkan kumpulan hasil. Hasil tersebut kemudian dievaluasi menggunakan `evaluate_predictions` untuk menghitung metrik Precision, Recall, dan F1-Score, dicetak ke layar, lalu disimpan ke berkas melalui `save_results`.

Dengan demikian, `main.py` berperan sebagai kerangka eksekusi tingkat atas yang merangkai validasi konfigurasi, pemuatan data dan basis pengetahuan, inisialisasi agen, pemrosesan laporan, hingga evaluasi dan penyimpanan hasil. Adapun proses pemetaan TTP yang sebenarnya berlangsung di dalam `process_report`, yang dijelaskan secara rinci pada bagian Implementasi Pipeline.
