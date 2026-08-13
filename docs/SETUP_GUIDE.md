# TTP Mapping System - Setup & Test Guide

## Keadaan Saat Ini

Pipeline menggunakan **LM Studio** (OpenAI-compatible endpoint) sebagai LLM.

Komponen utama:
- Agent code sudah terhubung ke LM Studio via OpenAI SDK
- Validasi startup sudah siap (tahu mana yang kurang)
- Dataset TRAM II + MITRE ATT&CK siap diproses

---

## Setup Langkah per Langkah

### 1. Muat dua model di LM Studio

Sistem membutuhkan **dua** model, bukan satu:

| Peran | Model | Fungsi |
|---|---|---|
| Generatif | `qwen/qwen3-4b` | agen Tactic, Technique, Reviewer |
| Embedding | `text-embedding-nomic-embed-text-v1.5` | retrieval hibrida (semantik) |

Unduh keduanya lewat menu **Discover**, muat keduanya di tab **Developer**
(atau *Local Server*), lalu jalankan server.

> ⚠️ Bila model embedding tidak dimuat, sistem **tetap berjalan** tetapi
> retrieval otomatis turun ke TF-IDF murni disertai peringatan di konsol.
> Plafon recall ikut turun, sehingga angka yang Anda peroleh tidak sebanding
> dengan yang dilaporkan pada `experiments/HASIL_EKSPERIMEN.md`. Kalau memang
> disengaja, setel `RETRIEVAL_EMBEDDING_HYBRID=false` agar eksplisit.

Verifikasi kedua model benar-benar `loaded`:

```powershell
curl http://localhost:1234/api/v0/models
```

### 2. Isi konfigurasi di `.env`

Buat berkas `.env` di direktori akar proyek:
```
LLM_PROVIDER=lmstudio
LOCAL_LLM_BASE_URL=http://localhost:1234
LOCAL_LLM_MODEL=qwen/qwen3-4b
LLM_DISABLE_THINKING=true     # wajib true untuk seri Qwen3 (model thinking)
# Optional jika server butuh auth
# LOCAL_LLM_API_KEY=
```

`LLM_DISABLE_THINKING=true` bersifat wajib untuk model seri Qwen3: tanpa itu,
blok penalaran menutupi keluaran JSON agen sehingga taktik gagal terpetakan.

Daftar lengkap parameter ada di [PANDUAN_PENGGUNAAN.md §D.2](PANDUAN_PENGGUNAAN.md).

### 3. Install Dependencies

```powershell
pip install -r requirements.txt
```

Atau manual jika ada yang kurang:
```powershell
pip install python-dotenv scikit-learn stix2 langgraph requests openai pypdf
```

---

## Siapkan Dataset

### Dataset TRAM II

Lokasi: `data/tram_ii/`

Setiap file harus format `.json` dengan struktur:
```json
{
  "sentences": [
    {
      "text": "The attackers used phishing emails...",
      "mappings": [
        {"attack_id": "T1566"}
      ]
    },
    {
      "text": "They executed malware via PowerShell...",
      "mappings": [
        {"attack_id": "T1059"}
      ]
    }
  ]
}
```

Format yang didukung:
- `.json`
- `.mjson`
- `.pdf`

Jika report kamu masih dalam bentuk PDF, konversi dulu ke JSON supaya lebih mudah dipakai untuk pengiriman konteks ke LLM:

```powershell
python src/knowledge/pdf_to_json_converter.py --input-dir data/tram_ii --output-dir data/tram_ii
```

Untuk scan subfolder:

```powershell
python src/knowledge/pdf_to_json_converter.py --input-dir data/tram_ii --output-dir data/tram_ii --recursive
```

Saat pipeline dijalankan lewat `python main.py`, loader akan otomatis mendeteksi file `.pdf`, mengonversinya menjadi file `*__pdf.json`, lalu membaca JSON hasil konversi tersebut.

### MITRE ATT&CK Knowledge Base

Lokasi: `data/mitre_cti/enterprise-attack.json`

