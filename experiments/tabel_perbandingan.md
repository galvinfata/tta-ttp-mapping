### Perbandingan preset eksperimen

| Preset | Laporan | Coverage median | Pred/laporan | Median peringkat | % di luar top-30 | P exact | R exact | F1 exact | P base | R base | F1 base | Retrieval-miss | Reasoning-miss | Durasi (mnt) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A_baseline_replikasi | 30 | 0.980 | 19.8 | 11 | 0.0% | 0.2445 | 0.3571 | 0.2903 | 0.3429 | 0.4548 | 0.3910 | 142 | 119 | 90.5 |
| G_tanpa_filter | 30 | 0.980 | 26.9 | 17 | 26.7% | 0.2094 | 0.4163 | 0.2786 | 0.3010 | 0.5248 | 0.3826 | 142 | 95 | 91.1 |
| H_acak_kandidat | 30 | 0.980 | 26.6 | 18.0 | 27.2% | 0.1980 | 0.3892 | 0.2625 | 0.3081 | 0.5102 | 0.3842 | 142 | 106 | 95.2 |
| E_jangkauan_penuh | 30 | 1.000 | 26.0 | 10 | 0.0% | 0.2330 | 0.4483 | 0.3067 | 0.3448 | 0.5860 | 0.4341 | 99 | 125 | 141.3 |
| F_jangkauan_reviewer | 30 | 1.000 | 26.6 | 10 | 0.0% | 0.2265 | 0.4458 | 0.3004 | 0.3344 | 0.5831 | 0.4251 | 99 | 126 | 161.2 |

### Keselarasan dengan peringkat retrieval

| Preset | ACCEPT_TOP_N | Seed acak | Prediksi berperingkat | Median | Rata-rata | 1–10 | 11–20 | 21–30 | 31+ | Dibuang filter | Median peringkat yang dibuang |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A_baseline_replikasi | 30 | - | 569 | 11 | 12.70 | 48.0% | 26.9% | 25.1% | 0.0% | 250 | 40.0 |
| G_tanpa_filter | 0 | - | 767 | 17 | 20.03 | 35.5% | 19.6% | 18.2% | 26.7% | 0 | - |
| H_acak_kandidat | 0 | 42 | 754 | 18.0 | 20.40 | 31.8% | 22.8% | 18.2% | 27.2% | 0 | - |
| E_jangkauan_penuh | 30 | - | 749 | 10 | 11.98 | 51.9% | 27.4% | 20.7% | 0.0% | 320 | 40.0 |
| F_jangkauan_reviewer | 30 | - | 767 | 10 | 12.10 | 51.1% | 27.9% | 21.0% | 0.0% | 322 | 40.0 |

### Konsumsi context window & Reviewer

| Preset | n_ctx | Panggilan LLM | Token prompt rata-rata | Token prompt maks | Panggilan melampaui n_ctx | Kandidat dibuang | Reviewer aktif | Laporan memicu revisi | Retrieval hibrida |
|---|---:|---:|---:|---:|---:|---:|---|---:|---|
| A_baseline_replikasi | 8192 | 111 | 4195 | 5018 | 0 / 111 (0%) | 0 | tidak | 0 | utuh |
| G_tanpa_filter | - | - | - | - | - | - | tidak | 0 | utuh |
| H_acak_kandidat | - | - | - | - | - | - | tidak | 0 | utuh |
| E_jangkauan_penuh | - | - | - | - | - | - | tidak | 0 | utuh |
| F_jangkauan_reviewer | - | - | - | - | - | - | ya | 4 | utuh |

Sumber berkas:

- **A_baseline_replikasi** — `exp_A_baseline_replikasi_20260806_111339.json`
- **G_tanpa_filter** — `exp_G_tanpa_filter_GABUNGAN_30.json`  ⚠️ _run berstatus **complete_merged** — 30 laporan, tidak setara dengan run utuh_
- **H_acak_kandidat** — `exp_H_acak_kandidat_GABUNGAN_30.json`  ⚠️ _run berstatus **complete_merged** — 30 laporan, tidak setara dengan run utuh_
- **E_jangkauan_penuh** — `exp_E_jangkauan_penuh_GABUNGAN_30.json`  ⚠️ _run berstatus **complete_merged** — 30 laporan, tidak setara dengan run utuh_
- **F_jangkauan_reviewer** — `exp_F_jangkauan_reviewer_GABUNGAN_30.json`  ⚠️ _run berstatus **complete_merged** — 30 laporan, tidak setara dengan run utuh_

> `Prediksi berperingkat` lebih kecil daripada total prediksi bila pascaproses menambah teknik yang tidak pernah dipilih dari daftar kandidat (reconciler mengganti sub-teknik dengan base technique-nya). Teknik seperti itu tidak punya peringkat retrieval dan sengaja tidak dimasukkan ke sebaran.
