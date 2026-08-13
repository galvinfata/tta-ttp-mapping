"""Run manifest: rekaman konfigurasi efektif setiap run pipeline.

Masalah yang diselesaikan: berkas hasil (results/predictions/*.json) hanya
berisi prediksi. Konfigurasi yang menghasilkannya — ukuran chunk, jumlah
kandidat, status Reviewer, versi KB, commit kode — tidak tersimpan di manapun,
sehingga angka pada naskah tidak bisa ditelusuri balik ke konfigurasinya.
Contoh konkret: status Reviewer pada run 11 Juli 2026 tidak dapat dibuktikan
karena jalur batch web UI tidak pernah menyimpannya.

Manifest ditulis BERDAMPINGAN dengan berkas hasil, bernama
<nama_hasil>.manifest.json, dan tidak pernah menimpa berkas hasil manapun.

Pemakaian:

    recorder = RunRecorder(entrypoint="main.py")
    ... jalankan pipeline, panggil recorder.record_failure() bila ada laporan gagal ...
    recorder.finalize(results, output_path)

reviewer_active ditentukan dari BUKTI RUNTIME (field reviewer_invoked pada tiap
hasil laporan yang diisi orchestrator), bukan dari nilai environment variable.
"""
import hashlib
import json
import os
import re
import statistics
import subprocess
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Environment variable yang relevan terhadap hasil, beserta default kode.
# Default DISALIN dari modul yang memakainya; bila modul tersebut sudah
# ter-import, nilai efektifnya diambil langsung dari konstanta modul (lihat
# _effective_env) supaya manifest tidak pernah melenceng dari yang dipakai.
_ENV_SPEC: list[tuple[str, str]] = [
    ("LLM_PROVIDER", "lmstudio"),
    ("LOCAL_LLM_MODEL", "qwen/qwen3-4b"),
    ("LOCAL_LLM_BASE_URL", "http://localhost:1234"),
    ("LLM_N_CTX", "4096"),
    ("PROMPT_BUDGET_ENFORCE", "false"),
    ("TECHNIQUE_CANDIDATE_TOP_K", "50"),
    ("LOCAL_LLM_CANDIDATE_TOP_K", "50"),
    ("CANDIDATE_LIST_MAX_CHARS", "4500"),
    ("CANDIDATE_DESC_CHARS", "120"),
    ("LOCAL_LLM_REPORT_MAX_CHARS", "3500"),
    ("LLM_CHUNK_OVERLAP_CHARS", "250"),
    ("LLM_MAX_CHUNKS", "3"),
    ("RETRIEVAL_MAX_CHARS", "20000"),
    ("RETRIEVAL_PER_CHUNK", "true"),
    ("RETRIEVAL_NAME_BOOST", "true"),
    ("RETRIEVAL_EXCLUDE_PRECOMPROMISE", "true"),
    ("RETRIEVAL_EMBEDDING_HYBRID", "true"),
    ("RETRIEVAL_EMBEDDING_MODEL", "text-embedding-nomic-embed-text-v1.5"),
    ("RETRIEVAL_EMBEDDING_ALPHA", "0.5"),
    ("RETRIEVAL_DOC_DESC_CHARS", "1000"),
    ("ATTCK_DESC_MAX_CHARS", "1000"),
    ("TECHNIQUE_ACCEPT_TOP_N", "30"),
    ("CANDIDATE_SHUFFLE_SEED", ""),
    ("RECONCILE_SUBTECH_FAMILY_CAP", "2"),
    ("RECONCILE_TACTIC_FILTER", "false"),
    ("REVIEWER_ENABLE", "true"),
    ("WEB_UI_ENABLE_REVIEWER", "true"),
    ("LLM_REVIEW_MAX_ITER", "2"),
    ("LLM_REVISE_TEMPERATURE", "0.4"),
    ("LLM_DISABLE_THINKING", "true"),
    ("LLM_REASONING_EFFORT", "none"),
    ("LLM_STRUCTURED_OUTPUT", "true"),
    ("LOCAL_LLM_MAX_TOKENS_TECHNIQUE", "512"),
    ("LOCAL_LLM_MAX_TOKENS_TACTIC", "512"),
    ("LOCAL_LLM_MAX_TOKENS_REVIEWER", "512"),
    # Ikut dicatat karena menentukan berapa lama satu laporan bisa menggantung
    # saat server bermasalah: timeout x 3 percobaan x jumlah chunk.
    ("LLM_REQUEST_TIMEOUT_SECONDS", "300"),
    ("ATTCK_SOURCE", "data/mitre_cti/enterprise-attack.json"),
]

