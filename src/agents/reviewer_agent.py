import os
import json
import re
import time
from dotenv import load_dotenv
from openai import OpenAI

from agents.prompt_budget import check_budget, record_prompt

load_dotenv()


MAX_RETRIES_PER_MODEL = 3
BASE_BACKOFF_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = int(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "300"))
LOCAL_LLM_REPORT_MAX_CHARS = int(os.getenv("LOCAL_LLM_REPORT_MAX_CHARS", "2000"))
LOCAL_LLM_MAX_TOKENS_REVIEWER = int(os.getenv("LOCAL_LLM_MAX_TOKENS_REVIEWER", "512"))
DEBUG_MODE = os.getenv("DEBUG_AGENT", "false").lower() == "true"
DISABLE_THINKING = os.getenv("LLM_DISABLE_THINKING", "true").lower() == "true"
# Qwen3.5 mengabaikan enable_thinking=false & /no_think; di LM Studio reasoning
# model itu dimatikan lewat parameter reasoning_effort="none". Kosongkan env
# ini untuk tidak mengirim parameternya sama sekali.
REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "none").strip()


def _strip_think_blocks(text: str) -> str:
    """Buang blok reasoning <think>...</think> dari output model thinking (mis. Qwen3)."""
    if not text:
        return text
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"</?think>", "", text, flags=re.IGNORECASE)
    return text.strip()


def _is_transient_error(error_text: str) -> bool:
    transient_markers = [
        "remote end closed connection",
        "connection aborted",
        "connection reset",
        "forcibly closed",
        "service response error",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "429",
        "rate limit",
        "503",
        "502",
        "504",
        "read timed out",
        "connection refused",
        "temporary failure",
        # LM Studio kadang menolak request dengan 'Context size has been
        # exceeded' padahal prompt muat (kondisi server sesaat setelah timeout
        # menumpuk; lihat results/laporan_run_parsial_20260710.md bag. 5).
        # Retry dengan backoff memberi server kesempatan pulih.
        "context size has been exceeded",
        # Error engine LM Studio saat model di-reload/unload di tengah run
        # (mis. ganti context length dari GUI) — sembuh sendiri setelah
        # model selesai dimuat ulang.
        "predict request failed",
        "fetch failed",
        "model is unloaded",
    ]
    return any(marker in error_text for marker in transient_markers)


def create_reviewer_agent():
    """Inisialisasi reviewer LM Studio local server (OpenAI-compatible)."""
    base_url = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:1234").rstrip("/")
    model_name = os.getenv("LOCAL_LLM_REVIEWER_MODEL", "qwen/qwen3-4b")
    api_key = os.getenv("LOCAL_LLM_API_KEY", "")
    fallback_model = os.getenv("LOCAL_LLM_REVIEWER_FALLBACK_MODEL", "").strip()
    if not fallback_model:
        fallback_model = os.getenv("LOCAL_LLM_FALLBACK_MODEL", "").strip() or None

    client = OpenAI(
        base_url=f"{base_url}/v1",
        api_key=api_key or "lm-studio",
    )

    return {
        "client": client,
        "base_url": base_url,
        "api_key": api_key,
        "model": model_name,
        "fallback_model": fallback_model,
    }


