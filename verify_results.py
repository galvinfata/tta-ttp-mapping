import json

with open('results/predictions/results_all.json') as f:
    results = json.load(f)

print(f"Total results: {len(results)}")
print(f"\nSample report IDs (first 5):")
for item in results[:5]:
    print(f"  - {item['report_id'][:70]}")

print(f"\nPrediction summary (first 10):")
for i, item in enumerate(results[:10], 1):
    pred_count = len(item.get('predicted_techniques', []))
    ground_count = len(item.get('ground_truth', []))
    print(f"  {i}. Report: {item['report_id'][:50]} | Predicted: {pred_count} | Ground Truth: {ground_count}")

total_predicted = sum(len(item.get('predicted_techniques', [])) for item in results)
total_ground = sum(len(item.get('ground_truth', [])) for item in results)
print(f"\nOverall totals:")
print(f"  Total predicted techniques: {total_predicted}")
print(f"  Total ground truth techniques: {total_ground}")
