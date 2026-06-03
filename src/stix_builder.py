import json
from datetime import datetime, timezone
from stix2 import Bundle, AttackPattern, Relationship, Indicator


def build_stix_bundle(
    report_id: str,
    report_text: str,
    techniques: list[str],
    attck_techniques: dict
) -> dict:
    """
    Mengkonversi hasil pemetaan TTP ke format STIX 2.1.
    
    Returns:
        dict: STIX 2.1 Bundle dalam format JSON
    """
    
    stix_objects = []
    
    for technique_id in techniques:
        if technique_id not in attck_techniques:
            continue
        
        technique_data = attck_techniques[technique_id]
        
        # Buat objek AttackPattern
        attack_pattern = AttackPattern(
            name=technique_data["name"],
            description=technique_data["description"][:200],
            external_references=[
                {
                    "source_name": "mitre-attack",
                    "external_id": technique_id,
                    "url": f"https://attack.mitre.org/techniques/{technique_id}/"
                }
            ],
            kill_chain_phases=[
                {
                    "kill_chain_name": "mitre-attack",
                    "phase_name": tactic
                }
                for tactic in technique_data.get("tactics", [])
            ]
        )
        
        stix_objects.append(attack_pattern)
    
    if not stix_objects:
        return {"type": "bundle", "objects": []}
    
    # Buat Bundle STIX 2.1
    bundle = Bundle(*stix_objects)
    
    return json.loads(bundle.serialize())


if __name__ == "__main__":
    # Test sederhana
    from attck_loader import load_attck_techniques
    
    techniques_db = load_attck_techniques(
        "data/mitre_cti/enterprise-attack.json"
    )
    
    result = build_stix_bundle(
        report_id="test-001",
        report_text="test report",
        techniques=["T1566", "T1059"],
        attck_techniques=techniques_db
    )
    
    print(f"STIX objects: {len(result.get('objects', []))}")