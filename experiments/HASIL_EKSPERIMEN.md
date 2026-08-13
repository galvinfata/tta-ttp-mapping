# Hasil Eksperimen — Jangkauan Pembacaan & Anggaran Context Window

> **Tambahan 5 Agustus 2026 — lihat §7 di bawah.** Instrumentasi keselarasan
> peringkat retrieval (Tahap 2b) dan pengacakan urutan kandidat (Tahap 4) sudah
> terpasang dan terverifikasi pada run nyata. Temuan 1 audit (**filter
> `TECHNIQUE_ACCEPT_TOP_N` menihilkan kontribusi LLM**) kini **TERKONFIRMASI
> secara mekanistis dengan angka dari kode hari ini**, bukan dari cache kandidat
> yang tidak sezaman. Preset baru `G` dan `H` ditambahkan; rancangan preset
> lengkap ada di `RANCANGAN_PRESET.md`.

**Tanggal:** 4 Agustus 2026 · **diperbarui 9 Agustus 2026**
**Subset:** 30 laporan pertama `data/tram_ii` (urutan sama untuk semua preset)
**Status:** ✅ **EKSPERIMEN SELESAI.** Instrumentasi & perkakas selesai;
rancangan preset direvisi jadi **A / G / H / E / F** setelah Bukti 1 terbantah
(§1). Kelima preset **tuntas 30/30 laporan** per 8 Agustus 2026; tabel §4.1
terisi penuh. Percobaan pertama preset A pada 4 Agustus **GAGAL** dan diulang
(§4.2).

**Dua temuan penentu:** `MAX_CHUNKS` 3→10 (preset E) adalah **perbaikan murni**
— E mendominasi A secara mutlak (hanya-A = 0, hanya-E = 37, McNemar p ≈ 1,5e-11),
sehingga `MAX_CHUNKS=3` merupakan kerugian tanpa kompensasi. Sebaliknya
**Reviewer Agent tidak berdampak terukur** (E vs F: 1 sel diskordan dari 406
ground truth, p = 1,0) sambil menambah 14% waktu. Konfigurasi terbaik = **E**.

---

## 0. Ringkasan eksekutif

Tiga temuan, diurutkan dari yang paling mengubah kesimpulan naskah:

1. **Bukti 1 (prompt melampaui context window) TERBANTAH.** Diukur langsung ke
   server pada 4 Agustus 2026: `qwen/qwen3-4b` dimuat dengan
   **`loaded_context_length = 8192`**, bukan 4096. Prompt technique agent
   konfigurasi 11 Juli berukuran 19.740 karakter = **5.003 token** menurut
   tokenizer server, ditambah cadangan keluaran 512 token = 5.515 token, yaitu
   **67% dari kapasitas 8.192 — muat dengan sisa 33%**. Tidak ada pemotongan
   prompt. Rinciannya di §1.

2. **Bukti 2 (sebagian besar isi laporan tak pernah dibaca) TIDAK TERKONFIRMASI.**
   Statistik audit diambil dari **ukuran berkas JSON mentah**, bukan dari teks
   laporan yang benar-benar diproses pipeline. Median panjang teks laporan
   adalah **17.720 karakter**, bukan 62.508. Dengan jangkauan 23.500 karakter,
   sistem membaca **median 98%** isi laporan pada subset 30 (rata-rata 86%),
   bukan 38%. Hipotesis utama eksperimen ini karenanya berdiri di atas premis
   yang salah — lihat §2.

3. **Preset B dan C karenanya dibatalkan.** Keduanya mengecilkan chunk
   8000→3500 semata agar prompt muat di 4096 — masalah yang ternyata tidak ada.
   Efek sampingnya nyata: jangkauan preset B anjlok dari 23.500 ke 10.000
   karakter (coverage median 0,98 → **0,42**), dan preset C mencampur dua
   perubahan sekaligus (chunk lebih kecil DAN chunk lebih banyak) sehingga tidak
   mengisolasi apa pun. Berkas presetnya disimpan sebagai kontrol negatif
   terdokumentasi, tidak dijalankan.

**Rancangan pengganti (disetujui 4 Agustus 2026):**

| Preset | Beda dari preset sebelumnya | Jangkauan |
|---|---|---:|
| **A** | baseline replikasi 11 Juli (chunk 8000 × 3) | 23.500 char |
| **E** | dari A: `LLM_MAX_CHUNKS` 3 → 10 — **satu baris** | 77.750 char |
| **F** | dari E: `REVIEWER_ENABLE` false → true — **satu baris** | 77.750 char |

Ukuran chunk, anggaran daftar kandidat, dan seluruh parameter retrieval identik
di ketiganya. Karena `RETRIEVAL_PER_CHUNK=true` menjalankan retrieval atas tiap
chunk 8.000 karakter secara terpisah, `RETRIEVAL_MAX_CHARS=40000` tidak pernah
mengikat dan sengaja tidak diubah. Inilah isolasi jangkauan pembacaan yang
bersih: satu-satunya yang berubah adalah **berapa banyak** potongan yang dibaca.

