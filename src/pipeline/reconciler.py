import os

# Filter konsistensi taktik bersifat opt-in. Analisis run parsial 2026-07-10
# (results/laporan_run_parsial_20260710.md) menunjukkan filter ini membuang
# ~30% true positive (13 dari 45 TP pada 23 laporan): teknik yang benar ikut
# terbuang hanya karena Tactic Agent luput mengidentifikasi taktik induknya.
# Default: nonaktif (pertahankan semua teknik valid). Set env
# RECONCILE_TACTIC_FILTER=true untuk perilaku lama (pembanding/ablasi).
RECONCILE_TACTIC_FILTER = os.getenv("RECONCILE_TACTIC_FILTER", "false").lower() == "true"

# Cap jumlah sub-teknik per keluarga (T1055.*). Model 4B cenderung "memborong"
# banyak sub-teknik sekeluarga sekaligus saat deskripsi kandidatnya mirip
# (run parsial 2026-07-10: 9 sub-teknik T1055.* dipilih, GT hanya 3). Sub-teknik
# melewati cap diganti base-technique-nya (sekali) agar sinyal keluarga tidak
# hilang. Terukur pada prediksi run parsial: precision exact 0.250 -> 0.268,
# F1 0.183 -> 0.187, recall tidak turun. Set 0 untuk menonaktifkan.
RECONCILE_SUBTECH_FAMILY_CAP = int(os.getenv("RECONCILE_SUBTECH_FAMILY_CAP", "2"))


def reconcile_results(
    tactics: list[str],
    techniques: list[str],
    attck_techniques: dict
) -> list[str]:
    """
    Menggabungkan dan merekonsiliasi hasil dari
    Tactic Agent dan Technique Agent.

    Logika:
    1. Pertahankan semua teknik yang valid (ada di knowledge base)
    2. Opsional (RECONCILE_TACTIC_FILTER=true): filter teknik yang taktiknya
       tidak konsisten dengan taktik teridentifikasi
    3. Hapus duplikat

    Returns:
        list of reconciled technique IDs
    """

    if not techniques:
        return []

    # Kumpulkan dulu seluruh teknik valid (ada di knowledge base).
    valid_techniques = [t for t in techniques if t in attck_techniques]

    if RECONCILE_TACTIC_FILTER:
        reconciled = _filter_by_tactic_consistency(
            tactics, valid_techniques, attck_techniques
        )
    else:
        reconciled = list(valid_techniques)

    if RECONCILE_SUBTECH_FAMILY_CAP > 0:
        reconciled = _cap_subtechnique_families(
            reconciled, attck_techniques, RECONCILE_SUBTECH_FAMILY_CAP
        )

    # Hapus duplikat sambil pertahankan urutan
    seen = set()
    final = []
    for t in reconciled:
        if t not in seen:
            seen.add(t)
            final.append(t)

    return final


def _cap_subtechnique_families(
    techniques: list[str],
    attck_techniques: dict,
    cap: int,
) -> list[str]:
    """Batasi jumlah sub-teknik per keluarga; kelebihan diganti base technique.

    Urutan prediksi dipertahankan (sub-teknik yang lebih dulu diprediksi
    dianggap lebih diyakini). Base technique pengganti hanya ditambahkan bila
    valid di KB dan belum ada di daftar prediksi.
    """
    sub_counts: dict[str, int] = {}
    predicted = set(techniques)
    result: list[str] = []
    for tid in techniques:
        if "." not in tid:
            result.append(tid)
            continue
        family = tid.split(".")[0]
        sub_counts[family] = sub_counts.get(family, 0) + 1
        if sub_counts[family] <= cap:
            result.append(tid)
        elif (
            family in attck_techniques
            and family not in predicted
            and family not in result
        ):
            result.append(family)
    return result


def _filter_by_tactic_consistency(
    tactics: list[str],
    valid_techniques: list[str],
    attck_techniques: dict,
) -> list[str]:
    """Perilaku lama: buang teknik yang taktik induknya tidak teridentifikasi."""

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

    # Kalau tidak ada taktik teridentifikasi, pertahankan semua teknik valid
    if not identified_phases:
        return list(valid_techniques)

    reconciled = []
    for technique_id in valid_techniques:
        technique_data = attck_techniques[technique_id]
        technique_tactics = set(technique_data.get("tactics", []))

        # Pertahankan teknik yang taktiknya konsisten dengan taktik teridentifikasi
        if technique_tactics.intersection(identified_phases):
            reconciled.append(technique_id)

    # Safety net: prediksi taktik dari LLM tidak sempurna. Jika filter konsistensi
    # justru membuang SEMUA teknik valid, jangan kosongkan hasil — pertahankan
    # seluruh teknik valid agar recall tidak hilang total (penting untuk demo/PoC).
    if not reconciled and valid_techniques:
        reconciled = list(valid_techniques)

    return reconciled
