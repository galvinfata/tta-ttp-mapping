"""Retrieval kandidat teknik berbasis TF-IDF (murni, tanpa dependensi LLM).

Modul ini sengaja dipisah dari technique_agent.py agar bagian retrieval bisa
dipakai ulang secara OFFLINE dan DETERMINISTIK (mis. oleh skrip evaluasi
baseline) tanpa ikut memuat klien LLM (openai). Variabel .env tetap dimuat di
sini agar konfigurasi retrieval/chunking konsisten antara pipeline dan skrip
evaluasi. technique_agent.py mengimpor ulang fungsi & konstanta dari sini agar
perilaku sistem yang sudah ada tidak berubah.

Upgrade retrieval (2026-07-11) — dipilih lewat eksperimen plafon recall offline
(151 laporan TRAM II; plafon = %GT yang muncul di kandidat yang TAMPIL di
prompt setelah anggaran karakter):

| Varian                                   | exact@shown | base@shown |
|------------------------------------------|-------------|------------|
| Lama (whole-report, desc 500, tf linier) | 0.2017      | 0.3163     |
| Baru (per-chunk + name-boost + sublinear |             |            |
|       + deskripsi KB 1000 char)          | 0.3628      | 0.5067     |

Tiga komponen upgrade:
1. sublinear_tf=True — meredam dominasi term frekuensi tinggi pada laporan
   panjang.
2. Name-boost — teknik yang NAMANYA muncul verbatim di teks laporan dijamin
   masuk (dan ditaruh di depan) daftar kandidat. Laporan CTI sangat sering
   menyebut nama teknik secara eksplisit ("PowerShell", "Scheduled Task").
3. Retrieval per-chunk (lihat retrieve_candidates_per_chunk) — tiap potongan
   laporan mendapat daftar kandidatnya sendiri, sehingga total kandidat yang
   dilihat LLM lebih banyak TANPA menambah konsumsi context window per prompt.
"""
import os
import re
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Konstanta modul ini dibaca dari env SAAT IMPORT. Muat .env di sini (bukan
# mengandalkan load_dotenv() di technique_agent) karena import agents.retrieval
# dieksekusi SEBELUM load_dotenv() pemanggil berjalan — tanpa ini, override
# .env seperti LOCAL_LLM_REPORT_MAX_CHARS diam-diam tidak berlaku.
load_dotenv()


# Panjang maksimum teks laporan yang diumpankan ke TF-IDF saat retrieval.
RETRIEVAL_MAX_CHARS = int(os.getenv("RETRIEVAL_MAX_CHARS", "20000"))
# Sertakan sub-teknik (T1566.001) sebagai kandidat retrieval selain base-technique.
INCLUDE_SUBTECHNIQUES = os.getenv("TECHNIQUE_INCLUDE_SUBTECHNIQUES", "true").lower() == "true"
# Panjang deskripsi teknik yang dipakai dalam dokumen TF-IDF. Sinkron dengan
# ATTCK_DESC_MAX_CHARS di attck_loader (deskripsi KB dipotong duluan di sana).
RETRIEVAL_DOC_DESC_CHARS = int(os.getenv("RETRIEVAL_DOC_DESC_CHARS", "1000"))
# Jamin teknik yang namanya disebut verbatim di laporan masuk daftar kandidat.
RETRIEVAL_NAME_BOOST = os.getenv("RETRIEVAL_NAME_BOOST", "true").lower() == "true"
# Buang teknik yang SELURUH taktiknya pre-compromise (reconnaissance /
# resource-development) dari kandidat retrieval. Ground truth TRAM II tidak
# pernah melabeli fase ini (0 dari 1943 label), sementara 88 teknik tsb terus
# terprediksi sebagai FP murni; membuangnya juga membebaskan slot kandidat
# (plafon recall exact@50 naik 48,7% -> 51,6%). Set false bila memakai dataset
# lain yang melabeli fase pre-compromise.
RETRIEVAL_EXCLUDE_PRECOMPROMISE = (
    os.getenv("RETRIEVAL_EXCLUDE_PRECOMPROMISE", "true").lower() == "true"
)
_PRECOMPROMISE_TACTICS = {"reconnaissance", "resource-development"}

# Parameter chunking laporan — satu sumber kebenaran untuk technique_agent
# (prompt LLM) dan skrip evaluasi offline (rekonstruksi kandidat per-chunk).
LOCAL_LLM_REPORT_MAX_CHARS = int(os.getenv("LOCAL_LLM_REPORT_MAX_CHARS", "3500"))
CHUNK_OVERLAP_CHARS = int(os.getenv("LLM_CHUNK_OVERLAP_CHARS", "250"))
MAX_CHUNKS = int(os.getenv("LLM_MAX_CHUNKS", "3"))

# Sidik jari konfigurasi retrieval. Ikut divalidasi oleh cache kandidat skrip
# evaluasi (_candidates_top50*.json) — naikkan versi bila logika berubah agar
# cache lama tidak dianggap valid.
# --- Retrieval hybrid TF-IDF + embedding semantik (opsional) ---
# Skor akhir = alpha * z(embedding) + (1-alpha) * z(TF-IDF). Embedding dihitung
# per-JENDELA kecil teks (max-pool antar jendela) agar kalimat spesifik tidak
# tenggelam dalam chunk panjang. Terukur offline (151 laporan): plafon recall
# exact@50 0.559 -> 0.591, base 0.688 -> 0.705. Model embedding di-serve LM
# Studio (endpoint yang sama dengan LLM); bila server/model tak tersedia,
# retrieval otomatis jatuh kembali ke TF-IDF murni (dengan peringatan).
RETRIEVAL_EMBEDDING_HYBRID = (
    os.getenv("RETRIEVAL_EMBEDDING_HYBRID", "true").lower() == "true"
)
EMBEDDING_MODEL = os.getenv(
    "RETRIEVAL_EMBEDDING_MODEL", "text-embedding-nomic-embed-text-v1.5"
)
EMBEDDING_ALPHA = float(os.getenv("RETRIEVAL_EMBEDDING_ALPHA", "0.5"))
EMBEDDING_WINDOW_CHARS = int(os.getenv("RETRIEVAL_EMBEDDING_WINDOW_CHARS", "700"))
EMBEDDING_WINDOW_STEP = int(os.getenv("RETRIEVAL_EMBEDDING_WINDOW_STEP", "600"))

RETRIEVAL_SIGNATURE = (
    f"v5:sublinear:desc{RETRIEVAL_DOC_DESC_CHARS}:nameboost{int(RETRIEVAL_NAME_BOOST)}:"
    f"noprecomp{int(RETRIEVAL_EXCLUDE_PRECOMPROMISE)}:"
    f"chunk{LOCAL_LLM_REPORT_MAX_CHARS}-{CHUNK_OVERLAP_CHARS}-{MAX_CHUNKS}:"
    f"emb{int(RETRIEVAL_EMBEDDING_HYBRID)}-a{EMBEDDING_ALPHA}-w{EMBEDDING_WINDOW_CHARS}"
)


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


def coverage_stats(
    report_text: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
    max_chunks: int | None = None,
) -> dict:
    """Jangkauan pembacaan efektif satu laporan dengan parameter chunking aktif.

    Chunk dibatasi max_chunks, sehingga laporan yang lebih panjang dari
    chunk_size + (max_chunks-1) * (chunk_size - overlap) TIDAK PERNAH dibaca
    seluruhnya oleh agen — bagian ekornya tak pernah masuk prompt manapun.
    Fungsi ini membuat batas itu terukur per laporan, bukan konstanta tersembunyi.

    Karena chunk bersifat kontigu (mulai dari indeks 0, hanya bergeser dengan
    overlap), jangkauan = indeks akhir chunk terakhir.

    Returns:
        report_chars   : panjang teks laporan asli
        coverage_chars : jumlah karakter yang benar-benar masuk ke prompt
        coverage_ratio : coverage_chars / report_chars (1.0 = terbaca utuh)
        chunks         : jumlah chunk yang dihasilkan
        retrieval_chars: panjang teks yang dilihat TF-IDF (RETRIEVAL_MAX_CHARS)
    """
    chunk_size = LOCAL_LLM_REPORT_MAX_CHARS if chunk_size is None else chunk_size
    overlap = CHUNK_OVERLAP_CHARS if overlap is None else overlap
    max_chunks = MAX_CHUNKS if max_chunks is None else max_chunks

    text = report_text or ""
    report_chars = len(text)
    chunks = _chunk_text(text, chunk_size=chunk_size, overlap=overlap, max_chunks=max_chunks)
    step = max(1, chunk_size - overlap)
    coverage_chars = min(report_chars, (len(chunks) - 1) * step + chunk_size)

    return {
        "report_chars": report_chars,
        "coverage_chars": coverage_chars,
        "coverage_ratio": round(coverage_chars / report_chars, 4) if report_chars else 1.0,
        "chunks": len(chunks),
        "retrieval_chars": min(report_chars, RETRIEVAL_MAX_CHARS),
    }


def _build_technique_document(technique_id: str, technique_data: dict) -> str:
    name = technique_data.get("name", "")
    description = technique_data.get("description", "")[:RETRIEVAL_DOC_DESC_CHARS]
    tactics = " ".join(technique_data.get("tactics", []))
    return f"{technique_id} {name}. {description} {tactics}"


# Cache regex nama teknik (di-compile sekali per nama, dipakai lintas laporan).
_NAME_PATTERN_CACHE: dict[str, re.Pattern] = {}

# Taktik pre-compromise juga dikecualikan dari name-boost (relevan saat
# RETRIEVAL_EXCLUDE_PRECOMPROMISE=false). Nama tekniknya kata Inggris generik
# ("Exploits", "Vulnerabilities", "Vulnerability Scanning") yang muncul di
# hampir semua laporan CTI (sering di kalimat mitigasi) — smoke test
# 2026-07-11: T1587.004/T1595.002 terprediksi di 3/3 laporan (semuanya FP).
_NAME_BOOST_EXCLUDED_TACTICS = _PRECOMPROMISE_TACTICS


def _match_named_techniques(report_text: str, attck_techniques: dict) -> list[str]:
    """Teknik yang NAMANYA muncul verbatim di teks (word-boundary, case-insensitive).

    Hanya nama dengan panjang >= 8 karakter ATAU >= 2 kata yang dicocokkan,
    agar nama super-generik satu kata ("Malware", "Tool", "Phishing" lolos
    karena 8 huruf) tidak memicu match di hampir semua laporan. Teknik yang
    seluruh taktiknya pre-compromise juga dikecualikan (lihat catatan di atas).
    """
    low = report_text.lower()
    hits = []
    for tid, tdata in attck_techniques.items():
        name = str(tdata.get("name", "")).strip()
        if not name or (len(name) < 8 and " " not in name):
            continue
        tactics = set(tdata.get("tactics", []))
        if tactics and tactics <= _NAME_BOOST_EXCLUDED_TACTICS:
            continue
        key = name.lower()
        if key not in low:  # saringan cepat sebelum regex
            continue
        pattern = _NAME_PATTERN_CACHE.get(key)
        if pattern is None:
            pattern = re.compile(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])")
            _NAME_PATTERN_CACHE[key] = pattern
        if pattern.search(low):
            hits.append(tid)
    return hits


