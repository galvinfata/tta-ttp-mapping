import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from knowledge.data_loader import load_tram_dataset, split_dataset
from knowledge.attck_loader import load_attck_techniques, load_attck_tactics
from agents.tactic_agent import create_tactic_agent
from agents.technique_agent import create_technique_agent
from agents.reviewer_agent import create_reviewer_agent
from pipeline.orchestrator import process_report
from evaluation.evaluator import evaluate_predictions, save_results


def validate_setup() -> bool:
    """Validasi konfigurasi wajib sebelum pipeline dijalankan."""
    ok = True
    provider = os.getenv("LLM_PROVIDER", "lmstudio").strip().lower()

    tram_dir = Path("data/tram_ii")
    mitre_dir = Path("data/mitre_cti")
    has_local_base_url = bool(os.getenv("LOCAL_LLM_BASE_URL"))

    tram_files = []
    if tram_dir.exists():
        tram_files = (
            list(tram_dir.glob("*.json"))
            + list(tram_dir.glob("*.mjson"))
            + list(tram_dir.glob("*.pdf"))
        )

    if not tram_files:
        ok = False
        print("[SETUP] Dataset TRAM II belum ada.")
        print("        Letakkan file .json, .mjson, atau .pdf laporan di folder: data/tram_ii")

    mitre_files = []
    if mitre_dir.exists():
        mitre_files = list(mitre_dir.glob("*.json"))

    if not mitre_files:
        ok = False
        print("[SETUP] File MITRE CTI belum ada.")
        print("        Letakkan file ATT&CK (.json) di folder: data/mitre_cti")

    if provider == "lmstudio" and not has_local_base_url:
        ok = False
        print("[SETUP] LOCAL_LLM_BASE_URL belum di-set untuk mode LM Studio.")
        print("        Isi di .env, contoh: LOCAL_LLM_BASE_URL=http://100.100.211.39:1234")

    if not ok:
        print("\nSetup belum lengkap. Perbaiki dulu, lalu jalankan lagi: python main.py")

    return ok


def main():
    print("=== TTP Mapping System ===\n")
    provider = os.getenv("LLM_PROVIDER", "lmstudio").strip().lower()
    print(f"Provider LLM aktif: {provider}")

    if not validate_setup():
        return
    
    # 1. Load data
    print("1. Loading dataset TRAM II...")
    reports = load_tram_dataset("data/tram_ii")
    print(f"   Total laporan: {len(reports)}")
    
    # Gunakan subset kecil dulu untuk testing
    test_reports = reports[:5]
    
    # 2. Load ATT&CK knowledge base
    print("\n2. Loading MITRE ATT&CK knowledge base...")
    attck_source = os.getenv("ATTCK_SOURCE", "data/mitre_cti/enterprise-attack.json")
    attck_techniques = load_attck_techniques(attck_source)
    attck_tactics = load_attck_tactics(attck_source)
    print(f"   Total teknik: {len(attck_techniques)}")
    print(f"   Total taktik: {len(attck_tactics)}")
    
    # 3. Inisialisasi agen
    print("\n3. Inisialisasi agen...")
    tactic_model = create_tactic_agent()
    technique_model = create_technique_agent()
    reviewer_enable = os.getenv("REVIEWER_ENABLE", "false").lower() == "true"
    reviewer_model = create_reviewer_agent() if reviewer_enable else None
    if reviewer_enable:
        print("   Reviewer (multi-agent debate loop) AKTIF")
    print("   Agen siap.")

    # 4. Proses laporan
    print("\n4. Memproses laporan CTI...")
    results = []

    for report in test_reports:
        result = process_report(
            report=report,
            attck_techniques=attck_techniques,
            attck_tactics=attck_tactics,
            tactic_model=tactic_model,
            technique_model=technique_model,
            reviewer_model=reviewer_model
        )
        results.append(result)
    
    # 5. Evaluasi
    print("\n5. Evaluasi hasil...")
    metrics = evaluate_predictions(results)
    
    print("\n=== HASIL EVALUASI ===")
    print(f"Precision : {metrics['precision']}")
    print(f"Recall    : {metrics['recall']}")
    print(f"Micro-F1  : {metrics['micro_f1']}")
    print(f"Total     : {metrics['total_reports']} laporan")
    
    # 6. Simpan hasil
    save_results(results, "results/predictions/results.json")


if __name__ == "__main__":
    main()