# Tunggu run preset A yang sedang berjalan selesai, lalu jalankan sisa rantai.
#
# Konteks 6 Agustus 2026: preset A diluncurkan 11:13 sebagai anak dari task
# harness. Task-nya dihentikan, tetapi PROSES PYTHON-nya selamat dan terus
# berjalan - hanya rantai preset berikutnya yang ikut hilang bersama shell
#
# CATATAN: berkas .ps1 ini WAJIB ASCII murni. Windows PowerShell 5.1 membaca
# skrip tanpa BOM sebagai ANSI, sehingga em dash UTF-8 terbaca sebagai tiga
# karakter yang terakhirnya kutip-tutup pintar - dan PowerShell memperlakukan
# kutip pintar sebagai penutup string. Versi pertama skrip ini gagal parse
# persis karena itu.
# pembungkusnya. Skrip ini menyambung kembali rantai itu tanpa membuang
# kemajuan preset A (17 dari 30 laporan saat skrip ini dibuat).
#
# Dijalankan lewat Start-Process supaya lepas dari induknya.

param(
    [int]$WaitForPid = 0,
    # Preset yang akan dijalankan berurutan. Default = seluruh sisa rantai.
    # Untuk melanjutkan sesudah G selesai:  -Presets H_acak_kandidat,E_jangkauan_penuh,F_jangkauan_reviewer
    [string[]]$Presets = @(
        "G_tanpa_filter",
        "H_acak_kandidat",
        "E_jangkauan_penuh",
        "F_jangkauan_reviewer"
    )
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $env:BASE_URL) { $env:BASE_URL = "http://192.168.50.2:1234" }
$env:PYTHONUNBUFFERED = "1"

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logDir = Join-Path $root "experiments\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$statusFile = Join-Path $logDir "STATUS_sisa_$stamp.log"

function Write-Status([string]$msg) {
    "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg |
        Out-File -FilePath $statusFile -Encoding utf8 -Append
}

Write-Status "penyambung rantai mulai (pid $PID), menunggu pid $WaitForPid"

# Menunggu proses preset A selesai. Polling, bukan Wait-Process, supaya tetap
# aman bila prosesnya sudah lebih dulu berakhir saat skrip ini mulai.
if ($WaitForPid -gt 0) {
    while ($true) {
        $p = Get-Process -Id $WaitForPid -ErrorAction SilentlyContinue
        if (-not $p) { break }
        Start-Sleep -Seconds 30
    }
    Write-Status "pid $WaitForPid selesai - melanjutkan rantai"
}

Write-Status ("preset yang akan dijalankan: " + ($Presets -join ", "))

foreach ($p in $Presets) {
    $log = Join-Path $logDir "$($p)_$stamp.log"
    Write-Status "MULAI $p -> $log"
    # *> $log akan menulis UTF-16 (default Out-File PowerShell 5.1), yang membuat
    # grep/tail biasa tidak menemukan apa pun - pemantauan run G 6 Agustus 2026
    # buta total karenanya. Paksa UTF-8.
    & python scripts/run_experiment.py --preset $p --reports 30 --base-url $env:BASE_URL 2>&1 |
        Out-File -FilePath $log -Encoding utf8
    Write-Status "SELESAI $p (exit=$LASTEXITCODE)"
}

Write-Status "SEMUA PRESET SISA SELESAI"
