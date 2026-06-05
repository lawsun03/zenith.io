param()
$RepoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $RepoRoot

$today = Get-Date -Format "yyyy-MM-dd"
$logFile = Join-Path $RepoRoot "logs\commit_trades.log"

function Log($msg) {
    $line = "$(Get-Date -Format 'HH:mm:ss') $msg"
    Write-Host $line
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

Log "--- commit_trades.ps1 started ($today) ---"

# Stage all trade/excursion/rejection CSVs
git add trades/*.csv 2>&1 | ForEach-Object { Log $_ }

# Check if anything is staged
$staged = git diff --cached --quiet 2>&1
if ($LASTEXITCODE -eq 0) {
    Log "Nothing to commit — trades unchanged since last push."
    exit 0
}

$msg = "chore: auto-commit trades $today"
git commit -m $msg 2>&1 | ForEach-Object { Log $_ }
if ($LASTEXITCODE -ne 0) {
    Log "ERROR: git commit failed"
    exit 1
}

git push 2>&1 | ForEach-Object { Log $_ }
if ($LASTEXITCODE -ne 0) {
    Log "ERROR: git push failed"
    exit 1
}

Log "Done — trades committed and pushed."
