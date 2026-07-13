"""TUGAS 2 — Recall ceiling retrieval (untuk analisis FN).

Rekonstruksi daftar top-50 kandidat TF-IDF per laporan (fungsi retrieval yang
sama dengan sistem), lalu hitung berapa persen teknik GT (exact & base) yang
ADA di dalam 50 kandidat itu, diagregasi seluruh laporan = PLAFON RECALL
retrieval.

Ini memisahkan "gagal retrieval" (GT tak ada di kandidat -> mustahil benar oleh
tahap reasoning apa pun) dari "gagal reasoning" (GT ada di kandidat tapi LLM
tak memilihnya).

Offline & deterministik. Jalankan:
    python scripts/retrieval_ceiling.py
"""
import json

from eval_common import (
    ATTCK_SOURCE, DATA_DIR, METRICS_DIR, CANDIDATE_TOP_K,
    base_technique, build_candidate_map,
)
from knowledge.attck_loader import load_attck_techniques
from knowledge.data_loader import load_tram_dataset

# K yang ditinjau; 50 = plafon utama (jumlah kandidat yang benar-benar dilihat LLM).
K_VALUES = [10, 20, 50]


def _candidate_union(cand, k):
    """Kandidat yang dilihat sistem pada plafon k.

    cand bisa berupa list ID (mode whole-report) atau list-of-list per chunk
    (mode per-chunk); untuk per-chunk, plafon = UNION top-k tiap chunk, karena
    tiap chunk dikirim ke LLM dengan daftar kandidatnya sendiri.
    """
    if cand and isinstance(cand[0], list):
        union = set()
        for chunk_cands in cand:
            union.update(chunk_cands[:k])
        return union
    return set(cand[:k])


def _ceiling_at_k(reports, candidate_map, k, base):
    """Plafon recall pada top-k: (GT tercakup) / (total GT) diagregasi antar laporan."""
    total_gt = 0
    covered = 0
    for r in reports:
        cand = _candidate_union(candidate_map[r["id"]], k)
        gt = {base_technique(t) for t in r["techniques"]} if base else set(r["techniques"])
        cand_set = {base_technique(c) for c in cand} if base else set(cand)
        total_gt += len(gt)
        covered += len(gt & cand_set)
    ceiling = covered / total_gt if total_gt else 0.0
    return {
        "k": k,
        "gt_total": total_gt,
        "gt_covered": covered,
        "gt_missed": total_gt - covered,
        "recall_ceiling": round(ceiling, 4),
    }


def main():
    attck_techniques = load_attck_techniques(ATTCK_SOURCE)
    reports = load_tram_dataset(DATA_DIR)
    print(f"KB: {len(attck_techniques)} teknik | Laporan: {len(reports)}")

    # Dua mode: "whole" = satu retrieval untuk seluruh laporan (perbandingan
    # historis / baseline); "per_chunk" = kandidat per potongan laporan,
    # mencerminkan apa yang BENAR-BENAR dilihat sistem sejak upgrade retrieval.
    print("\nMerekonstruksi kandidat TF-IDF top-50 per laporan (whole-report)...")
    candidate_map_whole = build_candidate_map(reports, attck_techniques, top_k=CANDIDATE_TOP_K)
    print("Merekonstruksi kandidat TF-IDF top-50 per chunk (mode sistem)...")
    candidate_map_chunk = build_candidate_map(
        reports, attck_techniques, top_k=CANDIDATE_TOP_K, per_chunk=True
    )

    out = {}
    for mode, cmap in (("whole", candidate_map_whole), ("per_chunk", candidate_map_chunk)):
        out[mode] = {"exact": {}, "base": {}}
        for base_mode, key in ((False, "exact"), (True, "base")):
            for k in K_VALUES:
                out[mode][key][str(k)] = _ceiling_at_k(reports, cmap, k, base_mode)

    # --- Tabel markdown ---
    print("\n### Plafon recall retrieval (GT yang muncul di kandidat TF-IDF)")
    print("| Retrieval | Mode | K | GT total | GT tercakup | GT hilang | Plafon recall |")
    print("|---|---|---|---|---|---|---|")
    for mode, mode_label in (("whole", "whole-report"), ("per_chunk", "per-chunk (sistem)")):
        for key, label in (("exact", "EXACT"), ("base", "BASE")):
            for k in K_VALUES:
                c = out[mode][key][str(k)]
                print(
                    f"| {mode_label} | {label} | {k} | {c['gt_total']} | {c['gt_covered']} | "
                    f"{c['gt_missed']} | {c['recall_ceiling']:.4f} |"
                )

    c50e = out["per_chunk"]["exact"]["50"]
    c50b = out["per_chunk"]["base"]["50"]
    print(
        f"\nInterpretasi (per-chunk, top-50): dari {c50e['gt_total']} label GT exact, "
        f"{c50e['gt_covered']} ada di kandidat ({c50e['recall_ceiling']*100:.1f}%); "
        f"{c50e['gt_missed']} MUSTAHIL benar (retrieval-miss). "
        f"BASE: plafon {c50b['recall_ceiling']*100:.1f}% "
        f"({c50b['gt_missed']} base-technique tak terjangkau retrieval)."
    )

    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "dataset": {"reports": len(reports), "kb_techniques": len(attck_techniques)},
        "k_values": K_VALUES,
        "ceiling": out,
    }
    out_path = METRICS_DIR / "retrieval_ceiling.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDisimpan: {out_path}")


if __name__ == "__main__":
    main()
