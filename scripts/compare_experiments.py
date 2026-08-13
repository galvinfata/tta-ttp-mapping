"""Bandingkan beberapa berkas hasil run dan cetak tabel Markdown siap tempel.

    python scripts/compare_experiments.py results/predictions/exp_A_*.json \
                                          results/predictions/exp_B_*.json

Membaca metrik lewat src/evaluation/evaluator.py APA ADANYA (tidak ada logika
metrik yang dihitung ulang di sini) supaya angka tetap sebanding dengan seluruh
angka pada naskah. Konfigurasi run dibaca dari <hasil>.manifest.json bila ada.

Kategorisasi FN memakai field `candidates_shown` yang dicatat pipeline saat run
— yaitu kandidat yang BENAR-BENAR tampil di prompt, bukan rekonstruksi ulang
TF-IDF. Berkas hasil lama (sebelum instrumentasi ini) tidak punya field itu dan
akan ditampilkan sebagai "-", bukan ditebak.
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from evaluation.evaluator import evaluate_predictions, evaluate_tactics  # noqa: E402
from knowledge.attck_loader import load_attck_techniques  # noqa: E402
from utils.run_manifest import load_manifest, _rank_summary  # noqa: E402


def _fmt(value, digits: int = 4) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _fn_categories(results: list[dict]) -> tuple[int | None, int | None]:
    """(retrieval_miss, reasoning_miss) dari kandidat yang tercatat saat run.

    retrieval-miss : GT tidak pernah tampil di prompt manapun -> mustahil benar.
    reasoning-miss : GT tampil di kandidat tapi tidak dipilih LLM.
    None bila berkas hasil tidak memuat candidates_shown.
    """
    if not any(r.get("candidates_shown") for r in results):
        return None, None

    retrieval_miss = reasoning_miss = 0
    for r in results:
        shown = set(r.get("candidates_shown") or [])
        missed = set(r.get("ground_truth", [])) - set(r.get("predicted_techniques", []))
        for tid in missed:
            if tid in shown:
                reasoning_miss += 1
            else:
                retrieval_miss += 1
    return retrieval_miss, reasoning_miss


def summarize(path: Path, attck_techniques: dict) -> dict:
    results = json.loads(path.read_text(encoding="utf-8"))
    manifest = load_manifest(path) or {}

    technique = evaluate_predictions(results, attck_techniques)
    tactic = evaluate_tactics(results, attck_techniques)

    ratios = [r["coverage_ratio"] for r in results if isinstance(r.get("coverage_ratio"), (int, float))]
    predictions = [len(r.get("predicted_techniques", [])) for r in results]
    retrieval_miss, reasoning_miss = _fn_categories(results)

    prompt_stats = manifest.get("prompt_stats") or {}
    duration = manifest.get("duration_seconds")
    # Keselarasan dengan peringkat retrieval. Dibaca dari manifest bila ada;
    # kalau tidak, dihitung ulang dari berkas hasil (manifest lama belum punya
    # blok ini, tapi berkas hasilnya mungkin sudah memuat peta peringkat).
    rank = manifest.get("rank_alignment") or _rank_summary(results)

    return {
        "file": path.name,
        "preset": manifest.get("preset") or path.stem,
        "reports": len(results),
        "coverage_median": statistics.median(ratios) if ratios else None,
        "coverage_mean": (sum(ratios) / len(ratios)) if ratios else None,
        "fully_read": sum(1 for x in ratios if x >= 0.999) if ratios else None,
        "pred_per_report": (sum(predictions) / len(predictions)) if predictions else 0.0,
        "exact_p": technique["precision"],
        "exact_r": technique["recall"],
        "exact_f1": technique["micro_f1"],
        "base_p": technique["base_precision"],
        "base_r": technique["base_recall"],
        "base_f1": technique["base_micro_f1"],
        "tactic_f1": tactic["tactic_micro_f1"],
        "retrieval_miss": retrieval_miss,
        "reasoning_miss": reasoning_miss,
        "ranked_predictions": rank.get("ranked_predictions"),
        "predictions_without_rank": rank.get("predictions_without_rank"),
        "median_rank": rank.get("median_tfidf_rank"),
        "mean_rank": rank.get("mean_tfidf_rank"),
        "pct_outside_top30": rank.get("pct_outside_top30"),
        "rank_bucket_pct": rank.get("rank_bucket_pct"),
        "filtered_out": rank.get("filtered_out_by_accept_top_n"),
        "filtered_out_median_rank": rank.get("filtered_out_median_rank"),
        "shuffle_seed": (manifest.get("env") or {}).get("CANDIDATE_SHUFFLE_SEED") or None,
        "accept_top_n": (manifest.get("env") or {}).get("TECHNIQUE_ACCEPT_TOP_N"),
        "duration_min": (duration / 60) if duration else None,
        "reviewer_active": manifest.get("reviewer_active"),
        "reports_triggering_revision": manifest.get("reports_triggering_revision"),
        "n_ctx": prompt_stats.get("n_ctx"),
        "overflow_calls": prompt_stats.get("overflow_calls"),
        "total_calls": prompt_stats.get("total_calls"),
        "prompt_tokens_avg": prompt_stats.get("prompt_tokens_avg"),
        "prompt_tokens_max": prompt_stats.get("prompt_tokens_max"),
        "candidates_dropped": prompt_stats.get("candidates_dropped"),
        # Bukti runtime retrieval hibrida. Env RETRIEVAL_EMBEDDING_HYBRID hanya
        # menyatakan niat; bila server embedding mati di tengah run, sisa laporan
        # diproses TF-IDF murni. Preset yang hasilnya menyimpang harus bisa
        # diperiksa apakah retrieval-nya memang utuh hibrida.
        "emb_fallback": (manifest.get("embedding_runtime") or {}).get("fallback_triggered"),
        "emb_pct_hybrid": (manifest.get("embedding_runtime") or {}).get("pct_calls_hybrid"),
        "has_manifest": bool(manifest),
        # "complete" = run selesai wajar. "partial"/"aborted" = run terhenti;
        # hasilnya TIDAK boleh dibandingkan setara dengan run utuh karena jumlah
        # laporannya berbeda dan laporan terakhir bisa terkontaminasi timeout.
        "run_status": manifest.get("status") if manifest else None,
        "do_not_use": bool(manifest.get("do_not_use_for_metrics")),
    }


def _pct(value) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def render_main_table(rows: list[dict]) -> str:
    header = (
        "| Preset | Laporan | Coverage median | Pred/laporan | Median peringkat | "
        "% di luar top-30 | P exact | R exact | F1 exact | P base | R base | F1 base | "
        "Retrieval-miss | Reasoning-miss | Durasi (mnt) |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    )
    lines = [header]
    for r in rows:
        lines.append(
            f"| {r['preset']} | {r['reports']} | {_fmt(r['coverage_median'], 3)} | "
            f"{r['pred_per_report']:.1f} | {_fmt(r['median_rank'], 1)} | "
            f"{_pct(r['pct_outside_top30'])} | "
            f"{_fmt(r['exact_p'])} | {_fmt(r['exact_r'])} | "
            f"{_fmt(r['exact_f1'])} | {_fmt(r['base_p'])} | {_fmt(r['base_r'])} | "
            f"{_fmt(r['base_f1'])} | {_fmt(r['retrieval_miss'])} | {_fmt(r['reasoning_miss'])} | "
            f"{_fmt(r['duration_min'], 1)} |"
        )
    return "\n".join(lines)


def render_rank_table(rows: list[dict]) -> str:
    """Sebaran peringkat retrieval dari teknik yang benar-benar diprediksi.

    Inilah tabel yang menjawab apakah keluaran sistem sekadar mengikuti urutan
    retrieval: makin besar porsi peringkat bawah dan `% di luar top-30`, makin
    besar kontribusi LLM yang independen dari peringkat.
    """
    header = (
        "| Preset | ACCEPT_TOP_N | Seed acak | Prediksi berperingkat | Median | Rata-rata | "
        "1–10 | 11–20 | 21–30 | 31+ | Dibuang filter | Median peringkat yang dibuang |\n"
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    )
    lines = [header]
    for r in rows:
        buckets = r["rank_bucket_pct"] or {}
        lines.append(
            f"| {r['preset']} | {_fmt(r['accept_top_n'])} | {r['shuffle_seed'] or '-'} | "
            f"{_fmt(r.get('ranked_predictions'))} | {_fmt(r['median_rank'], 1)} | "
            f"{_fmt(r['mean_rank'], 2)} | "
            f"{_pct(buckets.get('1-10'))} | {_pct(buckets.get('11-20'))} | "
            f"{_pct(buckets.get('21-30'))} | {_pct(buckets.get('31+'))} | "
            f"{_fmt(r['filtered_out'])} | {_fmt(r['filtered_out_median_rank'], 1)} |"
        )
    return "\n".join(lines)


def render_context_table(rows: list[dict]) -> str:
    header = (
        "| Preset | n_ctx | Panggilan LLM | Token prompt rata-rata | Token prompt maks | "
        "Panggilan melampaui n_ctx | Kandidat dibuang | Reviewer aktif | Laporan memicu revisi | "
        "Retrieval hibrida |\n"
        "|---|---:|---:|---:|---:|---:|---:|---|---:|---|"
    )
    lines = [header]
    for r in rows:
        overflow = r["overflow_calls"]
        total = r["total_calls"]
        overflow_txt = (
            f"{overflow} / {total} ({overflow / total * 100:.0f}%)"
            if isinstance(overflow, int) and total else _fmt(overflow)
        )
        reviewer = (
            "-" if r["reviewer_active"] is None
            else ("ya" if r["reviewer_active"] else "tidak")
        )
        # Run lama (sebelum instrumentasi) tidak punya field ini -> "-", bukan "utuh".
        if r["emb_fallback"] is None:
            hybrid_txt = "-"
        elif r["emb_fallback"]:
            hybrid_txt = f"⚠ JATUH ke TF-IDF ({_pct(r['emb_pct_hybrid'])} hibrida)"
        else:
            hybrid_txt = "utuh"
        lines.append(
            f"| {r['preset']} | {_fmt(r['n_ctx'])} | {_fmt(total)} | "
            f"{_fmt(r['prompt_tokens_avg'], 0)} | {_fmt(r['prompt_tokens_max'])} | {overflow_txt} | "
            f"{_fmt(r['candidates_dropped'])} | {reviewer} | {_fmt(r['reports_triggering_revision'])} | "
            f"{hybrid_txt} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bandingkan beberapa berkas hasil run.")
    parser.add_argument("results", nargs="+", help="Path berkas hasil JSON")
    parser.add_argument("--attck", default="data/mitre_cti/enterprise-attack.json")
    parser.add_argument("--out", default="", help="Tulis tabel Markdown ke berkas ini")
    args = parser.parse_args()

    paths = [Path(p) for p in args.results]
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise SystemExit(f"Berkas tidak ditemukan: {', '.join(str(p) for p in missing)}")

    attck_techniques = load_attck_techniques(args.attck)
    rows = [summarize(p, attck_techniques) for p in paths]

    # Run yang terhenti tidak boleh masuk tabel perbandingan tanpa peringatan
    # keras — jumlah laporannya berbeda dan laporan terakhirnya bisa
    # terkontaminasi kegagalan server.
    suspect = [r for r in rows if r["do_not_use"] or (r["run_status"] not in (None, "complete"))]
    for r in suspect:
        print(
            f"!! PERINGATAN: {r['file']} berstatus '{r['run_status']}'"
            f"{' dan ditandai do_not_use_for_metrics' if r['do_not_use'] else ''} — "
            f"hanya {r['reports']} laporan. JANGAN dibandingkan setara dengan run utuh.",
            file=sys.stderr,
        )

    blocks = [
        "### Perbandingan preset eksperimen",
        "",
        render_main_table(rows),
        "",
        "### Keselarasan dengan peringkat retrieval",
        "",
        render_rank_table(rows),
        "",
        "### Konsumsi context window & Reviewer",
        "",
        render_context_table(rows),
        "",
        "Sumber berkas:",
        "",
        *[
            f"- **{r['preset']}** — `{r['file']}`"
            + ("" if r["has_manifest"] else "  _(tanpa manifest: konfigurasi run tidak terekam)_")
            + ("" if r["run_status"] in (None, "complete")
               else f"  ⚠️ _run berstatus **{r['run_status']}** — {r['reports']} laporan, "
                    "tidak setara dengan run utuh_")
            for r in rows
        ],
    ]
    if any(r["median_rank"] is None for r in rows):
        blocks += [
            "",
            "> Kolom peringkat bernilai `-` untuk berkas hasil dari sebelum instrumentasi "
            "keselarasan (`rank_of_accepted`) dipasang. Peringkat run lama TIDAK "
            "direkonstruksi di sini: retrieval sudah berubah versi sejak run itu, sehingga "
            "rekonstruksi akan mengukur retrieval hari ini, bukan yang dipakai run tersebut.",
        ]
    if any(r["predictions_without_rank"] for r in rows):
        blocks += [
            "",
            "> `Prediksi berperingkat` lebih kecil daripada total prediksi bila pascaproses "
            "menambah teknik yang tidak pernah dipilih dari daftar kandidat (reconciler "
            "mengganti sub-teknik dengan base technique-nya). Teknik seperti itu tidak punya "
            "peringkat retrieval dan sengaja tidak dimasukkan ke sebaran.",
        ]
    if any(r["retrieval_miss"] is None for r in rows):
        blocks += [
            "",
            "> Kolom retrieval-miss/reasoning-miss bernilai `-` untuk berkas hasil yang dibuat "
            "sebelum instrumentasi kandidat (`candidates_shown`) ada, karena kandidat yang "
            "dilihat sistem saat itu tidak terekam dan tidak dapat direkonstruksi secara sah.",
        ]

    output = "\n".join(blocks)
    print(output)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(output + "\n", encoding="utf-8")
        print(f"\nDisimpan: {args.out}")


if __name__ == "__main__":
    main()
