# Windows deployment

Deploys the bot to run automatically at 6:00 AM local time, Mon–Fri.
The 6am start covers London open (02:00 ET), NY AM (08:30 ET), and
NY PM (13:30 ET) killzones with margin on either side.

## First-time setup

### 1. Install Python and clone the repo

```powershell
# Python 3.11+ required.
python --version
git clone <your-fork> C:\topstep-bot
cd C:\topstep-bot
```

### 2. Create the venv and install deps

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install fastapi uvicorn hypothesis pytest pytest-asyncio pytest-timeout project-x-py
```

### 3. Verify with the test suite

```powershell
python -m pytest tests/ -v
```

All 76 tests should pass.

### 4. Configure secrets

```powershell
copy .env.example .env
notepad .env
```

Fill in `PROJECT_X_API_KEY` and `PROJECT_X_USERNAME`. Leave
`TOPSTEP_BOT_MODE=live` (or change to `paper` for a dry run).

### 5. Test-run from the command line

```powershell
.\deploy\windows\start.bat
```

The bot starts, the dashboard lives at http://127.0.0.1:5174, and
logs append to `logs\YYYY-MM-DD.log`. Ctrl+C to stop.

If this works, scheduling will work.

### 6. Register the scheduled task

Open PowerShell **as Administrator** (the task install needs it).

```powershell
cd C:\topstep-bot
powershell -ExecutionPolicy Bypass -File .\deploy\windows\install-task.ps1
```

The installer reads the `topstep-bot.xml` template, substitutes your
repo path and Windows username, and registers it as `TopstepBot`. The
substituted XML is written to a temp file, registered via `schtasks
/Create`, then deleted — your repo's `topstep-bot.xml` stays
unmodified.

Verify:

```powershell
schtasks /Query /TN TopstepBot /V /FO LIST
```

### 7. Test-fire without waiting for 6am

```powershell
schtasks /Run /TN TopstepBot
```

Check `logs\YYYY-MM-DD.log` — you should see the same startup banner
as the manual run.

## Daily operation

| What you do | Where |
| --- | --- |
| Check the day's P&L and lockouts | http://127.0.0.1:5174 (or from any device on local network) |
| Review fills and signals | Same dashboard |
| Read the bot's logs | `logs\YYYY-MM-DD.log` |
| Stop the bot mid-day | `schtasks /End /TN TopstepBot` |
| Restart manually after a stop | `schtasks /Run /TN TopstepBot` |

The bot stops automatically at 16:30 local time via the
`ExecutionTimeLimit` in the XML. NY PM session ends at 15:00 ET, so
this gives 90 minutes of headroom for cleanup and dashboard review.

## Troubleshooting

### Task ran but bot didn't start

Look at `logs\YYYY-MM-DD.log` for the launcher banner. If it's not
there, the task itself didn't fire — check `schtasks /Query` history
or open Task Scheduler GUI and look at the "Last Run Result" column.

Common causes:

- **0x1**: usually permission. Re-run `install-task.ps1` as Administrator.
- **0x41301**: task is currently running (hadn't stopped from yesterday).
  `schtasks /End /TN TopstepBot` then `/Run`.
- **0x2**: wrong path in the XML. Re-run `install-task.ps1`.

### Bot started but immediately exited

Check the log file. If you see `Missing required env var`, the .env
file is missing or unreadable. If you see `project_x_py` import errors,
the venv was created but deps weren't installed.

### Dashboard is unreachable

The dashboard binds to `127.0.0.1:5174`. If the task ran without a
logged-in user session, the HTTP port stays open but requires
network access from another device:

- From the same machine after logging in: http://127.0.0.1:5174
- From a phone on the same WiFi: http://YOUR_PC_IP:5174
  (you may need to allow inbound 5174 in Windows Firewall)

If you want phone-from-anywhere access, that's where the DigitalOcean
sync layer comes in — out of scope for the auto-run.

### Sleeping computer missed the 6am trigger

The task XML has `WakeToRun=true`, but this requires Windows power
plan to use **Sleep**, not **Hibernate**. Check:

```powershell
powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE
```

If it shows hibernate, change in Control Panel → Power Options →
Change plan settings → Change advanced power settings → Sleep →
Hibernate after = Never.

### DST transitions

The trigger time is "wall clock". Spring forward / fall back happens
automatically — no action needed.

The bot itself handles all session math in ET, regardless of machine
timezone, so London/NY killzone windows stay correct across DST too.

## Removing the task

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\windows\install-task.ps1 -Uninstall
```

## What this does NOT do

- **Does not auto-update the code.** `git pull` is your job.
- **Does not back up logs.** Log files accumulate in `logs\`. Rotate
  manually or add a scheduled cleanup task if your disk fills up.
- **Does not run on weekends.** The task is set Mon–Fri only because
  the futures markets close 16:00 ET Friday and don't reopen until
  17:00 ET Sunday. If you want Sunday evening starts, add `Sunday`
  to `<DaysOfWeek>` in the XML and re-run the installer.
- **Does not survive a reboot during the trading day.** If the
  machine reboots at 11am, the task does not re-trigger until tomorrow
  6am. For mid-day recovery, run `schtasks /Run /TN TopstepBot` manually.
