import os
import json
import re
import time
from dotenv import load_dotenv
from openai import OpenAI

# Retrieval TF-IDF dipisah ke modul murni tanpa dependensi LLM agar bisa dipakai
# ulang oleh skrip evaluasi offline. Di-import ulang di sini demi kompatibilitas.
# Parameter chunking juga tinggal di agents.retrieval (satu sumber kebenaran
# antara prompt LLM dan rekonstruksi kandidat skrip evaluasi).
from agents.retrieval import (
    RETRIEVAL_MAX_CHARS,
    INCLUDE_SUBTECHNIQUES,
    LOCAL_LLM_REPORT_MAX_CHARS,
    CHUNK_OVERLAP_CHARS,
    MAX_CHUNKS,
    _build_technique_document,
    _chunk_text,
    _retrieve_candidate_techniques,
    retrieve_candidates_per_chunk,
)

load_dotenv()


MAX_RETRIES_PER_MODEL = 3
BASE_BACKOFF_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = int(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "300"))
DEFAULT_CANDIDATE_TOP_K = int(os.getenv("TECHNIQUE_CANDIDATE_TOP_K", "50"))
# Ukuran chunk teks laporan (LOCAL_LLM_REPORT_MAX_CHARS dkk.) di-import dari
# agents.retrieval. CATATAN: total prompt (daftar kandidat + chunk + output)
# harus muat di context window model; default aman untuk n_ctx 4096. Jika model
# dimuat dengan context lebih besar di LM Studio, naikkan lewat env.
LOCAL_LLM_MAX_TOKENS_TECHNIQUE = int(os.getenv("LOCAL_LLM_MAX_TOKENS_TECHNIQUE", "512"))
LOCAL_LLM_STRICT_JSON = os.getenv("LOCAL_LLM_STRICT_JSON", "true").lower() == "true"
# Plafon kandidat teknik yang dilihat LLM. Dinaikkan dari 10 → 50: dengan 10,
# teknik benar yang tidak masuk top-10 retrieval mustahil terpilih (recall rendah).
LOCAL_LLM_CANDIDATE_TOP_K = int(os.getenv("LOCAL_LLM_CANDIDATE_TOP_K", "50"))
# Panjang deskripsi tiap kandidat di prompt & anggaran total karakter daftar
# kandidat. Dipakai agar daftar 50 kandidat tetap muat di context window kecil.
CANDIDATE_DESC_CHARS = int(os.getenv("CANDIDATE_DESC_CHARS", "120"))
CANDIDATE_LIST_MAX_CHARS = int(os.getenv("CANDIDATE_LIST_MAX_CHARS", "4500"))
STRUCTURED_OUTPUT = os.getenv("LLM_STRUCTURED_OUTPUT", "true").lower() == "true"
# Retrieval per-chunk: tiap potongan laporan mendapat daftar kandidatnya
# sendiri (bukan satu daftar dari seluruh laporan yang dipakai semua chunk).
# Menaikkan plafon recall retrieval efektif dari 20% -> 36% exact (lihat
# agents/retrieval.py) tanpa menambah konsumsi context window per prompt.
RETRIEVAL_PER_CHUNK = os.getenv("RETRIEVAL_PER_CHUNK", "true").lower() == "true"
# Filter presisi: buang pilihan LLM yang TIDAK masuk top-N kandidat retrieval
# chunk-nya. TP hampir selalu kandidat berperingkat tinggi; FP jenis
# "mention-mapping" (nama teknik disebut di kalimat mitigasi) dan overreach
# semantik menumpuk di peringkat bawah. Terukur offline pada batch 5 laporan
# (2026-07-11): precision exact 0.244->0.304, F1 0.321->0.350 (recall
# 0.471->0.412). Set 0 untuk menonaktifkan (model bebas memilih dari seluruh
# kandidat yang tampil).
TECHNIQUE_ACCEPT_TOP_N = int(os.getenv("TECHNIQUE_ACCEPT_TOP_N", "30"))
DEBUG_MODE = os.getenv("DEBUG_AGENT", "false").lower() == "true"
DISABLE_THINKING = os.getenv("LLM_DISABLE_THINKING", "true").lower() == "true"
# Qwen3.5 mengabaikan enable_thinking=false & /no_think; di LM Studio reasoning
# model itu dimatikan lewat parameter reasoning_effort="none" (terverifikasi
# 2026-07-11: content kosong 512 token thinking -> JSON bersih 3 detik).
# Kosongkan env ini untuk tidak mengirim parameternya sama sekali.
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


def create_technique_agent():
    """Inisialisasi LM Studio local server (OpenAI-compatible)."""
    base_url = os.getenv("LOCAL_LLM_BASE_URL", "http://100.100.211.39:1234").rstrip("/")
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


# JSON schema untuk structured output: objek {"ids": ["T1566", ...]}.
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


