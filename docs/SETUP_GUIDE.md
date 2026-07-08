# TTP Mapping System - Setup & Test Guide

## Keadaan Saat Ini

Pipeline menggunakan **LM Studio** (OpenAI-compatible endpoint) sebagai LLM.

Komponen utama:
- Agent code sudah terhubung ke LM Studio via OpenAI SDK
- Validasi startup sudah siap (tahu mana yang kurang)
- Dataset TRAM II + MITRE ATT&CK siap diproses

---

## Setup Langkah per Langkah

### 1. Isi konfigurasi di `.env`

Pastikan nilai berikut terisi:
```
LLM_PROVIDER=lmstudio
LOCAL_LLM_BASE_URL=http://100.100.211.39:1234
LOCAL_LLM_MODEL=qwen/qwen3-4b
# Optional jika server butuh auth
# LOCAL_LLM_API_KEY=
```

### 2. Install Dependencies

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

1. Download dari: https://github.com/mitre/cti/blob/master/enterprise-attack/enterprise-attack.json
2. Atau gunakan API: https://raw.githubusercontent.com/mitre/cti/master/enterprise-attack/enterprise-attack.json
3. Simpan dengan nama **EXACT**: `enterprise-attack.json`

---

## Jalankan Pipeline

Saat semua setup siap:

```powershell
python main.py
```

Pipeline akan:
1. Load laporan dari `data/tram_ii/`
2. Load teknik ATT&CK dari `data/mitre_cti/enterprise-attack.json`
3. Inisialisasi Tactic & Technique Agent (pakai LM Studio)
4. Proses laporan 1-5 (subset untuk testing)
5. Evaluasi: Precision, Recall, Micro-F1
6. Simpan hasil ke `results/predictions/results.json`

---

## File Structure Referensi

```
tta-ttp-mapping/
├── main.py                           # Entry point
├── requirements.txt                  # Python dependencies
├── .env                              # ⭐ TOKEN DI SINI (jangan commit!)
├── .env.example                      # Template (safe to commit)
├── src/
│   ├── tactic_agent.py               # Identifikasi taktik ATT&CK
│   ├── technique_agent.py            # Ekstrak teknik ATT&CK
│   ├── orchestrator.py               # Orkestrasi pipeline per laporan
│   ├── attck_loader.py               # Load ATT&CK knowledge base
│   ├── data_loader.py                # Load TRAM II dataset
│   ├── reconciler.py                 # Rekonsiliasi taktik + teknik
│   ├── validator.py                  # Validasi teknik
│   ├── stix_builder.py               # Convert ke STIX 2.1 bundle
│   └── evaluator.py                  # Hitung metrik (P/R/F1)
├── data/
│   ├── tram_ii/                      # ⭐ Dataset TRAM II (taruh .json di sini)
│   └── mitre_cti/
│       └── enterprise-attack.json    # ⭐ MITRE ATT&CK file (taruh di sini)
└── results/
    └── predictions/                  # Output: predictions + metrics

```

---

## Quick Checklist

Sebelum jalankan `python main.py`:

- [ ] LM Studio server aktif di `LOCAL_LLM_BASE_URL`
- [ ] Dataset TRAM II ada di `data/tram_ii/` (minimal 1 file `.json`, `.mjson`, atau `.pdf`)
- [ ] File `data/mitre_cti/enterprise-attack.json` sudah ada
- [ ] Dependencies sudah install: `pip install -r requirements.txt`

---

## Support & Debugging

**Q: Model response lambat / timeout?**
A: Coba ganti model di `.env` ke yang lebih ringan, lalu jalankan lagi.

**Q: Bagaimana format TRAM II yang benar?**
A: Lihat contoh di section "Dataset TRAM II" di atas. Format `.json`/`.mjson` yang punya `sentences` + `mappings.attack_id` akan dipakai sebagai data berlabel. Untuk file `.pdf`, pipeline akan ekstrak teks otomatis (tanpa label ground-truth).

**Q: Bisa pakai model lain?**
A: Ya! Ganti `LOCAL_LLM_MODEL` sesuai model yang kamu load di LM Studio.
