# Rancangan preset — peta terhadap spesifikasi eksperimen

Dokumen ini menjelaskan preset mana yang menjawab pertanyaan mana, dan mengapa
penamaannya tidak sama persis dengan spesifikasi `Prompt_untuk_Claude_Code_v2.md`.
Ditulis 5 Agustus 2026.

## Mengapa penamaannya berbeda

Spesifikasi v2 meminta preset `A`–`E`. Huruf `B`, `C`, dan `D` sudah terpakai
oleh rancangan 4 Agustus yang **dibatalkan setelah premisnya terbantah**, dan
berkasnya sengaja disimpan sebagai kontrol negatif terdokumentasi. Memakai ulang
huruf yang sama untuk konfigurasi yang berbeda akan membuat manifest lama dan
baru tidak dapat dibedakan — persis masalah ketertelusuran yang sedang ditutup.
Karena itu preset baru memakai huruf lanjutan `G` dan `H`.

## Peta spesifikasi v2 → preset yang benar-benar dijalankan

| Spesifikasi v2 | Preset nyata | Isolasi | Status |
|---|---|---|---|
| A `A_acuan` | **`A_baseline_replikasi`** | titik acuan pada kode hari ini | dijalankan |
| B `B_tanpa_filter` | **`G_tanpa_filter`** | efek filter `TECHNIQUE_ACCEPT_TOP_N` | dijalankan |
| C `C_acak` | **`H_acak_kandidat`** | **efek urutan — apakah LLM membaca** | dijalankan |
| D `D_perbaikan` | **`E_jangkauan_penuh`** | efek jangkauan pembacaan | dijalankan |
| E `E_reviewer` | **`F_jangkauan_reviewer`** | kontribusi Reviewer Agent | dijalankan |

Tiap preset berbeda dari pendahulunya pada **tepat satu baris**:

```
A  ──(TECHNIQUE_ACCEPT_TOP_N 30→0)──►  G  ──(CANDIDATE_SHUFFLE_SEED ∅→42)──►  H
A  ──(LLM_MAX_CHUNKS 3→10)─────────►  E  ──(REVIEWER_ENABLE false→true)───►  F
```

Diverifikasi dengan `diff` (mengabaikan komentar): satu baris berubah per anak panah.

## Mengapa `D_perbaikan` versi v2 tidak dijalankan apa adanya

Spesifikasi v2 mendefinisikan preset D sebagai:

```
CANDIDATE_DESC_CHARS=60  CANDIDATE_LIST_MAX_CHARS=4500
LOCAL_LLM_REPORT_MAX_CHARS=3500  LLM_MAX_CHUNKS=12
RETRIEVAL_MAX_CHARS=80000  PROMPT_BUDGET_ENFORCE=true
```

Empat dari enam perubahan itu ada untuk memuat prompt di `n_ctx=4096`. **Kapasitas
sebenarnya 8192**, terukur langsung ke server pada 4 Agustus 2026
(`GET /api/v0/models` → `loaded_context_length=8192`; prompt 5.003 token diterima,
8.812 token ditolak). Prompt preset A memakai 67% kapasitas dengan sisa 33% —
tidak pernah terpotong. Rinciannya di `HASIL_EKSPERIMEN.md` §1.

Menjalankannya apa adanya justru merugikan dan mencampur banyak variabel sekaligus:

- `LOCAL_LLM_REPORT_MAX_CHARS` 8000→3500 dipakai **bersama oleh tiga agen** —
  ukuran chunk technique agent, panjang kutipan tactic agent, dan panjang kutipan
  reviewer agent berubah serentak.
- Chunk lebih kecil **menurunkan** jangkauan pembacaan per panggilan; efek
  `LLM_MAX_CHUNKS=12` sebagian habis hanya untuk menutupi kerugian itu.
- `CANDIDATE_DESC_CHARS` 160→60 memangkas bukti yang dilihat LLM tentang tiap
  kandidat — ini perubahan pada isi prompt, bukan sekadar ukuran.

`E_jangkauan_penuh` menguji hipotesis yang sama — apakah membaca lebih banyak teks
menaikkan kinerja — dengan **satu** variabel berubah (`LLM_MAX_CHUNKS` 3→10),
mencapai coverage 1,000 pada seluruh 30 laporan subset. Itu isolasi yang bersih;
preset D versi v2 bukan.

`RETRIEVAL_MAX_CHARS` sengaja tidak dinaikkan: dengan `RETRIEVAL_PER_CHUNK=true`,
retrieval berjalan atas tiap chunk secara terpisah sehingga batas itu tidak pernah
mengikat.

## Preset yang dibatalkan (kontrol negatif, tidak dijalankan)

| Berkas | Alasan |
|---|---|
| `B_budget_aman.env` | mengecilkan chunk demi n_ctx 4096 yang ternyata 8192; jangkauan anjlok ke coverage median 0,42 |
| `C_jangkauan_luas.env` | mencampur dua perubahan (chunk lebih kecil + lebih banyak) — tidak mengisolasi apa pun |
| `D_jangkauan_reviewer.env` | sama seperti C, ditambah reviewer |

## Cara membaca preset H (inti eksperimen)

| Hasil H vs G | Tafsiran |
|---|---|
| metrik **stabil**, `pct_outside_top30` mirip | LLM benar-benar membaca laporan; urutan tidak menentukan pilihannya |
| metrik **turun tajam** | LLM sebagian besar mengikuti urutan kandidat, bukan isi laporan |
| metrik **naik** | urutan retrieval justru menyesatkan model |

Ketiganya temuan yang sah dan dilaporkan apa adanya.

## Baseline sebanding

Baseline harus dihitung pada kode, versi retrieval, mode chunking, dan subset
laporan yang **sama** dengan preset:

```bash
python scripts/eval_baselines.py --reports 30 --per-chunk --pure-tfidf
python scripts/eval_baselines.py --reports 30 --per-chunk \
    --match-budget-to "results/predictions/exp_A_baseline_replikasi_*.json"
```

`results/metrics/baseline_sweep.json` yang lama **tidak dipakai**: baseline di
dalamnya dihitung dengan retrieval v5 sedangkan run sistem pembandingnya memakai
retrieval lama, dilabeli "TF-IDF-only" padahal hibrida, dan memakai mode
whole-report sedangkan sistem per-chunk.

Baseline majority dilabeli **oracle**: peringkatnya dihitung dari ground truth
laporan yang dievaluasi juga. Pada subset 30, majority-oracle top-50 mencapai
recall **1,000** — bukti langsung kebocoran label. Ia batas atas trivial, bukan
pesaing yang wajar.