---

## 1. Context window — Bukti 1 terbantah

### 1.1 Berapa sebenarnya n_ctx model?

Audit mengambil angka `n_ctx = 4096` dari komentar di **bagian bawah** `.env`.
Komentar itu usang dan bertentangan dengan komentar di **baris ke-4 berkas yang
sama**, yang menyatakan "Model aktif: qwen3-4b, context 8192 (muat penuh di VRAM
GTX 1650)". Diukur langsung ke server, tiga bukti independen menunjuk 8192:

| Pengukuran | Hasil |
|---|---|
| `GET /api/v0/models` (REST native LM Studio) | `qwen/qwen3-4b` → `state=loaded`, `loaded_context_length=8192`, `max_context_length=32768` |
| Prompt technique agent asli preset A dikirim ke server | diterima, `usage.prompt_tokens=5003`, `finish_reason=stop`, jawaban JSON valid |
| Prompt sintetis ~8.800 token | **ditolak** HTTP 400: `request (8812 tokens) exceeds the available context size` |

### 1.2 Konsumsi context window sesungguhnya

Prompt technique agent preset A: system 178 char + user 19.562 char = **19.740
karakter = 5.003 token** (angka tokenizer server, bukan estimasi). Ditambah
cadangan keluaran 512 token = **5.515 token dari kapasitas 8.192 → utilisasi 67%,
sisa 2.677 token**. Prompt muat utuh; daftar kandidat tidak pernah terpotong.

Rasio karakter/token terukur = **3,95** (19.740 / 5.003). Default kode 3,5
melebih-lebihkan konsumsi token sekitar **13%** — estimasi offline sebelumnya
(5.417 token) karenanya terlalu tinggi. Preset kini menyetel
`PROMPT_BUDGET_CHARS_PER_TOKEN=3.95` berdasarkan pengukuran ini.

| Preset | Panggilan technique agent | Token prompt (terukur/estimasi) | Melampaui `n_ctx=8192` |
|---|---:|---:|---:|
| A | 80 | 5.003 (terukur, 1 sampel) | **0** |
| E | 115 | ≈5.003 (ukuran chunk & kandidat identik dengan A) | **0** |
| F | ≥115 | ≈5.003 + prompt reviewer | 0 |

Konsekuensi: **`PROMPT_BUDGET_ENFORCE` tidak pernah aktif** pada rancangan
A/E/F, karena tidak ada prompt yang melampaui kapasitas. Mekanismenya tetap
terpasang dan terverifikasi berfungsi sebagai jaring pengaman (uji terkendali:
chunk 3500 + `CANDIDATE_LIST_MAX_CHARS=12000` pada n_ctx 4096 → 50 kandidat
dipangkas jadi 36, overflow 0).

### 1.3 Apakah instrumentasi ini jadi sia-sia?

Tidak. Justru instrumentasi inilah yang **membantah** dugaan audit dengan angka,
bukan dengan dugaan tandingan. Sebelum ada `prompt_budget.py` dan manifest, tidak
ada satu pun cara untuk mengetahui berapa token yang benar-benar dikirim atau
berapa n_ctx yang benar-benar aktif — dan justru karena itulah komentar `.env`
yang usang bisa bertahan berbulan-bulan dan masuk ke audit. Mulai run berikutnya,
setiap berkas hasil membawa manifest yang mencatat `LLM_N_CTX` efektif, jumlah
panggilan, token rata-rata & maksimum, serta jumlah overflow.

---

## 2. Statistik jangkauan pembacaan — koreksi terhadap Bukti 2

### 2.1 Sumber ketidakcocokan

Audit menyatakan median panjang laporan 62.508 karakter dan maksimum 290.121.
Angka yang sebenarnya, diukur pada teks yang **benar-benar diproses pipeline**
(hasil `load_tram_dataset`, yaitu gabungan `sentences[].text`):

| Ukuran | Klaim audit | Terukur (151 laporan) |
|---|---:|---:|
| Median panjang laporan | 62.508 char | **17.720 char** |
| Maksimum | 290.121 char | **77.052 char** |

Median **ukuran berkas** `.json` di `data/tram_ii` adalah 62.401 byte dan
maksimumnya 253.390 byte — praktis identik dengan angka audit. Jadi audit
mengukur berkas JSON mentah (termasuk struktur `sentences`, `mappings`, dan
metadata label), bukan teks laporannya. Rasio ±3,5× itu adalah overhead JSON.

### 2.2 Jangkauan pembacaan sesungguhnya

Untuk 151 laporan pada konfigurasi 11 Juli (jangkauan 23.500 char):

| Ukuran | Klaim audit | Terukur |
|---|---:|---:|
| Laporan terbaca utuh | 10 / 151 (7%) | **108 / 151 (72%)** |
| Median porsi terbaca | 38% | **100%** |
| Rata-rata porsi terbaca | — | **93%** |
| Laporan >60% tak terbaca | 83 / 151 | **2 / 151** |

