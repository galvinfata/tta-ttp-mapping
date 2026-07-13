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
│   │   ├── technique_agent.py      · retrieval + pilih Techniques
│   │   └── reviewer_agent.py       · review & picu revisi (debat)
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
│   └── web/
│       └── web_app.py          # Backend FastAPI (UI + API)
│
├── web_ui/                     # Front-end web
│   ├── index.html                  · homepage (penjelasan)
│   └── app.html                    · console (tool utama)
│
├── scripts/                    # Skrip utilitas / batch
│   ├── run_full_pipeline.py        · jalankan pipeline atas seluruh dataset
│   ├── compare_results.py          · bandingkan dua hasil run
│   ├── verify_results.py           · sanity-check hasil
│   └── audit_dataset.py            · audit dataset
│
├── docs/                       # Dokumentasi
│   ├── SETUP_GUIDE.md
│   ├── AGENT.md
│   ├── ARSITEKTUR_DAN_FLOWCHART.md
│   └── skripsi/                    · dokumen tugas akhir (BAB 4, dsb.)
│
├── data/
│   ├── tram_ii/                # Dataset laporan CTI + anotasi (ground truth)
│   └── mitre_cti/              # enterprise-attack.json (tidak di-commit; unduh dari MITRE)
│
└── results/
    ├── predictions/            # Hasil prediksi (JSON)
    └── metrics/                # Laporan metrik (Excel)
```

---

## Quick Start

### 1. Prasyarat
```bash
python -m pip install -r requirements.txt
```

### 2. Konfigurasi LM Studio
Pastikan LM Studio server aktif (endpoint OpenAI-compatible). Buat `.env`:
```
LLM_PROVIDER=lmstudio
LOCAL_LLM_BASE_URL=http://100.100.211.39:1234
LOCAL_LLM_MODEL=qwen/qwen3-4b
# LOCAL_LLM_API_KEY=...        # jika server butuh auth
LLM_DISABLE_THINKING=true      # wajib true untuk model thinking (Qwen3); false untuk Qwen2.5
```

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
python main.py
```

**Pipeline penuh atas seluruh dataset:**
```bash
python scripts/run_full_pipeline.py
```

**Evaluasi (metrik P/R/F1):**
```bash
# dari file hasil yang sudah ada
python src/evaluation/evaluate_run.py results/predictions/<file>.json
# atau live N laporan
EVAL_N=5 python src/evaluation/evaluate_run.py
```

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
- Setup & troubleshooting: [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md)
- Arsitektur & flowchart: [docs/ARSITEKTUR_DAN_FLOWCHART.md](docs/ARSITEKTUR_DAN_FLOWCHART.md)
- Catatan agen/konfigurasi: [docs/AGENT.md](docs/AGENT.md)

## Tech stack
LM Studio (LLM lokal) · LangGraph · MITRE ATT&CK Enterprise · dataset TRAM II ·
FastAPI · STIX 2.1 · reportlab (PDF)

## Keamanan
`.env` berisi konfigurasi lokal dan **tidak di-commit** (sudah di `.gitignore`).
Jangan hardcode kredensial di repo.