def _complete_chat(
    model: dict,
    attempt_model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    use_structured: bool = False,
    temperature: float = 0.0,
) -> str:
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
        # Klien lama tidak menerima extra_body / response_format sebagai kwargs
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
            # Server menolak response_format (mis. tidak mendukung json_schema).
            # Coba ulang tanpa structured output sebelum menyerah.
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
        for key in ("ids", "technique_ids", "tactic_ids", "techniques", "tactics"):
            value = parsed.get(key)
            if isinstance(value, list):
                return value
    return []


def _extract_json_array(response_text: str) -> list:
    """Ekstrak daftar ID dari output model.

    Mendukung dua bentuk:
    - JSON array langsung: ["T1566", ...]
    - JSON objek structured output: {"ids": ["T1566", ...]}
    """
    # 1. Coba parse seluruh teks sebagai JSON (kasus structured output bersih).
    try:
        return _coerce_ids(json.loads(response_text.strip()))
    except (json.JSONDecodeError, AttributeError):
        pass

    # 2. Objek JSON di dalam teks (mis. ada teks tambahan).
    obj_match = re.search(r"\{[\s\S]*\}", response_text)
    if obj_match:
        try:
            ids = _coerce_ids(json.loads(obj_match.group(0)))
            if ids:
                return ids
        except json.JSONDecodeError:
            pass

    # 3. Array dalam blok kode markdown.
    match = re.search(r"```(?:json)?\s*(\[[^\]]*\])\s*```", response_text, re.DOTALL | re.IGNORECASE)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 4. Array mentah di mana saja.
    match = re.search(r"\[[\s\S]*\]", response_text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("Could not find JSON array in response", response_text, 0)


def _extract_technique_ids_from_text(response_text: str, attck_techniques: dict) -> list[str]:
    """Fallback parser untuk output model non-JSON."""
    if not response_text or not response_text.strip():
        return []
    
    # Clean response
    cleaned = response_text.upper()
    cleaned = cleaned.replace('"', '').replace("'", '').replace('[', '').replace(']', '')
    
    # Find all T#### dan T####.### patterns
    ids = re.findall(r'\bT\d{4}(?:\.\d{3})?\b', cleaned)
    
    # Dedupe & validate
    seen = set()
    valid_ids = []
    for technique_id in ids:
        if technique_id in attck_techniques and technique_id not in seen:
            valid_ids.append(technique_id)
            seen.add(technique_id)
    
    return valid_ids


def _format_candidate_list(
    candidate_technique_ids: list[str],
    attck_techniques: dict,
) -> tuple[str, set]:
    """Format daftar kandidat hasil retrieval untuk prompt.

    Deskripsi dipangkas dan daftar dibatasi anggaran karakter agar muat di
    context window model (penting untuk model dengan n_ctx kecil seperti 4096).
    Mengembalikan (teks daftar, himpunan ID yang benar-benar tampil).
    """
    technique_list = []
    shown_candidate_ids = set()
    used_chars = 0
    for tid in candidate_technique_ids:
        tdata = attck_techniques[tid]
        desc = tdata["description"][:CANDIDATE_DESC_CHARS].replace("\n", " ")
        line = f"- {tid}: {tdata['name']} — {desc}"
        if used_chars + len(line) > CANDIDATE_LIST_MAX_CHARS and technique_list:
            break
        technique_list.append(line)
        shown_candidate_ids.add(tid)
        used_chars += len(line) + 1
    return "\n".join(technique_list), shown_candidate_ids


def extract_techniques(
    model,
    report_text: str,
    attck_techniques: dict,
    top_k: int = DEFAULT_CANDIDATE_TOP_K,
    reviewer_feedback: str = "",
) -> list[str]:
    """
    Mengekstrak teknik ATT&CK spesifik dari laporan CTI.

    Returns:
        list of technique IDs: ["T1566", "T1059", ...]
    """

    top_k = min(top_k, LOCAL_LLM_CANDIDATE_TOP_K)

    # Saat revisi (ada feedback reviewer), masukkan feedback ke prompt & naikkan
    # temperature agar hasil bisa berubah dari iterasi sebelumnya.
    feedback_block = ""
    revise_temperature = 0.0
    if reviewer_feedback and reviewer_feedback.strip():
        feedback_block = (
            "\nReviewer feedback on your PREVIOUS answer (revise accordingly):\n"
            f"\"\"\"{reviewer_feedback.strip()[:600]}\"\"\"\n"
        )
        revise_temperature = float(os.getenv("LLM_REVISE_TEMPERATURE", "0.4"))

    # Laporan panjang dipecah jadi beberapa chunk agar TTP di bagian akhir tidak
    # hilang akibat pemotongan. Hasil tiap chunk digabung (union) dengan menjaga
    # urutan. Default: retrieval PER-CHUNK — tiap chunk mendapat daftar kandidat
    # yang relevan dengan potongannya sendiri (plafon recall efektif naik dari
    # 20% -> 36% exact; lihat agents/retrieval.py). Set RETRIEVAL_PER_CHUNK=false
    # untuk perilaku lama (satu daftar kandidat dari seluruh laporan).
    if RETRIEVAL_PER_CHUNK:
        chunk_candidates = retrieve_candidates_per_chunk(
            report_text=report_text,
            attck_techniques=attck_techniques,
            top_k=top_k,
        )
    else:
        candidate_technique_ids = _retrieve_candidate_techniques(
            report_text=report_text,
            attck_techniques=attck_techniques,
            top_k=top_k,
        )
        chunks = _chunk_text(
            report_text,
            chunk_size=LOCAL_LLM_REPORT_MAX_CHARS,
            overlap=CHUNK_OVERLAP_CHARS,
            max_chunks=MAX_CHUNKS,
        )
        chunk_candidates = [(chunk, candidate_technique_ids) for chunk in chunks]

    if not any(cands for _, cands in chunk_candidates):
        return []

    max_tokens = LOCAL_LLM_MAX_TOKENS_TECHNIQUE
    system_prompt = "You are an expert CTI analyst. Map the text to MITRE ATT&CK Techniques. Output ONLY a JSON object {\"ids\": [...]} of technique IDs from the candidate list, nothing else."
    if DISABLE_THINKING:
        system_prompt += " /no_think"

    seen = set()
    aggregated: list[str] = []
    for chunk, candidate_technique_ids in chunk_candidates:
        if not candidate_technique_ids:
            continue
        technique_str, shown_candidate_ids = _format_candidate_list(
            candidate_technique_ids, attck_techniques
        )
        prompt = f"""TASK: Select every MITRE ATT&CK technique from CANDIDATES that is described in
the report excerpt. You MUST choose only from CANDIDATES. Never output an ID
that is not in the list.

CANDIDATES:
{technique_str}

DECISION RULES:
1. Select a technique ONLY if the report describes the attacker actually
   performing that behavior. Do NOT select for:
   - security recommendations or mitigations ("enable MFA", "patch systems")
   - tool capabilities that were not observed in use
   - plain IOC lists (hashes, IPs, domains) with no described behavior
2. Prefer the sub-technique (T####.###) when the report is specific about the
   variant; use the parent (T####) only when the description is generic.
3. Scan the WHOLE excerpt sentence by sentence. CTI reports typically describe
   5-15 techniques; do not stop after the first few matches.
4. A behavior can be described without naming the technique — match on meaning
   (e.g., "decoded a base64 payload" = Deobfuscate/Decode Files or Information).
5. If a candidate has no supporting sentence in the excerpt, exclude it.
6. If none match, return {{"ids": []}}.

EXAMPLE
Report: "The actor sent spear-phishing emails with a malicious ZIP attachment.
When opened by the victim, a PowerShell loader decoded a base64-encoded payload
and created a Run registry key for persistence."
Output: {{"ids": ["T1566.001","T1204.002","T1059.001","T1140","T1547.001"]}}
(Example IDs are illustrative — your answer must come from CANDIDATES above.)

EXAMPLE (nothing applies)
Report: "Indicators: 5f2b...e91a, 203.0.113.7. We recommend blocking these IPs."
Output: {{"ids": []}}
{feedback_block}
REPORT EXCERPT:
\"\"\"{chunk}\"\"\"

Answer with ONLY the JSON object {{"ids": [...]}}.
"""
        chunk_techniques = _llm_extract_ids(
            model=model,
            system_prompt=system_prompt,
            prompt=prompt,
            max_tokens=max_tokens,
            attck_techniques=attck_techniques,
            temperature=revise_temperature,
            allowed_ids=shown_candidate_ids,
        )
        if TECHNIQUE_ACCEPT_TOP_N > 0:
            accepted = set(candidate_technique_ids[:TECHNIQUE_ACCEPT_TOP_N])
            chunk_techniques = [t for t in chunk_techniques if t in accepted]
        for tid in chunk_techniques:
            if tid not in seen:
                seen.add(tid)
                aggregated.append(tid)

    return aggregated


def _llm_extract_ids(
    model,
    system_prompt: str,
    prompt: str,
    max_tokens: int,
    attck_techniques: dict,
    temperature: float = 0.0,
    allowed_ids: set | None = None,
) -> list[str]:
    """Panggil LLM untuk satu prompt dengan retry/fallback, kembalikan teknik valid.

    allowed_ids: bila diisi, ID di luar himpunan ini dibuang (closed-set selection —
    menyaring ID yang disalin model dari contoh few-shot tapi tak ada di kandidat).
    """
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
                    temperature=temperature,
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
                    techniques = _extract_json_array(response_text)
                except json.JSONDecodeError:
                    techniques = _extract_technique_ids_from_text(response_text, attck_techniques)
                    if not techniques:
                        raise

                return [
                    t for t in techniques
                    if isinstance(t, str)
                    and t in attck_techniques
                    and (allowed_ids is None or t in allowed_ids)
                ]

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
                print(f"Error di Technique Agent ({attempt_model}) [attempt {attempt_idx}/{MAX_RETRIES_PER_MODEL}]: {e}")

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