Pada subset 30 laporan yang dipakai eksperimen (kebetulan berisi beberapa
laporan panjang), jangkauan preset A: median 0,98, rata-rata 0,86, terbaca utuh
14 dari 30.

### 2.3 Jangkauan per preset (subset 30 laporan)

Rancangan yang dijalankan (A/E/F). Angka dihitung offline dan bersifat
deterministik — tidak akan berubah saat run dieksekusi.

| Preset | Jangkauan maks/laporan | Coverage median | Coverage rata-rata | Terbaca utuh | Panggilan technique agent |
|---|---:|---:|---:|---:|---:|
| A | 23.500 char | 0,981 | 0,863 | 14 / 30 | 80 |
| **E** | 77.750 char | **1,000** | **1,000** | **30 / 30** | 115 |
| **F** | 77.750 char | 1,000 | 1,000 | 30 / 30 | ≥115 + reviewer |

Preset E membaca **seluruh isi ke-30 laporan** dengan biaya 1,44× panggilan LLM
dibanding A (115 vs 80). Preset F menambah 1–2 panggilan reviewer per laporan,
dan tiap penolakan reviewer mengulang seluruh tactic+technique agent — sampai 10
panggilan technique agent tambahan per putaran revisi.

Rancangan yang **dibatalkan** (B/C/D), disimpan sebagai kontrol negatif:

| Preset | Jangkauan | Coverage median | Panggilan | Alasan dibatalkan |
|---|---:|---:|---:|---|
| B | 10.000 char | 0,417 | 89 | mengecilkan chunk demi n_ctx 4096 yang ternyata 8192; jangkauan justru anjlok |
| C | 39.250 char | 1,000 | 225 | mencampur dua perubahan (chunk lebih kecil + lebih banyak); 2× lebih mahal dari E untuk cakupan lebih rendah |
| D | 39.250 char | 1,000 | ≥225 | sama seperti C, ditambah reviewer |

### 2.4 Implikasi terhadap naskah

Kesimpulan naskah bahwa **kualitas retrieval leksikal adalah bottleneck utama
tetap berdiri**, dan kini lebih kuat: dua penjelasan alternatif yang diajukan
audit — "sistem hanya membaca 38% laporan" dan "prompt terpotong context
window" — keduanya terbantah oleh pengukuran. Yang perlu ditambahkan ke naskah
justru catatan metodologis: bahwa
`results/metrics/retrieval_ceiling.json` dihitung dengan parameter chunking yang
sama sehingga plafon 0,592/0,705 sudah memuat efek jangkauan tersebut.

---

## 3. Jawaban atas dua pertanyaan eksperimen

**Apakah perluasan jangkauan pembacaan menaikkan recall? Berapa besar?**
**Belum dapat dijawab** — preset E belum dijalankan (server LLM mati, §4.2).
Yang bisa dikatakan dari data offline: ruang perbaikan yang tersedia jauh lebih
kecil daripada yang diperkirakan audit. Preset E menaikkan cakupan rata-rata
0,863 → 1,000, yaitu **13,7 poin persen teks tambahan** yang terkonsentrasi pada
16 dari 30 laporan; 14 laporan sisanya sudah terbaca utuh di preset A sehingga
hasilnya tidak mungkin berubah sama sekali. **Ekspektasi jujur: efeknya
kemungkinan besar kecil, dan bila F1 naik, kenaikannya harus berasal dari
laporan-laporan panjang itu — bila tidak, kenaikan tersebut adalah derau.**
Pemeriksaan ini bisa dilakukan per laporan lewat field `coverage_ratio`.

**Apakah Reviewer Agent mengubah precision/recall? Berapa besar?**
**Belum dapat dijawab** — preset F belum dijalankan. Instrumentasi yang
diperlukan untuk menjawabnya sudah terpasang: manifest mencatat
`reviewer_active` (bukti runtime), `review_iterations_total`, dan
`reports_triggering_revision`, sehingga hasil run F nanti bisa dibuktikan
statusnya — hal yang tidak mungkin dilakukan untuk run 11 Juli.

---

## 4. Perbandingan preset

### 4.1 Tabel hasil

Diisi dari `scripts/compare_experiments.py`; salinan mutakhir selalu ada di
`experiments/tabel_perbandingan.md`. **Lengkap per 8 Agustus 2026 pukul 21:53.**

