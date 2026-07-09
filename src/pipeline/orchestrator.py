import os
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agents.tactic_agent import identify_tactics
from agents.technique_agent import extract_techniques
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
    tactics = identify_tactics(
        state["tactic_model"],
        state["report_text"],
        state.get("attck_tactics", {}),
        reviewer_feedback=feedback,
    )
    print(f"  Taktik: {tactics}")

    return {
        "tactics_identified": tactics,
    }


def _technique_extraction_node(state: PipelineState) -> PipelineState:
    feedback = state.get("reviewer_feedback", "") if not state.get("review_is_valid", False) else ""
    techniques = extract_techniques(
        state["technique_model"],
        state["report_text"],
        state["attck_techniques"],
        reviewer_feedback=feedback,
    )
    print(f"  Teknik awal: {techniques}")

    return {
        "techniques_raw": techniques,
    }


def _review_node(state: PipelineState) -> PipelineState:
    reviewer_model = state.get("reviewer_model")
    if not reviewer_model:
        return {
            "review_is_valid": True,
            "reviewer_feedback": "",
            "review_iterations": state.get("review_iterations", 0) + 1,
        }

    iteration = state.get("review_iterations", 0) + 1
    errors_this_round = 0

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
        return {
            "review_is_valid": True,
            "reviewer_feedback": "",
            "review_iterations": iteration,
            "reviewer_error_count": error_count,
        }

    print(f"  Review: {result}")

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
    reviewer_model=None
) -> dict:
    """
    Memproses satu laporan CTI melalui seluruh pipeline.
    
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
    }

    final_state = _PIPELINE.invoke(initial_state)

    # Field telemetri reviewer bersifat ADITIF — jangan mengubah/merename field
    # lama karena skrip re-scoring evaluasi membaca file results JSON ini.
    reviewer_error_count = final_state.get("reviewer_error_count", 0)
    return {
        "report_id": final_state["report_id"],
        "predicted_techniques": final_state["predicted_techniques"],
        "ground_truth": final_state["ground_truth"],
        "tactics_identified": final_state["tactics_identified"],
        "stix_bundle": final_state["stix_bundle"],
        "reviewer_error_count": reviewer_error_count,
        "reviewer_errored": reviewer_error_count > 0,
    }
