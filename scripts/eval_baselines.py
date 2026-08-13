"""Baseline retrieval-tanpa-LLM, dihitung SEBANDING dengan run sistem.

Untuk tiap laporan, ambil top-N teknik ber-peringkat retrieval (fungsi retrieval
yang SAMA dengan sistem) lalu jadikan langsung sebagai prediksi, tanpa LLM.
Selisih terhadap sistem = kontribusi LLM.

Perbaikan atas versi lama (audit 3 & 5 Agustus 2026). Versi lama menghasilkan
perbandingan yang TERKONFOUNDING pada empat hal sekaligus:

1. **Salah label.** Yang disebut "TF-IDF-only" sebenarnya hibrida TF-IDF +
   embedding neural (RETRIEVAL_EMBEDDING_HYBRID default true). Kini label
   mengikuti apa yang benar-benar dijalankan, dan --pure-tfidf menyediakan
   baseline leksikal murni yang jujur.
2. **Bukan ablasi murni.** Baseline memakai retrieval whole-report, sistem
   memakai per-chunk. Kini --per-chunk mereproduksi persis mode sistem.
3. **Beda versi & beda subset.** Baseline diregenerasi dengan retrieval v5,
   sedangkan run sistem pembandingnya memakai retrieval lama. Kini --reports
   membatasi ke subset yang sama dengan preset, dan seluruh angka dihitung dalam
   satu proses dengan kode yang sama.
4. **Justifikasi N yang menyesatkan.** N dipilih karena F1-nya tertinggi —
   yaitu disetel pada data yang sama yang dilaporkan. Kini N utama dipilih
   BUDGET-MATCHED: setara rata-rata jumlah prediksi sistem, sehingga baseline
   dan sistem dinilai pada anggaran prediksi yang sama.

Baseline MAJORITY dilabeli ORACLE: peringkatnya dihitung dari ground truth
laporan yang sama yang dievaluasi, jadi ia membocorkan label uji dan hanya sah
dibaca sebagai BATAS ATAS TRIVIAL — bukan sebagai pesaing yang wajar.

Offline & deterministik (kecuali --hybrid, yang memerlukan server embedding).

    # baseline sebanding untuk preset A/G/H (subset 30 laporan, mode per-chunk)
    python scripts/eval_baselines.py --reports 30 --per-chunk \
        --match-budget-to results/predictions/exp_A_baseline_replikasi_*.json

    # tambahan: baseline leksikal murni, tanpa embedding neural
    python scripts/eval_baselines.py --reports 30 --per-chunk --pure-tfidf
"""
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

SWEEP_NS = [3, 5, 10, 15, 20, 50]