| Preset | Laporan | Coverage median | Pred/laporan | Median peringkat | % di luar top-30 | P exact | R exact | F1 exact | P base | R base | F1 base | Retrieval-miss | Reasoning-miss | Durasi (mnt) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 30 | 0,980 | 19,8 | 11 | 0,0% | 0,2445 | 0,3571 | 0,2903 | 0,3429 | 0,4548 | 0,3910 | 142 | 119 | 90,5 |
| G | 30 | 0,980 | 26,9 | 17 | 26,7% | 0,2094 | 0,4163 | 0,2786 | 0,3010 | 0,5248 | 0,3826 | 142 | 95 | 91,1 |
| H | 30 | 0,980 | 26,6 | 18,0 | 27,2% | 0,1980 | 0,3892 | 0,2625 | 0,3081 | 0,5102 | 0,3842 | 142 | 106 | 95,2 |
| **E** | 30 | 1,000 | 26,0 | 10 | 0,0% | 0,2330 | 0,4483 | **0,3067** | 0,3448 | 0,5860 | **0,4341** | **99** | 125 | 141,3 |
| F | 30 | 1,000 | 26,6 | 10 | 0,0% | 0,2265 | 0,4458 | 0,3004 | 0,3344 | 0,5831 | 0,4251 | 99 | 126 | 161,2 |

⚠️ Kelima run memakai **subset 30 laporan**, tidak sebanding langsung dengan run
final 151 laporan. Yang sah dibandingkan hanya A ↔ G ↔ H ↔ E ↔ F karena subset,
kode, model, dan retrieval-nya identik.

G, H, E, dan F berstatus `complete_merged` (hasil penggabungan potongan run
dengan `scripts/merge_partial_runs.py`); metrik dihitung ulang dari hasil
gabungan, bukan dirata-ratakan.

**Preset E memperbaiki retrieval, bukan penalaran.** Naiknya `MAX_CHUNKS` dari 3
ke 10 menurunkan retrieval-miss **142 → 99**: 43 teknik yang sebelumnya tidak
pernah sampai ke daftar kandidat kini tersedia. Reasoning-miss justru naik
(119 → 125) karena kandidat yang bertambah itu harus dipilih dari himpunan yang
lebih besar. Efek bersihnya tetap positif di semua level metrik, dan uji McNemar
berpasangan A lawan E pada 406 ground truth yang sama menunjukkan **hanya-A = 0,
hanya-E = 37, p ≈ 1,5e-11**: tidak ada satu pun ground truth yang ditemukan A
tetapi hilang di E. Dominasi mutlak seperti ini jarang terjadi dan menjadikan
`MAX_CHUNKS=3` **kerugian murni**, bukan pertukaran.

### 4.1.2 Reviewer Agent tidak berdampak terukur (E → F)

Preset F berbeda dari E hanya pada `REVIEWER_ENABLE`. Uji McNemar berpasangan
pada 406 ground truth yang sama:

| | n |
|---|---:|
| Ditemukan keduanya | 181 |
| Terlewat keduanya | 224 |
| Hanya E | 1 |
| Hanya F | 0 |

**p = 1,0** → **satu** ground truth di seluruh dataset yang membedakan keduanya,
dan arahnya justru merugikan F. Tidak ada satu level metrik pun yang membaik
(exact F1 −0,006, base F1 −0,009, taktik F1 −0,017), sementara durasinya naik
**141,3 → 161,2 menit (+14%)**.

Mekanismenya terbaca dari manifest: Reviewer dipanggil di **30/30** laporan
tetapi memicu revisi nyata hanya di **4** — dan keempatnya laporan teknis
non-intrusion dengan daftar teknik awal 1–2 buah. Reviewer karenanya aktif justru
pada kasus yang paling miskin isi, bukan pada laporan padat yang paling
membutuhkan koreksi.

⚠️ **Batas interpretasi.** Yang benar dinyatakan adalah *tidak ada bukti Reviewer
mengubah kemampuan sistem menemukan teknik yang benar*, **bukan** "Reviewer
terbukti tidak berguna". Uji McNemar di sini hanya menguji recall terhadap ground
truth; ia tidak menguji keterbacaan keluaran maupun kualitas alasan yang
menyertainya.

Kolom coverage untuk E dan F berasal dari perhitungan offline (§2.3) dan tidak
berubah saat run dijalankan — chunking bersifat deterministik.

**Retrieval-miss identik (142) pada A, G, dan H** karena ketiganya memakai
retrieval dan subset yang sama; yang berbeda hanya apa yang dilakukan terhadap
kandidat sesudahnya. Reasoning-miss turun 119 → 95 saat filter dimatikan, yaitu
teknik yang tersedia di kandidat dan akhirnya berhasil dipilih LLM.

### 4.1.1 Pengacakan urutan kandidat (A → G → H)

Preset H menguji apakah LLM benar-benar membaca laporan atau sekadar mengikuti
urutan peringkat retrieval. Uji McNemar berpasangan pada 406 ground truth yang
sama, G lawan H:

| | n |
|---|---:|
| Ditemukan keduanya | 135 |
| Terlewat keduanya | 214 |
| Hanya G | 34 |
| Hanya H | 23 |

**p = 0,185** (eksak, dua sisi) → selisihnya **tidak signifikan**. Mengacak
seluruh informasi urutan tidak mengubah perilaku sistem secara terukur, sehingga
penjelasan "sistem hanya mengikuti peringkat retrieval" tidak didukung data.

