<#
.SYNOPSIS
    Install the topstep-bot scheduled task.

.DESCRIPTION
    Reads the XML template, substitutes paths and the current user,
    and registers the task with Task Scheduler. Idempotent: if the
    task already exists, it's replaced.

.EXAMPLE
    Run from an Administrator PowerShell, from the repo root:
        powershell -ExecutionPolicy Bypass -File .\deploy\windows\install-task.ps1

.EXAMPLE
    Specify a custom username (e.g. domain account):
        .\install-task.ps1 -UserId "DOMAIN\username"

.EXAMPLE
    Remove the task:
        .\install-task.ps1 -Uninstall
#>

[CmdletBinding()]
param(
    [string]$TaskName = "TopstepBot",
    [string]$UserId   = $env:USERNAME,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

# Resolve repo root from this script's location (deploy\windows\install-task.ps1).
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path

# ---- Uninstall path: stop and remove the task, then exit. ----
if ($Uninstall) {
    Write-Host "Removing scheduled task '$TaskName'..."
    schtasks /Delete /TN $TaskName /F
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Removed."
    } else {
        Write-Warning "Task may not have existed (exit $LASTEXITCODE)."
    }
    exit 0
}

# ---- Install path ----

# Sanity checks before we touch the registry.
$BatPath = Join-Path $RepoRoot "deploy\windows\start.bat"
if (-not (Test-Path $BatPath)) {
    throw "start.bat not found at $BatPath"
}
if (-not (Test-Path (Join-Path $RepoRoot "app\main.py"))) {
    throw "Repo structure unexpected: app\main.py missing under $RepoRoot"
}
if (-not (Test-Path (Join-Path $RepoRoot ".venv\Scripts\python.exe"))) {
    Write-Warning ".venv not found at $RepoRoot\.venv. Create with:"
    Write-Warning "    python -m venv .venv"
    Write-Warning "    .venv\Scripts\pip install -r requirements.txt"
    Write-Warning "Continuing with task install — fix the venv before the task fires."
}

# Load and customize the XML template.
$XmlPath = Join-Path $ScriptDir "topstep-bot.xml"
[xml]$Xml = Get-Content $XmlPath -Encoding UTF8

# Namespaced lookups — Task Scheduler XML uses a default namespace,
# so PowerShell needs the namespace manager to find nodes.
$ns = New-Object System.Xml.XmlNamespaceManager $Xml.NameTable
$ns.AddNamespace("t", "http://schemas.microsoft.com/windows/2004/02/mit/task")

# Substitute UserId.
$userNode = $Xml.SelectSingleNode("//t:Principal/t:UserId", $ns)
if ($null -eq $userNode) { throw "Could not find UserId node in XML" }
Write-Host "Setting Principal/UserId = $UserId"
$userNode.InnerText = $UserId

# Substitute Command (path to start.bat) and WorkingDirectory.
$commandNode = $Xml.SelectSingleNode("//t:Actions/t:Exec/t:Command", $ns)
$workDirNode = $Xml.SelectSingleNode("//t:Actions/t:Exec/t:WorkingDirectory", $ns)
if ($null -eq $commandNode -or $null -eq $workDirNode) {
    throw "Could not find Action Command or WorkingDirectory in XML"
}
Write-Host "Setting Actions/Exec/Command = $BatPath"
$commandNode.InnerText = $BatPath
Write-Host "Setting Actions/Exec/WorkingDirectory = $RepoRoot"
$workDirNode.InnerText = $RepoRoot

# Move StartBoundary forward to today (the template's date is in the past).
# Task Scheduler treats StartBoundary as "trigger becomes valid at this
# datetime; recurrence kicks in based on time-of-day". Stale dates
# don't break it, but a fresh one is cleaner.
$startNode = $Xml.SelectSingleNode("//t:CalendarTrigger/t:StartBoundary", $ns)
if ($startNode) {
    # Keep the original time-of-day (06:00:00).
    $today = (Get-Date).ToString("yyyy-MM-dd")
    $newStart = "${today}T06:00:00"
    Write-Host "Setting Trigger/StartBoundary = $newStart"
    $startNode.InnerText = $newStart
}

# Write the customized XML to a temp file (schtasks reads from disk).
$TempXml = [System.IO.Path]::GetTempFileName() + ".xml"
$Xml.Save($TempXml)

try {
    # Delete first if it exists, so we get a clean replace.
    schtasks /Query /TN $TaskName 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Task '$TaskName' exists; replacing..."
        schtasks /Delete /TN $TaskName /F | Out-Null
    }

    Write-Host "Registering task '$TaskName' from $TempXml..."
    # /RU + /RP would let us pass the password directly; safer to omit
    # and let Task Scheduler prompt securely on first run. If you'd
    # rather not be prompted, run schtasks manually with /RU /RP after
    # this script finishes.
    schtasks /Create /TN $TaskName /XML $TempXml
    if ($LASTEXITCODE -ne 0) {
        throw "schtasks /Create failed with exit $LASTEXITCODE"
    }

    Write-Host ""
    Write-Host "Installed. Verify with:"
    Write-Host "    schtasks /Query /TN $TaskName /V /FO LIST"
    Write-Host ""
    Write-Host "Test-fire (without waiting for 6am) with:"
    Write-Host "    schtasks /Run /TN $TaskName"
    Write-Host ""
    Write-Host "Logs will appear at:"
    Write-Host "    $RepoRoot\logs\YYYY-MM-DD.log"
} finally {
    Remove-Item $TempXml -Force -ErrorAction SilentlyContinue
}
