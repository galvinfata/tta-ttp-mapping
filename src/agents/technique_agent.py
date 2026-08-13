import os
import json
import random
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
    coverage_stats,
    retrieve_candidates_per_chunk,
)
from agents.prompt_budget import (
    PROMPT_BUDGET_ENFORCE,
    check_budget,
    estimate_tokens,
    record_prompt,
    trim_to_budget,
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
# Acak URUTAN daftar kandidat di dalam prompt (diagnostik, default nonaktif).
# Tujuannya menguji apakah pilihan LLM digerakkan oleh ISI laporan atau sekadar
# oleh urutan peringkat retrieval: bila metrik stabil saat urutan diacak, model
# benar-benar membaca; bila anjlok, model sebagian besar mengikuti urutan.
# Yang berubah HANYA urutan tampil — himpunan kandidat, peringkat retrieval asli
# (dipakai metrik keselarasan), dan himpunan yang lolos TECHNIQUE_ACCEPT_TOP_N
# semuanya dihitung dari urutan asli sehingga tidak ikut terpengaruh.
CANDIDATE_SHUFFLE_SEED = os.getenv("CANDIDATE_SHUFFLE_SEED", "").strip()
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
) -> tuple[list[str], list[str]]:
    """Format daftar kandidat hasil retrieval untuk prompt.

    Deskripsi dipangkas dan daftar dibatasi anggaran karakter agar muat di
    context window model (penting untuk model dengan n_ctx kecil seperti 4096).
    Mengembalikan (baris-baris daftar, ID yang benar-benar tampil) sebagai dua
    list SEJAJAR — supaya pemangkasan anggaran token (prompt_budget) bisa
    membuang baris dari ekor sekaligus ID-nya.
    """
    technique_lines = []
    shown_candidate_ids = []
    used_chars = 0
    for tid in candidate_technique_ids:
        tdata = attck_techniques[tid]
        desc = tdata["description"][:CANDIDATE_DESC_CHARS].replace("\n", " ")
        line = f"- {tid}: {tdata['name']} — {desc}"
        if used_chars + len(line) > CANDIDATE_LIST_MAX_CHARS and technique_lines:
            break
        technique_lines.append(line)
        shown_candidate_ids.append(tid)
        used_chars += len(line) + 1
    return technique_lines, shown_candidate_ids


def _shuffle_candidate_display(
    technique_lines: list[str],
    shown_candidate_ids: list[str],
    chunk: str,
) -> tuple[list[str], list[str]]:
    """Acak urutan tampil daftar kandidat secara reproducible (Tahap 4).

    Dipanggil SESUDAH _format_candidate_list, bukan sebelum — supaya himpunan
    kandidat yang tampil tetap ditentukan oleh peringkat retrieval asli dan
    anggaran karakter. Kalau pengacakan dilakukan lebih dulu, pemangkasan
    anggaran akan memotong ekor daftar yang sudah teracak dan HIMPUNAN-nya ikut
    berubah — bukan lagi eksperimen urutan murni.

    Seed diturunkan dari (seed pengguna + isi chunk) supaya tiap chunk mendapat
    permutasi berbeda, tetapi run dengan seed sama menghasilkan urutan identik
    tanpa bergantung pada urutan laporan diproses.
    """
    pairs = list(zip(technique_lines, shown_candidate_ids))
    rng = random.Random(f"{CANDIDATE_SHUFFLE_SEED}|{len(chunk)}|{chunk[:200]}")
    rng.shuffle(pairs)
    return [line for line, _ in pairs], [tid for _, tid in pairs]


def _record_ranks(store: dict, technique_ids: list[str], rank_of: dict) -> None:
    """Catat peringkat retrieval tiap teknik, simpan yang TERBAIK antar chunk.

    Satu teknik bisa dipilih di beberapa chunk dengan peringkat berbeda; yang
    dipakai untuk statistik adalah peringkat terkecil, yaitu posisi terbaik yang
    pernah diberikan retrieval kepada teknik itu.
    """
    for tid in technique_ids:
        rank = rank_of.get(tid)
        if rank is None:
            continue
        if tid not in store or rank < store[tid]:
            store[tid] = rank


