# Laporan Percobaan: Run Parsial Pipeline TTP Mapping (23/151 Laporan)

**Tanggal:** 10 Juli 2026 (malam)
**Status:** Run dihentikan manual di laporan ke-24 (23 laporan selesai penuh) atas permintaan — dilanjutkan lain waktu.
**File hasil (JSON):** `results/predictions/partial_run_20260710_221227.json`

---

## 1. Konfigurasi Percobaan

| Parameter | Nilai |
|---|---|
| Model LLM | `qwen/qwen3-4b` (LM Studio, OpenAI-compatible API) |
| Server | `http://100.100.211.39:1234` (via Tailscale) |
| Context window ter-load | **4.096 token** (kemampuan maks model: 32.768) |
| Mode thinking | Dinonaktifkan (`LLM_DISABLE_THINKING=true`, `/no_think` + `enable_thinking=false`) |
| Structured output | Aktif (JSON schema `{"ids": [...]}`) |
| Kandidat teknik (top-k) | 50, dibatasi `CANDIDATE_LIST_MAX_CHARS=4500` |
| Potongan laporan | Tactic agent 6.000 char; Technique agent 3.500 char/chunk, maks 3 chunk |
| Reviewer agent | **Nonaktif** (run baseline tanpa debate loop) |
| Knowledge base | ATT&CK Enterprise saja (607 teknik) |
| Dataset | TRAM II, 151 laporan CTI |
| Kecepatan | ~67 detik/laporan |

Catatan: pada run pertama sempat terjadi insiden server (2× timeout lalu semua request ditolak `Context size has been exceeded` meski prompt hanya 2.353 token). Terbukti kondisi server sesaat — setelah restart run, **23 laporan diproses tanpa satu pun error**.

## 2. Metrik Agregat (23 laporan)

| Metrik | Precision | Recall | Micro-F1 |
|---|---|---|---|
| **Teknik — exact** (termasuk sub-teknik) | 0,250 | 0,144 | **0,183** |
| **Teknik — base** (abaikan sub-teknik) | 0,365 | 0,173 | **0,235** |
| **Taktik** (GT diturunkan dari teknik GT) | 0,594 | 0,494 | **0,539** |

Pembacaan singkat:

- **Taktik jauh lebih baik daripada teknik** (F1 0,54 vs 0,18) — konsisten dengan run-run sebelumnya: model 4B cukup andal mengenali *fase* serangan, tapi kesulitan memilih teknik spesifik dari ratusan kandidat.
- **Gap exact vs base (0,18 → 0,23)** menunjukkan model sering benar di *keluarga* teknik tapi salah memilih sub-tekniknya (contoh: memprediksi T1055.001 padahal GT-nya T1055.002).
- Dibanding kondisi awal proyek (F1 exact ~0,03), prompt v2 + top-k 50 + structured output menaikkan F1 exact ke ~0,18 pada sampel ini.

## 3. Hasil Per Laporan

| # | Laporan | Pred | GT | TP | FP | FN | Benar tapi dibuang* |
|---|---|---|---|---|---|---|---|
| 1 | 3CXDesktopApp Backdoored (Lazarus) | 3 | 9 | 0 | 3 | 9 | 2 |
| 2 | TA410 umbrella (cyberespionage) | 15 | 38 | 3 | 12 | 35 | – |
| 3 | AA20-258A Chinese MSS Actor | 15 | 13 | 1 | 14 | 12 | – |
| 4 | AA20-336A APT vs US Think Tanks | 8 | 2 | 0 | 8 | 2 | 1 |
| 5 | AA21-076A TrickBot Malware | 9 | 23 | 5 | 4 | 18 | – |
| 6 | AA21-200A APT40 (MSS Hainan) | 7 | 12 | 2 | 5 | 10 | – |
| 7 | AA21-200B Chinese State-Sponsored TTPs | 5 | 24 | 2 | 3 | 22 | – |
| 8 | AA22-320A Iranian APT (Crypto Miner) | 9 | 15 | 5 | 4 | 10 | 1 |
| 9 | Abusing cloud services | 9 | 24 | 4 | 5 | 20 | 1 |
| 10 | Akira Ransomware | 5 | 12 | 3 | 2 | 9 | – |
| 11 | Wiper attacks analysis | 3 | 7 | 0 | 3 | 7 | 1 |
| 12 | Solorigate DLL (SolarWinds) | 15 | 14 | 1 | 14 | 13 | – |
| 13 | Domain fronting (Myanmar, Cobalt Strike) | 5 | 7 | 2 | 3 | 5 | – |
| 14 | Babadeda Crypter | 14 | 10 | 3 | 11 | 7 | – |
| 15 | Banking Trojan Techniques | 15 | 11 | 3 | 12 | 8 | – |
| 16 | BazarLoader Malspam | 1 | 12 | 0 | 1 | 12 | 1 |
| 17 | Trigona Ransomware | 3 | 16 | 0 | 3 | 16 | 4 |
| 18 | BlueNoroff bypass MoTW | 12 | 17 | 2 | 10 | 15 | 1 |
| 19 | Breaking Pedersen Hashes (non-CTI) | 0 | 0 | ✓ | – | – | – |
| 20 | BumbleBee Roasts to Domain Admin | 9 | 29 | 2 | 7 | 27 | 1 |
| 21 | Bypassing Intel CET (OffSec) | 0 | 2 | 0 | – | 2 | – |
| 22 | **LockBit Campaign** | 15 | 9 | **7** | 8 | 2 | – |
| 23 | Carbon Black TrueBot | 3 | 7 | 0 | 3 | 7 | – |

