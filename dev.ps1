# dev.ps1 - load .env and launch the bot for local development.
# Usage: .\dev.ps1 [-Paper] [-Live]
# Defaults to whatever TOPSTEP_BOT_MODE is set to in .env.

param(
    [switch]$Paper,
    [switch]$Live
)

$RepoRoot = $PSScriptRoot

# Clear any stale TOPSTEP_BOT_* vars from previous runs before reloading .env
Get-ChildItem Env: | Where-Object { $_.Name -like "TOPSTEP_BOT_*" } | ForEach-Object {
    Remove-Item "Env:\$($_.Name)"
}

# Load .env
$EnvFile = Join-Path $RepoRoot ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | Where-Object {
        $_ -notmatch "^\s*#" -and $_ -match "="
    } | ForEach-Object {
        $k, $v = $_ -split "=", 2
        [System.Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim())
    }
} else {
    Write-Error ".env not found at $EnvFile - copy .env.example and fill in your credentials."
    exit 1
}

# Flag overrides
if ($Paper) { $env:TOPSTEP_BOT_MODE = "paper" }
if ($Live)  { $env:TOPSTEP_BOT_MODE = "live"  }

Write-Host "mode=$env:TOPSTEP_BOT_MODE  instrument=$env:TOPSTEP_BOT_INSTRUMENT  bars=$env:TOPSTEP_BOT_PAPER_BARS"

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Error "venv not found. Run: py -m venv .venv"
    exit 1
}

# Exponential backoff on crashes; fast restart on requested restarts.
#   exit 0   = clean shutdown (don't restart)
#   exit 2   = restart requested via API (fast restart, reset backoff)
#   exit 130 = Ctrl+C (don't restart)
#   other    = crash (exponential backoff to avoid Topstep session-rate-limit)
$attempts = 0
while ($true) {
    & $Python -u -m app.main
    $exit = $LASTEXITCODE
    if ($exit -eq 0 -or $exit -eq 130) {
        Write-Host "Bot exited cleanly (code $exit). Stopping."
        break
    }
    if ($exit -eq 2) {
        $attempts = 0
        Write-Host "Bot requested restart (code 2). Restarting in 3s..."
        Start-Sleep -Seconds 3
        continue
    }
    $attempts++
    # 30s, 60s, 120s, 240s, capped at 300s (5 min)
    $delay = [Math]::Min(30 * [Math]::Pow(2, $attempts - 1), 300)
    Write-Host "Bot exited with code $exit (attempt $attempts). Restarting in ${delay}s... (Ctrl+C to stop)"
    Start-Sleep -Seconds $delay
}