# Konstanta modul yang menjadi SUMBER KEBENARAN nilai efektif (bila modulnya
# sudah ter-import oleh proses yang berjalan).
_MODULE_CONSTANTS: dict[str, tuple[str, str]] = {
    "LOCAL_LLM_REPORT_MAX_CHARS": ("agents.retrieval", "LOCAL_LLM_REPORT_MAX_CHARS"),
    "LLM_CHUNK_OVERLAP_CHARS": ("agents.retrieval", "CHUNK_OVERLAP_CHARS"),
    "LLM_MAX_CHUNKS": ("agents.retrieval", "MAX_CHUNKS"),
    "RETRIEVAL_MAX_CHARS": ("agents.retrieval", "RETRIEVAL_MAX_CHARS"),
    "CANDIDATE_LIST_MAX_CHARS": ("agents.technique_agent", "CANDIDATE_LIST_MAX_CHARS"),
    "CANDIDATE_DESC_CHARS": ("agents.technique_agent", "CANDIDATE_DESC_CHARS"),
    "TECHNIQUE_ACCEPT_TOP_N": ("agents.technique_agent", "TECHNIQUE_ACCEPT_TOP_N"),
    "CANDIDATE_SHUFFLE_SEED": ("agents.technique_agent", "CANDIDATE_SHUFFLE_SEED"),
    "LLM_N_CTX": ("agents.prompt_budget", "LLM_N_CTX"),
    "PROMPT_BUDGET_ENFORCE": ("agents.prompt_budget", "PROMPT_BUDGET_ENFORCE"),
}


def new_run_id() -> str:
    return f"run_{datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}"


def _git_info() -> dict:
    """Commit hash & status dirty repo (kosong bila git tidak tersedia)."""
    info = {"commit": None, "dirty": None, "branch": None}
    try:
        run = lambda args: subprocess.run(  # noqa: E731
            ["git", *args], cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=15
        )
        head = run(["rev-parse", "HEAD"])
        if head.returncode == 0:
            info["commit"] = head.stdout.strip()
        branch = run(["rev-parse", "--abbrev-ref", "HEAD"])
        if branch.returncode == 0:
            info["branch"] = branch.stdout.strip()
        status = run(["status", "--porcelain"])
        if status.returncode == 0:
            info["dirty"] = bool(status.stdout.strip())
    except Exception:
        pass
    return info


