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

# Targeted-Opus model policy (Lawrence, 2026-06-14, "option 1"): hard BUILD /
# refactor items run on Opus 4.8; everything else (Phase-1 data-mining gates,
# benchmarks, likely-rejections) runs on Sonnet, which keeps quota plentiful
# (~17 sessions/window vs ~3-4 on Opus). Selection is per-session from the top
# `[pending]` backlog item's header tag `model:opus` (see research/PROTOCOL.md
# for the tagging convention). Secondary, dormant in practice: near a window
# reset with quota likely left a Sonnet item may still use Opus (honors the
# earlier "use the big model when the window is ending with quota left"
# instruction; rarely fires since windows run ~17 sessions). The usage window
# is tracked heuristically: 5h from the first session after loop start or after
# a limit-sleep.
$backlogFile = Join-Path $root "research\BACKLOG.md"
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

    # Pick the model for the item the next session will claim (top `[pending`).
    # `model:opus` in that item's header -> Opus; otherwise Sonnet.
    $model = 'sonnet'
    $topPending = Select-String -Path $backlogFile -Pattern '^##\s+B\d+.*\[pending' |
        Select-Object -First 1
    if ($topPending -and $topPending.Line -match 'model:\s*opus') { $model = 'opus' }
    if ($null -eq $windowStart -or (Get-Date) -ge $windowStart.AddHours($windowHours)) {
        $windowStart = Get-Date; $windowSessions = 0
    }
    $minsLeft = [int](($windowStart.AddHours($windowHours) - (Get-Date)).TotalMinutes)
    if ($model -eq 'sonnet' -and $minsLeft -le 75 -and $windowSessions -le 5) { $model = 'opus' }
    $windowSessions++
    # Opus runs at LOW effort to stretch the tighter weekly Opus quota (Lawrence,
    # 2026-06-15: use opus at a lower effort when Sonnet is weekly-capped). Sonnet
    # has abundant quota, so it stays high.
    $effort = if ($model -eq 'opus') { 'low' } else { 'high' }

    Log "session #$sessionNum starting (model $model effort $effort, ~${minsLeft}m left in est. window, $windowSessions sessions this window) -> $sessionLog"
    $start = Get-Date

    # Headless session: full permission bypass (approved 2026-06-12).
    & claude -p $prompt --model $model --effort $effort --dangerously-skip-permissions 2>&1 |
        Tee-Object -FilePath $sessionLog | Out-Null
    $code = $LASTEXITCODE
    $mins = [math]::Round(((Get-Date) - $start).TotalMinutes, 1)

    $tail = ""
    if (Test-Path $sessionLog) {
        $tail = (Get-Content $sessionLog -Tail 30 | Out-String)
        Add-Content -Path $loopLog -Value ("--- session #$sessionNum tail ---`r`n" + $tail) -Encoding utf8
    }

    # Anchor on the actual CLI limit message ("You've hit your <weekly|usage|5-hour>
    # limit · resets ..."), NOT loose substrings like "weekly limit"/"exceeded" — those
    # appear in research output (and in backlog notes) and caused false limit-blocks.
    if ($tail -match "you'?ve hit your[\w\s-]*limit|usage limit reached|claude usage limit|rate_limit_error|overloaded_error|\bAPI Error:\s*429\b|out of credit") {
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
        elseif ($tail -match 'resets (\d{1,2})\s*(am|pm)') {
            # "resets 2pm" form (no minutes), e.g. the weekly-limit message.
            $h = [int]$Matches[1]
            if ($Matches[2] -eq 'pm' -and $h -ne 12) { $h += 12 }
            if ($Matches[2] -eq 'am' -and $h -eq 12) { $h = 0 }
            $target = (Get-Date).Date.AddHours($h)
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