# State runtime embedding: client OpenAI, matriks embedding dokumen KB, dan
# flag disabled (sekali gagal -> nonaktif untuk sisa proses agar perilaku
# konsisten, tidak campur hybrid/TF-IDF antar laporan).
#
# Tiga pencacah terakhir ada supaya manifest bisa membuktikan retrieval run ini
# BENAR-BENAR hibrida sepanjang run, bukan sekadar menyalin nilai env
# RETRIEVAL_EMBEDDING_HYBRID. Sebelum ini, server embedding yang mati di tengah
# run membuat sisa laporan diproses TF-IDF murni tanpa jejak apapun di manifest
# — pola persis yang sudah ditutup untuk reviewer_active lewat bukti runtime.
_EMB_STATE: dict = {
    "client": None,
    "ids": None,
    "doc_vecs": None,
    "disabled": False,
    "calls_hybrid": 0,       # panggilan retrieval yang skornya benar-benar fusi
    "calls_tfidf_only": 0,   # panggilan retrieval yang jatuh ke TF-IDF murni
    "fallback_reason": None, # pesan error pertama yang memicu fallback
}


def embedding_runtime_state() -> dict:
    """Bukti runtime status retrieval hibrida, untuk dicatat di manifest.

    `fallback_triggered=True` berarti sebagian (atau seluruh) run berjalan
    dengan TF-IDF murni meski env menyatakan hybrid aktif.
    """
    hybrid = _EMB_STATE["calls_hybrid"]
    tfidf_only = _EMB_STATE["calls_tfidf_only"]
    total = hybrid + tfidf_only
    return {
        "hybrid_requested": RETRIEVAL_EMBEDDING_HYBRID,
        "fallback_triggered": bool(_EMB_STATE["disabled"]),
        "fallback_reason": _EMB_STATE["fallback_reason"],
        "retrieval_calls_hybrid": hybrid,
        "retrieval_calls_tfidf_only": tfidf_only,
        "pct_calls_hybrid": round(hybrid / total, 4) if total else None,
    }
