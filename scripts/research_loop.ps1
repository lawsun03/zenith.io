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

# Adaptive model policy (Lawrence, 2026-06-12): sonnet by default — the work
# is protocol-following execution and a cheaper model multiplies sessions per
# usage window. But "if the session is nearing the end and there's still a
# good % left, go ahead and use fable 5 or opus 4.8": when the usage window is
# close to its reset AND few sessions have consumed it (quota likely
# plentiful), escalate to opus — the window resets soon anyway, so a big-model
# session costs nothing in lost future sessions. The CLI exposes no quota %,
# so the window is tracked heuristically: 5h from the first session after loop
# start or after a limit-sleep.
$windowHours = 5
$windowStart = $null
$windowSessions = 0

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

    if ($null -eq $windowStart -or (Get-Date) -ge $windowStart.AddHours($windowHours)) {
        $windowStart = Get-Date; $windowSessions = 0
    }
    $minsLeft = [int](($windowStart.AddHours($windowHours) - (Get-Date)).TotalMinutes)
    $model = 'sonnet'
    if ($minsLeft -le 75 -and $windowSessions -le 5) { $model = 'opus' }
    $windowSessions++

    Log "session #$sessionNum starting (model $model, ~${minsLeft}m left in est. window, $windowSessions sessions this window) -> $sessionLog"
    $start = Get-Date

    # Headless session: full permission bypass (approved 2026-06-12).
    & claude -p $prompt --model $model --dangerously-skip-permissions 2>&1 |
        Tee-Object -FilePath $sessionLog | Out-Null
    $code = $LASTEXITCODE
    $mins = [math]::Round(((Get-Date) - $start).TotalMinutes, 1)

    $tail = ""
    if (Test-Path $sessionLog) {
        $tail = (Get-Content $sessionLog -Tail 30 | Out-String)
        Add-Content -Path $loopLog -Value ("--- session #$sessionNum tail ---`r`n" + $tail) -Encoding utf8
    }

    if ($tail -match 'session limit|usage limit|rate limit|limit reached|overloaded|429|out of credit|exceeded') {
        # Sleep until the stated reset time when present ("resets 4:30pm"),
        # else fall back to 40 min. +3 min cushion past the reset.
        $sleepSec = 2400
        if ($tail -match 'resets (\d{1,2}):(\d{2})\s*(am|pm)') {
            $h = [int]$Matches[1]; $m = [int]$Matches[2]
            if ($Matches[3] -eq 'pm' -and $h -ne 12) { $h += 12 }
            if ($Matches[3] -eq 'am' -and $h -eq 12) { $h = 0 }
            $target = (Get-Date).Date.AddHours($h).AddMinutes($m)
            if ($target -le (Get-Date)) { $target = $target.AddDays(1) }
            $sleepSec = [int]((($target - (Get-Date)).TotalSeconds) + 180)
        }
        Log "session #$sessionNum limit-blocked after ${mins}m - sleeping $([int]($sleepSec/60)) min (until reset)."
        Start-Sleep -Seconds $sleepSec
        # Fresh usage window after the reset.
        $windowStart = $null
    }
    elseif ($code -ne 0) {
        Log "session #$sessionNum exited $code after ${mins}m (not limit-shaped) - sleeping 10 min."
        Start-Sleep -Seconds 600
    }
    else {
        Log "session #$sessionNum completed in ${mins}m - next in 60s."
        Start-Sleep -Seconds 60
    }
}
Log "=== research loop stopped after $sessionNum session(s) ==="
