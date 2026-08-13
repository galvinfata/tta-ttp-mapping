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
LOCAL_LLM_REPORT_MAX_CHARS = int(os.getenv("LOCAL_LLM_REPORT_MAX_CHARS", "6000"))
LOCAL_LLM_MAX_TOKENS_TACTIC = int(os.getenv("LOCAL_LLM_MAX_TOKENS_TACTIC", "512"))
LOCAL_LLM_STRICT_JSON = os.getenv("LOCAL_LLM_STRICT_JSON", "true").lower() == "true"
DEBUG_MODE = os.getenv("DEBUG_AGENT", "false").lower() == "true"
# Matikan reasoning/thinking pada model hybrid (Qwen3 dll.). Default true karena
# thinking menghabiskan token budget dan membuat output JSON kosong.
DISABLE_THINKING = os.getenv("LLM_DISABLE_THINKING", "true").lower() == "true"
# Qwen3.5 mengabaikan enable_thinking=false & /no_think; di LM Studio reasoning
# model itu dimatikan lewat parameter reasoning_effort="none". Kosongkan env
# ini untuk tidak mengirim parameternya sama sekali.
REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "none").strip()
STRUCTURED_OUTPUT = os.getenv("LLM_STRUCTURED_OUTPUT", "true").lower() == "true"

# JSON schema untuk structured output: objek {"ids": ["TA0001", ...]}.
_IDS_JSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "attack_ids",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["ids"],
            "additionalProperties": False,
        },
    },
}

DEFAULT_TACTIC_LIST = {
    "TA0001": "Initial Access",
    "TA0002": "Execution",
    "TA0003": "Persistence",
    "TA0004": "Privilege Escalation",
    "TA0005": "Defense Evasion",
    "TA0006": "Credential Access",
    "TA0007": "Discovery",
    "TA0008": "Lateral Movement",
    "TA0009": "Collection",
    "TA0010": "Exfiltration",
    "TA0011": "Command and Control",
    "TA0040": "Impact",
    "TA0042": "Resource Development",
    "TA0043": "Reconnaissance",
}

# Glosarium satu baris per taktik (prompt v2). Nama taktik saja ambigu untuk
# model 4B ("Resource Development", "Collection"); definisi singkat memberi
# jangkar semantik. Urutan dict = urutan kill-chain untuk penyusunan prompt.
TACTIC_GLOSSARY = {
    "TA0043": "gathering info about the target before attack",
    "TA0042": "acquiring infrastructure, accounts, or tools",
    "TA0001": "getting into the network (phishing, exploits, valid accounts)",
    "TA0002": "running attacker code (scripts, commands, user execution)",
    "TA0003": "keeping access across restarts (run keys, services, tasks)",
    "TA0004": "gaining higher-level permissions",
    "TA0005": "avoiding detection (obfuscation, disabling tools, masquerading)",
    "TA0006": "stealing passwords, hashes, tokens, keys",
    "TA0007": "exploring the environment (system, network, account enumeration)",
    "TA0008": "moving to other systems in the network",
    "TA0009": "gathering data of interest (files, screenshots, keylogging)",
    "TA0011": "communicating with compromised systems (C2, tunneling)",
    "TA0010": "stealing data out of the network",
    "TA0040": "destroying, encrypting, or disrupting systems and data",
}


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


def create_tactic_agent():
    """Inisialisasi LM Studio local server (OpenAI-compatible)."""
    base_url = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:1234").rstrip("/")
    model_name = os.getenv("LOCAL_LLM_MODEL", "qwen/qwen3-4b")
    api_key = os.getenv("LOCAL_LLM_API_KEY", "")
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


def _strip_think_blocks(text: str) -> str:
    """Buang blok reasoning <think>...</think> dari output model thinking (mis. Qwen3)."""
    if not text:
        return text
    # Hapus pasangan lengkap <think>...</think>
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Jika tag pembuka ada tapi penutup hilang (output terpotong), buang sampai akhir
    text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Buang sisa tag penutup yang menggantung
    text = re.sub(r"</?think>", "", text, flags=re.IGNORECASE)
    return text.strip()