# Cache embedding dokumen KB di disk (dihitung sekali per isi KB + model).
_EMB_CACHE_PATH = Path(__file__).resolve().parents[2] / "results" / "metrics" / "_emb_docs_cache.npz"


def _emb_client():
    if _EMB_STATE["client"] is None:
        from openai import OpenAI

        base_url = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:1234").rstrip("/")
        api_key = os.getenv("LOCAL_LLM_API_KEY", "") or "lm-studio"
        _EMB_STATE["client"] = OpenAI(base_url=f"{base_url}/v1", api_key=api_key)
    return _EMB_STATE["client"]


def _embed_texts(texts: list[str], batch: int = 32) -> np.ndarray:
    """Embed daftar teks via LM Studio; hasil dinormalisasi L2 per baris."""
    vecs = []
    client = _emb_client()
    for i in range(0, len(texts), batch):
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=texts[i:i + batch])
        vecs.extend(d.embedding for d in resp.data)
    arr = np.asarray(vecs, dtype=np.float32)
    return arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9)


def _ensure_doc_embeddings(candidate_ids: list[str], documents: list[str]) -> np.ndarray | None:
    """Embedding dokumen KB (dihitung sekali, di-cache ke disk & memori).

    Mengembalikan None (dan menonaktifkan hybrid) bila server embedding gagal.
    """
    if _EMB_STATE["disabled"]:
        return None
    if _EMB_STATE["doc_vecs"] is not None and _EMB_STATE["ids"] == candidate_ids:
        return _EMB_STATE["doc_vecs"]

    cache_key = f"{EMBEDDING_MODEL}|{len(candidate_ids)}|{sum(len(d) for d in documents)}"
    try:
        if _EMB_CACHE_PATH.exists():
            npz = np.load(_EMB_CACHE_PATH, allow_pickle=False)
            if str(npz.get("cache_key")) == cache_key and list(npz["ids"]) == candidate_ids:
                _EMB_STATE.update({"ids": list(candidate_ids), "doc_vecs": npz["vecs"]})
                return _EMB_STATE["doc_vecs"]
    except Exception:
        pass  # cache korup -> hitung ulang

    try:
        vecs = _embed_texts([f"search_document: {d}" for d in documents])
    except Exception as exc:
        print(
            f"Warning: server embedding tidak tersedia ({str(exc)[:80]}) — "
            "retrieval jatuh kembali ke TF-IDF murni untuk sisa proses."
        )
        _EMB_STATE["disabled"] = True
        _EMB_STATE["fallback_reason"] = f"doc embedding: {str(exc)[:200]}"
        return None

    _EMB_STATE.update({"ids": list(candidate_ids), "doc_vecs": vecs})
    try:
        _EMB_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            _EMB_CACHE_PATH,
            cache_key=np.asarray(cache_key),
            ids=np.asarray(candidate_ids),
            vecs=vecs,
        )
    except Exception:
        pass  # gagal menulis cache bukan masalah fatal
    return vecs


