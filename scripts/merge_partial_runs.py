"""Gabungkan beberapa potongan run preset yang sama menjadi satu berkas utuh.

    python scripts/merge_partial_runs.py \
        results/predictions/exp_G_tanpa_filter_20260806_142143.json \
        results/predictions/exp_G_tanpa_filter_r21-30_20260806_220000.json \
        --out results/predictions/exp_G_tanpa_filter_GABUNGAN_30.json

Kenapa penggabungan ini SAH: pipeline memproses tiap laporan secara independen —
tidak ada keadaan yang terbawa antar laporan yang memengaruhi prediksi. Retrieval
dihitung ulang per laporan, prompt dibangun per chunk, dan tidak ada pembelajaran
antar-iterasi. Menjalankan laporan 1-20 lalu 21-30 karenanya menghasilkan
himpunan prediksi yang setara dengan menjalankan 1-30 sekaligus.

Kenapa tetap harus lewat skrip ini, bukan disatukan manual: kesetaraan itu HANYA
berlaku bila konfigurasi yang memengaruhi prediksi benar-benar identik. Skrip
menolak menggabungkan bila ada satu saja yang berbeda, dan mencatat asal-usul tiap
potongan di manifest hasil supaya angka di naskah tetap dapat ditelusuri.
"""
import argparse
import json
import os
import statistics
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# Variabel yang MENGUBAH prediksi. Perbedaan pada salah satu membuat potongan
# tidak sebanding, sehingga penggabungan ditolak. Variabel infrastruktur
# (LOCAL_LLM_BASE_URL, TRAM_DATA_DIR) sengaja TIDAK ada di sini: alamat server
# berbeda tidak mengubah apa yang dihitung — dan potongan G memang dijalankan
# dari dua alamat berbeda (LAN lalu Tailscale) untuk mesin yang sama.
PREDICTION_AFFECTING = [
    "LOCAL_LLM_MODEL",
    "LLM_N_CTX",
    "PROMPT_BUDGET_ENFORCE",
    "TECHNIQUE_CANDIDATE_TOP_K",
    "LOCAL_LLM_CANDIDATE_TOP_K",
    "CANDIDATE_LIST_MAX_CHARS",
    "CANDIDATE_DESC_CHARS",
    "LOCAL_LLM_REPORT_MAX_CHARS",
    "LLM_CHUNK_OVERLAP_CHARS",
    "LLM_MAX_CHUNKS",
    "RETRIEVAL_MAX_CHARS",
    "RETRIEVAL_PER_CHUNK",
    "RETRIEVAL_NAME_BOOST",
    "RETRIEVAL_EXCLUDE_PRECOMPROMISE",
    "RETRIEVAL_EMBEDDING_HYBRID",
    "RETRIEVAL_EMBEDDING_MODEL",
    "RETRIEVAL_EMBEDDING_ALPHA",
    "RETRIEVAL_DOC_DESC_CHARS",
    "ATTCK_DESC_MAX_CHARS",
    "TECHNIQUE_ACCEPT_TOP_N",
    "CANDIDATE_SHUFFLE_SEED",
    "RECONCILE_SUBTECH_FAMILY_CAP",
    "RECONCILE_TACTIC_FILTER",
    "REVIEWER_ENABLE",
    "LLM_REVIEW_MAX_ITER",
    "LLM_DISABLE_THINKING",
    "LLM_STRUCTURED_OUTPUT",
    "ATTCK_SOURCE",
]


