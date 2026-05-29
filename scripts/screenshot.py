"""
Take a full-screen screenshot and save it to the project root as screenshot.png.
Usage: python scripts/screenshot.py [output_path]
"""
import subprocess
import sys
from pathlib import Path

out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("screenshot.png")
out = out.resolve()

ps = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
$bmp.Save('{str(out).replace(chr(92), chr(92)+chr(92))}')
$g.Dispose(); $bmp.Dispose()
Write-Output 'ok'
"""

r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, text=True)
if r.returncode != 0 or "ok" not in r.stdout:
    print(f"Screenshot failed: {r.stderr}", file=sys.stderr)
    sys.exit(1)

print(str(out))
