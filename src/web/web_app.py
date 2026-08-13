import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

# src/web/web_app.py -> naik 3 level ke root proyek.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT / "src"))

from knowledge.attck_loader import load_attck_tactics, load_attck_techniques
from knowledge.data_loader import load_tram_dataset
from evaluation.evaluator import (
    derive_tactic_ground_truth,
    evaluate_predictions,
    evaluate_tactics,
    save_results,
)
from reporting.evidence import build_evidence_map
from pipeline.orchestrator import process_report
from reporting.report_builder import build_pdf_report
from reporting.stix_builder import build_stix_bundle
from agents.tactic_agent import create_tactic_agent
from agents.technique_agent import create_technique_agent
from agents.reviewer_agent import create_reviewer_agent
from agents.prompt_budget import format_prompt_stats
from utils.run_manifest import RunRecorder, write_manifest

WEB_UI_PATH = PROJECT_ROOT / "web_ui" / "index.html"
WEB_UI_APP_PATH = PROJECT_ROOT / "web_ui" / "app.html"
WEB_UI_BATCH_PATH = PROJECT_ROOT / "web_ui" / "batch.html"
ATTCK_SOURCE = os.getenv("ATTCK_SOURCE", str(PROJECT_ROOT / "data" / "mitre_cti" / "enterprise-attack.json"))
WEB_UI_ENABLE_REVIEWER = os.getenv("WEB_UI_ENABLE_REVIEWER", "true").lower() == "true"

MAX_UPLOAD_BYTES = 5 * 1024 * 1024

APP_STATE: dict[str, Any] = {
    "ready": False,
    "attck_techniques": {},
    "attck_tactics": {},
    "tactic_model": None,
    "technique_model": None,
    "reviewer_model": None,
    "lock": threading.Lock(),
}

JOBS: dict[str, dict[str, Any]] = {}

TRAM_DATA_DIR = os.getenv("TRAM_DATA_DIR", str(PROJECT_ROOT / "data" / "tram_ii"))
# Manifest job satu-laporan tidak punya berkas hasil untuk ditumpangi, jadi
# disimpan di direktori tersendiri (jalur batch menulis manifest berdampingan
# dengan berkas hasilnya, sesuai konvensi run_manifest).
JOB_MANIFEST_DIR = PROJECT_ROOT / "results" / "runs"
# Simpan hasil batch ke file setiap N laporan agar run panjang yang terputus
# tidak kehilangan hasil (sama seperti main.py).
BATCH_SAVE_EVERY = 5

# State evaluasi batch (satu run aktif dalam satu waktu). Semua field selain
# "lock" dan "cancel" aman dikirim ke frontend lewat /api/batch/status.
AGENT_KEYS = ("tactic", "technique", "reviewer", "reconciler")
# Berapa event agent terakhir yang disimpan untuk live log di UI.
AGENT_LOG_MAX = 60


def _idle_agents() -> dict[str, dict[str, Any]]:
    return {
        key: {"status": "idle", "detail": "", "iteration": 0, "updated_at": None}
        for key in AGENT_KEYS
    }


BATCH: dict[str, Any] = {
    "status": "idle",  # idle | running | done | cancelled | error
    "total": 0,
    "done": 0,
    "current_index": 0,
    "current_report": "",
    "started_at": None,
    "finished_at": None,
    "reviewer": False,
    "rows": [],
    "metrics": None,
    "tactic_metrics": None,
    "output_path": None,
    "error": None,
    # Status live tiap agent pada laporan yang sedang diproses + riwayat event.
    "agents": _idle_agents(),
    "agent_log": [],
    "cancel": False,
    "lock": threading.Lock(),
}

app = FastAPI(title="TTP Mapping PoC")


