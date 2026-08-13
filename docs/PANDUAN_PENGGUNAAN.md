# PANDUAN PENGGUNAAN DAN REPRODUKSI ARTEFAK
## Sistem Pemetaan Laporan CTI ke MITRE ATT&CK Berbasis *Multi-Agent* LLM Lokal

---

## A. Pendahuluan

### A.1 Tujuan Panduan

Panduan ini merupakan wujud saluran komunikasi bagi audiens teknis (*technology-oriented audience*) sebagaimana dituntut oleh *Guideline 7: Communication of Research* (Hevner dkk., 2004) dan tahap *communication* dalam DSRM (Peffers dkk., 2007). Dokumen ini memuat detail yang memadai agar artefak dapat **dikonstruksi ulang, dijalankan, dan diverifikasi kembali** oleh peneliti maupun praktisi lain di luar penulis.

### A.2 Ruang Lingkup

Panduan mencakup:

1. Prasyarat perangkat keras dan perangkat lunak;
2. Prosedur instalasi dan konfigurasi lingkungan;
3. Tata cara pengoperasian melalui antarmuka web dan antarmuka baris perintah;
4. Format masukan dan keluaran artefak;
5. Prosedur reproduksi evaluasi pada dataset TRAM II hingga menghasilkan kembali metrik dan grafik;
6. Antarmuka pemrograman (API) untuk integrasi;
7. Penanganan galat yang umum terjadi dan batasan artefak.

### A.3 Audiens

Panduan ditujukan bagi analis *threat intelligence*, peneliti keamanan siber, dan pengembang yang ingin menggunakan atau mengembangkan artefak. Pembaca diasumsikan menguasai dasar sistem operasi, Python, dan konsep MITRE ATT&CK.

---

## B. Prasyarat

### B.1 Perangkat Keras

| Komponen | Minimum | Disarankan |
|---|---|---|
| RAM | 16 GB | 32 GB |
| GPU | — (mode CPU, sangat lambat) | VRAM ≥ 8 GB (mis. RTX 3060/4060) |
| Penyimpanan | 15 GB kosong | 25 GB kosong |

Kebutuhan GPU berasal dari inferensi LLM lokal. Model Qwen3-4B dengan kuantisasi 4-bit membutuhkan sekitar 3–4 GB VRAM; sisanya untuk *context window* dan model *embedding*.

### B.2 Perangkat Lunak

| Perangkat lunak | Versi | Keterangan |
|---|---|---|
| Python | 3.10 atau lebih baru | Kode memakai sintaks *union type* (`dict \| None`) |
| LM Studio | 0.3.x atau lebih baru | Penyedia *endpoint* LLM lokal yang kompatibel OpenAI |
| Git | bebas | Untuk mengunduh kode sumber |
| Peramban web | modern | Untuk antarmuka web (Chrome/Edge/Firefox) |

### B.3 Model LLM

Artefak membutuhkan **dua model** yang dimuat pada LM Studio:

| Peran | Model yang digunakan pada penelitian | Fungsi |
|---|---|---|
| Model generatif | `qwen/qwen3-4b` | Agen Tactic, Technique, dan Reviewer |
| Model *embedding* | `text-embedding-nomic-embed-text-v1.5` | Komponen *retrieval* hibrida (semantik) |

Model generatif lain dapat digunakan dengan mengubah `LOCAL_LLM_MODEL`. Perlu diperhatikan bahwa model dengan mode *thinking* (seri Qwen3) mengharuskan `LLM_DISABLE_THINKING=true`; tanpa itu, keluaran JSON agen akan tertutup oleh blok penalaran sehingga taktik gagal terpetakan.

Apabila model *embedding* tidak tersedia, sistem tetap berjalan: *retrieval* otomatis kembali ke mode TF-IDF murni disertai peringatan pada konsol, dengan konsekuensi penurunan plafon *recall*.

---

## C. Instalasi

### C.1 Mengunduh Kode Sumber

```bash
git clone <URL-repositori> tta-ttp-mapping
cd tta-ttp-mapping
```

### C.2 Membuat Lingkungan Virtual

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### C.3 Memasang Dependensi

```bash
python -m pip install -r requirements.txt
```

Berkas `requirements.txt` memuat: `python-dotenv`, `scikit-learn`, `stix2`, `langgraph`, `requests`, `openai`, `pypdf`, `fastapi`, `uvicorn`, `python-multipart`, dan `reportlab`.

Dua pustaka tambahan diperlukan **hanya untuk pelaporan evaluasi** dan belum tercantum pada `requirements.txt`:

```bash
python -m pip install openpyxl matplotlib
```

`openpyxl` dibutuhkan oleh pembangkit laporan Excel, sedangkan `matplotlib` dibutuhkan oleh pembangkit grafik evaluasi.

### C.4 Menyiapkan LM Studio

1. Pasang LM Studio, lalu unduh model `qwen/qwen3-4b` dan `text-embedding-nomic-embed-text-v1.5` melalui menu *Discover*.
2. Buka tab **Developer** (atau *Local Server*), muat kedua model, lalu jalankan server.
3. Catat alamat *endpoint* yang tampil, umumnya `http://127.0.0.1:1234`.
4. Verifikasi server aktif:

   ```bash
   curl http://127.0.0.1:1234/v1/models
   ```

   Respons berisi daftar model yang termuat. Apabila LM Studio berjalan pada mesin lain dalam satu jaringan, aktifkan opsi *Serve on Local Network* dan gunakan alamat IP mesin tersebut.

---

## D. Konfigurasi Lingkungan

