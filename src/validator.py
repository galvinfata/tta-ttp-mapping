def validate_techniques(
    techniques: list[str],
    attck_techniques: dict
) -> dict:
    """
    Memverifikasi teknik hasil Reconciler terhadap 
    ATT&CK knowledge base.
    
    Returns:
        dict: {
            "valid": ["T1566", "T1059"],
            "invalid": ["T9999"]
        }
    """
    
    valid = []
    invalid = []
    
    for technique_id in techniques:
        if technique_id in attck_techniques:
            valid.append(technique_id)
        else:
            invalid.append(technique_id)
    
    if invalid:
        print(f"Validator: {len(invalid)} teknik tidak valid: {invalid}")
    
    return {
        "valid": valid,
        "invalid": invalid
    }