# Outline Detail BAB 4 — Fokus "Proses Pembuatan Aplikasi" dalam DSRM

Catatan kerja:
- Proses pembuatan aplikasi = tahap **Design and Development** (DSRM). Tidak perlu bab/SDLC terpisah.
- Yang ditambahkan ke draft: (a) narasi realisasi per-modul yang lebih "bercerita", (b) **sub-bagian baru "Keputusan Desain & Iterasi Teknis"** sebagai inti proses pembuatan, (c) **potongan kode kunci** yang dijelaskan.
- Legenda: [P] = paragraf, [Tabel], [Gambar], [Kode] = snippet kode yang perlu ditampilkan.

---

## 4.1 Identify Problem, Motivate, & Define Objectives  *(sudah ada — pertahankan)*
- [P] Masalah: laporan CTI tidak terstruktur, TTP implisit → pemetaan manual lambat & tidak konsisten.
- [P] Motivasi & solusi: LLM lokal sebagai sistem multi-agent untuk otomasi pemetaan TTP → MITRE ATT&CK Enterprise → keluaran STIX 2.1.
- [P] Objective (terukur): (1) pipeline multi-agent end-to-end berjalan; (2) menghasilkan STIX 2.1 valid; (3) dievaluasi P/R/F1 pada TRAM II.
- *Tidak perlu diubah; cukup pastikan objective dinyatakan terukur agar nyambung ke Evaluation.*

---

## 4.2 Design and Development  ← **DI SINI PROSES PEMBUATAN APLIKASI**

Saran sub-struktur (4 sub-bagian, tambah satu di akhir):

### 4.2.1 Arsitektur Sistem Multi-Agent  *(sudah ada — perkuat dgn gambar)*
- [P] Ringkas: pipeline multi-agent diorkestrasi LangGraph (StateGraph), tiap tahap = node yang membaca/menulis satu state bersama `PipelineState`.
- [P] Empat lapisan: data, agen, orkestrasi, keluaran+antarmuka (sudah ada).
- [P] **Deviasi dari Bab III** (PENTING — ini bagian dari "proses"): Orchestrator tidak jadi agen LLM, tapi graf LangGraph; Reconciler & Validator jadi modul deterministik berbasis aturan. Jelaskan *alasannya*: hemat panggilan LLM, deterministik = mudah diuji & direproduksi.
- [Gambar 4.x] Diagram arsitektur 4-lapis. (Ambil dari dokumen arsitektur yang sudah dibuat / gambar ulang.)
- [Gambar 4.x] Diagram alur pipeline LangGraph (node + conditional edge revisi). (Ambil flowchart `_build_graph` di dokumen flowchart.)
- [Tabel 4.1] Komponen penyusun arsitektur (sudah ada).
- [P] Penjelasan `PipelineState`: field utama (report_text, tactics_identified, techniques_raw, predicted_techniques, reviewer_feedback, review_iterations, stix_bundle) + manfaat: tiap node mandiri.
- [Kode 4.1] Definisi `PipelineState` (TypedDict) + `_build_graph()` (nodes & edges). → menunjukkan keputusan desain orkestrasi secara konkret.

### 4.2.2 Lingkungan Implementasi  *(sudah ada)*
- [P] Python; LangGraph; klien OpenAI → endpoint lokal LM Studio; scikit-learn (TF-IDF) untuk retrieval; stix2; reportlab/openpyxl; FastAPI.
- [Tabel 4.2] Lingkungan implementasi (komponen + versi/peran).
- [P] Konfigurasi via ENV (LOCAL_LLM_BASE_URL, LOCAL_LLM_MODEL=qwen/qwen3-4b, TECHNIQUE_CANDIDATE_TOP_K, LLM_REVIEW_MAX_ITER, REVIEWER_ENABLE, dll). Tekankan: perilaku diatur tanpa ubah kode → keputusan desain agar mudah dieksperimen.
- [Tabel 4.x] (opsional) Daftar parameter ENV penting + nilai default + fungsinya.

