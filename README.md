# TTP Mapping System — CTI ke MITRE ATT&CK (LLM Multi-Agent)

Sistem untuk memetakan laporan **Cyber Threat Intelligence (CTI)** ke framework
**MITRE ATT&CK** (Tactics & Techniques) menggunakan LLM lokal (LM Studio) dengan
pipeline **multi-agent** (LangGraph). Output ganda: **STIX 2.1** bundle + **laporan PDF**.

---

## Struktur Proyek

```
tta-ttp-mapping/
├── main.py                     # Entry point: pipeline batch atas dataset
├── requirements.txt
├── .env                        # Konfigurasi LM Studio (tidak di-commit)
│
├── src/                        # Kode sumber (paket Python)
│   ├── agents/                 # Agen LLM
│   │   ├── tactic_agent.py         · identifikasi Tactics
│   │   ├── technique_agent.py      · pilih Techniques dari kandidat
│   │   ├── reviewer_agent.py       · review & picu revisi (debat)
│   │   ├── retrieval.py            · RAG: kandidat ATT&CK (TF-IDF + embedding)
│   │   └── prompt_budget.py        · anggaran token prompt vs n_ctx
│   ├── pipeline/               # Orkestrasi
│   │   ├── orchestrator.py         · graph LangGraph (alur utama)
│   │   ├── reconciler.py           · rekonsiliasi taktik↔teknik
│   │   └── validator.py            · validasi ID ATT&CK
│   ├── knowledge/              # Data & knowledge base
│   │   ├── attck_loader.py         · muat ATT&CK (teknik & taktik)
│   │   ├── data_loader.py          · muat dataset TRAM II + ground truth
│   │   └── pdf_to_json_converter.py
│   ├── reporting/              # Keluaran
│   │   ├── stix_builder.py         · bundle STIX 2.1
│   │   ├── report_builder.py       · laporan PDF
│   │   └── evidence.py             · kalimat rujukan (evidence) per mapping
│   ├── evaluation/             # Evaluasi
│   │   ├── evaluator.py            · Precision/Recall/F1 (teknik & taktik)
│   │   ├── evaluate_run.py         · harness evaluasi (file / live)
│   │   └── build_excel_report.py   · ekspor laporan Excel
│   ├── utils/
│   │   └── run_manifest.py     # Rekam konfigurasi efektif tiap run
│   └── web/
│       └── web_app.py          # Backend FastAPI (UI + API)
│
├── web_ui/                     # Front-end web
│   ├── index.html                  · homepage (penjelasan)
│   ├── app.html                    · console analisis satu laporan
│   └── batch.html                  · console evaluasi batch
│
├── scripts/                    # Skrip utilitas / batch
│   ├── run_experiment.py           · jalankan satu preset eksperimen
│   ├── compare_experiments.py      · tabel perbandingan antar preset
│   ├── merge_partial_runs.py       · gabung potongan run yang terputus
│   ├── run_full_pipeline.py        · jalankan pipeline atas seluruh dataset
│   ├── eval_baselines.py           · baseline TF-IDF / hibrida / majority
│   ├── retrieval_ceiling.py        · plafon recall tahap retrieval
│   ├── fn_fp_analysis.py           · analisis false negative & false positive
│   ├── compare_results.py          · bandingkan dua hasil run
│   ├── verify_results.py           · sanity-check hasil
│   └── audit_dataset.py            · audit dataset
│
├── experiments/                # Ablasi terkendali (tiap preset beda 1 baris)
│   ├── A_baseline_replikasi.env    · baseline
│   ├── E_jangkauan_penuh.env       · MAX_CHUNKS 3→10
│   ├── F_jangkauan_reviewer.env    · E + Reviewer aktif
│   ├── G_tanpa_filter.env          · ACCEPT_TOP_N 30→0
│   ├── H_acak_kandidat.env         · G + urutan kandidat diacak
│   ├── HASIL_EKSPERIMEN.md         · temuan lengkap + keterbatasan
│   ├── RANCANGAN_PRESET.md         · rancangan & alasan tiap preset
│   └── tabel_perbandingan.md       · tabel metrik antar preset
│
├── tests/                      # Uji unit
│
├── docs/                       # Dokumentasi
│   ├── PANDUAN_PENGGUNAAN.md       · panduan lengkap & reproduksi
│   ├── SETUP_GUIDE.md
│   ├── AGENT.md
│   └── ARSITEKTUR_DAN_FLOWCHART.md
│
├── data/
│   ├── tram_ii/                # Dataset laporan CTI + anotasi (ground truth)
│   └── mitre_cti/              # enterprise-attack.json (tidak di-commit; unduh dari MITRE)
│
└── results/
    ├── predictions/            # Hasil prediksi + manifest tiap run (JSON)
    └── metrics/                # Baseline & plafon retrieval (JSON)
```

---

## Quick Start

### 1. Prasyarat
```bash
python -m pip install -r requirements.txt
```

### 2. Konfigurasi LM Studio

Muat **dua model** di LM Studio, lalu jalankan servernya:

