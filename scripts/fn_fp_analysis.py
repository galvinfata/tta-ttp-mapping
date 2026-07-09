"""TUGAS 3 — Ekstraksi & kategorisasi FN/FP (untuk tabel kasus Bab IV).

Dari results/predictions/results_all_20260531_190814.json:
- Join ke rekonstruksi kandidat TF-IDF top-50 (TUGAS 2) berdasarkan report_id.
- Untuk tiap FN, tandai kategori:
    * "retrieval-miss"  -> GT tak masuk 50 kandidat (mustahil benar).
    * "reasoning-miss"  -> GT ada di kandidat tapi tak diprediksi LLM.
- Keluarkan 5 contoh FN & 5 contoh FP nyata: report_id, ID teknik, NAMA teknik
  (dari KB), kategori. Flag ID teknik yang DEPRECATED/REVOKED di ATT&CK.

Catatan data: hasil sistem yang dianalisis (results_all_20260531_190814) dibuat
SEBELUM attck_loader.py menyaring `revoked`, sehingga ID revoked (mis.
T1017/T1077/T1177) sempat bocor menjadi kandidat dan diprediksi sistem.
Status stale di sini dibaca langsung dari STIX (bukan dari keanggotaan KB)
agar kategorisasi tetap benar terlepas dari versi filter di loader.

Offline & deterministik. Jalankan:
    python scripts/fn_fp_analysis.py
"""
import json

from eval_common import (
    ATTCK_SOURCE, DATA_DIR, METRICS_DIR, CANDIDATE_TOP_K, build_candidate_map,
)
from knowledge.attck_loader import load_attck_techniques
from knowledge.data_loader import load_tram_dataset

FULL_SYSTEM_RESULTS = "results/predictions/results_all_20260531_190814.json"


def _load_stale_ids(stix_path: str) -> set:
    """Set technique ID yang revoked ATAU deprecated menurut STIX ATT&CK mentah."""
    data = json.load(open(stix_path, encoding="utf-8"))
    stale = set()
    for obj in data.get("objects", []):
        if obj.get("type") != "attack-pattern":
            continue
        if not (obj.get("revoked") or obj.get("x_mitre_deprecated")):
            continue
        for ref in obj.get("external_references", []):
            eid = ref.get("external_id")
            if str(ref.get("source_name", "")).lower().startswith("mitre") and \
               isinstance(eid, str) and eid.startswith("T"):
                stale.add(eid)
                break
    return stale


def _name_of(tid, kb, stale):
    if tid in kb:
        return kb[tid].get("name", "")
    if tid in stale:
        return "(revoked/deprecated — tak dimuat KB)"
    return "(tidak dikenal di KB)"