### 4.2.3 Implementasi Pipeline (per Tahap/Modul)  *(perluas jadi naratif "kebutuhan → desain → realisasi → tantangan")*
Untuk tiap tahap pakai pola konsisten: **apa kebutuhannya → bagaimana dirancang → bagaimana direalisasikan → tantangan teknis & solusi**.

- [P+Tabel 4.3] Lima node berurutan (input_report, tactic_extraction, technique_extraction, review, post_process) + conditional edge.

a) **Identifikasi Taktik (Tactic Agent)**
- [P] Kebutuhan: pilih dari 14 taktik tertutup → ruang kecil, fokus presisi.
- [P] Realisasi: structured output skema JSON `{"ids":[...]}`; system prompt + daftar taktik; validasi hasil terhadap daftar.
- [Kode 4.2] `_IDS_JSON_SCHEMA` (skema structured output) — menunjukkan cara menjamin format keluaran.

b) **Ekstraksi Teknik (Technique Agent)** — tahap paling kompleks
- [P] Kebutuhan: ruang teknik ~750 kelas, mustahil dimasukkan semua ke prompt model kecil (n_ctx 4096).
- [P] Solusi retrieval: bangun dokumen ringkas per teknik → TF-IDF + cosine similarity → ambil top-K kandidat → hanya kandidat yang ditawarkan ke LLM.
- [Kode 4.3] Inti `_retrieve_candidate_techniques()` (TF-IDF fit_transform + cosine_similarity + argsort top_k).
- [P] Chunking laporan panjang: dipecah dgn overlap supaya TTP di akhir teks tidak hilang; hasil tiap chunk di-union.
- [Kode 4.4] `_chunk_text()`.

c) **Review (Reviewer Agent)** — loop "debat" multi-agent
- [P] Kebutuhan: menilai konsistensi taktik+teknik vs teks; jika tidak valid → revisi.
- [P] Realisasi: reviewer mengembalikan {is_valid, feedback}; feedback disuntikkan ke prompt agent + temperature dinaikkan agar jawaban berubah.
- [Kode 4.5] `_should_revise()` (conditional edge: valid/iter-max → post_process; selain itu → balik ke tactic_extraction). → bukti realisasi loop iteratif.
- [P] Catatan: reviewer opt-in (REVIEWER_ENABLE) karena menambah biaya panggilan LLM.

d) **Pasca-pemrosesan (deterministik, tanpa LLM)**
- [P] Tiga langkah: rekonsiliasi (filter konsistensi taktik–teknik) → validasi (cek ada di KB) → bangun STIX bundle.
- [Kode 4.6] Potongan safety-net Reconciler (jika filter membuang semua teknik, pertahankan semua teknik valid).

### 4.2.4 Keputusan Desain & Iterasi Teknis  ← **SUB-BAGIAN BARU = "PROSES PEMBUATAN"**
Inti yang kamu cari. Ceritakan masalah nyata saat membangun + solusinya. Bisa berbentuk narasi atau tabel.

- [Tabel 4.x] Ringkasan keputusan desain & iterasi:
  | Tantangan saat pengembangan | Keputusan / Solusi | Alasan |
  |---|---|---|
  | Top-10 kandidat → recall rendah | Naikkan kandidat 10 → 50 | Teknik benar sering di luar top-10 retrieval |
  | TTP di akhir laporan hilang saat dipotong | Chunking + overlap, union hasil | Konteks panjang melebihi n_ctx |
  | Model Qwen3 boros token utk "thinking", output JSON kosong | Matikan thinking (`enable_thinking=False`, `/no_think`) | Token budget habis sebelum keluar jawaban |
  | LLM kadal gagal hasilkan JSON valid | Fallback parser berlapis (regex) + retry backoff + fallback model | Robustness tanpa gagal total |
  | Filter konsistensi membuang semua teknik | Safety-net pertahankan teknik valid | Jaga recall (penting utk PoC) |
  | Muncul tactic ID invalid (TA0027 dari Mobile/PRE) | Default fokus matriks Enterprise saja | Konsistensi ruang label |
