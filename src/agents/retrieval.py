"""Retrieval kandidat teknik berbasis TF-IDF (murni, tanpa dependensi LLM).

Modul ini sengaja dipisah dari technique_agent.py agar bagian retrieval bisa
dipakai ulang secara OFFLINE dan DETERMINISTIK (mis. oleh skrip evaluasi
baseline) tanpa ikut memuat klien LLM (openai) atau memuat variabel .env.
technique_agent.py mengimpor ulang fungsi & konstanta dari sini agar perilaku
sistem yang sudah ada tidak berubah.
"""
import os

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


# Panjang maksimum teks laporan yang diumpankan ke TF-IDF saat retrieval.
RETRIEVAL_MAX_CHARS = int(os.getenv("RETRIEVAL_MAX_CHARS", "20000"))
# Sertakan sub-teknik (T1566.001) sebagai kandidat retrieval selain base-technique.
INCLUDE_SUBTECHNIQUES = os.getenv("TECHNIQUE_INCLUDE_SUBTECHNIQUES", "true").lower() == "true"


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