| Peran | Model | Wajib? |
|---|---|---|
| Generatif | `qwen/qwen3-4b` | ya — agen Tactic, Technique, Reviewer |
| Embedding | `text-embedding-nomic-embed-text-v1.5` | ya untuk retrieval hibrida |

Tanpa model embedding sistem tetap jalan, tapi retrieval otomatis turun ke
TF-IDF murni (disertai peringatan di konsol) dan **plafon recall ikut turun** —
angka yang Anda dapat tidak akan sebanding dengan yang dilaporkan.

Buat `.env`:
```
LLM_PROVIDER=lmstudio
LOCAL_LLM_BASE_URL=http://localhost:1234
LOCAL_LLM_MODEL=qwen/qwen3-4b
# LOCAL_LLM_API_KEY=...        # jika server butuh auth
LLM_DISABLE_THINKING=true      # wajib true untuk model thinking (Qwen3); false untuk Qwen2.5
```

Daftar lengkap parameter (retrieval, chunking, reviewer, rekonsiliasi) ada di
[docs/PANDUAN_PENGGUNAAN.md §D.2](docs/PANDUAN_PENGGUNAAN.md).

### 3. Siapkan data
- Dataset TRAM II (`.json`/`.mjson`/`.pdf`) → `data/tram_ii/`
- `enterprise-attack.json` → `data/mitre_cti/` (unduh dari
  [mitre-attack/attack-stix-data](https://github.com/mitre-attack/attack-stix-data))

### 4. Jalankan

**Web UI (disarankan untuk demo):**
```bash
python -m uvicorn web.web_app:app --app-dir src --host 127.0.0.1 --port 8000
```
Buka http://127.0.0.1:8000 → homepage → **Buka Console**.

**Pipeline batch (subset dataset):**
```bash
python main.py 20      # 20 laporan pertama
python main.py all     # seluruh dataset
python main.py         # interaktif, default 5 laporan
```
Hasil disimpan bertahap tiap 5 laporan ke
`results/predictions/results_main_<timestamp>.json`, jadi run panjang yang
terputus tidak kehilangan seluruh capaian.

**Pipeline penuh atas seluruh dataset:**
```bash
python scripts/run_full_pipeline.py
```

**Evaluasi (metrik P/R/F1):**
```bash
# dari file hasil yang sudah ada
python src/evaluation/evaluate_run.py results/predictions/<file>.json

# atau live N laporan
$env:EVAL_N="5"; python src/evaluation/evaluate_run.py    # PowerShell
EVAL_N=5 python src/evaluation/evaluate_run.py            # bash
```

---

## Reproduksi eksperimen

Setiap preset di `experiments/` berbeda dari induknya pada **tepat satu baris**,
sehingga efek yang terukur bisa diatribusikan ke satu variabel saja.

```bash
python scripts/run_experiment.py --preset E --reports 30
python scripts/compare_experiments.py results/predictions/exp_*.json \
    --out experiments/tabel_perbandingan.md
```

Tiap run menulis `<hasil>.json` **dan** `<hasil>.json.manifest.json` yang merekam
konfigurasi efektif, commit git, checksum ATT&CK, statistik token prompt, dan
metrik — sehingga angka yang dilaporkan dapat ditelusuri ke kondisi run-nya.
Manifest ditulis di tiap checkpoint, jadi run yang terhenti tetap meninggalkan
rekaman (`status`: `complete` / `partial` / `aborted`).

Temuan lengkap beserta keterbatasannya ada di
[experiments/HASIL_EKSPERIMEN.md](experiments/HASIL_EKSPERIMEN.md).

---

## Arsitektur pipeline

```
Input (laporan CTI: teks / .json / .mjson / .pdf)
        │
        ▼
[Tactic Agent] → [Technique Agent]        ← LM Studio (LLM lokal)
        │            (RAG: retrieval kandidat ATT&CK)
        ▼
[Reviewer] → revisi bila tidak konsisten  (loop debat multi-agent, opt-in)
        │
        ▼
[Reconciler] → [Validator]
        │
        ▼
Output: STIX 2.1 bundle  +  laporan PDF (dengan kalimat rujukan)
```

---

## Dokumentasi lengkap
- **Panduan penggunaan & reproduksi:** [docs/PANDUAN_PENGGUNAAN.md](docs/PANDUAN_PENGGUNAAN.md)
  — prasyarat, seluruh parameter konfigurasi, format data, API, penanganan galat
- Setup & troubleshooting: [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md)
- Arsitektur & flowchart: [docs/ARSITEKTUR_DAN_FLOWCHART.md](docs/ARSITEKTUR_DAN_FLOWCHART.md)
- Catatan agen/konfigurasi: [docs/AGENT.md](docs/AGENT.md)
- Hasil eksperimen ablasi: [experiments/HASIL_EKSPERIMEN.md](experiments/HASIL_EKSPERIMEN.md)

## Tech stack
LM Studio (LLM lokal) · LangGraph · MITRE ATT&CK Enterprise · dataset TRAM II ·
FastAPI · STIX 2.1 · reportlab (PDF)

## Keamanan
`.env` berisi konfigurasi lokal dan **tidak di-commit** (sudah di `.gitignore`).
Jangan hardcode kredensial di repo.
