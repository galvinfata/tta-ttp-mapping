"""Utilitas bersama untuk skrip evaluasi tambahan (offline & deterministik).

Berisi:
- Bootstrap sys.path agar paket `src` bisa di-import saat menjalankan skrip di
  folder scripts/ secara langsung.
- Konstanta path KB & dataset.
- Metrik Precision/Recall/F1 berbasis OPERASI HIMPUNAN murni (tanpa sklearn),
  level laporan, micro-averaged — sesuai definisi skripsi.

Tidak ada satupun yang memanggil LLM atau jaringan.
"""
import os
import sys
from pathlib import Path

# Konsol Windows default cp1252 tidak bisa mencetak em-dash / simbol; paksa UTF-8
# agar tabel markdown (dan ✓/✗) tercetak konsisten & siap tempel ke skripsi.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# scripts/eval_common.py -> root proyek = parent dari scripts/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Path default; dapat di-override lewat environment.
ATTCK_SOURCE = os.getenv("ATTCK_SOURCE", str(PROJECT_ROOT / "data/mitre_cti/enterprise-attack.json"))
DATA_DIR = os.getenv("TRAM_DATA_DIR", str(PROJECT_ROOT / "data/tram_ii"))
METRICS_DIR = PROJECT_ROOT / "results/metrics"


def base_technique(technique_id: str) -> str:
    """Normalisasi ke base-technique: 'T1566.001' -> 'T1566'."""
    if not isinstance(technique_id, str):
        return technique_id
    return technique_id.split(".")[0]


def _to_sets(rows: list[list[str]], base: bool) -> list[set]:
    """Ubah tiap baris (list ID) jadi himpunan; opsional normalisasi ke base."""
    out = []
    for row in rows:
        ids = [base_technique(t) for t in row] if base else list(row)
        out.append(set(ids))
    return out


def set_metrics(
    y_true: list[list[str]],
    y_pred: list[list[str]],
    base: bool = False,
    n_labels: int | None = None,
) -> dict:
    """Hitung metrik micro berbasis himpunan per laporan.

    Untuk tiap laporan i:
        TP_i = |pred_i ∩ gt_i|, FP_i = |pred_i \\ gt_i|, FN_i = |gt_i \\ pred_i|
    Agregasi micro menjumlahkan TP/FP/FN seluruh laporan lalu:
        P = TP/(TP+FP), R = TP/(TP+FN), F1 = 2TP/(2TP+FP+FN).

    Tambahan:
    - accuracy: (TP+TN)/(N x n_labels) bila n_labels (ukuran ruang label,
                mis. jumlah teknik KB) diberikan — didominasi TN pada ruang
                label besar, laporkan bersama F1.
    - jaccard : tetap dihitung & tersimpan di dict/JSON (tidak ditampilkan).
    """
    assert len(y_true) == len(y_pred), "jumlah laporan y_true & y_pred harus sama"
    gt_sets = _to_sets(y_true, base)
    pred_sets = _to_sets(y_pred, base)

    tp = fp = fn = 0
    jaccard_total = 0.0
    correct_pairs = 0
    for gt, pred in zip(gt_sets, pred_sets):
        tp += len(pred & gt)
        fp += len(pred - gt)
        fn += len(gt - pred)
        union = gt | pred
        jaccard_total += (len(gt & pred) / len(union)) if union else 1.0
        if n_labels:
            correct_pairs += n_labels - len(gt ^ pred)

    n = len(gt_sets)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * tp) / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "jaccard": round(jaccard_total / n, 4) if n else 0.0,
        "accuracy": round(correct_pairs / (n * n_labels), 4) if (n and n_labels) else None,
    }


def both_modes(
    y_true: list[list[str]],
    y_pred: list[list[str]],
    n_labels_exact: int | None = None,
    n_labels_base: int | None = None,
) -> dict:
    """Kembalikan metrik EXACT dan BASE-TECHNIQUE sekaligus."""
    return {
        "exact": set_metrics(y_true, y_pred, base=False, n_labels=n_labels_exact),
        "base": set_metrics(y_true, y_pred, base=True, n_labels=n_labels_base),
    }


def fmt_row(label: str, m: dict) -> str:
    """Baris tabel markdown: label | TP | FP | FN | P | R | F1 | Acc."""
    acc = f"{m['accuracy']:.4f}" if m.get("accuracy") is not None else "-"
    return (
        f"| {label} | {m['tp']} | {m['fp']} | {m['fn']} | "
        f"{m['precision']:.4f} | {m['recall']:.4f} | {m['f1']:.4f} | {acc} |"
    )