1. Download dari: https://raw.githubusercontent.com/mitre/cti/master/enterprise-attack/enterprise-attack.json
2. Sumber alternatif: [mitre-attack/attack-stix-data](https://github.com/mitre-attack/attack-stix-data)
3. Simpan dengan nama **EXACT**: `enterprise-attack.json`

Versi yang dipakai pada eksperimen adalah **ATT&CK Enterprise v13.1** (tercatat
beserta checksum SHA-256 pada tiap berkas manifest run). Versi lain tetap jalan,
tetapi metrik akan bergeser karena ruang label berubah.

---

## Jalankan Pipeline

Saat semua setup siap:

```powershell
python main.py 20      # 20 laporan pertama
python main.py all     # seluruh dataset
python main.py         # interaktif; Enter = 5 laporan
```

Pipeline akan:
1. Validasi prasyarat (dataset, berkas ATT&CK, `LOCAL_LLM_BASE_URL`) dan berhenti
   dengan pesan perbaikan bila ada yang kurang
2. Load laporan dari `data/tram_ii/`
3. Load teknik ATT&CK dari `data/mitre_cti/enterprise-attack.json`
4. Inisialisasi Tactic & Technique Agent (pakai LM Studio)
5. Proses laporan sebanyak yang dipilih, **menyimpan bertahap tiap 5 laporan**
6. Evaluasi: Precision, Recall, Micro-F1 (tingkat exact, base, dan taktik)
7. Simpan hasil ke `results/predictions/results_main_<timestamp>.json`
   beserta `.manifest.json` yang merekam konfigurasi efektif run tersebut

Nama berkas ber-timestamp, jadi run baru tidak pernah menimpa hasil run lama.
`Ctrl+C` tetap menghasilkan evaluasi atas laporan yang sudah selesai.

---

## File Structure Referensi

```
tta-ttp-mapping/
├── main.py                           # Entry point
├── requirements.txt                  # Python dependencies
├── .env                              # ⭐ KONFIGURASI DI SINI (jangan commit!)
├── src/
│   ├── agents/
│   │   ├── tactic_agent.py           # Identifikasi taktik ATT&CK
│   │   ├── technique_agent.py        # Pilih teknik dari daftar kandidat
│   │   ├── reviewer_agent.py         # Review & picu revisi
│   │   ├── retrieval.py              # RAG: kandidat (TF-IDF + embedding)
│   │   └── prompt_budget.py          # Anggaran token prompt vs n_ctx
│   ├── pipeline/
│   │   ├── orchestrator.py           # Orkestrasi pipeline per laporan
│   │   ├── reconciler.py             # Rekonsiliasi taktik + teknik
│   │   └── validator.py              # Validasi teknik
│   ├── knowledge/
│   │   ├── attck_loader.py           # Load ATT&CK knowledge base
│   │   └── data_loader.py            # Load TRAM II dataset
│   ├── reporting/
│   │   ├── stix_builder.py           # Convert ke STIX 2.1 bundle
│   │   ├── report_builder.py         # Laporan PDF
│   │   └── evidence.py               # Kalimat bukti per mapping
│   ├── evaluation/
│   │   ├── evaluator.py              # Hitung metrik (P/R/F1)
│   │   └── evaluate_run.py           # Harness evaluasi
│   ├── utils/
│   │   └── run_manifest.py           # Rekam konfigurasi efektif tiap run
│   └── web/
│       └── web_app.py                # Backend FastAPI
├── data/
│   ├── tram_ii/                      # ⭐ Dataset TRAM II (taruh .mjson di sini)
│   └── mitre_cti/
│       └── enterprise-attack.json    # ⭐ MITRE ATT&CK file (taruh di sini)
├── experiments/                      # Preset ablasi A–H + hasilnya
└── results/
    ├── predictions/                  # Output: prediksi + manifest per run
    └── metrics/                      # Baseline & plafon retrieval
```

Impor antar-modul memakai `src/` sebagai akar (`from agents.retrieval import ...`);
`main.py` menambahkan `src/` ke `sys.path` di baris ke-5.

---

## Quick Checklist

Sebelum jalankan `python main.py`:

- [ ] LM Studio server aktif di `LOCAL_LLM_BASE_URL`
- [ ] **Kedua** model `state=loaded` — generatif DAN embedding
      (cek: `curl <url>/api/v0/models`)
- [ ] Dataset TRAM II ada di `data/tram_ii/` (minimal 1 file `.json`, `.mjson`, atau `.pdf`)
- [ ] File `data/mitre_cti/enterprise-attack.json` sudah ada
- [ ] Dependencies sudah install: `pip install -r requirements.txt`
- [ ] `LLM_DISABLE_THINKING=true` bila memakai model seri Qwen3

---

## Support & Debugging

**Q: Model response lambat / timeout?**
A: Coba ganti model di `.env` ke yang lebih ringan, lalu jalankan lagi.

**Q: Bagaimana format TRAM II yang benar?**
A: Lihat contoh di section "Dataset TRAM II" di atas. Format `.json`/`.mjson` yang punya `sentences` + `mappings.attack_id` akan dipakai sebagai data berlabel. Untuk file `.pdf`, pipeline akan ekstrak teks otomatis (tanpa label ground-truth).

**Q: Bisa pakai model lain?**
A: Ya! Ganti `LOCAL_LLM_MODEL` sesuai model yang kamu load di LM Studio. Untuk
model seri Qwen3 (mode *thinking*), `LLM_DISABLE_THINKING=true` wajib.

**Q: Konsol memunculkan peringatan embedding gagal / retrieval kembali ke TF-IDF?**
A: Model embedding belum dimuat di LM Studio. Muat
`text-embedding-nomic-embed-text-v1.5`, atau setel `RETRIEVAL_EMBEDDING_HYBRID=false`
bila memang ingin TF-IDF murni. Perhatikan bahwa hasilnya tidak sebanding dengan
angka yang dilaporkan.

**Q: Taktik selalu kosong di hasil?**
A: Model *thinking* menutupi keluaran JSON agen. Setel `LLM_DISABLE_THINKING=true`.

**Q: Run panjang tiba-tiba mati di tengah?**
A: Periksa apakah kedua model masih `state=loaded`. Pada GPU dengan VRAM terbatas,
LM Studio dapat bongkar-pasang model sehingga server berhenti merespons. Hasil yang
sudah selesai tetap tersimpan (checkpoint tiap 5 laporan) dan potongan run dapat
digabungkan dengan `scripts/merge_partial_runs.py`.

Penanganan galat yang lebih lengkap ada di
[PANDUAN_PENGGUNAAN.md §J](PANDUAN_PENGGUNAAN.md).
