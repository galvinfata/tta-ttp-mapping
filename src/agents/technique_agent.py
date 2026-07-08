import os
import json
import re
import time
from dotenv import load_dotenv
from openai import OpenAI
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

load_dotenv()


MAX_RETRIES_PER_MODEL = 3
BASE_BACKOFF_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = int(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "300"))
DEFAULT_CANDIDATE_TOP_K = int(os.getenv("TECHNIQUE_CANDIDATE_TOP_K", "50"))
INCLUDE_SUBTECHNIQUES = os.getenv("TECHNIQUE_INCLUDE_SUBTECHNIQUES", "true").lower() == "true"
# Ukuran chunk teks laporan yang dikirim ke LLM. Laporan panjang dipecah jadi
# beberapa chunk (lihat _chunk_text) agar TTP di bagian akhir tidak hilang.
# CATATAN: total prompt (daftar kandidat + chunk + output) harus muat di context
# window model. Default ini dipilih agar aman untuk n_ctx 4096. Jika model
# dimuat dengan context lebih besar di LM Studio, naikkan nilai-nilai ini.
LOCAL_LLM_REPORT_MAX_CHARS = int(os.getenv("LOCAL_LLM_REPORT_MAX_CHARS", "3500"))
RETRIEVAL_MAX_CHARS = int(os.getenv("RETRIEVAL_MAX_CHARS", "20000"))
CHUNK_OVERLAP_CHARS = int(os.getenv("LLM_CHUNK_OVERLAP_CHARS", "250"))
MAX_CHUNKS = int(os.getenv("LLM_MAX_CHUNKS", "3"))
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
DEBUG_MODE = os.getenv("DEBUG_AGENT", "false").lower() == "true"
DISABLE_THINKING = os.getenv("LLM_DISABLE_THINKING", "true").lower() == "true"


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
    ]
    return any(marker in error_text for marker in transient_markers)


def create_technique_agent():
    """Inisialisasi LM Studio local server (OpenAI-compatible)."""
    base_url = os.getenv("LOCAL_LLM_BASE_URL").rstrip("/")
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
        create_kwargs["extra_body"] = {
            "chat_template_kwargs": {"enable_thinking": False}
        }
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
        # Server menolak response_format (mis. tidak mendukung json_schema).
        # Coba ulang tanpa structured output sebelum menyerah.
        if "response_format" in create_kwargs:
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


def _chunk_text(text: str, chunk_size: int, overlap: int, max_chunks: int) -> list[str]:
    """Pecah teks panjang menjadi beberapa chunk dengan overlap kecil.

    Mengembalikan minimal satu chunk. Dibatasi max_chunks agar latency terkendali.
    """
    text = text or ""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    step = max(1, chunk_size - overlap)
    while start < len(text) and len(chunks) < max_chunks:
        chunks.append(text[start:start + chunk_size])
        start += step
    return chunks


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


def _build_technique_document(technique_id: str, technique_data: dict) -> str:
    name = technique_data.get("name", "")
    description = technique_data.get("description", "")[:800]
    tactics = " ".join(technique_data.get("tactics", []))
    return f"{technique_id} {name}. {description} {tactics}"


def _retrieve_candidate_techniques(
    report_text: str,
    attck_techniques: dict,
    top_k: int,
    include_subtechniques: bool = INCLUDE_SUBTECHNIQUES,
) -> list[str]:
    candidates = []
    documents = []

    for technique_id, technique_data in attck_techniques.items():
        if not include_subtechniques and "." in technique_id:
            continue
        candidates.append(technique_id)
        documents.append(_build_technique_document(technique_id, technique_data))

    if not candidates:
        return []

    try:
        vectorizer = TfidfVectorizer(
            stop_words="english",
            ngram_range=(1, 2),
            max_features=20000,
        )
        matrix = vectorizer.fit_transform([report_text[:RETRIEVAL_MAX_CHARS]] + documents)
        similarity = cosine_similarity(matrix[0:1], matrix[1:]).flatten()

        ranked_indices = similarity.argsort()[::-1]
        max_candidates = min(top_k, len(candidates))
        return [candidates[idx] for idx in ranked_indices[:max_candidates]]

    except Exception as e:
        # Fallback aman jika retrieval gagal: pertahankan perilaku lama (urutan awal dictionary).
        print(f"Warning retrieval kandidat gagal, pakai fallback default: {e}")
        return candidates[:top_k]


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

    candidate_technique_ids = _retrieve_candidate_techniques(
        report_text=report_text,
        attck_techniques=attck_techniques,
        top_k=top_k,
    )

    if not candidate_technique_ids:
        return []

    # Format daftar kandidat teknik hasil retrieval untuk prompt.
    # Deskripsi dipangkas dan daftar dibatasi anggaran karakter agar muat di
    # context window model (penting untuk model dengan n_ctx kecil seperti 4096).
    technique_list = []
    used_chars = 0
    for tid in candidate_technique_ids:
        tdata = attck_techniques[tid]
        desc = tdata["description"][:CANDIDATE_DESC_CHARS].replace("\n", " ")
        line = f"- {tid}: {tdata['name']} — {desc}"
        if used_chars + len(line) > CANDIDATE_LIST_MAX_CHARS and technique_list:
            break
        technique_list.append(line)
        used_chars += len(line) + 1
    technique_str = "\n".join(technique_list)
    
    max_tokens = LOCAL_LLM_MAX_TOKENS_TECHNIQUE
    system_prompt = "You are an expert CTI analyst. Map the text to MITRE ATT&CK Techniques. Output ONLY a JSON object {\"ids\": [...]} of technique IDs from the candidate list, nothing else."
    if DISABLE_THINKING:
        system_prompt += " /no_think"

    # Laporan panjang dipecah jadi beberapa chunk agar TTP di bagian akhir tidak
    # hilang akibat pemotongan. Hasil tiap chunk digabung (union) dengan menjaga urutan.
    chunks = _chunk_text(
        report_text,
        chunk_size=LOCAL_LLM_REPORT_MAX_CHARS,
        overlap=CHUNK_OVERLAP_CHARS,
        max_chunks=MAX_CHUNKS,
    )

    seen = set()
    aggregated: list[str] = []
    for chunk in chunks:
        prompt = f"""You are a CTI analyst mapping text to MITRE ATT&CK TECHNIQUES (T#### or T####.###).
You MUST select ONLY from the candidate list below. If none match, return an empty list.

Candidate Techniques:
{technique_str}

CTI Example:
"Phishing email with malicious attachment executed by the victim."
Expected output: {{"ids": ["T1566.001"]}}
{feedback_block}
Report Excerpt:
\"\"\"{chunk}\"\"\"

Rules:
1. Output ONLY a JSON object: {{"ids": ["T....", ...]}} using technique IDs from the candidate list.
2. Do not invent IDs outside the list.
3. If unsure, use an empty list: {{"ids": []}}.
4. No extra text, no markdown, no explanation.
"""
        chunk_techniques = _llm_extract_ids(
            model=model,
            system_prompt=system_prompt,
            prompt=prompt,
            max_tokens=max_tokens,
            attck_techniques=attck_techniques,
            temperature=revise_temperature,
        )
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
) -> list[str]:
    """Panggil LLM untuk satu prompt dengan retry/fallback, kembalikan teknik valid."""
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
                    if isinstance(t, str) and t in attck_techniques
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