Namun kesamaan agregat itu **tidak berarti keluarannya sama**. Jaccard daftar
teknik per laporan antara G dan H rata-rata **0,476** — separuh teknik berbeda,
padahal yang berubah hanyalah urutan tampil yang tidak membawa informasi.

⚠️ **Pembatas interpretasi.** Agen taktik tidak menerima daftar kandidat teknik,
sehingga keluarannya seharusnya identik antara G dan H; kenyataannya berbeda pada
**7 dari 30 laporan** meski `temperature=0.0`. Jadi ada lantai nondeterminisme
inferensi yang belum dikuantifikasi, dan porsi kerapuhan yang murni disebabkan
pengacakan belum terpisah bersih. Mengukurnya memerlukan run ulang G dengan
konfigurasi persis sama.

### 4.2 Catatan eksekusi — run preset A 4 Agustus 2026 GAGAL

Run pertama preset A dijalankan pukul 14:15 dan **dibunuh pada menit ke-53 di
laporan ke-14**. Urutan kejadiannya:

- Laporan 1–13 diproses normal, laju **2,93 menit/laporan** — jauh lebih lambat
  daripada smoke test satu panggilan (1,5 detik untuk prompt kecil, ~10–20 detik
  yang wajar untuk prompt 5.000 token).
- Laporan ke-14: technique agent kena `Request timed out` pada seluruh 3 chunk ×
  3 percobaan, menghasilkan prediksi kosong.
- Sesudah itu server berhenti merespons sepenuhnya (`GET /v1/models` → `http=000`
  tiga kali berturut-turut dengan batas 45 detik).

**Dugaan penyebab, belum terbukti:** VRAM GTX 1650 hanya 4 GB. Saat run dimulai,
`text-embedding-nomic-embed-text-v1.5` berstatus `not-loaded` dan dimuat JIT pada
retrieval pertama, sementara `qwen/qwen3-4b` sudah menempati VRAM pada ctx 8192.
Bila keduanya tidak muat bersamaan, LM Studio bongkar-pasang model pada setiap
laporan (retrieval → embedder → chat → LLM → …), yang konsisten dengan laju
2,93 menit/laporan dan dengan kematian engine pada akhirnya. Verifikasi menunggu
server hidup kembali.

**Data yang tersimpan:** 10 laporan di
`results/predictions/exp_A_baseline_replikasi_20260804_141528.json`, seluruhnya
dengan prediksi tidak kosong (laporan ke-14 yang terkontaminasi timeout tidak
ikut tersimpan — checkpoint terakhir di laporan ke-10). Berkas ini **tidak boleh
dipakai sebagai hasil**: manifestnya bertanda `"status": "aborted"` dan
`"do_not_use_for_metrics": true`. Preset A akan diulang dari nol.

**Perbaikan yang sudah diterapkan akibat insiden ini:**

1. `run_experiment.py` kini menulis manifest berstatus `"partial"` di **setiap
   checkpoint**, bukan hanya di akhir. Run yang dibunuh tetap meninggalkan
   rekaman konfigurasi. (Insiden ini terjadi justru karena manifest hanya
   ditulis di akhir — celah yang sama dengan yang ditutup untuk jalur batch.)
2. Preset A/E/F kini menyetel `LLM_REQUEST_TIMEOUT_SECONDS=120`. Default 300
   detik terlalu longgar: satu laporan macet bisa menggantung
   300 × 3 percobaan × jumlah chunk — pada preset E itu **2,5 jam untuk satu
   laporan**. Nilai efektifnya ikut dicatat di manifest.

**Pembanding run 11 Juli** (`results/predictions/results_web_20260711_223544.json`,
151 laporan, dihitung ulang dengan `evaluator.py` yang sama):
exact P=0,1781 R=0,2784 F1=0,2173 | base P=0,3098 R=0,4016 F1=0,3498.
Perhatikan: pembanding ini **151 laporan**, sedangkan preset A **30 laporan**,
sehingga gerbang keputusan preset A harus dinilai dengan toleransi — subset 30
tidak wajib menghasilkan angka yang sama persis.

---

## 5. Keterbatasan

- **Subset 30 laporan**, bukan 151. Interval kepercayaan lebar; selisih F1 di
  bawah ~0,03 tidak bermakna pada ukuran sampel ini.
- **Satu kali run per preset, tanpa pengulangan.** Temperature 0,0 membuat
  technique/tactic agent nyaris deterministik, tapi preset F memakai
  `LLM_REVISE_TEMPERATURE=0.4` saat revisi sehingga run F tidak deterministik.
- **Parameter disetel post-hoc**, setelah melihat hasil run 11 Juli. Ini bukan
  hold-out yang bersih.
