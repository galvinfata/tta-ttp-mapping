from sklearn.metrics import precision_score, recall_score, f1_score
from sklearn.preprocessing import MultiLabelBinarizer
import json


# Peta nama fase kill-chain (kill_chain_phases.phase_name) -> tactic ID.
# Dipakai untuk menurunkan ground-truth taktik dari ground-truth teknik,
# karena dataset TRAM II hanya memberi label teknik (bukan taktik).
PHASE_TO_TACTIC_ID = {
    "reconnaissance": "TA0043",
    "resource-development": "TA0042",
    "initial-access": "TA0001",
    "execution": "TA0002",
    "persistence": "TA0003",
    "privilege-escalation": "TA0004",
    "defense-evasion": "TA0005",
    "credential-access": "TA0006",
    "discovery": "TA0007",
    "lateral-movement": "TA0008",
    "collection": "TA0009",
    "command-and-control": "TA0011",
    "exfiltration": "TA0010",
    "impact": "TA0040",
}


def _base_technique(technique_id: str) -> str:
    """Normalisasi ke base-technique: 'T1566.001' -> 'T1566'."""
    return technique_id.split(".")[0] if isinstance(technique_id, str) else technique_id


def _micro_scores(y_true: list[list[str]], y_pred: list[list[str]]) -> dict:
    """Hitung micro Precision/Recall/F1 untuk label multi-label."""
    mlb = MultiLabelBinarizer()
    # Pastikan ada minimal satu label agar binarizer tidak error.
    mlb.fit(y_true + y_pred + [[""]])

    y_true_bin = mlb.transform(y_true)
    y_pred_bin = mlb.transform(y_pred)

    return {
        "precision": round(precision_score(y_true_bin, y_pred_bin, average="micro", zero_division=0), 4),
        "recall": round(recall_score(y_true_bin, y_pred_bin, average="micro", zero_division=0), 4),
        "micro_f1": round(f1_score(y_true_bin, y_pred_bin, average="micro", zero_division=0), 4),
    }


def evaluate_predictions(results: list[dict]) -> dict:
    """
    Menghitung metrik teknik dalam dua mode:
    - exact: ID persis termasuk sub-teknik (T1566.001)
    - base : dinormalisasi ke base-technique (T1566) agar tidak menghukum
             ketidakcocokan granularitas sub-teknik.
    """
    y_true = [r.get("ground_truth", []) for r in results]
    y_pred = [r.get("predicted_techniques", []) for r in results]

    exact = _micro_scores(y_true, y_pred)

    y_true_base = [sorted({_base_technique(t) for t in row}) for row in y_true]
    y_pred_base = [sorted({_base_technique(t) for t in row}) for row in y_pred]
    base = _micro_scores(y_true_base, y_pred_base)

    return {
        "precision": exact["precision"],
        "recall": exact["recall"],
        "micro_f1": exact["micro_f1"],
        "base_precision": base["precision"],
        "base_recall": base["recall"],
        "base_micro_f1": base["micro_f1"],
        "total_reports": len(results),
    }


def derive_tactic_ground_truth(gt_techniques: list[str], attck_techniques: dict) -> list[str]:
    """Turunkan set tactic ID dari daftar teknik ground-truth.

    Memetakan tiap teknik -> fase kill-chain -> tactic ID. Sub-teknik (T1566.001)
    yang tidak ada di KB di-fallback ke base-technique-nya.
    """
    tactic_ids = set()
    for tid in gt_techniques:
        data = attck_techniques.get(tid) or attck_techniques.get(_base_technique(tid))
        if not data:
            continue
        for phase in data.get("tactics", []):
            ta_id = PHASE_TO_TACTIC_ID.get(phase)
            if ta_id:
                tactic_ids.add(ta_id)
    return sorted(tactic_ids)


def evaluate_tactics(results: list[dict], attck_techniques: dict) -> dict:
    """Evaluasi taktik dengan ground-truth taktik yang diturunkan dari teknik GT."""
    y_true = [
        derive_tactic_ground_truth(r.get("ground_truth", []), attck_techniques)
        for r in results
    ]
    y_pred = [r.get("tactics_identified", []) for r in results]

    scores = _micro_scores(y_true, y_pred)
    return {
        "tactic_precision": scores["precision"],
        "tactic_recall": scores["recall"],
        "tactic_micro_f1": scores["micro_f1"],
        "total_reports": len(results),
    }


def save_results(results: list[dict], output_path: str):
    """Simpan hasil prediksi ke file JSON."""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Hasil disimpan ke: {output_path}")
