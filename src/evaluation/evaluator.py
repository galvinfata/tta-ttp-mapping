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


def _accuracy_scores(
    y_true: list[list[str]], y_pred: list[list[str]], n_labels: int | None = None
) -> dict:
    """Dua definisi accuracy untuk klasifikasi multi-label.

    - jaccard  : rata-rata per laporan |pred ∩ gt| / |pred ∪ gt| (Godbole &
                 Sarawagi) — "accuracy multi-label" yang tidak terdistorsi
                 true negative. 1.0 bila pred dan gt sama-sama kosong.
    - accuracy : (TP+TN) / (N x |ruang label|) micro, definisi textbook per
                 pasangan (laporan, label). CATATAN: dengan ruang label besar
                 (607 teknik) nilai ini didominasi TN sehingga selalu tampak
                 tinggi — laporkan berdampingan dengan Jaccard/F1, jangan
                 berdiri sendiri. None bila n_labels tidak diberikan.
    """
    jaccard_total = 0.0
    correct_pairs = 0
    for gt_row, pred_row in zip(y_true, y_pred):
        gt, pred = set(gt_row) - {""}, set(pred_row) - {""}
        union = gt | pred
        jaccard_total += (len(gt & pred) / len(union)) if union else 1.0
        if n_labels:
            # TP + TN = semua label yang statusnya sama di pred & gt
            # = n_labels - |selisih simetris| = n_labels - (FP + FN).
            correct_pairs += n_labels - len(gt ^ pred)

    n = len(y_true)
    return {
        "jaccard": round(jaccard_total / n, 4) if n else 0.0,
        "accuracy": round(correct_pairs / (n * n_labels), 4) if (n and n_labels) else None,
    }


def evaluate_predictions(results: list[dict], attck_techniques: dict | None = None) -> dict:
    """
    Menghitung metrik teknik dalam dua mode:
    - exact: ID persis termasuk sub-teknik (T1566.001)
    - base : dinormalisasi ke base-technique (T1566) agar tidak menghukum
             ketidakcocokan granularitas sub-teknik.

    attck_techniques (opsional): bila diberikan, accuracy label-space ikut
    dihitung dengan |ruang label| = jumlah teknik di KB (exact) / jumlah
    base-technique unik (base). Jaccard accuracy selalu dihitung.
    """
    y_true = [r.get("ground_truth", []) for r in results]
    y_pred = [r.get("predicted_techniques", []) for r in results]

    n_exact = len(attck_techniques) if attck_techniques else None
    n_base = len({_base_technique(t) for t in attck_techniques}) if attck_techniques else None

    exact = _micro_scores(y_true, y_pred)
    exact_acc = _accuracy_scores(y_true, y_pred, n_labels=n_exact)

    y_true_base = [sorted({_base_technique(t) for t in row}) for row in y_true]
    y_pred_base = [sorted({_base_technique(t) for t in row}) for row in y_pred]
    base = _micro_scores(y_true_base, y_pred_base)
    base_acc = _accuracy_scores(y_true_base, y_pred_base, n_labels=n_base)

    return {
        "precision": exact["precision"],
        "recall": exact["recall"],
        "micro_f1": exact["micro_f1"],
        "accuracy": exact_acc["accuracy"],
        "jaccard": exact_acc["jaccard"],
        "base_precision": base["precision"],
        "base_recall": base["recall"],
        "base_micro_f1": base["micro_f1"],
        "base_accuracy": base_acc["accuracy"],
        "base_jaccard": base_acc["jaccard"],
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
    acc = _accuracy_scores(y_true, y_pred, n_labels=len(PHASE_TO_TACTIC_ID))
    return {
        "tactic_precision": scores["precision"],
        "tactic_recall": scores["recall"],
        "tactic_micro_f1": scores["micro_f1"],
        "tactic_accuracy": acc["accuracy"],
        "tactic_jaccard": acc["jaccard"],
        "total_reports": len(results),
    }


def save_results(results: list[dict], output_path: str):
    """Simpan hasil prediksi ke file JSON."""
    from pathlib import Path
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Hasil disimpan ke: {output_path}")
