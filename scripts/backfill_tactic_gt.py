"""Tambahkan field `ground_truth_tactics` ke berkas hasil prediksi lama.

Sejak 2026-08-08 `process_report()` menyimpan GT taktik (diturunkan dari GT
teknik lewat fase kill-chain) langsung di tiap record. Berkas hasil yang dibuat
SEBELUM itu — termasuk kelima preset A/G/H/E/F — belum punya field tersebut.

Skrip ini mengisinya tanpa menjalankan ulang apa pun: derivasinya deterministik
dari `ground_truth` + KB ATT&CK, memakai fungsi yang sama persis
(`derive_tactic_ground_truth`) yang selama ini dipakai `evaluate_tactics()`.
Jadi angka evaluasi taktik TIDAK berubah — yang berubah hanya keterbacaan berkas.

Pemakaian:
    python scripts/backfill_tactic_gt.py results/predictions/exp_*.json
    python scripts/backfill_tactic_gt.py <berkas> --dry-run     # lihat dulu
    python scripts/backfill_tactic_gt.py <berkas> --overwrite   # timpa yang sudah ada
"""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from evaluation.evaluator import derive_tactic_ground_truth  # noqa: E402
from knowledge.attck_loader import load_attck_techniques  # noqa: E402


def backfill_file(path: Path, attck_techniques: dict, overwrite: bool, dry_run: bool) -> dict:
    with path.open(encoding="utf-8") as fh:
        records = json.load(fh)

    if not isinstance(records, list):
        return {"path": path, "status": "dilewati", "detail": "bukan daftar record"}

    added = skipped = changed = 0
    no_gt = 0
    for rec in records:
        if not isinstance(rec, dict) or "ground_truth" not in rec:
            continue
        gt = rec.get("ground_truth") or []
        if not gt:
            no_gt += 1
        derived = derive_tactic_ground_truth(gt, attck_techniques)

        if "ground_truth_tactics" in rec:
            if not overwrite:
                skipped += 1
                continue
            if rec["ground_truth_tactics"] != derived:
                changed += 1
            rec["ground_truth_tactics"] = derived
        else:
            rec["ground_truth_tactics"] = derived
            added += 1

    if not dry_run and (added or changed):
        # Tulis via berkas sementara lalu ganti, supaya berkas hasil tidak
        # rusak separuh jalan kalau proses mati di tengah penulisan.
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

    return {
        "path": path,
        "status": "ok",
        "records": len(records),
        "added": added,
        "skipped": skipped,
        "changed": changed,
        "no_gt": no_gt,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", help="Berkas hasil prediksi (.json)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Hitung ulang field yang sudah ada (default: dilewati)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Tampilkan yang akan berubah tanpa menulis berkas")
    args = parser.parse_args()

    attck_source = os.getenv("ATTCK_SOURCE", str(PROJECT_ROOT / "data/mitre_cti/enterprise-attack.json"))
    attck_techniques = load_attck_techniques(attck_source)
    print(f"KB: {len(attck_techniques)} teknik (source: {attck_source})")
    if args.dry_run:
        print("[DRY-RUN] tidak ada berkas yang ditulis\n")

    for raw in args.files:
        path = Path(raw)
        if not path.exists():
            print(f"  [LEWAT] tidak ada: {path}")
            continue
        if path.name.endswith(".manifest.json"):
            continue  # manifest tidak memuat record per-laporan
        r = backfill_file(path, attck_techniques, args.overwrite, args.dry_run)
        if r["status"] != "ok":
            print(f"  [LEWAT] {path.name}: {r['detail']}")
            continue
        note = f" | {r['no_gt']} record tanpa GT" if r["no_gt"] else ""
        print(f"  {path.name}: {r['records']} record | +{r['added']} ditambah "
              f"| {r['skipped']} sudah ada | {r['changed']} diubah{note}")


if __name__ == "__main__":
    main()