Konfigurasi dibaca dari berkas `.env` pada direktori akar proyek (dimuat melalui `python-dotenv`). Berkas ini **tidak disertakan dalam repositori** karena memuat konfigurasi lokal.

### D.1 Konfigurasi Minimum

Buat berkas `.env` dengan isi berikut:

```ini
LLM_PROVIDER=lmstudio
LOCAL_LLM_BASE_URL=http://127.0.0.1:1234
LOCAL_LLM_MODEL=qwen/qwen3-4b
LLM_DISABLE_THINKING=true
```

### D.2 Daftar Parameter Konfigurasi

Seluruh parameter bersifat opsional kecuali yang ditandai wajib. Nilai bawaan diambil langsung dari kode sumber.

**Koneksi LLM**

| Parameter | Bawaan | Keterangan |
|---|---|---|
| `LLM_PROVIDER` | `lmstudio` | Penyedia LLM yang aktif |
| `LOCAL_LLM_BASE_URL` | — (**wajib**) | *Endpoint* LM Studio, tanpa akhiran `/v1` |
| `LOCAL_LLM_MODEL` | `qwen/qwen3-4b` | Model untuk agen Tactic dan Technique |
| `LOCAL_LLM_REVIEWER_MODEL` | `qwen/qwen3-4b` | Model untuk agen Reviewer |
| `LOCAL_LLM_API_KEY` | *(kosong)* | Diisi bila server memerlukan autentikasi |
| `LOCAL_LLM_FALLBACK_MODEL` | *(kosong)* | Model cadangan bila model utama gagal dimuat |
| `LLM_REQUEST_TIMEOUT_SECONDS` | `300` | Batas waktu satu panggilan LLM (detik) |

**Perilaku LLM**

| Parameter | Bawaan | Keterangan |
|---|---|---|
| `LLM_DISABLE_THINKING` | `true` | **Wajib `true` untuk model seri Qwen3**; `false` untuk Qwen2.5 |
| `LLM_REASONING_EFFORT` | `none` | Tingkat penalaran bila model mendukung |
| `LLM_STRUCTURED_OUTPUT` | `true` | Memaksa keluaran berskema JSON |
| `LOCAL_LLM_STRICT_JSON` | `true` | Mode JSON ketat |
| `LOCAL_LLM_MAX_TOKENS_TACTIC` | `512` | Batas token keluaran agen Tactic |
| `LOCAL_LLM_MAX_TOKENS_TECHNIQUE` | `512` | Batas token keluaran agen Technique |
| `LOCAL_LLM_MAX_TOKENS_REVIEWER` | `512` | Batas token keluaran agen Reviewer |
| `LLM_REVISE_TEMPERATURE` | `0.4` | Suhu *sampling* pada iterasi revisi |

**Retrieval (RAG)**

