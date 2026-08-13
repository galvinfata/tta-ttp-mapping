#!/usr/bin/env bash
# Jalankan rantai preset secara berurutan. Urutan A -> G -> H -> E -> F dipilih
# supaya inti ilmiah (efek filter + apakah LLM membaca) selesai lebih dulu bila
# run harus dihentikan di tengah jalan.
#
# Tiap preset ditulis ke log terpisah; kegagalan satu preset TIDAK menghentikan
# sisanya (preflight akan gagal cepat bila servernya yang mati).
cd "$(dirname "$0")/.." || exit 1
BASE_URL="${BASE_URL:-http://192.168.50.2:1234}"
STAMP="$(date +%Y%m%d_%H%M%S)"
for P in A_baseline_replikasi G_tanpa_filter H_acak_kandidat E_jangkauan_penuh F_jangkauan_reviewer; do
  LOG="experiments/logs/${P}_${STAMP}.log"
  echo "=== [$(date +%H:%M:%S)] MULAI $P -> $LOG"
  python scripts/run_experiment.py --preset "$P" --reports 30 --base-url "$BASE_URL" > "$LOG" 2>&1
  echo "=== [$(date +%H:%M:%S)] SELESAI $P (exit=$?)"
done
echo "=== SEMUA PRESET SELESAI"