def _complete_chat(model: dict, attempt_model: str, system_prompt: str, user_prompt: str, max_tokens: int, use_structured: bool = False, temperature: float = 0.0) -> str:
    create_kwargs = dict(
        model=attempt_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        top_p=1.0,
        max_tokens=max_tokens,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    # Nonaktifkan mode "thinking" pada model hybrid (Qwen3 dsb.) agar token tidak
    # habis untuk reasoning dan output JSON benar-benar dikembalikan.
    if DISABLE_THINKING:
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
        if REASONING_EFFORT:
            extra_body["reasoning_effort"] = REASONING_EFFORT
        create_kwargs["extra_body"] = extra_body
    if use_structured and STRUCTURED_OUTPUT:
        create_kwargs["response_format"] = _IDS_JSON_SCHEMA

    try:
        response = model["client"].chat.completions.create(**create_kwargs)
    except TypeError:
        # Beberapa versi klien tidak menerima extra_body/response_format lewat kwargs ini
        create_kwargs.pop("extra_body", None)
        create_kwargs.pop("response_format", None)
        response = model["client"].chat.completions.create(**create_kwargs)
    except Exception as exc:
        error_text = str(exc).lower()
        extra_body = create_kwargs.get("extra_body") or {}
        if "reasoning" in error_text and "reasoning_effort" in extra_body:
            # Server menolak parameter reasoning_effort → ulang tanpa itu.
            if DEBUG_MODE:
                print(f"[DEBUG] reasoning_effort ditolak, fallback: {str(exc)[:120]}")
            extra_body.pop("reasoning_effort", None)
            response = model["client"].chat.completions.create(**create_kwargs)
        elif "response_format" in create_kwargs:
            # Server menolak response_format (tidak mendukung json_schema) → ulang tanpa itu.
            if DEBUG_MODE:
                print(f"[DEBUG] structured output ditolak, fallback plain: {str(exc)[:120]}")
            create_kwargs.pop("response_format", None)
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


def _coerce_ids(parsed) -> list:
    """Ambil daftar ID dari hasil parse yang bisa berupa array atau objek {'ids': [...]}."""
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("ids", "tactic_ids", "tactics", "technique_ids"):
            value = parsed.get(key)
            if isinstance(value, list):
                return value
    return []


def _extract_json_array(response_text: str) -> list:
    """Ekstrak daftar ID dari output model.

    Mendukung array langsung ["TA0001", ...] maupun objek {"ids": ["TA0001", ...]}.
    """
    try:
        return _coerce_ids(json.loads(response_text.strip()))
    except (json.JSONDecodeError, AttributeError):
        pass

    obj_match = re.search(r"\{[\s\S]*\}", response_text)
    if obj_match:
        try:
            ids = _coerce_ids(json.loads(obj_match.group(0)))
            if ids:
                return ids
        except json.JSONDecodeError:
            pass

    match = re.search(r"```(?:json)?\s*(\[[^\]]*\])\s*```", response_text, re.DOTALL | re.IGNORECASE)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    match = re.search(r"\[[\s\S]*\]", response_text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("Could not find JSON array in response", response_text, 0)


def _extract_tactic_ids_from_text(response_text: str, tactic_list: dict) -> list[str]:
    """Ekstrak tactic IDs dari text fallback jika JSON parsing gagal."""
    if not response_text or not response_text.strip():
        return []

    cleaned = response_text.upper()
    cleaned = cleaned.replace('"', "").replace("'", "").replace("[", "").replace("]", "")

    ids = re.findall(r"\bTA\d{4}\b", cleaned)

    seen = set()
    valid_ids = []
    for tactic_id in ids:
        if tactic_id in tactic_list and tactic_id not in seen:
            valid_ids.append(tactic_id)
            seen.add(tactic_id)

    if valid_ids:
        return valid_ids

    normalized = re.sub(r"[^a-z0-9\s]", " ", response_text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    name_to_id = {name.lower(): tid for tid, name in tactic_list.items()}
    for name, tactic_id in name_to_id.items():
        pattern = r"\b" + re.sub(r"\s+", r"\\s+", re.escape(name)) + r"\b"
        if re.search(pattern, normalized):
            if tactic_id not in seen:
                valid_ids.append(tactic_id)
                seen.add(tactic_id)

    return valid_ids


def identify_tactics(
    model,
    report_text: str,
    tactic_list: dict | None = None,
    reviewer_feedback: str = "",
) -> list[str]:
    """
    Mengidentifikasi taktik ATT&CK dari laporan CTI.

    Returns:
        list of tactic IDs: ["TA0001", "TA0003", ...]
    """
    TACTIC_LIST = tactic_list or DEFAULT_TACTIC_LIST

    # Susun daftar taktik urut kill-chain + glosarium satu baris (prompt v2).
    # Taktik di luar glosarium (kalau tactic_list kustom) ditambahkan tanpa definisi.
    ordered_ids = [tid for tid in TACTIC_GLOSSARY if tid in TACTIC_LIST]
    ordered_ids += [tid for tid in TACTIC_LIST if tid not in TACTIC_GLOSSARY]
    tactic_lines = []
    for tid in ordered_ids:
        gloss = TACTIC_GLOSSARY.get(tid)
        if gloss:
            tactic_lines.append(f"- {tid}: {TACTIC_LIST[tid]} — {gloss}")
        else:
            tactic_lines.append(f"- {tid}: {TACTIC_LIST[tid]}")
    tactic_str = "\n".join(tactic_lines)

    report_excerpt_size = LOCAL_LLM_REPORT_MAX_CHARS
    max_tokens = LOCAL_LLM_MAX_TOKENS_TACTIC

    # Saat revisi (ada feedback dari reviewer), masukkan feedback ke prompt dan
    # naikkan temperature agar output bisa berubah dari iterasi sebelumnya.
    feedback_block = ""
    revise_temperature = 0.0
    if reviewer_feedback and reviewer_feedback.strip():
        feedback_block = (
            "\nReviewer feedback on your PREVIOUS answer (revise accordingly):\n"
            f"\"\"\"{reviewer_feedback.strip()[:600]}\"\"\"\n"
        )
        revise_temperature = float(os.getenv("LLM_REVISE_TEMPERATURE", "0.4"))

    prompt = f"""TASK: Identify every MITRE ATT&CK TACTIC (TA####) whose goal is pursued by the
attacker in the report excerpt. Choose ONLY from the list below.

AVAILABLE TACTICS:
{tactic_str}

DECISION RULES:
1. Include a tactic ONLY if the report describes the attacker actually pursuing
   that goal — not tool capabilities, IOC lists, or defensive recommendations.
2. Scan the WHOLE excerpt sentence by sentence; multi-stage intrusions typically
   involve 4-8 tactics. Do not stop after the first matches.
3. If nothing applies, return {{"ids": []}}.

EXAMPLE
Report: "Attackers sent spear-phishing emails with malicious attachments, then
used PowerShell to run malware, dumped LSASS memory, and contacted a C2 server."
Output: {{"ids": ["TA0001","TA0002","TA0006","TA0011"]}}

EXAMPLE (nothing applies)
Report: "This advisory lists file hashes and recommends enabling MFA."
Output: {{"ids": []}}
{feedback_block}
REPORT EXCERPT:
\"\"\"{report_text[:report_excerpt_size]}\"\"\"

Answer with ONLY the JSON object {{"ids": [...]}}.
"""

    system_prompt = "You are an expert CTI analyst. Map the text to MITRE ATT&CK Tactics. Output ONLY a JSON object {\"ids\": [...]} of tactic IDs, nothing else."
    if DISABLE_THINKING:
        system_prompt += " /no_think"

    # Catat konsumsi context window. Agen taktik tidak punya daftar kandidat
    # yang bisa dipangkas (daftar taktik hanya 14 baris dan wajib utuh), jadi
    # di sini instrumentasi bersifat CATAT-SAJA — ukuran prompt dikendalikan
    # lewat LOCAL_LLM_REPORT_MAX_CHARS.
    record_prompt("tactic", check_budget(system_prompt, prompt, max_tokens))

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
                    max_tokens=max_tokens,
                    use_structured=True,
                    temperature=revise_temperature,
                )

                if DEBUG_MODE:
                    print(f"[DEBUG] Response dari {attempt_model}: '{response_text[:200]}'")

                if not response_text or not response_text.strip():
                    print(f"Empty response dari {attempt_model} [attempt {attempt_idx}/{MAX_RETRIES_PER_MODEL}]")
                    if attempt_idx < MAX_RETRIES_PER_MODEL:
                        backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt_idx - 1))
                        print(f"Retry dalam {backoff:.1f} detik...")
                        time.sleep(backoff)
                        continue
                    if attempt_model != attempt_models[-1]:
                        print(f"Pindah ke fallback model: {fallback_model}")
                    break

                try:
                    tactics = _extract_json_array(response_text)
                except json.JSONDecodeError:
                    tactics = _extract_tactic_ids_from_text(response_text, TACTIC_LIST)
                    if not tactics:
                        raise

                valid_tactics = [
                    t for t in tactics
                    if isinstance(t, str) and t in TACTIC_LIST
                ]

                return valid_tactics

            except json.JSONDecodeError as e:
                print(f"JSON parse error dari {attempt_model} [attempt {attempt_idx}/{MAX_RETRIES_PER_MODEL}]: {str(e)[:100]}")
                is_last_attempt_for_model = attempt_idx == MAX_RETRIES_PER_MODEL
                if not is_last_attempt_for_model:
                    backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt_idx - 1))
                    print(f"Retry dalam {backoff:.1f} detik...")
                    time.sleep(backoff)
                    continue
                if attempt_model != attempt_models[-1]:
                    print(f"Pindah ke fallback model: {fallback_model}")
                    break
                return []

            except Exception as e:
                error_text = str(e).lower()
                print(f"Error di Tactic Agent ({attempt_model}) [attempt {attempt_idx}/{MAX_RETRIES_PER_MODEL}]: {e}")

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

                return []

    return []


if __name__ == "__main__":
    model = create_tactic_agent()

    test_report = """
    The attackers sent spear-phishing emails containing 
    malicious attachments to employees. Once executed, 
    the malware established persistence through registry 
    modifications and began communicating with C2 servers.
    """

    tactics = identify_tactics(model, test_report)
    print(f"Taktik teridentifikasi: {tactics}")
