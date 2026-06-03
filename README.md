# TTP Mapping PoC - LM Studio LLM

Sistem untuk memetakan Cyber Threat Intelligence (CTI) ke MITRE ATT&CK framework menggunakan LLM dari server local LM Studio.

## Quick Start (3 Menit)

### 1. Konfigurasi LM Studio (Local Server)

Pastikan LM Studio server aktif dan endpoint OpenAI-compatible tersedia.

Edit `.env`:
```
LLM_PROVIDER=lmstudio
LOCAL_LLM_BASE_URL=http://100.100.211.39:1234
LOCAL_LLM_MODEL=your-loaded-model-name
# Optional jika server butuh auth
# LOCAL_LLM_API_KEY=...
```

### 2. Pastikan LM Studio server aktif
Server harus berjalan dan endpoint OpenAI-compatible tersedia pada `LOCAL_LLM_BASE_URL`.

### 3. Siapkan Dataset
- Letakkan file TRAM II `.json`, `.mjson`, atau `.pdf` di: `data/tram_ii/`
- Letakkan `enterprise-attack.json` di: `data/mitre_cti/`

Jika sumber awal kamu PDF dan ingin ubah dulu ke JSON agar lebih mudah diproses LLM:

```powershell
python src/pdf_to_json_converter.py --input-dir data/tram_ii --output-dir data/tram_ii
```

Saat `main.py` dijalankan, loader juga akan otomatis mengonversi PDF ke JSON dengan nama `__pdf.json` di akhir nama file (contoh: `report__pdf.json`) lalu memproses JSON tersebut.

Opsional recursive:

```powershell
python src/pdf_to_json_converter.py --input-dir data/tram_ii --output-dir data/tram_ii --recursive
```

### 4. Jalankan
```powershell
python main.py
```

---

## Web UI PoC (Optional)

Gunakan UI sederhana untuk upload laporan dan validasi hasil mapping.

```powershell
python -m uvicorn src.web_app:app --reload
```

Buka di browser: http://127.0.0.1:8000

---

## Dokumentasi Lengkap

👉 Baca [SETUP_GUIDE.md](SETUP_GUIDE.md) untuk detail setup, troubleshooting, dan format dataset.

---

## Architecture

```
Input (TRAM II Reports)
    ↓
[Tactic Agent] + [Technique Agent]  ← LM Studio API
    ↓
[Reconciler] → [Validator] → [STIX Builder]
    ↓
Output (JSON + STIX 2.1 Bundles)
```

---

## Files

- `main.py` - Entry point
- `src/` - Core pipeline modules
- `data/tram_ii/` - Input dataset (mendukung .json, .mjson, .pdf)
- `src/pdf_to_json_converter.py` - Konversi PDF report ke JSON
- `data/mitre_cti/` - ATT&CK knowledge base
- `results/predictions/` - Output predictions + metrics
- `.env` - Config token (jangan commit!)

---

## Tech Stack

- **LLM**: LM Studio (OpenAI-compatible endpoint)
- **Framework**: MITRE ATT&CK
- **Dataset**: TRAM II (Cyber Threat Intelligence reports)
- **Output**: STIX 2.1 + evaluation metrics (P/R/F1)

---

## ⚠️ Security

- Jangan pernah hardcode token di repository
- Token di `.env` sudah di `.gitignore`
- Jangan share token di chat/issue
- Revoke token lama segera setelah diganti

---

## Support

Kalau ada error, cek [SETUP_GUIDE.md](SETUP_GUIDE.md) section "Support & Debugging".