def manifest_for(results_path: Path) -> dict:
    path = Path(str(results_path) + ".manifest.json")
    if not path.exists():
        raise SystemExit(
            f"[DIBATALKAN] {results_path.name} tidak punya manifest.\n"
            f"  Tanpa manifest, kesebandingan konfigurasi tidak dapat diperiksa —\n"
            f"  dan itulah satu-satunya alasan penggabungan ini sah."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def check_compatible(manifests: list[dict], names: list[str]) -> list[str]:
    """Tolak bila ada beda yang memengaruhi prediksi. Kembalikan daftar peringatan."""
    warnings: list[str] = []
    base, base_name = manifests[0], names[0]

    for m, name in zip(manifests[1:], names[1:]):
        if m.get("preset") != base.get("preset"):
            raise SystemExit(
                f"[DIBATALKAN] preset berbeda: {base_name}='{base.get('preset')}' "
                f"vs {name}='{m.get('preset')}'"
            )

        beda = [
            (k, base.get("env", {}).get(k), m.get("env", {}).get(k))
            for k in PREDICTION_AFFECTING
            if str(base.get("env", {}).get(k)) != str(m.get("env", {}).get(k))
        ]
        if beda:
            rincian = "\n".join(f"    {k}: '{a}' vs '{b}'" for k, a, b in beda)
            raise SystemExit(
                f"[DIBATALKAN] konfigurasi yang memengaruhi prediksi berbeda "
                f"antara {base_name} dan {name}:\n{rincian}\n"
                f"  Potongan ini TIDAK sebanding dan tidak boleh digabung."
            )

        if base.get("attck", {}).get("sha256") != m.get("attck", {}).get("sha256"):
            raise SystemExit(
                f"[DIBATALKAN] berkas ATT&CK berbeda antara {base_name} dan {name} "
                f"(sha256 tidak sama) — basis pengetahuannya bukan yang sama."
            )

        # Commit berbeda tidak otomatis membatalkan (bisa saja hanya menyentuh
        # skrip pelapor), tetapi WAJIB terlihat oleh pembaca hasil.
        if base.get("git", {}).get("commit") != m.get("git", {}).get("commit"):
            warnings.append(
                f"commit git berbeda: {base_name}={str(base.get('git', {}).get('commit'))[:8]} "
                f"vs {name}={str(m.get('git', {}).get('commit'))[:8]} — pastikan bedanya "
                f"tidak menyentuh jalur prediksi"
            )
        if m.get("git", {}).get("dirty") or base.get("git", {}).get("dirty"):
            warnings.append("repo dalam keadaan dirty saat run — kode persis tidak terekam commit")

    return warnings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gabungkan potongan run preset yang sama menjadi satu berkas utuh."
    )
    parser.add_argument("results", nargs="+", help="Berkas hasil potongan (urutan bebas)")
    parser.add_argument("--out", required=True, help="Path berkas hasil gabungan")
    parser.add_argument(
        "--expect", type=int, default=0,
        help="Jumlah laporan yang diharapkan (mis. 30). Bila tidak cocok, dibatalkan.",
    )
    args = parser.parse_args()

    paths = [Path(p) for p in args.results]
    out_path = Path(args.out)
    if out_path.exists():
        raise SystemExit(f"[DIBATALKAN] {out_path} sudah ada — tidak menimpa berkas hasil.")

    all_results: list[dict] = []
    manifests, names = [], []
    seen: dict[str, str] = {}

    for p in paths:
        if not p.exists():
            raise SystemExit(f"[DIBATALKAN] tidak ditemukan: {p}")
        results = json.loads(p.read_text(encoding="utf-8"))
        manifests.append(manifest_for(p))
        names.append(p.name)

        for r in results:
            rid = str(r.get("report_id", ""))
            if rid in seen:
                raise SystemExit(
                    f"[DIBATALKAN] laporan ganda '{rid[:60]}' muncul di "
                    f"{seen[rid]} DAN {p.name}.\n"
                    f"  Menggabungkannya akan menghitung laporan itu dua kali."
                )
            seen[rid] = p.name
            all_results.append(r)

    warnings = check_compatible(manifests, names)

    print(f"Potongan yang digabung ({len(paths)}):")
    for p, m in zip(paths, manifests):
        print(f"  {p.name}: {m.get('reports_processed')} laporan, status={m.get('status')}")
    print(f"Total: {len(all_results)} laporan unik")

    if args.expect and len(all_results) != args.expect:
        raise SystemExit(
            f"[DIBATALKAN] jumlah laporan {len(all_results)} != --expect {args.expect}. "
            f"Ada potongan yang hilang atau tumpang tindih."
        )
    for w in warnings:
        print(f"  [PERINGATAN] {w}")

    # --- Metrik dihitung ULANG dari hasil gabungan, bukan dirata-ratakan ---
    from knowledge.attck_loader import load_attck_techniques
    from evaluation.evaluator import evaluate_predictions, evaluate_tactics, save_results
    from utils.run_manifest import (
        _coverage_summary, _rank_summary, _reviewer_summary, write_manifest,
    )

    attck_source = manifests[0].get("attck", {}).get("source") or os.getenv(
        "ATTCK_SOURCE", "data/mitre_cti/enterprise-attack.json"
    )
    attck_techniques = load_attck_techniques(attck_source)

    metrics = evaluate_predictions(all_results, attck_techniques)
    tactic_metrics = evaluate_tactics(all_results, attck_techniques)

    save_results(all_results, str(out_path))

    print(f"\n=== GABUNGAN {manifests[0].get('preset')} | {len(all_results)} laporan ===")
    print(f"exact  P={metrics['precision']} R={metrics['recall']} F1={metrics['micro_f1']}")
    print(f"base   P={metrics['base_precision']} R={metrics['base_recall']} F1={metrics['base_micro_f1']}")
    print(f"taktik P={tactic_metrics['tactic_precision']} R={tactic_metrics['tactic_recall']} "
          f"F1={tactic_metrics['tactic_micro_f1']}")

    # Statistik embedding dijumlahkan; fallback di potongan MANAPUN menodai hasil.
    emb_hybrid = sum(int((m.get("embedding_runtime") or {}).get("retrieval_calls_hybrid") or 0)
                     for m in manifests)
    emb_tfidf = sum(int((m.get("embedding_runtime") or {}).get("retrieval_calls_tfidf_only") or 0)
                    for m in manifests)
    emb_total = emb_hybrid + emb_tfidf
    embedding_runtime = {
        "hybrid_requested": all((m.get("embedding_runtime") or {}).get("hybrid_requested")
                                for m in manifests),
        "fallback_triggered": any((m.get("embedding_runtime") or {}).get("fallback_triggered")
                                  for m in manifests),
        "fallback_reason": next(
            ((m.get("embedding_runtime") or {}).get("fallback_reason") for m in manifests
             if (m.get("embedding_runtime") or {}).get("fallback_reason")), None),
        "retrieval_calls_hybrid": emb_hybrid,
        "retrieval_calls_tfidf_only": emb_tfidf,
        "pct_calls_hybrid": round(emb_hybrid / emb_total, 4) if emb_total else None,
    }

    merged = {
        "run_id": f"merge_{datetime.now():%Y%m%d_%H%M%S}",
        "entrypoint": "scripts/merge_partial_runs.py",
        "preset": manifests[0].get("preset"),
        "status": "complete_merged",
        # Asal-usul tiap potongan: inti ketertelusuran berkas gabungan ini.
        "merged_from": [
            {
                "results_file": str(p),
                "run_id": m.get("run_id"),
                "reports": m.get("reports_processed"),
                "status_asli": m.get("status"),
                "started_at": m.get("started_at"),
                "duration_seconds": m.get("duration_seconds"),
                "base_url": (m.get("env") or {}).get("LOCAL_LLM_BASE_URL"),
                "git_commit": (m.get("git") or {}).get("commit"),
            }
            for p, m in zip(paths, manifests)
        ],
        "merge_warnings": warnings,
        "merge_note": (
            "Digabung dari beberapa potongan run. Sah karena tiap laporan diproses "
            "independen dan seluruh variabel yang memengaruhi prediksi terverifikasi "
            "identik. Metrik dihitung ULANG dari hasil gabungan."
        ),
        "git": manifests[0].get("git"),
        "env": manifests[0].get("env"),
        "attck": manifests[0].get("attck"),
        "reports_processed": len(all_results),
        "reports_failed": sum(int(m.get("reports_failed") or 0) for m in manifests),
        "duration_seconds": round(sum(float(m.get("duration_seconds") or 0)
                                      for m in manifests), 1),
        "metrics": {"technique": metrics, "tactic": tactic_metrics},
        "coverage_stats": _coverage_summary(all_results),
        "rank_alignment": _rank_summary(all_results),
        "embedding_runtime": embedding_runtime,
        "results_file": str(out_path),
    }
    merged.update(_reviewer_summary(all_results))

    if embedding_runtime["fallback_triggered"]:
        print("\n[PERINGATAN] salah satu potongan mengalami fallback embedding ke TF-IDF — "
              "retrieval TIDAK hibrida sepanjang run gabungan ini.")

    write_manifest(out_path, merged)
    rank = merged["rank_alignment"]
    print(f"\npct_outside_top30 = {rank.get('pct_outside_top30')} "
          f"| median peringkat = {rank.get('median_tfidf_rank')} "
          f"| dibuang filter = {rank.get('filtered_out_by_accept_top_n')}")
    print(f"retrieval hibrida = {embedding_runtime['pct_calls_hybrid']}")


if __name__ == "__main__":
    main()
