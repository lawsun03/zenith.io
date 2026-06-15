# B57 benchmark runner — combines + equity exports for r_multiple sensitivity
# Runs all jobs in parallel, waits, saves output to research/b57_out/
param()

$ErrorActionPreference = "Stop"
$ROOT = Split-Path $PSScriptRoot -Parent
$PYTHON = Join-Path $ROOT ".venv\Scripts\python.exe"
$BARS_FULL = Join-Path $ROOT "bars\bars_MNQ_dbv_2021_2026.csv"
$BARS_YEARLY = Join-Path $ROOT "bars\yearly"
$OUT_DIR = Join-Path $ROOT "research\b57_out"
$EQ_DIR = Join-Path $ROOT "research\equity_b57"

New-Item -ItemType Directory -Force -Path $OUT_DIR | Out-Null
New-Item -ItemType Directory -Force -Path $EQ_DIR | Out-Null

$jobs = @()

# --- Combine benchmarks (r=2.0, 2.5, 3.0) ---
foreach ($r in @("2.0", "2.5", "3.0")) {
    $tag = "r" + ($r -replace "\.", "p")
    $id = "b57_$tag"
    $label = "B57 iFVG r=$r"
    $outfile = Join-Path $OUT_DIR "combine_${tag}.txt"
    $jobs += Start-Job -Name "combine_$tag" -ScriptBlock {
        param($py, $bars, $r, $id, $label, $outfile, $root)
        Set-Location $root
        & $py "scripts\run_monthly_combine.py" `
            --bars $bars --instrument MNQ --timeframe 5min `
            --set "r_multiple=$r" `
            --save-id $id --save-label $label 2>&1 | Tee-Object $outfile
    } -ArgumentList $PYTHON, $BARS_FULL, $r, $id, $label, $outfile, $ROOT
}

# --- Combine baseline r=3.5 (MNQ override, no --set r_multiple) ---
$outfile35 = Join-Path $OUT_DIR "combine_r3p5.txt"
$jobs += Start-Job -Name "combine_r3p5" -ScriptBlock {
    param($py, $bars, $outfile, $root)
    Set-Location $root
    & $py "scripts\run_monthly_combine.py" `
        --bars $bars --instrument MNQ --timeframe 5min `
        --save-id b57_r35_base --save-label "B57 iFVG r=3.5 (baseline)" 2>&1 | Tee-Object $outfile
} -ArgumentList $PYTHON, $BARS_FULL, $outfile35, $ROOT

# --- Equity exports per year (r=2.0, 2.5, 3.0, excl 2022) ---
$YEARS = @("2021", "2023", "2024", "2025", "2026")
foreach ($r in @("2.0", "2.5", "3.0")) {
    $tag = "r" + ($r -replace "\.", "p")
    foreach ($year in $YEARS) {
        $bars = Join-Path $BARS_YEARLY "bars_MNQ_dbv_${year}.csv"
        $out = Join-Path $EQ_DIR "${tag}_${year}.csv"
        $logfile = Join-Path $OUT_DIR "eq_${tag}_${year}.txt"
        $jobs += Start-Job -Name "eq_${tag}_${year}" -ScriptBlock {
            param($py, $bars, $r, $out, $logfile, $root)
            Set-Location $root
            & $py "scripts\equity_export.py" `
                --bars $bars --instrument MNQ --timeframe 5min `
                --set "r_multiple=$r" --out $out 2>&1 | Tee-Object $logfile
        } -ArgumentList $PYTHON, $bars, $r, $out, $logfile, $ROOT
    }
}

Write-Host "$(Get-Date -Format 'HH:mm:ss') — Started $($jobs.Count) jobs"
$jobs | Format-Table -Property Name, Id, State -AutoSize

# Wait for all jobs
$null = $jobs | Wait-Job -Timeout 900

# Report completion status
Write-Host "`n$(Get-Date -Format 'HH:mm:ss') — All jobs done:"
foreach ($j in $jobs) {
    $state = $j.State
    Write-Host "  $($j.Name): $state"
    if ($state -eq "Failed") {
        $j | Receive-Job 2>&1 | Select-Object -Last 10 | ForEach-Object { Write-Host "    ERR: $_" }
    }
}

Write-Host "`nDone. Output in $OUT_DIR and $EQ_DIR"