def _embedding_similarity(report_text: str, doc_vecs: np.ndarray) -> np.ndarray | None:
    """Skor embedding query->dokumen: max-pool antar jendela kecil teks."""
    windows = []
    text = report_text[:RETRIEVAL_MAX_CHARS]
    for start in range(0, len(text), EMBEDDING_WINDOW_STEP):
        w = text[start:start + EMBEDDING_WINDOW_CHARS]
        if len(w) >= 80 or not windows:
            windows.append(f"search_query: {w}")
    try:
        w_vecs = _embed_texts(windows)
    except Exception as exc:
        print(
            f"Warning: embedding query gagal ({str(exc)[:80]}) — "
            "retrieval jatuh kembali ke TF-IDF murni untuk sisa proses."
        )
        _EMB_STATE["disabled"] = True
        _EMB_STATE["fallback_reason"] = f"query embedding: {str(exc)[:200]}"
        return None
    return (w_vecs @ doc_vecs.T).max(axis=0)


def _zscore(x: np.ndarray) -> np.ndarray:
    return (x - x.mean()) / (x.std() + 1e-9)


def _merge_forced_candidates(ranked: list[str], forced: list[str], top_k: int) -> list[str]:
    """Gabungkan kandidat name-match (forced) ke daftar ranking TF-IDF.

    Forced ditaruh paling depan (agar lolos anggaran karakter daftar kandidat
    di prompt), sisanya mengikuti urutan skor TF-IDF. Panjang akhir <= top_k.
    """
    if not forced:
        return ranked[:top_k]
    forced_set = set(forced)
    head = [t for t in ranked if t in forced_set]
    head_seen = set(head)
    head += [t for t in forced if t not in head_seen]
    tail = [t for t in ranked if t not in forced_set]
    return (head + tail)[:top_k]


