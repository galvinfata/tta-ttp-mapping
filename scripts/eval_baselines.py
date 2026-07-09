"""TUGAS 1 — Baseline TF-IDF-only (retrieval tanpa LLM).

Untuk tiap laporan, ambil top-N teknik ber-ranking TF-IDF (fungsi retrieval yang
sama dengan sistem) lalu jadikan LANGSUNG sebagai prediksi (tanpa LLM). Sapu
N ∈ {3,5,10,15,20,50}, hitung P/R/F1 (exact & base) + TP/FP/FN.

Juga: baseline MAJORITY (selalu prediksi N teknik base tersering di GT) sebagai
lantai absolut, dan verifikasi bahwa metrik set-murni mereproduksi angka sistem.

Offline & deterministik. Jalankan:
    python scripts/eval_baselines.py
"""
import json
from collections import Counter

from eval_common import (
    ATTCK_SOURCE, DATA_DIR, METRICS_DIR, CANDIDATE_TOP_K,
    both_modes, base_technique, fmt_row, METRIC_HEADER, build_candidate_map,
)
from knowledge.attck_loader import load_attck_techniques
from knowledge.data_loader import load_tram_dataset

SWEEP_NS = [3, 5, 10, 15, 20, 50]
FULL_SYSTEM_RESULTS = "results/predictions/results_all_20260531_190814.json"


def _majority_base_ranking(reports):
    """Urutkan base-technique berdasar frekuensi DOKUMEN (jumlah laporan yang memuatnya)."""
    counter = Counter()
    for r in reports:
        for base in {base_technique(t) for t in r["techniques"]}:
            counter[base] += 1
    # Urut: frekuensi menurun, lalu ID (deterministik saat seri).
    return [tid for tid, _ in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


def _verify_full_system(y_true):
    """Verifikasi metrik set-murni mereproduksi angka sistem penuh."""
    try:
        res = json.load(open(FULL_SYSTEM_RESULTS, encoding="utf-8"))
    except FileNotFoundError:
        print(f"[WARN] {FULL_SYSTEM_RESULTS} tidak ada — lewati verifikasi.")
        return None
    yt = [r.get("ground_truth", []) for r in res]
    yp = [r.get("predicted_techniques", []) for r in res]
    m = both_modes(yt, yp)
    e, b = m["exact"], m["base"]
    ok = (
        e["tp"] == 137 and e["fp"] == 650 and e["fn"] == 1806
        and b["tp"] == 196 and b["fp"] == 448 and b["fn"] == 1527
    )
    print("\n### Verifikasi metrik (harus sama dgn angka skripsi)")
    print(METRIC_HEADER)
    print(fmt_row("Sistem penuh — EXACT", e))
    print(fmt_row("Sistem penuh — BASE", b))
    print(f"\nReproduksi angka skripsi: {'OK ✓' if ok else 'GAGAL ✗'}")
    return m


def main():
    attck_techniques = load_attck_techniques(ATTCK_SOURCE)
    reports = load_tram_dataset(DATA_DIR)
    print(f"KB: {len(attck_techniques)} teknik | Laporan: {len(reports)}")

    y_true = [r["techniques"] for r in reports]

    # Verifikasi lebih dulu (kriteria terima).
    full_system = _verify_full_system(y_true)

    # Retrieval top-50 sekali per laporan; top-N = slice pertama.
    print("\nMenghitung kandidat TF-IDF top-50 per laporan...")
    candidate_map = build_candidate_map(reports, attck_techniques, top_k=CANDIDATE_TOP_K)
    candidates_ordered = [candidate_map[r["id"]] for r in reports]

    majority_ranking = _majority_base_ranking(reports)

    sweep = {}
    for n in SWEEP_NS:
        tfidf_pred = [c[:n] for c in candidates_ordered]
        maj_pred = [majority_ranking[:n] for _ in reports]
        sweep[n] = {
            "tfidf": both_modes(y_true, tfidf_pred),
            "majority": both_modes(y_true, maj_pred),
        }

    # N terbaik untuk baseline TF-IDF = F1 base tertinggi.
    best_n = max(SWEEP_NS, key=lambda n: sweep[n]["tfidf"]["base"]["f1"])

    # --- Cetak tabel markdown ---
    print("\n### Baseline TF-IDF-only — EXACT")
    print(METRIC_HEADER)
    for n in SWEEP_NS:
        print(fmt_row(f"TF-IDF top-{n}", sweep[n]["tfidf"]["exact"]))

    print("\n### Baseline TF-IDF-only — BASE-TECHNIQUE (figur utama)")
    print(METRIC_HEADER)
    for n in SWEEP_NS:
        print(fmt_row(f"TF-IDF top-{n}", sweep[n]["tfidf"]["base"]))

    print("\n### Baseline MAJORITY (N base tersering di GT) — BASE-TECHNIQUE")
    print(METRIC_HEADER)
    for n in SWEEP_NS:
        print(fmt_row(f"Majority top-{n}", sweep[n]["majority"]["base"]))

    print("\n### Ringkasan pembanding (BASE-TECHNIQUE)")
    print(METRIC_HEADER)
    print(fmt_row(f"TF-IDF N terbaik (top-{best_n}, F1 tertinggi)", sweep[best_n]["tfidf"]["base"]))
    print(fmt_row("TF-IDF top-5 (≈ rata-rata prediksi sistem)", sweep[5]["tfidf"]["base"]))
    print(fmt_row("Majority top-5", sweep[5]["majority"]["base"]))
    if full_system:
        print(fmt_row("Sistem penuh (LLM multi-agent)", full_system["base"]))

    # --- Simpan JSON mentah ---
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "dataset": {"reports": len(reports), "kb_techniques": len(attck_techniques)},
        "sweep_ns": SWEEP_NS,
        "best_n_tfidf_base_f1": best_n,
        "majority_ranking_top20": majority_ranking[:20],
        "results": {str(n): sweep[n] for n in SWEEP_NS},
        "full_system_reference": full_system,
    }
    out_path = METRICS_DIR / "baseline_sweep.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDisimpan: {out_path}")


if __name__ == "__main__":
    main()
