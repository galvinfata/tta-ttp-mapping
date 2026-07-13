import sys
import os
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from knowledge.data_loader import load_tram_dataset, split_dataset
from knowledge.attck_loader import load_attck_techniques, load_attck_tactics
from agents.tactic_agent import create_tactic_agent
from agents.technique_agent import create_technique_agent
from agents.reviewer_agent import create_reviewer_agent
from pipeline.orchestrator import process_report
from evaluation.evaluator import evaluate_predictions, evaluate_tactics, save_results

# Simpan hasil ke file setiap N laporan agar run panjang yang terputus
# (Ctrl+C, server mati) tidak kehilangan seluruh hasil — pelajaran dari run
# 10 Juli 2026 yang dihentikan di laporan ke-24 tanpa JSON tersimpan.
SAVE_EVERY_N_REPORTS = 5


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


def _choose_report_count(total: int) -> int:
    """Tentukan jumlah laporan yang diproses.

    Prioritas: argumen CLI (`python main.py 20` / `python main.py all`),
    lalu tanya interaktif. Enter tanpa isian = 5 (subset uji cepat).
    """
    default_n = min(5, total)

    if len(sys.argv) > 1:
        arg = sys.argv[1].strip().lower()
        if arg in ("all", "semua"):
            return total
        try:
            n = int(arg)
            if n > 0:
                return min(n, total)
        except ValueError:
            pass
        print(f"Argumen '{sys.argv[1]}' tidak dikenal — pakai angka atau 'all'.")

    while True:
        try:
            raw = input(
                f"Mau proses berapa laporan? (1-{total}, 'all' = semua, "
                f"Enter = {default_n}): "
            ).strip().lower()
        except EOFError:
            # stdin non-interaktif (mis. dijalankan dari skrip) -> pakai default.
            print(f"Input tidak tersedia, pakai default {default_n} laporan.")
            return default_n
        if raw == "":
            return default_n
        if raw in ("all", "semua"):
            return total
        try:
            n = int(raw)
        except ValueError:
            print("Masukkan angka, 'all', atau tekan Enter.")
            continue
        if n > 0:
            return min(n, total)
        print("Jumlah harus lebih dari 0.")


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

    # User memilih jumlah laporan (argumen CLI atau interaktif).
    n_reports = _choose_report_count(len(reports))
    test_reports = reports[:n_reports]
    print(f"   Akan diproses: {n_reports} dari {len(reports)} laporan")

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
    # Nama file ber-timestamp agar run baru tidak menimpa hasil run sebelumnya.
    output_path = (
        f"results/predictions/results_main_{datetime.now():%Y%m%d_%H%M%S}.json"
    )

    try:
        for i, report in enumerate(test_reports, 1):
            print(f"\n[{i}/{len(test_reports)}]", end=" ")
            result = process_report(
                report=report,
                attck_techniques=attck_techniques,
                attck_tactics=attck_tactics,
                tactic_model=tactic_model,
                technique_model=technique_model,
                reviewer_model=reviewer_model
            )
            results.append(result)
            # Simpan incremental agar run yang terputus tidak kehilangan hasil.
            if i % SAVE_EVERY_N_REPORTS == 0:
                save_results(results, output_path)
    except KeyboardInterrupt:
        print(f"\nRun dihentikan manual di laporan ke-{len(results) + 1}. "
              f"Hasil {len(results)} laporan yang selesai tetap dievaluasi & disimpan.")

    if not results:
        print("Tidak ada laporan yang selesai diproses.")
        return

    # 5. Evaluasi
    print("\n5. Evaluasi hasil...")
    metrics = evaluate_predictions(results, attck_techniques)
    tactic_metrics = evaluate_tactics(results, attck_techniques)

    print("\n=== HASIL EVALUASI ===")
    print(f"Reports dievaluasi : {metrics['total_reports']}")
    print("--- Teknik (exact, termasuk sub-teknik) ---")
    print(f"  Precision : {metrics['precision']}")
    print(f"  Recall    : {metrics['recall']}")
    print(f"  Micro-F1  : {metrics['micro_f1']}")
    print(f"  Accuracy  : {metrics['accuracy']}")
    print("--- Teknik (base-technique, abaikan sub) ---")
    print(f"  Precision : {metrics['base_precision']}")
    print(f"  Recall    : {metrics['base_recall']}")
    print(f"  Micro-F1  : {metrics['base_micro_f1']}")
    print(f"  Accuracy  : {metrics['base_accuracy']}")
    print("--- Taktik (GT diturunkan dari teknik) ---")
    print(f"  Precision : {tactic_metrics['tactic_precision']}")
    print(f"  Recall    : {tactic_metrics['tactic_recall']}")
    print(f"  Micro-F1  : {tactic_metrics['tactic_micro_f1']}")
    print(f"  Accuracy  : {tactic_metrics['tactic_accuracy']}")

    # 6. Simpan hasil
    save_results(results, output_path)


if __name__ == "__main__":
    main()