- **Preset A tidak dapat dibuktikan identik dengan run 11 Juli.** `.env` tidak
  masuk git (ada di `.gitignore`) dan run 11 Juli tidak menulis manifest, jadi
  preset A adalah salinan `.env` per 4 Agustus 2026 — dugaan terbaik, bukan
  rekaman. Mulai run berikutnya, manifest menutup celah ini.
- **Angka §2.3 dihitung dengan `RETRIEVAL_EMBEDDING_HYBRID=false`** agar 100%
  offline. Ini mengubah *peringkat* kandidat sedikit, bukan *ukuran* prompt
  maupun jangkauan pembacaan yang diukur di sana. Token pada §1.2 sebaliknya
  **terukur** dari `usage.prompt_tokens` server, bukan estimasi.
- **Satu sampel prompt untuk pengukuran token.** Rasio 3,95 char/token diukur
  dari satu prompt preset A. Variasinya antar prompt kecil (isi prompt hampir
  seluruhnya teks Inggris teknis dengan struktur sama), tapi belum diukur
  sebarannya.
- `LOCAL_LLM_REPORT_MAX_CHARS` dipakai bersama oleh **tiga** agen (ukuran chunk
  technique agent, panjang kutipan tactic agent, panjang kutipan reviewer agent).
  Pada rancangan A/E/F nilainya **sama di ketiga preset** (8.000), sehingga
  confounder ini **tidak aktif** — inilah salah satu keuntungan rancangan
  pengganti dibanding B/C/D, yang mengubahnya jadi 3.500 dan ikut memperpendek
  kutipan tactic agent.
- **Stabilitas server belum terselesaikan** (§4.2). Selama penyebab kematian
  engine belum dipastikan, setiap run panjang berisiko terhenti di tengah.

---

## 6. Cara menjalankan ketiga run

Prasyarat: LM Studio hidup di `LOCAL_LLM_BASE_URL`, `qwen/qwen3-4b` dimuat pada
context length **8192** (sesuai `LLM_N_CTX` di preset). Sebelum menjalankan,
pastikan `text-embedding-nomic-embed-text-v1.5` **sudah ikut dimuat** dan
auto-unload dimatikan — lihat §4.2. Periksa dengan:

```bash
curl -s http://localhost:1234/api/v0/models   # cek state & loaded_context_length
```

```bash
python scripts/run_experiment.py --preset A --reports 30   # gerbang keputusan
python scripts/run_experiment.py --preset E --reports 30   # hipotesis utama
python scripts/run_experiment.py --preset F --reports 30   # kontribusi Reviewer

python scripts/compare_experiments.py \
    results/predictions/exp_A_*.json \
    results/predictions/exp_E_*.json \
    results/predictions/exp_F_*.json \
    --out experiments/tabel_perbandingan.md
```

Gerbang keputusan: bila metrik preset A meleset jauh dari pembanding 11 Juli
(§4.1), **hentikan** — berarti ada yang berubah sejak Juli dan seluruh
perbandingan A/E/F tidak sah. Bila satu preset melebihi 3 jam, hentikan dan
laporkan; preset F paling berisiko karena reviewer dapat memicu revisi berulang.

Tiap run menghasilkan dua berkas dan **tidak pernah menimpa hasil lama**:
`results/predictions/exp_<preset>_<timestamp>.json` dan `.manifest.json`-nya.
Manifest ditulis di tiap checkpoint (5 laporan), jadi run yang terhenti tetap
meninggalkan rekaman — periksa field `status`: `complete`, `partial`, atau
`aborted`.

---

## 7. Tambahan 5 Agustus 2026 — keselarasan dengan peringkat retrieval

### 7.1 Temuan 1 terkonfirmasi: filter membuang 29% pilihan LLM

Audit 5 Agustus menyatakan filter `TECHNIQUE_ACCEPT_TOP_N=30`
(`technique_agent.py`) membuang setiap pilihan LLM di luar peringkat 30 daftar
kandidat, sehingga sistem tidak dapat menyimpang dari peringkat retrieval.
Angka pendukungnya waktu itu **lemah**: dihitung dari cache kandidat 12 Juli
(retrieval v5) terhadap run 11 Juli (retrieval lama), dengan ~11% prediksi tidak
terpetakan.

Instrumentasi Tahap 2b kini mengukurnya **di dalam run itu sendiri**. Tiap teknik
yang dipilih LLM dicatat peringkat retrieval chunk asalnya, sebelum dan sesudah
filter. Hasil run probe preset A (3 laporan, 5 Agustus 2026,
`probe_A_hybrid_20260805_215516.json`):

| Ukuran | Nilai |
|---|---:|
| Prediksi yang punya peringkat | 61 |
| **Pilihan LLM yang DIBUANG filter** | **25** |
| Porsi pilihan LLM yang dibuang | **29%** (25 dari 86) |
| Median peringkat yang dibuang | **42** |
| Median peringkat prediksi yang lolos | **9** |
| Rata-rata peringkat prediksi yang lolos | 11,44 |
| `pct_outside_top30` | **0,0%** |