def _batch_row(index: int, result: dict, attck_techniques: dict, attck_tactics: dict) -> dict:
    """Ringkasan satu laporan untuk tabel progres UI (metrik exact).

    Selain hitungan pred/gt/tp/fp/fn, sertakan detail taktik & teknik yang
    teridentifikasi (dengan nama + status hit/miss) supaya UI bisa menampilkan
    popup "view" per laporan tanpa memuat ulang hasil dari file.
    """
    pred = set(result.get("predicted_techniques", []))
    gt = set(result.get("ground_truth", []))

    # GT taktik diturunkan dari GT teknik. Sejak 2026-08-08 field ini disimpan
    # di hasil; berkas lama belum punya, jadi diturunkan on-the-fly agar UI
    # tetap bisa menampilkan hit/miss taktik untuk run lama.
    gt_tactics = set(
        result.get("ground_truth_tactics")
        or derive_tactic_ground_truth(sorted(gt), attck_techniques)
    )
    pred_tactics = result.get("tactics_identified", [])

    tactics = [
        {"id": tid, "name": attck_tactics.get(tid, ""), "hit": tid in gt_tactics}
        for tid in pred_tactics
    ]
    missed_tactics = [
        {"id": tid, "name": attck_tactics.get(tid, "")}
        for tid in sorted(gt_tactics - set(pred_tactics))
    ]
    techniques = [
        {
            "id": tech_id,
            "name": attck_techniques.get(tech_id, {}).get("name", ""),
            "hit": tech_id in gt,  # True = cocok ground truth (TP), False = FP
        }
        for tech_id in result.get("predicted_techniques", [])
    ]
    missed = [
        {"id": tech_id, "name": attck_techniques.get(tech_id, {}).get("name", "")}
        for tech_id in sorted(gt - pred)
    ]

    return {
        "index": index,
        "report_id": str(result.get("report_id", "")).strip()[:90],
        "pred": len(pred),
        "gt": len(gt),
        "tp": len(pred & gt),
        "fp": len(pred - gt),
        "fn": len(gt - pred),
        "tactics": tactics,
        "missed_tactics": missed_tactics,
        "techniques": techniques,
        "missed": missed,
    }


def _make_progress_cb(report_index: int, report_id: str):
    """Callback yang dipanggil orchestrator tiap agent mulai/selesai.

    Menulis status agent yang sedang berjalan + menambah baris ke live log.
    Dipanggil dari worker thread yang sama dengan _run_batch.
    """

    def on_event(event: dict) -> None:
        agent = event.get("agent", "")
        if agent not in BATCH["agents"]:
            return

        status = event.get("status", "")
        detail = event.get("detail", "")
        BATCH["agents"][agent] = {
            "status": status,
            "detail": detail,
            "iteration": event.get("iteration", 0),
            "updated_at": time.time(),
        }
        BATCH["agent_log"] = (BATCH["agent_log"] + [{
            "ts": time.time(),
            "report_index": report_index,
            "report_id": report_id,
            "agent": agent,
            "status": status,
            "detail": detail,
        }])[-AGENT_LOG_MAX:]

    return on_event


