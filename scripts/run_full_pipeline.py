import os, sys, json, time
from datetime import datetime
from pathlib import Path

# scripts/run_full_pipeline.py -> tambahkan folder src (satu level di atas) ke path.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from knowledge.data_loader import load_tram_dataset
from knowledge.attck_loader import load_attck_techniques, load_attck_tactics
from agents.tactic_agent import create_tactic_agent
from agents.technique_agent import create_technique_agent
from agents.reviewer_agent import create_reviewer_agent
from pipeline.orchestrator import process_report
from evaluation.evaluator import evaluate_predictions, save_results
from agents.prompt_budget import format_prompt_stats
from utils.run_manifest import RunRecorder

# Default fokus ke matrix Enterprise saja agar tidak tercampur teknik/taktik
# Mobile & PRE (yang memunculkan ID taktik invalid seperti TA0027).
ATTCK_SOURCE = os.getenv('ATTCK_SOURCE', 'data/mitre_cti/enterprise-attack.json')
# Reviewer (loop debat multi-agent) opt-in karena menambah panggilan LLM/laporan.
REVIEWER_ENABLE = os.getenv('REVIEWER_ENABLE', 'false').lower() == 'true'

reports = load_tram_dataset('data/tram_ii')
att = load_attck_techniques(ATTCK_SOURCE)
tactics = load_attck_tactics(ATTCK_SOURCE)

tactic_model = create_tactic_agent()
technique_model = create_technique_agent()
reviewer_model = create_reviewer_agent() if REVIEWER_ENABLE else None
if REVIEWER_ENABLE:
    print('Reviewer (multi-agent debate loop) AKTIF')

recorder = RunRecorder(entrypoint="scripts/run_full_pipeline.py")
results = []
start = time.time()
for i, r in enumerate(reports, 1):
    print(f"Processing {i}/{len(reports)}: {r['id']}")
    try:
        res = process_report(r, att, tactics, tactic_model, technique_model, reviewer_model)
    except Exception as e:
        print(f"[ERROR] processing {r['id']}: {e}")
        recorder.record_failure(r.get('id', ''))
        res = {
            'report_id': r.get('id', ''),
            'predicted_techniques': [],
            'ground_truth': r.get('techniques', []),
            'tactics_identified': [],
            'stix_bundle': {'type': 'bundle', 'objects': []}
        }
    results.append(res)
    sys.stdout.flush()
end = time.time()
print('Run time seconds:', round(end-start,2))

timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
out_path = f'results/predictions/results_all_{timestamp}.json'
save_results(results, out_path)
metrics = evaluate_predictions(results)
print('\n=== METRICS ===')
print('Reports processed:', len(results))
print('Precision:', metrics['precision'])
print('Recall   :', metrics['recall'])
print('Micro-F1 :', metrics['micro_f1'])
print('Saved to', out_path)
print()
print(format_prompt_stats())
recorder.finalize(results, out_path, metrics={'technique': metrics},
                  reviewer_enable_env=REVIEWER_ENABLE)