`pct_outside_top30 = 0,0%` bukan temuan empiris melainkan **konsekuensi
struktural**: dengan filter aktif, angka itu tidak mungkin bukan nol. Inilah
bukti langsung bahwa keluaran sistem terkunci pada 30 besar peringkat retrieval.
Median peringkat 9 (rata-rata 11,4) juga sejalan dengan median 11 yang
diperkirakan audit — perkiraannya benar meski metodenya lemah.

Angka 29% adalah besaran kontribusi LLM yang selama ini tidak pernah sampai ke
keluaran. Preset `G` mengukur apa jadinya bila pilihan itu dibiarkan lewat.

**Catatan kejujuran:** filter ini dipasang 11 Juli justru karena TERUKUR
menaikkan precision (exact 0,244→0,304) dengan mengorbankan recall
(0,471→0,412). Mematikannya kemungkinan besar menurunkan precision. Yang dicari
preset G bukan F1 yang lebih tinggi, melainkan besaran dan mutu kontribusi LLM
yang dibuang.

### 7.2 Bukti 1 (prompt melampaui n_ctx) kembali terbantah

Run probe ini mengukur ulang secara independen: 11 panggilan LLM, token prompt
maksimum **5.018** dari kapasitas **8.192**, **0 panggilan melampaui n_ctx**,
0 kandidat terpangkas. Konsisten dengan §1.

### 7.3 Perkakas yang ditambahkan

| Tahap | Berkas | Isi |
|---|---|---|
| 2b | `technique_agent.py`, `orchestrator.py`, `run_manifest.py` | peta `{teknik: peringkat}` sebelum/sesudah filter; agregasi `mean/median_tfidf_rank`, sebaran 1–10/11–20/21–30/31+, `pct_outside_top30` |
| 4 | `technique_agent.py` | `CANDIDATE_SHUFFLE_SEED` — acak urutan tampil kandidat, reproducible |
| 5 | `experiments/G_tanpa_filter.env`, `H_acak_kandidat.env` | dua preset baru, masing-masing beda satu baris dari pendahulunya |
| 5b | `scripts/eval_baselines.py` | `--per-chunk`, `--pure-tfidf`, `--reports`, `--match-budget-to`; majority dilabeli oracle |
| 6 | `scripts/compare_experiments.py` | kolom median peringkat & `% di luar top-30`, plus tabel keselarasan tersendiri |

Pengacakan Tahap 4 dilakukan **paling akhir** — sesudah anggaran karakter dan
sesudah pemangkasan budget menentukan himpunan yang tampil — supaya yang berubah
benar-benar hanya urutan. Terverifikasi: seed sama → urutan identik; seed beda →
urutan beda; himpunan kandidat identik di ketiga kasus.

### 7.4 Baseline sebanding (Tahap 5b) — sudah dihitung

`results/metrics/baseline_tfidf_murni_perchunk_n30.json`, dihitung pada **kode,
versi retrieval, mode chunking, dan subset 30 laporan yang sama** dengan preset:

| Baseline (BASE-TECHNIQUE) | P | R | F1 |
|---|---:|---:|---:|
| TF-IDF murni per-chunk top-10 | 0,3586 | 0,2624 | 0,3030 |
| TF-IDF murni per-chunk top-20 | 0,2773 | 0,3703 | 0,3171 |
| TF-IDF murni per-chunk top-50 | 0,1878 | 0,5277 | 0,2770 |
| Majority-**oracle** top-20 | 0,3650 | 0,6385 | 0,4645 |
| Majority-**oracle** top-50 | 0,2333 | **1,0000** | 0,3784 |

Majority-oracle top-50 mencapai **recall 1,000**: seluruh base-technique pada
ground truth ke-30 laporan termuat dalam 50 teknik tersering di ground truth itu
sendiri. Itu bukti langsung kebocoran label — baseline ini **batas atas trivial**,
bukan pesaing yang wajar, dan harus dilabeli demikian di naskah.

N utama baseline kini dipilih **budget-matched** (setara rata-rata jumlah
prediksi sistem) lewat `--match-budget-to`, menggantikan pemilihan "F1 tertinggi"
yang menyetel N pada data yang sama yang dilaporkan.

### 7.4.1 Perbandingan bersih — baseline hibrida, budget-matched (9 Agustus 2026)

⚠️ **Baseline TF-IDF murni di atas tidak boleh dipakai untuk mengklaim kontribusi
lapisan penalaran.** Baseline itu menonaktifkan komponen embedding
(`embedding_hybrid: false`), sedangkan kelima preset memakai retrieval **hibrida**.
Selisih terhadapnya karenanya memuat **dua** perubahan sekaligus — LLM *dan*
embedding — sehingga tidak mengisolasi apa pun. Ini melanggar standar yang
dipakai pada rancangan preset, di mana tiap perlakuan hanya mengubah satu baris.

