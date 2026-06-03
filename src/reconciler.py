def reconcile_results(
    tactics: list[str],
    techniques: list[str],
    attck_techniques: dict
) -> list[str]:
    """
    Menggabungkan dan merekonsiliasi hasil dari 
    Tactic Agent dan Technique Agent.
    
    Logika:
    1. Pertahankan semua teknik yang valid
    2. Filter teknik yang taktiknya tidak konsisten
    3. Hapus duplikat
    
    Returns:
        list of reconciled technique IDs
    """
    
    if not techniques:
        return []
    
    reconciled = []
    
    # Mapping taktik ID ke nama fase
    tactic_phase_map = {
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
        "TA0043": "reconnaissance"
    }
    
    # Konversi tactic IDs ke phase names
    identified_phases = set()
    for tactic_id in tactics:
        phase = tactic_phase_map.get(tactic_id)
        if phase:
            identified_phases.add(phase)
    
    # Kumpulkan dulu seluruh teknik valid (ada di knowledge base).
    valid_techniques = [t for t in techniques if t in attck_techniques]

    for technique_id in valid_techniques:
        technique_data = attck_techniques[technique_id]
        technique_tactics = set(technique_data.get("tactics", []))

        # Kalau tidak ada taktik teridentifikasi,
        # pertahankan semua teknik valid
        if not identified_phases:
            reconciled.append(technique_id)
            continue

        # Pertahankan teknik yang taktiknya konsisten dengan taktik teridentifikasi
        if technique_tactics.intersection(identified_phases):
            reconciled.append(technique_id)

    # Safety net: prediksi taktik dari LLM tidak sempurna. Jika filter konsistensi
    # justru membuang SEMUA teknik valid, jangan kosongkan hasil — pertahankan
    # seluruh teknik valid agar recall tidak hilang total (penting untuk demo/PoC).
    if not reconciled and valid_techniques:
        reconciled = list(valid_techniques)

    # Hapus duplikat sambil pertahankan urutan
    seen = set()
    final = []
    for t in reconciled:
        if t not in seen:
            seen.add(t)
            final.append(t)

    return final