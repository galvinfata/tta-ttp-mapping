import os
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agents.tactic_agent import identify_tactics
from agents.technique_agent import extract_techniques
from evaluation.evaluator import derive_tactic_ground_truth
from pipeline.reconciler import reconcile_results
from pipeline.validator import validate_techniques
from reporting.stix_builder import build_stix_bundle
from agents.reviewer_agent import review_tactics_and_techniques


class PipelineState(TypedDict):
    report: dict
    attck_techniques: dict
    attck_tactics: dict
    tactic_model: Any
    technique_model: Any
    reviewer_model: Any
    progress_cb: Any
    report_id: str
    report_text: str
    ground_truth: list[str]
    tactics_identified: list[str]
    techniques_raw: list[str]
    predicted_techniques: list[str]
    stix_bundle: dict
    reviewer_feedback: str
    review_is_valid: bool
    review_iterations: int
    reviewer_error_count: int
    technique_telemetry: dict


def _emit(
    state: PipelineState,
    agent: str,
    status: str,
    detail: str = "",
    iteration: int | None = None,
) -> None:
    """Kirim event progres per-agent ke pemanggil (UI batch/console).

    Callback bersifat opsional dan best-effort: error di sisi konsumen tidak
    boleh menjatuhkan pipeline.
    """
    callback = state.get("progress_cb")
    if not callback:
        return
    try:
        callback({
            "agent": agent,            # tactic | technique | reviewer | reconciler
            "status": status,          # running | done | skipped | error
            "detail": detail,
            # review_iterations di state baru bertambah setelah node review selesai,
            # jadi node reviewer mengirim nomor iterasi berjalannya sendiri.
            "iteration": state.get("review_iterations", 0) if iteration is None else iteration,
            "report_id": state.get("report_id", ""),
        })
    except Exception:
        pass


def _input_report_node(state: PipelineState) -> PipelineState:
    report = state["report"]
    report_id = report["id"]
    report_text = report["text"]

    print(f"Memproses: {report_id}")

    return {
        "report_id": report_id,
        "report_text": report_text,
        "ground_truth": report.get("techniques", []),
    }


def _tactic_extraction_node(state: PipelineState) -> PipelineState:
    # Feedback reviewer (kalau ada, mis. iterasi revisi) diteruskan agar agent
    # benar-benar merevisi jawabannya — inilah inti "debat" antar-agent.
    feedback = state.get("reviewer_feedback", "") if not state.get("review_is_valid", False) else ""
    _emit(
        state, "tactic", "running",
        "Merevisi taktik dari feedback reviewer" if feedback else "Mengidentifikasi taktik dari teks laporan",
    )
    tactics = identify_tactics(
        state["tactic_model"],
        state["report_text"],
        state.get("attck_tactics", {}),
        reviewer_feedback=feedback,
    )
    print(f"  Taktik: {tactics}")
    _emit(state, "tactic", "done", f"{len(tactics)} taktik: {', '.join(tactics) if tactics else '-'}")

    return {
        "tactics_identified": tactics,
    }


def _technique_extraction_node(state: PipelineState) -> PipelineState:
    feedback = state.get("reviewer_feedback", "") if not state.get("review_is_valid", False) else ""
    _emit(
        state, "technique", "running",
        "Merevisi teknik dari feedback reviewer" if feedback else "Mengekstrak teknik (retrieval + LLM)",
    )
    # Telemetri jangkauan pembacaan & kandidat yang benar-benar tampil di prompt
    # (diisi di tempat oleh extract_techniques; tidak mengubah hasil pemetaan).
    telemetry: dict = {}
    techniques = extract_techniques(
        state["technique_model"],
        state["report_text"],
        state["attck_techniques"],
        reviewer_feedback=feedback,
        telemetry=telemetry,
    )
    print(f"  Teknik awal: {techniques}")
    _emit(state, "technique", "done", f"{len(techniques)} teknik kandidat")

    return {
        "techniques_raw": techniques,
        "technique_telemetry": telemetry,
    }


