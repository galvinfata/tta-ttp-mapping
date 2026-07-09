import json
from pathlib import Path


def _get_domain_from_source(file_path: Path, obj: dict) -> str:
    domains = obj.get("x_mitre_domains") or []
    if isinstance(domains, list) and domains:
        return str(domains[0])

    stem = file_path.stem.lower()
    if stem.endswith("-attack"):
        return stem
    return "enterprise-attack"


def _iter_attck_files(attck_source: str) -> list[Path]:
    source_path = Path(attck_source)
    if source_path.is_file():
        return [source_path]
    if source_path.is_dir():
        return sorted(source_path.glob("*.json"))
    raise FileNotFoundError(f"Sumber ATT&CK tidak ditemukan: {attck_source}")


def load_attck_techniques(attck_source: str) -> dict:
    """
    Memuat seluruh teknik ATT&CK dari satu file atau folder JSON MITRE CTI.
    
    Returns:
        dict: {
            "T1566": {
                "id": "T1566",
                "name": "Phishing",
                "description": "...",
                "tactic": ["initial-access"]
            }
        }
    """
    attack_files = _iter_attck_files(attck_source)
    if not attack_files:
        raise FileNotFoundError(f"Tidak ada file JSON ATT&CK di: {attck_source}")

    techniques = {}

    for attack_file in attack_files:
        with open(attack_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        for obj in data.get("objects", []):
            # Hanya ambil attack-pattern (teknik ATT&CK)
            if obj.get("type") != "attack-pattern":
                continue

            # Skip yang sudah deprecated ATAU revoked (ID usang seperti T1017/T1077
            # tetap ada di STIX sebagai objek revoked; tanpa filter ini mereka bocor
            # jadi kandidat retrieval dan menjadi false positive).
            if obj.get("x_mitre_deprecated", False) or obj.get("revoked", False):
                continue

            # Ambil ID teknik (T1566, T1059, dll)
            technique_id = None
            for ref in obj.get("external_references", []):
                source_name = str(ref.get("source_name", "")).lower()
                external_id = ref.get("external_id")

                is_attack_source = source_name.startswith("mitre") and "attack" in source_name
                is_attack_technique = isinstance(external_id, str) and external_id.startswith("T")

                if is_attack_source and is_attack_technique:
                    technique_id = external_id
                    break

            if not technique_id or not str(technique_id).startswith("T"):
                continue

            # Ambil taktik
            tactics = []
            for phase in obj.get("kill_chain_phases", []):
                if phase.get("kill_chain_name") == "mitre-attack":
                    tactics.append(phase.get("phase_name"))

            domain = _get_domain_from_source(attack_file, obj)

            if technique_id in techniques:
                existing = techniques[technique_id]
                merged_tactics = sorted(set(existing.get("tactics", []) + tactics))
                merged_domains = sorted(set(existing.get("domains", []) + [domain]))
                description = existing.get("description", "")
                new_description = obj.get("description", "")[:500]
                if len(new_description) > len(description):
                    description = new_description

                existing.update({
                    "tactics": merged_tactics,
                    "domains": merged_domains,
                    "description": description,
                })
            else:
                techniques[technique_id] = {
                    "id": technique_id,
                    "name": obj.get("name", ""),
                    "description": obj.get("description", "")[:500],
                    "tactics": sorted(set(tactics)),
                    "stix_id": obj.get("id", ""),
                    "domains": [domain],
                }
    
    return techniques


def load_attck_tactics(attck_source: str) -> dict:
    """
    Memuat daftar taktik ATT&CK dari satu file/folder MITRE CTI.

    Returns:
        dict: {"TA0001": "Initial Access", ...}
    """
    attack_files = _iter_attck_files(attck_source)
    if not attack_files:
        raise FileNotFoundError(f"Tidak ada file JSON ATT&CK di: {attck_source}")

    tactics = {}

    for attack_file in attack_files:
        with open(attack_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        for obj in data.get("objects", []):
            if obj.get("type") != "x-mitre-tactic":
                continue
            if obj.get("x_mitre_deprecated", False) or obj.get("revoked", False):
                continue

            tactic_id = None
            for ref in obj.get("external_references", []):
                source_name = str(ref.get("source_name", "")).lower()
                external_id = ref.get("external_id")
                if (
                    source_name.startswith("mitre")
                    and "attack" in source_name
                    and isinstance(external_id, str)
                    and external_id.startswith("TA")
                ):
                    tactic_id = external_id
                    break

            if not tactic_id:
                continue

            tactics[tactic_id] = obj.get("name", "")

    return dict(sorted(tactics.items()))


def get_technique_names(techniques: dict) -> dict:
    """
    Membuat mapping sederhana ID → nama untuk prompt LLM.
    """
    return {k: v["name"] for k, v in techniques.items()}


if __name__ == "__main__":
    techniques = load_attck_techniques(
        "data/mitre_cti"
    )
    print(f"Total teknik ATT&CK: {len(techniques)}")
    
    # Contoh
    if "T1566" in techniques:
        print(f"\nContoh T1566:")
        print(f"  Nama: {techniques['T1566']['name']}")
        print(f"  Taktik: {techniques['T1566']['tactics']}")