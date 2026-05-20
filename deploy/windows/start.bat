@echo off
REM ======================================================================
REM topstep-bot launcher (Windows)
REM ----------------------------------------------------------------------
REM Called by Task Scheduler at 6:00 AM ET daily. Activates the venv,
REM sets env vars, launches the bot, captures output to a daily log.
REM
REM What this script does:
REM   1. Resolves its own location so it can run from anywhere.
REM   2. Activates the venv at .venv\Scripts\activate.bat (relative to repo root).
REM   3. Sources secrets from .env in the repo root (ignored by git).
REM   4. Sets one-day-per-file rolling logs in logs\YYYY-MM-DD.log
REM   5. Runs the bot. When it exits (Task Scheduler stops it at session
REM      end), the script exits with the bot's return code.
REM
REM Why a wrapper:
REM   Task Scheduler can run python directly, but managing env vars,
REM   secrets, venv activation, and log redirection inside the Action
REM   panel is brittle. A .bat wrapper keeps the scheduler entry simple
REM   and puts all the operational complexity in one auditable file.
REM ======================================================================

setlocal EnableDelayedExpansion

REM ---- Resolve repo root from this script's location ----
REM %~dp0 is the directory of this .bat file. The repo root is two
REM levels up (deploy\windows\start.bat).
set SCRIPT_DIR=%~dp0
pushd "%SCRIPT_DIR%..\.." >nul 2>&1
set REPO_ROOT=%CD%
popd >nul 2>&1

if not exist "%REPO_ROOT%\app\main.py" (
  echo [start.bat] ERROR: app\main.py not found under %REPO_ROOT% 1>&2
  exit /b 2
)

REM ---- Logs directory: create if missing, daily file ----
set LOG_DIR=%REPO_ROOT%\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM YYYY-MM-DD using PowerShell — Windows date format is locale-dependent
REM and unreliable for filenames. PowerShell is reliable everywhere.
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i
set LOG_FILE=%LOG_DIR%\%TODAY%.log

echo. >> "%LOG_FILE%"
echo ====================================================================== >> "%LOG_FILE%"
echo [start.bat] launching at %DATE% %TIME% >> "%LOG_FILE%"
echo ====================================================================== >> "%LOG_FILE%"

REM ---- Source .env for secrets (PROJECT_X_API_KEY, etc.) ----
REM Format: KEY=value, one per line, no quotes, no spaces around =.
REM Lines starting with # are comments.
if exist "%REPO_ROOT%\.env" (
  for /f "usebackq tokens=1,2 delims==" %%a in ("%REPO_ROOT%\.env") do (
    set "_key=%%a"
    set "_val=%%b"
    REM Skip blank lines and comments.
    if not "!_key!"=="" if not "!_key:~0,1!"=="#" set "!_key!=!_val!"
  )
)

REM ---- Bot config — defaults if not already set ----
if not defined TOPSTEP_BOT_MODE          set TOPSTEP_BOT_MODE=live
if not defined TOPSTEP_BOT_INSTRUMENT    set TOPSTEP_BOT_INSTRUMENT=MGC
if not defined TOPSTEP_BOT_TIMEFRAMES    set TOPSTEP_BOT_TIMEFRAMES=1min
if not defined TOPSTEP_BOT_SOFT_BUFFER   set TOPSTEP_BOT_SOFT_BUFFER=500
if not defined TOPSTEP_BOT_RECONCILE_SECS set TOPSTEP_BOT_RECONCILE_SECS=30
if not defined TOPSTEP_BOT_LOG_LEVEL     set TOPSTEP_BOT_LOG_LEVEL=INFO
if not defined TOPSTEP_BOT_PORT          set TOPSTEP_BOT_PORT=5174

REM ---- Activate venv ----
if not exist "%REPO_ROOT%\.venv\Scripts\activate.bat" (
  echo [start.bat] ERROR: venv not found at %REPO_ROOT%\.venv 1>&2
  echo Create with: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt 1>&2
  exit /b 3
)
call "%REPO_ROOT%\.venv\Scripts\activate.bat"

REM ---- Run the bot ----
cd /d "%REPO_ROOT%"

echo [start.bat] config: mode=%TOPSTEP_BOT_MODE% inst=%TOPSTEP_BOT_INSTRUMENT% port=%TOPSTEP_BOT_PORT% >> "%LOG_FILE%"

REM Use venv python explicitly — don't rely on PATH activation which
REM can resolve to system Python on some Windows setups.
"%REPO_ROOT%\.venv\Scripts\python.exe" -u -m app.main >> "%LOG_FILE%" 2>&1
set EXIT_CODE=%ERRORLEVEL%

echo [start.bat] exit code: %EXIT_CODE% at %DATE% %TIME% >> "%LOG_FILE%"
exit /b %EXIT_CODE%