Baseline diulang dengan retrieval **hibrida** (`baseline_hibrida_perchunk_n30.json`)
dan anggaran prediksi disamakan lewat `--match-budget-to`, sehingga terhadap tiap
preset yang berbeda tinggal **satu** hal: ada atau tidaknya lapisan penalaran LLM.

**Preset E — anggaran N = 26 (rata-rata 26,0 prediksi/laporan):**

| Metode (30 laporan) | Level | P | R | F1 |
|---|---|---:|---:|---:|
| **Sistem preset E** | base | **0,3448** | **0,5860** | **0,4341** |
| Baseline hibrida per-chunk top-26 | base | 0,2549 | 0,4198 | 0,3172 |
| **Sistem preset E** | exact | **0,2330** | **0,4483** | **0,3067** |
| Baseline hibrida per-chunk top-26 | exact | 0,1603 | 0,3079 | 0,2108 |

**Preset A — anggaran N = 20 (rata-rata 19,8 prediksi/laporan):**

| Metode (30 laporan) | Level | P | R | F1 |
|---|---|---:|---:|---:|
| **Sistem preset A** | base | **0,3429** | **0,4548** | **0,3910** |
| Baseline hibrida per-chunk top-20 | base | 0,2901 | 0,3848 | 0,3308 |
| **Sistem preset A** | exact | **0,2445** | **0,3571** | **0,2903** |
| Baseline hibrida per-chunk top-20 | exact | 0,1817 | 0,2685 | 0,2167 |

Pada anggaran prediksi yang sama, sistem **mengungguli baseline pada precision
maupun recall sekaligus** — bukan pertukaran. Selisih F1 base **+0,1169** untuk E
dan **+0,0602** untuk A; pada level exact **+0,0959** dan **+0,0736**. Keduanya
jauh di atas ambang kehati-hatian ±0,03 (§4.1.1).

**Catatan terhadap majority-oracle.** Pada anggaran yang sama (N = 26), precision
sistem E (**0,3448**) justru **lebih tinggi** daripada majority-oracle (0,3333).
Keunggulan oracle seluruhnya terletak pada recall (0,7580 vs 0,5860) — yaitu
dimensi yang memang paling diuntungkan oleh pengetahuan atas kunci jawaban.
Ini memperkuat pembacaan bahwa oracle adalah **batas atas trivial**, bukan sistem
yang lebih baik.

⚠️ **Populasi harus disamakan saat dilaporkan.** Angka sistem run 151 laporan
(base F1 0,350) **tidak boleh** disandingkan dengan baseline mana pun di atas,
yang seluruhnya dihitung pada **30 laporan**. Gunakan preset A atau E sebagai
baris sistem pada tabel perbandingan baseline.

Berkas: `baseline_hibrida_perchunk_n30.json` ·
`baseline_hibrida_budgetmatched_E_n30.json` ·
`baseline_hibrida_budgetmatched_A_n30.json`

### 7.5 Biaya run — terukur

Run probe: **3 laporan / 9,0 menit = 3,0 menit per laporan**, dengan
`qwen/qwen3-4b` DAN `text-embedding-nomic-embed-text-v1.5` sama-sama
`state=loaded`. Karena keduanya terbukti muat bersamaan di VRAM, hipotesis
bongkar-pasang model (§4.2) **melemah** — laju lambat tampaknya memang laju wajar
mesin ini, bukan gejala thrashing. Konsekuensi perencanaan: satu preset 30
laporan ≈ **90 menit**; lima preset ≈ **8–12 jam** (preset F lebih lama karena
tiap penolakan reviewer mengulang seluruh tactic+technique agent).

### 7.6 Status

Seluruh perkakas Tahap 1–6 selesai dan terverifikasi. ✅ **Preset A/G/H/E/F
tuntas 30/30 laporan** per 8 Agustus 2026; tabel §4.1 terisi penuh dan
eksperimen ditutup.

Rekapitulasi — tiap preset mengubah **tepat satu** variabel terhadap induknya:

| Variabel | Preset | Efek | Sifat |
|---|---|---|---|
| `ACCEPT_TOP_N` 30→0 | A→G | recall +0,059, precision −0,035 | pertukaran |
| `CANDIDATE_SHUFFLE_SEED` | G→H | tak ada efek terukur (p=0,185) | netral |
| `MAX_CHUNKS` 3→10 | A→E | recall +0,091, p ≈ 1,5e-11 | **perbaikan murni** |
| `REVIEWER_ENABLE` | E→F | tak ada efek terukur (p=1,0), waktu +14% | **beban murni** |

Tiga penjelasan tandingan atas kemiripan sistem dengan baseline karenanya gugur
satu per satu — jangkauan baca (§2), kapasitas context window (§1), dan
"LLM sekadar mengikuti peringkat retrieval" (§4.1.1). Yang tersisa sebagai
penjelas perilaku sistem adalah filter `ACCEPT_TOP_N` (§7.1).