def main():
    kb = load_attck_techniques(ATTCK_SOURCE)
    reports = load_tram_dataset(DATA_DIR)
    stale = _load_stale_ids(ATTCK_SOURCE)
    res = json.load(open(FULL_SYSTEM_RESULTS, encoding="utf-8"))
    print(f"KB: {len(kb)} teknik | Laporan: {len(reports)} | Hasil sistem: {len(res)} | "
          f"ID stale (revoked/deprecated): {len(stale)}")

    print("\nMerekonstruksi kandidat TF-IDF top-50 per laporan...")
    candidate_map = build_candidate_map(reports, kb, top_k=CANDIDATE_TOP_K)

    fn_records, fp_records = [], []
    cat_counter = {"retrieval-miss": 0, "reasoning-miss": 0, "no-candidates": 0}

    for r in res:
        rid = r["report_id"]
        pred = set(r.get("predicted_techniques", []))
        gt = set(r.get("ground_truth", []))
        cand = set(candidate_map.get(rid, []))

        # False Negatives: GT yang tak diprediksi -> kategorikan.
        for tid in sorted(gt - pred):
            if not cand:
                category = "no-candidates"
            elif tid in cand:
                category = "reasoning-miss"
            else:
                category = "retrieval-miss"
            cat_counter[category] = cat_counter.get(category, 0) + 1
            fn_records.append({
                "report_id": rid.strip(),
                "technique_id": tid,
                "technique_name": _name_of(tid, kb, stale),
                "category": category,
                "stale": tid in stale,
            })

        # False Positives: prediksi yang bukan GT.
        for tid in sorted(pred - gt):
            fp_records.append({
                "report_id": rid.strip(),
                "technique_id": tid,
                "technique_name": _name_of(tid, kb, stale),
                "stale": tid in stale,
                "in_candidates": tid in cand,
            })

    # --- Pilih 5 contoh FN (campur kategori) & 5 contoh FP (utamakan stale) ---
    reasoning = [x for x in fn_records if x["category"] == "reasoning-miss"]
    retrieval = [x for x in fn_records if x["category"] == "retrieval-miss"]
    fn_examples = (reasoning[:3] + retrieval[:2])
    if len(fn_examples) < 5:
        fn_examples += [x for x in fn_records if x not in fn_examples][:5 - len(fn_examples)]
    fn_examples = fn_examples[:5]

    fp_stale = [x for x in fp_records if x["stale"]]
    fp_other = [x for x in fp_records if not x["stale"]]
    fp_examples = (fp_stale[:3] + fp_other[:2])
    if len(fp_examples) < 5:
        fp_examples += [x for x in fp_records if x not in fp_examples][:5 - len(fp_examples)]
    fp_examples = fp_examples[:5]

    total_fn = len(fn_records)
    total_fp = len(fp_records)

    # --- Tabel markdown ---
    print("\n### Kategorisasi FALSE NEGATIVE (exact, agregat)")
    print("| Kategori | Jumlah | % dari FN |")
    print("|---|---|---|")
    for cat in ("retrieval-miss", "reasoning-miss", "no-candidates"):
        n = cat_counter.get(cat, 0)
        pct = (n / total_fn * 100) if total_fn else 0
        print(f"| {cat} | {n} | {pct:.1f}% |")
    print(f"| **Total FN** | **{total_fn}** | 100% |")

    print(f"\nFalse Positive total: {total_fp} | di antaranya STALE (revoked/deprecated): "
          f"{sum(1 for x in fp_records if x['stale'])}")

    print("\n### 5 contoh FALSE NEGATIVE")
    print("| report_id | Teknik | Nama | Kategori | Stale |")
    print("|---|---|---|---|---|")
    for x in fn_examples:
        print(f"| {x['report_id'][:40]} | {x['technique_id']} | {x['technique_name'][:45]} | "
              f"{x['category']} | {'ya' if x['stale'] else '-'} |")

    print("\n### 5 contoh FALSE POSITIVE")
    print("| report_id | Teknik | Nama | Stale | Di kandidat |")
    print("|---|---|---|---|---|")
    for x in fp_examples:
        print(f"| {x['report_id'][:40]} | {x['technique_id']} | {x['technique_name'][:45]} | "
              f"{'ya' if x['stale'] else '-'} | {'ya' if x['in_candidates'] else '-'} |")

    stale_predicted = sorted({x["technique_id"] for x in fp_records if x["stale"]})
    print(f"\nID stale yang benar-benar diprediksi sistem sbg FP: {stale_predicted}")

    # --- Simpan JSON ---
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": {
            "total_fn": total_fn,
            "total_fp": total_fp,
            "fn_by_category": cat_counter,
            "fp_stale_count": sum(1 for x in fp_records if x["stale"]),
            "stale_ids_predicted": stale_predicted,
        },
        "fn_examples": fn_examples,
        "fp_examples": fp_examples,
        "all_fn": fn_records,
        "all_fp": fp_records,
    }
    out_path = METRICS_DIR / "fn_fp_examples.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDisimpan: {out_path}")


if __name__ == "__main__":
    main()