def _review_node(state: PipelineState) -> PipelineState:
    reviewer_model = state.get("reviewer_model")
    if not reviewer_model:
        _emit(state, "reviewer", "skipped", "Reviewer nonaktif — hasil langsung diteruskan")
        return {
            "review_is_valid": True,
            "reviewer_feedback": "",
            "review_iterations": state.get("review_iterations", 0) + 1,
        }

    iteration = state.get("review_iterations", 0) + 1
    errors_this_round = 0
    _emit(state, "reviewer", "running", f"Menilai taktik & teknik (iterasi {iteration})", iteration)

    # Error TEKNIS (exception/timeout/JSON rusak/respons kosong) ≠ penolakan
    # substantif. Coba maksimal 2x panggilan (1x retry); kalau tetap error,
    # FAIL-OPEN: anggap valid agar pipeline lanjut ke post_process tanpa loop
    # revisi palsu, dan feedback dikosongkan agar teks error tak masuk prompt.
    result = {}
    for call_idx in (1, 2):
        result = review_tactics_and_techniques(
            model=reviewer_model,
            report_text=state["report_text"],
            tactics=state.get("tactics_identified", []),
            techniques=state.get("techniques_raw", []),
            attck_tactics=state.get("attck_tactics", {}),
            attck_techniques=state.get("attck_techniques", {}),
        )
        error_kind = result.get("error")
        if not error_kind:
            break
        errors_this_round += 1
        raw_excerpt = str(result.get("raw_response", ""))[:120]
        print(
            f"  [REVIEWER-ERROR] report={state.get('report_id', '')} "
            f"iter={iteration} call={call_idx}/2 kind={error_kind} raw='{raw_excerpt}'"
        )

    error_count = state.get("reviewer_error_count", 0) + errors_this_round

    if result.get("error"):
        print(
            f"  [REVIEWER-ERROR] report={state.get('report_id', '')} "
            f"iter={iteration} fail-open: review dilewati (dianggap valid, tanpa feedback)"
        )
        _emit(
            state, "reviewer", "error",
            f"Error teknis ({result.get('error')}) — fail-open, hasil diteruskan apa adanya",
            iteration,
        )
        return {
            "review_is_valid": True,
            "reviewer_feedback": "",
            "review_iterations": iteration,
            "reviewer_error_count": error_count,
        }

    print(f"  Review: {result}")

    is_valid = bool(result.get("is_valid"))
    feedback = str(result.get("feedback", "")).strip()
    _emit(
        state, "reviewer", "done",
        "Disetujui — lanjut ke rekonsiliasi" if is_valid
        else f"Ditolak (iterasi {iteration}) — minta revisi: {feedback[:160] or 'tanpa detail'}",
        iteration,
    )

    return {
        "review_is_valid": bool(result.get("is_valid")),
        "reviewer_feedback": result.get("feedback", ""),
        "review_iterations": iteration,
        "reviewer_error_count": error_count,
    }


def _should_revise(state: PipelineState) -> str:
    max_iterations = int(os.getenv("LLM_REVIEW_MAX_ITER", "2"))
    if state.get("review_is_valid"):
        return "post_process"
    if state.get("review_iterations", 0) >= max_iterations:
        return "post_process"
    return "tactic_extraction"


def _post_process_node(state: PipelineState) -> PipelineState:
    _emit(state, "reconciler", "running", "Rekonsiliasi taktik-teknik, validasi ID, dan build STIX")
    reconciled = reconcile_results(
        state.get("tactics_identified", []),
        state.get("techniques_raw", []),
        state["attck_techniques"],
    )
    print(f"  Setelah rekonsiliasi: {reconciled}")

    validation = validate_techniques(reconciled, state["attck_techniques"])
    final_techniques = validation["valid"]
    print(f"  Final valid: {final_techniques}")

    stix_bundle = build_stix_bundle(
        report_id=state["report_id"],
        report_text=state["report_text"],
        techniques=final_techniques,
        attck_techniques=state["attck_techniques"],
    )
    _emit(state, "reconciler", "done", f"{len(final_techniques)} teknik final tervalidasi")

    return {
        "predicted_techniques": final_techniques,
        "stix_bundle": stix_bundle,
    }


def _build_graph():
    graph = StateGraph(PipelineState)

    graph.add_node("input_report", _input_report_node)
    graph.add_node("tactic_extraction", _tactic_extraction_node)
    graph.add_node("technique_extraction", _technique_extraction_node)
    graph.add_node("review", _review_node)
    graph.add_node("post_process", _post_process_node)

    graph.add_edge(START, "input_report")
    graph.add_edge("input_report", "tactic_extraction")
    graph.add_edge("tactic_extraction", "technique_extraction")
    graph.add_edge("technique_extraction", "review")
    graph.add_conditional_edges("review", _should_revise, {
        "tactic_extraction": "tactic_extraction",
        "post_process": "post_process",
    })
    graph.add_edge("post_process", END)

    return graph.compile()


