"""Ekstraksi kalimat rujukan (evidence) untuk hasil pemetaan TTP.

Untuk tiap taktik/teknik yang terpetakan, dicari kalimat di laporan yang paling
relevan menggunakan TF-IDF + cosine similarity (tanpa panggilan LLM tambahan).
Berguna agar laporan PDF menampilkan dasar/bukti dari setiap mapping.
"""
from __future__ import annotations

import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Ambang minimal kemiripan agar sebuah kalimat dianggap relevan.
MIN_SIMILARITY = 0.06
NO_EVIDENCE = "(tidak ada kalimat spesifik yang teridentifikasi)"

# Tactic ID -> kill-chain phase name (untuk menautkan teknik ke taktiknya).
TACTIC_ID_TO_PHASE = {
    "TA0001": "initial-access",
    "TA0002": "execution",
    "TA0003": "persistence",
    "TA0004": "privilege-escalation",
    "TA0005": "defense-evasion",
    "TA0006": "credential-access",
    "TA0007": "discovery",
    "TA0008": "lateral-movement",
    "TA0009": "collection",
    "TA0010": "exfiltration",
    "TA0011": "command-and-control",
    "TA0040": "impact",
    "TA0042": "resource-development",
    "TA0043": "reconnaissance",
}


def split_sentences(text: str) -> list[str]:
    """Pecah teks laporan menjadi daftar kalimat yang bersih."""
    if not text:
        return []

    # Normalisasi spasi non-breaking & whitespace berlebih.
    text = text.replace("\xa0", " ")
    # Pisah berdasarkan newline dan batas kalimat (. ! ?).
    raw_parts = re.split(r"(?<=[.!?])\s+|\n+", text)

    sentences = []
    for part in raw_parts:
        s = part.strip()
        # Buang baris metadata khas (title:, url:, dsb.) dan potongan terlalu pendek.
        if len(s) < 25:
            continue
        if re.match(r"^(title|url|source|author|date|tags?)\s*:", s, re.IGNORECASE):
            continue
        sentences.append(s)

    return sentences


def _best_sentence(query: str, sentences: list[str], matrix, vectorizer) -> str:
    if not query.strip():
        return NO_EVIDENCE
    try:
        q_vec = vectorizer.transform([query])
    except ValueError:
        return NO_EVIDENCE

    sims = cosine_similarity(q_vec, matrix).flatten()
    if sims.size == 0:
        return NO_EVIDENCE

    best_idx = int(sims.argmax())
    if sims[best_idx] < MIN_SIMILARITY:
        return NO_EVIDENCE

    sentence = sentences[best_idx].strip()
    if len(sentence) > 320:
        sentence = sentence[:320].rstrip() + " ..."
    return sentence


def build_evidence_map(
    report_text: str,
    tactics: list[dict],
    techniques: list[dict],
    attck_techniques: dict | None = None,
    attck_tactics: dict | None = None,
) -> tuple[dict, dict]:
    """Bangun peta {id -> kalimat rujukan} untuk taktik & teknik.

    tactics    : [{"id": "TA0001", "name": "Initial Access"}, ...]
    techniques : [{"id": "T1566", "name": "Phishing"}, ...]
    """
    attck_techniques = attck_techniques or {}
    attck_tactics = attck_tactics or {}

    sentences = split_sentences(report_text)
    tactic_ev = {t.get("id", ""): NO_EVIDENCE for t in tactics}
    technique_ev = {t.get("id", ""): NO_EVIDENCE for t in techniques}

    if not sentences:
        return tactic_ev, technique_ev

    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
    try:
        matrix = vectorizer.fit_transform(sentences)
    except ValueError:
        return tactic_ev, technique_ev

    # Teknik: query = nama + cuplikan deskripsi ATT&CK.
    for t in techniques:
        tid = t.get("id", "")
        name = t.get("name", "") or attck_techniques.get(tid, {}).get("name", "")
        desc = attck_techniques.get(tid, {}).get("description", "")[:300]
        # Nama digandakan agar lebih berbobot saat pencocokan.
        query = f"{name} {name} {desc}"
        technique_ev[tid] = _best_sentence(query, sentences, matrix, vectorizer)

    # Taktik: coba cocokkan nama taktik; jika tak ada, pakai kalimat dari teknik
    # yang termasuk taktik tersebut (teks laporan umumnya mendeskripsikan teknik).
    for t in tactics:
        tid = t.get("id", "")
        name = t.get("name", "") or attck_tactics.get(tid, "")
        by_name = _best_sentence(f"{name} {name}", sentences, matrix, vectorizer)

        if by_name != NO_EVIDENCE:
            tactic_ev[tid] = by_name
            continue

        phase = TACTIC_ID_TO_PHASE.get(tid)
        fallback = NO_EVIDENCE
        for tech in techniques:
            ttid = tech.get("id", "")
            tech_phases = attck_techniques.get(ttid, {}).get("tactics", [])
            if phase and phase in tech_phases and technique_ev.get(ttid, NO_EVIDENCE) != NO_EVIDENCE:
                fallback = technique_ev[ttid]
                break
        tactic_ev[tid] = fallback

    return tactic_ev, technique_ev
