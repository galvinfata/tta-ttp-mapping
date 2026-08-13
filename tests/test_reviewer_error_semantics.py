"""Tes semantik error reviewer: kegagalan TEKNIS harus fail-open (lanjut ke
post_process tanpa loop revisi, feedback kosong), penolakan SUBSTANTIF tetap
memicu revisi, dan iterasi tidak pernah melewati LLM_REVIEW_MAX_ITER.

Jalankan (tanpa LLM/jaringan, semuanya di-mock):
    python -m unittest tests.test_reviewer_error_semantics -v
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agents import reviewer_agent
from pipeline import orchestrator


FAKE_MODEL = {"client": None, "model": "fake-model", "fallback_model": None}

ATTCK_TACTICS = {"TA0001": "Initial Access"}
ATTCK_TECHNIQUES = {
    "T1566": {
        "id": "T1566",
        "name": "Phishing",
        "description": "Adversaries may send phishing messages.",
        "tactics": ["initial-access"],
        "stix_id": "attack-pattern--x",
        "domains": ["enterprise-attack"],
    }
}


def _call_review(fake_complete_chat):
    """Panggil review_tactics_and_techniques dengan _complete_chat di-mock."""
    with mock.patch.object(reviewer_agent, "_complete_chat", fake_complete_chat), \
         mock.patch.object(reviewer_agent.time, "sleep"):
        return reviewer_agent.review_tactics_and_techniques(
            model=FAKE_MODEL,
            report_text="Attackers sent spear-phishing emails.",
            tactics=["TA0001"],
            techniques=["T1566"],
            attck_tactics=ATTCK_TACTICS,
            attck_techniques=ATTCK_TECHNIQUES,
        )


class TestReviewerAgentErrorPaths(unittest.TestCase):
    """Level agent: jalur error harus mengembalikan key 'error' + feedback kosong."""

    def test_llm_exception_returns_error_marker(self):
        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        result = _call_review(boom)
        self.assertFalse(result["is_valid"])
        self.assertEqual(result["feedback"], "")
        self.assertTrue(result.get("error", "").startswith("exception:"))

    def test_empty_response_returns_error_marker(self):
        result = _call_review(lambda *a, **k: "")
        self.assertEqual(result["feedback"], "")
        self.assertEqual(result.get("error"), "empty_response")

    def test_malformed_json_returns_error_marker(self):
        result = _call_review(lambda *a, **k: "%%% ###")
        self.assertEqual(result["feedback"], "")
        self.assertEqual(result.get("error"), "json_parse")
        self.assertEqual(result.get("raw_response"), "%%% ###")

    def test_genuine_rejection_has_no_error_marker(self):
        result = _call_review(
            lambda *a, **k: '{"is_valid": false, "feedback": "Remove T1105: no download behavior."}'
        )
        self.assertFalse(result["is_valid"])
        self.assertEqual(result["feedback"], "Remove T1105: no download behavior.")
        self.assertIsNone(result.get("error"))

    def test_valid_result_has_no_error_marker(self):
        result = _call_review(lambda *a, **k: '{"is_valid": true, "feedback": ""}')
        self.assertTrue(result["is_valid"])
        self.assertIsNone(result.get("error"))


def _review_state(**overrides):
    state = {
        "reviewer_model": FAKE_MODEL,
        "report_id": "report-x",
        "report_text": "text",
        "tactics_identified": ["TA0001"],
        "techniques_raw": ["T1566"],
        "attck_tactics": ATTCK_TACTICS,
        "attck_techniques": ATTCK_TECHNIQUES,
        "review_iterations": 0,
        "reviewer_error_count": 0,
    }
    state.update(overrides)
    return state


ERROR_RESULT = {"is_valid": False, "feedback": "", "error": "empty_response", "raw_response": ""}
INVALID_RESULT = {"is_valid": False, "feedback": "Add TA0002: PowerShell execution described."}
VALID_RESULT = {"is_valid": True, "feedback": ""}


class TestReviewNodeSemantics(unittest.TestCase):
    """Level node: retry 1x pada error, lalu fail-open; INVALID asli lolos apa adanya."""

    def test_error_twice_fails_open_without_feedback(self):
        fake = mock.Mock(side_effect=[ERROR_RESULT, ERROR_RESULT])
        with mock.patch.object(orchestrator, "review_tactics_and_techniques", fake):
            out = orchestrator._review_node(_review_state())
        self.assertEqual(fake.call_count, 2)  # 1 panggilan + 1 retry, tidak lebih
        self.assertTrue(out["review_is_valid"])  # fail-open
        self.assertEqual(out["reviewer_feedback"], "")  # teks error tak bocor
        self.assertEqual(out["reviewer_error_count"], 2)
        self.assertEqual(out["review_iterations"], 1)

    def test_error_then_success_uses_retry_result(self):
        fake = mock.Mock(side_effect=[ERROR_RESULT, INVALID_RESULT])
        with mock.patch.object(orchestrator, "review_tactics_and_techniques", fake):
            out = orchestrator._review_node(_review_state())
        self.assertEqual(fake.call_count, 2)
        self.assertFalse(out["review_is_valid"])  # penolakan asli dari retry dipakai
        self.assertEqual(out["reviewer_feedback"], INVALID_RESULT["feedback"])
        self.assertEqual(out["reviewer_error_count"], 1)

    def test_genuine_invalid_passes_through_without_retry(self):
        fake = mock.Mock(return_value=INVALID_RESULT)
        with mock.patch.object(orchestrator, "review_tactics_and_techniques", fake):
            out = orchestrator._review_node(_review_state())
        self.assertEqual(fake.call_count, 1)
        self.assertFalse(out["review_is_valid"])
        self.assertEqual(out["reviewer_feedback"], INVALID_RESULT["feedback"])
        self.assertEqual(out["reviewer_error_count"], 0)

    def test_reviewer_disabled_path_unchanged(self):
        out = orchestrator._review_node(_review_state(reviewer_model=None))
        self.assertTrue(out["review_is_valid"])
        self.assertEqual(out["reviewer_feedback"], "")

    def test_should_revise_conditions(self):
        self.assertEqual(
            orchestrator._should_revise({"review_is_valid": True, "review_iterations": 1}),
            "post_process",
        )
        self.assertEqual(
            orchestrator._should_revise({"review_is_valid": False, "review_iterations": 1}),
            "tactic_extraction",
        )
        # Iterasi tidak boleh melewati MAX_ITER (default 2).
        self.assertEqual(
            orchestrator._should_revise({"review_is_valid": False, "review_iterations": 2}),
            "post_process",
        )


class TestFullGraphBehavior(unittest.TestCase):
    """Level graph: error reviewer tidak boleh memicu revisi; INVALID asli harus."""

    def _run_pipeline(self, review_side_effect):
        tactic_calls = []
        technique_calls = []

        def fake_identify_tactics(model, report_text, tactic_list=None, reviewer_feedback=""):
            tactic_calls.append(reviewer_feedback)
            return ["TA0001"]

        def fake_extract_techniques(
            model, report_text, attck_techniques, reviewer_feedback="", telemetry=None
        ):
            technique_calls.append(reviewer_feedback)
            # Agen asli mengisi telemetry di tempat (jangkauan pembacaan &
            # kandidat yang tampil); stub meniru kontrak itu seadanya.
            if telemetry is not None:
                telemetry.update({
                    "report_chars": len(report_text),
                    "coverage_chars": len(report_text),
                    "coverage_ratio": 1.0,
                    "chunks": 1,
                    "candidates_shown": ["T1566"],
                })
            return ["T1566"]

        fake_review = mock.Mock(side_effect=review_side_effect)
        with mock.patch.object(orchestrator, "identify_tactics", fake_identify_tactics), \
             mock.patch.object(orchestrator, "extract_techniques", fake_extract_techniques), \
             mock.patch.object(orchestrator, "review_tactics_and_techniques", fake_review):
            result = orchestrator.process_report(
                report={"id": "report-x", "text": "text", "techniques": ["T1566"]},
                attck_techniques=ATTCK_TECHNIQUES,
                attck_tactics=ATTCK_TACTICS,
                tactic_model=FAKE_MODEL,
                technique_model=FAKE_MODEL,
                reviewer_model=FAKE_MODEL,
            )
        return result, tactic_calls, technique_calls, fake_review

    def test_reviewer_error_does_not_trigger_revision(self):
        result, tactic_calls, technique_calls, fake_review = self._run_pipeline(
            [ERROR_RESULT, ERROR_RESULT]
        )
        # Agen hanya dipanggil sekali — tidak ada loop revisi palsu.
        self.assertEqual(len(tactic_calls), 1)
        self.assertEqual(len(technique_calls), 1)
        self.assertEqual(tactic_calls[0], "")  # tak ada teks error tersuntik
        self.assertEqual(fake_review.call_count, 2)  # 1 + 1 retry
        # Pipeline tetap menghasilkan output & telemetri aditif tercatat.
        self.assertEqual(result["predicted_techniques"], ["T1566"])
        self.assertEqual(result["reviewer_error_count"], 2)
        self.assertTrue(result["reviewer_errored"])
        # Field lama tetap ada (kontrak skrip evaluasi).
        for key in ("report_id", "predicted_techniques", "ground_truth",
                    "tactics_identified", "stix_bundle"):
            self.assertIn(key, result)

    def test_ground_truth_tactics_derived_from_gt_techniques(self):
        """GT taktik ikut tersimpan di hasil, diturunkan dari GT teknik.

        Dataset TRAM II hanya melabeli teknik; taktiknya diturunkan lewat fase
        kill-chain. Fixture: T1566 -> initial-access -> TA0001.
        """
        result, _, _, _ = self._run_pipeline([VALID_RESULT])
        self.assertIn("ground_truth_tactics", result)
        self.assertEqual(result["ground_truth_tactics"], ["TA0001"])
        # Diturunkan dari ground_truth, BUKAN disalin dari prediksi taktik.
        self.assertIsNot(result["ground_truth_tactics"], result["tactics_identified"])

    def test_genuine_rejection_triggers_revision_capped_at_max_iter(self):
        result, tactic_calls, technique_calls, fake_review = self._run_pipeline(
            [INVALID_RESULT, INVALID_RESULT, INVALID_RESULT]
        )
        # MAX_ITER default 2: agen jalan 2x (awal + 1 revisi), review 2x, berhenti.
        self.assertEqual(len(tactic_calls), 2)
        self.assertEqual(fake_review.call_count, 2)
        # Revisi kedua menerima feedback substantif, bukan teks error.
        self.assertEqual(tactic_calls[1], INVALID_RESULT["feedback"])
        self.assertEqual(result["reviewer_error_count"], 0)
        self.assertFalse(result["reviewer_errored"])

    def test_reviewer_valid_first_try_single_pass(self):
        result, tactic_calls, _, fake_review = self._run_pipeline([VALID_RESULT])
        self.assertEqual(len(tactic_calls), 1)
        self.assertEqual(fake_review.call_count, 1)
        self.assertFalse(result["reviewer_errored"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