def _retrieve_candidate_techniques(
    report_text: str,
    attck_techniques: dict,
    top_k: int,
    include_subtechniques: bool = INCLUDE_SUBTECHNIQUES,
    name_boost: bool = RETRIEVAL_NAME_BOOST,
) -> list[str]:
    candidates = []
    documents = []

    for technique_id, technique_data in attck_techniques.items():
        if not include_subtechniques and "." in technique_id:
            continue
        if RETRIEVAL_EXCLUDE_PRECOMPROMISE:
            tactics = set(technique_data.get("tactics", []))
            if tactics and tactics <= _PRECOMPROMISE_TACTICS:
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
            sublinear_tf=True,
        )
        matrix = vectorizer.fit_transform([report_text[:RETRIEVAL_MAX_CHARS]] + documents)
        similarity = cosine_similarity(matrix[0:1], matrix[1:]).flatten()

        # Fusi dengan skor embedding semantik (z-score keduanya agar skala
        # sebanding). Bila server embedding tak tersedia, similarity TF-IDF
        # murni tetap dipakai (fallback otomatis, lihat _ensure_doc_embeddings).
        fused = False
        if RETRIEVAL_EMBEDDING_HYBRID and not _EMB_STATE["disabled"]:
            doc_vecs = _ensure_doc_embeddings(candidates, documents)
            if doc_vecs is not None:
                emb_sim = _embedding_similarity(report_text, doc_vecs)
                if emb_sim is not None:
                    similarity = (
                        EMBEDDING_ALPHA * _zscore(emb_sim)
                        + (1 - EMBEDDING_ALPHA) * _zscore(similarity)
                    )
                    fused = True
        if RETRIEVAL_EMBEDDING_HYBRID:
            _EMB_STATE["calls_hybrid" if fused else "calls_tfidf_only"] += 1

        ranked_indices = similarity.argsort()[::-1]
        ranked = [candidates[idx] for idx in ranked_indices]

        forced: list[str] = []
        if name_boost:
            allowed = set(candidates)
            forced = [
                t for t in _match_named_techniques(report_text, attck_techniques)
                if t in allowed
            ]
        return _merge_forced_candidates(ranked, forced, min(top_k, len(candidates)))

    except Exception as e:
        # Fallback aman jika retrieval gagal: pertahankan perilaku lama (urutan awal dictionary).
        print(f"Warning retrieval kandidat gagal, pakai fallback default: {e}")
        return candidates[:top_k]


def retrieve_candidates_per_chunk(
    report_text: str,
    attck_techniques: dict,
    top_k: int,
) -> list[tuple[str, list[str]]]:
    """Retrieval per-chunk: tiap potongan laporan mendapat kandidatnya sendiri.

    Mengembalikan list pasangan (chunk_text, candidate_ids). Chunking memakai
    parameter yang sama dengan prompt LLM (LOCAL_LLM_REPORT_MAX_CHARS dst.),
    sehingga daftar kandidat tiap prompt relevan dengan potongan yang dibaca.
    """
    chunks = _chunk_text(
        report_text,
        chunk_size=LOCAL_LLM_REPORT_MAX_CHARS,
        overlap=CHUNK_OVERLAP_CHARS,
        max_chunks=MAX_CHUNKS,
    )
    return [
        (
            chunk,
            _retrieve_candidate_techniques(
                report_text=chunk,
                attck_techniques=attck_techniques,
                top_k=top_k,
            ),
        )
        for chunk in chunks
    ]
