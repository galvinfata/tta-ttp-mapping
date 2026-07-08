import json
from pathlib import Path

prev_path = Path("results/predictions/results_prev.json")
new_path = Path("results/predictions/results.json")

if not prev_path.exists():
    raise SystemExit(f"Missing {prev_path}")
if not new_path.exists():
    raise SystemExit(f"Missing {new_path}")

prev = json.loads(prev_path.read_text(encoding="utf-8"))
new = json.loads(new_path.read_text(encoding="utf-8"))

prev_map = {item["report_id"]: item for item in prev}
new_map = {item["report_id"]: item for item in new}

all_ids = sorted(set(prev_map) | set(new_map))

changed = []
for rid in all_ids:
    p = prev_map.get(rid)
    n = new_map.get(rid)
    if p is None or n is None:
        changed.append((rid, "added" if p is None else "removed", [], []))
        continue

    p_pred = p.get("predicted_techniques", [])
    n_pred = n.get("predicted_techniques", [])
    if p_pred != n_pred:
        changed.append((rid, "predicted_changed", p_pred, n_pred))

print(f"Total reports prev: {len(prev)}")
print(f"Total reports new : {len(new)}")
print(f"Changes detected  : {len(changed)}")

for rid, kind, p_pred, n_pred in changed:
    if kind == "predicted_changed":
        print("-" * 60)
        print(f"Report: {rid}")
        print(f"Prev predicted ({len(p_pred)}): {p_pred}")
        print(f"New  predicted ({len(n_pred)}): {n_pred}")
    else:
        print("-" * 60)
        print(f"Report: {rid} ({kind})")