- [P] Naratif 1: keputusan retrieval & kandidat (dengan rujukan ke Kode 4.3).
- [P] Naratif 2: keputusan robustness LLM (structured output → fallback parser → retry/fallback model).
- [Kode 4.7] Potongan `enable_thinking=False` / penambahan `/no_think` — keputusan menonaktifkan thinking.
- [P] Naratif 3: keputusan deterministik pada pasca-pemrosesan (kenapa bukan LLM) + safety-net recall.
- [P] Penutup: keputusan-keputusan ini menunjukkan proses pengembangan bersifat iteratif dan berbasis bukti, sekaligus menjadi justifikasi rancangan akhir.

---

## 4.3 Demonstration  *(sudah ada — minor)*
- [P] Tujuan: buktikan seluruh komponen berjalan terintegrasi end-to-end via `run_full_pipeline`.
- [P+Tabel 4.4] Alur end-to-end pada laporan contoh ("StopRansomware Royal Ransomware") dari input → STIX.
- [Tabel 4.5] Prediksi vs ground truth pada laporan contoh.
- [P] Contoh objek STIX 2.1 (mis. T1566 → attack-pattern + external ref + kill-chain phase).
- [Gambar 4.x] (opsional) Tangkapan layar Web UI / cuplikan STIX bundle JSON.

---

## 4.4 Evaluation  *(sudah ada — pertahankan)*
- [P] Metode: P/R/F1 micro-averaging, mode exact & base-technique; persamaan (3.1)–(3.6).
- [Tabel 4.6] Hasil evaluasi pada TRAM II (taktik, teknik exact, teknik base).
- [Tabel 4.7] Statistik deskriptif (151 laporan, 787 prediksi, 1.943 GT, dst.).
- [P] Analisis: Precision > Recall (sistem konservatif); taktik > teknik (ruang label lebih kecil); perbandingan dgn DistilBERT (F0.5 ~76%) + konteks bahwa sistem ini zero-shot LLM lokal.
- *Saran: tambahkan 1 paragraf yang MENAUTKAN hasil evaluasi balik ke keputusan desain di 4.2.4 (mis. recall rendah → relevan dgn keputusan kandidat/chunking) agar Design & Development dan Evaluation saling terhubung.*

---

## 4.5 Communication  *(sudah ada)*
- [P] Dokumentasi = laporan TA ini; artefak = prototipe sistem multi-agent + keluaran STIX 2.1.
- [P] Temuan utama yang dikomunikasikan.
- *Opsional: sebutkan artefak pendukung (repositori kode, dokumen arsitektur/flowchart) sebagai bentuk diseminasi.*

---

## Daftar potongan kode yang disiapkan (untuk dilampirkan di 4.2)
1. Kode 4.1 — `PipelineState` + `_build_graph()`  → orchestrator.py
2. Kode 4.2 — `_IDS_JSON_SCHEMA`  → tactic_agent.py / technique_agent.py
3. Kode 4.3 — inti `_retrieve_candidate_techniques()`  → technique_agent.py
4. Kode 4.4 — `_chunk_text()`  → technique_agent.py
5. Kode 4.5 — `_should_revise()`  → orchestrator.py
6. Kode 4.6 — safety-net Reconciler  → reconciler.py
7. Kode 4.7 — `enable_thinking=False` / `/no_think`  → *_agent.py

Tips penyajian kode di laporan:
- Tampilkan 5–15 baris paling representatif saja, beri nomor "Kode 4.x" + 1–2 kalimat penjelasan di bawahnya.
- Jangan tempel seluruh file. Sisanya cukup dirujuk ke Lampiran.
- Konsisten beri keterangan bahasa & nama modul asal.
```
