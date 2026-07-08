"""Harness evaluasi TTP mapping.

Dua mode pemakaian:
  1. Evaluasi file hasil yang sudah ada:
        python src/evaluate_run.py results/predictions/results_all_xxx.json
  2. Jalankan pipeline pada N laporan lalu evaluasi (live ke LM Studio):
        python src/evaluate_run.py            # N default = EVAL_N atau 5

Mencetak: metrik teknik (exact + base-technique) dan metrik taktik
(ground-truth taktik diturunkan dari teknik GT).
"""
import os
import sys
import json
import time
from datetime import datetime
from pathlib import Path

# src/evaluation/evaluate_run.py -> tambahkan folder src ke path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledge.attck_loader import load_attck_techniques, load_attck_tactics
from evaluation.evaluator import evaluate_predictions, evaluate_tactics, save_results

ATTCK_SOURCE = os.getenv("ATTCK_SOURCE", "data/mitre_cti/enterprise-attack.json")


def _print_metrics(results: list[dict], attck_techniques: dict) -> None:
    tech = evaluate_predictions(results)
    tac = evaluate_tactics(results, attck_techniques)

    print("\n=== METRIK ===")
    print(f"Reports dievaluasi : {tech['total_reports']}")
    print("--- Teknik (exact, termasuk sub-teknik) ---")
    print(f"  Precision : {tech['precision']}")
    print(f"  Recall    : {tech['recall']}")
    print(f"  Micro-F1  : {tech['micro_f1']}")
    print("--- Teknik (base-technique, abaikan sub) ---")
    print(f"  Precision : {tech['base_precision']}")
    print(f"  Recall    : {tech['base_recall']}")
    print(f"  Micro-F1  : {tech['base_micro_f1']}")
    print("--- Taktik (GT diturunkan dari teknik) ---")
    print(f"  Precision : {tac['tactic_precision']}")
    print(f"  Recall    : {tac['tactic_recall']}")
    print(f"  Micro-F1  : {tac['tactic_micro_f1']}")


def _run_live(n: int, attck_techniques: dict, attck_tactics: dict) -> list[dict]:
    from knowledge.data_loader import load_tram_dataset
    from agents.tactic_agent import create_tactic_agent
    from agents.technique_agent import create_technique_agent
    from pipeline.orchestrator import process_report

    reports = load_tram_dataset("data/tram_ii")[:n]
    tactic_model = create_tactic_agent()
    technique_model = create_technique_agent()

    results = []
    start = time.time()
    for i, r in enumerate(reports, 1):
        print(f"[{i}/{len(reports)}] {r['id'][:60]}")
        try:
            res = process_report(r, attck_techniques, attck_tactics, tactic_model, technique_model)
        except Exception as e:
            print(f"  [ERROR] {e}")
            res = {
                "report_id": r.get("id", ""),
                "predicted_techniques": [],
                "ground_truth": r.get("techniques", []),
                "tactics_identified": [],
                "stix_bundle": {"type": "bundle", "objects": []},
            }
        results.append(res)
        sys.stdout.flush()
    print(f"Run time: {round(time.time() - start, 1)}s")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = f"results/predictions/eval_run_{ts}.json"
    save_results(results, out_path)
    return results


def main():
    attck_techniques = load_attck_techniques(ATTCK_SOURCE)
    attck_tactics = load_attck_tactics(ATTCK_SOURCE)
    print(f"KB: {len(attck_tactics)} taktik, {len(attck_techniques)} teknik (source: {ATTCK_SOURCE})")

    if len(sys.argv) > 1:
        results_path = sys.argv[1]
        with open(results_path, "r", encoding="utf-8") as f:
            results = json.load(f)
        print(f"Memuat {len(results)} hasil dari {results_path}")
    else:
        n = int(os.getenv("EVAL_N", "5"))
        results = _run_live(n, attck_techniques, attck_tactics)

    _print_metrics(results, attck_techniques)


if __name__ == "__main__":
    main()