def _majority_base_ranking(reports):
    """Urutkan base-technique berdasar frekuensi DOKUMEN (jumlah laporan yang memuatnya).

    ORACLE: dihitung dari ground truth laporan yang dievaluasi juga.
    """
    from eval_common import base_technique

    counter = Counter()
    for r in reports:
        for base in {base_technique(t) for t in r["techniques"]}:
            counter[base] += 1
    # Urut: frekuensi menurun, lalu ID (deterministik saat seri).
    return [tid for tid, _ in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


def _merge_per_chunk(chunk_lists: list[list[str]], n: int) -> list[str]:
    """Gabungkan daftar kandidat per-chunk jadi satu peringkat top-N.

    Round-robin menurut peringkat: ambil peringkat-1 dari tiap chunk, lalu
    peringkat-2 dari tiap chunk, dan seterusnya, dedup sambil jalan. Ini meniru
    cara sistem melihat kandidat — tiap chunk menyumbang kandidat terbaiknya
    lebih dulu — tanpa mengistimewakan chunk pertama seperti kalau daftar
    sekadar disambung berurutan.
    """
    merged: list[str] = []
    seen: set[str] = set()
    depth = max((len(c) for c in chunk_lists), default=0)
    for rank in range(depth):
        for chunk in chunk_lists:
            if rank < len(chunk) and chunk[rank] not in seen:
                seen.add(chunk[rank])
                merged.append(chunk[rank])
                if len(merged) >= n:
                    return merged
    return merged


def _system_budget(path_pattern: str) -> tuple[float, int, str] | None:
    """Rata-rata jumlah prediksi per laporan dari sebuah berkas hasil sistem."""
    matches = sorted(Path().glob(path_pattern)) if any(c in path_pattern for c in "*?[") \
        else [Path(path_pattern)]
    matches = [p for p in matches if p.exists()]
    if not matches:
        print(f"[WARN] --match-budget-to '{path_pattern}' tidak cocok dengan berkas manapun.")
        return None
    path = matches[-1]
    results = json.loads(path.read_text(encoding="utf-8"))
    counts = [len(r.get("predicted_techniques", [])) for r in results]
    if not counts:
        return None
    return sum(counts) / len(counts), len(counts), path.name


def main():
    parser = argparse.ArgumentParser(description="Baseline retrieval tanpa LLM.")
    parser.add_argument(
        "--reports", type=int, default=0,
        help="Pakai N laporan PERTAMA saja (harus sama dengan preset; 0 = semua)",
    )
    parser.add_argument(
        "--per-chunk", action="store_true",
        help="Kandidat diambil per-chunk seperti sistem (ablasi murni). "
             "Tanpa ini: whole-report, yang TIDAK sebanding dengan sistem.",
    )
    parser.add_argument(
        "--pure-tfidf", action="store_true",
        help="Matikan hibrida embedding -> baseline leksikal murni yang jujur berlabel TF-IDF",
    )
    parser.add_argument(
        "--match-budget-to", default="",
        help="Berkas hasil sistem; N utama disetel = rata-rata prediksi/laporan run itu",
    )
    parser.add_argument("--out", default="", help="Nama berkas JSON keluaran (opsional)")
    args = parser.parse_args()

    # HARUS di-set sebelum agents.retrieval di-import: konstantanya dibaca saat
    # import, jadi menyetel sesudahnya tidak akan berpengaruh apa-apa.
    if args.pure_tfidf:
        os.environ["RETRIEVAL_EMBEDDING_HYBRID"] = "false"
        if "agents.retrieval" in sys.modules:
            raise SystemExit(
                "agents.retrieval sudah ter-import sebelum --pure-tfidf sempat berlaku."
            )

    from eval_common import (
        ATTCK_SOURCE, DATA_DIR, METRICS_DIR, CANDIDATE_TOP_K,
        both_modes, base_technique, fmt_row, METRIC_HEADER, build_candidate_map,
    )
    from knowledge.attck_loader import load_attck_techniques
    from knowledge.data_loader import load_tram_dataset
    from agents.retrieval import RETRIEVAL_EMBEDDING_HYBRID, RETRIEVAL_SIGNATURE

    if args.pure_tfidf and RETRIEVAL_EMBEDDING_HYBRID:
        raise SystemExit("--pure-tfidf gagal: RETRIEVAL_EMBEDDING_HYBRID masih aktif.")

    attck_techniques = load_attck_techniques(ATTCK_SOURCE)
    reports = load_tram_dataset(DATA_DIR)
    total_reports = len(reports)
    if args.reports:
        reports = reports[: args.reports]

    retrieval_label = "tfidf_murni" if args.pure_tfidf else "hibrida"
    chunk_label = "perchunk" if args.per_chunk else "wholereport"
    baseline_label = f"baseline_{retrieval_label}_{chunk_label}"

    print(f"KB: {len(attck_techniques)} teknik | Laporan: {len(reports)} dari {total_reports}")
    print(f"Retrieval: {'TF-IDF murni' if args.pure_tfidf else 'hibrida TF-IDF + embedding'}"
          f" | mode: {'per-chunk (seperti sistem)' if args.per_chunk else 'whole-report'}")
    print(f"Signature: {RETRIEVAL_SIGNATURE}")
    print(f"Label keluaran: {baseline_label}")

    y_true = [r["techniques"] for r in reports]
    n_labels_exact = len(attck_techniques)
    n_labels_base = len({base_technique(t) for t in attck_techniques})

    # Anggaran prediksi sistem -> N utama baseline (budget-matched).
    budget_n = None
    budget_note = ""
    if args.match_budget_to:
        budget = _system_budget(args.match_budget_to)
        if budget:
            mean_preds, n_rep, fname = budget
            budget_n = max(1, round(mean_preds))
            budget_note = (
                f"N={budget_n} disetel BUDGET-MATCHED: rata-rata {mean_preds:.1f} "
                f"prediksi/laporan pada {fname} ({n_rep} laporan)"
            )
            print(f"\n{budget_note}")

    print(f"\nMenghitung kandidat top-{CANDIDATE_TOP_K} per laporan"
          f"{' (per-chunk)' if args.per_chunk else ''}...")
    # --pure-tfidf mengabaikan cache: cache dibangun dengan konfigurasi retrieval
    # lain (hibrida) dan memakainya akan diam-diam mengembalikan kandidat hibrida
    # dengan label "TF-IDF murni" — persis kesalahan pelabelan yang skrip ini
    # diperbaiki untuk menghindarinya. (Sidik jari cache memuat RETRIEVAL_SIGNATURE
    # sehingga sebenarnya sudah tertolak, tapi eksplisit lebih baik daripada
    # bergantung pada itu.)
    candidate_map = build_candidate_map(
        reports, attck_techniques, top_k=CANDIDATE_TOP_K,
        use_cache=not args.pure_tfidf, per_chunk=args.per_chunk,
    )

    ns = sorted(set(SWEEP_NS + ([budget_n] if budget_n else [])))
    majority_ranking = _majority_base_ranking(reports)

    sweep = {}
    for n in ns:
        if args.per_chunk:
            retr_pred = [_merge_per_chunk(candidate_map[r["id"]], n) for r in reports]
        else:
            retr_pred = [candidate_map[r["id"]][:n] for r in reports]
        maj_pred = [majority_ranking[:n] for _ in reports]
        sweep[n] = {
            "retrieval": both_modes(y_true, retr_pred, n_labels_exact, n_labels_base),
            "majority_oracle": both_modes(y_true, maj_pred, n_labels_exact, n_labels_base),
        }

    label_pretty = ("TF-IDF murni" if args.pure_tfidf else "Hibrida TF-IDF+emb") + \
                   (" per-chunk" if args.per_chunk else " whole-report")

    print(f"\n### Baseline {label_pretty} — EXACT")
    print(METRIC_HEADER)
    for n in ns:
        print(fmt_row(f"top-{n}", sweep[n]["retrieval"]["exact"]))

    print(f"\n### Baseline {label_pretty} — BASE-TECHNIQUE")
    print(METRIC_HEADER)
    for n in ns:
        print(fmt_row(f"top-{n}", sweep[n]["retrieval"]["base"]))

    print("\n### Baseline MAJORITY — ORACLE, BATAS ATAS TRIVIAL (BASE-TECHNIQUE)")
    print("Peringkat dihitung dari ground truth laporan yang dievaluasi juga —")
    print("membocorkan label uji. Bukan pesaing yang wajar; batas atas trivial saja.")
    print(METRIC_HEADER)
    for n in ns:
        print(fmt_row(f"Majority-oracle top-{n}", sweep[n]["majority_oracle"]["base"]))

    if budget_n:
        print(f"\n### Pembanding utama (budget-matched, N={budget_n})")
        print(budget_note)
        print(METRIC_HEADER)
        print(fmt_row(f"{label_pretty} top-{budget_n} — EXACT", sweep[budget_n]["retrieval"]["exact"]))
        print(fmt_row(f"{label_pretty} top-{budget_n} — BASE", sweep[budget_n]["retrieval"]["base"]))
        print(fmt_row(f"Majority-oracle top-{budget_n} — BASE", sweep[budget_n]["majority_oracle"]["base"]))

    out = {
        "label": baseline_label,
        "retrieval": {
            "embedding_hybrid": RETRIEVAL_EMBEDDING_HYBRID,
            "mode": "per_chunk" if args.per_chunk else "whole_report",
            "signature": RETRIEVAL_SIGNATURE,
            "top_k": CANDIDATE_TOP_K,
        },
        "dataset": {
            "reports_evaluated": len(reports),
            "reports_available": total_reports,
            "subset": "first_n" if args.reports else "all",
            "kb_techniques": len(attck_techniques),
        },
        "budget_matched_n": budget_n,
        "budget_matched_note": budget_note or None,
        "majority_is_oracle": True,
        "majority_ranking_top20": majority_ranking[:20],
        "sweep_ns": ns,
        "results": {str(n): sweep[n] for n in ns},
        # CATATAN: TIDAK ada "full_system_reference" di sini. Angka sistem harus
        # datang dari run yang dijalankan pada kode, versi retrieval, mode
        # chunking, dan subset laporan yang SAMA — bandingkan lewat
        # scripts/compare_experiments.py, bukan dengan angka sistem yang
        # ditempelkan ke berkas baseline.
    }
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    name = args.out or f"baseline_{retrieval_label}_{chunk_label}" \
                       f"{f'_n{len(reports)}' if args.reports else ''}.json"
    out_path = METRICS_DIR / name
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDisimpan: {out_path}")
    print("Berkas lama results/metrics/baseline_sweep.json TIDAK disentuh.")


if __name__ == "__main__":
    main()
