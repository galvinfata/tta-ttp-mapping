# Jalankan rantai preset secara berurutan, sebagai proses MANDIRI.
#
# Versi PowerShell ini ada karena varian bash-nya dijalankan sebagai anak dari
# task harness: saat task itu dihentikan, python ikut mati di tengah run (terjadi
# 6 Agustus 2026, preset A mati di laporan ke-17 setelah 47 menit). Skrip ini
# dimaksudkan diluncurkan lewat Start-Process sehingga lepas dari induknya.
#
# Urutan A -> G -> H -> E -> F disengaja: inti ilmiah (efek filter ACCEPT_TOP_N
# dan apakah LLM benar-benar membaca) selesai lebih dulu bila run harus
# dihentikan di tengah jalan.
#
# Berkas .ps1 ini WAJIB ASCII murni: PowerShell 5.1 membaca skrip tanpa BOM
# sebagai ANSI, dan em dash UTF-8 berubah menjadi kutip pintar yang menutup
# string lebih awal.
#
# Kegagalan satu preset TIDAK menghentikan sisanya - preflight akan gagal cepat
# bila yang mati adalah servernya.

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $env:BASE_URL) { $env:BASE_URL = "http://192.168.50.2:1234" }
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logDir = Join-Path $root "experiments\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

# Penanda hidup/mati untuk pengawasan dari luar tanpa perlu menebak dari log.
$statusFile = Join-Path $logDir "STATUS_$stamp.log"

# PYTHONUNBUFFERED: tanpa ini stdout python di-buffer saat dialihkan ke berkas,
# sehingga progres run panjang tidak terlihat sampai buffer penuh.
$env:PYTHONUNBUFFERED = "1"

$presets = @(
    "A_baseline_replikasi",
    "G_tanpa_filter",
    "H_acak_kandidat",
    "E_jangkauan_penuh",
    "F_jangkauan_reviewer"
)

"[{0}] MULAI rantai preset (pid {1})" -f (Get-Date -Format "HH:mm:ss"), $PID |
    Out-File -FilePath $statusFile -Encoding utf8 -Append

foreach ($p in $presets) {
    $log = Join-Path $logDir "$($p)_$stamp.log"
    "[{0}] MULAI $p -> $log" -f (Get-Date -Format "HH:mm:ss") |
        Out-File -FilePath $statusFile -Encoding utf8 -Append

    # Paksa UTF-8: *> $log menulis UTF-16 sehingga log tidak terbaca grep/tail.
    & python scripts/run_experiment.py --preset $p --reports 30 --base-url $env:BASE_URL 2>&1 |
        Out-File -FilePath $log -Encoding utf8
    $code = $LASTEXITCODE

    "[{0}] SELESAI $p (exit=$code)" -f (Get-Date -Format "HH:mm:ss") |
        Out-File -FilePath $statusFile -Encoding utf8 -Append
}

"[{0}] SEMUA PRESET SELESAI" -f (Get-Date -Format "HH:mm:ss") |
    Out-File -FilePath $statusFile -Encoding utf8 -Append