def _run_batch(n_reports: int, use_reviewer: bool) -> None:
    """Worker thread: jalankan pipeline atas n laporan TRAM + evaluasi live."""
    recorder = RunRecorder(entrypoint="web_app._run_batch")
    results: list[dict] = []
    output_path = None
    try:
        _ensure_initialized()
        reports = load_tram_dataset(TRAM_DATA_DIR)[:n_reports]

        reviewer_model = APP_STATE["reviewer_model"]
        if use_reviewer and reviewer_model is None:
            reviewer_model = create_reviewer_agent()

        output_path = str(
            PROJECT_ROOT / "results" / "predictions" /
            f"results_web_{datetime.now():%Y%m%d_%H%M%S}.json"
        )
        BATCH.update({
            "total": len(reports),
            "output_path": output_path,
        })

        for i, report in enumerate(reports, 1):
            if BATCH["cancel"]:
                BATCH["status"] = "cancelled"
                break

            report_id = str(report.get("id", "")).strip()[:120]
            BATCH.update({
                "current_index": i,
                "current_report": report_id,
                # Reset kartu agent tiap laporan baru.
                "agents": _idle_agents(),
            })

            result = process_report(
                report=report,
                attck_techniques=APP_STATE["attck_techniques"],
                attck_tactics=APP_STATE["attck_tactics"],
                tactic_model=APP_STATE["tactic_model"],
                technique_model=APP_STATE["technique_model"],
                reviewer_model=reviewer_model,
                progress_cb=_make_progress_cb(i, report_id),
            )
            results.append(result)

            # Update progres + metrik berjalan setelah tiap laporan selesai.
            BATCH["rows"].append(_batch_row(
                i, result, APP_STATE["attck_techniques"], APP_STATE["attck_tactics"]
            ))
            BATCH["metrics"] = evaluate_predictions(
                results, APP_STATE["attck_techniques"]
            )
            BATCH["tactic_metrics"] = evaluate_tactics(
                results, APP_STATE["attck_techniques"]
            )
            BATCH["done"] = i

            if i % BATCH_SAVE_EVERY == 0:
                save_results(results, output_path)

        if results:
            save_results(results, output_path)
        if BATCH["status"] == "running":
            BATCH["status"] = "done"
    except Exception as exc:
        BATCH["status"] = "error"
        BATCH["error"] = str(exc)
    finally:
        # Manifest ditulis APAPUN hasil akhirnya (selesai, dibatalkan, atau
        # error) selama ada hasil — inilah satu-satunya tempat status Reviewer,
        # ukuran chunk, dan versi KB run batch tersimpan permanen. Sebelum ini,
        # jalur batch tidak menyimpan status reviewer sama sekali sehingga
        # konfigurasi run 11 Juli 2026 tidak bisa dibuktikan.
        if results and output_path:
            try:
                print()
                print(format_prompt_stats())
                recorder.finalize(
                    results, output_path,
                    metrics={
                        "technique": BATCH.get("metrics"),
                        "tactic": BATCH.get("tactic_metrics"),
                    },
                    reviewer_requested=use_reviewer,
                    batch_status=BATCH.get("status"),
                    reports_requested=n_reports,
                )
            except Exception as manifest_exc:  # manifest gagal != run gagal
                print(f"[WARN] Gagal menulis manifest batch: {manifest_exc}")
        BATCH["finished_at"] = time.time()
        # Jangan tinggalkan kartu agent dalam keadaan "running" setelah run berhenti.
        for key, agent in BATCH["agents"].items():
            if agent.get("status") == "running":
                BATCH["agents"][key] = {**agent, "status": "idle"}


def _ensure_initialized() -> None:
    if APP_STATE["ready"]:
        return

    with APP_STATE["lock"]:
        if APP_STATE["ready"]:
            return

        attck_techniques = load_attck_techniques(ATTCK_SOURCE)
        attck_tactics = load_attck_tactics(ATTCK_SOURCE)
        tactic_model = create_tactic_agent()
        technique_model = create_technique_agent()
        reviewer_model = create_reviewer_agent() if WEB_UI_ENABLE_REVIEWER else None

        APP_STATE.update({
            "ready": True,
            "attck_techniques": attck_techniques,
            "attck_tactics": attck_tactics,
            "tactic_model": tactic_model,
            "technique_model": technique_model,
            "reviewer_model": reviewer_model,
        })


def _extract_text_from_json(payload: dict) -> str:
    if isinstance(payload.get("sentences"), list):
        text_parts = []
        for sentence in payload["sentences"]:
            text = sentence.get("text", "")
            if text:
                text_parts.append(text)
        return " ".join(text_parts).strip()

    signal_text = payload.get("signal")
    if isinstance(signal_text, str) and signal_text.strip():
        return signal_text.strip()

    plain_text = payload.get("text")
    if isinstance(plain_text, str) and plain_text.strip():
        return plain_text.strip()

    return ""


def _build_report(report_id: str, text: str) -> dict:
    return {
        "id": report_id,
        "text": text,
        "techniques": [],
    }