def _complete_chat(model: dict, attempt_model: str, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    create_kwargs = dict(
        model=attempt_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        top_p=1.0,
        max_tokens=max_tokens,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if DISABLE_THINKING:
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
        if REASONING_EFFORT:
            extra_body["reasoning_effort"] = REASONING_EFFORT
        create_kwargs["extra_body"] = extra_body

    try:
        response = model["client"].chat.completions.create(**create_kwargs)
    except TypeError:
        create_kwargs.pop("extra_body", None)
        response = model["client"].chat.completions.create(**create_kwargs)
    except Exception as exc:
        error_text = str(exc).lower()
        extra_body = create_kwargs.get("extra_body") or {}
        if "reasoning" in error_text and "reasoning_effort" in extra_body:
            # Server menolak parameter reasoning_effort → ulang tanpa itu.
            extra_body.pop("reasoning_effort", None)
            response = model["client"].chat.completions.create(**create_kwargs)
        else:
            raise

    message = response.choices[0].message
    content = _strip_think_blocks((message.content or "").strip())
    if content:
        return content

    reasoning_content = getattr(message, "reasoning_content", None)
    if isinstance(reasoning_content, str) and reasoning_content.strip():
        return _strip_think_blocks(reasoning_content.strip())

    model_extra = getattr(message, "model_extra", None) or {}
    extra_reasoning = model_extra.get("reasoning_content")
    if isinstance(extra_reasoning, str) and extra_reasoning.strip():
        return _strip_think_blocks(extra_reasoning.strip())

    return ""


def _extract_json_object(response_text: str) -> dict:
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", response_text, re.DOTALL | re.IGNORECASE)
    if match:
        json_str = match.group(1)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    match = re.search(r"\{[\s\S]*\}", response_text)
    if match:
        json_str = match.group(0)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("Could not find JSON object in response", response_text, 0)


def _fallback_review_parse(response_text: str) -> dict | None:
    if not response_text or not response_text.strip():
        return None

    text = response_text.strip()
    lower = text.lower()

    match = re.search(r"\bis_valid\b\s*[:=]\s*(true|false)", lower)
    if match:
        is_valid = match.group(1) == "true"
    else:
        if re.search(r"\b(inconsistent|invalid|incorrect|not consistent)\b", lower):
            is_valid = False
        elif re.search(r"\b(consistent|valid|correct)\b", lower):
            is_valid = True
        else:
            return None

    feedback = ""
    feedback_match = re.search(r"\bfeedback\b\s*[:=]\s*(.+)", text, re.IGNORECASE)
    if feedback_match:
        feedback = feedback_match.group(1).strip().strip("\"'")
        feedback = feedback.splitlines()[0].strip()

    return {"is_valid": is_valid, "feedback": feedback}


def review_tactics_and_techniques(
    model: dict,
    report_text: str,
    tactics: list[str],
    techniques: list[str],
    attck_tactics: dict,
    attck_techniques: dict,
) -> dict:
    """
    Menilai konsistensi output Tactic & Technique terhadap teks laporan.

    Returns:
        dict: {"is_valid": bool, "feedback": str}
    """

    report_excerpt = report_text[:LOCAL_LLM_REPORT_MAX_CHARS]
    tactics_summary = ", ".join(
        [f"{tid} ({attck_tactics.get(tid, '')})" for tid in tactics]
    ) or "None"

    technique_lines = []
    for tid in techniques:
        data = attck_techniques.get(tid, {})
        name = data.get("name", "")
        tactic_tags = ", ".join(data.get("tactics", []))
        description = data.get("description", "").replace("\n", " ")
        short_desc = description[:240]
        technique_lines.append(
            f"- {tid}: {name} | tactics: {tactic_tags} | {short_desc}"
        )

    technique_summary = "\n".join(technique_lines) or "None"

    prompt = f"""TASK: Review whether the tactics and techniques below are consistent with the
report excerpt.

REPORT EXCERPT:
\"\"\"{report_excerpt}\"\"\"

SELECTED TACTICS:
{tactics_summary}

SELECTED TECHNIQUES:
{technique_summary}

CHECKS:
1. Every selected technique must correspond to a behavior described in the
   report (not just a tool name, IOC, or recommendation).
2. Every selected technique's tactic should appear in the selected tactics,
   and every selected tactic should be supported by at least one technique
   or an explicit statement in the report.
3. Flag obvious omissions: a clearly described attacker behavior with no
   matching technique selected.

OUTPUT FORMAT:
- If consistent: {{"is_valid": true, "feedback": ""}}
- If not: {{"is_valid": false, "feedback": "<one short sentence: what to ADD or
  REMOVE and why, e.g. 'Remove T1105: no download behavior described. Add
  TA0003: registry Run key persistence is described.'>"}}
Output only the JSON object.
"""

    system_prompt = "You are a strict MITRE ATT&CK reviewer. Output only a JSON object, nothing else."
    if DISABLE_THINKING:
        system_prompt += " /no_think"

    # Catat konsumsi context window (CATAT-SAJA). Daftar teknik di prompt ini
    # adalah OBJEK yang dinilai, bukan kandidat pilihan — memangkasnya berarti
    # reviewer menilai sebagian jawaban saja, sehingga sengaja tidak dilakukan
    # meski PROMPT_BUDGET_ENFORCE=true. Ukurannya dikendalikan lewat
    # LOCAL_LLM_REPORT_MAX_CHARS dan jumlah teknik yang diusulkan agen.
    record_prompt("reviewer", check_budget(system_prompt, prompt, LOCAL_LLM_MAX_TOKENS_REVIEWER))

    attempt_models = [model["model"]]
    fallback_model = model.get("fallback_model")
    if fallback_model and fallback_model != model["model"]:
        attempt_models.append(fallback_model)

    for attempt_model in attempt_models:
        for attempt_idx in range(1, MAX_RETRIES_PER_MODEL + 1):
            try:
                response_text = _complete_chat(
                    model=model,
                    attempt_model=attempt_model,
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                    max_tokens=LOCAL_LLM_MAX_TOKENS_REVIEWER,
                )

                if DEBUG_MODE:
                    print(f"[DEBUG] Reviewer response from {attempt_model}: '{response_text[:200]}'")

                if not response_text or not response_text.strip():
                    print(f"Empty response dari reviewer {attempt_model} [attempt {attempt_idx}/{MAX_RETRIES_PER_MODEL}]")
                    if attempt_idx < MAX_RETRIES_PER_MODEL:
                        backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt_idx - 1))
                        print(f"Retry dalam {backoff:.1f} detik...")
                        time.sleep(backoff)
                        continue
                    if attempt_model != attempt_models[-1]:
                        print(f"Pindah ke fallback model: {fallback_model}")
                        break
                    # Kegagalan TEKNIS, bukan penolakan substantif: feedback wajib
                    # kosong agar teks error tak pernah tersuntik ke prompt agen.
                    return {"is_valid": False, "feedback": "", "error": "empty_response", "raw_response": ""}

                try:
                    data = _extract_json_object(response_text)
                    is_valid = bool(data.get("is_valid", False))
                    feedback = data.get("feedback", "") or ""
                    return {"is_valid": is_valid, "feedback": feedback}
                except json.JSONDecodeError:
                    fallback = _fallback_review_parse(response_text)
                    if fallback is not None:
                        return fallback
                    raise

            except json.JSONDecodeError as e:
                print(f"JSON parse error dari reviewer {attempt_model} [attempt {attempt_idx}/{MAX_RETRIES_PER_MODEL}]: {str(e)[:100]}")
                is_last_attempt_for_model = attempt_idx == MAX_RETRIES_PER_MODEL
                if not is_last_attempt_for_model:
                    backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt_idx - 1))
                    print(f"Retry dalam {backoff:.1f} detik...")
                    time.sleep(backoff)
                    continue
                if attempt_model != attempt_models[-1]:
                    print(f"Pindah ke fallback model: {fallback_model}")
                    break
                return {
                    "is_valid": False,
                    "feedback": "",
                    "error": "json_parse",
                    "raw_response": (response_text or "")[:200],
                }

            except Exception as e:
                error_text = str(e).lower()
                print(f"Error di Reviewer Agent ({attempt_model}) [attempt {attempt_idx}/{MAX_RETRIES_PER_MODEL}]: {e}")

                is_last_attempt_for_model = attempt_idx == MAX_RETRIES_PER_MODEL
                transient = _is_transient_error(error_text)

                if transient and not is_last_attempt_for_model:
                    backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt_idx - 1))
                    print(f"Retry dalam {backoff:.1f} detik...")
                    time.sleep(backoff)
                    continue

                if transient and attempt_model != attempt_models[-1]:
                    print(f"Pindah ke fallback model: {fallback_model}")
                    break

                return {
                    "is_valid": False,
                    "feedback": "",
                    "error": f"exception: {str(e)[:120]}",
                    "raw_response": "",
                }

    return {"is_valid": False, "feedback": "", "error": "retries_exhausted", "raw_response": ""}


if __name__ == "__main__":
    model = create_reviewer_agent()
    sample_report = "Attackers sent spear-phishing emails and used stolen credentials to access systems."
    print(
        review_tactics_and_techniques(
            model,
            sample_report,
            tactics=["TA0001"],
            techniques=["T1566"],
            attck_tactics={"TA0001": "Initial Access"},
            attck_techniques={"T1566": {"name": "Phishing", "description": "Phishing", "tactics": ["initial-access"]}},
        )
    )
