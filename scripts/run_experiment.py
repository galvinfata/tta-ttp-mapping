"""Jalankan pipeline dengan satu PRESET konfigurasi eksperimen.

    python scripts/run_experiment.py --preset B --reports 30

Menghasilkan DUA berkas:
    results/predictions/exp_<preset>_<timestamp>.json
    results/predictions/exp_<preset>_<timestamp>.json.manifest.json

Berkas hasil lama tidak pernah disentuh: nama selalu ber-timestamp, dan skrip
menolak menimpa berkas yang sudah ada.

Preset dimuat dari experiments/<nama>.env dan MENIMPA .env — karena itu preset
harus dibaca SEBELUM modul pipeline di-import (konstanta seperti ukuran chunk
dibaca saat import). Urutan import di bawah karenanya disengaja dan tidak boleh
dirapikan oleh linter.
"""
import argparse
import atexit
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "experiments"
sys.path.insert(0, str(PROJECT_ROOT / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def resolve_preset(name: str) -> Path:
    """Terima nama pendek ('B'), nama berkas, atau path lengkap."""
    candidate = Path(name)
    if candidate.is_file():
        return candidate

    matches = sorted(EXPERIMENTS_DIR.glob(f"{name}*.env"))
    if not matches:
        matches = sorted(EXPERIMENTS_DIR.glob(f"*{name}*.env"))
    if not matches:
        available = ", ".join(p.name for p in sorted(EXPERIMENTS_DIR.glob("*.env")))
        raise SystemExit(f"Preset '{name}' tidak ditemukan. Tersedia: {available or '(kosong)'}")
    if len(matches) > 1:
        raise SystemExit(
            f"Preset '{name}' ambigu: {', '.join(p.name for p in matches)}"
        )
    return matches[0]


# Variabel INFRASTRUKTUR: menunjuk ke mana server berada, bukan parameter
# eksperimen. Boleh di-override dari luar tanpa menyentuh berkas preset, karena
# mengubahnya TIDAK mengubah apa yang diukur — hanya alamat mesinnya. Berguna
# saat jaringan berpindah (mis. Tailscale -> LAN langsung) sementara preset
# harus tetap menjadi rekaman konfigurasi eksperimen yang tidak berubah.
_INFRA_KEYS = {"LOCAL_LLM_BASE_URL", "LOCAL_LLM_API_KEY", "TRAM_DATA_DIR"}


def load_preset(path: Path, base_url: str = "") -> tuple[dict, list[str]]:
    """Muat preset ke os.environ dengan OVERRIDE, kecuali variabel infrastruktur.

    load_dotenv() di dalam modul-modul agen tidak menimpa nilai yang sudah ada
    di os.environ, sehingga menyetel di sini membuat preset menang atas .env.

    Urutan prioritas untuk variabel infrastruktur (_INFRA_KEYS):
        1. argumen --base-url
        2. environment variable yang sudah di-set di shell sebelum menjalankan
        3. nilai di berkas preset

    Returns: (nilai yang diterapkan, daftar catatan override untuk dicetak).
    """
    from dotenv import dotenv_values

    values = {k: v for k, v in dotenv_values(path).items() if v is not None}
    notes: list[str] = []

    # Variabel infrastruktur yang sudah di-set di shell tidak boleh ditimpa preset.
    for key in _INFRA_KEYS:
        shell_value = os.environ.get(key)
        if shell_value and key in values and shell_value != values[key]:
            notes.append(f"{key}: preset '{values[key]}' DIABAIKAN -> pakai '{shell_value}' (dari environment)")
            values[key] = shell_value

    if base_url:
        preset_url = dotenv_values(path).get("LOCAL_LLM_BASE_URL", "")
        notes.append(f"LOCAL_LLM_BASE_URL: preset '{preset_url}' DIABAIKAN -> pakai '{base_url}' (dari --base-url)")
        values["LOCAL_LLM_BASE_URL"] = base_url

    os.environ.update(values)
    return values, notes


LOCK_PATH = PROJECT_ROOT / "results" / "predictions" / ".run_experiment.lock"


def _pid_alive(pid: int) -> bool:
    """True bila proses pid masih berjalan.

    Sengaja TIDAK memakai os.kill(pid, 0): di Windows, os.kill hanya mendukung
    sinyal CTRL_C_EVENT/CTRL_BREAK_EVENT dan selain itu justru MEMBUNUH proses.
    """
    import subprocess

    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=15,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return True  # tidak bisa memastikan -> anggap hidup (aman)
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_lock(preset_name: str, force: bool = False) -> None:
    """Tolak berjalan bila run eksperimen lain sedang aktif.

    Alasan: 6 Agustus 2026 dua salinan preset G berjalan serentak (rantai bash
    lama yang tidak benar-benar mati + rantai penggantinya). LM Studio membagi
    context window 8192 menjadi slot per permintaan, sehingga prompt 5.018 token
    SELALU ditolak 'Context size has been exceeded' dan seluruh prediksi kosong.
    Kerusakannya senyap: run tetap berjalan 93 menit dan tetap menulis berkas.
    """
    if LOCK_PATH.exists():
        try:
            info = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
            other_pid = int(info.get("pid", 0))
        except (OSError, ValueError, json.JSONDecodeError):
            info, other_pid = {}, 0

        if other_pid and _pid_alive(other_pid) and other_pid != os.getpid():
            if not force:
                raise SystemExit(
                    f"\n[DIBATALKAN] Run eksperimen lain sedang berjalan:\n"
                    f"  pid {other_pid} | preset {info.get('preset')} | mulai {info.get('started')}\n"
                    f"  berkas kunci: {LOCK_PATH}\n\n"
                    f"Menjalankan dua run serentak pada satu server LM Studio membuat\n"
                    f"context window terbagi dan SELURUH prompt ditolak — hasilnya kosong.\n"
                    f"Tunggu run itu selesai, atau hentikan prosesnya lebih dulu.\n"
                    f"Bila yakin pid itu sudah mati: hapus berkas kunci, atau pakai --force.\n"
                )
            print(f"[LOCK] --force: mengabaikan run lain (pid {other_pid}).")
        else:
            print(f"[LOCK] Berkas kunci usang (pid {other_pid} sudah mati) — diambil alih.")

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(json.dumps({
        "pid": os.getpid(),
        "preset": preset_name,
        "started": datetime.now().isoformat(timespec="seconds"),
    }), encoding="utf-8")


def release_lock() -> None:
    """Lepas kunci hanya bila memang milik proses ini."""
    try:
        info = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        if int(info.get("pid", 0)) == os.getpid():
            LOCK_PATH.unlink()
    except (OSError, ValueError, json.JSONDecodeError):
        pass


def preflight(base_url: str, model: str) -> None:
    """Pastikan server LLM hidup SEBELUM run dimulai; berhenti dengan pesan jelas kalau tidak.

    Tanpa ini, server yang mati baru ketahuan setelah pipeline memuat KB 40 MB
    dan menghabiskan menit-menit pertama pada timeout berulang — persis yang
    terjadi pada run preset A 4 Agustus 2026.
    """
    import json
    import urllib.error
    import urllib.request

    root = base_url.rstrip("/")
    try:
        with urllib.request.urlopen(f"{root}/v1/models", timeout=15) as response:
            available = {m.get("id") for m in json.loads(response.read()).get("data", [])}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SystemExit(
            f"\n[PREFLIGHT GAGAL] Server LLM di {root} tidak menjawab: {str(exc)[:150]}\n"
            f"  - Pastikan LM Studio berjalan di mesin itu dan server-nya START.\n"
            f"  - Kalau mengakses lewat jaringan lain, aktifkan 'Serve on Local Network'\n"
            f"    di LM Studio dan pastikan port 1234 tidak diblokir firewall.\n"
            f"  - Ganti alamat tanpa menyentuh preset: --base-url http://IP:1234\n"
        )

    if model not in available:
        print(f"[PREFLIGHT] PERINGATAN: model '{model}' tidak ada di daftar server. "
              f"Tersedia: {', '.join(sorted(available)) or '(kosong)'}")

    # Context length yang benar-benar dimuat (hanya ada di REST API native LM Studio).
    try:
        with urllib.request.urlopen(f"{root}/api/v0/models", timeout=15) as response:
            for m in json.loads(response.read()).get("data", []):
                if m.get("state") != "not-loaded":
                    print(f"[PREFLIGHT] dimuat: {m.get('id')} "
                          f"(ctx={m.get('loaded_context_length')}, state={m.get('state')})")
    except Exception:
        print("[PREFLIGHT] /api/v0/models tidak tersedia — context length aktif tidak dapat diperiksa.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Jalankan satu preset eksperimen.")
    parser.add_argument("--preset", required=True, help="Nama preset (A/B/C/D) atau path .env")
    parser.add_argument("--reports", type=int, default=30, help="Jumlah laporan yang diproses (default 30)")
    parser.add_argument(
        "--skip", type=int, default=0,
        help="Lewati N laporan pertama, lalu proses --reports berikutnya. "
             "Dipakai untuk melanjutkan run yang terputus: --skip 20 --reports 10 "
             "memproses laporan 21-30. Urutan dataset deterministik (sorted nama berkas), "
             "sehingga potongan ini stabil antar-run. Gabungkan hasilnya dengan "
             "scripts/merge_partial_runs.py, JANGAN disatukan manual.",
    )
    parser.add_argument("--save-every", type=int, default=5, help="Simpan hasil tiap N laporan")
    parser.add_argument("--out", default="", help="Path berkas hasil (opsional)")
    parser.add_argument(
        "--base-url", default="",
        help="Alamat server LLM, menimpa nilai di preset TANPA mengubah berkasnya "
             "(mis. http://192.168.50.2:1234 saat berpindah dari Tailscale ke LAN)",
    )
    parser.add_argument(
        "--skip-preflight", action="store_true",
        help="Lewati pemeriksaan server sebelum run (tidak disarankan)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Jalan meski ada run lain terdeteksi (lihat acquire_lock; hampir selalu salah)",
    )
    args = parser.parse_args()

    preset_path = resolve_preset(args.preset)
    preset_name = preset_path.stem
    acquire_lock(preset_name, force=args.force)
    atexit.register(release_lock)
    applied, override_notes = load_preset(preset_path, base_url=args.base_url)
    print(f"Preset: {preset_path} ({len(applied)} variabel diterapkan)")
    for note in override_notes:
        print(f"  [OVERRIDE] {note}")

    effective_url = os.environ.get("LOCAL_LLM_BASE_URL", "")
    print(f"Server LLM: {effective_url}")
    if not args.skip_preflight:
        preflight(effective_url, os.environ.get("LOCAL_LLM_MODEL", ""))

    # --- Import SETELAH preset diterapkan (lihat docstring modul) ---
    from knowledge.data_loader import load_tram_dataset
    from knowledge.attck_loader import load_attck_techniques, load_attck_tactics
    from agents.tactic_agent import create_tactic_agent
    from agents.technique_agent import create_technique_agent
    from agents.reviewer_agent import create_reviewer_agent
    from agents.prompt_budget import format_prompt_stats
    from agents.retrieval import LOCAL_LLM_REPORT_MAX_CHARS, CHUNK_OVERLAP_CHARS, MAX_CHUNKS
    from pipeline.orchestrator import process_report
    from evaluation.evaluator import (
        derive_tactic_ground_truth,
        evaluate_predictions,
        evaluate_tactics,
        save_results,
    )
    from utils.run_manifest import RunRecorder

    reach = LOCAL_LLM_REPORT_MAX_CHARS + (MAX_CHUNKS - 1) * (LOCAL_LLM_REPORT_MAX_CHARS - CHUNK_OVERLAP_CHARS)
    print(
        f"Chunk {LOCAL_LLM_REPORT_MAX_CHARS} x maks {MAX_CHUNKS} (overlap {CHUNK_OVERLAP_CHARS}) "
        f"-> jangkauan maksimum {reach} char/laporan"
    )

    attck_source = os.getenv("ATTCK_SOURCE", "data/mitre_cti/enterprise-attack.json")
    attck_techniques = load_attck_techniques(attck_source)
    attck_tactics = load_attck_tactics(attck_source)
    all_reports = load_tram_dataset(os.getenv("TRAM_DATA_DIR", "data/tram_ii"))
    reports = all_reports[args.skip: args.skip + args.reports]
    if not reports:
        raise SystemExit(
            f"Tidak ada laporan pada potongan --skip {args.skip} --reports {args.reports} "
            f"(dataset berisi {len(all_reports)} laporan)."
        )
    if args.skip:
        print(f"Potongan: laporan {args.skip + 1}-{args.skip + len(reports)} dari {len(all_reports)}")
    print(f"KB: {len(attck_techniques)} teknik | Laporan diproses: {len(reports)}")

    reviewer_enable = os.getenv("REVIEWER_ENABLE", "false").lower() == "true"
    tactic_model = create_tactic_agent()
    technique_model = create_technique_agent()
    reviewer_model = create_reviewer_agent() if reviewer_enable else None
    print(f"Reviewer: {'AKTIF' if reviewer_model else 'nonaktif'}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Potongan ditandai di nama berkas supaya tidak pernah tertukar dengan run utuh.
    slice_tag = f"_r{args.skip + 1}-{args.skip + len(reports)}" if args.skip else ""
    output_path = Path(args.out) if args.out else (
        PROJECT_ROOT / "results" / "predictions" / f"exp_{preset_name}{slice_tag}_{timestamp}.json"
    )
    if output_path.exists():
        raise SystemExit(f"Berkas hasil {output_path} sudah ada — dibatalkan agar tidak menimpa.")

    recorder = RunRecorder(entrypoint="scripts/run_experiment.py", preset=preset_name)
    results: list[dict] = []
    start = time.time()

    try:
        for i, report in enumerate(reports, 1):
            elapsed = time.time() - start
            print(f"[{i}/{len(reports)}] ({elapsed / 60:.1f} mnt) {str(report['id'])[:60]}")
            try:
                result = process_report(
                    report=report,
                    attck_techniques=attck_techniques,
                    attck_tactics=attck_tactics,
                    tactic_model=tactic_model,
                    technique_model=technique_model,
                    reviewer_model=reviewer_model,
                )
            except Exception as exc:
                print(f"  [ERROR] {exc}")
                recorder.record_failure(report.get("id", ""))
                gt = report.get("techniques", [])
                result = {
                    "report_id": report.get("id", ""),
                    "predicted_techniques": [],
                    "ground_truth": gt,
                    # GT tetap diketahui meski pipeline gagal, jadi taktiknya
                    # ikut diturunkan — bukan dikosongkan.
                    "ground_truth_tactics": derive_tactic_ground_truth(gt, attck_techniques),
                    "tactics_identified": [],
                    "stix_bundle": {"type": "bundle", "objects": []},
                    "reviewer_invoked": bool(reviewer_model),
                }
            results.append(result)
            if i % args.save_every == 0:
                save_results(results, str(output_path))
                # Manifest ikut ditulis tiap checkpoint. Run yang DIBUNUH di
                # tengah jalan (server mati, proses di-kill) tidak sempat
                # menjalankan blok finalisasi di bawah, dan tanpa ini hasil
                # parsialnya jadi berkas tanpa rekaman konfigurasi apapun.
                recorder.finalize(
                    results, str(output_path),
                    status="partial",
                    preset_file=str(preset_path),
                    reports_requested=args.reports,
                    reports_skipped=args.skip,
                )
            sys.stdout.flush()
    except KeyboardInterrupt:
        print(f"\nDihentikan manual setelah {len(results)} laporan — hasil parsial tetap disimpan.")

    if not results:
        print("Tidak ada hasil untuk disimpan.")
        return

    duration = time.time() - start
    save_results(results, str(output_path))

    metrics = evaluate_predictions(results, attck_techniques)
    tactic_metrics = evaluate_tactics(results, attck_techniques)
    print(f"\n=== PRESET {preset_name} | {len(results)} laporan | {duration / 60:.1f} menit ===")
    print(f"exact  P={metrics['precision']} R={metrics['recall']} F1={metrics['micro_f1']}")
    print(f"base   P={metrics['base_precision']} R={metrics['base_recall']} F1={metrics['base_micro_f1']}")
    print(f"taktik P={tactic_metrics['tactic_precision']} R={tactic_metrics['tactic_recall']} "
          f"F1={tactic_metrics['tactic_micro_f1']}")
    print()
    print(format_prompt_stats())

    recorder.finalize(
        results, str(output_path),
        metrics={"technique": metrics, "tactic": tactic_metrics},
        preset_file=str(preset_path),
        preset_values=applied,
        reviewer_enable_env=reviewer_enable,
        reports_requested=args.reports,
        reports_skipped=args.skip,
        # Override infrastruktur dicatat eksplisit: berkas preset menyebut satu
        # alamat, run ini mungkin memakai alamat lain (mis. pindah jaringan).
        # Tanpa ini, manifest terlihat konsisten padahal preset dan run berbeda.
        infra_overrides=override_notes or None,
    )


if __name__ == "__main__":
    main()