def _extract_text_from_pdf_bytes(raw: bytes) -> str:
    """Ekstrak teks dari byte PDF yang diunggah (pakai pypdf)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="pypdf belum terpasang di server. Jalankan: pip install pypdf",
        )

    import io

    try:
        reader = PdfReader(io.BytesIO(raw))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
        return "\n\n".join(p for p in pages if p).strip()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Gagal membaca PDF: {exc}")


def _read_upload(upload: UploadFile | None, report_text: str, report_id: str) -> dict:
    if upload is None and not report_text.strip():
        raise HTTPException(status_code=400, detail="Upload file atau isi teks laporan.")

    if upload is not None:
        raw = upload.file.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File terlalu besar (maks 5MB).")

        filename = upload.filename or "report"
        suffix = Path(filename).suffix.lower()

        if suffix in {".json", ".mjson"}:
            try:
                payload = json.loads(raw.decode("utf-8", errors="ignore"))
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail=f"JSON invalid: {exc}")

            text = _extract_text_from_json(payload)
            if not text:
                raise HTTPException(status_code=400, detail="Isi laporan kosong atau tidak dikenali.")

            derived_id = payload.get("id") or payload.get("title") or Path(filename).stem
            report_id = report_id.strip() or derived_id
            return _build_report(report_id, text)

        if suffix == ".pdf":
            text = _extract_text_from_pdf_bytes(raw)
            if not text:
                raise HTTPException(
                    status_code=400,
                    detail="Teks PDF kosong/tidak terbaca (mungkin PDF hasil scan/gambar).",
                )
            report_id = report_id.strip() or Path(filename).stem
            return _build_report(report_id, text)

        text = raw.decode("utf-8", errors="ignore").strip()
        if not text:
            raise HTTPException(status_code=400, detail="Isi file kosong.")

        report_id = report_id.strip() or Path(filename).stem
        return _build_report(report_id, text)

    report_id = report_id.strip() or "manual-report"
    return _build_report(report_id, report_text.strip())


def _get_reviewer_model():
    """Reviewer model (lazy). Dibuat sekali lalu di-cache di APP_STATE."""
    reviewer_model = APP_STATE["reviewer_model"]
    if reviewer_model is None:
        reviewer_model = create_reviewer_agent()
        APP_STATE["reviewer_model"] = reviewer_model
    return reviewer_model


def _make_job_progress_cb(job: dict):
    """Callback progres per-agent untuk satu job console (analog batch).

    Menulis status agent yang sedang berjalan + menambah baris ke live log job.
    """

    def on_event(event: dict) -> None:
        agent = event.get("agent", "")
        if agent not in job["agents"]:
            return

        status = event.get("status", "")
        detail = event.get("detail", "")
        job["agents"][agent] = {
            "status": status,
            "detail": detail,
            "iteration": event.get("iteration", 0),
            "updated_at": time.time(),
        }
        job["agent_log"] = (job["agent_log"] + [{
            "ts": time.time(),
            "agent": agent,
            "status": status,
            "detail": detail,
        }])[-AGENT_LOG_MAX:]

    return on_event


def _run_job(job_id: str, report: dict, use_reviewer: bool) -> None:
    job = JOBS[job_id]
    job["status"] = "running"
    job["agents"] = _idle_agents()
    job["agent_log"] = []
    recorder = RunRecorder(entrypoint="web_app._run_job")
    result: dict = {}

    try:
        _ensure_initialized()
        reviewer_model = _get_reviewer_model() if use_reviewer else None
        result = process_report(
            report=report,
            attck_techniques=APP_STATE["attck_techniques"],
            attck_tactics=APP_STATE["attck_tactics"],
            tactic_model=APP_STATE["tactic_model"],
            technique_model=APP_STATE["technique_model"],
            reviewer_model=reviewer_model,
            progress_cb=_make_job_progress_cb(job),
        )

        tactics = []
        for tactic_id in result.get("tactics_identified", []):
            tactics.append({
                "id": tactic_id,
                "name": APP_STATE["attck_tactics"].get(tactic_id, ""),
            })

        techniques = []
        for technique_id in result.get("predicted_techniques", []):
            technique = APP_STATE["attck_techniques"].get(technique_id, {})
            techniques.append({
                "id": technique_id,
                "name": technique.get("name", ""),
            })

        job["result"] = {
            "report_id": result.get("report_id", ""),
            "tactics": tactics,
            "techniques": techniques,
            "stix_bundle": result.get("stix_bundle", {}),
            "report_text": report.get("text", ""),
            # reviewer_used = permintaan pengguna; reviewer_active = bukti
            # runtime bahwa reviewer benar-benar dipasang di pipeline.
            "reviewer_used": use_reviewer,
            "reviewer_active": bool(result.get("reviewer_invoked")),
            "coverage_ratio": result.get("coverage_ratio"),
            "report_chars": result.get("report_chars"),
        }
        job["status"] = "done"
    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)
        recorder.record_failure(report.get("id", ""))
    finally:
        # Jangan tinggalkan kartu agent dalam keadaan "running" setelah job berhenti.
        for key, agent in job.get("agents", {}).items():
            if agent.get("status") == "running":
                job["agents"][key] = {**agent, "status": "idle"}

        # Manifest job satu-laporan (tanpa berkas hasil untuk ditumpangi).
        try:
            JOB_MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
            stem = JOB_MANIFEST_DIR / f"job_{datetime.now():%Y%m%d_%H%M%S}_{job_id[:8]}.json"
            manifest = recorder.build(
                [result] if result else [], stem,
                reviewer_requested=use_reviewer,
                job_status=job["status"],
                report_id=report.get("id", ""),
            )
            write_manifest(stem, manifest)
            job["manifest"] = manifest
        except Exception as manifest_exc:
            print(f"[WARN] Gagal menulis manifest job: {manifest_exc}")


@app.get("/")
def index():
    if not WEB_UI_PATH.exists():
        raise HTTPException(status_code=404, detail="UI file tidak ditemukan.")
    return FileResponse(WEB_UI_PATH)


@app.get("/app")
def app_console():
    if not WEB_UI_APP_PATH.exists():
        raise HTTPException(status_code=404, detail="Halaman console tidak ditemukan.")
    return FileResponse(WEB_UI_APP_PATH)


@app.get("/batch")
def batch_page():
    if not WEB_UI_BATCH_PATH.exists():
        raise HTTPException(status_code=404, detail="Halaman batch tidak ditemukan.")
    return FileResponse(WEB_UI_BATCH_PATH)


@app.post("/api/process")
def process(
    report_file: UploadFile | None = File(default=None),
    report_text: str = Form(default=""),
    report_id: str = Form(default=""),
    use_reviewer: bool = Form(default=True),
):
    report = _read_upload(report_file, report_text, report_id)

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "status": "queued",
        "error": None,
        "result": None,
        "final": None,
        "agents": _idle_agents(),
        "agent_log": [],
    }

    # Jalankan di thread mandiri (daemon), bukan via BackgroundTasks yang menahan
    # worker threadpool request selama proses panjang (bisa bermenit-menit).
    worker = threading.Thread(
        target=_run_job, args=(job_id, report, use_reviewer), daemon=True
    )
    worker.start()
    return {"job_id": job_id}


@app.get("/api/batch/info")
def batch_info():
    """Info dataset untuk form UI (jumlah total laporan TRAM tersedia)."""
    try:
        total = len(load_tram_dataset(TRAM_DATA_DIR))
    except FileNotFoundError:
        total = 0
    return {"total_reports": total, "status": BATCH["status"]}


@app.post("/api/batch/start")
def batch_start(payload: dict | None = None):
    payload = payload or {}
    with BATCH["lock"]:
        if BATCH["status"] == "running":
            raise HTTPException(status_code=409, detail="Evaluasi batch sedang berjalan.")

        try:
            total_available = len(load_tram_dataset(TRAM_DATA_DIR))
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail=f"Dataset TRAM tidak ditemukan di {TRAM_DATA_DIR}.")
        if total_available == 0:
            raise HTTPException(status_code=500, detail="Dataset TRAM kosong.")

        count = payload.get("count", "all")
        if isinstance(count, str) and count.lower() in ("all", "semua"):
            n_reports = total_available
        else:
            try:
                n_reports = int(count)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="count harus angka atau 'all'.")
            if n_reports <= 0:
                raise HTTPException(status_code=400, detail="count harus > 0.")
            n_reports = min(n_reports, total_available)

        use_reviewer = bool(payload.get("reviewer", False))

        # Reset state run sebelumnya.
        BATCH.update({
            "status": "running",
            "total": n_reports,
            "done": 0,
            "current_index": 0,
            "current_report": "(memuat knowledge base...)",
            "started_at": time.time(),
            "finished_at": None,
            "reviewer": use_reviewer,
            "rows": [],
            "metrics": None,
            "tactic_metrics": None,
            "output_path": None,
            "error": None,
            "agents": _idle_agents(),
            "agent_log": [],
            "cancel": False,
        })

    worker = threading.Thread(
        target=_run_batch, args=(n_reports, use_reviewer), daemon=True
    )
    worker.start()
    return {"status": "running", "total": n_reports, "reviewer": use_reviewer}


@app.get("/api/batch/status")
def batch_status():
    snapshot = {k: v for k, v in BATCH.items() if k not in ("lock", "cancel")}
    return JSONResponse(snapshot)


@app.post("/api/batch/cancel")
def batch_cancel():
    if BATCH["status"] != "running":
        raise HTTPException(status_code=409, detail="Tidak ada evaluasi batch yang berjalan.")
    BATCH["cancel"] = True
    return {"status": "cancelling", "detail": "Berhenti setelah laporan yang sedang diproses selesai."}


@app.get("/api/status/{job_id}")
async def status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
    return {
        "job_id": job_id,
        "status": job["status"],
        "error": job.get("error"),
        "agents": job.get("agents", {}),
        "agent_log": job.get("agent_log", []),
    }


@app.get("/api/results/{job_id}")
async def results(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail="Job belum selesai.")
    return job["result"]


@app.post("/api/validate/{job_id}")
def validate(job_id: str, payload: dict):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail="Job belum selesai.")

    accepted_tactics = payload.get("accepted_tactics") or []
    accepted_techniques = payload.get("accepted_techniques") or []
    rejected_tactics = payload.get("rejected_tactics") or []
    rejected_techniques = payload.get("rejected_techniques") or []

    if not accepted_tactics and not rejected_tactics:
        accepted_tactics = [item["id"] for item in job["result"]["tactics"]]
    if not accepted_techniques and not rejected_techniques:
        accepted_techniques = [item["id"] for item in job["result"]["techniques"]]

    accepted_tactics = [t for t in accepted_tactics if t in APP_STATE["attck_tactics"]]
    accepted_techniques = [t for t in accepted_techniques if t in APP_STATE["attck_techniques"]]

    report_id = job["result"]["report_id"]
    report_text = job["result"].get("report_text", "")

    stix_bundle = build_stix_bundle(
        report_id=report_id,
        report_text=report_text,
        techniques=accepted_techniques,
        attck_techniques=APP_STATE["attck_techniques"],
    )

    final_report = {
        "report_id": report_id,
        "accepted_tactics": accepted_tactics,
        "accepted_techniques": accepted_techniques,
        "rejected_tactics": rejected_tactics,
        "rejected_techniques": rejected_techniques,
        "stix_bundle": stix_bundle,
    }

    job["final"] = final_report
    return final_report


@app.get("/api/final/{job_id}")
def final(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
    if not job.get("final"):
        raise HTTPException(status_code=404, detail="Final report belum dibuat.")
    return JSONResponse(job["final"])


def _resolve_report_items(job: dict) -> tuple[list[dict], list[dict], dict]:
    """Tentukan taktik & teknik untuk laporan PDF.

    Pakai hasil validasi (accepted_*) jika analis sudah memvalidasi; jika belum,
    pakai hasil mentah dari pipeline.
    """
    result = job.get("result") or {}
    final_report = job.get("final")

    if final_report:
        tactics = [
            {"id": tid, "name": APP_STATE["attck_tactics"].get(tid, "")}
            for tid in final_report.get("accepted_tactics", [])
        ]
        techniques = [
            {"id": tid, "name": APP_STATE["attck_techniques"].get(tid, {}).get("name", "")}
            for tid in final_report.get("accepted_techniques", [])
        ]
        stix_bundle = final_report.get("stix_bundle", {})
    else:
        tactics = result.get("tactics", [])
        techniques = result.get("techniques", [])
        stix_bundle = result.get("stix_bundle", {})

    return tactics, techniques, stix_bundle


@app.get("/api/report/{job_id}.pdf")
def report_pdf(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail="Job belum selesai.")

    result = job.get("result") or {}
    tactics, techniques, stix_bundle = _resolve_report_items(job)
    report_text = result.get("report_text", "")

    # Cari kalimat rujukan (evidence) untuk tiap taktik/teknik dari teks laporan.
    tactic_evidence, technique_evidence = build_evidence_map(
        report_text=report_text,
        tactics=tactics,
        techniques=techniques,
        attck_techniques=APP_STATE["attck_techniques"],
        attck_tactics=APP_STATE["attck_tactics"],
    )

    pdf_bytes = build_pdf_report(
        report_id=result.get("report_id", job_id),
        report_text=report_text,
        tactics=tactics,
        techniques=techniques,
        stix_bundle=stix_bundle,
        attck_techniques=APP_STATE["attck_techniques"],
        tactic_evidence=tactic_evidence,
        technique_evidence=technique_evidence,
    )

    safe_id = (result.get("report_id") or job_id).replace("/", "_").replace("\\", "_")[:80]
    filename = f"ttp_report_{safe_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
    )