| Parameter | Bawaan | Keterangan |
|---|---|---|
| `TECHNIQUE_CANDIDATE_TOP_K` | `50` | Jumlah kandidat teknik yang diambil per laporan |
| `TECHNIQUE_ACCEPT_TOP_N` | `30` | Batas jumlah teknik yang diterima agen |
| `TECHNIQUE_INCLUDE_SUBTECHNIQUES` | `true` | Menyertakan sub-teknik sebagai kandidat |
| `RETRIEVAL_PER_CHUNK` | `true` | *Retrieval* dijalankan per *chunk*, bukan per laporan utuh |
| `RETRIEVAL_NAME_BOOST` | `true` | Menjamin teknik yang namanya disebut verbatim masuk kandidat |
| `RETRIEVAL_EXCLUDE_PRECOMPROMISE` | `true` | Membuang teknik *reconnaissance*/*resource-development*; setel `false` bila dataset melabeli fase pra-kompromi |
| `RETRIEVAL_EMBEDDING_HYBRID` | `true` | Mengaktifkan skor gabungan TF-IDF + *embedding* |
| `RETRIEVAL_EMBEDDING_MODEL` | `text-embedding-nomic-embed-text-v1.5` | Model *embedding* pada LM Studio |
| `RETRIEVAL_EMBEDDING_ALPHA` | `0.5` | Bobot *embedding* terhadap TF-IDF |
| `RETRIEVAL_EMBEDDING_WINDOW_CHARS` | `700` | Lebar jendela teks untuk *embedding* |
| `RETRIEVAL_EMBEDDING_WINDOW_STEP` | `600` | Langkah geser jendela |
| `RETRIEVAL_MAX_CHARS` | `20000` | Panjang maksimum teks laporan untuk TF-IDF |
| `RETRIEVAL_DOC_DESC_CHARS` | `1000` | Panjang deskripsi teknik pada dokumen TF-IDF |
| `ATTCK_DESC_MAX_CHARS` | `1000` | Pemotongan deskripsi saat memuat basis pengetahuan |

**Pemecahan Laporan (*Chunking*)**

| Parameter | Bawaan | Keterangan |
|---|---|---|
| `LOCAL_LLM_REPORT_MAX_CHARS` | lihat catatan | Ukuran satu *chunk* laporan |
| `LLM_CHUNK_OVERLAP_CHARS` | `250` | Tumpang tindih antar *chunk* |
| `LLM_MAX_CHUNKS` | `3` | Jumlah maksimum *chunk* per laporan |

> **Catatan penting mengenai `LOCAL_LLM_REPORT_MAX_CHARS`.** Parameter ini dibaca
> oleh **tiga** modul dengan **nilai bawaan yang berbeda-beda**:
> `3500` pada `src/agents/retrieval.py` (ukuran *chunk* technique agent),
> `6000` pada `src/agents/tactic_agent.py` (panjang kutipan laporan), dan
> `2000` pada `src/agents/reviewer_agent.py`. Artinya, bila parameter ini
> **tidak** disetel pada `.env`, ketiga agen bekerja pada panjang teks yang
> berlainan. Begitu disetel, satu nilai berlaku untuk ketiganya sekaligus —
> mengubahnya mengubah tiga hal serentak. Seluruh preset pada `experiments/`
> menyetelnya eksplisit (`8000`) justru untuk menghilangkan ambiguitas ini,
> dan disarankan Anda melakukan hal yang sama.

**Reviewer dan Rekonsiliasi**

| Parameter | Bawaan | Keterangan |
|---|---|---|
| `REVIEWER_ENABLE` | `true` | Mengaktifkan *loop* debat *multi-agent* pada mode CLI; set `false` untuk ablasi |
| `WEB_UI_ENABLE_REVIEWER` | `true` | Mengaktifkan Reviewer pada antarmuka web; set `false` untuk ablasi |
| `LLM_REVIEW_MAX_ITER` | `2` | Batas iterasi revisi |
| `RECONCILE_TACTIC_FILTER` | `false` | Menyaring teknik yang taktiknya tidak teridentifikasi |
| `RECONCILE_SUBTECH_FAMILY_CAP` | `2` | Batas sub-teknik per keluarga teknik |

**Lokasi Data**

| Parameter | Bawaan | Keterangan |
|---|---|---|
| `ATTCK_SOURCE` | `data/mitre_cti/enterprise-attack.json` | Berkas STIX ATT&CK Enterprise |
| `TRAM_DATA_DIR` | `data/tram_ii` | Direktori dataset laporan |
| `EVAL_N` | `5` | Jumlah laporan pada mode evaluasi *live* |
| `DEBUG_AGENT` | `false` | Mencetak *prompt* dan respons mentah agen |

---

## E. Penyiapan Data

### E.1 Basis Pengetahuan MITRE ATT&CK

Unduh berkas STIX ATT&CK Enterprise dan simpan dengan nama **persis** `enterprise-attack.json`:

```bash
curl -o data/mitre_cti/enterprise-attack.json \
  https://raw.githubusercontent.com/mitre/cti/master/enterprise-attack/enterprise-attack.json
```

Sumber alternatif: repositori [mitre-attack/attack-stix-data](https://github.com/mitre-attack/attack-stix-data).

Pemuat basis pengetahuan menyaring objek dengan atribut `revoked` maupun `x_mitre_deprecated` sehingga identitas teknik usang tidak masuk ke ruang prediksi. Hanya matriks **Enterprise** yang digunakan agar identitas taktik Mobile dan PRE tidak tercampur.

### E.2 Dataset Laporan CTI

Letakkan berkas laporan pada `data/tram_ii/`. Tiga format didukung:

| Format | Keterangan | Letak label acuan |
|---|---|---|
| `.mjson` | **Format yang digunakan pada penelitian ini** (151 berkas) | `asets[].type` |
| `.json` | Varian TRAM II berbasis kalimat | `sentences[].mappings[].attack_id` |
| `.pdf` | Laporan mentah tanpa label; teks diekstraksi otomatis | — |

**Format `.mjson`.** Anotasi tersimpan pada larik `asets`, dengan identitas teknik tertulis di dalam tanda kurung pada atribut `type`:

```json
{
  "signal": "<teks lengkap laporan>",
  "metadata": { },
  "asets": [
    { "type": "zone",  "annots": [[0, 14541, "body"]] },
    { "type": "lex",   "annots": [] },
    { "type": "File and Directory Discovery (T1083)",  "annots": [] },
    { "type": "Embedded Payloads (T1027.009)",         "annots": [] },
    { "type": "DLL Side-Loading (T1574.002)",          "annots": [] }
  ]
}
```

Teks laporan diambil dari atribut `signal`, sedangkan identitas teknik diekstraksi dengan ekspresi reguler `\((T\d{4}(?:\.\d{3})?)\)`. Entri `asets` bertipe struktural seperti `zone`, `lex`, dan `SEGMENT` tidak menghasilkan label karena tidak memuat pola tersebut.

**Format `.json`.** Anotasi tersimpan pada tingkat kalimat:

```json
{
  "sentences": [
    {
      "text": "The attackers used phishing emails with malicious attachments.",
      "mappings": [ { "attack_id": "T1566.001" } ]
    },
    {
      "text": "Malware was executed through PowerShell commands.",
      "mappings": [ { "attack_id": "T1059.001" } ]
    }
  ]
}
```

Pada kedua format, identitas teknik dikumpulkan dan dihilangkan duplikatnya **pada tingkat dokumen**, bukan tingkat kalimat. Dengan demikian satuan evaluasi berupa himpunan teknik yang muncul dalam satu laporan. Berkas `.pdf` tanpa label tetap dapat diproses, namun tidak dapat dievaluasi.

Konversi PDF ke JSON secara manual (opsional, sebab pemuat melakukannya otomatis):

```bash
python src/knowledge/pdf_to_json_converter.py --input-dir data/tram_ii --output-dir data/tram_ii
```

Tambahkan `--recursive` untuk memindai subdirektori.

### E.3 Penentuan Label Acuan (*Ground Truth*)

Bagian ini menjelaskan asal-usul label acuan yang dipakai pada seluruh perhitungan metrik.

**Label acuan teknik** diekstraksi oleh fungsi `_extract_techniques_from_report()` pada `src/knowledge/data_loader.py`, sesuai format berkas sebagaimana diuraikan pada Bagian E.2. Label tersebut disalin apa adanya ke keluaran pipeline melalui atribut `ground_truth` pada `src/pipeline/orchestrator.py`. Label acuan **tidak pernah disertakan dalam *prompt* yang dikirim ke LLM**, sehingga tidak terjadi kebocoran label terhadap agen.

**Label acuan taktik tidak tersedia pada dataset.** Anotasi TRAM II hanya memuat label teknik. Label acuan taktik karenanya **diturunkan secara deterministik** dari label teknik oleh fungsi `derive_tactic_ground_truth()` pada `src/evaluation/evaluator.py`, melalui tiga langkah:

1. Setiap identitas teknik acuan dicari pada basis pengetahuan ATT&CK. Sub-teknik yang tidak ditemukan dicari melalui teknik induknya (mekanisme *fallback*).
2. Atribut `kill_chain_phases` teknik tersebut diambil, terbatas pada entri dengan `kill_chain_name` bernilai `mitre-attack` (lihat `src/knowledge/attck_loader.py`).
3. Nama fase dipetakan ke identitas taktik melalui tabel `PHASE_TO_TACTIC_ID` yang memuat empat belas pasangan, misalnya `defense-evasion` menjadi `TA0005`.

Sebagai ilustrasi, label acuan `T1027.009` (*Embedded Payloads*) berfase `defense-evasion` sehingga laporan yang memuatnya memperoleh label acuan taktik `TA0005`.

**Karakteristik label acuan dataset penelitian.** Verifikasi atas 151 laporan menghasilkan angka berikut:

| Besaran | Nilai |
|---|---|
| Jumlah laporan | 151 |
| Total label acuan teknik | 1.943 |
| Teknik unik pada label acuan | 77 (51 sub-teknik, 26 teknik induk) |
| Rata-rata label acuan per laporan | 12,87 |
| Jumlah teknik pada basis pengetahuan | 607 |
| Label acuan yang tidak dikenali basis pengetahuan | 0 |
| Sub-teknik yang memerlukan *fallback* ke induk | 0 |
| Taktik yang muncul pada label acuan turunan | 11 dari 14 |

Tidak adanya label yang gagal dikenali menunjukkan bahwa seluruh identitas teknik pada dataset masih berlaku pada versi ATT&CK yang digunakan, sehingga penurunan label taktik berlangsung tanpa kehilangan informasi.

Tiga taktik — *Reconnaissance* (TA0043), *Resource Development* (TA0042), dan *Impact* (TA0040) — tidak pernah muncul pada label acuan. Kondisi ini mendasari dua keputusan perancangan: pengecualian teknik pra-kompromi dari kandidat *retrieval* melalui `RETRIEVAL_EXCLUDE_PRECOMPROMISE`, serta pengecualian ketiga taktik tersebut dari perhitungan rerata makro pada grafik evaluasi per-taktik.

Perlu dicatat bahwa satu teknik dapat memiliki lebih dari satu taktik. Sebagai contoh, `T1574.002` (*DLL Side-Loading*) tercatat pada fase *persistence*, *privilege-escalation*, dan *defense-evasion* sekaligus. Akibatnya, label acuan taktik hasil penurunan cenderung lebih luas dibandingkan anotasi taktik yang dibuat manual oleh analis. Hal ini merupakan batasan metodologis yang perlu dipertimbangkan saat menafsirkan metrik tingkat taktik.

---

## F. Pengoperasian Artefak

Artefak menyediakan empat cara pengoperasian sesuai kebutuhan pengguna.

| Cara | Perintah | Kegunaan |
|---|---|---|
| Antarmuka web — konsol | `uvicorn` lalu `/app` | Analisis satu laporan disertai validasi analis |
| Antarmuka web — batch | `uvicorn` lalu `/batch` | Evaluasi banyak laporan dengan pemantauan langsung |
| CLI — subset | `python main.py [N]` | Uji cepat sejumlah laporan |
| CLI — dataset penuh | `python scripts/run_full_pipeline.py` | Menjalankan seluruh dataset |

### F.1 Menjalankan Peladen Web

```bash
python -m uvicorn web.web_app:app --app-dir src --host 127.0.0.1 --port 8000
```

Tambahkan `--reload` selama pengembangan. Setelah peladen aktif, tersedia tiga halaman:

| Alamat | Halaman |
|---|---|
| `http://127.0.0.1:8000/` | Beranda dan penjelasan sistem |
| `http://127.0.0.1:8000/app` | Konsol analisis satu laporan |
| `http://127.0.0.1:8000/batch` | Konsol evaluasi *batch* |

Basis pengetahuan ATT&CK dimuat sekali saat permintaan pertama, sehingga pemrosesan pertama memerlukan waktu lebih lama.

### F.2 Analisis Satu Laporan (Konsol Web)

Prosedur:

1. Buka `http://127.0.0.1:8000/app`.
2. Masukkan laporan melalui salah satu cara: menempelkan teks pada kotak masukan, atau mengunggah berkas `.json`/`.mjson`/`.pdf`/`.txt`.
3. Isi identitas laporan (opsional) pada kolom *Report ID*.
4. Tentukan apakah agen Reviewer diaktifkan. Reviewer menambah panggilan LLM sehingga memperpanjang waktu proses.
5. Tekan tombol proses. Kartu agen menampilkan status berjalan setiap tahap: Tactic → Technique → Reviewer → Reconciler → Validator.
6. Tinjau hasil. Setiap taktik dan teknik disertai kalimat bukti (*evidence*) yang dikutip dari laporan asal.
7. Lakukan validasi dengan menerima atau menolak tiap butir hasil. Butir yang diterima menjadi dasar bundel STIX akhir.
8. Unduh keluaran: bundel STIX 2.1 (JSON) dan laporan PDF.

Tahap ke-7 mencerminkan posisi artefak sebagai **asisten semi-otomatis**: keputusan akhir tetap berada pada analis, dan kalimat bukti disediakan agar setiap pemetaan dapat diverifikasi.

### F.3 Evaluasi Batch (Konsol Web)

Prosedur:

1. Buka `http://127.0.0.1:8000/batch`.
2. Tentukan jumlah laporan yang diproses (angka tertentu atau `all` untuk seluruh dataset).
3. Tentukan status agen Reviewer.
4. Jalankan proses. Halaman menampilkan kemajuan per laporan, identitas laporan yang sedang diproses, dan metrik berjalan.
5. Proses dapat dihentikan melalui tombol pembatalan; laporan yang sedang diproses diselesaikan lebih dahulu, dan hasil yang telah selesai tetap tersimpan.
6. Setelah selesai, hasil tersimpan otomatis pada `results/predictions/results_web_<timestamp>.json`.

Hanya satu proses *batch* yang dapat berjalan pada satu waktu; permintaan kedua ditolak dengan kode galat HTTP 409.

### F.4 Antarmuka Baris Perintah

Memproses sejumlah laporan:

```bash
python main.py 20        # 20 laporan pertama
python main.py all       # seluruh dataset
python main.py           # mode interaktif, bawaan 5 laporan
```

Program melakukan validasi prasyarat terlebih dahulu (ketersediaan dataset, berkas ATT&CK, dan `LOCAL_LLM_BASE_URL`) dan menghentikan proses disertai pesan perbaikan bila prasyarat belum terpenuhi.

Hasil disimpan secara bertahap setiap 5 laporan pada `results/predictions/results_main_<timestamp>.json` agar proses panjang yang terputus tidak kehilangan seluruh capaian. Penghentian melalui `Ctrl+C` tetap menghasilkan evaluasi atas laporan yang telah selesai.

Menjalankan seluruh dataset tanpa interaksi:

```bash
python scripts/run_full_pipeline.py
```

Keluaran disimpan pada `results/predictions/results_all_<timestamp>.json`.

Mengaktifkan Reviewer pada mode CLI dilakukan melalui variabel lingkungan:

```bash
# Windows PowerShell
$env:REVIEWER_ENABLE="true"; python main.py all

# Linux/macOS
REVIEWER_ENABLE=true python main.py all
```

---

## G. Format Masukan dan Keluaran

### G.1 Masukan

| Jalur | Format yang diterima |
|---|---|
| Konsol web | Teks bebas, `.txt`, `.json`, `.mjson`, `.pdf` |
| Batch dan CLI | Seluruh berkas pada `data/tram_ii/` |

### G.2 Berkas Hasil Prediksi

Berkas JSON berisi larik objek, satu objek per laporan:

```json
[
  {
    "report_id": "3CXDesktopApp Backdoored in a Suspected Lazarus Campaign",
    "predicted_techniques": ["T1566.001", "T1059.001", "T1071.001"],
    "ground_truth": ["T1566.001", "T1059.003", "T1071.001"],
    "tactics_identified": ["TA0001", "TA0002", "TA0011"],
    "stix_bundle": { "type": "bundle", "objects": [] },
    "reviewer_error_count": 0,
    "reviewer_errored": false
  }
]
```

Penjelasan atribut:

| Atribut | Keterangan |
|---|---|
| `report_id` | Identitas laporan |
| `predicted_techniques` | Identitas teknik ATT&CK hasil prediksi sistem |
| `ground_truth` | Identitas teknik acuan dari anotasi dataset |
| `tactics_identified` | Taktik yang dilaporkan langsung oleh agen Tactic |
| `stix_bundle` | Bundel STIX 2.1 hasil pemetaan |
| `reviewer_error_count` | Jumlah galat teknis pada agen Reviewer |
| `reviewer_errored` | Penanda terjadinya galat teknis Reviewer |

Dua atribut terakhir memisahkan **galat teknis** Reviewer (misalnya *timeout*) dari **penolakan substantif**, sehingga kegagalan teknis tidak disalahartikan sebagai keputusan analitis.

### G.3 Bundel STIX 2.1

Keluaran STIX menggunakan objek `attack-pattern` beserta relasinya, sehingga dapat diimpor ke platform *threat intelligence* seperti MISP atau OpenCTI. Bundel dapat diunduh melalui konsol web atau diambil dari atribut `stix_bundle` pada berkas hasil prediksi.

### G.4 Laporan PDF

Laporan PDF memuat identitas laporan, daftar taktik dan teknik hasil pemetaan, kalimat bukti untuk setiap butir, serta ringkasan bundel STIX. Apabila analis telah melakukan validasi, laporan disusun berdasarkan butir yang diterima; bila belum, laporan menggunakan hasil mentah pipeline.

---

## H. Reproduksi Evaluasi

Bagian ini memungkinkan pembaca memverifikasi kembali angka yang dilaporkan pada Bab IV.

### H.1 Menghitung Metrik

Dari berkas hasil yang telah ada:

```bash
python src/evaluation/evaluate_run.py results/predictions/results_web_20260711_223544.json
```

Menjalankan pipeline secara langsung lalu mengevaluasinya:

```bash
# Windows PowerShell
$env:EVAL_N="5"; python src/evaluation/evaluate_run.py

# Linux/macOS
EVAL_N=5 python src/evaluation/evaluate_run.py
```

Keluaran mencetak tiga kelompok metrik:

1. **Teknik (*exact*)** — kecocokan hingga tingkat sub-teknik, misalnya `T1566.001`;
2. **Teknik (*base*)** — kecocokan pada tingkat teknik induk, dengan mengabaikan sub-teknik;
3. **Taktik** — acuan taktik diturunkan dari teknik *ground truth* melalui `kill_chain_phases` pada basis pengetahuan ATT&CK.

### H.2 Membangun Laporan Excel

```bash
python src/evaluation/build_excel_report.py results/predictions/<berkas>.json
```

Tanpa argumen, skrip memakai berkas terbaru pada `results/predictions/`. Keluaran tersimpan pada `results/metrics/laporan_ttp_<timestamp>.xlsx`, berisi lembar ringkasan metrik dan rincian per laporan.

### H.3 Membangkitkan Grafik Evaluasi

> ⚠️ **Skrip pembangkit grafik tidak disertakan pada repositori publik.** Direktori
> `evaluation_charts/` dikecualikan melalui `.gitignore` karena keluarannya
> merupakan materi penulisan Tugas Akhir. Bagian ini didokumentasikan sebagai
> rekaman prosedur yang ditempuh, bukan sebagai langkah yang dapat dijalankan
> langsung dari salinan repositori. Seluruh angka yang mendasari grafik tetap
> dapat dihitung ulang melalui Bagian H.1, H.2, dan H.4.

```bash
python evaluation_charts/generate_charts.py results/predictions/<berkas>.json [direktori_keluaran]
```

Skrip menghasilkan enam belas grafik pada direktori keluaran (bawaan: `evaluation_charts/`):

| Berkas | Isi |
|---|---|
| `01_overall_metrics.png` | Metrik mikro dan makro, tingkat *exact* dan induk |
| `02_f1_distribution.png` | Sebaran F1 per laporan |
| `03_metric_boxplot.png` | Diagram kotak P/R/F1 per laporan |
| `04_pred_vs_gt_counts.png` | Analisis prediksi berlebih |
| `05_top_false_positives.png` | Lima belas *false positive* terbanyak |
| `06_top_false_negatives.png` | Lima belas *false negative* terbanyak |
| `07_top_true_positives.png` | Lima belas *true positive* terbanyak |
| `08_best_worst_reports.png` | Sepuluh laporan terbaik dan terburuk |
| `09_tactics_distribution.png` | Sebaran taktik yang teridentifikasi |
| `10_granularity_effect.png` | Efek granularitas sub-teknik terhadap induk |
| `11_f1_cumulative.png` | Distribusi kumulatif F1 |
| `12_summary_dashboard.png` | Papan ringkasan |
| `13_tactic_confusion_matrix.png` | Matriks konfusi taktik terhimpun |
| `14_tactic_confusion_per_tactic.png` | Rincian konfusi per taktik |
| `15_tactic_f1_per_tactic.png` | Presisi, *recall*, dan F1 per taktik |
| `16_tactic_f1_summary.png` | F1 menurut tingkat abstraksi |

Grafik 13 sampai 16 memerlukan berkas `enterprise-attack.json`; bila tidak ditemukan, keempatnya dilewati disertai peringatan.

Perlu dicatat bahwa matriks konfusi hanya terdefinisi secara utuh pada **tingkat taktik**, sebab taktik merupakan himpunan tertutup berisi empat belas label sehingga *true negative* bermakna. Pada tingkat teknik, ruang label bersifat terbuka sehingga *true negative* tidak terdefinisi.

### H.4 Membandingkan dengan Baseline

```bash
python scripts/eval_baselines.py
```

Skrip menghitung dua pembanding secara *offline* dan deterministik: baseline TF-IDF murni tanpa LLM pada beberapa nilai N, serta baseline mayoritas sebagai batas bawah.

### H.5 Skrip Analisis Pendukung

| Skrip | Kegunaan |
|---|---|
| `scripts/compare_results.py` | Membandingkan dua berkas hasil run |
| `scripts/verify_results.py` | Pemeriksaan kewajaran berkas hasil |
| `scripts/audit_dataset.py` | Audit kualitas dataset |
| `scripts/fn_fp_analysis.py` | Analisis rinci *false negative* dan *false positive* |
| `scripts/retrieval_ceiling.py` | Mengukur plafon *recall* tahap *retrieval* |
| `scripts/smoke_reviewer.py` | Uji cepat fungsi agen Reviewer |

`scripts/retrieval_ceiling.py` berguna untuk menegaskan temuan bahwa tahap *retrieval* merupakan *bottleneck* utama: teknik yang tidak masuk daftar kandidat mustahil dipilih oleh LLM, berapa pun kualitas penalarannya.

### H.6 Reproduksi Eksperimen Ablasi

Direktori `experiments/` memuat berkas konfigurasi (*preset*) yang dirancang agar
setiap perlakuan berbeda dari induknya pada **tepat satu baris**, sehingga efek
yang terukur dapat diatribusikan kepada satu variabel saja.

| Preset | Beda dari induknya | Yang diuji |
|---|---|---|
| `A` | — (baseline replikasi) | titik acuan |
| `E` | dari A: `LLM_MAX_CHUNKS` 3 → 10 | jangkauan pembacaan laporan |
| `F` | dari E: `REVIEWER_ENABLE` false → true | kontribusi agen Reviewer |
| `G` | dari A: `TECHNIQUE_ACCEPT_TOP_N` 30 → 0 | efek penyaringan pilihan LLM |
| `H` | dari G: `CANDIDATE_SHUFFLE_SEED` diisi | ketergantungan pada urutan kandidat |

Preset `B`, `C`, dan `D` disimpan sebagai **kontrol negatif terdokumentasi** dan
sengaja tidak dijalankan; alasan pembatalannya tercantum pada berkasnya
masing-masing.

Menjalankan satu preset:

```bash
python scripts/run_experiment.py --preset E --reports 30
```

Menyusun tabel perbandingan antar preset:

```bash
python scripts/compare_experiments.py results/predictions/exp_*.json \
    --out experiments/tabel_perbandingan.md
```

Menggabungkan potongan run yang terputus di tengah:

```bash
python scripts/merge_partial_runs.py <potongan-1>.json <potongan-2>.json \
    --out results/predictions/exp_<preset>_GABUNGAN_30.json
```

Penggabungan hanya sah bila seluruh variabel yang memengaruhi prediksi terverifikasi
identik antar potongan; skrip memeriksanya dan menolak bila berbeda. Metrik
dihitung **ulang** dari hasil gabungan, bukan dirata-ratakan.

**Berkas manifest.** Setiap run menghasilkan `<hasil>.json` beserta
`<hasil>.json.manifest.json` yang merekam konfigurasi efektif, *commit* git,
checksum SHA-256 basis pengetahuan ATT&CK, statistik token *prompt*, indikator
keselarasan dengan peringkat *retrieval*, serta metrik akhir. Manifest ditulis
pada setiap *checkpoint*, sehingga run yang terhenti tetap meninggalkan rekaman —
periksa atribut `status` yang bernilai `complete`, `partial`, atau `aborted`.
Berkas bertanda `"do_not_use_for_metrics": true` tidak boleh dipakai sebagai hasil.

Temuan lengkap beserta seluruh keterbatasannya terdapat pada
`experiments/HASIL_EKSPERIMEN.md`.

---

## I. Antarmuka Pemrograman (API)

Peladen FastAPI menyediakan API berikut untuk integrasi dengan sistem lain.

| Metode | Alamat | Fungsi |
|---|---|---|
| `POST` | `/api/process` | Mengirim laporan; mengembalikan `job_id` |
| `GET` | `/api/status/{job_id}` | Status pekerjaan dan catatan aktivitas agen |
| `GET` | `/api/results/{job_id}` | Hasil pemetaan mentah |
| `POST` | `/api/validate/{job_id}` | Mengirim keputusan validasi analis |
| `GET` | `/api/final/{job_id}` | Hasil akhir setelah validasi |
| `GET` | `/api/report/{job_id}.pdf` | Mengunduh laporan PDF |
| `GET` | `/api/batch/info` | Informasi dataset dan status *batch* |
| `POST` | `/api/batch/start` | Memulai evaluasi *batch* |
| `GET` | `/api/batch/status` | Kemajuan *batch* terkini |
| `POST` | `/api/batch/cancel` | Menghentikan *batch* yang berjalan |

Contoh penggunaan:

```bash
# 1. Mengirim laporan
curl -X POST http://127.0.0.1:8000/api/process \
  -F "report_text=The attackers used spearphishing attachments to gain access." \
  -F "report_id=contoh-001" \
  -F "use_reviewer=false"
# Respons: {"job_id":"..."}

# 2. Memeriksa status
curl http://127.0.0.1:8000/api/status/<job_id>

# 3. Mengambil hasil setelah status bernilai "done"
curl http://127.0.0.1:8000/api/results/<job_id>
```

Pemrosesan berlangsung asinkron pada utas tersendiri; klien perlu melakukan *polling* terhadap `/api/status/{job_id}` hingga status bernilai `done` atau `error`.

Dokumentasi API interaktif tersedia pada `http://127.0.0.1:8000/docs` (Swagger UI bawaan FastAPI).

---

## J. Penanganan Galat

| Gejala | Penyebab | Penanganan |
|---|---|---|
| `Connection refused` saat memproses | Peladen LM Studio belum aktif atau alamat keliru | Aktifkan peladen; periksa `LOCAL_LLM_BASE_URL`; uji dengan `curl <url>/v1/models` |
| Taktik selalu kosong pada hasil | Model *thinking* menutupi keluaran JSON | Setel `LLM_DISABLE_THINKING=true` |
| Peringatan *embedding* gagal, *retrieval* kembali ke TF-IDF | Model *embedding* belum dimuat pada LM Studio | Muat `text-embedding-nomic-embed-text-v1.5`, atau setel `RETRIEVAL_EMBEDDING_HYBRID=false` bila memang dikehendaki |
| `[SETUP] Dataset TRAM II belum ada` | Direktori `data/tram_ii/` kosong | Letakkan berkas laporan sesuai Bagian E.2 |
| `[SETUP] File MITRE CTI belum ada` | `enterprise-attack.json` tidak ditemukan | Unduh sesuai Bagian E.1 |
| Proses sangat lambat | Inferensi berjalan pada CPU, atau Reviewer aktif | Aktifkan akselerasi GPU pada LM Studio; nonaktifkan Reviewer; kurangi `LLM_MAX_CHUNKS` |
| *Timeout* pada panggilan LLM | Model terlalu besar untuk perangkat keras | Naikkan `LLM_REQUEST_TIMEOUT_SECONDS`; gunakan model berkuantisasi lebih rendah |
| `UnicodeDecodeError` saat membaca berkas hasil | Berkas dibaca tanpa penyandian UTF-8 | Gunakan skrip bawaan proyek yang telah menetapkan `encoding="utf-8"` |
| HTTP 409 saat memulai *batch* | Proses *batch* lain sedang berjalan | Tunggu hingga selesai, atau hentikan melalui `/api/batch/cancel` |
| `ModuleNotFoundError: openpyxl` / `matplotlib` | Dependensi pelaporan belum terpasang | Jalankan `pip install openpyxl matplotlib` |

Untuk penelusuran lebih dalam, setel `DEBUG_AGENT=true` agar *prompt* dan respons mentah setiap agen tercetak pada konsol.

---

## K. Catatan Reproduksibilitas dan Batasan

### K.1 Faktor yang Memengaruhi Reproduksibilitas

Meskipun seluruh komponen bersifat lokal dan sumber terbuka, hasil dapat berbeda antarjalannya karena beberapa faktor berikut:

1. **Sifat stokastik LLM.** Inferensi tidak sepenuhnya deterministik meskipun suhu *sampling* rendah; perbedaan kecil pada daftar teknik keluaran merupakan hal yang wajar.
2. **Versi basis pengetahuan ATT&CK.** MITRE memutakhirkan ATT&CK secara berkala. Penambahan, penggabungan, dan pencabutan teknik mengubah ruang label sehingga metrik ikut bergeser. Versi berkas `enterprise-attack.json` yang digunakan sebaiknya dicatat.
3. **Versi model.** Penggantian model generatif atau model *embedding* mengubah hasil secara signifikan.
4. **Nilai parameter *retrieval*.** Parameter seperti `TECHNIQUE_CANDIDATE_TOP_K` dan `RETRIEVAL_EMBEDDING_ALPHA` berpengaruh langsung terhadap plafon *recall*.

Karena itu, berkas hasil prediksi pada `results/predictions/` disertakan agar metrik dan grafik yang dilaporkan dapat direproduksi secara persis tanpa perlu menjalankan ulang pipeline.

### K.2 Batasan Artefak

Batasan berikut disampaikan secara terbuka sesuai tuntutan komunikasi ilmiah yang objektif:

1. **Keunggulan atas baseline bersifat terukur namun terbatas populasinya.** Pada perbandingan yang menyamakan anggaran jumlah prediksi (*budget-matched*) dan memakai *retrieval* hibrida yang sama, sistem mengungguli baseline pada *precision* maupun *recall* sekaligus — bukan pertukaran antar keduanya. Pada preset E selisih F1 tingkat teknik induk mencapai **+0,1169** (0,4341 lawan 0,3172) dan pada tingkat *exact* **+0,0959**. Dua hal perlu dicatat secara jujur: perbandingan ini dihitung pada **subset 30 laporan**, bukan 151; dan pembanding yang sah hanyalah baseline hibrida *budget-matched*, sebab baseline TF-IDF murni menonaktifkan komponen *embedding* sehingga selisih terhadapnya memuat dua perubahan sekaligus. Rincian dan berkas pendukungnya ada pada `experiments/HASIL_EKSPERIMEN.md` §7.4.1.

   Perlu ditegaskan bahwa baseline *majority-oracle* yang mencapai *recall* 1,000 merupakan **batas atas trivial**, bukan pesaing yang wajar: baseline tersebut memanfaatkan pengetahuan atas kunci jawaban. Pada anggaran prediksi yang sama, *precision* sistem justru lebih tinggi daripada oracle tersebut.
2. ***Retrieval* sebagai *bottleneck* utama.** Teknik yang tidak masuk daftar kandidat mustahil terpilih. Plafon *recall* tahap *retrieval* membatasi kinerja seluruh pipeline.
3. **Cakupan dataset.** Anotasi TRAM II tidak memuat label untuk taktik *Reconnaissance*, *Resource Development*, dan *Impact*. Ketiga taktik tersebut karenanya tidak dapat dievaluasi, dan prediksi pada ketiganya selalu terhitung sebagai *false positive*.
4. **Ketergantungan pada anotasi acuan.** Anotasi TRAM II bersifat tidak lengkap. Sebagian *false positive* sistem dapat berupa pemetaan yang sebenarnya benar tetapi tidak teranotasi.
5. **Posisi sebagai asisten, bukan pengganti analis.** Artefak dirancang sebagai asisten semi-otomatis. Validasi analis merupakan bagian dari alur kerja, bukan langkah opsional.

---

## L. Rujukan Dokumentasi Lain

| Dokumen | Isi |
|---|---|
| `README.md` | Ringkasan proyek dan mulai cepat |
| `docs/SETUP_GUIDE.md` | Panduan pemasangan dan penelusuran galat |
| `docs/ARSITEKTUR_DAN_FLOWCHART.md` | Arsitektur sistem dan diagram alir |
| `docs/AGENT.md` | Catatan konfigurasi agen |
| `experiments/HASIL_EKSPERIMEN.md` | Hasil ablasi preset A–H beserta keterbatasannya |
| `experiments/RANCANGAN_PRESET.md` | Rancangan tiap preset dan alasannya |

Naskah Tugas Akhir tidak disertakan pada repositori ini; repositori memuat artefak
sistem beserta bukti eksperimennya.

---

## M. Keamanan dan Privasi Data

Seluruh pemrosesan berlangsung pada perangkat lokal. Laporan CTI tidak dikirimkan ke layanan pihak ketiga, sehingga artefak dapat digunakan pada laporan berklasifikasi maupun laporan internal organisasi. Sifat ini sekaligus menjadi salah satu alasan pemilihan LLM lokal sumber terbuka dibandingkan layanan LLM komersial.

Berkas `.env` memuat konfigurasi lokal dan telah dikecualikan melalui `.gitignore`. Kredensial tidak boleh ditulis langsung pada kode sumber.