def _sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def _attck_version(path: Path) -> str | None:
    """x_mitre_version dari objek x-mitre-collection.

    Objek koleksi selalu berada di awal bundle, jadi cukup membaca potongan
    kepala berkas — jauh lebih murah daripada mem-parse JSON 40 MB.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            head = f.read(8192)
    except OSError:
        return None
    if "x-mitre-collection" not in head:
        return None
    match = re.search(r'"x_mitre_version"\s*:\s*"([^"]+)"', head)
    return match.group(1) if match else None


def _effective_env() -> dict:
    """Nilai efektif tiap env var: dari konstanta modul bila ada, jika tidak dari os.getenv."""
    import sys

    values: dict[str, object] = {}
    for name, default in _ENV_SPEC:
        values[name] = os.getenv(name, default)

    for name, (module_name, attribute) in _MODULE_CONSTANTS.items():
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, attribute):
            values[name] = getattr(module, attribute)
    return values


def _coverage_summary(results: list[dict]) -> dict:
    ratios = [
        r["coverage_ratio"] for r in results
        if isinstance(r.get("coverage_ratio"), (int, float))
    ]
    if not ratios:
        return {"reports_with_coverage": 0}
    return {
        "reports_with_coverage": len(ratios),
        "coverage_ratio_median": round(statistics.median(ratios), 4),
        "coverage_ratio_mean": round(sum(ratios) / len(ratios), 4),
        "coverage_ratio_min": round(min(ratios), 4),
        "reports_fully_read": sum(1 for x in ratios if x >= 0.999),
        "reports_below_60pct": sum(1 for x in ratios if x < 0.6),
        "report_chars_median": int(statistics.median(
            [r["report_chars"] for r in results if r.get("report_chars")]
        )),
    }


def _rank_summary(results: list[dict]) -> dict:
    """Keselarasan keluaran sistem dengan peringkat retrieval (Tahap 2b).

    Untuk tiap teknik yang BENAR-BENAR diprediksi (setelah rekonsiliasi), ambil
    peringkatnya pada daftar kandidat chunk asalnya. Semakin besar
    `pct_outside_top30`, semakin banyak keluaran sistem yang tidak sekadar
    mengikuti urutan retrieval.

    predictions_without_rank = teknik pada keluaran akhir yang tidak punya
    peringkat, yaitu yang DITAMBAHKAN pascaproses (mis. reconciler mengganti
    sub-teknik dengan base technique-nya) sehingga tidak pernah dipilih LLM dari
    daftar kandidat. Dihitung terpisah, tidak dicampur ke sebaran peringkat.
    """
    ranks: list[int] = []
    without_rank = 0
    filtered_out_ranks: list[int] = []
    reports_with_ranks = 0

    for r in results:
        accepted = r.get("rank_of_accepted")
        if not isinstance(accepted, dict):
            continue
        reports_with_ranks += 1
        for tid in r.get("predicted_techniques", []):
            rank = accepted.get(tid)
            if isinstance(rank, int):
                ranks.append(rank)
            else:
                without_rank += 1
        dropped = r.get("rank_of_filtered_out")
        if isinstance(dropped, dict):
            filtered_out_ranks.extend(v for v in dropped.values() if isinstance(v, int))

    if not reports_with_ranks:
        # Berkas hasil dari sebelum instrumentasi ini ada — jangan menebak.
        return {"reports_with_ranks": 0}

    summary = {
        "reports_with_ranks": reports_with_ranks,
        "ranked_predictions": len(ranks),
        "predictions_without_rank": without_rank,
        # Kontribusi LLM yang dibuang filter TECHNIQUE_ACCEPT_TOP_N.
        "filtered_out_by_accept_top_n": len(filtered_out_ranks),
    }
    if ranks:
        buckets = {"1-10": 0, "11-20": 0, "21-30": 0, "31+": 0}
        for rank in ranks:
            if rank <= 10:
                buckets["1-10"] += 1
            elif rank <= 20:
                buckets["11-20"] += 1
            elif rank <= 30:
                buckets["21-30"] += 1
            else:
                buckets["31+"] += 1
        total = len(ranks)
        summary.update({
            "mean_tfidf_rank": round(sum(ranks) / total, 2),
            "median_tfidf_rank": round(statistics.median(ranks), 1),
            "rank_buckets": buckets,
            "rank_bucket_pct": {k: round(v / total, 4) for k, v in buckets.items()},
            "pct_outside_top30": round(buckets["31+"] / total, 4),
        })
    if filtered_out_ranks:
        summary["filtered_out_median_rank"] = round(
            statistics.median(filtered_out_ranks), 1
        )
    return summary


def _embedding_summary() -> dict:
    """Status retrieval hibrida dari BUKTI RUNTIME, bukan dari env.

    Env `RETRIEVAL_EMBEDDING_HYBRID: "true"` hanya menyatakan NIAT. Bila server
    embedding mati di tengah run, retrieval.py diam-diam jatuh ke TF-IDF murni
    untuk sisa proses; tanpa rekaman ini, sebuah run separuh hibrida tidak dapat
    dibedakan dari run hibrida penuh saat hasilnya ditafsirkan.
    """
    import sys

    module = sys.modules.get("agents.retrieval")
    if module is None or not hasattr(module, "embedding_runtime_state"):
        return {"instrumented": False}
    try:
        return module.embedding_runtime_state()
    except Exception:
        return {"instrumented": False}


def _reviewer_summary(results: list[dict]) -> dict:
    """Status reviewer dari BUKTI RUNTIME, bukan dari env.

    review_iterations dihitung orchestrator termasuk satu putaran "skipped"
    saat reviewer nonaktif, jadi jumlah revisi = iterasi di atas yang pertama
    dan hanya dihitung untuk laporan yang benar-benar melewati reviewer.
    """
    invoked = [r for r in results if r.get("reviewer_invoked")]
    iterations = [int(r.get("review_iterations") or 0) for r in invoked]
    return {
        "reviewer_active": bool(invoked),
        "reports_with_reviewer": len(invoked),
        "review_iterations_total": sum(iterations),
        "reports_triggering_revision": sum(1 for n in iterations if n > 1),
        "reviewer_error_reports": sum(1 for r in results if r.get("reviewer_errored")),
        "reviewer_error_calls": sum(int(r.get("reviewer_error_count") or 0) for r in results),
    }


class RunRecorder:
    """Kumpulkan metadata satu run lalu tulis manifest di samping berkas hasil."""

    def __init__(self, entrypoint: str, preset: str | None = None, notes: str = ""):
        # Statistik prompt di-reset agar manifest hanya memuat run ini.
        try:
            from agents.prompt_budget import reset_prompt_stats

            reset_prompt_stats()
        except Exception:
            pass

        self.run_id = new_run_id()
        self.entrypoint = entrypoint
        self.preset = preset
        self.notes = notes
        self.started_at = time.time()
        self.failed_reports: list[str] = []
        self.retry_count = 0
        # Di-cache karena manifest kini juga ditulis di tiap checkpoint parsial:
        # sha256 berkas ATT&CK 40 MB dan subprocess git tidak perlu diulang —
        # keduanya tidak berubah selama satu run.
        self._attck_cache: dict | None = None
        self._git_cache: dict | None = None

    def record_failure(self, report_id: str) -> None:
        self.failed_reports.append(str(report_id))

    def _attck_info(self) -> dict:
        if self._attck_cache is None:
            attck_source = Path(os.getenv(
                "ATTCK_SOURCE", str(PROJECT_ROOT / "data/mitre_cti/enterprise-attack.json")
            ))
            if not attck_source.is_absolute():
                attck_source = PROJECT_ROOT / attck_source
            self._attck_cache = {
                "source": str(attck_source),
                "sha256": _sha256_file(attck_source),
                "version": _attck_version(attck_source),
            }
        return self._attck_cache

    def build(self, results: list[dict], output_path: str | Path | None = None, **extra) -> dict:
        finished_at = time.time()
        if self._git_cache is None:
            self._git_cache = _git_info()

        try:
            from agents.prompt_budget import get_prompt_stats

            prompt_stats = get_prompt_stats()
        except Exception:
            prompt_stats = {}

        manifest = {
            "run_id": self.run_id,
            "entrypoint": self.entrypoint,
            "preset": self.preset,
            "notes": self.notes,
            "started_at": datetime.fromtimestamp(self.started_at).isoformat(timespec="seconds"),
            "finished_at": datetime.fromtimestamp(finished_at).isoformat(timespec="seconds"),
            "duration_seconds": round(finished_at - self.started_at, 1),
            # status: "complete" = run selesai wajar; "partial" = checkpoint di
            # tengah run. Checkpoint parsial ditulis agar run yang DIBUNUH
            # (server mati, kehabisan waktu, Ctrl+C) tetap meninggalkan rekaman
            # konfigurasi — pelajaran dari run preset A 4 Agustus 2026 yang
            # berhenti di laporan ke-14 tanpa manifest apapun.
            "status": "complete",
            "git": self._git_cache,
            "env": _effective_env(),
            "reports_processed": len(results),
            "reports_failed": len(self.failed_reports),
            "failed_report_ids": self.failed_reports[:50],
            "retry_count": self.retry_count,
            "prompt_stats": prompt_stats,
            "coverage_stats": _coverage_summary(results),
            "rank_alignment": _rank_summary(results),
            "embedding_runtime": _embedding_summary(),
            "attck": self._attck_info(),
            "results_file": str(output_path) if output_path else None,
        }
        manifest.update(_reviewer_summary(results))
        manifest.update(extra)
        return manifest

    def finalize(self, results: list[dict], output_path: str | Path | None = None, **extra) -> dict:
        """Bangun manifest, tulis ke <output_path>.manifest.json, kembalikan dict."""
        manifest = self.build(results, output_path, **extra)
        if output_path:
            write_manifest(output_path, manifest)
        return manifest


def manifest_path_for(results_path: str | Path) -> Path:
    return Path(str(results_path) + ".manifest.json")


def write_manifest(results_path: str | Path, manifest: dict) -> Path:
    """Tulis manifest di samping berkas hasil. TIDAK PERNAH menyentuh berkas hasil."""
    path = manifest_path_for(results_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Manifest disimpan ke: {path}")
    return path


def load_manifest(results_path: str | Path) -> dict | None:
    path = manifest_path_for(results_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
