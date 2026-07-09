"""Smoke test end-to-end reviewer ON pada N laporan pertama TRAM II.

Butuh LM Studio hidup di LOCAL_LLM_BASE_URL. Jalankan dari root proyek:
    python scripts/smoke_reviewer.py          # N default 2
    SMOKE_N=3 python scripts/smoke_reviewer.py

Menyimpan hasil ke results/predictions/smoke_reviewer_<ts>.json lalu langsung
mengevaluasinya dengan evaluator yang sama dengan skrip evaluasi penuh, untuk
membuktikan file hasil (dengan field aditif reviewer_error_count/reviewer_errored)
tetap terbaca.
"""
import os
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from knowledge.data_loader import load_tram_dataset
from knowledge.attck_loader import load_attck_techniques, load_attck_tactics
from agents.tactic_agent import create_tactic_agent
from agents.technique_agent import create_technique_agent
from agents.reviewer_agent import create_reviewer_agent
from pipeline.orchestrator import process_report
from evaluation.evaluator import evaluate_predictions, save_results

ATTCK_SOURCE = os.getenv("ATTCK_SOURCE", "data/mitre_cti/enterprise-attack.json")
N = int(os.getenv("SMOKE_N", "2"))


def main():
    reports = load_tram_dataset("data/tram_ii")[:N]
    att = load_attck_techniques(ATTCK_SOURCE)
    tactics = load_attck_tactics(ATTCK_SOURCE)
    print(f"KB: {len(att)} teknik | Smoke pada {len(reports)} laporan | reviewer ON")

    tactic_model = create_tactic_agent()
    technique_model = create_technique_agent()
    reviewer_model = create_reviewer_agent()

    results = []
    start = time.time()
    for i, r in enumerate(reports, 1):
        print(f"[{i}/{len(reports)}] {r['id'][:60]}")
        res = process_report(r, att, tactics, tactic_model, technique_model, reviewer_model)
        results.append(res)
    print(f"Run time: {round(time.time() - start, 1)}s")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = f"results/predictions/smoke_reviewer_{ts}.json"
    save_results(results, out_path)

    # Bukti kompatibilitas: file hasil dibaca ulang oleh evaluator yang sama.
    metrics = evaluate_predictions(results)
    print("\n=== SMOKE METRICS (bukan angka riset, hanya cek jalur) ===")
    print(f"Precision: {metrics['precision']} | Recall: {metrics['recall']} | F1: {metrics['micro_f1']}")
    total_err = sum(r.get("reviewer_error_count", 0) for r in results)
    errored = sum(1 for r in results if r.get("reviewer_errored"))
    print(f"Reviewer error count total: {total_err} | laporan dengan error reviewer: {errored}/{len(results)}")
    print(f"Hasil: {out_path}")


if __name__ == "__main__":
    main()