_PIPELINE = _build_graph()


def process_report(
    report: dict,
    attck_techniques: dict,
    attck_tactics: dict,
    tactic_model,
    technique_model,
    reviewer_model=None,
    progress_cb=None,
) -> dict:
    """
    Memproses satu laporan CTI melalui seluruh pipeline.

    Args:
        progress_cb: callback opsional yang dipanggil tiap agent mulai/selesai
            dengan dict {agent, status, detail, iteration, report_id}.

    Returns:
        dict: hasil pemetaan lengkap
    """

    initial_state: PipelineState = {
        "report": report,
        "attck_techniques": attck_techniques,
        "attck_tactics": attck_tactics,
        "tactic_model": tactic_model,
        "technique_model": technique_model,
        "reviewer_model": reviewer_model,
        "progress_cb": progress_cb,
        "report_id": "",
        "report_text": "",
        "ground_truth": [],
        "tactics_identified": [],
        "techniques_raw": [],
        "predicted_techniques": [],
        "stix_bundle": {},
        "reviewer_feedback": "",
        "review_is_valid": False,
        "review_iterations": 0,
        "reviewer_error_count": 0,
        "technique_telemetry": {},
    }

    final_state = _PIPELINE.invoke(initial_state)

    # Field telemetri reviewer bersifat ADITIF — jangan mengubah/merename field
    # lama karena skrip re-scoring evaluasi membaca file results JSON ini.
    reviewer_error_count = final_state.get("reviewer_error_count", 0)
    telemetry = final_state.get("technique_telemetry") or {}
    return {
        "report_id": final_state["report_id"],
        "predicted_techniques": final_state["predicted_techniques"],
        "ground_truth": final_state["ground_truth"],
        # GT taktik DITURUNKAN dari GT teknik lewat fase kill-chain — dataset
        # TRAM II tidak memberi label taktik. Disimpan agar berkas hasil bisa
        # dibaca berdampingan (GT teknik | GT taktik | prediksi teknik | prediksi
        # taktik) tanpa perlu memuat ulang KB ATT&CK. Sumber kebenarannya tetap
        # derive_tactic_ground_truth(); evaluasi taktik tidak membaca field ini.
        "ground_truth_tactics": derive_tactic_ground_truth(
            final_state["ground_truth"], attck_techniques
        ),
        "tactics_identified": final_state["tactics_identified"],
        "stix_bundle": final_state["stix_bundle"],
        "reviewer_error_count": reviewer_error_count,
        "reviewer_errored": reviewer_error_count > 0,
        # --- Instrumentasi jangkauan pembacaan & context window (aditif) ---
        # reviewer_invoked = bukti RUNTIME apakah Reviewer Agent benar-benar
        # dipakai pada laporan ini (bukan sekadar nilai env), dipakai run
        # manifest untuk menentukan reviewer_active.
        "reviewer_invoked": bool(reviewer_model),
        "review_iterations": final_state.get("review_iterations", 0),
        "report_chars": telemetry.get("report_chars"),
        "coverage_chars": telemetry.get("coverage_chars"),
        "coverage_ratio": telemetry.get("coverage_ratio"),
        "chunks_processed": telemetry.get("chunks"),
        "candidates_shown": telemetry.get("candidates_shown", []),
        "candidates_dropped_budget": telemetry.get("candidates_dropped_budget", 0),
        "prompt_overflow_calls": telemetry.get("prompt_overflow_calls", 0),
        # --- Keselarasan dengan peringkat retrieval (Tahap 2b) ---
        # Peta {technique_id: peringkat retrieval}. rank_of_selected = apa yang
        # dipilih LLM; rank_of_accepted = yang lolos TECHNIQUE_ACCEPT_TOP_N;
        # rank_of_filtered_out = yang dipilih LLM tapi DIBUANG filter — inilah
        # kontribusi LLM yang tidak pernah sampai ke keluaran sistem.
        "rank_of_selected": telemetry.get("rank_of_selected", {}),
        "rank_of_accepted": telemetry.get("rank_of_accepted", {}),
        "rank_of_filtered_out": telemetry.get("rank_of_filtered_out", {}),
        "candidate_shuffle_seed": telemetry.get("candidate_shuffle_seed"),
    }