\* Kolom terakhir = teknik yang **ada di ground truth dan sudah ditemukan agent**, tapi dibuang tahap rekonsiliasi (filter konsistensi taktik).

## 4. Temuan Kualitatif

### 4.1 Reconciler membuang jawaban benar (13 teknik di 9 laporan)
Filter konsistensi taktik di `reconciler.py` menghapus **13 prediksi yang sebenarnya benar** — hampir 30% dari total TP yang berhasil dikumpulkan (45). Kasus terparah: Trigona (4 teknik benar dibuang, hasil akhir TP = 0). Pola: teknik benar dibuang karena taktik induknya tidak masuk daftar taktik yang teridentifikasi. **Ini target perbaikan dengan rasio dampak/usaha terbaik saat ini.**

### 4.2 FP menggerombol dalam satu keluarga sub-teknik
Model cenderung "memborong" banyak sub-teknik satu keluarga sekaligus ketika deskripsi kandidatnya mirip: Banking Trojan → 9 sub-teknik T1055.* dipilih (GT hanya 3); AA20-258A & BumbleBee → borongan teknik Reconnaissance T1596.*/T1593.*. Deskripsi kandidat yang dipangkas 120 karakter membuat sub-teknik nyaris tak terbedakan.

### 4.3 Recall rendah didominasi keterbatasan konteks
FN terbesar ada di laporan panjang (TA410: 35 FN; BumbleBee: 27 FN) — teknik GT banyak yang bahkan tidak masuk daftar kandidat karena batas `n_ctx=4096` memaksa pemangkasan teks laporan dan daftar kandidat. Konsisten dengan analisis retrieval ceiling sebelumnya.

### 4.4 Kasus negatif ditangani benar
Laporan non-CTI *Breaking Pedersen Hashes* (GT kosong) menghasilkan prediksi kosong — prompt v2 dengan aturan "return []" bekerja. Sebaliknya *Bypassing Intel CET* (GT: T1055.009, T1106) menghasilkan kosong padahal ada 2 GT — teks exploit-development memang di luar pola bahasa laporan CTI.

## 5. Insiden Teknis (untuk reproducibility)

1. **Salah interpreter**: `ModuleNotFoundError: No module named 'dotenv'` terjadi bila memakai Python sistem. Wajib pakai `.venv`: `.\.venv\Scripts\python.exe scripts\run_full_pipeline.py` dari root proyek.
2. **Error `Context size has been exceeded` yang menyesatkan**: muncul setelah request timeout menumpuk di LM Studio, meski prompt muat. Solusi: pastikan server idle; jangka panjang: load model dengan n_ctx ≥ 16k.
3. Run dihentikan di tengah tidak menyimpan JSON (penyimpanan hanya di akhir skrip) — hasil parsial ini direkonstruksi dari log proses dan dievaluasi ulang dengan `evaluation.evaluator` yang sama dengan pipeline.

## 6. Langkah Berikutnya

1. **Muat ulang model di LM Studio dengan context 16k/32k**, lalu naikkan `CANDIDATE_LIST_MAX_CHARS` / `LOCAL_LLM_REPORT_MAX_CHARS` / `LLM_MAX_CHUNKS` — membuka plafon recall sekaligus menghilangkan kerentanan context-overflow.
2. **Perbaiki reconciler** agar tidak membuang teknik yang valid (mis. longgarkan filter bila teknik didukung bukti kuat, atau derive taktik dari teknik alih-alih sebaliknya).
3. Lanjutkan run penuh 151 laporan (idealnya setelah #1), lalu bandingkan dengan baseline `scripts/eval_baselines.py`.
4. Eksperimen reviewer ON vs OFF pada konfigurasi yang sama untuk mengukur kontribusi debate loop.
5. Pertimbangkan menyimpan hasil **incremental** di `run_full_pipeline.py` (tulis JSON per-N laporan) agar run yang terputus tidak kehilangan hasil.

---
*Dokumen ini dibuat otomatis dari log run `run_full_pipeline.py` 10 Juli 2026 malam; metrik dihitung dengan `src/evaluation/evaluator.py` (fungsi yang sama dengan evaluasi penuh).*