def extract_techniques(
    model,
    report_text: str,
    attck_techniques: dict,
    top_k: int = DEFAULT_CANDIDATE_TOP_K,
    reviewer_feedback: str = "",
    telemetry: dict | None = None,
) -> list[str]:
    """
    Mengekstrak teknik ATT&CK spesifik dari laporan CTI.

    Args:
        telemetry: dict opsional yang DIISI di tempat dengan jangkauan pembacaan
            (coverage_chars/report_chars/coverage_ratio), daftar kandidat yang
            benar-benar tampil di prompt, dan jumlah kandidat yang dipangkas
            anggaran context window. Tidak memengaruhi perilaku pemetaan.

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

    # Jangkauan pembacaan efektif laporan ini (dibatasi chunk_size & max_chunks).
    if telemetry is not None:
        telemetry.update(coverage_stats(report_text))
        telemetry["candidates_shown"] = []
        telemetry["candidates_dropped_budget"] = 0
        telemetry["prompt_overflow_calls"] = 0
        # --- Keselarasan dengan peringkat retrieval (Tahap 2b) ---
        # Peringkat 1-indeks pada daftar kandidat chunk ASAL, sebelum & sesudah
        # filter TECHNIQUE_ACCEPT_TOP_N. Nilai per teknik = peringkat TERBAIK
        # (terkecil) di antara chunk-chunk yang memilihnya.
        telemetry["rank_of_selected"] = {}
        telemetry["rank_of_accepted"] = {}
        telemetry["rank_of_filtered_out"] = {}
        telemetry["candidate_shuffle_seed"] = CANDIDATE_SHUFFLE_SEED or None

    if not any(cands for _, cands in chunk_candidates):
        return []

    max_tokens = LOCAL_LLM_MAX_TOKENS_TECHNIQUE
    system_prompt = "You are an expert CTI analyst. Map the text to MITRE ATT&CK Techniques. Output ONLY a JSON object {\"ids\": [...]} of technique IDs from the candidate list, nothing else."
    if DISABLE_THINKING:
        system_prompt += " /no_think"

    seen = set()
    aggregated: list[str] = []
    shown_all: set[str] = set()
    for chunk, candidate_technique_ids in chunk_candidates:
        if not candidate_technique_ids:
            continue
        candidate_lines, shown_candidate_list = _format_candidate_list(
            candidate_technique_ids, attck_techniques
        )
        # Peringkat retrieval ASLI chunk ini (1 = kandidat teratas). Dihitung
        # sebelum pengacakan supaya metrik keselarasan tetap mengukur peringkat
        # retrieval yang sesungguhnya, bukan posisi tampil di prompt.
        rank_of = {tid: idx for idx, tid in enumerate(candidate_technique_ids, start=1)}
        # Prompt dibangun lewat closure agar bisa DISUSUN ULANG dengan daftar
        # kandidat yang lebih pendek tanpa mengubah satu kata pun teksnya.
        def _build_prompt(technique_str: str) -> str:
            return f"""TASK: Select every MITRE ATT&CK technique from CANDIDATES that is described in
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

        prompt = _build_prompt("\n".join(candidate_lines))
        budget = check_budget(system_prompt, prompt, max_tokens)
        dropped = 0

        # PROMPT_BUDGET_ENFORCE=true: pangkas kandidat dari peringkat TERBAWAH
        # sampai prompt muat di n_ctx. Potongan laporan tidak pernah dikorbankan.
        if PROMPT_BUDGET_ENFORCE and budget["overflow"] and len(candidate_lines) > 1:
            overhead = budget["prompt_tokens"] - estimate_tokens("\n".join(candidate_lines))
            candidate_lines, dropped = trim_to_budget(candidate_lines, overhead, max_tokens)
            if dropped:
                shown_candidate_list = shown_candidate_list[:len(candidate_lines)]
                prompt = _build_prompt("\n".join(candidate_lines))
                budget = check_budget(system_prompt, prompt, max_tokens)

        # Pengacakan urutan dilakukan PALING AKHIR — sesudah anggaran karakter
        # dan sesudah pemangkasan budget menentukan himpunan yang tampil, supaya
        # yang berubah benar-benar hanya urutan. Jumlah karakter tidak berubah,
        # jadi angka `budget` di atas tetap sahih.
        if CANDIDATE_SHUFFLE_SEED:
            candidate_lines, shown_candidate_list = _shuffle_candidate_display(
                candidate_lines, shown_candidate_list, chunk
            )
            prompt = _build_prompt("\n".join(candidate_lines))

        record_prompt("technique", budget, candidates_dropped=dropped)
        shown_candidate_ids = set(shown_candidate_list)
        shown_all.update(shown_candidate_ids)
        if telemetry is not None:
            telemetry["candidates_dropped_budget"] += dropped
            if budget["overflow"]:
                telemetry["prompt_overflow_calls"] += 1

        chunk_techniques = _llm_extract_ids(
            model=model,
            system_prompt=system_prompt,
            prompt=prompt,
            max_tokens=max_tokens,
            attck_techniques=attck_techniques,
            temperature=revise_temperature,
            allowed_ids=shown_candidate_ids,
        )
        # Peringkat retrieval dari apa yang DIPILIH LLM, sebelum filter apa pun.
        if telemetry is not None:
            _record_ranks(telemetry["rank_of_selected"], chunk_techniques, rank_of)

        selected_before_filter = chunk_techniques
        if TECHNIQUE_ACCEPT_TOP_N > 0:
            # Himpunan yang diterima ditentukan dari urutan retrieval ASLI,
            # tidak terpengaruh pengacakan tampilan di atas.
            accepted = set(candidate_technique_ids[:TECHNIQUE_ACCEPT_TOP_N])
            chunk_techniques = [t for t in chunk_techniques if t in accepted]

        if telemetry is not None:
            _record_ranks(telemetry["rank_of_accepted"], chunk_techniques, rank_of)
            kept = set(chunk_techniques)
            _record_ranks(
                telemetry["rank_of_filtered_out"],
                [t for t in selected_before_filter if t not in kept],
                rank_of,
            )

        for tid in chunk_techniques:
            if tid not in seen:
                seen.add(tid)
                aggregated.append(tid)

    if telemetry is not None:
        telemetry["candidates_shown"] = sorted(shown_all)
        # Teknik yang tersaring di satu chunk tapi diterima di chunk lain TIDAK
        # benar-benar dibuang filter — keluarkan dari daftar korban filter.
        telemetry["rank_of_filtered_out"] = {
            tid: rank for tid, rank in telemetry["rank_of_filtered_out"].items()
            if tid not in telemetry["rank_of_accepted"]
        }

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