METRIC_HEADER = (
    "| Konfigurasi | TP | FP | FN | Precision | Recall | F1 | Accuracy |\n"
    "|---|---|---|---|---|---|---|---|"
)

# Berapa banyak kandidat TF-IDF teratas yang disimpan per laporan. Dipakai
# sebagai basis baik untuk sweep baseline (top-N, N<=50) maupun analisis
# recall-ceiling & FN/FP (top-50).
CANDIDATE_TOP_K = 50
_CANDIDATE_CACHE = METRICS_DIR / "_candidates_top50.json"
_CANDIDATE_CACHE_PER_CHUNK = METRICS_DIR / "_candidates_top50_per_chunk.json"


def _kb_fingerprint(attck_techniques: dict) -> str:
    """Sidik jari isi KB + konfigurasi retrieval untuk validasi cache kandidat.

    Tanpa ini, cache lama tetap dianggap valid setelah KB berubah (mis. setelah
    filter revoked ditambahkan di attck_loader) ATAU setelah logika retrieval
    berubah (mis. sublinear_tf/name-boost), dan kandidat basi terus dipakai.
    """
    import hashlib
    from agents.retrieval import RETRIEVAL_SIGNATURE

    joined = ",".join(sorted(attck_techniques.keys()))
    desc_len = sum(len(t.get("description", "")) for t in attck_techniques.values())
    return (
        f"{len(attck_techniques)}:{desc_len}:"
        f"{hashlib.sha256(joined.encode('utf-8')).hexdigest()[:16]}:{RETRIEVAL_SIGNATURE}"
    )


def build_candidate_map(
    reports, attck_techniques, top_k=CANDIDATE_TOP_K, use_cache=True, per_chunk=False
):
    """Bangun rekonstruksi kandidat retrieval per laporan secara deterministik.

    per_chunk=False -> {report_id: [kandidat top_k dari seluruh laporan]}
                       (dipakai baseline TF-IDF top-N di eval_baselines).
    per_chunk=True  -> {report_id: [[kandidat top_k chunk-1], [chunk-2], ...]}
                       — mencerminkan kandidat yang BENAR-BENAR dilihat sistem
                       (technique_agent memakai retrieval per-chunk).

    Hasil di-cache ke results/metrics/_candidates_top50*.json agar skrip-skrip
    evaluasi memakai rekonstruksi kandidat yang IDENTIK dan tidak menghitung
    ulang TF-IDF berkali-kali. Cache divalidasi atas set report_id, sidik jari
    KB, dan konfigurasi retrieval; jika ada yang tidak cocok, dihitung ulang.
    """
    import json
    # Import di dalam fungsi agar modul ini tetap ringan saat hanya butuh metrik.
    from agents.retrieval import (
        _retrieve_candidate_techniques,
        retrieve_candidates_per_chunk,
    )

    cache_path = _CANDIDATE_CACHE_PER_CHUNK if per_chunk else _CANDIDATE_CACHE
    ids = [r["id"] for r in reports]
    kb_fp = _kb_fingerprint(attck_techniques)

    if use_cache and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                cached.get("top_k", 0) >= top_k
                and cached.get("kb_fingerprint") == kb_fp
                and set(cached.get("candidates", {}).keys()) == set(ids)
            ):
                if per_chunk:
                    return {
                        rid: [ch[:top_k] for ch in chunks]
                        for rid, chunks in cached["candidates"].items()
                    }
                return {rid: cand[:top_k] for rid, cand in cached["candidates"].items()}
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    candidate_map = {}
    for i, r in enumerate(reports, 1):
        if per_chunk:
            candidate_map[r["id"]] = [
                cands
                for _, cands in retrieve_candidates_per_chunk(
                    report_text=r["text"],
                    attck_techniques=attck_techniques,
                    top_k=top_k,
                )
            ]
        else:
            candidate_map[r["id"]] = _retrieve_candidate_techniques(
                report_text=r["text"],
                attck_techniques=attck_techniques,
                top_k=top_k,
            )
        print(f"  retrieval {i}/{len(reports)}", end="\r")
    print()

    if use_cache:
        METRICS_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {"top_k": top_k, "kb_fingerprint": kb_fp, "candidates": candidate_map},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return candidate_map
