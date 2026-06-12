# Weekend autonomous research loop.
# Launches headless claude -p sessions one at a time; each does ONE backlog
# item per research/PROTOCOL.md and exits. Sleeps out usage limits.
# Stop: create research/STOP (or close this window).
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$loopLog = Join-Path $root "research\loop.log"
$stopFile = Join-Path $root "research\STOP"
$promptFile = Join-Path $root "research\SESSION_PROMPT.md"
$sessionNum = 0

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | $msg"
    Write-Host $line
    Add-Content -Path $loopLog -Value $line -Encoding utf8
}

Log "=== research loop armed (PID $PID). Stop with research\STOP ==="

while ($true) {
    if (Test-Path $stopFile) { Log "STOP file found - halting loop."; break }

    $sessionNum++
    $prompt = Get-Content $promptFile -Raw
    $sessionLog = Join-Path $root ("research\sessions\session_{0}_{1}.log" -f `
        (Get-Date -Format 'yyyyMMdd_HHmmss'), $sessionNum)
    New-Item -ItemType Directory -Force -Path (Join-Path $root "research\sessions") | Out-Null

    Log "session #$sessionNum starting -> $sessionLog"
    $start = Get-Date

    # Headless session: full permission bypass (approved 2026-06-12).
    & claude -p $prompt --dangerously-skip-permissions 2>&1 |
        Tee-Object -FilePath $sessionLog | Out-Null
    $code = $LASTEXITCODE
    $mins = [math]::Round(((Get-Date) - $start).TotalMinutes, 1)

    $tail = ""
    if (Test-Path $sessionLog) {
        $tail = (Get-Content $sessionLog -Tail 30 | Out-String)
        Add-Content -Path $loopLog -Value ("--- session #$sessionNum tail ---`r`n" + $tail) -Encoding utf8
    }

    if ($tail -match 'usage limit|rate limit|limit reached|overloaded|429|out of credit|exceeded') {
        Log "session #$sessionNum hit a usage limit after ${mins}m (exit $code) - sleeping 40 min."
        Start-Sleep -Seconds 2400
    }
    elseif ($code -ne 0) {
        Log "session #$sessionNum exited $code after ${mins}m (not limit-shaped) - sleeping 5 min."
        Start-Sleep -Seconds 300
    }
    else {
        Log "session #$sessionNum completed in ${mins}m - next in 60s."
        Start-Sleep -Seconds 60
    }
}
Log "=== research loop stopped after $sessionNum session(s) ==="
